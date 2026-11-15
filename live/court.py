"""Evidence court: when the photo match is unsure, subagents argue over where the caller is.

Runs in its own environment (.venv-agents: Python 3.11+, deepagents, the Anthropic SDK; no
torch), as a separate process, so the GPU pipeline's environment is untouched:

    .venv-agents/bin/python -m live.court case.json        # -> verdict JSON on stdout

case.json: {"building": name, "address": ..., "caller_text": ..., "caller_photo": path,
            "candidates": [{"id": "A", "node_id", "label", "floor", "similarity", "inliers",
                            "facts": {...panorama attributes...}, "views": [jpg paths]}]}

How a case is heard (a LangChain deepagents graph):
  1. Evidence, in parallel: for every candidate, Claude (vision) compares the caller's photo
     with that spot's scanned views and lists matching and conflicting details.
  2. One scout subagent per candidate argues that the caller is there, from that evidence
     and the spot's facts, and must name its own weaknesses.
  3. The judge (the deep agent's lead) sends every scout out in one turn, weighs the cases,
     and rules: winner, confidence, and the single decisive detail. Typed via response_format.
The caller is never asked anything. If anything fails, the court abstains (exit code 2).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

JUDGE_MODEL = os.environ.get("PINPOINT_COURT_JUDGE", "claude-opus-5-5")
SCOUT_MODEL = os.environ.get("PINPOINT_COURT_SCOUT", "claude-haiku-5-5")
EVIDENCE_MODEL = os.environ.get("PINPOINT_COURT_EVIDENCE", "claude-haiku-5-5")


class Comparison(BaseModel):
    matching_details: list[str] = Field(description="specific things visible in BOTH the caller photo and these views")
    conflicting_details: list[str] = Field(description="things in the caller photo that these views contradict, or vice versa")
    same_place_score: int = Field(ge=0, le=10, description="0 = clearly elsewhere, 10 = certainly the same spot")


class Case(BaseModel):
    candidate: str = Field(description="candidate letter")
    argument: str = Field(description="two or three sentences: the strongest honest case FOR this spot")
    weaknesses: str = Field(description="one or two sentences: what argues against it")
    strength: int = Field(ge=0, le=10)


class Verdict(BaseModel):
    winner: str = Field(description="letter of the candidate where the caller most likely is")
    confidence: float = Field(ge=0, le=1, description="probability the caller is at the winning spot")
    decisive_detail: str = Field(description="the one visible detail that settled it, in plain words for a dispatcher")
    ruling: str = Field(description="two sentences a dispatcher can read in five seconds")
    cases: list[Case] = Field(description="each scout's case, one per candidate")


def _b64(path):
    return base64.standard_b64encode(Path(path).read_bytes()).decode()


def _image(path):
    media = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": _b64(path)}}


def gather_evidence(case, client):
    """Claude compares the caller photo with each candidate's views, all candidates at once."""
    def compare(c):
        content = [{"type": "text", "text": "The CALLER'S PHOTO:"}, _image(case["caller_photo"]),
                   {"type": "text", "text": f"Scanned views at candidate {c['id']} ({c['label']}):"}]
        content += [_image(v) for v in c["views"][:5]]
        content.append({"type": "text", "text": (
            "Is the caller's photo taken at this scanned spot? Compare concrete details: signs, "
            "doors, railings, columns, wall and floor finishes, ceiling, lighting, furniture, "
            "windows, layout. Lighting and camera angle differ between a phone photo and a scan; "
            "do not count those as conflicts.")})
        r = client.messages.parse(model=EVIDENCE_MODEL, max_tokens=16000, output_config={"effort": "low"},
                                  messages=[{"role": "user", "content": content}], output_format=Comparison)
        if r.stop_reason != "end_turn" or r.parsed_output is None:
            return {"matching_details": [], "conflicting_details": [f"no comparison ({r.stop_reason})"],
                    "same_place_score": 0}
        return r.parsed_output.model_dump()
    with ThreadPoolExecutor(max_workers=len(case["candidates"])) as pool:
        return dict(zip((c["id"] for c in case["candidates"]), pool.map(compare, case["candidates"])))


def hear(case):
    import anthropic
    from deepagents import create_deep_agent

    client = anthropic.Anthropic()
    t0 = time.time()
    evidence = gather_evidence(case, client)
    t_evidence = time.time() - t0
    by_id = {c["id"]: c for c in case["candidates"]}

    def candidate_file(candidate: str) -> str:
        """Everything known about one candidate spot: scan facts, photo-match scores, and the
        side-by-side comparison of the caller's photo with that spot's scanned views."""
        c = by_id.get(candidate.strip().upper()[:1])
        if c is None:
            return f"No candidate {candidate!r}. Candidates: {', '.join(by_id)}."
        return json.dumps({"candidate": c["id"], "spot": c["label"], "floor": c.get("floor"),
                           "photo_match": {"similarity": c.get("similarity"), "matched_points": c.get("inliers")},
                           "scan_facts": c.get("facts", {}), "comparison_with_caller_photo": evidence[c["id"]]},
                          indent=1)

    scouts = [{
        "name": f"scout-{c['id'].lower()}",
        "description": f"Advocate for candidate {c['id']}: argues the caller is at {c['label']}.",
        "system_prompt": (
            f"You are the scout for candidate {c['id']} ({c['label']}) in an emergency-dispatch evidence "
            f"review. A caller texted a photo; we must decide where in the building they are. Call "
            f"candidate_file('{c['id']}') once, then make the strongest HONEST case that the caller is at "
            "your spot, citing concrete visible details. Then state plainly what argues against it. Never "
            "invent details that are not in the file. Reply in under 90 words: ARGUMENT, WEAKNESSES, "
            "STRENGTH (0-10)."),
        "tools": [candidate_file],
        "model": f"anthropic:{SCOUT_MODEL}",
    } for c in case["candidates"]]

    judge = create_deep_agent(
        model=f"anthropic:{JUDGE_MODEL}",
        tools=[candidate_file],
        system_prompt=(
            "You are the judge in an emergency-dispatch evidence court. A caller texted a photo from "
            f"{case['building']} ({case['address']}); the photo match could not settle which spot they "
            "are at. Each candidate spot has a scout subagent. In ONE turn, send every scout out in "
            "parallel with the task tool (one task call per scout). When their cases return, you may "
            "check a candidate_file yourself, then rule. Decide on visible evidence, not on which scout "
            "argued loudest. Your confidence must be honest: if two spots are genuinely indistinguishable, "
            "say so with a low confidence. The decisive detail is shown to a dispatcher; keep it concrete "
            "(e.g. 'the red EXIT sign above a wood-panelled alcove')."),
        subagents=scouts,
        response_format=Verdict,
        name="evidence-court",
    )
    listing = "\n".join(f"- {c['id']}: {c['label']} (photo-match similarity {c.get('similarity')})"
                        for c in case["candidates"])
    t1 = time.time()
    result = judge.invoke({"messages": [{"role": "user", "content": [
        {"type": "text", "text": f"Caller said: \"{case.get('caller_text') or '(nothing)'}\"\nCandidates:\n{listing}\n"
                                 "The caller's photo is attached. Hear the case and rule."},
        _image(case["caller_photo"])]}]}, config={"recursion_limit": 60})
    verdict = result.get("structured_response")
    if verdict is None:
        raise RuntimeError("the judge returned no verdict")
    v = verdict.model_dump() if hasattr(verdict, "model_dump") else dict(verdict)
    winner = by_id.get(str(v["winner"]).strip().upper()[:1])
    if winner is None:
        raise RuntimeError(f"the judge named an unknown candidate {v['winner']!r}")
    for c in v["cases"]:
        spot = by_id.get(str(c["candidate"]).strip().upper()[:1])
        c["label"] = spot["label"] if spot else c["candidate"]
        c["evidence"] = evidence.get(spot["id"]) if spot else None
    return dict(v, node_id=winner["node_id"], label=winner["label"],
                models={"judge": JUDGE_MODEL, "scouts": SCOUT_MODEL, "evidence": EVIDENCE_MODEL},
                seconds={"evidence": round(t_evidence, 1), "deliberation": round(time.time() - t1, 1)},
                scouts=len(scouts))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    case = json.loads(Path(argv[0]).read_text())
    try:
        print(json.dumps(hear(case)))
    except Exception as exc:                      # the court abstains; the photo match stands
        print(json.dumps({"abstained": f"{type(exc).__name__}: {str(exc)[:300]}"}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
