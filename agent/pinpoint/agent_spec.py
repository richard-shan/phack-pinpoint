# Commander prompt + subagent dicts for create_deep_agent.
from __future__ import annotations

import os
from typing import Any

from . import stubs, routing, localization

COMMANDER_MODEL = os.environ.get("PINPOINT_COMMANDER_MODEL", "anthropic:claude-haiku-5-5")
WORKER_MODEL = os.environ.get("PINPOINT_WORKER_MODEL", "anthropic:claude-haiku-5-5")
LOCATOR_MODEL = os.environ.get("PINPOINT_LOCATOR_MODEL", "anthropic:claude-haiku-5-5")


def _assert_haiku_only() -> None:
    """Log a warning when a non-haiku model is wired in (env-var override kept available
    but should not be the default for Pinpoint)."""
    import logging as _logging
    for name, model in (
        ("COMMANDER_MODEL", COMMANDER_MODEL),
        ("WORKER_MODEL", WORKER_MODEL),
        ("LOCATOR_MODEL", LOCATOR_MODEL),
    ):
        if "haiku" not in (model or "").lower():
            _logging.getLogger("pinpoint.agent_spec").warning(
                "non-haiku model wired: %s=%s (Pinpoint budget assumes haiku-only)",
                name, model,
            )


_assert_haiku_only()

# ---------- Pydantic response_format models ----------
# Probed at import time. When pydantic is not installed in the environment, the
# subagent dicts omit response_format; the Commander still receives last-message
# text as before. Pydantic is a transitive dep of langchain/deepagents, so these
# models materialize the moment the real-llm requirements are installed.
try:
    from pydantic import BaseModel
    PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment,misc]


if PYDANTIC_AVAILABLE:
    class RecordsReport(BaseModel):  # type: ignore[misc]
        building_id: str
        address: str
        tenants: list[dict]
        contact: dict | None = None
        evidence: list[str]

    class LocationReport(BaseModel):  # type: ignore[misc]
        node_id: str
        room_id: str
        room_label: str
        belief: float
        candidates: list[dict]
        questions_asked: int
        evidence_path: str

    class SiteReport(BaseModel):  # type: ignore[misc]
        entrances: list[dict]
        staging: list[dict]
        obstructions: list[dict]
        aerial_tile_path: str | None = None
        evidence: list[str]

    class RouteReport(BaseModel):  # type: ignore[misc]
        best_entry_id: str
        path_nodes: list[str]
        meters: float
        seconds: float
        hazards: list[str]
        evidence_path: str

    class VerifierReport(BaseModel):  # type: ignore[misc]
        status: str  # "OK" | "DEGRADED" | "UNABLE"
        claims_total: int
        claims_sourced: int
        unsupported: list[str]
else:
    RecordsReport = LocationReport = SiteReport = RouteReport = VerifierReport = None  # type: ignore[assignment]


# ---------- FilesystemPermission probe ----------
# deepagents moved its permissions API around across versions; try the two known
# paths and fall back cleanly.
_FP = None
for _mod, _attr in (
    ("deepagents.middleware.permissions", "FilesystemPermission"),
    ("deepagents", "FilesystemPermission"),
):
    try:
        _FP = getattr(__import__(_mod, fromlist=[_attr]), _attr)
        break
    except (ImportError, AttributeError):
        continue
PERMISSIONS_AVAILABLE = _FP is not None
FilesystemPermission = _FP  # may be None


def _perms(specs: list[tuple[str, str]]):
    """Build a permissions list from (path, mode) pairs.

    Real deepagents signature (verified against installed source):
        FilesystemPermission(operations=list["read"|"write"],
                             paths=list[str starting with "/"],
                             mode=Literal["allow","deny","interrupt"] = "allow")

    We group paths by whether they are read-only or read-write so each
    subagent gets at most two FilesystemPermission objects (one per op set).
    All our rules are allow-rules; deny defaults are left to the framework.
    """
    if not PERMISSIONS_AVAILABLE:
        return None
    read_paths = []
    readwrite_paths = []
    for path, mode in specs:
        p = path if path.startswith("/") else "/" + path
        if mode == "read":
            read_paths.append(p)
        elif mode == "readwrite":
            readwrite_paths.append(p)
    out = []
    if read_paths:
        out.append(_FP(operations=["read"], paths=read_paths, mode="allow"))
    if readwrite_paths:
        out.append(_FP(operations=["read", "write"], paths=readwrite_paths, mode="allow"))
    return out or None


_SUBAGENT_PERMS = {
    "records": [
        ("agent/pinpoint/fixtures/**/property.json", "read"),
        ("agent/pinpoint/fixtures/**/match.json", "read"),
    ],
    "locator": [
        ("agent/pinpoint/fixtures/**/graph.json", "read"),
        ("agent/pinpoint/fixtures/**/match.json", "read"),
    ],
    "site": [
        ("agent/pinpoint/fixtures/**/site.json", "read"),
        ("agent/pinpoint/fixtures/*/vexcel_tile.*", "readwrite"),
    ],
    "router": [
        ("agent/pinpoint/fixtures/**/graph.json", "read"),
        ("agent/pinpoint/fixtures/**/site.json", "read"),
    ],
    "verifier": [
        ("agent/incidents/**/packet.md", "read"),
        ("agent/incidents/**/audit.json", "read"),
    ],
}


# ---------- Prompts ----------

COMMANDER_SYSTEM_PROMPT = """You are the Commander for Pinpoint, a 911 dispatch assistant.
You turn one caller photo plus a street address into a one-page responder packet and stop
for dispatcher approval before anything leaves the system.

Operating rules:

1. Write your plan BEFORE delegating. Call `write_todos` with the five steps: records,
   locator, site, router, verifier. Mark each one `in_progress` when you start and
   `completed` when its subagent returns.

2. Delegate `records`, `locator`, and `site` in parallel. Issue all three `task` tool
   calls in the SAME turn so the runtime can dispatch them concurrently. Do not wait for
   one to return before calling the next.

3. Only after records, locator, and site have ALL returned, delegate `router`.

4. After router returns, delegate `verifier`. The verifier audits that every claim in the
   packet cites a source. You do not send a packet whose audit did not pass.

5. Before you call `send_packet`, re-read the packet file and confirm the verifier's
   audit status is "OK". If the audit is DEGRADED or UNABLE, you still submit the packet,
   but the status field must reflect that honestly.

6. You never do graph math, image matching, or source lookups yourself. You delegate and
   read the JSON that subagents return. You never invent a fact a subagent did not
   provide.

7. `send_packet` and `notify_contact` will pause for dispatcher approval. That is by
   design. Do not try to work around it.

8. Keep your own context small. Write intermediate state to the incident folder via the
   filesystem tools and reference it by path.
"""

# Variant used when TodoListMiddleware is not available at build time. The model would
# otherwise be instructed to call a tool that does not exist, which would crash the
# first real run. Keep the same operational rules, just drop the write_todos step.
COMMANDER_SYSTEM_PROMPT_NO_TODOS = """You are the Commander for Pinpoint, a 911 dispatch assistant.
You turn one caller photo plus a street address into a one-page responder packet and stop
for dispatcher approval before anything leaves the system.

Operating rules:

1. Delegate `records`, `locator`, and `site` in parallel. Issue all three `task` tool
   calls in the SAME turn so the runtime can dispatch them concurrently. Do not wait for
   one to return before calling the next.

2. Only after records, locator, and site have ALL returned, delegate `router`.

3. After router returns, delegate `verifier`. The verifier audits that every claim in the
   packet cites a source. You do not send a packet whose audit did not pass.

4. Before you call `send_packet`, re-read the packet file and confirm the verifier's
   audit status is "OK". If the audit is DEGRADED or UNABLE, you still submit the packet,
   but the status field must reflect that honestly.

5. You never do graph math, image matching, or source lookups yourself. You delegate and
   read the JSON that subagents return. You never invent a fact a subagent did not
   provide.

6. `send_packet` and `notify_contact` will pause for dispatcher approval. That is by
   design. Do not try to work around it.

7. Keep your own context small. Write intermediate state to the incident folder via the
   filesystem tools and reference it by path.
"""

_VERIFIER_PROMPT = """You are the Verifier. For each sentence in the draft packet, confirm
it cites a source (a file path, record ID, or `src:<tag>`). If a sentence has no source,
flag it as unsupported. Write audit.json with the form {claims: N, sourced: M,
unsupported: K, status: "OK" | "DEGRADED" | "UNABLE"}. Status is OK iff K is zero and no
required section was dropped. Do not add new facts. Do not rewrite the packet. Report
what you see."""


def _attach_extras(sub: dict[str, Any], response_model) -> dict[str, Any]:
    """Attach response_format and permissions only if the deps are available."""
    if PYDANTIC_AVAILABLE and response_model is not None:
        sub["response_format"] = response_model
    perms = _perms(_SUBAGENT_PERMS.get(sub["name"], []))
    if perms is not None:
        sub["permissions"] = perms
    return sub


def build_subagents() -> list[dict[str, Any]]:
    # records / locator / site / router / verifier.
    return [
        _attach_extras({
            "name": "records",
            "description": "Pull the CoStar property record for the caller's address.",
            "system_prompt": (
                "Call `get_property(address)` and return a brief JSON object with the "
                "building_id, use_type, stories, year_built, sqft, tenants, and contact. "
                "Flag any clinic, daycare, or lab tenant on the identified floor. Cite "
                "the CoStar record ID as the evidence_path for every field you return."
            ),
            "tools": [stubs.get_property],
            "model": WORKER_MODEL,
            "mode": "isolated",
        }, RecordsReport),
        _attach_extras({
            "name": "locator",
            "description": (
                "Match the caller photo to a position inside the building; ask the "
                "caller follow-up questions only when they would change the answer."
            ),
            "system_prompt": (
                "Call `match_photo(building_id, image_path, k=10)` to get candidates. "
                "Normalize via `update_belief` with no answer to establish the prior. "
                "While the top candidate's belief is below 0.80 and you have questions "
                "left, call `best_question(candidates)` to pick the highest-info-gain "
                "attribute, phrase it naturally, call `ask_caller` with the phrasing, "
                "then call `update_belief(candidates, attribute, answer)` with the "
                "response. Stop early when the top candidate passes threshold. Return "
                "location.json with the predicted node_id, room_id, belief, and the "
                "evidence_path from the top candidate."
            ),
            "tools": [
                stubs.match_photo,
                stubs.look,
                localization.best_question,
                localization.update_belief,
                stubs.ask_caller,
            ],
            "model": LOCATOR_MODEL,
            "mode": "isolated",
        }, LocationReport),
        _attach_extras({
            "name": "site",
            "description": (
                "Read the aerial tile for this building's address; return entrances, "
                "staging candidates, and obstructions."
            ),
            "system_prompt": (
                "Call `detect_site_features(building_id, address=..., lat=..., "
                "lon=...)` and return site.json with entrances, staging areas, "
                "obstructions, and an `aerial_tile_path` evidence_path when Vexcel data "
                "is available. On Vexcel fallback, cite the fixture tile path instead."
            ),
            "tools": [stubs.detect_site_features],
            "model": WORKER_MODEL,
            "mode": "isolated",
        }, SiteReport),
        _attach_extras({
            "name": "router",
            "description": (
                "Pick the best responder staging door given the belief; produce the "
                "route and any hazards along it. Compare to the front-door baseline for "
                "the meters-saved number."
            ),
            "system_prompt": (
                "Call `best_entry_under_belief` with the candidate set to pick the "
                "staging door. Call `route` for the main path and `backup_route` for an "
                "alternative. Call `hazards_along(building_id, path)` to decorate "
                "hazards. Return route.json with the chosen door_id, the meters saved "
                "against the front-door baseline, and the hazard list. Every field "
                "cites the graph node or edge it came from."
            ),
            "tools": [
                routing.best_entry_under_belief,
                routing.route,
                stubs.hazards_along,
                routing.backup_route,
            ],
            "model": WORKER_MODEL,
            "mode": "isolated",
        }, RouteReport),
        _attach_extras({
            "name": "verifier",
            "description": "Audit the draft packet: every claim needs a source or it is flagged.",
            "system_prompt": _VERIFIER_PROMPT,
            "tools": [],
            "model": WORKER_MODEL,
            "mode": "isolated",
        }, VerifierReport),
    ]


INTERRUPT_ON = {
    "send_packet": {"allowed_decisions": ["approve", "reject"]},
    "notify_contact": {"allowed_decisions": ["approve", "reject"]},
}
