"""Tests for the Vexcel site integration. No live network — every path is mocked.
Run: python3 agent/tests/test_vexcel.py
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent.pinpoint import stubs, vexcel, trace
from agent.pinpoint.config import FIXTURES


def _init_trace():
    tmp = Path(tempfile.mkdtemp(prefix="pinpoint-trace-"))
    trace.start(tmp)
    return tmp


class VexcelFallbackTests(unittest.TestCase):
    def setUp(self):
        _init_trace()

    def test_fallback_when_vexcel_unavailable(self):
        """When vexcel.fetch_aerial raises, detect_site_features returns fixture data
        with source='fixture_only' and no aerial_tile_path."""
        with mock.patch.object(vexcel, "fetch_aerial",
                               side_effect=vexcel.VexcelUnavailable("network down")):
            out = stubs.detect_site_features("bayou-medical-plaza",
                                             address="1200 Smith St", lat=29.75, lon=-95.37)
        self.assertEqual(out.get("source"), "fixture_only")
        self.assertNotIn("aerial_tile_path", out)
        self.assertIn("entrances", out)
        self.assertIn("staging", out)

    def test_merge_when_vexcel_returns(self):
        """When vexcel.fetch_aerial returns, result has aerial_tile_path + aerial_features
        merged over the fixture baseline and source='vexcel+fixture'."""
        fake = {
            "tile_path": "pinpoint/fixtures/building_bayou/vexcel_tile.jpg",
            "tile_url": "https://api.vexcelgroup.com/v2/ortho/extract",
            "features": {"source": "vexcel.property/extract",
                         "structure_count": 1, "parcel_tree_pct": 12.3,
                         "pool_detected": False,
                         "primary_roof_material": "metal",
                         "primary_roof_shape": "flat",
                         "primary_roof_condition": "good",
                         "primary_footprint_sqft": 27000},
            "address": "1200 Smith St"}
        with mock.patch.object(vexcel, "fetch_aerial", return_value=fake):
            out = stubs.detect_site_features("bayou-medical-plaza",
                                             address="1200 Smith St", lat=29.75, lon=-95.37)
        self.assertEqual(out.get("source"), "vexcel+fixture")
        self.assertEqual(out.get("aerial_tile_path"), fake["tile_path"])
        self.assertEqual(out["aerial_features"]["structure_count"], 1)
        # fixture baseline still present
        self.assertIn("staging", out)
        self.assertIn("entrances", out)


class AuditTests(unittest.TestCase):
    """Packet audit still shows 0 unsupported when aerial path is present."""

    def test_aerial_line_passes_audit(self):
        _init_trace()
        from agent.pinpoint.packet import PacketWriter

        tmp = Path(tempfile.mkdtemp(prefix="pinpoint-pkt-"))
        pkt = PacketWriter(tmp / "packet.md",
                           {"address": "1200 Smith St, Houston, TX"})
        brief = {"building_id": "x", "address": "x", "use_type": "x",
                 "stories": 1, "year_built": 2000, "sqft": 1000,
                 "located_tenant": {"name": "T", "suite": "1",
                                    "occupancy_risk": "low"},
                 "scan_date": "2025-01-01",
                 "contact": {"name": "N", "phone": "P"}}
        location = {"node_id": "n1", "floor": 1, "room_type": "lobby",
                    "confidence": 0.9, "questions_asked": 0,
                    "evidence_path": "x.jpg", "below_threshold": False}
        site = {"staging": [{"label": "East lot", "node_id": "staging_lot"}],
                "aerial_tile_path": "pinpoint/fixtures/building_bayou/vexcel_tile.jpg"}
        route = {"foot": {"entrance_id": "ext_dock", "seconds": 60, "path": ["n1"]},
                 "foot_directions": [{"text": "go in", "meters": 10}],
                 "hazards": [], "equipment": [],
                 "seconds_saved_vs_front": 30,
                 "stretcher_differs": False}
        pkt.set_brief(brief); pkt.set_location(location)
        pkt.set_site(site); pkt.set_route(route)
        rendered = pkt.render()
        self.assertIn("Aerial reference:", rendered)
        self.assertIn("[src:site.aerial_tile_path]", rendered)

        # Now check the Verifier: every tagged claim must be supported.
        sources = {"brief": brief, "location": location, "site": site, "route": route}
        results = [stubs.check_claim(c, sources) for c in pkt.tagged_claims()]
        unsupported = [r for r in results if not r["supported"]]
        self.assertEqual(unsupported, [],
                         f"unsupported claims: {[r['claim'] for r in unsupported]}")


class ParseFeaturesTests(unittest.TestCase):
    """Small sanity on _extract_features so we don't ship a bug in the feature map."""

    def test_extract_from_minimal(self):
        rsp = {"features": [{"properties": {
            "structure.count": 2,
            "parcel-tree-cover.percent": 8.1,
            "pool.detected": False,
            "structures": [
                {"primary-structure": True,
                 "roof-characteristics": {"material": {"class": "composition"},
                                          "shape": {"value": "gable"},
                                          "condition": "good"},
                 "footprint": {"area": 1234.4}},
                {"primary-structure": False},
            ],
        }}]}
        out = vexcel._extract_features(rsp)
        self.assertEqual(out["structure_count"], 2)
        self.assertEqual(out["primary_roof_material"], "composition")
        self.assertEqual(out["primary_roof_shape"], "gable")
        self.assertEqual(out["primary_footprint_sqft"], 1234)

    def test_extract_empty(self):
        out = vexcel._extract_features({})
        self.assertIsNone(out["structure_count"])

    def test_load_key_missing_file(self):
        with mock.patch.object(vexcel, "_API_TXT", Path("/does/not/exist.txt")):
            with self.assertRaises(vexcel.VexcelUnavailable):
                vexcel._load_key()


if __name__ == "__main__":
    unittest.main(verbosity=2)
