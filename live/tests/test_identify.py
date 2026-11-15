"""Building rejection decisions do not use within-building probabilities."""
import unittest
from perception.identify import decide


def candidate(building='a', **kw):
    return dict(building_id=building, similarity=0.5, inliers=150,
                inlier_ratio=0.8, coverage=0.4, **kw)


class DecisionTests(unittest.TestCase):
    def test_strong_match(self):
        other = candidate('b'); other.update(similarity=0.2, inliers=10)
        self.assertEqual(decide([other, candidate()])['building_id'], 'a')

    def test_weak_and_nonfinite_evidence_is_unknown(self):
        for field, value in [('similarity', 0.1), ('inliers', 5), ('inlier_ratio', 0.1),
                             ('coverage', 0.01), ('similarity', float('nan'))]:
            row = candidate(); row[field] = value
            self.assertEqual(decide([row])['status'], 'unknown')

    def test_competing_buildings_are_unknown(self):
        self.assertEqual(decide([candidate(), candidate('b')])['reason'], 'ambiguous')

    def test_no_indexes(self):
        self.assertEqual(decide([])['reason'], 'no_indexes')
