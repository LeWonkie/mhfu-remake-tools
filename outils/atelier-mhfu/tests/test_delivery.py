"""Synthetic delivery contracts: no guest data or running game."""
from contextlib import ExitStack
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deliver


class DeliveryTests(unittest.TestCase):
    def test_only_single_item_deliveries_are_supported(self):
        goal = {"flags": 18, "target": 173, "quantity": 5}
        empty = {"flags": 0, "target": 0, "quantity": 0}
        self.assertEqual(deliver.objective({"initial": [empty, goal]}), goal)
        for entries in ([goal, goal], [empty], [dict(goal, flags=17)],
                        [dict(goal, flags=22)], [dict(goal, quantity=100)], [dict(goal, target=0)]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                deliver.objective({"initial": entries})

    def test_box_layout_keeps_position_radius_and_pointer(self):
        raw = struct.pack("<HH4fH2x", 7, 33, 15000, 1250, 15850, 200, 1234)
        box = deliver.parse_interactions(raw, 0x09000000)[0]
        self.assertEqual(box, {"address": 0x09000000, "id": 7, "type": 33,
                              "position": [15000, 1250, 15850], "radius": 200, "angle": 1234})

    def test_bad_volumes_and_truncated_table_are_rejected(self):
        for raw in (b"x", struct.pack("<HH4fH2x", 1, 33, float("nan"), 0, 0, 200, 0),
                    struct.pack("<HH4fH2x", 1, 33, 0, 0, 0, 0, 0)):
            with self.subTest(raw=raw), self.assertRaises(deliver.zones.Unavailable):
                deliver.parse_interactions(raw, 0x09000000)

    def test_no_ambiguous_or_invalid_table_is_used(self):
        area = {"definitions": 0x09100000}
        row = struct.pack("<HH4fH2x", 1, 33, 0, 0, 0, 200, 0)
        with patch.object(deliver.zones, "read_many", return_value=[struct.pack("<I", 0x09200000), b"\xff"]):
            with self.assertRaises(deliver.zones.Unavailable):
                deliver.delivery_box(Mock(), area)
        for raw in (row + row, struct.pack("<HH4fH2x", 1, 24, 0, 0, 0, 200, 0)):
            with patch.object(deliver.zones, "read_many", return_value=[
                    struct.pack("<I", 0x09200000), bytes([len(raw) // 24])]), \
                    patch.object(deliver.zones, "read", return_value=raw):
                with self.assertRaises(deliver.zones.Unavailable):
                    deliver.delivery_box(Mock(), area)

    def exercise(self, *, active_pouch=True, mission=3, remaining=0, missing_capture=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            session = Mock(run_id="synthetic", dir=Path(directory))
            session.ask.return_value = "[debug] pouch slot 1: item 173 x5"
            pilot = Mock(waits=[])
            pilot.wait_quest.return_value = {"stage": 98}
            pilot.confirm_shots.side_effect = lambda names: [] if missing_capture else names
            pilot.shoot.side_effect = lambda name, frames: name
            pilot.until.side_effect = [True, {"mission": mission}, {"task": 10}]
            workshop = Mock()
            stack.enter_context(patch.object(deliver.atelier_campaign, "Session", return_value=session))
            stack.enter_context(patch.object(deliver.atelier_pilot, "Pilot", return_value=pilot))
            initial = [{"flags": 0, "target": 0, "quantity": 0},
                       {"flags": 18, "target": 173, "quantity": 5}]
            snapshots = [{"initial": initial, "current": initial},
                         {"initial": initial, "current": [initial[0],
                          dict(initial[1], flags=50, quantity=remaining)]}]
            stack.enter_context(patch.object(deliver.zones, "mission_objectives", side_effect=snapshots))
            stack.enter_context(patch.object(deliver.zones, "u32", return_value=0x09000000))
            raw = struct.pack("<Hh", 173 if active_pouch else 0, 5) + bytes(92)
            stack.enter_context(patch.object(deliver.zones, "read", return_value=raw))
            stack.enter_context(patch.object(deliver.zones, "current", return_value={"stage": 98, "player": 1}))
            stack.enter_context(patch.object(deliver, "delivery_box", return_value={
                "position": [15000, 1250, 15850], "radius": 200, "address": 2}))
            place = stack.enter_context(patch.object(deliver.zones, "place"))
            try:
                result = deliver.deliver(workshop, "synthetic-seed", 10001)
            finally:
                workshop.stop.assert_called_once()
            self.assertTrue((Path(directory) / "delivery.json").is_file())
            return result, session, place

    def test_supplied_delivery_requires_game_clear_and_return(self):
        result, session, place = self.exercise()
        self.assertTrue(result["success"])
        self.assertTrue(result["village_ready"])
        self.assertFalse(result["gathering_validated"])
        place.assert_called_once_with(session, 1, (14950, 1250, 15850))
        session.inputs.assert_called_once_with(["0:pad b 6"])
        self.assertIn("pouch 173 5", [call.args[0] for call in session.ask.call_args_list])

    def test_saved_pouch_only_does_not_trigger_delivery(self):
        result, session, place = self.exercise(active_pouch=False)
        self.assertFalse(result["success"])
        self.assertIn("active quest inventory", result["reason"])
        place.assert_not_called()
        session.inputs.assert_not_called()

    def test_missing_evidence_stops_before_supplying_items(self):
        result, session, place = self.exercise(missing_capture=True)
        self.assertEqual(result["state"], "incomplete")
        self.assertFalse(result["success"])
        session.ask.assert_not_called()
        place.assert_not_called()

    def test_log_write_failure_still_stops_the_game(self):
        with patch.object(deliver.atelier_core, "_write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.exercise()

    def test_failed_mission_or_remaining_quantity_is_not_success(self):
        for kwargs in ({"mission": 5}, {"remaining": 5}):
            with self.subTest(kwargs=kwargs):
                result, _, _ = self.exercise(**kwargs)
                self.assertFalse(result["success"])

    def test_pre_cancelled_run_starts_no_game(self):
        cancel = threading.Event()
        cancel.set()
        workshop = Mock()
        with patch.object(deliver.atelier_campaign, "Session") as session:
            result = deliver.deliver(workshop, "seed", 10001, cancel=cancel)
        self.assertEqual(result["state"], "cancelled")
        session.assert_not_called()
        workshop.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
