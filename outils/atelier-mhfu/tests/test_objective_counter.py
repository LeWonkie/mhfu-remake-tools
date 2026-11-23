"""Objective counters: reject mismatched memory and preserve result semantics."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_hunt_state as state
import atelier_zones as zones
from test_memory_batches import Console


def goal():
    return {"quest": 11001, "verified": True, "species": [5], "required": 20,
            "progress_counter": {"flags": 0x211, "evidence": "synthetic"}}


def snapshot(remaining=13):
    empty = {"flags": 0, "target": 0, "quantity": 0}
    return {"quest": 11001, "current": [{"flags": 0x211, "target": 5, "quantity": remaining}, dict(empty)],
            "initial": [{"flags": 0x211, "target": 5, "quantity": 20}, dict(empty)]}


class InterpretationTests(unittest.TestCase):
    def test_registry_rejects_unidentified_or_non_slay_counter_formats(self):
        original = state.objectives()[11001]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "objectives.json"
            for counter in ({"flags": True, "evidence": "synthetic"},
                            {"flags": -1, "evidence": "synthetic"},
                            {"flags": 0x231, "evidence": "synthetic"},
                            {"flags": 0x212, "evidence": "synthetic"}, {"flags": 0x211}, []):
                with self.subTest(counter=counter):
                    path.write_text(json.dumps({"schema": 1, "quests": [dict(original,
                        progress_counter=counter)]}), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "objective counter"):
                        state.objectives(path)

    def test_game_progress_resets_stall_even_when_no_actor_death_was_seen(self):
        guard = state.ProgressGuard(0, seconds=120)
        guard.observe(75, 0, 0, objective_completed=0)
        self.assertFalse(guard.observe(75, 0, 119, objective_completed=1))
        self.assertFalse(guard.observe(75, 0, 200, objective_completed=None))
        self.assertTrue(guard.observe(75, 0, 240, objective_completed=1))

    def test_remaining_quantity_is_converted_to_game_progress(self):
        result = state.objective_progress(snapshot(), goal())
        self.assertEqual((result["completed"], result["remaining"], result["required"]), (7, 13, 20))
        self.assertEqual(result["source"], "game_mission_objective")

    def test_zero_counter_is_progress_not_a_success_result(self):
        data = snapshot(0)
        data["current"][0]["flags"] |= 0x20
        result = state.objective_progress(data, goal())
        self.assertEqual(result["completed"], 20)
        self.assertNotIn("success", result)

    def test_training_goal_uses_its_own_species_and_original_quantity(self):
        data = snapshot(0)
        data["quest"] = 21001
        data["current"][0].update(flags=0x231, target=6)
        data["initial"][0].update(target=6, quantity=1)
        training = dict(goal(), quest=21001, species=[6], required=1)
        result = state.objective_progress(data, training)
        self.assertEqual((result["completed"], result["required"]), (1, 1))

    def test_wrong_species_required_flags_and_ranges_are_rejected(self):
        for section, field, value in (("current", "target", 68), ("initial", "target", 68),
                ("initial", "quantity", 1), ("current", "quantity", -1),
                ("current", "quantity", 21), ("current", "flags", 0x251),
                ("initial", "flags", 0x231), ("current", "flags", 0x231)):
            with self.subTest(section=section, field=field, value=value):
                data = snapshot()
                data[section][0][field] = value
                self.assertIsNone(state.objective_progress(data, goal()))

    def test_other_quest_unverified_and_multi_objective_are_not_interpreted(self):
        data = snapshot()
        data["quest"] = 21001
        self.assertIsNone(state.objective_progress(data, goal()))
        for altered in (dict(goal(), verified=False), dict(goal(), progress_counter=None)):
            self.assertIsNone(state.objective_progress(snapshot(), altered))
        for section in ("current", "initial"):
            data = snapshot()
            data[section][1]["flags"] = 0x211
            self.assertIsNone(state.objective_progress(data, goal()))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.session = Console()
        self.quest, self.gamesys = 0x09000000, 0x09100000
        self.session.pack(zones.QUEST_PTR, "<I", self.quest)
        self.session.pack(zones.GAMESYS_PTR, "<I", self.gamesys)
        self.session.pack(self.gamesys + zones.ACCEPTED_QUEST_ID, "<H", 11001)
        self.session.put(self.quest + zones.MISSION_OBJECTIVES,
                         b"".join(struct.pack("<IHh", entry["flags"], entry["target"], entry["quantity"])
                                  for section in ("current", "initial") for entry in snapshot()[section]))

    def test_coherent_snapshot_uses_read_only_commands(self):
        self.assertEqual(zones.mission_objectives(self.session, 11001), snapshot())
        self.assertTrue(all(cmd.startswith("peek ") for batch in self.session.batches for cmd in batch))
        self.assertEqual(len(self.session.batches), 3)

    def test_pointer_or_accepted_quest_changes_discard_snapshot(self):
        original = copy.deepcopy(self.session.memory)
        for address, fmt, value in ((zones.QUEST_PTR, "<I", 0x09001000),
                (zones.GAMESYS_PTR, "<I", 0x09101000),
                (self.gamesys + zones.ACCEPTED_QUEST_ID, "<H", 21001)):
            with self.subTest(address=address):
                self.session.memory = dict(original)
                def changed(commands):
                    if any(f"0x{self.quest + zones.MISSION_OBJECTIVES:08X}" in cmd for cmd in commands):
                        self.session.pack(address, fmt, value)
                self.session.before_batch = changed
                with self.assertRaisesRegex(zones.Unavailable, "quest changed"):
                    zones.mission_objectives(self.session, 11001)

    def test_wrong_quest_and_missing_pointer_are_unavailable(self):
        with self.assertRaisesRegex(zones.Unavailable, "does not match"):
            zones.mission_objectives(self.session, 21001)
        self.session.pack(zones.QUEST_PTR, "<I", 0)
        with self.assertRaisesRegex(zones.Unavailable, "not loaded"):
            zones.mission_objectives(self.session, 11001)

    def test_field_beyond_guest_memory_is_rejected_before_reading_it(self):
        self.session.pack(zones.GAMESYS_PTR, "<I", 0x09FFFF00)
        with self.assertRaisesRegex(zones.Unavailable, "not loaded"):
            zones.mission_objectives(self.session, 11001)
        self.assertEqual(len(self.session.batches), 1)


if __name__ == "__main__":
    unittest.main()
