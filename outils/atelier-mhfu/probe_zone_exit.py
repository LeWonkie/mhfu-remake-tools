"""One area change through the game's own exit, on a fresh trial (read and
check first, then move the hunter into the exit). Prints a JSON summary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import atelier_campaign  # noqa: E402
import atelier_core  # noqa: E402
import atelier_zones  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--quest", type=int, default=10001)
    parser.add_argument("--rtc-us", type=int, default=1609459200123456)
    parser.add_argument("--hops", type=int, default=1)
    args = parser.parse_args()
    atelier_core.MAX_SECONDS = 420
    workshop = atelier_core.Workshop(HERE.parents[1])
    variant = atelier_core.register_rtc_variant("space_trace", args.rtc_us)
    session = atelier_campaign.Session(workshop, args.seed, atelier_core.MAX_SECONDS, variant=variant)
    log = {"run_id": session.run_id, "quest": args.quest, "hops": []}
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
        for hop in range(args.hops):
            here = atelier_zones.current(session)
            entry = {"stage": here["stage"], "position": here["position"],
                     "exits": [{"to": e["destination"], "shape": e["shape"], "p": e["p"], "size": e["size"],
                                "height": e["height"]} for e in here["exits"]]}
            print(json.dumps(entry), flush=True)
            session.shot(f"z{hop:02d}-{here['stage']}")
            if not here["exits"]:
                entry["result"] = "no exits"
                log["hops"].append(entry)
                break
            target = here["exits"][hop % len(here["exits"])]
            atelier_zones.go_through(session, here, target)
            reached = atelier_zones.wait_stage(session, target["destination"])
            entry["went_to"] = target["destination"]
            entry["reached"] = reached
            log["hops"].append(entry)
            print("hop", here["stage"], "->", target["destination"], "reached" if reached else "NOT reached", flush=True)
            if not reached:
                break
            time.sleep(6)
        session.shot("zfinal")
        time.sleep(1)
    finally:
        session.stop()
        out = workshop.data_dir / "runs" / log["run_id"] / "zone_probe.json"
        out.write_text(json.dumps(log, indent=2), encoding="utf-8")
        print("PROBE DONE", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
