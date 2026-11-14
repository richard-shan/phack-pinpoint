"""Prepare this machine to run an incident end to end.

    python3 -m live.setup                 # console data: catalog, scan graphs, viewer
    python3 -m live.setup --no-viewer     # skip the Matterport viewer download (about 11 MB)
    python3 -m live.setup --check         # only report what is ready

What it writes (all git-ignored, all rebuilt from what is in the repo):
- `app/data/catalog.json`: the console's property list, from lane A's CoStar index. An
  existing catalog (for example one built by `app/scripts/build-catalog.py` from the kit)
  is kept.
- `app/data/<PropertyId>-scan.json`: lane A's graph for each building, in the console's
  format, so its 3D view draws lane A's doors and routes.
- `app/vendor/matterport/`: the Matterport viewer the 3D view runs in.

It does not write API keys anywhere. `python3 -m live.serve` hands them to the console
at start, from `api.txt`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from env import console as console_export
from env import costar
from env.graph import Graph, list_buildings

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DATA = APP / "data"
VIEWER = APP / "vendor" / "matterport" / "showcase.html"

UNKNOWNS = ["Caller location has not been automatically determined.",
            "Entrance, staging, interior route and stretcher access are not verified.",
            "AEDs, hazards, door status and current tenant occupancy are not verified.",
            "Matterport scan date is not established by model creation dates.",
            "Vexcel imagery is historical; capture time and current access require confirmation."]


def buildings() -> list[str]:
    return [b for b in list_buildings() if b != "toy"]


def write_catalog(force: bool = False) -> str:
    """The console's catalog from lane A's CoStar index (3,339 properties)."""
    out = DATA / "catalog.json"
    if out.exists() and not force:
        return f"kept existing {out.relative_to(ROOT)}"
    props = costar._index()["props"]
    graphs = {b: Graph.load(b).data.get("matterport_model_id") for b in buildings()}
    records = DATA / "records"
    records.mkdir(parents=True, exist_ok=True)
    rows = []
    for pid, p in props.items():
        models = []
        for m in p.get("matterport_model_ids") or []:
            checked = graphs.get(pid) == m           # lane A built and looked at this scan
            models.append({"id": m, "name": f"Matterport scan {m}", "address": p.get("address"),
                           "scanDate": None, "url": "https://my.matterport.com/show/?m=" + m, "images": [],
                           "joinStatus": "verified" if checked else "address-unconfirmed",
                           "notes": ["Property ID agrees with the CoStar record."],
                           "source": {"file": "commercial-properties.csv", "recordId": m, "propertyId": pid}})
        models.sort(key=lambda m: m["joinStatus"] != "verified")
        latlon = p.get("latlon") or [None, None]
        rows.append({
            "id": pid, "name": p.get("name") or p.get("address"),
            "address": ", ".join(x for x in (p.get("address"), p.get("city"),
                                             " ".join(x for x in (p.get("state"), str(p.get("postcode") or "")[:5]) if x)) if x),
            "city": p.get("city"), "state": p.get("state"), "postalCode": str(p.get("postcode") or "")[:5] or None,
            "useType": p.get("use_type"), "subtype": p.get("use_subtype"), "stories": p.get("stories"),
            "yearBuilt": p.get("year_built"), "sqft": p.get("sqft"), "sqftBasis": "Building area",
            "latitude": latlon[0], "longitude": latlon[1],
            "source": {"file": "commercial-property.jsonl", "recordId": pid, "recordUrl": f"/data/records/{pid}.json",
                       "note": "normalized by lane A (env/costar.py)"},
            "models": models, "unknowns": list(UNKNOWNS)})
        (records / f"{pid}.json").write_text(json.dumps(
            {"provider": "CoStar hackathon data bundle", "sourceFile": "commercial-property.jsonl",
             "recordId": pid, "record": p,
             "note": "Lane A's normalized copy of the source record. Not a real-time verification."},
            ensure_ascii=False, indent=1) + "\n")
    have = set(graphs)
    rows.sort(key=lambda r: (r["id"] not in have, r["city"] or "", (r["name"] or "").casefold(), r["id"]))
    stats = {"properties": len(rows), "houston": sum(r["city"] == "Houston" for r in rows),
             "losAngeles": sum(r["city"] == "Los Angeles" for r in rows),
             "indexedModels": sum(len(r["models"]) for r in rows), "withRouting": len(have)}
    out.write_text(json.dumps({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "source": {"name": "Lane A CoStar index (env/data/costar_index.json.gz)",
                                          "mode": "offline source snapshot"},
                               "stats": stats, "properties": rows}, ensure_ascii=False, separators=(",", ":")) + "\n")
    return f"wrote {out.relative_to(ROOT)}: {len(rows)} properties"


def write_scans() -> list[str]:
    """Lane A's graph for every building, where the console looks for it."""
    DATA.mkdir(parents=True, exist_ok=True)
    done = []
    for b in buildings():
        try:
            path = console_export.write_scan(b, out=DATA / f"{b}-scan.json")
            scan = json.loads(path.read_text())
            done.append(f"{b}: {len(scan['nodes'])} positions, {len(scan['entries'])} doors")
        except ValueError as exc:
            done.append(f"{b}: skipped ({exc})")
    return done


VIEWER_ZIP = ("https://static.matterport.com/showcase-sdk/bundle/"
              "26.10.4_webgl-1728-g1cd7e0997a/showcase-bundle.zip")    # same build app/scripts/setup-princeton.mjs uses


def fetch_viewer() -> str:
    """The Matterport viewer bundle, from Matterport's own archive."""
    if VIEWER.exists():
        return "viewer already installed"
    import io
    import urllib.request
    import zipfile
    try:
        req = urllib.request.Request(VIEWER_ZIP, headers={"User-Agent": "pinpoint-setup"})
        with urllib.request.urlopen(req, timeout=300) as r:
            blob = r.read()
        VIEWER.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            root = VIEWER.parent.resolve()
            for name in z.namelist():                     # refuse entries that would land outside
                if not (root / name).resolve().is_relative_to(root):
                    return f"viewer archive has an unexpected path: {name}"
            z.extractall(root)
    except Exception as exc:
        return f"viewer download failed: {type(exc).__name__}: {str(exc)[:200]}"
    return f"viewer installed ({len(blob) // 1_000_000} MB)" if VIEWER.exists() else "viewer archive did not contain showcase.html"


def check() -> dict:
    """What is ready on this machine."""
    from importlib.util import find_spec
    index = ROOT / "cache" / "index"
    have_index = sorted(p.parent.name for p in index.glob("*/emb.npy")) if index.exists() else []
    return {
        "buildings_with_a_graph": buildings(),
        "console_catalog": (DATA / "catalog.json").exists(),
        "console_scan_graphs": sorted(p.name[:-len("-scan.json")] for p in DATA.glob("*-scan.json")) if DATA.exists() else [],
        "console_viewer": VIEWER.exists(),
        "node": bool(shutil.which("node")),
        "photo_matching_packages": all(find_spec(m) is not None for m in ("torch", "kornia", "cv2")),
        "photo_index_built_for": have_index,
        "keys_file": (ROOT / "api.txt").exists(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-viewer", action="store_true")
    ap.add_argument("--force-catalog", action="store_true", help="rebuild the catalog even if one exists")
    a = ap.parse_args()
    if not a.check:
        print(write_catalog(a.force_catalog))
        for line in write_scans():
            print("scan graph", line)
        if not a.no_viewer:
            print(fetch_viewer())
    state = check()
    print(json.dumps(state, indent=1))
    if not state["photo_matching_packages"]:
        print("\nPhoto matching is not installed in this Python. To install it:\n"
              "  uv venv --python 3.12 .venv\n"
              "  uv pip install --python .venv/bin/python --index-url https://download.pytorch.org/whl/cpu torch torchvision\n"
              "  uv pip install --python .venv/bin/python -r perception/requirements.txt -r env/requirements.txt huggingface_hub safetensors\n"
              "then build an index:  .venv/bin/python -m live.index <building id>")
    missing = [b for b in state["buildings_with_a_graph"] if b not in state["photo_index_built_for"]]
    if state["photo_matching_packages"] and missing:
        print(f"\nNo photo index yet for: {', '.join(missing)}. Build one with: "
              ".venv/bin/python -m live.index <building id>")


if __name__ == "__main__":
    main()
