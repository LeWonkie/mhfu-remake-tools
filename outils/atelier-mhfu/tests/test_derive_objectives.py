"""Objectives derived from mission descriptors recorded by campaigns."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_hunt_state as state
import derive_objectives as derive


def result(quest, entries, roster=()):
    pad = [{"flags": 0, "target": 0, "quantity": 0}] * (2 - len(entries))
    descriptors = [dict(zip(("flags", "target", "quantity"), e)) for e in entries] + pad
    return {"quest": quest, "campaign": "campaigns/x/campagne.json",
            "objectives": {"quest": quest, "current": descriptors, "initial": descriptors},
            "roster": [{"em_id": em, "count": 1} for em in roster]}


class DeriveTests(unittest.TestCase):
    def test_only_slay_and_hunt_descriptors_are_kept(self):
        entry, _ = derive.derive(result(11101, [(0x211, 63, 15)], [31]))
        self.assertEqual((entry["kind"], entry["species"], entry["required"]), ("slay", [63], 15))
        self.assertEqual(entry["progress_counter"]["flags"], 0x211)
        entry, _ = derive.derive(result(10501, [(0x11, 11, 1), (0x11, 1, 1)], [11, 1]))
        self.assertEqual((entry["kind"], entry["species"], entry["required"]), ("hunt", [1, 11], 2))
        self.assertNotIn("progress_counter", entry, "two descriptors: no single counter")
        self.assertEqual((entry["verified"], entry["source"]), (False, "mission_descriptors"))

    def test_paw_pass_ticket_with_bosses_is_not_a_hunt(self):
        # Quest 2002's visible Quest Info: Gathering - Jungle Zone,
        # Paw Pass Ticket 0/1, despite its Yian Kut-Ku roster.
        for bosses in ([], [6], [38, 79]):
            with self.subTest(bosses=bosses):
                entry, why = derive.derive(result(2002, [(0x12, 927, 1)], bosses))
                self.assertIsNone(entry)
                self.assertIn("livraison", why)

    def test_captures_gathering_and_unread_quests_are_left_out(self):
        for quest, entries, roster, reason in (
                (4001, [(0x12, 928, 1)], [77], "capture"), (1007, [(0x12, 173, 20)], [68], "livraison"),
                (1001, [(0x12, 927, 1)], [], "livraison"), (5, [(0x31, 6, 0)], [6], "drapeaux")):
            entry, why = derive.derive(result(quest, entries, roster))
            self.assertIsNone(entry)
            self.assertIn(reason, why)
        self.assertEqual(derive.derive({"quest": 9, "objectives": None})[0], None)
        started = result(11001, [(0x211, 5, 20)])
        started["objectives"]["current"] = [{"flags": 0x211, "target": 5, "quantity": 12}] + started["objectives"]["current"][1:]
        self.assertIsNone(derive.derive(started)[0], "a counter already moved is not a starting point")

    def test_written_registry_loads_after_the_verified_one(self):
        with tempfile.TemporaryDirectory() as folder:
            campaign = Path(folder) / "campagne.json"
            campaign.write_text(json.dumps({"results": [result(11001, [(0x211, 99, 3)]),
                                                        result(2002, [(0x12, 927, 1)], [6]),
                                                        result(4001, [(0x12, 928, 1)], [77])]}), encoding="utf-8")
            out = Path(folder) / "derived.json"
            derive.main([str(campaign), "--out", str(out)])
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(set(written["not_supported"]), {"2002", "4001"})
            self.assertIn("livraison", written["not_supported"]["2002"])
            goals = state.objectives(derived=out)
            self.assertEqual(goals[11001]["species"], [5], "the verified registry wins")
            self.assertNotIn(2002, goals, "a ticket delivery is never offered to the hunt controller")
            snapshot = {"quest": 11101, "current": [{"flags": 0x211, "target": 63, "quantity": 10},
                                                    {"flags": 0, "target": 0, "quantity": 0}],
                        "initial": [{"flags": 0x211, "target": 63, "quantity": 15},
                                    {"flags": 0, "target": 0, "quantity": 0}]}
            goal, _ = derive.derive(result(11101, [(0x211, 63, 15)]))
            self.assertEqual(state.objective_progress(snapshot, goal)["completed"], 5)
            forged = dict(goal, source="hand")
            self.assertIsNone(state.objective_progress(snapshot, forged), "unverified and not derived: ignored")
            bad = Path(folder) / "bad.json"
            bad.write_text(json.dumps({"schema": 1, "quests": [dict(goal, verified=False, source="guess")]}),
                           encoding="utf-8")
            with self.assertRaises(ValueError):
                state.objectives(derived=bad)


if __name__ == "__main__":
    unittest.main()
