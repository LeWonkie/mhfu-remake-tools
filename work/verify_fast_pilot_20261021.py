"""Bounded real-game checks for the fast pilot handoff (private Workshop runs)."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "outils/atelier-mhfu"))
import atelier_core
import atelier_pilot
import hunt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("survey", "hunt"))
    parser.add_argument("quests", nargs="+", type=int)
    parser.add_argument("--seconds", type=float, default=300)
    parser.add_argument("--limit", type=float, default=420)
    parser.add_argument("--speed", type=int, default=8)
    args = parser.parse_args()
    workshop = atelier_core.Workshop(PROJECT)
    cancel = threading.Event()
    timer = threading.Timer(args.limit, cancel.set)
    timer.daemon = True
    summary = {"phase": args.phase, "quests": args.quests, "speed": args.speed,
               "limit_seconds": args.limit, "seed": "20261020-153034-ceda7b",
               "rtc_us": 1609459200123456, "results": []}
    began = time.monotonic()
    timer.start()
    try:
        if args.phase == "survey":
            folder = atelier_pilot.run_campaign(workshop, args.quests, seed_id=summary["seed"],
                rtc_us=summary["rtc_us"], speed=args.speed, cancel=cancel, force=True,
                log=lambda line: print(line, flush=True))
            summary["campaign"] = str(folder)
            summary["results"] = json.loads((folder / "campagne.json").read_text(encoding="utf-8"))["results"]
        else:
            for quest in args.quests:
                if cancel.is_set():
                    break
                result = hunt.hunt(workshop, summary["seed"], quest, summary["rtc_us"],
                    args.seconds, speed=args.speed, cancel=cancel)
                summary["results"].append({key: result.get(key) for key in
                    ("quest", "run_id", "state", "reason", "success", "mission_state",
                     "returned_to_village", "elapsed_seconds", "objective_progress", "fast_forward")})
    finally:
        timer.cancel()
        workshop.stop()
        summary["wall_seconds"] = round(time.monotonic() - began, 3)
        summary["cancelled"] = cancel.is_set()
        folder = PROJECT / "work/codex-fast-pilot-game-checks"
        folder.mkdir(exist_ok=True)
        output = folder / (time.strftime("%Y%m%d-%H%M%S") + "-" + args.phase + ".json")
        atelier_core._write_json(output, summary)
        print("SUMMARY", output, json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
