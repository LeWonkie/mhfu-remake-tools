"""Offline quest-survey admission plan; never starts the game or records a validation."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

import atelier_ledger

REGISTRY = Path("outils/atelier-mhfu/quest_objectives_disc.json")
NATIVE_SOURCE = Path("psprecomp-mhfu/profiles/mhp3rd/host/debug/mhfu_tools.cpp")
KINDS = {"hunt", "slay", "delivery", "unsupported"}
NOTE = ("Admission déclarée dans les sources de quest start, pas une preuve de chargement ni de gameplay. "
        "Les objectifs spéciaux restent admissibles au repérage technique, sans interprétation de leur quantité.")


def build_plan(native_ids, missions, batch_size: int = 20) -> dict:
    """Cross-check native admission and disc metadata, retaining every excluded quest."""
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("taille de lot entière et positive requise")
    native = set(native_ids)
    records = {}
    for record in missions:
        quest = record.get("quest")
        if isinstance(quest, bool) or not isinstance(quest, int) or not 1 <= quest <= 65535:
            raise ValueError("identifiant de mission invalide")
        if quest in records:
            raise ValueError(f"mission dupliquée : {quest}")
        if record.get("kind") not in KINDS or not isinstance(record.get("descriptors"), list):
            raise ValueError(f"métadonnées de mission invalides : {quest}")
        records[quest] = record
    if not records:
        raise ValueError("registre des missions vide")
    if any(isinstance(q, bool) or not isinstance(q, int) or not 1 <= q <= 65535 for q in native):
        raise ValueError("identifiant natif invalide")
    rows = []
    for quest in sorted(native | records.keys()):
        record = records.get(quest)
        if record is None:
            status, reason = "missing_metadata", "mission absente du registre du disque"
        elif record.get("automation_exclusion") == "empty quest placeholder":
            status, reason = "empty", "mission vide : exclue du repérage"
        elif quest not in native:
            status, reason = "not_admitted", "identifiant non admis par la commande native quest start"
        else:
            status, reason = "eligible", "repérage technique possible ; exécution à vérifier"
        rows.append({"quest": quest, "name": record.get("name", "") if record else "",
                     "kind": record.get("kind", "unknown") if record else "unknown",
                     "native_admitted": quest in native, "in_registry": record is not None,
                     "status": status, "reason": reason,
                     "source": record.get("evidence") if record else None,
                     "gameplay_verified": False})
    eligible = [row["quest"] for row in rows if row["status"] == "eligible"]
    kinds = Counter(row["kind"] for row in rows if row["status"] == "eligible")
    return {"schema": 1, "purpose": "survey_admission", "note": NOTE,
            "counts": {"catalogued": len(records), "native_admitted": len(native),
                       **dict(Counter(row["status"] for row in rows))},
            "eligible_kinds": dict(sorted(kinds.items())), "quests": eligible,
            "batch_size": batch_size,
            "batches": [eligible[i:i + batch_size] for i in range(0, len(eligible), batch_size)],
            "rows": rows}


def load_plan(project: Path, batch_size: int = 20) -> dict:
    """Use the current local registry, without reading or changing the ISO or saves."""
    project = Path(project)
    try:
        raw = (project / REGISTRY).read_bytes()
        registry = json.loads(raw.decode("utf-8"))
        if registry.get("schema") != 1 or registry.get("source") != "quest_files":
            raise ValueError("format de registre du disque inattendu")
        plan = build_plan(atelier_ledger.known_quests(project), registry["missions"], batch_size)
    except (OSError, KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("plan impossible : régénérez quest_objectives_disc.json depuis votre disque") from error
    plan["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    plan["inputs"] = {
        "registry": {"path": REGISTRY.as_posix(), "sha256": hashlib.sha256(raw).hexdigest()},
        "native_source": {"path": NATIVE_SOURCE.as_posix(),
                          "sha256": hashlib.sha256((project / NATIVE_SOURCE).read_bytes()).hexdigest()},
    }
    return plan


def write_plan(plan: dict, out: Path) -> Path:
    """Keep plans in a new directory: an old plan or test result is never overwritten."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = plan["counts"]
    lines = ["# Préparation de la campagne MHFU", "", plan["note"], "",
             f"{counts['catalogued']} missions du disque ; {counts['native_admitted']} identifiants natifs ; "
             f"{len(plan['quests'])} missions admissibles au repérage.", "",
             "Aucune quête exécutée ; aucune validation ni sauvegarde modifiée.", "",
             "## Lots proposés", ""]
    for i, batch in enumerate(plan["batches"], 1):
        lines.append(f"- Lot {i} ({len(batch)}) : " + " ".join(map(str, batch)))
    lines += ["", "## Exclusions", "", "| Quête | Admission native | Motif |", "| --- | --- | --- |"]
    for row in plan["rows"]:
        if row["status"] != "eligible":
            lines.append(f"| {row['quest']} | {'oui' if row['native_admitted'] else 'non'} | {row['reason']} |")
    lines += ["", "Les missions sans admission native demandent un autre chemin de chargement, "
              "à établir séparément. Un simple ajout de leur ID ne valide pas ce chemin.", ""]
    path = out / "plan.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
