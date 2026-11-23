"""Contracts of the live session and of the Trial operations it shares with batch plans; no game is started."""
import json
from multiprocessing.connection import Listener
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_live as live
import atelier_pilot as ap
import atelier_retro as r
import atelier_zones as zones


SNAPSHOT = {"available": True, "money": 1000, "item_box": [{"id": 163, "count": 2}], "pouch": [],
            "equipment_box": [], "farm_upgrades_hex": "00", "kitchen_skill_ids_hex": "00", "money_address": 0x099FF090}
UI = {"available": True, "scene": 0, "dialogue_open": False, "text_complete": None, "next": False,
      "runs": [{"text": "Pokke"}], "menu": None}


class FakeRetroPilot:
    def __init__(self):
        self.buttons, self.commands, self.timings, self.transcript = [], [], [], []
        self.deadline = time.monotonic() + 60
        self.snapshots = [SNAPSHOT, {**SNAPSHOT, "money": 500}]
        self.ui_state = UI

    def check(self): pass
    def button(self, name, frames=6): self.buttons.append(name)
    def ui(self): return self.ui_state
    def command(self, line, timeout=6):
        self.commands.append(line)
        return ["talk queued"] if line.startswith("talk") else ["zenny 10000"]

    def json_command(self, command, prefix):
        self.commands.append(command)
        if command == "npc": return [{"slot": 4, "type": 6}]
        return self.snapshots.pop(0)

    def advance_dialogue_until(self, target, timeout): self.commands.append("advance " + target)
    def select(self, text, timeout): self.commands.append("select " + text)
    def back_to_village(self, timeout): self.commands.append("back")


class FakePilot:
    village_ready = staticmethod(ap.Pilot.village_ready)

    def __init__(self):
        self.shots, self.state = [], {"task": 10, "stage": 139, "scene": 0, "lobby_running": True, "lobby_loaded": True}

    def snapshot(self): return self.state
    def wait_village(self, timeout): return self.state
    def pause(self, seconds): pass
    def shoot(self, name): self.shots.append(name); return name
    def confirm_shots(self, names, timeout=8): return names

    def until(self, what, probe, timeout, poll=None):
        for _ in range(5):
            value = probe()
            if value: return value
        raise ap.WaitTimeout(what, timeout)


def make_trial(directory, assisted=False):
    workshop = SimpleNamespace(project=Path(directory), data_dir=Path(directory))
    trial = r.Trial(workshop, "seed-1", "trial", 60, assisted)
    trial.session = SimpleNamespace(dir=Path(directory), alive=lambda: True, run_id="run-1")
    trial.trial_dir, trial.rp, trial.pilot = Path(directory), FakeRetroPilot(), FakePilot()
    trial.result.update(run_id="run-1")
    return trial


class TrialSteps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.trial = make_trial(self.tmp.name)

    def test_names_become_usable_only_after_their_step_passed(self):
        t = self.trial
        t.rp.snapshots = []  # the snapshot command fails: no name is recorded
        with self.assertRaises(IndexError): t.step({"op": "snapshot", "name": "before"})
        self.assertEqual(t.result["steps"][-1]["state"], "failed")
        with self.assertRaises(ValueError): t.step({"op": "diff", "before": "before", "after": "before"})
        self.assertEqual(len(t.result["steps"]), 1, "a refused step is not recorded as run")
        t.rp.snapshots = [SNAPSHOT, {**SNAPSHOT, "money": 500}]
        t.step({"op": "snapshot", "name": "before"}); t.step({"op": "snapshot", "name": "after"})
        entry = t.step({"op": "diff", "before": "before", "after": "after"})
        self.assertEqual(entry["difference"]["money_delta"], -500)
        t.step({"op": "assert", "field": "money_delta", "equals": -500})
        self.assertEqual(t.verdict(), "passed")
        self.assertTrue((Path(self.tmp.name) / "before.snapshot.json").is_file())
        with self.assertRaises(AssertionError): t.step({"op": "assert", "field": "money_delta", "equals": 0})

    def test_exploration_without_assertion_is_not_a_validation(self):
        self.trial.step({"op": "button", "button": "dpright", "repeat": 3})
        self.assertEqual(self.trial.rp.buttons, ["dpright"] * 3)
        self.assertEqual(self.trial.verdict(), "explored")

    def test_dialogue_and_menu_operations_reach_the_pilot(self):
        t = self.trial
        t.rp.ui_state = {**UI, "scene": 36}
        t.step({"op": "talk", "type": 6})
        t.step({"op": "advance", "until": "menu"}); t.step({"op": "select", "text": "Yes"}); t.step({"op": "back"})
        self.assertEqual(t.rp.commands, ["npc", "talk 4", "advance menu", "select Yes", "back"])
        with self.assertRaises(ValueError): t.step({"op": "talk", "type": 99999999})

    def test_waits_on_ui_state_and_time(self):
        t = self.trial
        t.step({"op": "wait", "ui": {"contains_text": "Pokke", "scene": 0}})
        t.step({"op": "wait", "state": {"stage": 139}})
        t.step({"op": "wait", "seconds": 0})
        with self.assertRaises(ap.WaitTimeout): t.step({"op": "wait", "state": {"stage": 46}, "timeout": 1})

    def test_command_expectation_and_capture(self):
        t = self.trial
        self.assertEqual(t.step({"op": "command", "command": "money 10000", "expect": "zenny"})["answer"], ["zenny 10000"])
        with self.assertRaises(AssertionError): t.step({"op": "command", "command": "money 1", "expect": "absent"})
        self.assertEqual(t.step({"op": "capture", "name": "proof"})["files"], ["proof"])

    def test_run_reports_explored_failed_and_cancelled(self):
        plan = {"name": "p", "steps": [{"op": "button", "button": "a"}]}
        def fake_launch(trial):
            trial.session = SimpleNamespace(dir=Path(self.tmp.name), alive=lambda: True)
            trial.trial_dir, trial.rp, trial.pilot = Path(self.tmp.name), FakeRetroPilot(), FakePilot()
        with patch.object(r.Trial, "guard"), patch.object(r.Trial, "launch", fake_launch), patch.object(r.Trial, "close", lambda t: t.result):
            self.assertEqual(r.run(None, plan, "seed")["state"], "explored")
            with patch.object(FakeRetroPilot, "button", side_effect=ap.WaitTimeout("x", 1)):
                failed = r.run(None, plan, "seed")
            self.assertEqual((failed["state"], failed["steps"][0]["state"]), ("failed", "failed"))
            cancel = threading.Event(); cancel.set()
            with patch.object(FakeRetroPilot, "button", side_effect=ap.Stopped("cancelled")):
                self.assertEqual(r.run(None, plan, "seed", cancel=cancel)["state"], "cancelled")


class FakeSession:
    started = []

    def __init__(self, workshop, seed, seconds, variant=None, scenario=None):
        self.run_id = f"run-{len(FakeSession.started) + 1}"
        self.dir = workshop.data_dir / "runs" / self.run_id
        (self.dir / "game/ms0").mkdir(parents=True); (self.dir / "frames").mkdir()
        from PIL import Image
        Image.new("RGB", (8, 8)).save(self.dir / "frames/shot.bmp")
        self.scenario, self.seconds = scenario, seconds
        FakeSession.started.append(self)

    def alive(self): return True


class GuardedLifecycle(unittest.TestCase):
    """guard -> launch -> steps -> close with a fake game: digests, lock, reload and evidence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for path in ("game-mhfu/ms0", "seeds/s/ms0", "references/mhp2g-decomp/config"): (root / path).mkdir(parents=True)
        (root / "references/mhp2g-decomp/config/lobby_task.symbol_addrs.txt").write_text("NpcManager = 0x09B1E708;", encoding="utf-8")
        self.stopped = []
        self.workshop = SimpleNamespace(project=root, data_dir=root, _seed=lambda s: {}, stop=lambda: self.stopped.append(1),
                                        _run_dir=lambda run: root / "runs" / run)
        FakeSession.started = []
        pilot = FakePilot(); pilot.ensure_fast_forward = lambda: True
        self.patches = [patch.object(r, "yakumo_pids", return_value=[]), patch.object(r.campaign, "Session", FakeSession),
                        patch.object(r.ap, "Pilot", lambda session, cancel=None: pilot),
                        patch.object(r, "RetroPilot", lambda p, deadline: FakeRetroPilot())]
        for p in self.patches: p.start(); self.addCleanup(p.stop)

    def test_full_lifecycle_keeps_originals_and_writes_the_report(self):
        trial = r.Trial(self.workshop, "s", "life", 60, speed=8)
        trial.guard(); trial.launch()
        self.assertEqual(FakeSession.started[0].scenario["settings"]["video.fast_forward_speed"], "8")
        trial.step({"op": "snapshot", "name": "before"})
        (trial.session.dir / "game/ms0/SAVE").write_bytes(b"changed in game")
        trial.step({"op": "reload"})
        self.assertEqual(trial.result["run_ids"], ["run-1", "run-2"])
        self.assertTrue(FakeSession.started[1].scenario["private_ms0"].endswith(str(Path("run-1/game/ms0"))))
        with self.assertRaises(AssertionError): trial.step({"op": "reload"})  # run-2's copy did not change
        trial.result["state"] = trial.verdict()
        result = trial.close()
        self.assertTrue(result["protected_unchanged"]); self.assertEqual(result["state"], "explored")
        self.assertTrue((Path(result["directory"]) / "retro-report.html").is_file())
        self.assertTrue((Path(result["directory"]) / "frames/run-2-shot.bmp").is_file(), "frames of the reloaded game are kept")
        self.assertIs(trial.close(), result, "closing twice is harmless")
        self.assertTrue(trial.lock.closed)

    def test_changed_original_fails_the_trial(self):
        trial = r.Trial(self.workshop, "s", "life", 60)
        trial.guard(); trial.launch()
        (Path(self.tmp.name) / "game-mhfu/ms0/OOPS").write_bytes(b"x")
        trial.result["state"] = "explored"
        self.assertEqual(trial.close()["state"], "failed")

    def test_lock_is_released_when_the_report_fails(self):
        trial = r.Trial(self.workshop, "s", "life", 60)
        trial.guard(); trial.launch()
        with patch.object(r, "report", side_effect=OSError("unreadable capture")), self.assertRaises(OSError):
            trial.close()
        self.assertTrue(trial.lock.closed)
        self.assertTrue(trial.result["protected_unchanged"])

    def test_refusals_before_launch(self):
        with patch.object(r, "yakumo_pids", return_value=[42]), self.assertRaises(RuntimeError):
            r.Trial(self.workshop, "s", "t", 60).guard()
        with self.assertRaises(ValueError): r.Trial(self.workshop, "s", "t", 60, from_run="missing").guard()
        with self.assertRaises(ValueError): r.Trial(self.workshop, "s", "t", 60, speed=9)
        with self.assertRaises(ValueError): r.Trial(self.workshop, "s", "../t", 60)
        first = r.Trial(self.workshop, "s", "t", 60); first.guard()
        with self.assertRaises(RuntimeError): r.Trial(self.workshop, "s", "t", 60).guard()  # lock held
        first._release()

    def test_memory_operations_write_evidence_in_the_run(self):
        trial = r.Trial(self.workshop, "s", "mem", 60, speed=1)
        trial.guard(); self.addCleanup(trial._release); trial.launch()
        trial.rp.command = lambda line, timeout=6: (["symbols loaded 1"] if line.startswith("symbols") else
                                                     ["watch updated"] if line.startswith("watch") else self._dump(line))
        trial.step({"op": "symbols", "scope": "lobby_task"})
        trial.step({"op": "snapshot", "name": "a"})
        trial.step({"op": "watch", "snapshot": "a", "field": "money_address", "size": 4})
        trial.step({"op": "dump", "name": "d1", "address": {"snapshot": "a", "field": "money_address"}, "size": 16})
        trial.step({"op": "dump", "name": "d2", "address": "0x099FF090", "size": "16"})
        entry = trial.step({"op": "memory_diff", "before": "d1", "after": "d2", "scope": "lobby_task"})
        self.assertEqual(entry["changed_bytes"], 16)
        self.assertTrue((trial.trial_dir / entry["artifact"]).is_file())
        with self.assertRaises(ValueError): trial.step({"op": "symbols", "scope": "absent"})

    @staticmethod
    def _dump(line):
        path = Path(line.split('"')[1]); path.write_bytes((b"\x01" if path.stem == "d2" else b"\x00") * 16)
        return ["dumped"]


class StartPoints(unittest.TestCase):
    def test_fixture_plan_start_and_explicit_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            fixtures = Path(directory) / "fixtures.json"
            fixtures.write_text(json.dumps({"ready": {"seed": "s1", "from_run": "run-1", "ms0_sha256": "abc"}}), encoding="utf-8")
            with patch.object(r, "FIXTURES", fixtures):
                self.assertEqual(r.resolve_start(start="ready"), ("s1", "run-1", "abc"))
                with self.assertRaises(ValueError): r.resolve_start(start="missing")
        plan = {"start": {"seed": "s2", "from_run": "run-2"}}
        self.assertEqual(r.resolve_start(plan=plan), ("s2", "run-2", None))
        self.assertEqual(r.resolve_start("s3", plan=plan), ("s3", None, None))
        with self.assertRaises(ValueError): r.resolve_start()

    def test_plan_start_and_repeat_are_validated(self):
        step = [{"op": "button", "button": "a"}]
        r.validate({"name": "p", "start": {"seed": "s", "from_run": None}, "steps": step})
        for start in ("s", {"seed": "../x"}, {"seed": "s", "from_run": "a b"}):
            with self.assertRaises(ValueError): r.validate({"name": "p", "start": start, "steps": step})
        for repeat in (0, 61, True, 2.0):
            with self.assertRaises(ValueError): r.validate({"name": "p", "steps": [{"op": "button", "button": "a", "repeat": repeat}]})

    def test_fixture_digest_mismatch_refuses_the_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in ("game-mhfu/ms0", "seeds/s/ms0", "runs/run-1/game/ms0"): (root / path).mkdir(parents=True)
            workshop = SimpleNamespace(project=root, data_dir=root, _seed=lambda s: {}, _run_dir=lambda run: root / "runs" / run)
            trial = r.Trial(workshop, "s", "t", 60, from_run="run-1", expected_source="not-the-digest")
            with patch.object(r, "yakumo_pids", return_value=[]), self.assertRaises(ValueError):
                trial.guard()
            self.assertTrue(trial.lock.closed, "the lock is released when the start is refused")


class Shorthand(unittest.TestCase):
    def test_key_values_json_and_lists(self):
        self.assertEqual(live.parse_steps(["talk", "type=6"]), [{"op": "talk", "type": 6}])
        self.assertEqual(live.parse_steps(["select", "text=Beginner Train"]), [{"op": "select", "text": "Beginner Train"}])
        self.assertEqual(live.parse_steps(['{"op":"back"}']), [{"op": "back"}])
        self.assertEqual(len(live.parse_steps(['[{"op":"back"},', '{"op":"look"}]'])), 2)
        for bad in (["talk", "type"], [], ["[1]"]):
            with self.assertRaises(ValueError): live.parse_steps(bad)

    def test_compress_merges_identical_presses_only(self):
        steps = [{"op": "button", "button": "dpright"}] * 3 + [{"op": "button", "button": "a"}, {"op": "button", "button": "dpright", "frames": 2}]
        self.assertEqual(live.compress(steps), [{"op": "button", "button": "dpright", "repeat": 3}, {"op": "button", "button": "a"},
                                                {"op": "button", "button": "dpright", "frames": 2}])
        self.assertEqual(live.compress([{"op": "button", "button": "a", "repeat": 60}, {"op": "button", "button": "a"}])[1],
                         {"op": "button", "button": "a"})

    def test_export_keeps_passed_steps_and_the_start(self):
        result = {"run_id": "run-1", "seed": "s1", "starting_private_run": "run-0", "assisted": False, "steps": [
            {"index": 0, "state": "passed", "seconds": 1, "step": {"op": "snapshot", "name": "a"}},
            {"index": 1, "state": "failed", "seconds": 1, "step": {"op": "select", "text": "Wrong"}},
            {"index": 2, "state": "passed", "seconds": 1, "step": {"op": "button", "button": "a"}},
            {"index": 3, "state": "passed", "seconds": 1, "step": {"op": "button", "button": "a"}},
            {"index": 4, "state": "passed", "seconds": 1, "step": {"op": "snapshot", "name": "b"}},
            {"index": 5, "state": "passed", "seconds": 1, "step": {"op": "diff", "before": "a", "after": "b"}}]}
        plan = live.export_plan(result, "farm")
        self.assertEqual(plan["start"], {"seed": "s1", "from_run": "run-0"})
        self.assertEqual([s["op"] for s in plan["steps"]], ["snapshot", "button", "snapshot", "diff"])
        self.assertEqual(plan["steps"][1]["repeat"], 2)
        with self.assertRaises(ValueError): live.export_plan(result, "farm", first=2)  # "a" falls outside the range
        with self.assertRaises(ValueError): live.export_plan(result, "farm", first=1, last=1)


class CommandLine(unittest.TestCase):
    def test_client_commands_send_requests_and_set_the_exit_code(self):
        sent = []
        def fake_request(workshop, message, timeout=900):
            sent.append(message)
            return {"ok": message["cmd"] != "look"}
        with patch.object(live, "request", fake_request), patch("builtins.print"):
            self.assertEqual(live.main(["do", "button", "button=a", "--no-look"]), 0)
            self.assertEqual(live.main(["export", "farm", "--first", "2", "--force"]), 0)
            self.assertEqual(live.main(["look"]), 1)
            self.assertEqual(live.main(["steps"]), 0)
            with patch.object(live, "read_session", return_value={"state": "stopped"}):
                self.assertEqual(live.main(["stop"]), 0)
                self.assertEqual(live.main(["status"]), 0)
        self.assertEqual(sent[0], {"cmd": "do", "steps": [{"op": "button", "button": "a"}], "look": False})
        self.assertEqual(sent[1]["first"], 2); self.assertTrue(sent[1]["force"])
        self.assertEqual([m["cmd"] for m in sent[2:]], ["look", "steps", "stop"])


class LiveRequests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.trial = make_trial(self.tmp.name)
        self.live = live.Live(self.trial, 600)

    def test_do_answers_with_entries_and_the_screen(self):
        reply = self.live.handle({"cmd": "do", "steps": [{"op": "button", "button": "a"}]})
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["look"]["stage"], 139); self.assertTrue(reply["look"]["village_ready"])
        self.assertEqual(reply["look"]["text"], ["Pokke"])

    def test_failure_keeps_the_session_and_stops_the_batch(self):
        with patch.object(FakeRetroPilot, "button", side_effect=[None, ap.WaitTimeout("press", 1)]):
            reply = self.live.handle({"cmd": "do", "steps": [{"op": "button", "button": "a"}, {"op": "button", "button": "b"},
                                                             {"op": "button", "button": "x"}]})
        self.assertFalse(reply["ok"]); self.assertFalse(self.live.done)
        self.assertEqual([e["state"] for e in reply["entries"]], ["passed", "failed"])
        self.assertEqual(self.trial.pilot.shots, ["failure-1"])
        rejected = self.live.handle({"cmd": "do", "steps": [{"op": "fly"}]})
        self.assertEqual(rejected["entries"][0]["state"], "rejected")
        self.assertEqual(len(self.trial.result["steps"]), 2)
        self.assertEqual([s["state"] for s in self.live.handle({"cmd": "steps"})["steps"]], ["passed", "failed"])

    def test_dead_game_ends_the_session(self):
        self.trial.session.alive = lambda: False
        reply = self.live.handle({"cmd": "do", "steps": [{"op": "button", "button": "a"}]})
        self.assertTrue(reply["stopped"]); self.assertTrue(self.live.done)

    def test_look_unknown_stop_and_export(self):
        self.trial.rp.ui = lambda: (_ for _ in ()).throw(zones.Unavailable("no text"))
        self.assertNotIn("text", self.live.handle({"cmd": "look"})["look"])
        self.assertFalse(self.live.handle({"cmd": "fly"})["ok"])
        self.assertFalse(self.live.handle({"cmd": "do", "steps": []})["ok"])
        self.live.handle({"cmd": "do", "steps": [{"op": "button", "button": "a"}]})
        with patch.object(live, "SCENARIOS", Path(self.tmp.name)):
            first = self.live.handle({"cmd": "export", "name": "trial-one"})
            self.assertTrue(first["ok"], first)
            self.assertFalse(self.live.handle({"cmd": "export", "name": "trial-one"})["ok"])
            self.assertTrue(self.live.handle({"cmd": "export", "name": "trial-one", "force": True})["ok"])
            saved = json.loads((Path(self.tmp.name) / "trial-one.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["start"]["seed"], "seed-1")
        self.assertTrue(self.live.handle({"cmd": "stop"})["ok"]); self.assertTrue(self.live.done)

    def test_socket_round_trip_through_the_session_file(self):
        key = secrets.token_bytes(32)
        workshop = SimpleNamespace(data_dir=Path(self.tmp.name))
        with Listener(("127.0.0.1", 0), authkey=key) as listener:
            live.write_session(workshop, {"state": "ready", "address": list(listener.address), "authkey": key.hex()})
            def serve_one():
                with listener.accept() as conn: conn.send(self.live.handle(conn.recv()))
            thread = threading.Thread(target=serve_one); thread.start()
            reply = live.request(workshop, {"cmd": "ping"}, timeout=10)
            thread.join(5)
        self.assertEqual(reply, {"ok": True, "run_id": "run-1"})
        live.write_session(workshop, {"state": "stopped"})
        with self.assertRaises(RuntimeError): live.request(workshop, {"cmd": "ping"})


if __name__ == "__main__": unittest.main()
