"""Failure, cancellation and objective contracts without game data."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_hunt_state as state
import atelier_zones as zones
import hunt


class PlacementTests(unittest.TestCase):
    # The 71-72 exit case of run 20261021-072744-4251e5, simplified.
    CYLINDER = {"shape": 0, "p": (9400.0, -400.0, 5250.0), "q": (0.0, 0.0, 0.0), "size": 300.0, "height": 600.0}
    BOX = {"shape": 1, "p": (0.0, 0.0, 0.0), "q": (1000.0, 0.0, 0.0), "size": 200.0, "height": 500.0}

    def test_exit_volumes(self):
        self.assertTrue(zones.inside_exit((9500.0, -300.0, 5250.0), self.CYLINDER))
        self.assertFalse(zones.inside_exit((10000.0, -300.0, 5250.0), self.CYLINDER))
        self.assertFalse(zones.inside_exit((9400.0, 900.0, 5250.0), self.CYLINDER), "above the volume")
        self.assertTrue(zones.inside_exit((500.0, 100.0, 300.0), self.BOX))
        self.assertFalse(zones.inside_exit((500.0, 100.0, 400.0), self.BOX))
        self.assertTrue(zones.inside_exit((1300.0, 100.0, 0.0), self.BOX), "within the margin past q")

    def test_revisit_goes_towards_the_area_seen_longest_ago(self):
        graph = {1: [2], 2: [1, 3], 3: [2]}
        self.assertEqual(hunt.revisit_step(graph, 3, {1: 5.0, 2: 9.0, 3: 12.0}), 2, "towards 1 through 2")
        self.assertEqual(hunt.revisit_step(graph, 1, {1: 12.0, 2: 9.0, 3: 5.0}), 2)
        self.assertIsNone(hunt.revisit_step({1: []}, 1, {1: 1.0}), "nowhere else to go")

    def test_placement_avoids_exits_and_gives_up_when_surrounded(self):
        spot = hunt.placement((9400.0, -300.0, 5700.0), [self.CYLINDER], side=1)
        self.assertIsNotNone(spot)
        self.assertFalse(zones.inside_exit(spot, self.CYLINDER))
        self.assertEqual(hunt.placement((5000.0, 0.0, 5000.0), [self.CYLINDER]), (5150.0, 0.0, 5000.0))
        self.assertIsNone(hunt.placement((9400.0, -300.0, 5250.0), [self.CYLINDER]))


class ObjectiveTests(unittest.TestCase):
    def test_verified_small_monster_goal_overrides_boss_roster(self):
        goal = state.resolve_objective(11001)
        self.assertEqual((goal["species"], goal["required"]), ([5], 20))
        self.assertTrue(goal["verified"])
        self.assertEqual(state.resolve_objective(21001)["species"], [6])

    def test_unknown_goal_requires_explicit_diagnostic_species(self):
        with self.assertRaises(ValueError):
            state.resolve_objective(12345)
        goal = state.resolve_objective(12345, species=[6])
        self.assertEqual(goal["kind"], "diagnostic")
        self.assertFalse(goal["verified"])
        self.assertIsNone(goal["required"])

    def test_invalid_registry_is_not_used(self):
        original = state.objectives()[11001]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "objectives.json"
            for field, value in (("verified", False), ("required", 0), ("required", True),
                                 ("species", []), ("species", [256]), ("evidence", ""),
                                 ("patrol", [16384]), ("kind", "guessed")):
                goal = dict(original, **{field: value})
                path.write_text(json.dumps({"schema": 1, "quests": [goal]}), encoding="utf-8")
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    state.objectives(path)
            path.write_text(json.dumps({"schema": 1, "quests": [original, original]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                state.objectives(path)


def actor(health, species=5, address=0x09010000, slot=1, stage=75):
    return {"health": health, "type": species, "kind": 1, "address": address,
            "slot": slot, "stage": stage, "position": (1, 2, 3)}


class TrackingTests(unittest.TestCase):
    def test_only_confirmed_live_to_dead_target_transitions_count(self):
        tracker = state.KillTracker([5])
        self.assertEqual(tracker.observe([actor(1), actor(1, 68)]), [])
        self.assertEqual(tracker.observe([actor(0), actor(0, 68)]), [])
        self.assertEqual(len(tracker.observe([actor(0), actor(0, 68)])), 1)
        self.assertEqual(tracker.observe([actor(0)]), [])
        self.assertEqual(tracker.count, 1)
        tracker.observe([actor(1)])
        tracker.observe([actor(0)])
        tracker.observe([actor(0)])
        self.assertEqual(tracker.count, 2)

    def test_absent_reused_or_reclassified_slots_are_not_deaths(self):
        for middle in ([], [actor(0, 68)], [dict(actor(0), kind=0)], [actor(0, stage=48461)]):
            tracker = state.KillTracker([5])
            tracker.observe([actor(1)])
            tracker.observe(middle)
            tracker.observe([actor(0)])
            self.assertEqual(tracker.observe([actor(0)]), [])
            self.assertEqual(tracker.count, 0)

    def test_single_dead_read_or_other_area_is_not_a_death(self):
        tracker = state.KillTracker([5])
        tracker.observe([actor(1)], stage=75)
        tracker.observe([actor(0)], stage=75)
        self.assertEqual(tracker.observe([], stage=75), [])      # vanished before confirmation
        tracker = state.KillTracker([5])
        tracker.observe([actor(1, stage=72)], stage=16)
        tracker.observe([actor(0, stage=72)], stage=16)
        self.assertEqual(tracker.observe([actor(0, stage=72)], stage=16), [])
        tracker = state.KillTracker([5])
        tracker.observe([actor(1)], stage=75)
        tracker.observe([actor(0)], stage=75)
        self.assertEqual(tracker.observe([actor(0, stage=72)], stage=72), [])  # moved
        self.assertEqual(tracker.count, 0)

    def test_empty_navigation_cycle_cannot_extend_deadline(self):
        guard = state.ProgressGuard(0, seconds=120)
        self.assertFalse(guard.observe(75, 0, 0))
        self.assertFalse(guard.observe(67, 0, 10))
        self.assertFalse(guard.observe(75, 0, 100))
        self.assertTrue(guard.observe(67, 0, 130))

    def test_real_new_area_or_kill_resets_deadline(self):
        guard = state.ProgressGuard(0, seconds=120)
        guard.observe(75, 0, 0)
        self.assertFalse(guard.observe(75, 1, 119))
        self.assertFalse(guard.observe(75, 1, 200))
        self.assertFalse(guard.observe(70, 1, 230))
        self.assertTrue(guard.observe(70, 1, 350))


class SyntheticWorkshop:
    def __init__(self, root):
        self.project = root
        self.data_dir = root / "data"
        self.results = []

    def record_quest_result(self, run_id, result):
        self.results.append(copy.deepcopy(result))


class SyntheticSession:
    instances = []
    village = True
    cancel_on_input = False
    stop_on_input = False

    def __init__(self, workshop, seed, seconds, variant):
        self.w = workshop
        self.run_id = "synthetic-hunt"
        self.dir = workshop.data_dir / "runs" / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stopped = False
        self.stop_count = 0
        self.cancel = None
        self.commands, self.shots = [], []
        self.instances.append(self)

    def alive(self):
        return not self.stopped and not (self.cancel and self.cancel.is_set())

    def wait_task(self, task, *args, **kwargs):
        return self.village if task == 10 else True

    def arm_loadout(self, loadout):
        self.commands.append(f"armed {loadout}")
        return True

    def ask(self, command, *args):
        self.commands.append(command)
        return "[debug] started quest" if command.startswith("quest start") else "[debug] held"

    def shot(self, name):
        self.shots.append(name)
        return name

    def inputs(self, commands):
        self.commands.extend(commands)
        if self.cancel_on_input:
            self.cancel.set()
        if self.stop_on_input:
            self.stopped = True

    def stop(self):
        self.stop_count += 1
        self.stopped = True


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.workshop = SyntheticWorkshop(Path(self.folder.name))
        SyntheticSession.instances = []
        SyntheticSession.village = True
        SyntheticSession.cancel_on_input = False
        SyntheticSession.stop_on_input = False
        self.addCleanup(patch.stopall)
        patch.object(hunt.atelier_campaign, "Session", SyntheticSession).start()
        patch.object(hunt.time, "sleep").start()
        patch.object(zones, "current", return_value={"stage": 75, "player": 0x09000000, "exits": []}).start()
        patch.object(zones, "monsters", return_value=[]).start()
        patch.object(zones, "quest_targets", return_value=[{"em_id": 68, "count": 1}]).start()
        patch.object(zones, "mission_state", return_value=2).start()
        patch.object(zones, "mission_objectives", return_value={"quest": 11001,
            "current": [{"flags": 0x211, "target": 5, "quantity": 20}, {"flags": 0, "target": 0, "quantity": 0}],
            "initial": [{"flags": 0x211, "target": 5, "quantity": 20}, {"flags": 0, "target": 0, "quantity": 0}]}).start()
        patch.object(zones, "place").start()
        patch("builtins.print").start()

    def run_hunt(self, **kwargs):
        return hunt.hunt(self.workshop, "synthetic-seed", 11001, 1609459200123456,
                         kwargs.pop("seconds", 30), **kwargs)

    def test_unknown_or_invalid_plan_never_starts_game(self):
        with self.assertRaises(ValueError):
            hunt.hunt(self.workshop, "seed", 12345, 1609459200123456, 30)
        with self.assertRaises(ValueError):
            self.run_hunt(seconds=-1)
        with self.assertRaises(ValueError):
            self.run_hunt(walk_in_frames=601)
        with self.assertRaises(ValueError):
            self.run_hunt(loadout="worst")
        with self.assertRaises(ValueError):
            self.run_hunt(cheats=("health", "god"))
        self.assertEqual(SyntheticSession.instances, [])

    def test_best_loadout_before_departure_and_cheats_on_arrival_are_recorded(self):
        result = self.run_hunt()
        commands = SyntheticSession.instances[0].commands
        self.assertEqual(commands[0], "armed best", "sent before the save is loaded")
        self.assertTrue(result["loadout_armed_at_boot"])
        self.assertLess(commands.index("loadout best"), commands.index("quest start 11001"))
        held = [c for c in commands if c.startswith("hold ")]
        self.assertEqual(held, [f"hold {c} on" for c in hunt.DEFAULT_CHEATS])
        self.assertGreater(commands.index(held[0]), commands.index("quest start 11001"))
        self.assertEqual((result["loadout"], result["cheats"]), ("best", list(hunt.DEFAULT_CHEATS)))
        self.assertEqual(result["cheats_held"], list(hunt.DEFAULT_CHEATS))
        # The synthetic game answers "held", not "wearing": recorded as not worn.
        self.assertFalse(next(e for e in result["events"] if e.get("kind") == "loadout")["worn"])

    def test_save_equipment_and_no_cheats_on_request(self):
        result = self.run_hunt(loadout="none", cheats=())
        commands = SyntheticSession.instances[0].commands
        self.assertFalse(any(c.startswith(("loadout", "hold ", "armed")) for c in commands))
        self.assertEqual((result["loadout"], result["loadout_answer"], result["cheats_held"]), ("none", None, []))

    def test_cancelled_before_start_never_starts_game(self):
        cancel = threading.Event()
        cancel.set()
        result = self.run_hunt(cancel=cancel)
        self.assertEqual((result["state"], result["phase"]), ("cancelled", "finished"))
        self.assertEqual(result["objective"]["required"], 20)
        self.assertEqual(SyntheticSession.instances, [])

    def test_stop_during_boot_is_not_a_departure_failure(self):
        cancel = threading.Event()
        def cancel_at_village_wait(session, *args, **kwargs):
            cancel.set()
            return None
        with patch.object(SyntheticSession, "wait_task", cancel_at_village_wait):
            result = self.run_hunt(cancel=cancel)
        self.assertEqual((result["state"], result["reason"]), ("cancelled", "trial_stopped"))
        self.assertEqual(self.workshop.results[0]["state"], "cancelled")
        self.assertEqual(SyntheticSession.instances[0].stop_count, 1)

    def test_game_exit_during_boot_is_not_a_departure_failure(self):
        def exit_at_village_wait(session, *args, **kwargs):
            session.stopped = True
            return None
        with patch.object(SyntheticSession, "wait_task", exit_at_village_wait):
            result = self.run_hunt()
        self.assertEqual(result["state"], "runtime_stopped")

    def test_boot_deadline_while_game_runs_is_a_departure_failure(self):
        SyntheticSession.village = False
        result = self.run_hunt()
        self.assertEqual((result["state"], result["reason"]), ("start_failed", "village_not_reached"))

    def test_no_route_stops_and_records_reason_and_objective(self):
        result = self.run_hunt()
        self.assertEqual(result["state"], "no_route")
        self.assertEqual(result["objective"]["species"], [5])
        self.assertEqual(self.workshop.results[0]["state"], "no_route")
        session = SyntheticSession.instances[0]
        self.assertEqual(session.stop_count, 1)
        self.assertTrue((session.dir / "hunt.json").is_file())
        self.assertIn("h11001-stop", session.shots)

    def test_game_counter_completion_does_not_decide_quest_success(self):
        patch.object(hunt, "resolve_objective", return_value=dict(state.resolve_objective(11001),
            progress_counter={"flags": 0x211, "evidence": "synthetic"})).start()
        zones.mission_objectives.return_value["current"][0]["quantity"] = 0
        result = self.run_hunt()
        self.assertEqual(result["state"], "no_route")
        self.assertIsNone(result["success"])
        self.assertEqual(result["objective_progress"]["completed"], 20)
        self.assertEqual(self.workshop.results[0]["objective_progress"]["completed"], 20)

    def test_partial_slay_in_an_arena_waits_for_the_next_wave(self):
        initial = copy.deepcopy(zones.mission_objectives.return_value)
        initial["current"][0]["quantity"] = 18
        next_wave = copy.deepcopy(initial)
        next_wave["current"][0]["quantity"] = 17
        ended = copy.deepcopy(initial)
        ended["current"][0]["quantity"] = 0
        zones.mission_objectives.side_effect = [initial, next_wave, ended]
        zones.mission_state.side_effect = [2, 2, zones.MISSION_CLEAR]
        zones.monsters.side_effect = [[], [actor(1)]]
        result = self.run_hunt()
        self.assertEqual(result["state"], "quest_cleared")
        waiting = [e for e in result["events"] if e.get("kind") == "await_targets"]
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]["completed"], 2)
        self.assertTrue(any(e.get("kind") == "attack" for e in result["events"]))
        self.assertTrue(result["returned_to_village"])

    def test_waiting_for_a_wave_is_stopped_by_the_progress_deadline(self):
        zones.mission_objectives.return_value["current"][0]["quantity"] = 18
        with patch.object(hunt.ProgressGuard, "observe", side_effect=[False, True]):
            result = self.run_hunt()
        self.assertEqual(result["state"], "stalled")
        self.assertIsNone(result["success"])
        self.assertEqual(len([e for e in result["events"] if e.get("kind") == "await_targets"]), 1)

    def test_waiting_for_a_wave_remains_cancellable(self):
        zones.mission_objectives.return_value["current"][0]["quantity"] = 18
        cancel = threading.Event()

        def stop_while_waiting(event):
            if event["phase"] == "observing":
                session = SyntheticSession.instances[0]
                text = json.loads((session.dir / "hunt.json").read_text(encoding="utf-8"))
                if text["events"][-1].get("kind") == "await_targets":
                    cancel.set()
        result = self.run_hunt(cancel=cancel, progress=stop_while_waiting)
        self.assertEqual(result["state"], "cancelled")
        self.assertIsNone(result["success"])

    def test_counter_unavailable_does_not_prevent_mission_result(self):
        patch.object(hunt, "resolve_objective", return_value=dict(state.resolve_objective(11001),
            progress_counter={"flags": 0x211, "evidence": "synthetic"})).start()
        zones.mission_objectives.side_effect = zones.Unavailable("quest changed")
        zones.mission_state.return_value = zones.MISSION_CLEAR
        result = self.run_hunt()
        self.assertEqual(result["state"], "quest_cleared")
        self.assertFalse(result["objective_progress_available"])
        self.assertIsNone(result["objective_progress"])

    def test_unavailable_counter_keeps_last_reading_marked_as_stale(self):
        patch.object(hunt, "resolve_objective", return_value=dict(state.resolve_objective(11001),
            progress_counter={"flags": 0x211, "evidence": "synthetic"})).start()
        initial = zones.mission_objectives.return_value
        zones.mission_objectives.side_effect = [initial, zones.Unavailable("quest changed")]
        zones.mission_state.side_effect = [2, zones.MISSION_CLEAR]
        zones.monsters.return_value = [actor(1)]
        result = self.run_hunt()
        self.assertEqual(result["state"], "quest_cleared")
        self.assertFalse(result["objective_progress_available"])
        self.assertEqual(result["objective_progress"]["completed"], 0)

    def test_mission_clear_stops_moves_and_attacks_and_leaves_end_screens(self):
        zones.mission_state.return_value = zones.MISSION_CLEAR
        zones.monsters.return_value = [actor(1)]
        result = self.run_hunt()
        self.assertEqual(result["state"], "quest_cleared")
        self.assertIs(result["success"], True)
        self.assertTrue(result["returned_to_village"])
        self.assertEqual(result["mission_states"][0]["state"], 3)
        zones.place.assert_not_called()
        session = SyntheticSession.instances[0]
        self.assertNotIn("0:pad y 4", session.commands)
        self.assertIn("h11001-fin", session.shots)
        self.assertNotIn("h11001-stop", session.shots)
        self.assertIs(self.workshop.results[0]["success"], True)

    def test_mission_failure_and_unknown_end_states(self):
        zones.mission_state.return_value = zones.MISSION_FAILED
        result = self.run_hunt()
        self.assertEqual((result["state"], result["success"]), ("quest_failed", False))
        zones.mission_state.return_value = 7
        result = self.run_hunt()
        self.assertEqual((result["state"], result["success"]), ("quest ended", None))

    def test_end_detected_after_kill_before_another_action(self):
        other = actor(1, address=0x09020000, slot=2)
        zones.mission_state.side_effect = [2, 2, 2, zones.MISSION_CLEAR]
        zones.monsters.side_effect = [[actor(1), other], [actor(0), other], [actor(0), other]]
        result = self.run_hunt()
        self.assertEqual(result["state"], "quest_cleared")
        self.assertEqual(result["kills"], 1)
        self.assertEqual(zones.place.call_count, 3)

    def test_area_loading_during_exit_is_retried_not_fatal(self):
        exit = {"destination": 72, "shape": 0, "p": (0, 0, 0), "q": (0, 0, 0), "size": 1.0,
                "height": 1.0, "arrival": (0, 0, 0)}
        zones.current.return_value = {"stage": 16, "player": 0x09000000, "exits": [exit]}
        zones.mission_state.side_effect = [2, zones.MISSION_CLEAR]
        with patch.object(zones, "traverse", side_effect=zones.Unavailable("area loading")):
            result = self.run_hunt()
        self.assertEqual(result["state"], "quest_cleared")

    def test_cancellation_during_combat_stops_without_another_action(self):
        cancel = threading.Event()
        cancel.wait = Mock(return_value=False)  # No wall-clock delay in a synthetic trial.
        SyntheticSession.cancel_on_input = True
        zones.monsters.return_value = [actor(1)]
        result = self.run_hunt(cancel=cancel)
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(SyntheticSession.instances[0].stop_count, 1)

    def test_cancellation_on_combat_progress_prevents_placement_and_attack(self):
        cancel = threading.Event()
        cancel.wait = Mock(return_value=False)
        zones.monsters.return_value = [actor(1)]
        def stop_at_combat(event):
            if event["phase"] == "combat":
                cancel.set()
        result = self.run_hunt(cancel=cancel, progress=stop_at_combat)
        self.assertEqual(result["state"], "cancelled")
        zones.place.assert_not_called()
        self.assertFalse(any(command.startswith("0:pad y") for command in SyntheticSession.instances[0].commands))

    def test_cancellation_during_placement_prevents_attack_input(self):
        cancel = threading.Event()
        cancel.wait = Mock(return_value=False)
        zones.monsters.return_value = [actor(1)]
        zones.place.side_effect = lambda *args: cancel.set()
        result = self.run_hunt(cancel=cancel)
        self.assertEqual(result["state"], "cancelled")
        zones.place.assert_called_once()
        self.assertFalse(any(command.startswith("0:pad y") for command in SyntheticSession.instances[0].commands))

    def test_checkpoint_failure_still_stops_own_session(self):
        with patch.object(hunt.atelier_core, "_write_json", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.run_hunt()
        self.assertEqual(SyntheticSession.instances[0].stop_count, 1)

    def test_game_exit_is_not_mislabeled_as_user_cancellation(self):
        SyntheticSession.stop_on_input = True
        zones.monsters.return_value = [actor(1)]
        self.assertEqual(self.run_hunt()["state"], "runtime_stopped")

    def test_repeated_incomplete_reads_stop_after_three_attempts(self):
        zones.current.side_effect = zones.Unavailable("incomplete batch")
        result = self.run_hunt()
        self.assertEqual(result["state"], "read_failed")
        self.assertEqual(zones.current.call_count, 3)

    def test_progress_deadline_stops_controller_before_another_action(self):
        with patch.object(hunt, "ProgressGuard") as guard:
            guard.return_value.observe.return_value = True
            result = self.run_hunt()
        self.assertEqual(result["state"], "stalled")
        self.assertEqual(result["reason"], "no_new_area_or_target_death")

    def test_failed_village_does_not_send_quest_start(self):
        SyntheticSession.village = False
        result = self.run_hunt()
        self.assertEqual(result["reason"], "village_not_reached")
        # Only the loadout armed at boot; nothing is asked of the game afterwards.
        self.assertEqual(SyntheticSession.instances[0].commands, ["armed best"])


if __name__ == "__main__":
    unittest.main()
