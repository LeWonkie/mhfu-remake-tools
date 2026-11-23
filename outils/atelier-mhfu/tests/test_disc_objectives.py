"""Offline objectives: deliveries, compound goals, special types and precedence."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from test_quest_catalog import mission_file, FakeDisc
import atelier_hunt_state as state
import derive_objectives as derive
import mhfu_data


def mission(quest, entries):
    data = bytearray(mission_file(quest))
    for index, entry in enumerate(entries):
        struct.pack_into("<IHH", data, 0x6C + 8 * index, *entry)
    return bytes(data)


class DiscObjectivesTests(unittest.TestCase):
    def test_file_types_and_runtime_counters(self):
        for quest, descriptor, kind, flags in (
                (1020, (1, 6, 1), "hunt", 0x11), (11001, (0x201, 5, 20), "slay", 0x211),
                (21001, (0x201, 6, 1), "slay", 0x211)):
            record, goal = derive.disc_mission(mission(quest, [descriptor]), "synthetic")
            self.assertEqual(record["kind"], kind)
            self.assertEqual((goal["species"], goal["required"]), ([descriptor[1]], descriptor[2]))
            self.assertEqual(goal["progress_counter"]["flags"], flags)
            snapshot = {"quest": quest,
                        "initial": [{"flags": flags, "target": descriptor[1], "quantity": descriptor[2]},
                                    {"flags": 0, "target": 0, "quantity": 0}],
                        "current": [{"flags": flags, "target": descriptor[1], "quantity": descriptor[2] - 1},
                                    {"flags": 0, "target": 0, "quantity": 0}]}
            self.assertEqual(state.objective_progress(snapshot, goal)["completed"], 1)
            snapshot["initial"][0]["flags"] ^= 0x200
            self.assertIsNone(state.objective_progress(snapshot, goal))

    def test_both_species_are_required_and_no_single_counter_is_offered(self):
        record, goal = derive.disc_mission(mission(10501, [(1, 11, 1), (1, 1, 1)]), "synthetic")
        self.assertEqual((goal["species"], goal["required"]), ([1, 11], 2))
        self.assertEqual(len(record["descriptors"]), 2)
        self.assertNotIn("progress_counter", goal)

    def test_delivery_is_recorded_but_never_automated_as_a_hunt(self):
        for target, quantity in ((173, 5), (927, 1), (928, 1)):
            record, goal = derive.disc_mission(mission(10001, [(2, target, quantity)]), "synthetic")
            self.assertIsNone(goal)
            self.assertEqual(record["kind"], "delivery")
            self.assertEqual(record["items"], [{"item": target, "quantity": quantity}])

    def test_special_types_invalid_values_empty_and_placeholder_are_excluded(self):
        for entries in ([], [(0x18004, 60, 28)], [(1, 0, 1)], [(1, 128, 1)], [(1, 6, 0)],
                        [(1, 6, 1001)], [(1, 6, 600), (1, 7, 600)], [(1, 6, 1), (2, 173, 5)]):
            record, goal = derive.disc_mission(mission(7, entries), "synthetic")
            self.assertIsNone(goal)
            self.assertIn("automation_exclusion", record)
        with patch.object(derive.quest_catalog, "mission", return_value={
                "id": 50001, "title": "**********", "objective": ""}):
            self.assertIsNone(derive.disc_mission(mission(50001, [(1, 6, 1)]), "synthetic")[1])

    def test_truncated_wrong_header_and_mismatched_id_fail(self):
        for data in (b"", b"\0" * 0x200):
            with self.assertRaises(ValueError):
                derive.disc_mission(data, "synthetic")
        with self.assertRaises(ValueError):
            derive.disc_mission(mission(1020, [(1, 6, 1)]), "synthetic", expected_id=11001)

    def test_disc_downloaded_and_verified_registry_precedence(self):
        disc = FakeDisc({1020: mhfu_data.encrypt_quest(mission(1020, [(1, 6, 1)])),
                         11001: mhfu_data.encrypt_quest(mission(11001, [(0x201, 99, 3)]))},
                        b"pad" + mission(60001, [(1, 7, 2)]) + mission(1020, [(1, 55, 1)])
                        + mission(10001, [(2, 173, 5)]))
        disc.name = lambda file_id: f"mission_jpn/m{file_id:05}.mib"
        report = derive.derive_disc(disc)
        self.assertEqual([entry["quest"] for entry in report["missions"]], [1020, 10001, 11001, 60001])
        self.assertEqual(report["quests"][0]["species"], [6])
        self.assertIn("10001", report["not_supported"])
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "disc.json"
            with patch.object(derive.mhfu_data.DataBin, "open", return_value=disc):
                self.assertEqual(derive.main(["--disc", "synthetic.iso", "--out", str(out)]), 0)
            goals = state.objectives(derived=None, disc=out)
            self.assertEqual(goals[11001]["species"], [5])
            self.assertEqual(goals[60001]["species"], [7])
            self.assertNotIn(10001, goals)
            campaign = Path(folder) / "campaign.json"
            goal = dict(report["quests"][0], species=[8], source="mission_descriptors")
            campaign.write_text(json.dumps({"schema": 1, "quests": [goal]}), encoding="utf-8")
            self.assertEqual(state.objectives(derived=campaign, disc=out)[1020]["species"], [8])


if __name__ == "__main__":
    unittest.main()
