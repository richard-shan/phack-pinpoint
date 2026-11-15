# Belief over candidate rooms + which question to ask next.
import math
from .config import ANSWER_RELIABILITY

QUESTION_HINTS = {
    "floor":         "What floor are you on, or how many flights did you come up?",
    "has_window":    "Is there a window near you?",
    "near_elevator": "Can you see elevator doors from where you are?",
    "floor_surface": "Is the floor carpet, tile, or bare concrete?",
    "room_type":     "What does the space look like — waiting room, treatment area, hallway?",
}


def normalize_belief(candidates):
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


def best_question(candidates, exclude=None):
    # Attribute with highest expected info gain. Only attrs present on every candidate.
    """Choose the caller question whose answer is expected to shrink the belief entropy the most across the current candidate set. Returns the attribute, a phrasing hint, and the expected information gain."""
    normalize_belief(candidates)
    prior_h = _entropy([c["belief"] for c in candidates])

    attrs = set(candidates[0].get("attributes", {}))
    for c in candidates[1:]:
        attrs &= set(c.get("attributes", {}))
    attrs -= set(exclude or ())

    best = None
    for attr in sorted(attrs):
        groups = {}
        for c in candidates:
            v = c["attributes"][attr]
            groups.setdefault(_key(v), []).append(c["belief"])
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
            "eligible": len(groups) > 1,
        }
        if cand["eligible"] and (best is None or gain > best["expected_info_gain"]):
            best = cand

    if best is None:
        return {"attribute": None, "question_hint": None, "expected_info_gain": 0.0,
                "split": {}, "eligible": False}
    return best


def update_belief(candidates, attribute, answer):
    # Soft Bayes update — mismatches damp, never zero (callers mis-answer under stress).
    """Soft-update candidate beliefs given the caller's answer. Matching candidates boosted by answer-reliability; non-matching dampened; nothing zeroed. Returns the re-sorted candidates."""
    normalize_belief(candidates)
    r = ANSWER_RELIABILITY
    ans = _key(answer)
    for c in candidates:
        matches = _key(c.get("attributes", {}).get(attribute)) == ans
        c["belief"] = c["belief"] * (r if matches else (1.0 - r))
    normalize_belief(candidates)
    return sorted(candidates, key=lambda c: c["belief"], reverse=True)


def _key(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return str(v)
    return str(v).strip().lower()
