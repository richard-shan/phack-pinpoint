"""Active localization — the part of Part C that is REAL code, not a stub (plan 4, 'active
localization'). The Locator keeps a BELIEF over candidate positions and treats questions as
ACTIONS. These two functions are C's own IP:

  best_question(candidates)                 -> attribute with highest expected information gain
  update_belief(candidates, attribute, ans) -> reweighted, renormalized candidates

The LLM phrases the question and relays the caller's answer; the math lives here. ~both ~60 lines.
"""
import math
from .config import ANSWER_RELIABILITY

# Human phrasings for the attributes perception (Person B) attaches to each node.
QUESTION_HINTS = {
    "floor":         "What floor are you on, or how many flights did you come up?",
    "has_window":    "Is there a window near you?",
    "near_elevator": "Can you see elevator doors from where you are?",
    "floor_surface": "Is the floor carpet, tile, or bare concrete?",
    "room_type":     "What does the space look like — waiting room, treatment area, hallway?",
}


def normalize_belief(candidates):
    """Return candidates with a normalized 'belief' field (derived from 'score' first time)."""
    raw = [max(c.get("belief", c.get("score", 0.0)), 1e-9) for c in candidates]
    total = sum(raw)
    for c, r in zip(candidates, raw):
        c["belief"] = r / total
    return candidates


def top_confidence(candidates):
    normalize_belief(candidates)
    return max((c["belief"] for c in candidates), default=0.0)


def _entropy(weights):
    return -sum(w * math.log(w, 2) for w in weights if w > 0)


def best_question(candidates):
    """Pick the attribute whose answer is expected to shrink the belief's entropy the most
    (max expected information gain). Only attributes present on every candidate are eligible,
    so the partition is well defined. Returns the attribute + a phrasing hint + diagnostics."""
    normalize_belief(candidates)
    prior_h = _entropy([c["belief"] for c in candidates])

    attrs = set(candidates[0].get("attributes", {}))
    for c in candidates[1:]:
        attrs &= set(c.get("attributes", {}))
    attrs.discard("floor") if False else None  # floor stays eligible; it's a strong splitter

    best = None
    for attr in sorted(attrs):
        # group candidate belief mass by the answer that candidate implies
        groups = {}
        for c in candidates:
            v = c["attributes"][attr]
            groups.setdefault(_key(v), []).append(c["belief"])
        # expected posterior entropy = sum_v P(v) * H(belief | v)
        exp_post = 0.0
        for mass in groups.values():
            p_v = sum(mass)
            if p_v <= 0:
                continue
            post = [m / p_v for m in mass]
            exp_post += p_v * _entropy(post)
        gain = prior_h - exp_post
        cand = {
            "attribute": attr,
            "question_hint": QUESTION_HINTS.get(attr, f"Can you tell me about: {attr}?"),
            "expected_info_gain": round(gain, 4),
            "split": {k: round(sum(m), 3) for k, m in groups.items()},
            "eligible": len(groups) > 1,  # a question with one answer teaches nothing
        }
        if cand["eligible"] and (best is None or gain > best["expected_info_gain"]):
            best = cand

    if best is None:  # everything agrees on every attribute — no question helps
        return {"attribute": None, "question_hint": None, "expected_info_gain": 0.0,
                "split": {}, "eligible": False}
    return best


def update_belief(candidates, attribute, answer):
    """Soft Bayesian reweight given the caller's answer. A candidate whose attribute matches the
    answer is boosted by the answer-reliability r; mismatches are damped by (1-r). This is soft on
    purpose — callers under stress mis-answer, so a single answer never zeroes a candidate out."""
    normalize_belief(candidates)
    r = ANSWER_RELIABILITY
    ans = _key(answer)
    for c in candidates:
        matches = _key(c.get("attributes", {}).get(attribute)) == ans
        likelihood = r if matches else (1.0 - r)
        c["belief"] = c["belief"] * likelihood
    normalize_belief(candidates)
    return sorted(candidates, key=lambda c: c["belief"], reverse=True)


def _key(v):
    """Canonicalize an attribute value for comparison (bools, numbers, strings)."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return str(v)
    return str(v).strip().lower()
