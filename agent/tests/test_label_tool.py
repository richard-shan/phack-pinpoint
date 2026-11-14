"""Tests for the hand-labeling CLI.
Run: python3 agent/tests/test_label_tool.py (stdlib unittest, no deps)"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(ROOT))

from eval import label_tool


def _args(building, out, pano=None, from_synth=None):
    class A:
        pass
    a = A()
    a.building = building
    a.out = out
    a.pano = pano
    a.from_synth = from_synth
    return a


def _run(scripted_inputs, args):
    """Drive label_one with scripted stdin. Returns (rc, output_lines, written_label_dict)."""
    inputs = list(scripted_inputs)
    outputs = []

    def _inp(prompt):
        if not inputs:
            raise AssertionError(f"ran out of scripted inputs at prompt: {prompt!r}")
        return inputs.pop(0)

    def _print(*a, **k):
        outputs.append(" ".join(str(x) for x in a))

    rc = label_tool.label_one(args, input_fn=_inp, print_fn=_print)
    out_dir = Path(args.out)
    files = sorted(out_dir.glob("labeled_*.json"))
    label = json.loads(files[-1].read_text()) if files else None
    return rc, outputs, label, files


class TestLabelTool(unittest.TestCase):

    def test_all_defaults_produces_valid_schema(self):
        """Hitting enter at every prompt writes a valid labeled JSON against bayou."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, _out, label, files = _run(
                ["", "", "", ""],  # room, node (or skipped), entry, caller_text
                _args("building_bayou", tmp),
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].name.startswith("labeled_labeled-"))
            self.assertFalse(files[0].name.endswith(".partial.json"))
            for k in ("incident_id", "address", "building_id", "caller_photo_path",
                      "caller_text", "ground_truth_node_id", "ground_truth_room_id",
                      "ground_truth_best_entry_id"):
                self.assertIn(k, label)
            self.assertEqual(label["building_id"], "bayou-medical-plaza")
            self.assertTrue(label["address"].startswith("1200 Smith"))
            # Default best_entry on bayou (foot profile) resolves to a real entrance id.
            self.assertIn(label["ground_truth_best_entry_id"],
                          {"ext_main", "ext_dock", "ext_side"})
            # Default caller text is the first template.
            self.assertEqual(label["caller_text"], label_tool.CALLER_TEMPLATES[0])

    def test_targeted_pick_suite_210_treatment_floor(self):
        """Pick the Suite 210 Treatment Floor room; confirm node + entry + text land right."""
        with tempfile.TemporaryDirectory() as tmp:
            # Discover which room index corresponds to Suite 210 Treatment Floor.
            graph = json.loads(
                (label_tool.FIXTURES_DIR / "building_bayou" / "graph.json").read_text()
            )
            by_floor = label_tool._interior_nodes_by_floor(graph)
            rooms = label_tool._group_rooms(by_floor)
            suite_idx = next(i for i, (_, lbl, _) in enumerate(rooms)
                             if lbl == "Suite 210 Treatment Floor")
            rc, _out, label, _files = _run(
                [str(suite_idx + 1),  # room pick (1-indexed)
                 # Suite 210 Treatment Floor has one node; node step auto-selects, no prompt.
                 "2",   # entrance pick: ext_dock (index 2 in site.json order)
                 "3"],  # caller text: template 3
                _args("building_bayou", tmp),
            )
            self.assertEqual(rc, 0)
            self.assertEqual(label["ground_truth_room_id"], "Suite 210 Treatment Floor")
            self.assertEqual(label["ground_truth_node_id"], "suite210_interior")
            self.assertEqual(label["ground_truth_best_entry_id"], "ext_dock")
            self.assertEqual(label["caller_text"], label_tool.CALLER_TEMPLATES[2])

    def test_freeform_caller_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _out, label, _files = _run(
                ["", "", "help my grandfather is unconscious near the pharmacy"],
                _args("building_bayou", tmp),
            )
            # If chosen room has multiple nodes, 3 inputs is too few. Use first-room
            # default (Suite 210 Treatment Floor on this fixture after alphabetical sort
            # may differ) - but by always-default path the single-node rooms get skipped.
            # The existing fixture's default (floor-sorted alphabetical) first room is
            # "F1 East Corridor" (single node), then node step is auto-skipped.
            self.assertEqual(rc, 0)
            self.assertIn("pharmacy", label["caller_text"])

    def test_from_synth_prepopulates_defaults(self):
        """A synth incident file as draft: defaults land on the synth's values."""
        with tempfile.TemporaryDirectory() as tmp:
            # Grab any synth file.
            synth_dir = ROOT / "eval" / "fixtures" / "synth_bayou"
            synth_files = sorted(synth_dir.glob("synth-*.json"))
            self.assertGreater(len(synth_files), 0, "need synth fixtures to seed the test")
            synth = json.loads(synth_files[0].read_text())
            rc, _out, label, _files = _run(
                ["", "", "", ""],
                _args("building_bayou", tmp, from_synth=str(synth_files[0])),
            )
            self.assertEqual(rc, 0)
            self.assertEqual(label["ground_truth_room_id"], synth["ground_truth_room_id"])
            self.assertEqual(label["ground_truth_node_id"], synth["ground_truth_node_id"])
            self.assertEqual(label["ground_truth_best_entry_id"],
                             synth["ground_truth_best_entry_id"])
            self.assertEqual(label["caller_text"], synth["caller_text"])

    def test_keyboard_interrupt_writes_partial(self):
        """Ctrl+C mid-labeling leaves a .partial.json with whatever was collected."""
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"n": 0}

            def _inp(prompt):
                calls["n"] += 1
                if calls["n"] == 1:
                    return "1"  # pick first room
                raise KeyboardInterrupt

            def _print(*a, **k):
                pass

            args = _args("building_bayou", tmp)
            rc = label_tool.label_one(args, input_fn=_inp, print_fn=_print)
            self.assertEqual(rc, 130)
            partials = list(Path(tmp).glob("labeled_*.partial.json"))
            self.assertEqual(len(partials), 1)
            partial = json.loads(partials[0].read_text())
            # Room was captured before the interrupt.
            self.assertTrue(partial["ground_truth_room_id"])

    def test_missing_building_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = label_tool.label_one(
                _args("does_not_exist", tmp),
                input_fn=lambda _: "",
                print_fn=lambda *a, **k: None,
            )
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
