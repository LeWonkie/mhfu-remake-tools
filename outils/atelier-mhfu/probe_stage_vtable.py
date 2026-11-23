"""Print the code of the current area's definitions()/exits()/exit_count()
on a quest (read only), for areas whose exits are not a constant table."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import atelier_campaign  # noqa: E402
import atelier_core  # noqa: E402
import atelier_zones as z  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--quest", type=int, required=True)
    parser.add_argument("--rtc-us", type=int, default=1609459200123456)
    args = parser.parse_args()
    atelier_core.MAX_SECONDS = 300
    workshop = atelier_core.Workshop(HERE.parents[1])
    variant = atelier_core.register_rtc_variant("normal", args.rtc_us)
    session = atelier_campaign.Session(workshop, args.seed, atelier_core.MAX_SECONDS, variant=variant)
    try:
        session.wait_task(10, 150)
        time.sleep(8)
        for _ in range(20):
            answer = session.ask(f"quest start {args.quest}", "quest", 6.0)
            if not answer or "started quest" in answer:
                break
            time.sleep(2)
        session.wait_task(11, 90, quest=args.quest)
        time.sleep(8)
        here = z.current(session)
        out = {"stage": here["stage"], "slots": {}}
        for name, function in here["vtable_slots"].items():
            words = struct.unpack("<24I", z.read(session, function, 96))
            out["slots"][name] = {"address": f"0x{function:08X}", "words": [f"{w:08X}" for w in words]}
        manager = z.u32(session, z.STAGE_MANAGER_PTR)
        out["map_id"] = z.u32(session, manager + 0x284)
        print(json.dumps(out, indent=1), flush=True)
    finally:
        session.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
