"""Summarize archived survey batches without changing their evidence or cache."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outils/atelier-mhfu"))
import atelier_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    plan = atelier_plan.load_plan(ROOT)
    batches = []
    observed = set()
    for path in sorted((ROOT / "work").glob("step6-batch*/checks.json")):
        checks = json.loads(path.read_text(encoding="utf-8"))
        campaign_dir = Path(checks["campaign"])
        campaign = json.loads((campaign_dir / "campagne.json").read_text(encoding="utf-8"))
        results = campaign["results"]
        missing = []
        for row in results:
            frames = ROOT / "atelier-psprecomp/runs" / row["run_id"] / "frames"
            missing.extend(str(frames / (shot + ".bmp")) for shot in row["shots"]
                           if not (frames / (shot + ".bmp")).is_file())
        complete = (checks.get("state") == "completed" and checks.get("unchanged") is True
                    and not checks.get("deadline_reached") and not checks.get("active")
                    and len(checks.get("survey_passed", [])) == len(checks["quests"])
                    and set(checks.get("survey_passed", [])) == set(checks["quests"])
                    and len(results) == len(checks["quests"]) and not missing)
        if complete:
            observed.update(checks["quests"])
        returns = [row["timing"]["return"] for row in results if "return" in row.get("timing", {})]
        end_mismatches = [row["quest"] for row in results
                          if row.get("mission_state") == 3 and row.get("outcome") == "forced_end"]
        batches.append({"batch": checks["batch"], "checks": str(path.relative_to(ROOT)),
                        "campaign": str(campaign_dir.relative_to(ROOT)), "seconds": checks["seconds"],
                        "quests": checks["quests"], "archived_batch_passed": complete,
                        "mission_states": dict(Counter(str(row.get("mission_state")) for row in results)),
                        "outcomes_as_recorded": dict(Counter(row["outcome"] for row in results)),
                        "historical_clear_mislabeled_forced_end": end_mismatches,
                        "shots_recorded": sum(len(row["shots"]) for row in results),
                        "missing_recorded_shots": missing,
                        "return_total_seconds": round(sum(returns), 2),
                        "return_mean_seconds": round(statistics.mean(returns), 2) if returns else None})
    eligible = set(plan["quests"])
    remaining = sorted(eligible - observed)
    summary = {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "scope": "Archived technical surveys, not current-cache or normal-gameplay validation.",
               "eligible": len(eligible), "distinct_quests_in_passed_archives": len(eligible & observed),
               "remaining_quests": remaining, "batches": batches}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "status.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# État des lots de repérage MHFU", "",
             f"{len(eligible & observed)} quêtes distinctes dans les archives complètes, "
             f"sur {len(eligible)} candidates ; {len(remaining)} restantes.", "",
             "Ces archives ne constituent pas une validation sous la clé de cache actuelle ni de gameplay normal.",
             "La revue visuelle se trouve dans chaque dossier de preuves ; ce récapitulatif contrôle les fichiers enregistrés.", "",
             "| Lot | Quêtes | Durée (s) | Retour total (s) | Captures | Archive complète |",
             "| --- | --- | --- | --- | --- | --- |"]
    for batch in batches:
        lines.append(f"| {batch['batch']} | {len(batch['quests'])} | {batch['seconds']} | "
                     f"{batch['return_total_seconds']} | {batch['shots_recorded']} | "
                     f"{'oui' if batch['archived_batch_passed'] else 'non'} |")
    lines += ["", "Les anciens libellés sont préservés : le premier lot appelle encore la réussite de 1001 "
              "`forced_end`. Son état 3 a ensuite été confirmé avec le libellé corrigé `reported_clear`.", "",
              "## Preuves", ""]
    lines += [f"- Lot {batch['batch']} : `{batch['checks']}` ; `{batch['campaign']}`." for batch in batches]
    (args.out / "status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key not in ("batches", "remaining_quests")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
