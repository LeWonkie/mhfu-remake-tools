"""Bounded declarative village trials, memory UI readback and inspectable evidence.

Run: python atelier_retro.py scenarios/retro-forge.json --seed SEED   (or --start FIXTURE, or the plan's own start)
All game data and transcripts stay in the private run directory. atelier_live.py keeps one Trial open
between requests to explore step by step, then exports the steps that passed as a plan for this runner.
"""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import threading
import time

import atelier_campaign as campaign
import atelier_core as core
import atelier_pilot as ap
import atelier_zones as zones

NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
BUTTONS = {"a", "b", "x", "y", "start", "dpup", "dpdown", "dpleft", "dpright", "leftshoulder", "rightshoulder"}
OPS = {"command", "button", "wait", "capture", "snapshot", "diff", "assert", "talk", "advance", "select", "back", "dump",
       "interact", "area", "reload", "assert_snapshot", "memory_diff", "quest_fixture", "symbols", "watch"}
DIFF_FIELDS = {"money_delta", "item_box", "pouch", "equipment_box", "farm_changed", "kitchen_changed"}
ADDRESS_FIELDS = {"money_address", "item_box_address", "equipment_box_address", "main_task_address"}


def known_names():
    """Names an incoming step may refer to; grows as steps are accepted."""
    return {"snapshots": set(), "diffs": set(), "dumps": set()}


def validate(plan):
    if not isinstance(plan, dict) or not NAME.fullmatch(str(plan.get("name", ""))):
        raise ValueError("a simple scenario name is required")
    seconds = plan.get("seconds", 180)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 10 <= seconds <= 1200:
        raise ValueError("scenario budget must be 10..1200 seconds")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 300:
        raise ValueError("scenario needs 1..300 steps")
    if "start" in plan:
        start = plan["start"]
        if not isinstance(start, dict) or not NAME.fullmatch(str(start.get("seed", ""))) or (
                start.get("from_run") is not None and not NAME.fullmatch(str(start["from_run"]))):
            raise ValueError("start needs a seed name and an optional private run id")
    known = known_names()
    for s in steps: validate_step(s, known, plan.get("assisted", False))
    return plan


def validate_step(s, known, assisted=False):
    """Checks one step against the names declared before it, then records its own."""
    snapshots, diffs, dumps = known["snapshots"], known["diffs"], known["dumps"]
    if not isinstance(s, dict) or s.get("op") not in OPS:
        raise ValueError("unknown step")
    if "timeout" in s and (isinstance(s["timeout"], bool) or not isinstance(s["timeout"], (int, float)) or not math.isfinite(s["timeout"]) or not 0 < s["timeout"] <= 120):
        raise ValueError("step timeout must be 0..120 seconds")
    if s["op"] in {"capture", "snapshot", "dump"} and not NAME.fullmatch(str(s.get("name", ""))):
        raise ValueError("artifact names must be simple identifiers")
    if s["op"] == "button" and (s.get("button") not in BUTTONS or not 1 <= s.get("frames", 6) <= 60
                                or type(s.get("repeat", 1)) is not int or not 1 <= s.get("repeat", 1) <= 60):
        raise ValueError("invalid button, hold duration or repeat count")
    if s["op"] == "command":
        line = s.get("command", "")
        if not isinstance(line, str) or not line.strip() or any(c in line for c in "\n\r\0"):
            raise ValueError("one debug command per step")
        if line.split()[0] in {"dump", "talk", "snapshot", "diff"}:
            raise ValueError("use the structured operation for dumps, talk and snapshots")
    if s["op"] == "advance" and s.get("until", "menu") not in {"menu", "closed"}:
        raise ValueError("advance target must be menu or closed")
    if s["op"] == "select" and not isinstance(s.get("text"), str):
        raise ValueError("select needs a menu label")
    op = s["op"]
    if op == "snapshot": snapshots.add(s["name"])
    if op == "symbols" and not NAME.fullmatch(str(s.get("scope",""))): raise ValueError("symbols needs a scope name")
    if op == "watch" and (s.get("snapshot") not in snapshots or s.get("field") not in ADDRESS_FIELDS or type(s.get("size",4)) is not int or not 1<=s.get("size",4)<=4096):
        raise ValueError("watch needs a known snapshot address and a bounded size")
    if op == "quest_fixture" and (not assisted or type(s.get("quest")) is not int or not 1<=s["quest"]<=65535):
        raise ValueError("quest fixture requires an assisted scenario and a valid quest id")
    if op in {"talk", "interact"}:
        field = "type" if op == "talk" else "index"
        if type(s.get(field)) is not int or not 0 <= s[field] <= (65535 if op=="talk" else 63):
            raise ValueError(f"invalid {field}")
        if op == "interact" and s.get("button","x") not in BUTTONS: raise ValueError("invalid interaction button")
    if op in {"diff", "memory_diff"}:
        sources = snapshots if op == "diff" else dumps
        if s.get("before") not in sources or s.get("after") not in sources:
            raise ValueError("diff references a missing earlier snapshot or dump")
        if op == "diff": diffs.add(s.get("name","last"))
    if op == "assert":
        if (s.get("field") not in DIFF_FIELDS or s.get("diff","last") not in diffs or ("equals" in s) == ("at_least" in s)
                or ("at_least" in s and type(s["at_least"]) is not int)):
            raise ValueError("assert requires a known difference, field and expected value")
    if op == "assert_snapshot" and (s.get("snapshot") not in snapshots or s.get("field") not in
            {"money","item_box","equipment_box","pouch","farm_upgrades_hex","kitchen_skill_ids_hex"} or "equals" not in s):
        raise ValueError("snapshot assertion requires an earlier snapshot and known field")
    if op == "dump":
        try:
            size = int(str(s["size"]),0)
            address = s["address"]
            if isinstance(address,dict):
                if address.get("snapshot") not in snapshots or address.get("field") not in ADDRESS_FIELDS:
                    raise ValueError("unknown snapshot address")
                address=0x08800000
            else: address=int(str(address),0)
        except (KeyError,ValueError,TypeError) as e: raise ValueError("dump needs an address and size") from e
        if not 0x08800000 <= address < address+size <= 0x0A000000: raise ValueError("dump range outside user RAM")
        dumps.add(s["name"])
    if op == "wait":
        if sum(key in s for key in ("seconds","ui","state","address")) != 1:
            raise ValueError("wait needs exactly one delay, UI condition, state condition or memory address")
        if "seconds" in s:
            delay=s["seconds"]
            if type(delay) not in (int,float) or not math.isfinite(delay) or not 0<=delay<=10: raise ValueError("wait seconds must be 0..10")
        if "ui" in s and (not isinstance(s["ui"],dict) or not s["ui"] or
                not set(s["ui"]) <= {"scene","dialogue_open","text_complete","next","contains_text"}):
            raise ValueError("invalid UI condition")
        if "state" in s and (not isinstance(s["state"],dict) or not s["state"] or
                not set(s["state"]) <= {"task","quest","stage","scene","mission","clock","lobby_loaded","lobby_running"}):
            raise ValueError("invalid state condition")
        if "address" in s:
            try: address=int(str(s["address"]),0)
            except (TypeError,ValueError) as e: raise ValueError("invalid wait address") from e
            width=s.get("width",4)
            if width not in (1,2,4) or not 0x08800000<=address<address+width<=0x0A000000 or type(s.get("equals")) is not int:
                raise ValueError("invalid memory wait")
    return s


def delta(a, b):
    if not a.get("available") or not b.get("available"):
        raise ValueError("both snapshots must be available")
    def items(s, key):
        c = Counter()
        for entry in s[key]:
            if entry["id"]: c[str(entry["id"])] += entry["count"]
        return c
    def equipment(s):
        return Counter(f'{e["type"]}:{e["id"]}' for e in s["equipment_box"])
    def subtract(x, y):
        return {k: y[k] - x[k] for k in sorted(x.keys() | y.keys()) if x[k] != y[k]}
    return {"money_delta": b["money"] - a["money"],
            "item_box": subtract(items(a, "item_box"), items(b, "item_box")),
            "pouch": subtract(items(a, "pouch"), items(b, "pouch")),
            "equipment_box": subtract(equipment(a), equipment(b)),
            "farm_changed": a.get("farm_upgrades_hex") != b.get("farm_upgrades_hex"),
            "kitchen_changed": a.get("kitchen_skill_ids_hex") != b.get("kitchen_skill_ids_hex")}


def assertion(spec, difference):
    field = spec["field"]
    if field not in DIFF_FIELDS: raise ValueError("unknown difference field")
    actual = difference.get(field)
    if "key" in spec:
        if not isinstance(actual, dict): raise ValueError("assertion key requires a mapping")
        actual = sum(actual.values()) if spec["key"] == "*" else actual.get(str(spec["key"]), 0)
    if ("equals" in spec) == ("at_least" in spec): raise ValueError("assertion requires equals or at_least")
    if "at_least" in spec:  # random loot: a minimum gain, with key "*" summing every item
        if type(spec["at_least"]) is not int or type(actual) is not int: raise ValueError("at_least compares integers")
        return {"field": field, "key": spec.get("key"), "at_least": spec["at_least"], "actual": actual,
                "passed": actual >= spec["at_least"]}
    return {"field": field, "key": spec.get("key"), "expected": spec["equals"], "actual": actual,
            "passed": actual == spec["equals"]}


def yakumo_pids():
    if os.name != "nt":
        raise RuntimeError("process guard currently requires Windows")
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "ConvertTo-Json -Compress -InputObject @([System.Diagnostics.Process]::GetProcessesByName('Yakumo') | ForEach-Object { $_.Id })"],
                       capture_output=True, text=True, creationflags=core.NO_WINDOW, timeout=10)
    if p.returncode: raise RuntimeError("could not verify running Yakumo processes")
    return [int(pid) for pid in json.loads(p.stdout)]


class RetroPilot:
    def __init__(self, pilot, deadline):
        self.pilot, self.session, self.deadline = pilot, pilot.session, deadline
        self.budget = max(0, deadline - time.monotonic())
        self.timings, self.transcript = [], []
        self.last_ui = None

    def check(self):
        self.pilot.check()
        if time.monotonic() >= self.deadline: raise ap.WaitTimeout("trial budget", 0)

    def command(self, line, timeout=6):
        self.check()
        if any(c in line for c in "\n\r\0"): raise ValueError("one command per request")
        self.session.new_lines()
        # A read-only fence makes multi-line answers complete and ordered.
        began = time.monotonic()
        self.session.w.debug_commands([line, "peek 0x089C7508 4"])
        end = min(self.deadline, began + timeout)
        answers = []
        command_seen = fence_seen = False
        while time.monotonic() < end:
            self.check()
            for row in self.session.new_lines():
                if row.startswith("[debug] > "):
                    echo=row[len("[debug] > "):]
                    if not command_seen and echo==line: command_seen=True
                    elif command_seen and echo=="peek 0x089C7508 4": fence_seen=True
                    continue
                if fence_seen and re.search(r"(?:0x)?089C7508:", row):
                    self.timings.append({"command": line.split()[0], "seconds": time.monotonic() - began})
                    return answers
                if command_seen and not fence_seen and row.startswith("[debug] "): answers.append(row[8:])
            self.pilot.pause(.01)
        raise ap.WaitTimeout(line, timeout)

    def json_command(self, command, prefix):
        for row in self.command(command):
            if row.startswith(prefix + " "):
                return json.loads(row[len(prefix) + 1:])
        raise zones.Unavailable(f"no structured reply for {command}")

    def find(self, width, value):
        if width not in (8, 16, 32) or not 0 <= value < 1 << width:
            raise ValueError("search width/value out of range")
        return self.command(f"find{width} {value}", timeout=15)

    def next(self, value):
        if not isinstance(value, int) or not 0 <= value <= 0xffffffff:
            raise ValueError("search value out of range")
        return self.command(f"next {value}")

    def changed(self, changed=True):
        return self.command("changed" if changed else "unchanged")

    def ui(self):
        value = self.json_command("ui", "ui")
        if not value.get("available"): raise zones.Unavailable("game text is not available")
        if value != self.last_ui:
            record = {"elapsed": round(time.monotonic() - (self.deadline - self.budget), 3), **value}
            self.transcript.append(record)
            with (self.session.dir / "transcript.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.last_ui = value
        return value

    def button(self, name, frames=6):
        self.check()
        if name not in BUTTONS or not 1 <= frames <= 60: raise ValueError("invalid button")
        self.session.inputs([f"0:pad {name} {frames}"])
        self.pilot.pause(.25)
        self.check()

    def advance_dialogue_until(self, target="menu", timeout=40):
        if target not in {"menu", "closed"}: raise ValueError("invalid dialogue target")
        end = min(self.deadline, time.monotonic() + timeout)
        for _ in range(100):
            self.check()
            state = self.ui()
            if target == "menu" and state["menu"]: return state
            if state["scene"] == 0:
                if target == "closed": return state
                raise zones.Unavailable("dialogue closed before reaching a menu")
            if time.monotonic() >= end: break
            if state["menu"]:
                self.button("a")
            elif state.get("dialogue_open"):
                self.button("b")
            else:
                self.pilot.pause(.1)
        raise ap.WaitTimeout(f"dialogue until {target}", timeout)

    def select(self, text, timeout=12):
        end = min(self.deadline, time.monotonic() + timeout)
        for _ in range(64):
            self.check()
            state = self.ui()
            m = state.get("menu")
            if not m or not m.get("entries_complete"):
                if time.monotonic() >= end: break
                self.pilot.pause(.1)
                continue
            entries = [s.strip().casefold() for s in m["entries"]]
            if entries.count(text.strip().casefold()) != 1:
                raise ValueError(f"menu label absent or ambiguous: {text!r}; entries={m['entries']}")
            index = entries.index(text.strip().casefold())
            if m["cursor"] == index:
                self.button("b")
                return state
            if time.monotonic() >= end: break
            forward,backward = ("dpright","dpleft") if m.get("axis")=="horizontal" else ("dpdown","dpup")
            self.button(forward if m["cursor"] < index else backward)
        raise ap.WaitTimeout(f"select {text}", timeout)

    def back_to_village(self, timeout=20):
        end = min(self.deadline, time.monotonic() + timeout)
        for _ in range(64):
            self.check()
            if time.monotonic()>=end:break
            try:state = self.ui()
            except zones.Unavailable:
                self.pilot.pause(.05)
                continue
            entries=(state.get("menu") or {}).get("entries",[])
            if "Finish Training" in entries:
                self.select("Finish Training",min(12,end-time.monotonic()));continue
            if "Exit School" in entries:
                self.select("Exit School",min(12,end-time.monotonic()));continue
            if state.get("menu") and any("Return to Pokke Village?" in row["text"] for row in state["runs"]):
                self.select("Yes",min(12,end-time.monotonic()));continue
            if state["scene"] == 0:
                snapshot=self.pilot.snapshot()
                if self.pilot.village_ready(snapshot):
                    if snapshot["stage"]==139:return state
                    if snapshot["stage"]==66:
                        self.interact(0,"x",max(.1,end-time.monotonic()))
                        self.pilot.until("house exit",lambda:self.pilot.snapshot()["stage"]!=66,max(.1,end-time.monotonic()))
                        continue
                    raise zones.Unavailable(f"no verified return route from area {snapshot['stage']}")
            if time.monotonic() >= end: break
            self.button("a" if state["menu"] or not state["dialogue_open"] else "b")
        raise ap.WaitTimeout("back to village", timeout)

    def interact(self, index, button="x", timeout=20):
        import probe_village_npcs as probe
        import deliver
        self.check()
        area = zones.current(self.session)
        entries = probe.interactions(self.session, area)
        if not isinstance(index, int) or not 0 <= index < len(entries):
            raise ValueError("interaction index not present in this area")
        entry = entries[index]
        x,y,z…787 tokens truncated…uests."""

    def __init__(self, workshop, seed, name, budget, assisted=False, cancel=None, speed=8,
                 watch_writes=False, from_run=None, expected_source=None):
        if speed not in ap.SPEEDS: raise ValueError("speed must be 1..8")
        if not NAME.fullmatch(str(name)): raise ValueError("a simple trial name is required")
        self.workshop, self.seed, self.budget, self.speed = workshop, seed, budget, speed
        self.assisted, self.watch_writes, self.from_run = assisted, watch_writes, from_run
        self.expected_source = expected_source
        self.cancel = cancel or threading.Event()
        self.known = known_names()
        self.snapshots, self.differences, self.dumps = {}, {}, {}
        self.session = self.pilot = self.rp = self.trial_dir = self.lock = self.timer = None
        self.closed = False
        self.result = {"name": name, "state": "running", "steps": [], "assertions": [],
                       "assisted": assisted, "speed": speed, "starting_private_run": from_run, "seed": seed}

    def guard(self):
        """Refuses to start unless the originals exist, no Yakumo runs and no other runner holds the lock."""
        workshop = self.workshop
        self.protected = [workshop.project / "game-mhfu/ms0", workshop.data_dir / "seeds" / self.seed / "ms0"]
        workshop._seed(self.seed)
        self.private_source = workshop._run_dir(self.from_run)/"game/ms0" if self.from_run else None
        if self.private_source: self.protected.append(self.private_source)
        if not all(p.is_dir() for p in self.protected): raise ValueError("protected save directories missing")
        if yakumo_pids(): raise RuntimeError("another Yakumo is running")
        # The OS file lock serializes this runner across terminals; it is released on process exit.
        import msvcrt
        lock = (workshop.data_dir / "retro.lock").open("a+b")
        lock.seek(0); lock.write(b"0"); lock.flush(); lock.seek(0)
        try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock.close()
            raise RuntimeError("another retro trial or live session holds the lock")
        self.lock = lock
        self.before = [core._tree_digest(p) for p in self.protected]
        if self.expected_source and self.before[-1] != self.expected_source:
            self._release()
            raise ValueError("fixture save copy changed since it was recorded")
        self.starting_save_digest = self.before[-1] if self.private_source else self.before[1]
        self.result["protected_before"] = self.before

    def launch(self):
        self.began = time.monotonic()
        self.timer = threading.Timer(self.budget, self.cancel.set); self.timer.daemon = True; self.timer.start()
        variant_name = "normal"
        if self.watch_writes:
            variant_name = "retro_write_watch"
            core.VARIANTS[variant_name] = {"label":"Development write-watch session", "env":{"PSPRECOMP_WATCH_WRITE":"0x00000000"}}
        self.variant = core.register_rtc_variant(variant_name,1609459200123456)
        if yakumo_pids(): raise RuntimeError("another Yakumo started before launch")
        name = self.result["name"]
        self.scenario = {"name": name, "label": name, "fast_boot": True, "debug": True,
                         "script": campaign.BOOT, "screenshot_every": 0,
                         "settings": {"video.fast_forward": "toggle", "video.fast_forward_speed": str(self.speed), "video.fast_loading": "1"}}
        if self.private_source: self.scenario["private_ms0"] = str(self.private_source)
        self._start_session(self.scenario, min(1800, int(self.budget + 15)))
        self.trial_dir = self.session.dir
        self.result["run_id"] = self.session.run_id
        self.result["run_ids"] = [self.session.run_id]
        self.pilot.wait_village(min(60, self.budget))
        self.result["boot_to_village_seconds"] = time.monotonic() - self.began
        if self.speed > 1: self.pilot.ensure_fast_forward()

    def _start_session(self, scenario, seconds):
        transcript, timings = (self.rp.transcript, self.rp.timings) if self.rp else ([], [])
        self.session = campaign.Session(self.workshop, self.seed, seconds, variant=self.variant, scenario=scenario)
        self.session.cancel = self.cancel
        self.pilot = ap.Pilot(self.session, cancel=self.cancel)
        self.rp = RetroPilot(self.pilot, self.began + self.budget); self.rp.budget = self.budget
        self.rp.transcript, self.rp.timings = transcript, timings

    def step(self, step):
        """Validates and runs one step; names it declares become usable only once it passed."""
        known = {k: set(v) for k, v in self.known.items()}
        validate_step(step, known, self.assisted)
        self.rp.check()
        entry = {"index": len(self.result["steps"]), "step": step, "state": "running"}
        self.result["steps"].append(entry)
        start = time.monotonic()
        try:
            getattr(self, "op_" + step["op"])(step, entry, step.get("timeout", 30))
        except BaseException as error:
            entry.update(state="failed", error=str(error) or type(error).__name__, seconds=round(time.monotonic() - start, 4))
            raise
        entry.update(state="passed", seconds=round(time.monotonic() - start, 4))
        self.known = known
        core._write_json(self.trial_dir / "retro-result.json", self.result)
        return entry

    def verdict(self):
        """A plan that ran without asserting anything explored the game; it validated nothing."""
        return "passed" if self.result["assertions"] else "explored"

    def capture_failure(self, name="failure"):
        if self.rp and self.session.alive():
            try: self.pilot.confirm_shots([self.pilot.shoot(name)], timeout=3)
            except Exception: pass

    def close(self):
        if self.closed: return self.result
        self.closed = True
        result = self.result
        if self.timer: self.timer.cancel()
        try: self.workshop.stop()
        finally:
            try: self._record(result)
            finally: self._release()  # even when the report cannot be written
        return result

    def _record(self, result):
        result["protected_after"] = [core._tree_digest(p) for p in self.protected]
        result["protected_unchanged"] = self.before == result["protected_after"]
        result["remaining_yakumo_pids"] = yakumo_pids()
        if not result["protected_unchanged"] or result["remaining_yakumo_pids"]: result["state"] = "failed"
        result["seconds"] = time.monotonic() - self.began if hasattr(self, "began") else 0
        if self.rp:
            times = [t["seconds"] for t in self.rp.timings]
            result["command_timings"] = self.rp.timings
            result["rtt_median_seconds"] = statistics.median(times) if times else None
        if self.session:
            if self.session.dir != self.trial_dir:
                import shutil
                for shot in (self.session.dir/"frames").glob("*.bmp"):
                    shutil.copy2(shot,self.trial_dir/"frames"/(self.session.run_id+"-"+shot.name))
            core._write_json(self.trial_dir / "retro-result.json", result)
            result["directory"] = str(self.trial_dir)
            report(self.trial_dir, result, self.rp.transcript if self.rp else [])

    def _release(self):
        import msvcrt
        self.lock.seek(0); msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1); self.lock.close()

    # ----- operations: one method per step "op" ---------------------------
    def op_command(self, step, entry, timeout):
        entry["answer"] = self.rp.command(step["command"])
        if "expect" in step and not any(step["expect"] in a for a in entry["answer"]): raise AssertionError("command expectation failed")

    def op_symbols(self, step, entry, timeout):
        sources = list((self.workshop.project/"references/mhp2g-decomp/config").rglob(step["scope"]+".symbol_addrs.txt"))
        if len(sources)!=1: raise ValueError("symbol scope is missing or ambiguous")
        entry["answer"] = self.rp.command(f'symbols "{sources[0]}"')
        if not any(s.startswith("symbols loaded") for s in entry["answer"]): raise AssertionError(str(entry["answer"]))

    def op_watch(self, step, entry, timeout):
        address=self.snapshots[step["snapshot"]][step["field"]]
        entry["answer"] = self.rp.command(f'watch {address} {step.get("size",4)}')
        if "watch updated" not in entry["answer"]: raise AssertionError(str(entry["answer"]))

    def op_button(self, step, entry, timeout):
        for _ in range(step.get("repeat", 1)): self.rp.button(step["button"], step.get("frames", 6))

    def op_capture(self, step, entry, timeout):
        entry["files"] = self.pilot.confirm_shots([self.pilot.shoot(step["name"])])
        self.rp.ui()

    def op_snapshot(self, step, entry, timeout):
        self.snapshots[step["name"]] = self.rp.json_command("snapshot " + step["name"], "snapshot")
        path = self.session.dir / (step["name"] + ".snapshot.json")
        core._write_json(path, self.snapshots[step["name"]])
        entry["artifact"] = str(path)

    def op_diff(self, step, entry, timeout):
        key = step.get("name", "last")
        self.differences[key] = delta(self.snapshots[step["before"]], self.snapshots[step["after"]])
        entry["difference"] = self.differences[key]

    def op_assert(self, step, entry, timeout):
        verdict = assertion(step, self.differences[step.get("diff", "last")]); self.result["assertions"].append(verdict)
        if not verdict["passed"]: raise AssertionError(str(verdict))

    def op_assert_snapshot(self, step, entry, timeout):
        actual = self.snapshots[step["snapshot"]][step["field"]]
        verdict = {"snapshot":step["snapshot"], "field":step["field"], "actual":actual,
                   "expected":step["equals"], "passed":actual == step["equals"]}
        self.result["assertions"].append(verdict)
        if not verdict["passed"]: raise AssertionError(str(verdict))

    def op_talk(self, step, entry, timeout):
        rp = self.rp
        self.pilot.wait_village(timeout)
        def npc_loaded():
            npcs=rp.json_command("npc","npc")
            found=[n for n in npcs if n["type"]==step["type"]]
            if len(found)>1:raise ValueError("NPC type is ambiguous")
            return found[0] if found else None
        found=self.pilot.until("NPC type loaded",npc_loaded,timeout)
        answer = rp.command(f'talk {found["slot"]}')
        if "talk queued" not in answer: raise zones.Unavailable(str(answer))
        self.pilot.until("dialogue opened", lambda: rp.ui()["scene"] != 0, timeout)

    def op_advance(self, step, entry, timeout): self.rp.advance_dialogue_until(step.get("until", "menu"), timeout)

    def op_select(self, step, entry, timeout): self.rp.select(step["text"], timeout)

    def op_back(self, step, entry, timeout): self.rp.back_to_village(timeout)

    def op_quest_fixture(self, step, entry, timeout):
        from dataclasses import asdict
        q=campaign.QuestResult(step["quest"],run_id=self.session.run_id)
        ap.run_quest(self.pilot,step["quest"],q,look_seconds=.5,loadout="none",cheats=())
        entry["quest_result"]=asdict(q)
        entry["quest_result"].update(outcome=getattr(q,"outcome","unknown"),mission_state=getattr(q,"mission_state",None),
            timer_shortened=getattr(q,"timer_shortened",False))
        if not getattr(q,"village_ready",False): raise AssertionError("quest fixture did not return to an idle village")

    def op_area(self, step, entry, timeout):
        entry["area"] = zones.current(self.session)
        import probe_village_npcs as probe
        entry["interactions"] = probe.interactions(self.session, entry["area"])
        core._write_json(self.trial_dir / f"area-{entry['index']}.json",entry)

    def op_interact(self, step, entry, timeout): entry["interaction"] = self.rp.interact(step["index"],step.get("button","x"),timeout)

    def op_reload(self, step, entry, timeout):
        source = self.session.dir / "game/ms0"
        self.workshop.stop()
        entry["saved_copy_sha256"] = core._tree_digest(source)
        entry["saved_copy_changed_from_start"] = entry["saved_copy_sha256"] != self.starting_save_digest
        if step.get("require_save_changed",True) and not entry["saved_copy_changed_from_start"]:
            raise AssertionError("private save did not change before reload")
        remaining = min(1800, max(1,int(self.rp.deadline - time.monotonic())))
        self._start_session({**self.scenario, "private_ms0":str(source)}, remaining)
        self.result["run_ids"].append(self.session.run_id)
        self.pilot.wait_village(min(60,remaining))
        if self.speed > 1:self.pilot.ensure_fast_forward()

    def op_dump(self, step, entry, timeout):
        reference=step["address"]
        address = self.snapshots[reference["snapshot"]][reference["field"]] if isinstance(reference,dict) else int(str(reference),0)
        size = int(str(step["size"]), 0)
        if not 0x08800000 <= address < address + size <= 0x0A000000: raise ValueError("dump range outside user RAM")
        path = self.session.dir / (step["name"] + ".bin")
        answer = self.rp.command(f'dump "{path}" {address} {size}', timeout)
        if not path.is_file() or path.stat().st_size != size: raise IOError(str(answer))
        self.dumps[step["name"]] = {"path":path, "base":address, "size":size}
        entry["artifact"] = str(path)

    def op_memory_diff(self, step, entry, timeout):
        import retro_memory
        a,b = self.dumps[step["before"]],self.dumps[step["after"]]
        if (a["base"],a["size"]) != (b["base"],b["size"]): raise ValueError("dump ranges differ")
        symbols = retro_memory.Symbols.load(self.workshop.project/"references/mhp2g-decomp/config")
        regions = [{"name":obj["name"],"address":obj["address"],"size":64}
                   for s in self.snapshots.values() for obj in s.get("systems",[]) if obj["address"]]
        for s in self.snapshots.values():
            for field,size in (("money",4),("item_box",4000),("equipment_box",12000)):
                if s.get(field+"_address"): regions.append({"name":"UserData."+field,"address":s[field+"_address"],"size":size})
        observed = retro_memory.memory_diff(a["path"].read_bytes(),b["path"].read_bytes(),a["base"],symbols,step.get("scope","eboot"),regions)
        path = self.trial_dir/f"memory-diff-{entry['index']}.json"
        core._write_json(path,observed)
        entry.update(changed_bytes=observed["changed_bytes"],artifact=path.name)

    def op_wait(self, step, entry, timeout):
        rp, pilot = self.rp, self.pilot
        if "seconds" in step:
            delay = step["seconds"]
            if isinstance(delay,bool) or not isinstance(delay,(int,float)) or not math.isfinite(delay) or not 0<=delay<=10:
                raise ValueError("wait seconds must be 0..10")
            rp.check();pilot.pause(min(delay,max(0,rp.deadline-time.monotonic())));rp.check()
        elif "ui" in step:
            def ui_matches():
                observed = rp.ui()
                return all((any(str(v) in row["text"] for row in observed["runs"]) if k=="contains_text" else observed.get(k)==v)
                           for k,v in step["ui"].items())
            pilot.until("UI condition", ui_matches, timeout)
        elif "state" in step:
            def state_matches():
                observed = pilot.snapshot()
                return all(observed.get(k)==v for k,v in step["state"].items())
            pilot.until("game state condition",state_matches,timeout)
        else:
            address, expected = int(str(step["address"]), 0), step["equals"]
            width = step.get("width", 4)
            if width not in (1, 2, 4): raise ValueError("memory width must be 1, 2 or 4")
            pilot.until("memory condition", lambda: int.from_bytes(zones.read(self.session, address, width), "little") == expected, timeout)


def run(workshop, plan, seed, cancel=None, speed=8, watch_writes=False, from_run=None, expected_source=None):
    validate(plan)
    trial = Trial(workshop, seed, plan["name"], plan.get("seconds", 180), plan.get("assisted", False), cancel,
                  speed, watch_writes, from_run, expected_source)
    trial.guard()
    try:
        trial.launch()
        for step in plan["steps"]: trial.step(step)
        trial.result["state"] = trial.verdict()
    except (Exception, KeyboardInterrupt) as error:
        trial.result.update(state="cancelled" if trial.cancel.is_set() or isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
        trial.capture_failure()
    finally:
        trial.close()
    return trial.result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path); parser.add_argument("--seed", help="defaults to the fixture or the plan's start")
    parser.add_argument("--start", help="named fixture from fixtures.json (seed and verified private save)")
    parser.add_argument("--speed", type=int, default=8, choices=ap.SPEEDS)
    parser.add_argument("--watch-writes", action="store_true", help="arm the development store path so watch ADDRESS LENGTH can be used")
    parser.add_argument("--from-run", help="start from a stopped private run's save copy, preserving that source too")
    args = parser.parse_args()
    plan = json.loads(args.scenario.read_text(encoding="utf-8"))
    seed, from_run, expected = resolve_start(args.seed, args.from_run, args.start, plan)
    result = run(core.Workshop(Path(__file__).resolve().parents[2]), plan, seed, speed=args.speed,
                 watch_writes=args.watch_writes, from_run=from_run, expected_source=expected)
    print(json.dumps({k: result.get(k) for k in ("state", "error", "directory", "seconds", "boot_to_village_seconds", "rtt_median_seconds", "protected_unchanged")}, indent=2))
    return 0 if result["state"] in ("passed", "explored") else 1


if __name__ == "__main__": raise SystemExit(main())
