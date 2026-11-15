"""Client for the dispatcher console's local HTTP service (`app/local-server.mjs`).

The console keeps incidents on disk and guards every write with a revision number. This
wraps that: each write reads the current revision first and retries once or twice if
someone else (the dispatcher, the caller view) wrote in between.
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

MAX_PHOTO = 2 * 1024 * 1024


class ConsoleError(RuntimeError):
    def __init__(self, status, code, message):
        super().__init__(f"console {status} {code}: {message}")
        self.status, self.code = status, code


class Console:
    def __init__(self, base: str = "http://localhost:4175", timeout: float = 10.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------ transport
    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + "/api" + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", {})
            except Exception:
                err = {}
            finally:
                e.close()
            raise ConsoleError(e.code, err.get("code", "ERROR"), err.get("message", str(e))) from None

    def _write(self, incident_id: str, method: str, suffix: str, body: dict, tries: int = 5) -> dict:
        for attempt in range(tries):
            rev = self.get(incident_id)["revision"]
            try:
                return self._call(method, f"/incidents/{incident_id}{suffix}", dict(body, expectedRevision=rev))
            except ConsoleError as e:
                if e.status != 409 or e.code != "REVISION_CONFLICT" or attempt == tries - 1:
                    raise
                time.sleep(0.1 * (attempt + 1))
        raise AssertionError("unreachable")

    # ------------------------------------------------------------ reads
    def alive(self) -> bool:
        try:
            return bool(self._call("GET", "/health").get("ok"))
        except (ConsoleError, OSError):
            return False

    def get(self, incident_id: str) -> dict:
        return self._call("GET", f"/incidents/{incident_id}")

    def packet(self, packet_id: str) -> dict:
        return self._call("GET", f"/packets/{packet_id}")

    def incidents(self) -> list[dict]:
        return self._call("GET", "/incidents").get("incidents", [])

    def has_scan(self, property_id: str) -> bool:
        """Whether the console can show this building in its 3D route view."""
        try:
            self._call("GET", f"/spatial/model?propertyId={property_id}")
            return True
        except (ConsoleError, OSError):
            return False

    # ------------------------------------------------------------ writes
    def create(self, property_id: str, caller_text: str = "", photo_path: str | None = None,
               channel: dict | None = None) -> dict:
        """channel: {"platform", "handle", "location"} for incidents that arrived by message."""
        body = {"propertyId": str(property_id), "callerText": caller_text or ""}
        if photo_path:
            body["photo"] = photo_payload(photo_path)
        if channel:
            body["channel"] = channel
        try:
            return self._call("POST", "/incidents", body)
        except ConsoleError as e:
            if e.code != "UNKNOWN_FIELD" or "channel" not in body:
                raise
            body.pop("channel")                # a console from before that field existed
            return self._call("POST", "/incidents", body)

    def update(self, incident_id: str, **fields) -> dict:
        """fields: location, route, notes, unconfirmed (list), packetMarkdown, and
        updatedBy ("agent") so the console's activity list says who made the change."""
        try:
            return self._write(incident_id, "PATCH", "", fields)
        except ConsoleError as e:
            if e.code != "UNKNOWN_FIELD" or "updatedBy" not in fields:
                raise
            fields.pop("updatedBy")            # a console from before that field existed
            return self._write(incident_id, "PATCH", "", fields)

    def message(self, incident_id: str, role: str, text: str) -> dict:
        return self._write(incident_id, "POST", "/messages", {"role": role, "text": text[:4000]})

    def route(self, incident_id: str, body: dict) -> dict:
        return self._write(incident_id, "POST", "/route", body)

    def decide(self, incident_id: str, decision: str, reason: str = "", corrected_room_id: str = "") -> dict:
        body = {"decision": decision}
        if reason:
            body["reason"] = reason
        if corrected_room_id:
            body["correctedRoomId"] = corrected_room_id
        return self._write(incident_id, "POST", "/decision", body)

    # ------------------------------------------------------------ waiting on people
    def wait_for_caller(self, incident_id: str, timeout_s: float = 90.0, poll_s: float = 1.0) -> str | None:
        """The first caller message that arrives after this call starts."""
        seen = {m["id"] for m in self.get(incident_id).get("messages", [])}
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(poll_s)
            for m in self.get(incident_id).get("messages", []):
                if m["id"] not in seen and m.get("role") == "caller":
                    return m.get("text") or ""
        return None

    def wait_for_decision(self, incident_id: str, timeout_s: float | None = None, poll_s: float = 1.0) -> dict:
        """-> {"decision": "approved" | "rejected" | "timeout", "reason", "revision"}"""
        end = time.time() + timeout_s if timeout_s else None
        while end is None or time.time() < end:
            inc = self.get(incident_id)
            d = inc.get("decision")
            if d and inc.get("status") in ("approved", "rejected"):
                return {"decision": d["kind"], "reason": d.get("reason") or "", "revision": d.get("revision"),
                        "packet_id": inc.get("packetId"), "corrected_room_id": d.get("correctedRoomId") or ""}
            time.sleep(poll_s)
        return {"decision": "timeout", "reason": "", "revision": None}


def photo_payload(path: str) -> dict:
    """A photo as the console wants it: PNG, JPEG or WebP, at most 2 MB, as a data URL."""
    p = Path(path)
    raw = p.read_bytes()
    kind = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp"}.get(p.suffix.lower())
    if kind is None or len(raw) > MAX_PHOTO:
        from PIL import Image, ImageOps            # re-encode: other formats, or too large
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")     # phones store rotation in EXIF
        img.thumbnail((1600, 1600))
        for quality in (88, 75, 60):
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality)
            raw, kind = buf.getvalue(), "jpeg"
            if len(raw) <= MAX_PHOTO:
                break
    name = p.stem[:120] + (".jpg" if kind == "jpeg" else "." + kind)
    return {"name": name, "dataUrl": f"data:image/{kind};base64,{base64.b64encode(raw).decode()}"}


def save_photo(incident: dict, directory: str | Path) -> str | None:
    """Write an incident's caller photo to disk for the matcher. -> path or None."""
    photo = incident.get("photo") or {}
    url = photo.get("dataUrl") or ""
    if not url.startswith("data:image/") or ";base64," not in url:
        return None
    kind = url[len("data:image/"):url.index(";")]
    out = Path(directory) / f"caller_photo.{'jpg' if kind == 'jpeg' else kind}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(url.split(",", 1)[1]))
    return str(out)
