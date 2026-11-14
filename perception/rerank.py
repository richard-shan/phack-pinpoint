"""Re-rank match candidates by matching specific points (LoFTR), not overall look.

For each candidate view, find point correspondences with the caller's photo and
keep those consistent with one camera geometry (RANSAC on the fundamental matrix).
The count of surviving points is the score: dozens means "the same place", a
handful means "a similar-looking place". The surviving points are also the
evidence overlay.
"""
import cv2
import numpy as np
import torch

SIZE = 448          # both images are resized to SIZE x SIZE for matching
MIN_CONF = 0.2
_state = {}


def _load():
    if not _state:
        from kornia.feature import LoFTR
        dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
        _state["model"] = LoFTR(pretrained="indoor_new").to(dev).eval()
        _state["dev"] = dev
    return _state


def _gray(img):
    g = cv2.cvtColor(cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return torch.from_numpy(g).float()[None, None] / 255.0


def match_points(query, candidates, batch=8):
    """query and candidates are BGR arrays. Returns one dict per candidate:
    {"inliers": n, "matches": m, "pts_query": Nx2, "pts_cand": Nx2} with points as fractions
    (0..1) of each image's width and height."""
    s = _load()
    q = _gray(query)
    out = []
    for i in range(0, len(candidates), batch):
        chunk = candidates[i:i + batch]
        a = torch.cat([q] * len(chunk)).to(s["dev"])
        b = torch.cat([_gray(c) for c in chunk]).to(s["dev"])
        with torch.no_grad():
            r = s["model"]({"image0": a, "image1": b})
        k0, k1 = r["keypoints0"].cpu().numpy(), r["keypoints1"].cpu().numpy()
        conf, idx = r["confidence"].cpu().numpy(), r["batch_indexes"].cpu().numpy()
        for j in range(len(chunk)):
            keep = (idx == j) & (conf >= MIN_CONF)
            p0, p1 = k0[keep], k1[keep]
            raw = int(len(p0))                       # matches before the geometry check
            if len(p0) >= 8:
                try:
                    _, mask = cv2.findFundamentalMat(p0, p1, cv2.USAC_MAGSAC, 2.0, 0.999, 2000)
                except cv2.error:            # degenerate point sets make OpenCV's estimator assert
                    mask = None
                good = mask.ravel().astype(bool) if mask is not None else np.zeros(len(p0), bool)
                p0, p1 = p0[good], p1[good]
            else:
                p0, p1 = p0[:0], p1[:0]
            out.append({"inliers": int(len(p0)), "matches": raw,
                        "pts_query": p0 / SIZE, "pts_cand": p1 / SIZE})
    return out


def overlay(query, candidate, pts_query, pts_cand, out_path, label="", max_lines=60):
    """Caller's photo beside the matched view with lines joining matched points."""
    h = 448
    a = cv2.resize(query, (h, h))
    b = cv2.resize(candidate, (h, h))
    gap = 12
    img = np.hstack([a, np.full((h, gap, 3), 255, np.uint8), b])
    n = len(pts_query)
    pick = np.linspace(0, n - 1, min(n, max_lines)).astype(int) if n else []
    for i in pick:
        p = (int(pts_query[i][0] * h), int(pts_query[i][1] * h))
        q = (int(pts_cand[i][0] * h) + h + gap, int(pts_cand[i][1] * h))
        color = tuple(int(c) for c in cv2.applyColorMap(np.uint8([[int(255 * i / max(n - 1, 1))]]), cv2.COLORMAP_TURBO)[0, 0])
        cv2.line(img, p, q, color, 1, cv2.LINE_AA)
        cv2.circle(img, p, 3, color, -1, cv2.LINE_AA)
        cv2.circle(img, q, 3, color, -1, cv2.LINE_AA)
    if label:
        cv2.rectangle(img, (0, 0), (img.shape[1], 30), (0, 0, 0), -1)
        cv2.putText(img, label, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(out_path, img)
    return out_path
