"""Run one bounded survey batch from the admission plan, preserving original saves."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outils/atelier-mhfu"))
import atelier_core as core
import atelier_pilot as pilot
import atelier_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--batch", type=int)
    selection.add_argument("--quests", nargs="+", type=int)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--profile", action="store_true", help="record existing per-phase wall/CPU/perf measurements")
    args = parser.parse_args()
    plan = atelier_plan.load_plan(ROOT)
    batch = args.batch if args.batch is not None else 1
    if not 1 <= batch <= len(plan["batches"]):
        parser.error("batch outside the admission plan")
    if not 1 <= args.seconds <= 1800:
        parser.error("seconds must be between 1 and 1800")
    quests = args.quests if args.quests is not None else plan["batches"][batch - 1]
    if len(set(quests)) != len(quests) or any(quest not in plan["quests"] for quest in quests):
        parser.error("quests must be distinct and admitted by the plan")
    seed = "20261020-153034-ceda7b"
    roots = [ROOT / "game-mhfu/ms0", ROOT / "atelier-psprecomp/seeds" / seed / "ms0"]
    if not all(path.is_dir() for path in roots):
        raise RuntimeError("original save or seed missing")
    label = "check" if args.quests is not None else f"batch{batch:02d}"
    out = ROOT / "work" / datetime.now().strftime(f"step6-{label}-%Y%m%d-%H%M%S")
    out.mkdir(exist_ok=False)
    atelier_plan.write_plan(plan, out / "admission")
    before = {str(path.relative_to(ROOT)): core._tree_digest(path) for path in roots}
    workshop = core.Workshop(ROOT)
    cancel = threading.Event()
    timer = threading.Timer(args.seconds, cancel.set)
    timer.daemon = True
    result = {"batch": None if args.quests is not None else batch, "quests": quests, "max_seconds": args.seconds,
              "before": before, "checks": str(out / "checks.json")}
    meter = None
    if args.profile:
        from bench_atelier import Meter
        meter = Meter()
    began = time.monotonic()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    timer.start()
    try:
        report = pilot.run_campaign(workshop, quests, seed_id=seed,
                                    rtc_us=1609459200123456, speed=8,
                                    captures="fast", look_seconds=3,
                                    force=True, cancel=cancel,
                                    meter=meter,
                                    log=lambda message: print(message, flush=True))
        result["campaign"] = str(report)
        campaign = json.loads((report / "campagne.json").read_text(encoding="utf-8"))
        result["state"] = campaign["state"]
        result["outcomes"] = dict(Counter(row["outcome"] for row in campaign["results"]))
        result["survey_passed"] = [row["quest"] for row in campaign["results"]
                                   if pilot.survey_passed(type("Result", (), row)())]
    finally:
        timer.cancel()
        try:
            workshop.stop()
        finally:
            result["seconds"] = round(time.monotonic() - began, 2)
            result["deadline_reached"] = cancel.is_set()
            result["after"] = {str(path.relative_to(ROOT)): core._tree_digest(path) for path in roots}
            result["unchanged"] = result["before"] == result["after"]
            result["active"] = workshop.status()["active"]
            if meter is not None:
                core._write_json(out / "profile.json", meter.summary())
                result["profile"] = str(out / "profile.json")
            core._write_json(out / "checks.json", result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if (result["unchanged"] and not result["active"] and
                 len(result.get("survey_passed", [])) == len(quests)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
