"""Bounded delivery investigation; supplies test items but never writes mission state."""
import json
import argparse
from pathlib import Path
import sys
import struct
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outils/atelier-mhfu"))
import atelier_campaign
import atelier_core
import atelier_pilot
import atelier_zones as zones


def inspect_objects(session):
    """Read local CC0 decomp layout constants; no guest object is modified."""
    manager = zones.u32(session, 0x08A5DD2C)
    if not 0x08800000 <= manager < 0x0A000000:
        return []
    heads = struct.unpack("<15I", zones.read(session, manager + 0x80028, 60))
    found, seen = [], set()
    for group, head in enumerate(heads):
        address = head
        while 0x08800000 <= address < 0x09FFF000 and address not in seen and len(seen) < 80:
            seen.add(address)
            raw = zones.read(session, address, 0x29C)
            found.append({"group": group, "address": address, "vtable": struct.unpack_from("<I", raw)[0],
                "position": struct.unpack_from("<3f", raw, 0x200), "type": raw[0x1E8],
                "kind": raw[0x1E9], "stage": struct.unpack_from("<H", raw, 0x29A)[0]})
            address = struct.unpack_from("<I", raw, 0x1C4)[0]
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=180, choices=range(60, 301))
    parser.add_argument("--auto-box", action="store_true")
    parser.add_argument("--slow-interaction", action="store_true")
    parser.add_argument("--live-pouch", action="store_true")
    args = parser.parse_args()
    workshop = atelier_core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(args.limit, cancel.set)
    timer.daemon = True
    folder = ROOT / "work/codex-delivery-probe"
    folder.mkdir(exist_ok=True)
    timer.start()
    session = None
    observations = []
    diagnostics = []
    before = answer = initial = state = None
    try:
        variant = atelier_core.register_rtc_variant("normal", 1609459200123456)
        scenario = {"name": "campagne", "label": "Delivery box investigation", "fast_boot": True,
                    "debug": True, "dump_overlays": True, "script": atelier_campaign.BOOT,
                    "screenshot_every": 0,
                    "settings": {"video.fast_forward": "toggle", "video.fast_forward_speed": "8",
                                 "video.fast_loading": "1"}}
        session = atelier_campaign.Session(workshop, "20261020-153034-ceda7b", 240,
                                           variant=variant, scenario=scenario)
        session.cancel = cancel
        pilot = atelier_pilot.Pilot(session, cancel=cancel)
        session.arm_loadout("best")
        pilot.wait_village(60)
        pilot.ensure_fast_forward()
        pilot.confirm_shots([pilot.shoot("village-before-depart", 60)])
        pilot.depart(10001, 60)
        pilot.wait_quest(10001, 60)
        before = zones.mission_objectives(session, 10001)
        if before["initial"][0] != {"flags": 18, "target": 173, "quantity": 5}:
            raise RuntimeError("Unexpected delivery objective; no items inserted")
        answer = session.ask("pouch 173 5", "pouch", 5)
        gamesys = zones.u32(session, zones.GAMESYS_PTR)
        live_address = gamesys + 0x6AEA6  # GameSys::bag entry getter at ELF 0x088567AC
        saved_address = gamesys + 0x4A0 + 0x3F28
        def slots(address):
            raw = zones.read(session, address, 96)
            return [list(struct.unpack_from("<Hh", raw, i*4)) for i in range(24)]
        bags = {"live_before": slots(live_address), "saved_after_command": slots(saved_address)}
        if args.live_pouch:
            index = next(i for i,(item,count) in enumerate(bags["live_before"]) if item == 0)
            workshop.debug_commands([f"poke16 0x{live_address + 4*index:08X} 173",
                                     f"poke16 0x{live_address + 4*index + 2:08X} 5"])
            bags["live_after_test_insertion"] = slots(live_address)
            if bags["live_after_test_insertion"][index] != [173,5]:
                raise RuntimeError("Live pouch insertion not confirmed")
        atelier_core._write_json(session.dir / "pouch_comparison.json", bags)
        print("POUCH_COMPARISON", json.dumps(bags), flush=True)
        initial = zones.current(session)
        atelier_core._write_json(session.dir / "camp_objects.json", inspect_objects(session))
        pilot.confirm_shots([pilot.shoot("delivery-initial", 30)])
        if args.auto_box:
            zones.place(session, initial["player"], (14950, 1250, 15850))
            def detected():
                pointer = zones.u32(session, initial["player"] + 0x554)
                return pointer if 0x08800000 <= pointer < 0x09FFFF00 else None
            pointer = pilot.until("delivery box detected", detected, 10)
            print("BOX_DETECTED", hex(pointer), flush=True)
            if args.slow_interaction:
                session.inputs(["0:pad guide 4"])
                pilot.until("fast forward disabled", lambda: (pilot.drain() or not pilot.ff_active), 5)
            session.inputs(["30:pad b 180"])
            pilot.confirm_shots([pilot.shoot("delivery-open-attempt", 60)])
        control = folder / "control.json"
        last = json.loads(control.read_text(encoding="utf-8"))["id"] if control.is_file() else None
        print("DELIVERY_READY", json.dumps({"run_id": session.run_id, "area": initial, "pouch": answer}), flush=True)
        while not cancel.is_set() and session.alive():
            try:
                area = zones.current(session, timeout=3)
                mission = zones.mission_state(session)
            except zones.Unavailable:
                if cancel.is_set():
                    break
                raise
            state = {"run_id": session.run_id, "area": area, "mission": mission,
                     "rotation": list(struct.unpack("<4i", zones.read(session, area["player"] + 0x1F0, 16))),
                     "objectives": zones.mission_objectives(session, 10001)}
            raw_buttons, raw_interaction, raw_attributes, raw_action = zones.read_many(session, [
                (area["player"] + 0x480, 4), (area["player"] + 0x550, 16),
                (area["player"] + 0x614, 4), (area["player"] + 0x298, 2)])
            interaction = struct.unpack_from("<I", raw_interaction, 4)[0]
            state["player_diagnostic"] = {"buttons": struct.unpack("<I", raw_buttons)[0],
                "at_550": raw_interaction.hex(), "stage_unique": interaction,
                "attributes": struct.unpack("<I", raw_attributes)[0], "action": list(raw_action)}
            cockpit = zones.u32(session, 0x089C6CBC)
            if 0x08800000 <= cockpit < 0x09FFF000:
                state["cockpit_diagnostic"] = {"address": cockpit,
                    "delivery_open": zones.read(session, cockpit + 0x554, 1)[0]}
            if 0x08800000 <= interaction < 0x09FFFF00:
                state["player_diagnostic"]["interaction_data"] = zones.read(session, interaction, 24).hex()
            diagnostic = {"player": state["player_diagnostic"], "cockpit": state.get("cockpit_diagnostic")}
            if not diagnostics or diagnostic != diagnostics[-1]:
                diagnostics.append(diagnostic)
                atelier_core._write_json(session.dir / "delivery_input_history.json", diagnostics)
            atelier_core._write_json(folder / "state.json", state)
            if zones.mission_over(mission):
                pilot.confirm_shots([pilot.shoot("delivery-finished", 15)])
                print("DELIVERY_ENDED", json.dumps(state), flush=True)
                break
            if control.is_file():
                command = json.loads(control.read_text(encoding="utf-8"))
                if command["id"] != last:
                    last = command["id"]
                    if command.get("stop"):
                        break
                    if "position" in command:
                        point = command["position"]
                        if area["stage"] != initial["stage"] or any(abs(a-b)>4000 for a,b in zip(point, initial["position"])):
                            raise ValueError("Probe movement outside the original camp")
                        zones.place(session, area["player"], point)
                    if "yaw" in command:
                        yaw = command["yaw"]
                        if not isinstance(yaw, int) or not 0 <= yaw <= 65535:
                            raise ValueError("Invalid heading")
                        session.ask(f"poke32 0x{area['player'] + 0x1F4:08X} {yaw}", "[debug]", 3)
                    if "inputs" in command:
                        atelier_core.validate_script(";".join(command["inputs"]))
                        session.inputs(command["inputs"])
                    shot = pilot.shoot("delivery-step-" + str(last), 60)
                    pilot.confirm_shots([shot])
                    observations.append({"command": command, "shot": shot})
                    print("DELIVERY_STEP", last, flush=True)
            cancel.wait(0.3)
    finally:
        timer.cancel()
        if session:
            atelier_core._write_json(session.dir / "delivery_probe.json", {"objective_before": before,
                "pouch_answer": answer, "initial_area": initial, "observations": observations,
                "diagnostics": diagnostics,
                "final": state, "cancelled": cancel.is_set(), "items_supplied_by_debug_command": True})
        workshop.stop()


if __name__ == "__main__":
    main()
