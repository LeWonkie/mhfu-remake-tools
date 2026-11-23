"""State-driven quest survey: every wait is on the game's own state, bounded and cancellable.

The fixed pauses of `atelier_campaign.run_quest` (about 38 s a quest) are
replaced by the states the game exposes through its developer commands:

    village ready  task 10, the running lobby task loaded, scene word 0
                   (the conditions `quest start` itself checks)
    quest loaded   task 11 for the requested quest, a coherent area read
                   (`atelier_zones.current`) and the quest clock seen moving
    mission over   GameSys +0x2E between 3 and 8, never written here
    back ready     the village ready again after the result screens

Fast-forward is the engine's own (kernel/fast_forward.hpp): the private
settings put its bind in `toggle` mode at the chosen speed, and the boot
script's Guide press turns it on. The game reports every stretch with its
measured ratio; ad hoc play keeps it off by itself.

Captures are scheduled in game frames through the live input file and
confirmed by the game's `[render] window` line, so a capture is either proven
written or reported missing, whatever the speed.
"""
from __future__ import annotations

import re
import struct
import threading
import time
from datetime import datetime
from pathlib import Path

import atelier_campaign
import atelier_core
import atelier_ledger
import atelier_plan
import atelier_zones

TASK_MANAGER_PTR = 0x08A862B8   # four task pointers; slot 0 the running main task
TASK_ID, TASK_LOAD_STATUS, TASK_LOADED = 0x14, 0x11, 3
LOBBY_TASK_PTR = 0x09A0BB04     # the running lobby task (lobby_task.ovl data)
LOBBY_MANAGER = 0x09B0AC38      # scene word at +0: 0 while the hunter walks
DIALOG_SCENE = 3                # a text box over the village (the peddler after quest 1, 2026-10-22)
DIALOG_PRESSES = 6              # × presses at most to close it
QUEST_CLOCK = 0x10              # Quest +0x10, u32 frames left at 30 a second
VILLAGE_TASK, QUEST_TASK = 10, 11
FRAMES_PER_SECOND = 30
SPEEDS = range(1, 9)            # 1: real time; 2..8: the engine's fast-forward
CAPTURES = ("fast", "detailed")
FF_ON = re.compile(r"^\[fast-forward\] on, (\d+)x")
FF_OFF = re.compile(r"^\[fast-forward\] off \(([\w ]+)\)")
FF_REFUSED = "[fast-forward] single player only"
LOAD_BEGUN = "[overlay] installed "    # a task or an area's code: a load is under way
LOAD_DONE = "[load] fast "             # the engine's fast loading ends a load episode
LOAD_GRACE = 10.0                      # real seconds without that line before giving up on it
SHOT_DONE = re.compile(r"^\[render\] window \d+x\d+ -> .*[\\/]([^\\/]+)\.bmp\s*$")


def _valid(pointer: int) -> bool:
    return 0x08800000 <= pointer < 0x0A000000


class Stopped(RuntimeError):
    """The trial ended under the driver: cancelled, or the game stopped."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class WaitTimeout(RuntimeError):
    def __init__(self, what: str, seconds: float, last=None):
        super().__init__(f"{what}: pas atteint en {seconds:.0f} s (dernier état : {last})")
        self.what, self.last = what, last


class Pilot:
    """Drives one `atelier_campaign.Session` by state, observing every output line."""

    def __init__(self, session, cancel: threading.Event | None = None, poll: float = 0.02, meter=None):
        self.session = session
        self.cancel = cancel
        self.poll = poll
        self.meter = meter
        self.ff_active = False
        self.ff_speed = None
        self.ff_last_off = None
        self.ff_refused = False
        self.shots_done: set[str] = set()
        self.waits: list[dict] = []
        self.last_snapshot = None
        self.loading_since = None
        self.load_lines_missing = 0
        session.on_lines = self._observe

    # ----- output ----------------------------------------------------------
    def _observe(self, lines: list[str]) -> None:
        for line in lines:
            match = FF_ON.match(line)
            if match:
                self.ff_active, self.ff_speed = True, int(match[1])
                continue
            match = FF_OFF.match(line)
            if match:
                self.ff_active, self.ff_last_off = False, match[1]
                continue
            if line.startswith(FF_REFUSED):
                self.ff_refused = True
                continue
            if line.startswith(LOAD_BEGUN):
                self.loading_since = self.loading_since or time.monotonic()
                continue
            if line.startswith(LOAD_DONE):
                self.loading_since = None
                continue
            match = SHOT_DONE.match(line)
            if match:
                self.shots_done.add(match[1])
        if self.meter is not None:
            self.meter.lines(lines)

    def drain(self) -> None:
        self.session.new_lines()

    # ----- waiting ---------------------------------------------------------
    def check(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Stopped("cancelled")
        if not self.session.alive():
            raise Stopped("runtime_stopped")

    def pause(self, seconds: float) -> None:
        if self.cancel is not None:
            self.cancel.wait(seconds)
        else:
            time.sleep(seconds)

    def until(self, what: str, probe, timeout: float, poll: float | None = None):
        """Returns the first truthy value of `probe()`, or raises WaitTimeout/Stopped."""
        began = time.monotonic()
        deadline = began + timeout
        last = None
        while True:
            self.check()
            try:
                value = probe()
            except atelier_zones.Unavailable as error:
                value, last = None, str(error)
            if value:
                self.waits.append({"what": what, "seconds": round(time.monotonic() - began, 3)})
                return value
            if time.monotonic() >= deadline:
                self.waits.append({"what": what, "seconds": round(time.monotonic() - began, 3), "timeout": True})
                raise WaitTimeout(what, timeout, last or self.last_snapshot)
            self.pause(self.poll if poll is None else poll)

    # ----- commands --------------------------------------------------------
    def ask(self, lines: list[str], expects: list, timeout: float = 6.0) -> list[str | None]:
        """Sends a batch in one write (one console frame) and collects one answer per command.

        An expectation is a text or a tuple of alternative texts."""
        expects = [e if isinstance(e, tuple) else (e,) for e in expects]
        if len(lines) != len(expects):
            raise ValueError("une réponse attendue par commande")
        self.drain()
        self.session.w.debug_commands(lines)
        answers: list[str | None] = [None] * len(lines)
        deadline = time.monotonic() + timeout
        while None in answers:
            self.check()
            for answer in self.session.new_lines():
                if answer.startswith("[debug] > "):
                    continue
                for index, expect in enumerate(expects):
                    if answers[index] is None and any(text in answer for text in expect):
                        answers[index] = answer
                        break
            if None not in answers or time.monotonic() >= deadline:
                break
            self.pause(self.poll)
        return answers

    def snapshot(self) -> dict:
        """Task, quest, stage, mission state, quest clock and lobby readiness in two round trips."""
        state, pointers = self._state_and(
            [(atelier_zones.GAMESYS_PTR, 4), (TASK_MANAGER_PTR, 4), (atelier_zones.QUEST_PTR, 4),
             (LOBBY_TASK_PTR, 4), (LOBBY_MANAGER, 4)])
        gamesys, manager, quest_ptr, lobby, scene = (struct.unpack("<I", p)[0] for p in pointers)
        snap = {"task": state[0] if state else None, "quest": state[1] if state else None,
                "stage": state[2] if state else None, "scene": scene, "mission": None, "clock": None,
                "lobby_loaded": False, "lobby_running": False}
        requests, names = [], []
        if _valid(gamesys):
            requests.append((gamesys + atelier_zones.MISSION_STATE, 1))
            names.append("mission")
        if _valid(manager):
            requests.append((manager, 4))
            names.append("slot0")
        if _valid(lobby):
            requests.append((lobby + TASK_LOAD_STATUS, 1))
            names.append("lobby_status")
        if _valid(quest_ptr) and snap["task"] == QUEST_TASK:
            requests.append((quest_ptr + QUEST_CLOCK, 4))
            names.append("clock")
        values = dict(zip(names, atelier_zones.read_many(self.session, requests))) if requests else {}
        if "mission" in values:
            snap["mission"] = values["mission"][0]
        if "clock" in values:
            snap["clock"] = struct.unpack("<I", values["clock"])[0]
        if "slot0" in values and "lobby_status" in values:
            snap["lobby_running"] = struct.unpack("<I", values["slot0"])[0] == lobby
            snap["lobby_loaded"] = (values["lobby_status"][0] & 0x7F) == TASK_LOADED
        self.last_snapshot = snap
        return snap

    def _state_and(self, requests):
        """`state` and a peek batch in the same console frame."""
        commands = [f"peek 0x{address:08X} {length}" for address, length in requests]
        self.drain()
        self.session.w.debug_commands(["state", *commands])
        rows, state = {}, None
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and (state is None or len(rows) < len(requests)):
            self.check()
            for line in self.session.new_lines():
                if line.startswith("[debug] > "):
                    continue
                match = atelier_campaign.TASK.search(line)
                if match:
                    state = (int(match[1]), int(match[3]), int(match[4]))
                    continue
                match = atelier_zones.PEEK_ROW.search(line)
                if match:
                    rows[int(match[1], 16)] = bytes.fromhex(match[2])
            if state is None or len(rows) < len(requests):
                self.pause(self.poll)
        missing = [address for address, _ in requests if address not in rows]
        if missing:
            raise atelier_zones.Unavailable("lecture incomplète : " + ", ".join(f"0x{a:08X}" for a in missing))
        return state, [rows[address][:length] for address, length in requests]

    # ----- readiness -------------------------------------------------------
    @staticmethod
    def village_ready(snap: dict) -> bool:
        return (snap["task"] == VILLAGE_TASK and snap["scene"] == 0 and snap["lobby_running"]
                and snap["lobby_loaded"])

    @staticmethod
    def mission_over(snap: dict) -> bool:
        return atelier_zones.mission_over(snap["mission"])

    def settled(self) -> bool:
        """No load under way: the engine reported the end of the last one.

        The memory says the village is ready while its loading screen is
        still drawn (captures of 2026-10-21); a departure sent then is
        dropped. Fast loading (forced on in the trial's settings) prints a
        line when a load ends; without it for LOAD_GRACE seconds, the wait
        goes on and the miss is counted."""
        self.drain()
        if self.loading_since is None:
            return True
        if time.monotonic() - self.loading_since > LOAD_GRACE:
            self.loading_since = None
            self.load_lines_missing += 1
            return True
        return False

    def wait_village(self, timeout: float) -> dict:
        def ready():
            snap = self.snapshot()
            return snap if self.village_ready(snap) and self.settled() else None
        return self.until("village prêt", ready, timeout, poll=0.1)

    def leave_dialog(self, timeout: float, presses: int = DIALOG_PRESSES) -> tuple[dict, int]:
        """× while a text box holds a loaded village; the village ready and the presses spent.

        Only the state seen twice (task 10, lobby loaded and running, scene
        word 3) is pressed on, at most once a game second and `presses`
        times; × is the engine's back (pad b is ○ unless confirm is south).
        Readiness is read again from the game: a press never makes it."""
        spent, last, seen = [0], [0.0], [0]

        def free():
            snap = self.snapshot()
            if self.village_ready(snap) and self.settled():
                return snap
            boxed = (snap["task"] == VILLAGE_TASK and snap["scene"] == DIALOG_SCENE
                     and snap["lobby_running"] and snap["lobby_loaded"])
            seen[0] = seen[0] + 1 if boxed else 0
            speed = self.ff_speed if self.ff_active and self.ff_speed else 1
            if seen[0] >= 2 and spent[0] < presses and time.monotonic() - last[0] >= 1.0 / speed:
                self.session.inputs(["0:pad a 6"])
                spent[0] += 1
                last[0] = time.monotonic()
            return None
        try:
            return self.until("dialogue fermé", free, timeout, poll=0.25), spent[0]
        except WaitTimeout as error:
            error.presses = spent[0]
            raise

    def depart(self, quest: int, timeout: float = 60.0, attempts: int = 6) -> int:
        """`quest start` until the quest task runs; returns the departures the game dropped.

        Sent while the village is still initializing, the departure is
        accepted (scene word 0x1D) and then dropped: the scene word goes back
        to 0 and task 10 keeps running (seen 2026-10-21 right after boot,
        which the old fixed 8 s pause hid). That is seen here and the start
        is sent again, a bounded number of times."""
        deadline = time.monotonic() + timeout
        dropped = 0
        for _ in range(attempts):
            self.wait_village(max(1.0, deadline - time.monotonic()))
            answer = self.ask([f"quest start {quest}"], [("started quest", "not started", "refused")], 6.0)[0] or ""
            if "has no quest" in answer or "refused" in answer:
                raise WaitTimeout("départ", 0, answer)
            if "started quest" not in answer:
                continue

            def outcome():
                snap = self.snapshot()
                if snap["task"] == QUEST_TASK:
                    return "departing"
                if snap["task"] == VILLAGE_TASK and snap["scene"] == 0:
                    return "dropped"
                return None
            if self.until("départ en cours", outcome, max(1.0, deadline - time.monotonic()), poll=0.1) == "departing":
                return dropped
            dropped += 1
        raise WaitTimeout("départ", timeout, f"{dropped} départs abandonnés par le jeu")

    def wait_quest(self, quest: int, timeout: float) -> dict:
        """Task 11 for this quest, a coherent area matching GameSys, then the clock moving."""
        def loaded():
            snap = self.snapshot()
            if snap["task"] != QUEST_TASK or snap["quest"] != quest or not snap["stage"]:
                return None
            if self.mission_over(snap) or snap["clock"] is None or not self.settled():
                return None
            here = atelier_zones.current(self.session, timeout=2.0)
            return {**snap, "area": here} if here["stage"] == snap["stage"] else None
        first = self.until("quête chargée", loaded, timeout, poll=0.1)
        # A loaded area whose clock does not move is not playable yet.
        def moving():
            snap = self.snapshot()
            if snap["task"] != QUEST_TASK or snap["stage"] != first["stage"] or snap["clock"] is None:
                raise atelier_zones.Unavailable(f"zone changée pendant la vérification : {snap['stage']}")
            return snap if snap["clock"] < first["clock"] else None
        second = self.until("chronomètre en marche", moving, min(30.0, timeout), poll=0.05)
        return {**second, "area": first["area"], "clock_first": first["clock"]}

    # ----- fast-forward and captures --------------------------------------
    def ensure_fast_forward(self, timeout: float = 5.0) -> bool:
        """Presses the toggle once if the game reports it off; never presses blind."""
        self.drain()
        if self.ff_active or self.ff_refused:
            return self.ff_active
        self.session.inputs(["0:pad guide 2"])

        def reported():
            self.drain()
            return self.ff_active or self.ff_refused
        try:
            self.until("avance rapide", reported, timeout)
        except WaitTimeout:
            return False
        return self.ff_active

    def shoot(self, name: str, frames: int = 0) -> str:
        """Schedules a capture `frames` game frames from now."""
        self.shots_done.discard(name)
        self.session.inputs([f"{max(0, int(frames))}:shot {name}"])
        return name

    def confirm_shots(self, names: list[str], timeout: float = 8.0) -> list[str]:
        """The captures the game reported written; the others are missing."""
        def written():
            self.drain()
            return all(n in self.shots_done for n in names)
        try:
            self.until("captures", written, timeout)
        except WaitTimeout:
            pass
        return [n for n in names if n in self.shots_done]


# ----- one quest ------------------------------------------------------------
def run_quest(pilot: Pilot, quest: int, result: atelier_campaign.QuestResult, look_seconds: float = 12.0,
              captures: str = "fast", loadout: str = "best",
              cheats: tuple[str, ...] = atelier_campaign.DEFAULT_CHEATS, meter=None) -> atelier_campaign.QuestResult:
    """Start, load, look, end by the clock, come back: each step on the game's state.

    The outcome is a survey: `forced_end` means the clock was cut and the game
    reported failure (5). `reported_clear` retains a game-reported clear …1467 tokens truncated…ddler's "Greetin's!" box).
        snap = pilot.snapshot()
        if snap["task"] == VILLAGE_TASK:
            return snap
        speed = pilot.ff_speed if pilot.ff_active and pilot.ff_speed else 1
        if time.monotonic() - pressed[0] >= 1.0 / speed:
            session.inputs(["0:pad b 6"])
            pressed[0] = time.monotonic()
        return None
    try:
        pilot.until("retour au village", back, 90, poll=0.25)
        result.returned = True
        result.return_seconds = round(time.monotonic() - mark, 1)
        # Back in the village task is not a usable village: quest 2 came back
        # to a dialog keeping the scene word at 3 (2026-10-21), quest 1 to
        # the peddler's (2026-10-22). Closed with ×, bounded, and noted.
        try:
            pilot.wait_village(10)
        except WaitTimeout:
            _, closes = pilot.leave_dialog(20)
            if closes:
                result.notes.append(f"dialogue fermé au village ({closes} ×)")
        result.village_ready = True
    except WaitTimeout as error:
        presses = getattr(error, "presses", 0)
        if presses:
            result.notes.append(f"dialogue non fermé après {presses} ×")
        result.notes.append(("village non prêt : " if result.returned else "pas de retour au village : ") + str(error))
    if result.returned:
        # After the fade from black (about a game second, 2026-10-21); taken
        # also when the village is blocked, as the evidence of what blocks it.
        planned.append(pilot.shoot(f"q{quest}-village", FRAMES_PER_SECOND))
    result.shots = pilot.confirm_shots(planned, timeout=10)
    missing = [n for n in planned if n not in result.shots]
    if missing:
        result.notes.append("captures manquantes : " + ", ".join(missing))
    phase("return")
    result.crashed = not session.alive()
    if result.returned and not result.crashed and not result.village_ready:
        result.outcome = "village_blocked"
    elif result.returned and not result.crashed:
        # Keep the game's clear, failure and unknown ends distinct. Quest 1001
        # can report clear (3) even after the clock was shortened.
        if result.mission_state == 3:
            result.outcome = "reported_clear"
        elif result.mission_state == 5:
            result.outcome = "forced_end" if result.timer_shortened else "quest_failed"
        else:
            result.outcome = "quest_ended" if result.mission_state is not None else "returned_without_end"
    elif result.crashed:
        result.outcome = "crashed"
    else:
        result.outcome = "no_return"
    return result


def survey_passed(result) -> bool:
    """A technical pass with a recognized end, never a gameplay validation."""
    end = ((result.mission_state == 3 and getattr(result, "outcome", "") == "reported_clear")
           or (result.mission_state == 5 and getattr(result, "outcome", "") == "forced_end"
               and getattr(result, "timer_shortened", False)))
    return (result.started and result.reached and not result.crashed and result.returned
            and getattr(result, "village_ready", False)
            and end and result.objectives is not None and result.roster is not None
            and not any(note.startswith("captures manquantes") for note in result.notes))


# ----- campaign -------------------------------------------------------------
def run_campaign(workshop: atelier_core.Workshop, quests: list[int], seed_id: str | None = None,
                 rtc_us: int | None = None, log=print, mode: str = "fast", speed: int = 8,
                 captures: str = "fast", look_seconds: float = 12.0, cancel=None, loadout: str = "best",
                 cheats: tuple[str, ...] = atelier_campaign.DEFAULT_CHEATS, variant: str = "normal",
                 use_cache: bool = True, force: bool = False, resume: Path | None = None, meter=None) -> Path:
    """Survey `quests` in as few sessions as possible, with checkpoints, a cache and resumption."""
    if speed not in SPEEDS or captures not in CAPTURES:
        raise ValueError(f"vitesse 1..8, captures parmi {CAPTURES}")
    if loadout not in atelier_campaign.LOADOUTS or any(c not in atelier_campaign.CHEATS for c in cheats):
        raise ValueError("équipement ou triches inconnus")
    if not 1.0 <= look_seconds <= 120.0:
        raise ValueError("observation de 1 à 120 s de jeu")
    base_variant = variant
    variant = atelier_core.register_rtc_variant(variant, rtc_us)
    seed = workshop._current_seed() if seed_id is None else workshop._seed(seed_id)
    if seed is None:
        raise RuntimeError("préparez d'abord un point de départ pour la campagne")
    seed_id = seed["id"]
    scenario = {"name": "campagne", "label": "Campagne pilotée", "fast_boot": True, "debug": True,
                "dump_overlays": True, "script": atelier_campaign.BOOT,
                "settings": {"video.fast_forward": "toggle", "video.fast_forward_speed": str(speed),
                             "video.fast_loading": "1"} if speed > 1
                else {"video.fast_forward": "hold", "video.fast_loading": "1"},
                "screenshot_every": 0 if captures == "fast" else 600}
    settings = {"mode": mode, "speed": speed, "captures": captures, "look_seconds": look_seconds,
                "loadout": loadout, "cheats": list(cheats), "variant": base_variant, "rtc_us": rtc_us}
    environment = atelier_ledger.environment(workshop, seed, settings, scenario)
    ledger = atelier_ledger.Ledger(workshop.data_dir / "validations")
    out, results, metadata = _open_campaign(workshop, resume, quests, seed, variant, settings, environment)
    pending = [q for q in metadata["plan"] if q not in {r.quest for r in results if r.final}]
    session = pilot = None
    atelier_campaign._write_report(workshop, out, results, variant, metadata)
    try:
        for quest in pending:
            if cancel and cancel.is_set():
                metadata["state"] = "cancelled"
                break
            cached = ledger.find("survey", quest, environment) if use_cache and not force else None
            if cached:
                result = atelier_campaign.QuestResult(quest, run_id=cached["evidence"]["run_id"])
                result.reused, result.final, result.outcome = True, True, "reused"
                result.evidence = cached["evidence"]
                result.notes.append(f"validation réutilisée ({cached['recorded_at']}, clé {cached['key'][:12]})")
                results.append(result)
                log(f"quête {quest} : réutilisée ({cached['evidence']['campaign']})")
                atelier_campaign._write_report(workshop, out, results, variant, metadata)
                continue
            if session is not None and (time.monotonic() - session.began > atelier_core.MAX_SECONDS - 300
                                        or not session.alive()):
                session.stop()
                session = None
            if session is None:
                try:
                    session, pilot = _start_session(workshop, seed_id, variant, scenario, cancel, loadout, meter,
                                                    log, metadata["runs"])
                except (WaitTimeout, Stopped) as error:
                    result = atelier_campaign.QuestResult(quest, run_id=metadata["runs"][-1])
                    result.final, result.reused = isinstance(error, WaitTimeout), False
                    result.outcome = "boot_failed" if result.final else error.reason
                    result.notes.append(f"village non atteint au démarrage : {error}")
                    results.append(result)
                    metadata["state"] = "cancelled" if cancel and cancel.is_set() else "failed"
                    workshop.stop()
                    break
                metadata["fast_forward"].append({"run": session.run_id, "requested": speed})
                if speed > 1:
                    on = pilot.ensure_fast_forward()
                    metadata["fast_forward"][-1].update(active=on, reported_speed=pilot.ff_speed,
                                                        refused=pilot.ff_refused)
                    if not on:
                        log("avance rapide non confirmée : la campagne continue en temps réel")
                atelier_campaign._write_report(workshop, out, results, variant, metadata)
            log(f"quête {quest}…")
            result = atelier_campaign.QuestResult(quest, run_id=session.run_id)
            result.reused, result.final = False, False
            results.append(result)
            try:
                run_quest(pilot, quest, result, look_seconds, captures, loadout, cheats, meter)
                result.final = True
            except Stopped as stop:
                result.outcome = stop.reason
                result.notes.append("essai arrêté : " + stop.reason)
                result.crashed = stop.reason == "runtime_stopped"
                result.final = stop.reason == "runtime_stopped"
            except WaitTimeout as error:
                result.outcome = "timeout"
                result.notes.append(str(error))
                result.final = True
            finally:
                result.waits = list(pilot.waits)
                pilot.waits.clear()
                result.fast_forward = {"active": pilot.ff_active, "speed": pilot.ff_speed,
                                       "last_off": pilot.ff_last_off}
                workshop.record_quest_result(session.run_id, result.__dict__)
                atelier_campaign._write_report(workshop, out, results, variant, metadata)
            if result.final and survey_passed(result):
                ledger.record("survey", quest, environment, "passed",
                              {"campaign": str(out), "run_id": session.run_id, "shots": result.shots})
            elif result.final:
                ledger.record("survey", quest, environment, result.outcome,
                              {"campaign": str(out), "run_id": session.run_id, "shots": result.shots})
            log(f"quête {quest} : {result.outcome} {' ; '.join(result.notes)}")
            if cancel and cancel.is_set():
                metadata["state"] = "cancelled"
                break
            if speed > 1 and session.alive() and not pilot.ff_active and not pilot.ff_refused:
                pilot.ensure_fast_forward()
            # Anything but a clean survey may leave the game in an unknown state.
            if result.outcome not in ("forced_end", "reported_clear"):
                session.stop()
                session = None
        if metadata["state"] == "running":
            metadata["state"] = "completed"
    except KeyboardInterrupt:
        metadata["state"] = "interrupted"
        raise
    except Stopped as stop:
        metadata["state"] = "cancelled" if stop.reason == "cancelled" else "failed"
    except BaseException:
        metadata["state"] = "failed"
        raise
    finally:
        try:
            if session is not None:
                session.stop()
        finally:
            atelier_campaign._write_report(workshop, out, results, variant, metadata)
    return out


def _start_session(workshop, seed_id, variant, scenario, cancel, loadout, meter, log, runs):
    session = atelier_campaign.Session(workshop, seed_id, atelier_core.MAX_SECONDS, variant=variant,
                                       scenario=scenario)
    runs.append(session.run_id)
    session.cancel = cancel
    pilot = Pilot(session, cancel=cancel, meter=meter)
    if meter is not None:
        meter.close("between", None)
    if loadout != "none":
        session.arm_loadout(loadout)
    log(f"jeu lancé (essai {session.run_id})")
    pilot.wait_village(120)
    if meter is not None:
        meter.close("boot", None)
    return session, pilot


def _open_campaign(workshop, resume, quests, seed, variant, settings, environment):
    """A new campaign folder, or an interrupted one whose unfinished quests are taken up again."""
    if resume is not None:
        import json
        data = json.loads((Path(resume) / "campagne.json").read_text(encoding="utf-8"))
        if data.get("environment_key") != environment["key"]:
            raise RuntimeError("reprise refusée : exécutable, données, seed, RTC, réglages ou pilote ont changé")
        results = []
        for item in data.get("results", []):
            result = atelier_campaign.QuestResult(item["quest"])
            result.__dict__.update(item)
            # An interrupted or unknown result is taken up again, never counted.
            result.final = bool(item.get("final")) and item.get("outcome") not in ("cancelled", "unknown", None)
            if result.final:
                results.append(result)
        metadata = {k: v for k, v in data.items() if k not in ("results", "updated_at", "runtime_stop",
                                                               "interpreted_top", "window_size")}
        metadata["state"] = "running"
        metadata.setdefault("resumed_at", []).append(datetime.now().astimezone().isoformat(timespec="seconds"))
        return Path(resume), results, metadata
    out = workshop.data_dir / "campaigns" / datetime.now().strftime("%Y%m%d-%H%M%S")
    if out.exists():
        import uuid
        out = out.with_name(out.name + "-" + uuid.uuid4().hex[:6])
    out.mkdir(parents=True)
    metadata = {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"), "state": "running",
                "driver": "atelier_pilot", "plan": list(quests), "variant": variant, "rtc_us": settings["rtc_us"],
                "seed_id": seed["id"], "seed_sha256": seed["seed_sha256"], "window_size": None, "runs": [],
                "loadout": settings["loadout"], "cheats": settings["cheats"], "settings": settings,
                "environment_key": environment["key"], "environment": environment["components"],
                "fast_forward": []}
    return out, [], metadata


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Repérage piloté par l'état du jeu (rapide, avec cache)")
    parser.add_argument("quests", nargs="*", type=int)
    parser.add_argument("--seed", default=None)
    parser.add_argument("--rtc-us", type=int, default=None)
    parser.add_argument("--speed", type=int, default=8, choices=list(SPEEDS), help="1 : temps réel ; 2..8 : avance rapide")
    parser.add_argument("--captures", choices=CAPTURES, default="fast")
    parser.add_argument("--look", type=float, default=12.0, help="secondes de jeu observées par quête")
    parser.add_argument("--loadout", choices=atelier_campaign.LOADOUTS, default="best")
    parser.add_argument("--cheats", nargs="*", choices=atelier_campaign.CHEATS,
                        default=list(atelier_campaign.DEFAULT_CHEATS))
    parser.add_argument("--force", action="store_true", help="réexécuter même si une validation identique existe")
    parser.add_argument("--no-cache", action="store_true", help="ni lire ni réutiliser le cache")
    parser.add_argument("--resume", type=Path, default=None, help="reprendre une campagne interrompue")
    parser.add_argument("--all", action="store_true", help="missions du disque admises par quest start, hors missions vides")
    parser.add_argument("--plan", action="store_true", help="préparer le plan et les exclusions sans lancer le jeu")
    parser.add_argument("--plan-out", type=Path, help="nouveau dossier du plan (avec --plan seulement)")
    parser.add_argument("--limit", type=int, help="limiter ce lancement aux N premières quêtes sélectionnées")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit doit être positif")
    if args.plan_out is not None and not args.plan:
        parser.error("--plan-out demande --plan")
    if args.resume is not None and (args.all or args.quests or args.limit is not None or args.plan):
        parser.error("--resume reprend son propre plan ; ne pas combiner avec quêtes, --all, --limit ou --plan")
    if args.all and args.quests:
        parser.error("choisir des quêtes explicites ou --all")
    if args.plan and (args.quests or args.limit is not None):
        parser.error("--plan inventorie toutes les missions ; choisir un lot ensuite")
    project = Path(__file__).resolve().parents[2]
    if args.plan:
        try:
            plan = atelier_plan.load_plan(project)
            out = args.plan_out or project / "atelier-psprecomp" / "plans" / datetime.now().strftime("%Y%m%d-%H%M%S")
            path = atelier_plan.write_plan(plan, out)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        print(f"{len(plan['quests'])} missions admissibles ; aucun jeu lancé. Plan : {path}", flush=True)
        return 0
    quests = args.quests
    if args.all:
        try:
            plan = atelier_plan.load_plan(project)
        except ValueError as error:
            parser.error(str(error))
        quests = plan["quests"]
        print(f"Préparation : {len(quests)} missions admissibles sur {plan['counts']['catalogued']} cataloguées.", flush=True)
    if args.limit is not None:
        quests = quests[:args.limit]
    if not quests and args.resume is None:
        parser.error("des quêtes, --all ou --resume")
    if any(q <= 0 or q > 65535 for q in quests):
        parser.error("identifiants de quête entre 1 et 65535")
    workshop = atelier_core.Workshop(project)
    out = run_campaign(workshop, quests, args.seed, rtc_us=args.rtc_us, log=lambda m: print(m, flush=True),
                       speed=args.speed, captures=args.captures, look_seconds=args.look, loadout=args.loadout,
                       cheats=tuple(args.cheats), use_cache=not args.no_cache, force=args.force, resume=args.resume)
    print("rapport :", out / "campagne.html", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
