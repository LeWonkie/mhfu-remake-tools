"""Open a quest's in-game menu on a fresh, bounded, read-only inspection trial."""
import argparse
import json
import os
from pathlib import Path
import time

import atelier_campaign as campaign
import atelier_core as core
import atelier_zones as zones


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quest", type=int)
    parser.add_argument("--capture-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                      GIT_CONFIG_VALUE_0=str(root / "psprecomp-mhfu"))
    core.MAX_SECONDS = 360
    workshop = core.Workshop(root)
    session = campaign.Session(workshop, "20261020-153034-ceda7b", 360,
                               variant=core.register_rtc_variant("normal", 1609459200123456))
    print("RUN", session.dir, flush=True)
    try:
        if not session.wait_task(10, 150):
            raise RuntimeError("village not reached")
        time.sleep(8)
        print(session.ask(f"quest start {args.quest}", "quest", 6), flush=True)
        if not session.wait_task(11, 90, quest=args.quest):
            raise RuntimeError("quest not reached")
        time.sleep(8)
        here = zones.current(session)
        quest = zones.u32(session, zones.QUEST_PTR)
        info = {"quest": args.quest, "area": here, "quest_address": quest,
                "header": zones.read(session, quest, 48).hex(), "targets": zones.quest_targets(session)}
        (session.dir / "quest_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        if args.capture_only:
            session.inputs(["0:pad start 4", "12:pad dpdown 4", "24:pad dpdown 4", "36:pad b 4",
                            "70:shot quest-objective", "90:pad dpright 4", "120:shot quest-objective-2"])
            time.sleep(4)
            return
        session.inputs(["0:pad start 4", "30:shot quest-menu"])
        print("READY", session.dir, flush=True)
        deadline = time.monotonic() + 240
        while session.alive() and time.monotonic() < deadline:
            time.sleep(1)
    finally:
        session.stop()


if __name__ == "__main__":
    main()
