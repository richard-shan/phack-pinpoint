# Commander prompt + subagent dicts for create_deep_agent.
from __future__ import annotations

import os
from typing import Any

from . import stubs, routing, localization

COMMANDER_MODEL = os.environ.get("PINPOINT_COMMANDER_MODEL", "anthropic:claude-sonnet-5-5")
WORKER_MODEL = os.environ.get("PINPOINT_WORKER_MODEL", "anthropic:claude-haiku-5-5")
LOCATOR_MODEL = os.environ.get("PINPOINT_LOCATOR_MODEL", "anthropic:claude-sonnet-5-5")

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

_VERIFIER_PROMPT = """You are the Verifier. For each sentence in the draft packet, confirm
it cites a source (a file path, record ID, or `src:<tag>`). If a sentence has no source,
flag it as unsupported. Write audit.json with the form {claims: N, sourced: M,
unsourced: K, status: "OK" | "DEGRADED" | "UNABLE"}. Status is OK iff K is zero and no
required section was dropped. Do not add new facts. Do not rewrite the packet. Report
what you see."""


def build_subagents() -> list[dict[str, Any]]:
    # records / locator / site / router / verifier.
    return [
        {
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
        },
        {
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
        },
        {
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
        },
        {
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
        },
        {
            "name": "verifier",
            "description": "Audit the draft packet: every claim needs a source or it is flagged.",
            "system_prompt": _VERIFIER_PROMPT,
            "tools": [],
            "model": WORKER_MODEL,
            "mode": "isolated",
        },
    ]


INTERRUPT_ON = {
    "send_packet": {"allowed_decisions": ["approve", "reject"]},
    "notify_contact": {"allowed_decisions": ["approve", "reject"]},
}
