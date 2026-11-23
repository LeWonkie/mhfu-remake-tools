"""Observe candidate objective descriptors during a real, bounded hunt.

Diagnostic only: Quest +0x728 contains two eight-byte descriptors and +0x738
their initial copies (ELF initializer 0x0886E2E4). No objective is written.
The menu captures provide an independent visible check of the quantities.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quest", type=int, nargs="?", default=11001, choices=(11001, 21001))
    parser.add_argument("--partial", action="store_true", help="Stop after the intermediate menu capture")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                      GIT_CONFIG_VALUE_0=str(root / "psprecomp-mhfu"))
    workshop = atelier_core.Workshop(root)
    holder, observations = [], []
    captured, previous = set(), None
    cancel = threading.Event()
    required = hunt.resolve_objective(args.quest)["required"]

    class ProbeSession(atelier_campaign.Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder.append(self)

    def progress(event):
        nonlocal previous
        if not holder or event.get("phase") in ("booting", "departing", "finished"):
            return
        session = holder[0]
        if not session.alive():
            return
        try:
            quest = zones.u32(session, zones.QUEST_PTR)
            if not 0x08800000 <= quest <= 0x09FFF000:
                raise zones.Unavailable("invalid Quest pointer")
            raw, after = zones.read_many(session, [(quest + 0x728, 32), (zones.QUEST_PTR, 4)])
            if struct.unpack("<I", after)[0] != quest:
                raise zones.Unavailable("Quest pointer changed")
            entries = [list(struct.unpack_from("<IHh", raw, offset)) for offset in range(0, 32, 8)]
            if entries != previous:
                previous = entries
                sample = {"elapsed": event.get("elapsed_seconds"), "phase": event.get("phase"),
                          "quest_address": quest, "entries_flags_id_quantity": entries}
                observations.append(sample)
                atelier_core._write_json(session.dir / "objective_probe.json", observations)
                print("OBJECTIVE", json.dumps(sample), flush=True)
            remaining = entries[0][2]
            label = "initial" if remaining == required else "middle" if 0 < remaining <= required // 2 else None
            if label and label not in captured and event.get("phase") not in ("results", "loading"):
                captured.add(label)
                # The menu remembers Quest Info after the first inspection.
                select = ["12:pad dpdown 4", "24:pad dpdown 4"] if label == "initial" else []
                session.inputs(["0:pad start 4", *select, "36:pad b 4",
                                f"70:shot objective-{label}", "90:pad a 4", "102:pad start 4"])
                time.sleep(5)
                if label == "middle" and args.partial:
                    cancel.set()
        except zones.Unavailable as error:
            print("OBJECTIVE_RETRY", str(error), flush=True)

    with patch.object(atelier_campaign, "Session", ProbeSession):
        result = hunt.hunt(workshop, "20261020-153034-ceda7b", args.quest,
                           1609459200123456, 600, progress=progress, cancel=cancel)
    print("OBJECTIVE_RESULT", result["run_id"], result["state"], flush=True)
    if args.partial:
        return 0 if result["state"] == "cancelled" and "middle" in captured else 1
    return 0 if result["state"] == "quest_cleared" and result["returned_to_village"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
