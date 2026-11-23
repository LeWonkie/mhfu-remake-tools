"""Find the hunter's stamina and sharpness fields during a real, bounded hunt.

Diagnostic only: dumps the local Player object (0x800 bytes), the pouch
(UserData +0x3F28, InventoryEntry[24] in the MHP2G decompilation) and the
zenny. Stamina: rested, sprinting (R held while moving), rested again.
Sharpness: one dump at each attack of the hunt controller. Nothing is written,
except with --verify: test values poked into the candidate fields of this
trial's memory, each followed by a window capture of the HUD.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import threading
import time
from unittest.mock import patch

import atelier_campaign
import atelier_core
import atelier_zones as zones
import hunt

PLAYER_SIZE = 0x800
# Candidates from the first pass (run 20261021-080405-b041cb): +0x55A 300, 227
# sprinting, 300 rested again; +0x41C 100 then 81 and 62, beside +0x41E 100.
CANDIDATES = [(0x55A, 30, "stamina30"), (0x41C, 40, "f41c-40")]
USER_DATA, POUCH, FUNDS = 0x4A0, 0x3F28, 0x69250
SPRINT = ["0:pad rightshoulder 200", "0:axis righttrigger 1", "0:axis lefty -1",
          "200:axis lefty 0", "200:axis righttrigger 0"]


def snapshot(session):
    gamesys, manager = (struct.unpack("<I", data)[0] for data in zones.read_many(
        session, [(zones.GAMESYS_PTR, 4), (zones.PLAYER_MANAGER_PTR, 4)]))
    index = zones.read(session, gamesys + zones.LOCAL_PLAYER, 1)[0]
    player = zones.u32(session, manager + zones.PLAYER_LIST + 4 * index)
    if not 0x08800000 <= player < 0x0A000000:
        raise zones.Unavailable("no player")
    body, pouch, funds = zones.read_many(session, [
        (player, PLAYER_SIZE), (gamesys + USER_DATA + POUCH, 96), (gamesys + USER_DATA + FUNDS, 4)])
    return {"player": player, "body": body.hex(),
            "pouch": [list(struct.unpack_from("<Hh", pouch, i * 4)) for i in range(24)],
            "funds": struct.unpack("<i", funds)[0]}


def main():
    global PLAYER_SIZE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Poke the candidates and capture the HUD")
    args = parser.parse_args()
    if args.verify:
        PLAYER_SIZE = 0x2000
    root = Path(__file__).resolve().parents[2]
    os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                      GIT_CONFIG_VALUE_0=str(root / "psprecomp-mhfu"))
    workshop = atelier_core.Workshop(root)
    holder, dumps = [], []
    cancel = threading.Event()

    class ProbeSession(atelier_campaign.Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder.append(self)

    def keep(session, label, event):
        sample = snapshot(session)
        sample.update(label=label, elapsed=event.get("elapsed_seconds"), phase=event.get("phase"))
        dumps.append(sample)
        atelier_core._write_json(session.dir / "cheat_fields.json", dumps)
        print("DUMP", label, sample["elapsed"], flush=True)

    def progress(event):
        if not holder or event.get("phase") in ("booting", "departing", "finished", "results", "loading"):
            return
        session = holder[0]
        if not session.alive():
            return
        try:
            if not dumps and args.verify:
                keep(session, "rested", event)
                player = dumps[0]["player"]
                session.shot("hud-rest")
                for offset, value, label in CANDIDATES:
                    before = struct.unpack_from("<h", bytes.fromhex(dumps[0]["body"]), offset)[0]
                    print("POKE", label, session.ask(f"poke16 0x{player + offset:08X} {value}", "poke16", 4.0), flush=True)
                    session.shot("hud-" + label)
                    after = struct.unpack("<h", zones.read(session, player + offset, 2))[0]
                    print("READBACK", label, before, value, after, flush=True)
                    time.sleep(3)
                    later = struct.unpack("<h", zones.read(session, player + offset, 2))[0]
                    print("LATER", label, later, flush=True)
                    session.shot("hud-" + label + "-later")
            elif not dumps:
                keep(session, "rested", event)
                session.inputs(SPRINT)
                time.sleep(5)
                keep(session, "sprinting", event)
                time.sleep(12)
                keep(session, "recovered", event)
            elif event.get("kind") == "attack" or event.get("phase") == "combat":
                keep(session, "attack", event)
                if sum(d["label"] == "attack" for d in dumps) >= 30:
                    cancel.set()
        except zones.Unavailable as error:
            print("DUMP_RETRY", str(error), flush=True)

    with patch.object(atelier_campaign, "Session", ProbeSession):
        result = hunt.hunt(workshop, "20261020-153034-ceda7b", 11001,
                           1609459200123456, 240, progress=progress, cancel=cancel)
    print("CHEAT_FIELDS", result["run_id"], result["state"], len(dumps), flush=True)
    return 0 if len(dumps) >= 4 else 1


if __name__ == "__main__":
    raise SystemExit(main())
