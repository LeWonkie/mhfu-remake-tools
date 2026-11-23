"""Bounded village survey for step 7: where the shop and the forge stand.

Reads the village's interaction table (the layout the delivery check uses in
camps), moves the hunter beside each entry and captures it. Nothing is bought,
crafted or saved; money and pouch are only read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import threading
import time

import atelier_campaign
import atelier_core
import atelier_pilot
import atelier_zones as zones
import deliver

# lobby_task.ovl (mhp2g-decomp: Singleton<NpcManager>, __vt__3Npc); fields read in a village dump.
NPC_MANAGER_PTR, NPC_LIST, NPC_SLOTS, NPC_VTABLE = 0x09B1E708, 0xC, 14, 0x089B6100
NPC_KIND = 0x1E6  # u16, distinct per village NPC
EQUIPMENT_BOX, EQUIPMENT_BOX_SIZE = 0x4A0 + 0xA8, 1000 * 12  # GameSys: UserData's equipment box


def npcs(session) -> list[dict]:
    manager = zones.u32(session, NPC_MANAGER_PTR)
    if not 0x08800000 <= manager < 0x0A000000:
        raise zones.Unavailable("NPC manager not loaded")
    pointers = struct.unpack(f"<{NPC_SLOTS}I", zones.read(session, manager + NPC_LIST, 4 * NPC_SLOTS))
    found = []
    for slot, npc in enumerate(pointers):
        if not 0x08800000 <= npc < 0x0A000000:
            continue
        vtable, position, kind = zones.read_many(session, [(npc, 4), (npc + zones.POSITION, 12), (npc + NPC_KIND, 2)])
        found.append({"slot": slot, "address": npc, "vtable": struct.unpack("<I", vtable)[0],
                      "position": list(struct.unpack("<3f", position)), "kind": struct.unpack("<H", kind)[0]})
    return found


def interactions(session, area: dict) -> list[dict]:
    definitions = area.get("definitions")
    if not definitions or not 0x08800000 <= definitions < 0x09FFFFC0:
        raise zones.Unavailable("village has no supported definitions")
    table_raw, count_raw = zones.read_many(session, [
        (definitions + deliver.INTERACTION_TABLE, 4), (definitions + deliver.INTERACTION_COUNT, 1)])
    table, count = struct.unpack("<I", table_raw)[0], struct.unpack("<b", count_raw)[0]
    if not 0 < count <= 64 or not 0x08800000 <= table < 0x0A000000 - count * deliver.INTERACTION_SIZE:
        raise zones.Unavailable(f"invalid interaction table range ({table:#x}, {count})")
    return deliver.parse_interactions(
        zones.read(session, table, count * deliver.INTERACTION_SIZE), table)


def survey(workshop, seed: str, *, rtc_us: int = 1609459200123456, speed: int = 8,
           seconds: float = 120, limit: int = 12, dump: str | None = None, talk: bool = False,
           kinds: tuple = (), presses: int = 1, sequence: list | None = None,
           commands: list | None = None, cancel=None) -> dict:
    if speed not in atelier_pilot.SPEEDS or not 60 <= seconds <= 300:
        raise ValueError("speed must be 1..8 and the run budget 60..300 seconds")
    cancel = cancel if cancel is not None else threading.Event()
    timer = threading.Timer(seconds, cancel.set)
    timer.daemon = True
    log = {"state": "starting", "speed": speed, "shots": [], "visits": []}
    session = pilot = None
    began = time.monotonic()
    timer.start()
    try:
        variant = atelier_core.register_rtc_variant("normal", rtc_us)
        scenario = {"name": "campagne", "label": "Village NPC survey", "fast_boot": True,
                    "debug": True, "dump_overlays": True, "script": atelier_campaign.BOOT,
                    "screenshot_every": 0, "settings": {"video.fast_forward": "toggle",
                    "video.fast_forward_speed": str(speed), "video.fast_loading": "1"}}
        session = atelier_campaign.Session(workshop, seed, seconds + 15, variant=variant, scenario=scenario)
        session.cancel = cancel
        log["run_id"] = session.run_id
        pilot = atelier_pilot.Pilot(session, cancel=cancel)

        def capture(name, frames):
            log["shots"] += pilot.confirm_shots([pilot.shoot(name, frames)])

        pilot.wait_village(60)
        if speed > 1:
            pilot.ensure_fast_forward()
        log["state_answer"] = session.ask("state", "[debug] hunter ", 6)
        log["pouch_answer"] = session.ask("pouch", "pouch", 6)
        capture("village-arrival", 30)
        area = zones.current(session)
        log.update(state="surveying", stage=area["stage"], start=list(area["position"]),
                   definitions=area["definitions"], exits=area["exits"])
        log["player"] = area["player"]
        gamesys = zones.u32(session, zones.GAMESYS_PTR)
        log["equipment_box_before"] = zones.read(session, gamesys + EQUIPMENT_BOX, EQUIPMENT_BOX_SIZE).hex()
        # Test supplies (money, items): answers kept, never a proof of normal play.
        log["commands"] = [{"command": line, "answer": session.ask(line, "[debug]", 6)} for line in commands or []]
        if dump:
            # Whole user memory for offline search of the village's NPC objects.
            log["dump_answer"] = session.ask(f"dump {dump} 0x08800000 0x01800000", "dumped", 30)
        entries = interactions(session, area)
        log["interactions"] = entries
        for index, entry in enumerate(entries[:limit]):
            x, y, z = entry["position"]
            zones.place(session, area["player"], (x - min(50, entry["radius"] / 4), y, z))
            time.sleep(0.5)
            detected = zones.u32(session, area["player"] + deliver.PLAYER_INTERACTION)
            name = f"npc-{index:02d}-type{entry['type']:02x}"
            capture(name, 2)
            log["visits"].append({"index": index, "type": entry["type"], "id": entry["id"],
                                  "detected": detected == entry["address"], "shot": name})
        if talk:
            # Chosen NPCs (all by default): stand beside it, then ○ PRESSES times with a
            # capture and the lobby scene word after each, to follow dialogue into a menu.
            log["npcs"] = npcs(session)
            log["talks"] = []
            for npc in log["npcs"]:
                if npc["vtable"] != NPC_VTABLE or (kinds and npc["kind"] not in kinds):
                    continue
                x, y, z = npc["position"]
                zones.place(session, area["player"], (x + 80, y, z))
                time.sleep(0.6)
                name = f"talk-{npc['slot']:02d}-kind{npc['kind']:02d}"
                capture(name + "-before", 2)
                steps = []
                for press, button in enumerate(sequence or ["b"] * presses):
                    session.inputs([f"0:pad {button} 6"])
                    time.sleep(1.2)
                    shot = f"{name}-{press:02d}"
                    capture(shot, 2)
                    steps.append({"shot": shot, "button": button, "scene": pilot.snapshot().get("scene"),
                                  "money": session.ask("state", "[debug] hunter ", 6)})
                session.inputs([f"{i * 20}:pad a 6" for i in range(8)])
                time.sleep(2.5)
                capture(name + "-after", 2)
                log["talks"].append({"slot": npc["slot"], "kind": npc["kind"], "steps": steps,
                                     "scene_after": pilot.snapshot().get("scene")})
        log["equipment_box_after"] = zones.read(session, gamesys + EQUIPMENT_BOX, EQUIPMENT_BOX_SIZE).hex()
        log["state_after"] = session.ask("state", "[debug] hunter ", 6)
        log["state"] = "surveyed"
    except (atelier_pilot.Stopped, atelier_pilot.WaitTimeout, zones.Unavailable, ValueError) as error:
        log.update(state="cancelled" if cancel.is_set() else "incomplete", reason=str(error))
    finally:
        timer.cancel()
        log["duration_seconds"] = round(time.monotonic() - began, 2)
        try:
            if session:
                log["waits"] = pilot.waits if pilot else []
                atelier_core._write_json(session.dir / "village_survey.json", log)
        finally:
            workshop.stop()
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--rtc-us", type=int, default=1609459200123456)
    parser.add_argument("--speed", type=int, choices=atelier_pilot.SPEEDS, default=8)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--talk", action="store_true", help="talk to each NPC and capture what opens")
    parser.add_argument("--kinds", type=int, nargs="*", default=[], help="NPC kinds to talk to (all by default)")
    parser.add_argument("--presses", type=int, default=1, help="○ presses per NPC")
    parser.add_argument("--sequence", help="buttons pressed in turn instead of ○ (comma separated, e.g. b,b,r,a)")
    parser.add_argument("--command", action="append", default=[], help="debug command sent on arrival (repeatable)")
    parser.add_argument("--dump", help="file for a user-memory dump taken on arrival (path without spaces)")
    args = parser.parse_args()
    result = survey(atelier_core.Workshop(Path(__file__).resolve().parents[2]), args.seed,
                    rtc_us=args.rtc_us, speed=args.speed, seconds=args.seconds, limit=args.limit, dump=args.dump, talk=args.talk,
                    kinds=tuple(args.kinds), presses=args.presses,
                    sequence=args.sequence.split(",") if args.sequence else None, commands=args.command)
    print("SURVEY", json.dumps(result), flush=True)
    return 0 if result["state"] == "surveyed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
