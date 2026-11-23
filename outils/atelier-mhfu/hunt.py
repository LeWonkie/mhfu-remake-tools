"""Scripted hunt: kill a quest's monsters with real attacks and see the quest end.

A fresh trial per quest (its own save copy). Health held, monsters at one
health (`hold onehit on`), so any hit kills; the hits themselves are the
game's: the hunter is placed beside a monster and the attack buttons are
pressed. Areas are reached through the game's exits (atelier_zones). The
quest's end is GameSys's mission state, read without changing it: from the
first ended state on, nothing is moved or attacked; the end screens are
captured, then passed to return to the village. Writes hunt.json in the run
folder.

Before leaving, the hunter wears the game's `loadout best` (Ukanlos Soul
Hatchet and White Fatalis Z) unless asked otherwise; the cheats held on the
quest and the loadout answer are recorded in hunt.json.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import atelier_campaign  # noqa: E402
import atelier_core  # noqa: E402
import atelier_pilot  # noqa: E402
import atelier_zones  # noqa: E402
import zone_tour  # noqa: E402
from atelier_hunt_state import resolve_objective, objective_progress, KillTracker, ProgressGuard

# Triangle and circle (the virtual pad's y and b) draw the weapon and attack.
ATTACK = ["0:pad y 4", "12:pad y 4", "24:pad b 4", "36:pad y 4", "48:pad b 4", "60:pad y 4"]
SIDES = [(150.0, 0.0), (-150.0, 0.0), (0.0, 150.0), (0.0, -150.0)]
# Held on the quest through the game's `hold NAME on` (docs/MHFU_TOOLS.md).
CHEATS = ("health", "stamina", "sharpness", "onehit", "timer", "items")
DEFAULT_CHEATS = ("health", "stamina", "sharpness", "onehit")
LOADOUTS = ("best", "none")


def first_hop(graph: dict[int, list[int]], here: int, target: int) -> int | None:
    if here == target:
        return None
    seen, queue = {here}, deque((d, d) for d in graph.get(here, []))
    while queue:
        area, first = queue.popleft()
        if area == target:
            return first
        if area in seen:
            continue
        seen.add(area)
        queue.extend((d, first) for d in graph.get(area, []) if d not in seen)
    return None


def load_routes(path: Path, quest: int) -> dict[int, list[int]]:
    """Use a prior tour as route hints; each area's exits are reread on arrival."""
    data = json.loads(path.read_text(encoding="utf-8"))
    tour = next((t for t in data["tours"] if t["quest"] == quest), None)
    if tour is None:
        return {}
    return {int(stage): [d for d in area["exits"] if 0 < d < 0x1000 and d != int(stage)]
            for stage, area in tour["areas"].items()}


def patrol_step(graph, here, patrol, index):
    """Next reachable waypoint, cycling only through the explicit patrol."""
    for _ in range(len(patrol)):
        if here != patrol[index]:
            step = first_hop(graph, here, patrol[index])
            if step is not None:
                return step, index
        index = (index + 1) % len(patrol)
    return None, index


def revisit_step(graph: dict[int, list[int]], here: int, last_seen: dict[int, float]) -> int | None:
    """Once every area has been seen: the first hop towards the area seen
    longest ago. Big monsters wander and arrive late, small ones come back
    (runs of 20261021: 2002, 1006 and 3101 stopped after one tour)."""
    for area in sorted((a for a in last_seen if a != here), key=lambda a: last_seen[a]):
        step = first_hop(graph, here, area)
        if step is not None:
            return step
    return None


def placement(position, exits, side=0):
    """A spot beside the monster outside every exit volume of the area, trying
    the sides in turn from `side`; None when each side is in an exit."""
    x, y, z = position
    volumes = [e for e in exits if e.get("shape") in (0, 1) and e.get("size", 0) > 0 and e.get("height", 0) > 0]
    for k in range(len(SIDES)):
        dx, dz = SIDES[(side + k) % len(SIDES)]
        spot = (x + dx, y, z + dz)
        if not any(atelier_zones.inside_exit(spot, e) for e in volumes):
            return spot
    return None


def combat_targets(monsters, target_ids, strict=False):
    alive = [m for m in monsters if m["health"] > 0]
    preferred = [m for m in alive if m["type"] in target_ids]
    return preferred if preferred or strict else alive


def hunt(workshop, seed: str, quest: int, rtc_us: int, seconds: float, routes=None,
         species=None, patrol=None, cancel=None, progress=None, stall_seconds=120,
         walk_in_frames=240, loadout="best", cheats=DEFAULT_CHEATS, speed=1) -> dict:
    """`speed` 1 keeps the validated real-time run (and its frame-space trace);
    2..8 runs the engine's fast-forward (atelier_pilot), with the game-time
    pauses divided by the speed and the village and the area waited for by state."""
    if speed not in atelier_pilot.SPEEDS:
        raise ValueError("Speed 1..8")
    if not 1 <= seconds <= 1200 or not 15 <= stall_seconds <= 600:
        raise ValueError("Hunt duration must be 1..1200 s; progress deadline 15..600 s")
    if not isinstance(walk_in_frames, int) or not 0 <= walk_in_frames <= 600:
        raise ValueError("Arrival walking must release within 600 frames")
    if loadout not in LOADOUTS or any(cheat not in CHEATS for cheat in cheats):
        raise ValueError(f"Loadout {LOADOUTS}, cheats among {CHEATS}")
    goal = resolve_objective(quest, species, patrol)
    if routes is None and goal.get("routes"):
        route_path = workshop.project / goal["routes"]
        if route_path.is_file():
            routes = load_routes(route_path, quest)
    if cancel and cancel.is_set():
        return {"quest": quest, "state": "cancelled", "phase": "finished", "kills": 0,
                "run_id": None, "objective": goal, "reason": "trial_stopped", "success": None}
    variant = atelier_core.register_rtc_variant("space_trace" if speed == 1 else "normal", rtc_us)
    scenario = None
    if speed > 1:
        scenario = {"name": "campagne", "label": "Chasse rapide", "fast_boot": True, "debug": True,
                    "dump_overlays": True, "script": atelier_campaign.BOOT,
                    "settings": {"video.fast_forward": "toggle", "video.fast_forward_speed": str(speed),
                                 "video.fast_loading": "1"}}
    session = atelier_campaign.Session(workshop, seed, min(atelier_core.MAX_SECONDS, int(seconds) + 240),
                                       variant=variant, **({"scenario": scenario} if scenario else {}))
    session.cancel = cancel
    pilot = atelier_pilot.Pilot(session, cancel=cancel) if speed > 1 else None
    armed = loadout != "none" and session.arm_loadout(loadout)
    log = {"schema": 2, "quest": quest, "run_id": session.run_id, "events": [], "kills": 0,
           "state": "running", "phase": "booting", "objective": goal, "route_hints": routes or {},
           "combat_species": goal["species"], "patrol": goal.get("patrol", []),
           "success": None, "visual_review": "not_performed", "stage": None,
           "mission_state": None, "mission_states": [], "returned_to_village": None,
           "objective_progress": None, "objective_history": [], "objective_progress_available": False,
           "stall_seconds": stall_seconds, "budget_seconds": seconds,
           "loadout": loadout, "loadout_armed_at_boot": bool(armed), "loadout_answer": None,
           "cheats": list(cheats), "cheats_held": [], "speed": speed, "fast_forward": None}
    out = session.dir / "hunt.json"
    began = time.monotonic()

    def active():
        return not (cancel and cancel.is_set()) and session.alive()

    def stopped_state():
        return "cancelled" if cancel and cancel.is_set() else "runtime_stopped"

    def pause(duration):
        # Every pause here stands for game time (a banner, an animation, a settle).
        duration /= speed
        if cancel:
            cancel.wait(duration)
        else:
            time.sleep(duration)

    def note(**event):
        event["t"] = round(time.monotonic() - began, 2)
        if "phase" in event:
            log["phase"] = event["phase"]
        log["events"].append(event)
        log["elapsed_seconds"] = event["t"]
        log["memory"] = dict(getattr(session, "memory_stats", {}))
        atelier_core._write_json(out, log)
        if progress:
            progress({key: log.get(key) for key in ("quest", "run_id", "state", "phase", "stage",
                      "kills", "objective", "elapsed_seconds", "reason", "mission_state",
                      "success", "returned_to_village", "objective_progress", "objective_progress_available")})
        print(json.dumps(event), flush=True)

    def update_objective_progress():
        if not goal.get("progress_counter"):
            return
        try:
            value = objective_progress(atelier_zones.mission_objectives(session, quest), goal)
            if value is None:
                raise atelier_zones.Unavailable("objective descriptors do not match the verified counter")
        except atelier_zones.Unavailable as error:
            changed = log.get("objective_progress_error") != str(error) or log["objective_progress_available"]
            log.update(objective_progress_available=False, objective_progress_error=str(error))
            if changed:
                note(kind="objective_read_retry", error=str(error))
            return
        previous = log["objective_progress"]
        changed = previous is None or previous["completed"] != value["completed"]
        value["observed_at"] = round(time.monotonic() - began, 2)
        log.update(objective_progress=value, objective_progress_available=True, objective_progress_error=None)
        if changed:
            log["objective_history"].append(dict(value))
            note(kind="objective_progress", completed=value["completed"], required=value["required"])

    def finish_quest(mission, kills):
        """The game ended the quest: no more moves or attacks; capture, then leave."""
        clear, failed = atelier_zones.MISSION_CLEAR, atelier_zones.MISSION_FAILED
        log.update(state={clear: "quest_cleared", failed: "quest_failed"}.get(mission, "quest ended"),
                   reason=f"mission_state_{mission}", mission_state=mission,
                   success=True if mission == clear else False if mission == failed else None,
                   result_capture=f"frames/h{quest}-fin.bmp")
        note(kind="quest_end", phase="results", mission_state=mission, observed_kills=kills)
        session.shot(f"h{quest}-fin")
        pause(6)
        session.shot(f"h{quest}-banniere")
        try:
            note(kind="final_roster", boss_roster=atelier_zones.quest_targets(session))
        except atelier_zones.Unavailable as error:
            note(kind="final_roster", error=str(error))
        pause(6)
        session.shot(f"h{quest}-recompense")
        # The field time left after the end, then the reward and payment
        # screens: cut their countdown and confirm until the village loads.
        # Bounded; a quest kept on its end screens is reported.
        log["returned_to_village"] = False
        deadline = time.monotonic() + 150
        while active() and time.monotonic() < deadline:
            session.ask("quest results", "results countdown", 3.0)
            session.inputs(["0:pad b 6"])
            if session.wait_task(10, 4):
                log["returned_to_village"] = True
                pause(6)
                session.shot(f"h{quest}-village")
                break
        note(kind="left_quest", phase="results", returned_to_village=log["returned_to_village"])

    try:
        note(kind="phase", phase="booting")
        if not session.wait_task(10, 150):
            log.update(state="start_failed" if active() else stopped_state(),
                       reason="village_not_reached" if active() else "trial_stopped")
            return log
        if pilot:
            pilot.wait_village(60)
            log["fast_forward"] = {"active": pilot.ensure_fast_forward(), "reported_speed": pilot.ff_speed,
                                   "refused": pilot.ff_refused}
            note(kind="fast_forward", **log["fast_forward"])
        else:
            pause(8)
        if loadout != "none":
            # Recorded as the game answered; a refusal is noted, the hunt goes on.
            answer = session.ask(f"loadout {loadout}", "wearing", 6.0)
            log["loadout_answer"] = answer.replace("[debug] ", "") if answer else None
            note(kind="loadout", loadout=loadout, worn=bool(answer and "wearing" in answer))
        note(kind="phase", phase="departing")
        started = False
        if pilot:
            dropped = pilot.depart(quest, 60)
            started = True
            if dropped:
                note(kind="departure_dropped", count=dropped)
        for _ in range(0 if started else 20):
            if not active():
                break
            answer = session.ask(f"quest start {quest}", "quest", 6.0)
            if answer and "started quest" in answer:
                started = True
                break
            pause(1)
        if not started or not session.wait_task(11, 90, quest=quest):
            log.update(state="start_failed" if active() else stopped_state(), reason="quest_not_reached")
            return log
        for cheat in cheats:
            if session.ask(f"hold {cheat} on", cheat, 6.0):
                log["cheats_held"].append(cheat)
        if pilot:
            pilot.wait_quest(quest, 60)
        else:
            pause(5)
        combat_began = time.monotonic()
        guard = ProgressGuard(combat_began, stall_seconds)
        tracker = KillTracker(goal["species"])
        target_ids = set(goal["species"])
        graph = {stage: list(exits) for stage, exits in (routes or {}).items()}
        failed, visited = set(), set()
        side = patrol_index = read_failures = 0
        near_exit = set()
        last_seen = {}
        last_mission = -1
        patrol = goal.get("patrol", [])
        session.shot(f"h{quest}-debut")
        note(kind="objective", phase="observing", boss_roster=atelier_zones.quest_targets(session), objective=goal)
        while active() and time.monotonic() - combat_began < seconds:
            try:
                mission = atelier_zones.mission_state(session)
                if mission != last_mission:
                    last_mission = mission
                    log["mission_states"].append({"t": round(time.monotonic() - began, 2), "state": mission})
                update_objective_progress()
                if atelier_zones.mission_over(mission):
                    finish_quest(mission, tracker.count)
                    break
                here = atelier_zones.current(session, timeout=4)
                found = atelier_zones.monsters(session)
                read_failures = 0
            except atelier_zones.Unavailable as error:
                read_failures += 1
                note(kind="read_retry", phase="loading", attempt=read_failures, error=str(error))
                if not active():
                    break
                if read_failures >= 3:
                    log.update(state="read_failed", reason=str(error))
                    break
                pause(0.2)
                continue
            if not active():
                break
            stage = log["stage"] = here["stage"]
            usable = [e for e in here["exits"] if atelier_zones.usable(e)
                      and (stage, e["destination"]) not in failed and e["destination"] != stage]
            graph[stage] = [e["destination"] for e in usable]
            visited.add(stage)
            last_seen[stage] = time.monotonic()
            for monster in tracker.observe(found, stage):
                log["kills"] = tracker.count
                note(kind="kill", phase="combat", slot=monster["slot"], stage=monster["stage"],
                     species=monster["type"], observed_kills=tracker.count)
            if not active():
                break
            completed = log["objective_progress"]["completed"] if log["objective_progress_available"] else None
            if guard.observe(stage, tracker.count, time.monotonic(), objective_completed=completed):
                log.update(state="stalled", reason="no_new_area_or_target_death")
                break
            alive = combat_targets(found, target_ids, strict=True)
            local = [m for m in alive if m["stage"] == stage]
            spot = None
            for target in local:
                spot = placement(target["position"], here["exits"], side)
                if spot:
                    break
                if (stage, target["slot"]) not in near_exit:
                    near_exit.add((stage, target["slot"]))
                    note(kind="target_by_exit", phase="combat", target_slot=target["slot"], stage=stage)
            if spot:
                side += 1
                note(kind="attack", phase="combat", target_slot=target["slot"], species=target["type"])
                if not active():
                    break
                atelier_zones.place(session, here["player"], spot)
                if not active():
                    break
                session.inputs(ATTACK)
                pause(2.8)
                continue
            # Monsters standing in an exit stay out of reach from here: look elsewhere.
            wanted = {m["stage"] for m in alive} - {stage}
            step = None
            if patrol:
                step, patrol_index = patrol_step(graph, stage, patrol, patrol_index)
            if step is None:
                for target_stage in sorted(wanted):
                    step = first_hop(graph, stage, target_stage)
                    if step is not None:
                        break
            if step is None:
                step = zone_tour.next_step(graph, visited, stage)
            if step is None:
                # The progress guard ends the hunt if the revisits find nothing.
                step = revisit_step(graph, stage, last_seen)
            if step is None:
                # Arena slay quests have no next area: their small monsters
                # arrive in waves. After confirmed objective progress, keep
                # observing for the next wave instead of reporting no_route
                # between spawns (11101, 2/15 on 2026-10-21). The same budget,
                # cancellation and progress deadline still bound this wait.
                if goal.get("kind") == "slay" and completed is not None and 0 < completed < goal["required"]:
                    if not log["events"] or log["events"][-1].get("kind") != "await_targets":
                        note(kind="await_targets", phase="observing", stage=stage, completed=completed)
                    pause(2)
                    continue
                log.update(state="no_route", reason="no_reachable_target_or_frontier")
                break
            exit = next(e for e in usable if e["destination"] == step)
            note(kind="navigate", phase="navigating", frm=stage, to=step)
            if not active():
                break
            try:
                outcome = atelier_zones.traverse(session, here, exit, walk_in_frames=walk_in_frames, settle=5.0 / speed,
                          on_attempt=lambda attempt: note(kind="exit_attempt", frm=stage, to=step, **attempt))
            except atelier_zones.Unavailable as error:
                # An area or the quest's end loading: the next turn rereads
                # the mission state before anything else.
                read_failures += 1
                note(kind="read_retry", phase="loading", attempt=read_failures, error=str(error))
                if read_failures >= 3:
                    log.update(state="read_failed", reason=str(error))
                    break
                continue
            if not outcome["reached"]:
                failed.add((stage, step))
                graph[stage] = [d for d in graph[stage] if d != step]
            log["stage"] = step if outcome["reached"] else outcome.get("observed_stage", stage)
            note(kind="hop", phase="observing", frm=stage, to=step, **outcome)
        if log["state"] == "running":
            log.update(state="time_limit" if active() else stopped_state(),
                       reason="hunt_budget_exhausted" if active() else "trial_stopped")
    except atelier_zones.Unavailable as error:
        log.update(state="read_failed" if active() else stopped_state(), reason=str(error))
    except atelier_pilot.WaitTimeout as error:
        log.update(state="start_failed", reason=f"timeout: {error.what}")
    except atelier_pilot.Stopped:
        log.update(state=stopped_state(), reason="trial_stopped")
    except KeyboardInterrupt:
        log.update(state="interrupted", reason="keyboard_interrupt")
        raise
    except Exception as error:
        log.update(state="failed", reason=f"{type(error).__name__}: {error}")
        raise
    finally:
        try:
            if log["state"] == "running":
                log.update(state=stopped_state(), reason="trial_stopped")
            if session.alive() and log["mission_state"] is None:
                session.shot(f"h{quest}-stop")
            log["phase"] = "finished"
            note(kind="finished", state=log["state"], reason=log.get("reason"))
            workshop.record_quest_result(session.run_id, {"kind": "hunt", "quest": quest,
                "state": log["state"], "reason": log.get("reason"), "objective": goal,
                "mission_state": log["mission_state"], "success": log["success"],
                "returned_to_village": log["returned_to_village"],
                "objective_progress": log["objective_progress"],
                "objective_progress_available": log["objective_progress_available"],
                "observed_kills": log["kills"], "visual_review": "not_performed", "file": "hunt.json"})
        finally:
            session.stop()
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quests", nargs="+", type=int)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--rtc-us", type=int, default=1609459200123456)
    parser.add_argument("--seconds", type=float, default=360)
    parser.add_argument("--routes", type=Path, help="Prior tour.json for route hints, checked live at each hop")
    parser.add_argument("--species", nargs="+", type=int, help="Only attack these observed species ids")
    parser.add_argument("--no-walk-in", action="store_true", help="Disable bounded arrival walking for comparison")
    parser.add_argument("--stall-seconds", type=float, default=120)
    parser.add_argument("--patrol", nargs="+", type=int, help="Cycle through these areas for respawning small monsters")
    parser.add_argument("--loadout", choices=LOADOUTS, default="best",
                        help="Equipment worn before leaving: the game's best loadout, or the save's own (none)")
    parser.add_argument("--cheats", nargs="*", choices=CHEATS, default=list(DEFAULT_CHEATS),
                        help="Held on the quest (default: %(default)s); none with an empty list")
    parser.add_argument("--speed", type=int, default=1, choices=list(atelier_pilot.SPEEDS),
                        help="1: real time with the frame-space trace (validated runs); 2..8: fast-forward")
    args = parser.parse_args()
    workshop = atelier_core.Workshop(HERE.parents[1])
    for quest in args.quests:
        routes = load_routes(args.routes, quest) if args.routes else None
        result = hunt(workshop, args.seed, quest, args.rtc_us, args.seconds, routes=routes,
                      species=args.species, patrol=args.patrol,
                      stall_seconds=args.stall_seconds, walk_in_frames=0 if args.no_walk_in else 240,
                      loadout=args.loadout, cheats=tuple(args.cheats), speed=args.speed)
        print("HUNT", json.dumps({k: result[k] for k in ("quest", "run_id", "state", "success", "kills")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
