"""UI control contracts without opening a window or launching a game."""
from pathlib import Path
import json
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atelier_ui import WorkshopUI


class AutomationUITests(unittest.TestCase):
    def test_requested_stop_with_abnormal_exit_is_described_as_requested(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "runs" / "synthetic"
            run.mkdir(parents=True)
            ui = WorkshopUI.__new__(WorkshopUI)
            ui.status_data = {"data_dir": folder}
            ui._event_log, ui.status_var = Mock(), Mock()
            for reason, expected in (("arrêt demandé", "code de sortie -1"),
                                     ("Missing HLE: test", "arrêté par le jeu")):
                with self.subTest(reason=reason):
                    (run / "manifest.json").write_text(json.dumps({"state": "failed",
                        "stop_reason": reason, "exit_code": -1}), encoding="utf-8")
                    ui._announce_end("synthetic")
                    text = ui._event_log.call_args.args[0]
                    self.assertIn(expected, text)
                    if reason == "arrêt demandé":
                        self.assertNotIn("Signaler le bug", text)

    def test_manual_inputs_are_disabled_while_stop_remains_available(self):
        ui = WorkshopUI.__new__(WorkshopUI)
        ui.busy, ui.smoke, ui.closing, ui.automation_active = True, False, False, True
        ui.status_data = {"prepared": True, "active": {"state": "running"}}
        ui.stop_button, ui.automation_stop = Mock(), Mock()
        ui.start_button, ui.checkpoint_button = Mock(), Mock()
        ui.hunt_button, ui.campaign_button = Mock(), Mock()
        manual = Mock()
        ui.mutable = [ui.hunt_button, ui.campaign_button]
        ui.live_controls = [manual, ui.stop_button, ui.automation_stop]
        ui._states()
        manual.configure.assert_called_with(state="disabled")
        ui.stop_button.configure.assert_called_with(state="normal")
        ui.automation_stop.configure.assert_called_with(state="normal")
        ui.hunt_button.configure.assert_called_with(state="disabled")

    def test_stop_sets_cancellation_before_stopping_owned_game(self):
        ui = WorkshopUI.__new__(WorkshopUI)
        ui.automation_cancel = threading.Event()
        ui.footer_var, ui.backend, ui.events = Mock(), Mock(), queue.Queue()
        with patch("atelier_ui.threading.Thread") as worker:
            ui.stop()
            self.assertTrue(ui.automation_cancel.is_set())
            worker.call_args.kwargs["target"]()
        ui.backend.stop.assert_called_once()

    def test_cheat_and_loadout_controls_send_the_game_commands(self):
        ui = WorkshopUI.__new__(WorkshopUI)
        ui.backend, ui.events = Mock(), queue.Queue()
        with patch("atelier_ui.threading.Thread") as worker:
            for line in ("hold stamina on", "loadout best"):
                ui.send_debug(line)
                worker.call_args.kwargs["target"]()
        self.assertEqual([c.args[0] for c in ui.backend.debug_command.call_args_list], ["hold stamina on", "loadout best"])
        self.assertEqual(ui.events.get_nowait()[0], "control")
        ui.backend.debug_command.side_effect = RuntimeError("aucun essai en cours")
        with patch("atelier_ui.threading.Thread") as worker:
            ui.send_debug("money 9999999")
            worker.call_args.kwargs["target"]()
        ui.events.get_nowait()
        self.assertEqual(ui.events.get_nowait(), ("control_error", "aucun essai en cours"))

    def test_result_label_requires_visual_review_and_uses_plain_language(self):
        ui = WorkshopUI.__new__(WorkshopUI)
        ui.hunt_status = Mock()
        ui._hunt_progress({"state": "quest ended", "phase": "finished", "kills": 20, "mission_state": 7,
                           "stage": 75, "objective": {"required": 20}})
        text = ui.hunt_status.set.call_args.args[0]
        self.assertIn("Fin de quête (état 7)", text)
        self.assertIn("écrans de fin non quittés", text)
        self.assertNotIn("Victoire", text)
        ui._hunt_progress({"state": "quest_cleared", "phase": "finished", "kills": 19, "mission_state": 3,
                           "returned_to_village": True, "objective": {"required": 20}})
        self.assertIn("Quête réussie selon le jeu ; retour au village", ui.hunt_status.set.call_args.args[0])
        ui._hunt_progress({"state": "stalled", "phase": "finished", "reason": "internal_reason"})
        self.assertIn("absence de progrès", ui.hunt_status.set.call_args.args[0])
        self.assertNotIn("internal_reason", ui.hunt_status.set.call_args.args[0])

    def test_game_progress_is_distinct_from_observed_deaths_and_stale_reads(self):
        ui = WorkshopUI.__new__(WorkshopUI)
        ui.hunt_status = Mock()
        result = {"state": "running", "phase": "combat", "kills": 4,
                  "objective": {"required": 20}, "objective_progress_available": True,
                  "objective_progress": {"completed": 10, "required": 20}}
        ui._hunt_progress(result)
        self.assertIn("objectif du jeu : 10/20", ui.hunt_status.set.call_args.args[0])
        self.assertIn("morts observées : 4", ui.hunt_status.set.call_args.args[0])
        result["objective_progress_available"] = False
        ui._hunt_progress(result)
        self.assertIn("dernier relevé du jeu : 10/20", ui.hunt_status.set.call_args.args[0])


class CampaignUITests(unittest.TestCase):
    def make_ui(self):
        ui = WorkshopUI.__new__(WorkshopUI)
        ui.busy, ui.smoke, ui.automation_active = False, False, False
        ui._running = Mock(return_value=False)
        ui.campaign_quests = Mock(get=Mock(return_value="2 1002"))
        ui.campaign_look = Mock(get=Mock(return_value="12"))
        ui.campaign_speed = Mock(get=Mock(return_value="8"))
        ui.campaign_captures = Mock(get=Mock(return_value="Essentielles"))
        ui.campaign_force = Mock(get=Mock(return_value=False))
        ui.backend = Mock()
        ui.backend._current_seed.return_value = {"id": "selected-seed"}
        ui.automation_cancel = threading.Event()
        ui.events = queue.Queue()
        ui._task, ui._input_error, ui._states, ui.open_path, ui._event_log = (Mock() for _ in range(5))
        return ui

    def test_campaign_uses_fast_pilot_fixed_rtc_and_selected_seed(self):
        ui = self.make_ui()
        ui.automation_cancel.set()
        with patch("atelier_ui.atelier_pilot.run_campaign", return_value=Path("synthetic")) as pilot, \
                patch("atelier_ui.atelier_campaign.run_campaign") as legacy:
            ui.run_campaign()
            self.assertTrue(ui.automation_active)
            self.assertFalse(ui.automation_cancel.is_set())
            _, worker, done = ui._task.call_args.args
            # Edits after scheduling must not change the worker's configuration.
            ui.campaign_speed.get.return_value = "1"
            ui.backend._current_seed.return_value = {"id": "later-seed"}
            out = worker()
            args, settings = pilot.call_args
            self.assertEqual(args, (ui.backend, [2, 1002]))
            self.assertEqual(settings["seed_id"], "selected-seed")
            self.assertEqual(settings["rtc_us"], 1609459200123456)
            self.assertEqual(settings["speed"], 8)
            self.assertEqual(settings["captures"], "fast")
            self.assertEqual(settings["look_seconds"], 12)
            self.assertFalse(settings["force"])
            self.assertIs(settings["cancel"], ui.automation_cancel)
            settings["log"]("quête 2 : village_blocked")
            self.assertEqual(ui.events.get_nowait(), ("note", "Campagne : quête 2 : village_blocked"))
            ui.automation_cancel.set()
            self.assertTrue(settings["cancel"].is_set())
            done(out)
            self.assertFalse(ui.automation_active)
            self.assertEqual(ui.last_report, Path("synthetic/campagne.html"))
            ui.open_path.assert_called_once_with(ui.last_report)
            legacy.assert_not_called()

    def test_campaign_allows_realtime_detailed_captures_and_forced_recheck(self):
        ui = self.make_ui()
        ui.campaign_speed.get.return_value = "1"
        ui.campaign_captures.get.return_value = "Détaillées"
        ui.campaign_force.get.return_value = True
        ui.campaign_look.get.return_value = "120"
        with patch("atelier_ui.atelier_pilot.run_campaign") as pilot:
            ui.run_campaign()
            ui._task.call_args.args[1]()
        self.assertEqual(pilot.call_args.kwargs["speed"], 1)
        self.assertEqual(pilot.call_args.kwargs["captures"], "detailed")
        self.assertEqual(pilot.call_args.kwargs["look_seconds"], 120)
        self.assertTrue(pilot.call_args.kwargs["force"])

    def test_invalid_campaign_input_does_not_schedule_or_clear_cancellation(self):
        for field, value in (("quests", ""), ("quests", "-1"), ("quests", "0"), ("quests", "a"),
                             ("look", "0"), ("look", "121"), ("look", "nan"), ("look", "inf"),
                             ("look", "a"), ("speed", "0"), ("speed", "9"), ("speed", "1.5"),
                             ("captures", "inconnues")):
            with self.subTest(field=field, value=value):
                ui = self.make_ui()
                getattr(ui, "campaign_" + field).get.return_value = value
                ui.automation_cancel.set()
                ui.run_campaign()
                ui._input_error.assert_called_once()
                ui._task.assert_not_called()
                self.assertFalse(ui.automation_active)
                self.assertTrue(ui.automation_cancel.is_set())

    def test_missing_seed_does_not_schedule_campaign(self):
        ui = self.make_ui()
        ui.backend._current_seed.return_value = None
        ui.run_campaign()
        ui._task.assert_not_called()
        self.assertIn("point de départ", str(ui._input_error.call_args.args[0]))

    def test_busy_running_and_smoke_modes_do_not_schedule_campaign(self):
        for guard in ("busy", "running", "smoke"):
            with self.subTest(guard=guard):
                ui = self.make_ui()
                if guard == "running":
                    ui._running.return_value = True
                else:
                    setattr(ui, guard, True)
                ui.run_campaign()
                ui._task.assert_not_called()
                self.assertFalse(ui.automation_active)
