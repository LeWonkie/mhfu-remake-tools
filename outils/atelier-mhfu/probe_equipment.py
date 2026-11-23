"""Read the hunter's equipment in the village (diagnostic only, nothing written).

UserData (GameSys +0x4A0) as the MHP2G decompilation lays it out:
equipment[6] at +0x20 and equipmentBox[1000] at +0xA8, twelve bytes each
(type u8, u8, id u16, upgrades u16, three decorations u16).
"""
import json
import os
from pathlib import Path
import struct
import time

import atelier_campaign
import atelier_core
import atelier_zones as zones

USER_DATA, WORN, BOX = 0x4A0, 0x20, 0xA8


def entries(data):
    return [list(struct.unpack_from("<BBHHhhh", data, i)) for i in range(0, len(data), 12)]


def main():
    root = Path(__file__).resolve().parents[2]
    os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                      GIT_CONFIG_VALUE_0=str(root / "psprecomp-mhfu"))
    workshop = atelier_core.Workshop(root)
    session = atelier_campaign.Session(workshop, "20261020-153034-ceda7b", 180)
    try:
        if not session.wait_task(10, 120):
            print("EQUIPMENT no village", flush=True)
            return 1
        time.sleep(3)
        sys = zones.u32(session, zones.GAMESYS_PTR)
        worn, box = zones.read_many(session, [(sys + USER_DATA + WORN, 72), (sys + USER_DATA + BOX, 12 * 200)])
        result = {"worn": entries(worn), "box": [e for e in entries(box) if e[0] or e[2]]}
        atelier_core._write_json(session.dir / "equipment.json", result)
        print("EQUIPMENT", session.run_id, json.dumps(result), flush=True)
        session.shot("village")
        # User memory, for finding the equipment tables the game loads.
        print("DUMP", session.ask("dump ram-village.bin 0x08800000 0x1800000", "dumped", 30.0), flush=True)
        print("START", session.ask("quest start 1001", "quest", 6.0), flush=True)
        if not session.wait_task(11, 90, quest=1001):
            return 1
        time.sleep(8)
        print("DUMP", session.ask("dump ram-quest.bin 0x08800000 0x1800000", "dumped", 30.0), flush=True)
        # Candidate sharpness (Player +0x10F2, one less per hit landed in run
        # 20261021-080816-ac6b86): poke a low value and look at the HUD gauge.
        manager = zones.u32(session, zones.PLAYER_MANAGER_PTR)
        index = zones.read(session, sys + zones.LOCAL_PLAYER, 1)[0]
        player = zones.u32(session, manager + zones.PLAYER_LIST + 4 * index)
        sharp = player + 0x10F2
        before = struct.unpack("<h", zones.read(session, sharp, 2))[0]
        session.shot("sharp-before")
        for value in (40, 3):
            print("POKE", session.ask(f"poke16 0x{sharp:08X} {value}", "poke16", 4.0), flush=True)
            time.sleep(1)
            session.shot(f"sharp-{value}")
        print("SHARP", before, struct.unpack("<h", zones.read(session, sharp, 2))[0], flush=True)
        return 0
    finally:
        session.stop()


if __name__ == "__main__":
    raise SystemExit(main())
