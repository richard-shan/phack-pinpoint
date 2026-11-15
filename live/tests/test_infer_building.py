import unittest
from live.infer_building import choose


class BuildingChoiceTests(unittest.TestCase):
    def test_strong_distinct_match(self):
        self.assertEqual(choose([{'building_id': 'a', 'inliers': 100}, {'building_id': 'b', 'inliers': 30}]), 'a')

    def test_weak_and_ambiguous_matches_are_not_selected(self):
        self.assertIsNone(choose([{'building_id': 'a', 'inliers': 59}, {'building_id': 'b', 'inliers': 0}]))
        self.assertIsNone(choose([{'building_id': 'a', 'inliers': 100}, {'building_id': 'b', 'inliers': 85}]))
        self.assertIsNone(choose([{'building_id': 'a', 'inliers': 100}]))

if __name__ == '__main__':
    unittest.main()
