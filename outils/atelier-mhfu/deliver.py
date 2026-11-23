"""Bounded delivery check with supplied test items and the game's red-box interaction.

This validates delivery and return, not gathering: items and camp placement
are supplied through developer commands. Mission state is only read.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import threading
import time

import atelier_campaign
import atelier_core
import atelier_pilot
import atelier_zones as zones

INTERACTION_TABLE, INTERACTION_COUNT, INTERACTION_SIZE = 0x10, 0x3E, 24
DELIVERY_BOX, PLAYER_INTERACTION, QUEST_POUCH = 0x21, 0x554, 0x6AEA6


def objective(snapshot: dict) -> dict:
    """Only one ordinary item-delivery objective; reject transport and hunts."""
    entries = [entry for entry in snapshot["initial"] if entry["flags"]]
    if len(entries) != 1 or entries[0]["flags"] != 0x12:
        raise ValueError("expected one ordinary item-delivery objective")
    entry = entries[0]
    if not 0 < entry["target"] <= 65535 or not 0 < entry["quantity"] <= 99:
        raise ValueError("delivery stack outside the supported range")
    return dict(entry)


def parse_interactions(raw: bytes, address: int) -> list[dict]:
    if len(raw) % INTERACTION_SIZE:
        raise zones.Unavailable("incomplete interaction table")
    entries = []
    for offset in range(0, len(raw), INTERACTION_SIZE):
        identifier, kind, x, y, z, radius, angle = struct.unpack_from("<HH4fH", raw, offset)
        if not all(map(math.isfinite, (x, y, z, radius))) or radius <= 0:
            raise zones.Unavailable("invalid interaction volume")
        entries.append({"address": address + offset, "id": identifier, "type": kind,
                        "position": [x, y, z], "radius": radius, "angle": angle})
    return entries


def delivery_box(session, area: dict) -> dict:
    definitions = area.get("definitions")
    if not definitions or not 0x08800000 <= definitions < 0x09FFFFC0:
        raise zones.Unavailable("area has no supported definitions")
    table_raw, count_raw = zones.read_many(session, [
        (definitions + INTERACTION_TABLE, 4), (definitions + INTERACTION_COUNT, 1)])
    table, count = struct.unpack("<I", table_raw)[0], struct.unpack("<b", count_raw)[0]
    if not 0 < count <= 64 or not 0x08800000 <= table < 0x0A000000 - count * INTERACTION_SIZE:
        raise zones.Unavailable("invalid interaction table range")
    entries = parse_interactions(zones.read(session, table, count * INTERACTION_SIZE), table)
    boxes = [entry for entry in entries if entry["type"] == DELIVERY_BOX]
    if len(boxes) != 1:
        raise zones.Unavailable("expected one red delivery box in the loaded camp")
    return boxes[0]


def deliver(workshop, seed: str, quest: int, *, rtc_us: int = 1609459200123456,
            speed: int = 8, seconds: float = 180, cancel=None) -> dict:
    if speed not in atelier_pilot.SPEEDS or not 60 <= seconds <= 300:
        raise ValueError("speed must be 1..8 and the run budget 60..300 seconds")
    cancel = cancel if cancel is not None else threading.Event()
    timer = threading.Timer(seconds, cancel.set)
    timer.daemon = True
    log = {"quest": quest, "state": "starting", "success": False,
           "returned_to_village": False, "village_ready": False,
           "items_supplied_by_debug_command": True, "camp_placement_supplied": True,
           "gathering_validated": False, "speed": speed, "shots": []}
    session = pilot = None
    began = time.monotonic()
    timer.start()
    try:
        if cancel.is_set():
            raise atelier_pilot.Stopped("cancelled")
        variant = atelier_core.register_rtc_variant("normal", rtc_us)
        scenario = {"name": "campagne", "label": "Delivery interaction check", "fast_boot": True,
                    "debug": True, "dump_overlays": True, "script": atelier_campaign.BOOT,
                    "screenshot_every": 0, "settings": {"video.fast_forward": "toggle",
                    "video.fast_forward_speed": str(speed), "video.fast_loading": "1"}}
        session = atelier_campaign.Session(workshop, seed, seconds + 15, variant=variant, scenario=scenario)
        session.cancel = cancel
        log["run_id"] = session.run_id
        pilot = atelier_pilot.Pilot(session, cancel=cancel)

        def capture(name, frames):
            written = pilot.confirm_shots([pilot.shoot(name, frames)])
            log["shots"] += written
            if name not in written:
                raise zones.Unavailable(f"missing delivery evidence: {name}")

        session.arm_loadout("best")
        pilot.wait_village(60)
        if speed > 1:
            pilot.ensure_fast_forward()
        # Also records what was drawn before departure; not a fix for the startup race.
        capture("delivery-village-before", 60)
        pilot.depart(quest, 60)
        loaded = pilot.wait_quest(quest, 60)
        before = zones.mission_objectives(session, quest)
        goal = objective(before)
        log.update(state="delivering", objective_before=before, supplied_items=goal)
        log["pouch_answer"] = session.ask(f"pouch {goal['target']} {goal['quantity']}", "pouch", 5)
        gamesys = zones.u32(session, zones.GAMESYS_PTR)
        raw = zones.read(session, gamesys + QUEST_POUCH, 96)
        slots = [list(struct.unpack_from("<Hh", raw, index * 4)) for index in range(24)]
        log["quest_pouch_after_command"] = slots
        if [goal["target"], goal["quantity"]] not in slots:
            raise zones.Unavailable("pouch command did not supply the active quest inventory")
        area = zones.current(session)
        box = delivery_box(session, area)
        log["box"] = box
        if area["stage"] != loaded["stage"] or zones.current(session)["stage"] != area["stage"]:
            raise zones.Unavailable("camp changed before delivery")
        x, y, z = box["position"]
        zones.place(session, area["player"], (x - min(50, box["radius"] / 4), y, z))
        pilot.until("red box detected", lambda: zones.u32(session, area["player"] + PLAYER_INTERACTION)
                    == box["address"], 10)
        capture("delivery-box", 1)
        # Real circle input: the game removes the supplied items and checks its objective.
        session.inputs(["0:pad b 6"])
        ended = pilot.until("delivery objective ended", lambda: (lambda snap:
            snap if pilot.mission_over(snap) else None)(pilot.snapshot()), 15)
        after = zones.mission_objectives(session, quest)
        log.update(mission_state=ended["mission"], objective_after=after)
        goal_index = before["initial"].index(goal)
        log["success"] = (ended["mission"] == zones.MISSION_CLEAR
                          and after["current"][goal_index]["target"] == goal["target"]
                          and after["current"][goal_index]["quantity"] <= 0)
        log["state"] = "quest_cleared" if log["success"] else "quest_failed"
        capture("delivery-finished", 15)

        def back():
            snap = pilot.snapshot()
            if snap["task"] == atelier_pilot.VILLAGE_TASK:
                return snap
            pilot.ask(["quest results"], ["results countdown"], 1)
            session.inputs(["0:pad b 6"])
            return None

        pilot.until("delivery return to village", back, 90, poll=0.3)
        log["returned_to_village"] = True
        pilot.wait_village(30)
        log["village_ready"] = True
        capture("delivery-village", 30)
    except (atelier_pilot.Stopped, atelier_pilot.WaitTimeout, zones.Unavailable, ValueError) as error:
        log.update(state="cancelled" if cancel.is_set() else "incomplete", reason=str(error))
    finally:
        timer.cancel()
        log["duration_seconds"] = round(time.monotonic() - began, 2)
        try:
            if session:
                log["waits"] = pilot.waits if pilot else []
                atelier_core._write_json(session.dir / "delivery.json", log)
        finally:
            workshop.stop()
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quest", type=int)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--rtc-us", type=int, default=1609459200123456)
    parser.add_argument("--speed", type=int, choices=atelier_pilot.SPEEDS, default=8)
    parser.add_argument("--seconds", type=float, default=180)
    args = parser.parse_args()
    result = deliver(atelier_core.Workshop(Path(__file__).resolve().parents[2]), args.seed, args.quest,
                     rtc_us=args.rtc_us, speed=args.speed, seconds=args.seconds)
    print("DELIVERY", json.dumps(result), flush=True)
    return 0 if result["state"] == "quest_cleared" and result["success"] and result["village_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
