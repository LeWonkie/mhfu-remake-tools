"""State driver, validation cache and resumption, with a synthetic game only."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_campaign
import atelier_ledger
import atelier_pilot
import atelier_zones
from atelier_pilot import Pilot, Stopped, WaitTimeout

GAMESYS, MANAGER, QUEST, LOBBY, SLOT0 = 0x09000000, 0x09100000, 0x09200000, 0x09300000, 0x09300000


class FakeWorkshop:
    def __init__(self, game):
        self.game = game

    def debug_commands(self, lines):
        self.game.commands(lines)

    def stop(self):
        self.game.running = False


class FakeGame:
    """Answers `state` and `peek` from a small memory; tests change the fields."""

    def __init__(self):
        self.task, self.quest, self.stage = 10, 1, 139
        self.scene, self.mission, self.clock, self.lobby_status = 0, 0, 90000, 3
        self.running = True
        self.pending: list[str] = []
        self.sent: list[str] = []
        self.inputs_sent: list[str] = []
        self.on_command = None
        self.on_lines = None
        self.w = FakeWorkshop(self)
        self.dir = Path(tempfile.gettempdir())

    def memory(self) -> dict[int, bytes]:
        return {atelier_zones.GAMESYS_PTR: struct.pack("<I", GAMESYS),
                atelier_pilot.TASK_MANAGER_PTR: struct.pack("<I", MANAGER),
                atelier_zones.QUEST_PTR: struct.pack("<I", QUEST),
                atelier_pilot.LOBBY_TASK_PTR: struct.pack("<I", LOBBY),
                atelier_pilot.LOBBY_MANAGER: struct.pack("<I", self.scene),
                GAMESYS + atelier_zones.MISSION_STATE: bytes([self.mission]),
                MANAGER: struct.pack("<I", SLOT0),
                LOBBY + atelier_pilot.TASK_LOAD_STATUS: bytes([self.lobby_status]),
                QUEST + atelier_pilot.QUEST_CLOCK: struct.pack("<I", self.clock)}

    def commands(self, lines):
        for line in lines:
            self.sent.append(line)
            if self.on_command:
                self.on_command(self, line)
            if line == "state":
                name = "village" if self.task == 10 else "quest"
                self.pending.append(f"[debug] hunter hunter, HR 1, 100z; task {self.task} ({name}), "
                                    f"quest id {self.quest}, stage {self.stage}")
            elif line.startswith("peek "):
                _, address, length = line.split()
                data = self.memory()[int(address, 16)][:int(length)]
                self.pending.append(f"[debug] {int(address, 16):08X}: " + " ".join(f"{b:02X}" for b in data))

    def new_lines(self):
        lines, self.pending = self.pending, []
        if lines and self.on_lines:
            self.on_lines(lines)
        return lines

    def alive(self):
        return self.running

    def inputs(self, lines):
        self.inputs_sent.extend(lines)
        for line in lines:
            if ":shot " in line:
                name = line.split(":shot ", 1)[1]
                self.pending.append(f"[render] window 960x544 -> C:\\runs\\x\\frames\\{name}.bmp")
            if line.endswith("pad guide 2"):
                self.pending.append("[fast-forward] on, 8x")


class SnapshotAndWaits(unittest.TestCase):
    def test_snapshot_reads_village_readiness(self):
        game = FakeGame()
        snap = Pilot(game).snapshot()
        self.assertEqual((snap["task"], snap["quest"], snap["stage"], snap["mission"]), (10, 1, 139, 0))
        self.assertTrue(Pilot.village_ready(snap))
        game.scene = 0x1D
        self.assertFalse(Pilot.village_ready(Pilot(game).snapshot()))
        game.scene, game.lobby_status = 0, 1
        self.assertFalse(Pilot.village_ready(Pilot(game).snapshot()), "village still loading")

    def test_state_and_peeks_go_in_one_write(self):
        game = FakeGame()
        writes = []
        original = game.commands
        game.commands = lambda lines: (writes.append(list(lines)), original(lines))
        Pilot(game).snapshot()
        self.assertEqual(writes[0][0], "state")
        self.assertEqual(len(writes), 2, "state + pointers, then dependent fields")

    def test_wait_times_out_with_its_name(self):
        game = FakeGame()
        pilot = Pilot(game, poll=0.001)
        with self.assertRaises(WaitTimeout) as caught:
            pilot.until("jamais", lambda: None, 0.05)
        self.assertEqual(caught.exception.what, "jamais")
        self.assertTrue(pilot.waits[-1]["timeout"])

    def test_cancel_stops_a_wait(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Stopped) as caught:
            Pilot(FakeGame(), cancel=cancel).until("x", lambda: None, 10)
        self.assertEqual(caught.exception.reason, "cancelled")

    def test_engine_stop_ends_a_wait(self):
        game = FakeGame()
        game.running = False
        with self.assertRaises(Stopped) as caught:
            Pilot(game).until("x", lambda: None, 10)
        self.assertEqual(caught.exception.reason, "runtime_stopped")

    def test_unavailable_reads_are_retried_not_fatal(self):
        calls = []

        def probe():
            calls.append(1)
            if len(calls) < 3:
                raise atelier_zones.Unavailable("loading")
            return "ok"
        self.assertEqual(Pilot(FakeGame(), poll=0.001).until("x", probe, 1), "ok")

    def test_ask_matches_alternatives_and_batches(self):
        game = FakeGame()
        game.on_command = lambda g, line: g.pending.append(
            "[debug] not started, the village is still loading" if line.startswith("quest start") else
            "[debug] hold health on")
        answers = Pilot(game).ask(["hold health on", "quest start 1001"],
                                  ["hold", ("started quest", "not started")], 1.0)
        self.assertEqual(answers[1], "[debug] not started, the village is still loading")
        self.assertIn("hold health on", answers[0])


class LoadingChecks(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame()
        self.game.task, self.game.quest, self.game.stage = 11, 1001, 98
        self.pilot = Pilot(self.game, poll=0.001)

    def test_inconsistent_area_is_not_loaded(self):
        with patch.object(atelier_zones, "current", return_value={"stage": 97}):
            with self.assertRaises(WaitTimeout):
                self.pilot.wait_quest(1001, 0.1)

    def test_frozen_clock_is_not_playable(self):
        with patch.object(atelier_zones, "current", return_value={"stage": 98}):
            with self.assertRaises(WaitTimeout) as caught:
                self.pilot.wait_quest(1001, 0.1)
        self.assertEqual(caught.exception.what, "chronomètre en marche")

    def test_other_quest_is_never_attributed(self):
        self.game.quest = 1002
        with patch.object(atelier_zones, "current", return_value={"stage": 98}):
            with self.assertRaises(WaitTimeout):
                self.pilot.wait_quest(1001, 0.1)

    def test_moving_clock_in_coherent_area_is_loaded(self):
        def tick(game, line):
            if line == "state":
                game.clock -= 30
        self.game.on_command = tick
        with patch.object(atelier_zones, "current", return_value={"stage": 98}):
            loaded = self.pilot.wait_quest(1001, 1.0)
        self.assertEqual(loaded["stage"], 98)
        self.assertLess(loaded["clock"], loaded["clock_first"])


class LoadEnd(unittest.TestCase):
    def test_village_waits_for_the_engine_end_of_load(self):
        game = FakeGame()
        pilot = Pilot(game, poll=0.001)
        game.pending.append("[overlay] installed stage139 (2 KiB) at 0x09D5DF80")
        with self.assertRaises(WaitTimeout):
            pilot.wait_village(0.1)
        game.pending.append("[load] fast 1522 ms of game time in 307 ms real, 1215 ms saved (sound; 7.5 s saved so far)")
        self.assertEqual(pilot.wait_village(1.0)["stage"], 139)

    def test_missing_end_line_is_bounded_and_counted(self):
        game = FakeGame()
        pilot = Pilot(game, poll=0.001)
        game.pending.append("[overlay] installed lobby_task (705 KiB) at 0x09A5A580")
        with patch.object(atelier_pilot, "LOAD_GRACE", 0.05):
            pilot.wait_village(1.0)
        self.assertEqual(pilot.load_lines_missing, 1)


class Departure(unittest.TestCase):
    def game(self, drops):
        game = FakeGame()
        game.drops = drops

        def follow(g, line):
            if line.startswith("quest start"):
                g.pending.append("[debug] started quest 1001")
                g.quest, g.scene = 1001, 0x1D
            elif line == "state" and g.scene == 0x1D:
                if g.drops:
                    g.drops -= 1
                    g.scene = 0          # the village was not ready: dropped
                else:
                    g.task, g.scene = 11, 0x50600003
        game.on_command = follow
        return game

    def test_dropped_departure_is_sent_again(self):
        game = self.game(drops=2)
        self.assertEqual(Pilot(game, poll=0.001).depart(1001, 5), 2)
        self.assertEqual(game.task, 11)
        self.assertEqual(sum(line.startswith("quest start") for line in game.sent), 3)

    def test_departures_are_bounded(self):
        with self.assertRaises(WaitTimeout):
            Pilot(self.game(drops=99), poll=0.001).depart(1001, 5, attempts=3)

    def test_refusal_is_not_retried(self):
        game = FakeGame()
        game.on_command = lambda g, line: line.startswith("quest start") and g.pending.append(
            "[debug] not started: the game has no quest 99")
        with self.assertRaises(WaitTimeout):
            Pilot(game, poll=0.001).depart(99, 5)


class FastForward(unittest.TestCase):
    def test_pressed_once_when_reported_off(self):
        game = FakeGame()
        pilot = Pilot(game, poll=0.001)
        self.assertTrue(pilot.ensure_fast_forward(1.0))
        self.assertEqual(game.inputs_sent.count("0:pad guide 2"), 1)
        self.assertEqual(pilot.ff_speed, 8)

    def test_never_pressed_blind_when_already_on(self):
        game = FakeGame()
        pilot = Pilot(game)
        game.pending.append("[fast-forward] on, 8x")
        self.assertTrue(pilot.ensure_fast_forward(1.0))
        self.assertEqual(game.inputs_sent, [])

    def test_ad_hoc_refusal_is_kept(self):
        game = FakeGame()
        pilot = Pilot(game)
        game.pending.append("[fast-forward] single player only: the bind does nothing during ad hoc play")
        self.assertFalse(pilot.ensure_fast_forward(1.0))
        self.assertEqual(game.inputs_sent, [])


class Survey(unittest.TestCase):
    """A whole quest against a game that follows the commands."""

    def game(self, return_to_village=True, capture=True):
        game = FakeGame()

        def follow(g, line):
            if line.startswith("quest start"):
                g.pending.append("[debug] started quest 1001")
                g.task, g.quest, g.stage = 11, 1001, 98
            elif line == "state" and g.task == 11:
                g.clock -= 30
            elif line.startswith("hold"):
                g.pending.append(f"[debug] {line}")
            elif line == "quest":
                g.pending.append("[debug] time left 49:00, health 100")
            elif line == "quest time 2":
                g.pending.append("[debug] quest time set to 2 s")
                g.mission = 5
            elif line == "quest results" and return_to_village:
                g.pending.append("[debug] no results countdown (0)")
                g.task, g.quest, g.stage, g.mission = 10, 1, 139, 0
            elif line.startswith("loadout"):
                g.pending.append("[debug] wearing Ukanlos Soul Hatchet")
        game.on_command = follow
        if not capture:
            game.inputs = lambda lines: game.inputs_sent.extend(lines)
        return game

    def run_survey(self, game):
        result = atelier_campaign.QuestResult(1001, run_id="r")
        with patch.object(atelier_zones, "current", return_value={"stage": 98}), \
                patch.object(atelier_zones, "mission_objectives", return_value={"quest": 1001, "current": [], "initial": []}), \
                patch.object(atelier_zones, "quest_targets", return_value=[]), \
                patch.object(atelier_pilot.atelier_core, "capture_size", return_value=[960, 544]):
            atelier_pilot.run_quest(Pilot(game, poll=0.001), 1001, result, look_seconds=1.0)
        return result

    def test_forced_end_is_a_survey_pass_not_an_objective(self):
        result = self.run_survey(self.game())
        self.assertEqual(result.outcome, "forced_end")
        self.assertEqual(result.mission_state, 5)
        self.assertTrue(atelier_pilot.survey_passed(result))
        self.assertEqual(result.shots, ["q1001-arrivee", "q1001-pendant", "q1001-fin", "q1001-fin2", "q1001-village"])
        self.assertEqual(set(result.timing), {"village", "start", "load", "look", "end", "return"})

    def test_missing_captures_fail_the_survey(self):
        with patch.object(atelier_pilot.Pilot, "confirm_shots", lambda self, names, timeout=8.0: []):
            result = self.run_survey(self.game())
        self.assertFalse(atelier_pilot.survey_passed(result))

    def test_game_reported_clear_is_distinct_from_forced_failure(self):
        game = self.game()
        original = game.on_command

        def cleared(g, line):
            original(g, line)
            if line == "quest time 2":
                g.mission = 3
        game.on_command = cleared
        result = self.run_survey(game)
        self.assertEqual(result.outcome, "reported_clear")
        self.assertEqual(result.mission_state, 3)
        self.assertTrue(result.timer_shortened)
        self.assertTrue(atelier_pilot.survey_passed(result))
        result.outcome = "forced_end"
        self.assertFalse(atelier_pilot.survey_passed(result), "old mislabeled clears must not pass")

    def test_uninterpreted_end_codes_are_not_technical_passes(self):
        for state in (4, 6, 7, 8):
            with self.subTest(state=state):
                game = self.game()
                original = game.on_command

                def ended(g, line):
                    original(g, line)
                    if line == "quest time 2":
                        g.mission = state
                game.on_command = ended
                result = self.run_survey(game)
                self.assertEqual(result.outcome, "quest_ended")
                self.assertEqual(result.mission_state, state)
                self.assertFalse(atelier_pilot.survey_passed(result))

    def test_unconfirmed_timer_does_not_attribute_failure_to_the_clock(self):
        game = self.game()
        original = game.on_command

        def failed(g, line):
            original(g, line)
            if line == "quest time 2":
                g.pending[-1] = "[debug] quest time refused"
        game.on_command = failed
        result = self.run_survey(game)
        self.assertEqual(result.outcome, "quest_failed")
        self.assertFalse(result.timer_shortened)
        self.assertFalse(atelier_pilot.survey_passed(result))

    def test_no_return_is_reported(self):
        game = self.game(return_to_village=False)
        with patch.object(atelier_pilot.Pilot, "until", autospec=True,
                          side_effect=self._short(atelier_pilot.Pilot.until)):
            result = self.run_survey(game)
        self.assertEqual(result.outcome, "no_return")
        self.assertFalse(atelier_pilot.survey_passed(result))

    def test_accepted_but_stuck_departure_is_a_timeout_with_a_capture(self):
        game = self.game()
        follow = game.on_command

        def stuck(g, line):
            if line.startswith("quest start"):
                g.pending.append("[debug] started quest 1001")
                g.quest, g.scene = 1001, 0x1D
            else:
                follow(g, line)
        game.on_command = stuck
        with patch.object(atelier_pilot.Pilot, "until", autospec=True,
                          side_effect=self._short(atelier_pilot.Pilot.until)):
            result = self.run_survey(game)
        self.assertEqual(result.outcome, "start_timeout")
        self.assertEqual(result.shots, ["q1001-depart-bloque"])
        self.assertIn("départ en cours", result.notes[0])
        self.assertFalse(result.reached)
        self.assertFalse(atelier_pilot.survey_passed(result))
        self.assertEqual(game.sent.count("quest start 1001"), 1, "never resend while departure remains pending")

    def test_refused_departure_keeps_its_distinct_outcome_and_capture(self):
        game = self.game()
        follow = game.on_command

        def refused(g, line):
            if line.startswith("quest start"):
                g.pending.append("[debug] not started: the game has no quest 1001")
            else:
                follow(g, line)
        game.on_command = refused
        result = self.run_survey(game)
        self.assertEqual(result.outcome, "start_refused")
        self.assertEqual(result.shots, ["q1001-depart-bloque"])
        self.assertIn("has no quest", result.notes[0])
        self.assertFalse(atelier_pilot.survey_passed(result))

    def test_frozen_loaded_quest_keeps_a_loading_failure_capture(self):
        game = self.game()
        follow = game.on_command

        def frozen(g, line):
            follow(g, line)
            g.clock = 90000
        game.on_command = frozen
        with patch.object(atelier_pilot.Pilot, "until", autospec=True,
                          side_effect=self._short(atelier_pilot.Pilot.until)):
            result = self.run_survey(game)
        self.assertEqual(result.outcome, "not_loaded")
        self.assertEqual(result.shots, ["q1001-chargement-bloque"])
        self.assertFalse(atelier_pilot.survey_passed(result))

    @staticmethod
    def _short(until):
        def wrapper(self, what, probe, timeout, poll=None):
            return until(self, what, probe, min(timeout, 0.2), poll=0.001)
        return wrapper

    def test_blocked_village_is_not_a_pass(self):
        game = self.game()
        original = game.on_command

        def dialog(g, line):
            original(g, line)
            if line == "quest results":
                g.scene = 3          # a dialog over the village
        game.on_command = dialog
        with patch.object(atelier_pilot.Pilot, "until", autospec=True,
                          side_effect=self._short(atelier_pilot.Pilot.until)):
            result = self.run_survey(game)
        self.assertTrue(result.returned)
        self.assertEqual(result.outcome, "village_blocked")
        self.assertIn("q1001-village", result.shots, "the blocked village is captured as evidence")
        self.assertFalse(atelier_pilot.survey_passed(result))
        self.assertLessEqual(game.inputs_sent.count("0:pad a 6"), atelier_pilot.DIALOG_PRESSES)

    def test_no_confirm_lands_in_the_village(self):
        game = self.game()
        result = self.run_survey(game)
        self.assertEqual(result.outcome, "forced_end")
        self.assertNotIn("0:pad b 6", game.inputs_sent, "the results answer already brought the village")

    def test_dialog_closed_by_back_then_ready_is_read_again(self):
        game = self.game()
        original = game.on_command

        def dialog(g, line):
            original(g, line)
            if line == "quest results":
                g.scene = 3
        game.on_command = dialog
        follow_inputs = game.inputs

        def inputs(lines):
            follow_inputs(lines)
            if "0:pad a 6" in lines:
                game.scene = 0
        game.inputs = inputs
        with patch.object(atelier_pilot.Pilot, "until", autospec=True,
                          side_effect=self._short(atelier_pilot.Pilot.until)):
            result = self.run_survey(game)
        self.assertEqual(result.outcome, "forced_end")
        self.assertTrue(result.village_ready)
        self.assertEqual(game.inputs_sent.count("0:pad a 6"), 1)
        self.assertIn("dialogue fermé au village (1 ×)", result.notes)

    def test_back_is_never_pressed_outside_a_dialog(self):
        game = FakeGame()
        game.scene = 0x1D            # a departure under way, not a text box
        with self.assertRaises(WaitTimeout) as caught:
            Pilot(game, poll=0.001).leave_dialog(0.2)
        self.assertEqual(caught.exception.presses, 0)
        self.assertEqual(game.inputs_sent, [])

    def test_engine_failure_mid_quest_is_a_stop(self):
        game = self.game()
        original = game.on_command

        def crash(g, line):
            original(g, line)
            if line == "quest time 2":
                g.running = False
        game.on_command = crash
        with self.assertRaises(Stopped):
            self.run_survey(game)


class LedgerTests(unittest.TestCase):
    def env(self, **change):
        components = {"executable_sha256": "e", "overlays_sha256": "o", "data_sha256": "d", "seed_sha256": "s",
                      "rtc_us": 1609459200123456, "driver_sha256": "p", "settings": {"speed": 8},
                      "scenario": {}, "player_settings_sha256": "x", **change}
        components["complete"] = all(components[k] is not None for k in
                                     ("executable_sha256", "overlays_sha256", "data_sha256", "seed_sha256",
                                      "driver_sha256")) and components["rtc_us"] is not None
        key = atelier_ledger._sha(json.dumps(components, sort_keys=True).encode())
        return {"key": key, "components": components}

    def test_reuse_needs_the_same_key_and_a_pass(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = atelier_ledger.Ledger(Path(root))
            env = self.env()
            ledger.record("survey", 1001, env, "passed", {"campaign": "c", "run_id": "r"})
            self.assertEqual(ledger.find("survey", 1001, env)["evidence"]["run_id"], "r")
            for change in ({"executable_sha256": "e2"}, {"overlays_sha256": "o2"}, {"data_sha256": "d2"},
                           {"seed_sha256": "s2"}, {"rtc_us": 1}, {"driver_sha256": "p2"},
                           {"settings": {"speed": 1}}):
                self.assertIsNone(ledger.find("survey", 1001, self.env(**change)), change)
            self.assertIsNone(ledger.find("gameplay", 1001, env), "a survey never validates gameplay")

    def test_failures_and_incomplete_keys_are_never_reused(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = atelier_ledger.Ledger(Path(root))
            ledger.record("survey", 1002, self.env(), "no_return", {"campaign": "c", "run_id": "r"})
            self.assertIsNone(ledger.find("survey", 1002, self.env()))
            incomplete = self.env(rtc_us=None)
            ledger.record("survey", 1003, incomplete, "passed", {"campaign": "c", "run_id": "r"})
            self.assertIsNone(ledger.find("survey", 1003, incomplete))

    def test_forget_forces_a_new_check(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = atelier_ledger.Ledger(Path(root))
            env = self.env()
            ledger.record("survey", 1001, env, "passed", {"campaign": "c", "run_id": "r"})
            self.assertTrue(ledger.forget("survey", 1001))
            self.assertIsNone(ledger.find("survey", 1001, env))

    def test_rerun_keeps_history(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = atelier_ledger.Ledger(Path(root))
            env = self.env()
            ledger.record("survey", 1001, env, "no_return", {"campaign": "a", "run_id": "1"})
            entry = ledger.record("survey", 1001, env, "passed", {"campaign": "b", "run_id": "2"})
            self.assertEqual(entry["history"][0]["status"], "no_return")

    def test_objective_families(self):
        slay = {"current": [{"flags": 0x11, "target": 5, "quantity": 20}]}
        ticket = {"current": [{"flags": 0x12, "target": 927, "quantity": 1}]}
        self.assertEqual(atelier_ledger.objective_kind(slay, []), "abattage")
        self.assertEqual(atelier_ledger.objective_kind(ticket, [{"em_id": 6}]), "livraison (ticket)")
        self.assertEqual(atelier_ledger.objective_kind(ticket, []), "livraison (ticket)")
        self.assertEqual(atelier_ledger.objective_kind(None, None), "inconnu")


class Resume(unittest.TestCase):
    def test_unfinished_results_are_taken_up_again(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            env = {"key": "k", "components": {}}
            (folder / "campagne.json").write_text(json.dumps({
                "plan": [1001, 1002, 1003], "environment_key": "k", "state": "running", "runs": ["r"],
                "results": [{"quest": 1001, "final": True, "outcome": "forced_end"},
                            {"quest": 1002, "final": False, "outcome": "cancelled"}]}), encoding="utf-8")
            out, results, metadata = atelier_pilot._open_campaign(None, folder, [], {}, "normal", {}, env)
            self.assertEqual([r.quest for r in results], [1001])
            self.assertEqual(metadata["plan"], [1001, 1002, 1003])
            self.assertEqual(len(metadata["resumed_at"]), 1)

    def test_changed_environment_refuses_resumption(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / "campagne.json").write_text(json.dumps({"plan": [1], "environment_key": "old",
                                                              "results": []}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                atelier_pilot._open_campaign(None, folder, [], {}, "normal", {}, {"key": "new", "components": {}})


if __name__ == "__main__":
    unittest.main()
