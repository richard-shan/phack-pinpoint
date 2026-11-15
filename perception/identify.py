"""Open-set building retrieval over locally indexed, routable buildings.

Scores are evidence heuristics, NOT calibrated building probabilities. Search every
available index, geometrically verify its best views, and abstain on weak/tied evidence.
"""
import math
from pathlib import Path
import tempfile

MIN_SIMILARITY = 0.25
MIN_INLIERS = 60
MIN_INLIER_RATIO = 0.5
MIN_COVERAGE = 0.04
MIN_MARGIN = 0.08
MIN_INLIER_ADVANTAGE = 1.5


def decide(candidates):
    """Pure rejection policy, kept separate so ambiguity and unknowns are testable."""
    if any(not all(math.isfinite(c[k]) for k in ('similarity', 'inliers', 'inlier_ratio', 'coverage'))
           for c in candidates):
        return {'status': 'unknown', 'building_id': None, 'candidates': [], 'reason': 'invalid_evidence'}
    ranked = sorted(candidates, key=lambda c: c['similarity'] + 0.08 * math.log1p(c['inliers']), reverse=True)
    result = {'status': 'unknown', 'building_id': None, 'candidates': ranked}
    if not ranked:
        return dict(result, reason='no_indexes')
    top = ranked[0]
    if (not all(math.isfinite(top[k]) for k in ('similarity', 'inliers', 'inlier_ratio', 'coverage'))
            or top['similarity'] < MIN_SIMILARITY or top['inliers'] < MIN_INLIERS
            or top['inlier_ratio'] < MIN_INLIER_RATIO or top['coverage'] < MIN_COVERAGE):
        return dict(result, reason='weak_evidence')
    if len(ranked) > 1:
        other = ranked[1]
        margin = (top['similarity'] - other['similarity']
                  + 0.08 * math.log((1 + top['inliers']) / (1 + other['inliers'])))
        if margin < MIN_MARGIN or top['inliers'] < MIN_INLIER_ADVANTAGE * max(c['inliers'] for c in ranked[1:]):
            return dict(result, reason='ambiguous')
    return dict(result, status='matched', building_id=top['building_id'], reason='verified')


def identify_building(image_path, buildings):
    """Search all available indexes, with three positions/two views per building.

    Missing indexes are outside coverage. A broken existing index fails the search
    rather than silently removing a possible competing building.
    """
    import numpy as np
    from .embed import embed
    from .index import index_dir, load
    from .match import _read_photo, _view_image
    from .rerank import match_points

    available = [str(b) for b in sorted(buildings)
                 if (Path(index_dir(b)) / 'emb.npy').exists()]
    if not available:
        return dict(decide([]), searched_buildings=[])
    with tempfile.TemporaryDirectory(prefix='pinpoint-identify-') as directory:
        img = _read_photo(str(image_path), directory)
        # Blank/near-blank images should not make an expensive model call.
        if float(img.std()) < 3:
            return {'status': 'unknown', 'building_id': None, 'reason': 'weak_evidence',
                    'candidates': [], 'searched_buildings': available}
        query = embed([img])[0]
        candidates = []
        for building in available:
            emb, meta = load(building)
            rows = meta['rows']
            if not rows or len(rows) != len(emb):
                raise ValueError('Invalid building index')
            sims = emb @ query
            nodes = {}
            for i, row in enumerate(rows):
                nodes.setdefault(row['node_id'], []).append(i)
            pooled = []
            for node, indices in nodes.items():
                order = sorted(indices, key=lambda i: float(sims[i]), reverse=True)
                pooled.append((float(np.mean(sims[order[:3]])), node, order[:2]))
            pooled.sort(reverse=True)
            views = [(sim, node, i) for sim, node, indices in pooled[:3] for i in indices]
            # One pair at a time keeps memory bounded on laptop GPUs.
            matches = match_points(img, [_view_image(rows[i]) for _, _, i in views], batch=1)
            verified = []
            for (sim, node, i), match in zip(views, matches):
                points = match['pts_query']
                coverage = float(np.prod(np.ptp(points, axis=0))) if len(points) else 0.0
                verified.append({'building_id': building, 'node_id': node, 'similarity': sim,
                                 'inliers': int(match['inliers']),
                                 'inlier_ratio': match['inliers'] / max(match['matches'], 1),
                                 'coverage': coverage})
            candidates.append(max(verified, key=lambda c: c['similarity'] + 0.08 * math.log1p(c['inliers'])))
    return dict(decide(candidates), searched_buildings=available)
