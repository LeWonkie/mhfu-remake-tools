"""Live retro session: one guarded game kept open, steps sent one call at a time.

    python atelier_live.py start --start apres-intro      boot once (~9 s), then wait for steps
    python atelier_live.py do talk type=6                 one step, answered with what the screen shows
    python atelier_live.py do '[{"op":"advance","until":"menu"},{"op":"select","text":"Yes"}]'
    python atelier_live.py look | steps | status
    python atelier_live.py export retro-farm              passed steps -> scenarios/retro-farm.json
    python atelier_live.py stop                           stop the game, check the originals, write the report

The session is an ordinary atelier_retro Trial: same validation, same guards (originals hashed
before and after, one Yakumo, lock shared with batch trials), same evidence in its run directory.
A failed step leaves the game running so the next attempt costs one step, not one boot. Only
steps that passed are exported; replaying the export in batch is what validates a mechanic.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from multiprocessing.connection import Client, Listener
from multiprocessing import AuthenticationError
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

import atelier_core as core
import atelier_retro as retro
import atelier_zones as zones

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
SCENARIOS = HERE / "scenarios"
MAX_MINUTES = 29          # Workshop.MAX_SECONDS is 1800 and the trial adds a 15 s launch margin
LOOK_TEXT_ROWS = 24


def session_file(workshop):
    return workshop.data_dir / "live" / "session.json"


def read_session(workshop):
    path = session_file(workshop)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def write_session(workshop, state):
    path = session_file(workshop)
    path.parent.mkdir(parents=True, exist_ok=True)
    core._write_json(path, state)


# ----- step shorthand -------------------------------------------------------
def parse_shorthand(words):
    if not words: raise ValueError("a step is required")
    step = {"op": words[0]}
    for word in words[1:]:
        key, sep, raw = word.partition("=")
        if not sep or not key: raise ValueError(f"expected key=value, got {word!r}")
        try: value = json.loads(raw)
        except json.JSONDecodeError: value = raw
        *parents, last = key.split(".")
        target = step
        for name in parents:  # `state.stage=46` is {"state": {"stage": 46}}
            target = target.setdefault(name, {})
            if not isinstance(target, dict): raise ValueError(f"{key!r} conflicts with a plain value")
        target[last] = value
    return step


def parse_steps(words, stdin=None):
    """`talk type=6`, steps chained with `+`, a JSON object or list, `@file.json`, or `-` for standard input.

    Windows PowerShell 5.1 strips the double quotes of a JSON argument, so JSON is safest from a file or a pipe."""
    if len(words) == 1 and (words[0] == "-" or words[0].startswith("@")):
        text = (stdin or sys.stdin).read() if words[0] == "-" else Path(words[0][1:]).read_text(encoding="utf-8-sig")
        text = text.lstrip("﻿").strip()
        words = text.split()
    else:
        text = " ".join(words).strip()
    if text.startswith(("{", "[")):
        value = json.loads(text)
        steps = value if isinstance(value, list) else [value]
    else:
        groups, current = [], []
        for word in words:
            if word == "+": groups.append(current); current = []
            else: current.append(word)
        groups.append(current)
        steps = [parse_shorthand(group) for group in groups]
    if not steps or not all(isinstance(s, dict) for s in steps): raise ValueError("steps must be JSON objects")
    return steps


def compress(steps):
    """Consecutive identical button presses become one step with `repeat`."""
    out = []
    for step in steps:
        last = out[-1] if out else None
        if (last and step.get("op") == "button" and last.get("op") == "button"
                and {k: v for k, v in step.items() if k != "repeat"} == {k: v for k, v in last.items() if k != "repeat"}
                and last.get("repeat", 1) + step.get("repeat", 1) <= 60):
            last["repeat"] = last.get("repeat", 1) + step.get("repeat", 1)
        else:
            out.append(dict(step))
    return out


def export_plan(result, name, first=0, last=None, description=None):
    """A replayable plan from the steps that passed, keeping the session's starting point."""
    rows = [e for e in result["steps"] if e["state"] == "passed" and first <= e["index"] <= (math.inf if last is None else last)]
    if not rows: raise ValueError("no passed step in that range")
    spent = sum(e.get("seconds", 0) for e in rows)
    plan = {"name": name,
            "seconds": int(min(1200, max(60, math.ceil(spent * 2 + 45)))),
            "assisted": result.get("assisted", False),
            "description": description or f"Recorded from live session {result.get('run_id')} ({datetime.now():%Y-%m-%d}).",
            "start": {"seed": result["seed"], "from_run": result.get("starting_private_run")},
            "steps": compress([e["step"] for e in rows])}
    return retro.validate(plan)


# ----- what the screen shows ------------------------------------------------
def look(trial):
    snap = trial.pilot.snapshot()
    view = {k: snap.get(k) for k in ("task", "stage", "scene")}
    view["village_ready"] = trial.pilot.village_ready(snap)
    try: ui = trial.rp.ui()
    except zones.Unavailable: ui = None
    if ui:
        view.update(dialogue_open=ui.get("dialogue_open"), text_complete=ui.get("text_complete"), next=ui.get("next"))
        menu = ui.get("menu")
        view["menu"] = {k: menu[k] for k in ("entries", "cursor", "axis", "entries_complete") if k in menu} if menu else None
        view["text"] = [row["text"] for row in ui.get("runs", [])][:LOOK_TEXT_ROWS]
    return view


class Live:
    """Answers one request at a time for an open Trial."""

    def __init__(self, trial, idle_seconds):
        self.trial, self.idle_seconds = trial, idle_seconds
        self.done = False
        self.reason = None
        self.busy = False
        self.last = time.monotonic()

    def handle(self, message):
        self.busy, self.last = True, time.monotonic()
        try:
            command = message.get("cmd") if isinstance(message, dict) else None
            if command == "ping": return {"ok": True, "run_id": self.trial.result.get("run_id")}
            if command == "look": return {"ok": True, "look": look(self.trial)}
            if command == "steps":
                return {"ok": True, "steps": [{k: e.get(k) for k in ("index", "state", "seconds", "error", "step")}
                                              for e in self.trial.result["steps"]]}
            if command == "do": return self.do(message.get("steps"), message.get("look", True))
            if command == "export": return self.export(message)
            if command == "stop":
                self.done, self.reason = True, message.get("reason", "requested")
                return {"ok": True}
            return {"ok": False, "error": f"unknown request {command!r}"}
        except Exception as error:
            return {"ok": False, "error": str(error) or type(error).__name__}
        finally:
            self.busy, self.last = False, time.monotonic()

    def do(self, steps, want_look=True):
        if not isinstance(steps, list) or not steps: return {"ok": False, "error": "steps must be a non-empty list"}
        entries, ok = [], True
        for step in steps:
            count = len(self.trial.result["steps"])
            try:
                entries.append(self.trial.step(step))
            except Exception as error:
                ok = False
                if len(self.trial.result["steps"]) > count:
                    entry = self.trial.result["steps"][-1]
                    self.trial.capture_failure(f"failure-{entry['index']}")
                else:  # refused before running: nothing was sent to the game
                    entry = {"step": step, "state": "rejected", "error": str(error) or type(error).__name__}
                entries.append(entry)
                break
        reply = {"ok": ok, "entries": entries}
        if self.trial.session is None or not self.trial.session.alive():
            self.done, self.reason = True, "game stopped"
            reply["stopped"] = True
        elif want_look:
            try: reply["look"] = look(self.trial)
            except Exception as error: reply["look_error"] = str(error)
        return reply

    def export(self, message):
        name = str(message.get("name", ""))
        plan = export_plan(self.trial.result, name, int(message.get("first", 0)),
                           None if message.get("last") is None else int(message["last"]), message.get("description"))
        path = SCENARIOS / f"{name}.json"
        if path.exists() and not message.get("force"): return {"ok": False, "error": f"{path.name} exists; pass --force"}
        path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"ok": True, "path": str(path), "steps": len(plan["steps"]), "seconds": plan["seconds"]}


def job_guard():
    """Puts this server in a kill-on-close job so the game it starts cannot outlive it."""
    if os.name != "nt": return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return False
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()): return False
        job_guard.handle = job  # the handle must stay open for the life of this process
        return True
    except (OSError, AttributeError):
        return False


# ----- server ---------------------------------------------------------------
def serve(args):
    workshop = core.Workshop(PROJECT)
    state = {"state": "starting", "pid": os.getpid(), "started_at": core._now(), "name": args.name,
             "start": args.start, "idle_minutes": args.idle, "minutes": args.minutes}
    write_session(workshop, state)
    state["job_guard"] = job_guard()
    trial = None
    try:
        seed, from_run, expected = retro.resolve_start(args.seed, args.from_run, args.start)
        state.update(seed=seed, from_run=from_run)
        trial = retro.Trial(workshop, seed, args.name, args.minutes * 60, args.assisted, None, args.speed,
                            args.watch_writes, from_run, expected)
        trial.guard()
    except Exception as error:
        state.update(state="failed", error=str(error), ended_at=core._now())
        write_session(workshop, state)
        return 1
    key = secrets.token_bytes(32)
    try:
        trial.launch()
        trial.result["state"] = "live"
        listener = Listener(("127.0.0.1", 0), authkey=key)
        live = Live(trial, args.idle * 60)
        state.update(state="ready", address=list(listener.address), authkey=key.hex(), run_id=trial.result["run_id"],
                     directory=str(trial.trial_dir), boot_to_village_seconds=round(trial.result["boot_to_village_seconds"], 3))
        write_session(workshop, state)

        def watchdog():
            while not live.done:
                time.sleep(1)
                idle = not live.busy and time.monotonic() - live.last > live.idle_seconds
                if idle or trial.cancel.is_set():
                    try:
                        with Client(listener.address, authkey=key) as conn:
                            conn.send({"cmd": "stop", "reason": "idle" if idle else "budget"}); conn.recv()
                    except OSError:
                        live.done = True
                    return
        threading.Thread(target=watchdog, daemon=True, name="live-watchdog").start()
        with listener:
            while not live.done:
                try: conn = listener.accept()
                except (AuthenticationError, OSError): continue
                with conn:
                    try: conn.send(live.handle(conn.recv()))
                    except (EOFError, OSError): pass
        state["stop_reason"] = live.reason
    except Exception as error:
        state["error"] = str(error)
        trial.result.update(state="failed", error=str(error))
    finally:
        if trial.result["state"] == "live": trial.result["state"] = "stopped"
        result = trial.close()
        steps = result["steps"]
        state.update(state="stopped", ended_at=core._now(), result_state=result["state"],
                     protected_unchanged=result.get("protected_unchanged"), remaining_yakumo_pids=result.get("remaining_yakumo_pids"),
                     steps_passed=sum(e["state"] == "passed" for e in steps), steps_failed=sum(e["state"] == "failed" for e in steps),
                     seconds=round(result.get("seconds", 0), 1), directory=result.get("directory"))
        state.pop("authkey", None); state.pop("address", None)
        write_session(workshop, state)
    return 0


# ----- client ---------------------------------------------------------------
def request(workshop, message, timeout=900):
    state = read_session(workshop)
    if not state or state.get("state") != "ready": raise RuntimeError("no live session; run: atelier_live.py start ...")
    try: conn = Client(tuple(state["address"]), authkey=bytes.fromhex(state["authkey"]))
    except OSError as error: raise RuntimeError(f"live session unreachable (stale {session_file(workshop)}?): {error}")
    with conn:
        conn.send(message)
        end = time.monotonic() + timeout
        while not conn.poll(0.2):
            if time.monotonic() > end: raise TimeoutError("no answer from the live session")
        return conn.recv()


def start(args):
    workshop = core.Workshop(PROJECT)
    state = read_session(workshop)
    if state and state.get("state") in ("starting", "ready"):
        try:
            if state.get("state") == "ready" and request(workshop, {"cmd": "ping"}, 5).get("ok"):
                raise SystemExit(f"a live session is already open: {state.get('run_id')}")
        except RuntimeError: pass
        if state.get("state") == "starting" and time.time() - session_file(workshop).stat().st_mtime < 120:
            raise SystemExit("a live session is starting")
    command = [sys.executable, str(Path(__file__).resolve()), "serve", "--name", args.name, "--speed", str(args.speed),
               "--minutes", str(args.minutes), "--idle", str(args.idle)]
    for flag, value in (("--seed", args.seed), ("--from-run", args.from_run), ("--start", args.start)):
        if value: command += [flag, value]
    if args.watch_writes: command.append("--watch-writes")
    if args.assisted: command.append("--assisted")
    log = (workshop.data_dir / "live").resolve()
    log.mkdir(parents=True, exist_ok=True)
    session_file(workshop).unlink(missing_ok=True)
    flags = 0x00000008 | 0x00000200 | core.NO_WINDOW  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with (log / "server.log").open("ab") as out:
        try:
            subprocess.Popen(command, cwd=HERE, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                             creationflags=flags | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB: outlive this shell
        except OSError:
            subprocess.Popen(command, cwd=HERE, stdin=subprocess.DEVNULL, stdout=out, stderr=out, creationflags=flags)
    end = time.monotonic() + 120
    while time.monotonic() < end:
        state = read_session(workshop)
        if state and state.get("state") in ("ready", "failed", "stopped"): break
        time.sleep(0.25)
    else:
        raise SystemExit("live session did not become ready within 120 s; see " + str(log / "server.log"))
    if state["state"] != "ready":
        print(json.dumps(state, indent=2, ensure_ascii=False)); return 1
    shown = {k: state.get(k) for k in ("run_id", "directory", "seed", "from_run", "boot_to_village_seconds", "minutes", "idle_minutes", "job_guard")}
    shown["look"] = request(workshop, {"cmd": "look"}).get("look")
    print(json.dumps(shown, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "serve"):
        p = sub.add_parser(name, help="boot the game once and keep it for steps" if name == "start" else argparse.SUPPRESS)
        p.add_argument("--seed"); p.add_argument("--from-run"); p.add_argument("--start", help="fixture name from fixtures.json")
        p.add_argument("--name", default="live"); p.add_argument("--speed", type=int, default=8, choices=range(1, 9))
        p.add_argument("--minutes", type=int, default=25, choices=range(1, MAX_MINUTES + 1), metavar="1..29")
        p.add_argument("--idle", type=int, default=10, choices=range(1, MAX_MINUTES + 1), metavar="1..29",
                       help="stop after this many minutes without a request")
        p.add_argument("--watch-writes", action="store_true"); p.add_argument("--assisted", action="store_true")
    p = sub.add_parser("do", help="run steps: OP key=value ... | JSON object | JSON list")
    p.add_argument("step", nargs="+"); p.add_argument("--no-look", action="store_true")
    sub.add_parser("look", help="game state and the text on screen")
    sub.add_parser("steps", help="every step of this session with its verdict")
    sub.add_parser("status", help="the live session record")
    p = sub.add_parser("export", help="write the passed steps as a replayable scenario")
    p.add_argument("name"); p.add_argument("--first", type=int, default=0); p.add_argument("--last", type=int)
    p.add_argument("--description"); p.add_argument("--force", action="store_true")
    sub.add_parser("stop", help="stop the game, check the originals, write the report")
    args = parser.parse_args(argv)

    if args.command == "serve": return serve(args)
    if args.command == "start": return start(args)
    workshop = core.Workshop(PROJECT)
    if args.command == "status":
        print(json.dumps(read_session(workshop), indent=2, ensure_ascii=False)); return 0
    if args.command == "do":
        reply = request(workshop, {"cmd": "do", "steps": parse_steps(args.step), "look": not args.no_look})
    elif args.command == "export":
        reply = request(workshop, {"cmd": "export", "name": args.name, "first": args.first, "last": args.last,
                                   "description": args.description, "force": args.force})
    elif args.command == "stop":
        reply = request(workshop, {"cmd": "stop"})
        end = time.monotonic() + 60
        while time.monotonic() < end and (read_session(workshop) or {}).get("state") != "stopped": time.sleep(0.25)
        reply = read_session(workshop)
    else:
        reply = request(workshop, {"cmd": args.command})
    print(json.dumps(reply, indent=2, ensure_ascii=False))
    return 0 if reply and reply.get("ok", reply.get("state") == "stopped") else 1


if __name__ == "__main__": raise SystemExit(main())
