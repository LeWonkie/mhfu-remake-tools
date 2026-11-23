"""Reproduce the isolated 11001 RTC experiment, writing only new run copies."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    sys.path.insert(0, str(project / "outils" / "atelier-mhfu"))
    import atelier_core
    import atelier_campaign

    # Six minutes per owned game process, including boot and quest return.
    atelier_core.MAX_SECONDS = 360
    values = [946684800000000, 1609459200123456, 1767225600654321, 1792533600999999]
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    workshop = atelier_core.Workshop(project)
    protected = {
        "original_saves": project / "game-mhfu" / "ms0",
        "all_seeds": workshop.data_dir / "seeds",
        "current_seed_selection": workshop.data_dir / "current.json",
    }
    before = {name: atelier_core._tree_digest(path) if path.is_dir() else atelier_core._digest(path)
              for name, path in protected.items()}
    old_metadata = list((workshop.data_dir / "runs").glob("*/manifest.json"))
    old_metadata += list((workshop.data_dir / "campaigns").glob("*/campagne.json"))
    metadata_before = {str(path): atelier_core._digest(path) for path in old_metadata}
    data = {"created_at": datetime.now(timezone.utc).isoformat(), "quest": 11001,
            "values_us": values, "repetitions": 2, "variant": "space_trace", "look_seconds": 2,
            "run_limit_seconds": atelier_core.MAX_SECONDS, "seed_id": args.seed,
            "protected_before": before, "old_metadata_sha256": metadata_before,
            "state": "running", "trials": []}

    def checkpoint():
        atelier_core._write_json(out / "rtc_probe.json", data)

    checkpoint()
    try:
        for repeat in range(1, 3):
            for rtc_us in values:
                print(f"RTC {rtc_us} repetition {repeat}/2", flush=True)
                campaign = atelier_campaign.run_campaign(
                    workshop, [11001], seed_id=args.seed, look_seconds=2,
                    log=lambda text: print(text, flush=True), variant="space_trace", rtc_us=rtc_us)
                report = json.loads((campaign / "campagne.json").read_text(encoding="utf-8"))
                result = report["results"][0] if report["results"] else {}
                manifest = json.loads((workshop.data_dir / "runs" / result["run_id"] /
                                       "manifest.json").read_text(encoding="utf-8")) if result.get("run_id") else {}
                trial = {"rtc_us": rtc_us, "repetition": repeat, "campaign": str(campaign),
                         "run_id": result.get("run_id"), "stage": result.get("stage"),
                         "reached": result.get("reached"), "returned": result.get("returned"),
                         "window_size": result.get("window_size"),
                         "window_sizes": manifest.get("window_sizes"),
                         "rtc_epoch_confirmed": manifest.get("rtc_epoch_confirmed"),
                         "seed_sha256": manifest.get("seed_sha256"), "notes": result.get("notes")}
                data["trials"].append(trial)
                checkpoint()
                print(json.dumps(trial, ensure_ascii=False), flush=True)
        data["state"] = "completed"
    except BaseException as error:
        data["state"] = "interrupted"
        data["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        workshop.stop()
        after = {name: atelier_core._tree_digest(path) if path.is_dir() else atelier_core._digest(path)
                 for name, path in protected.items()}
        data["protected_after"] = after
        data["protected_unchanged"] = before == after
        data["old_metadata_unchanged"] = all(path.is_file() and atelier_core._digest(path) ==
                                              metadata_before[str(path)] for path in old_metadata)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows = []
        for value in values:
            trials = [trial for trial in data["trials"] if trial["rtc_us"] == value]
            stages = [trial["stage"] for trial in trials]
            valid = len(trials) == 2 and all(trial["reached"] and trial["rtc_epoch_confirmed"] for trial in trials)
            rows.append({"rtc_us": value, "stages": stages,
                         "reproducible": len(set(stages)) == 1 if valid else None})
        data["summary"] = rows
        checkpoint()
        print("PROBE COMPLETE " + str(out / "rtc_probe.json"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
