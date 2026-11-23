"""Validation cache, coverage and the quest list, for not repeating what is already proven.

A validation is reused only when its whole key matches: the executable, the
overlays, the game data, the seed, the RTC, the private settings, the driver's
source, the scenario and the coverage level. Anything else, an unfinished
result or one that did not pass, is run again. A reused result always names
the original evidence (campaign folder, run, captures) and is marked reused.

    python atelier_ledger.py coverage [--out DOSSIER]
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import atelier_core

HERE = Path(__file__).resolve().parent
DRIVER_FILES = ("atelier_pilot.py", "atelier_campaign.py", "atelier_zones.py", "atelier_core.py",
                "atelier_ledger.py", "atelier_plan.py")
LEVELS = ("survey", "gameplay")
PAW_PASS_TICKET, LIMITED_PAW_PASS = 927, 928


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def overlays_digest(directory: Path) -> str | None:
    """Every recompiled overlay library the executable loads (bin/overlays), by content."""
    if not directory.is_dir():
        return None
    return atelier_core._tree_digest(directory)


def driver_digest(root: Path = HERE) -> str:
    h = hashlib.sha256()
    for name in DRIVER_FILES:
        path = root / name
        h.update(name.encode() + b"\0" + (atelier_core._digest(path).encode() if path.is_file() else b"-") + b"\n")
    return h.hexdigest()


def settings_digest(workshop) -> str:
    """The player settings the trial copies (input bindings, frame rate…), before the atelier's overrides."""
    source = Path(__import__("os").environ.get("APPDATA", "")) / "Yakumo" / "MHP3rd" / "settings.ini"
    return _sha(source.read_bytes()) if source.is_file() else "absent"


def environment(workshop, seed: dict, settings: dict, scenario: dict) -> dict:
    """The key shared by every quest of a campaign; `scenario` adds the quest itself."""
    executable = Path(workshop.executable)
    reference = workshop._assets / "reference.json"
    components = {
        "executable_sha256": atelier_core._digest(executable) if executable.is_file() else None,
        "overlays_sha256": overlays_digest(executable.parent / "overlays"),
        "data_sha256": _sha(reference.read_bytes()) if reference.is_file() else None,
        "seed_sha256": seed.get("seed_sha256"),
        "rtc_us": settings.get("rtc_us"),
        "player_settings_sha256": settings_digest(workshop),
        "settings": {k: settings[k] for k in sorted(settings)},
        "scenario": {k: scenario[k] for k in sorted(scenario) if k != "label"},
        "driver_sha256": driver_digest(),
    }
    complete = all(components[k] is not None for k in
                   ("executable_sha256", "overlays_sha256", "data_sha256", "seed_sha256", "driver_sha256"))
    # A fixed RTC is part of a comparable trial; without it the guest clock follows the wall clock.
    components["complete"] = complete and components["rtc_us"] is not None
    key = _sha(json.dumps(components, sort_keys=True).encode())
    return {"key": key, "components": components}


def quest_key(level: str, quest: int, env: dict) -> str:
    return _sha(f"{level}\0{quest}\0{env['key']}".encode())


class Ledger:
    """One JSON file per level and quest, holding every key that was run, written atomically."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, level: str, quest: int) -> Path:
        if level not in LEVELS:
            raise ValueError(f"niveau parmi {LEVELS}")
        return self.root / level / f"{int(quest)}.json"

    def entries(self, level: str, quest: int) -> dict:
        try:
            return json.loads(self._path(level, quest).read_text(encoding="utf-8")).get("entries", {})
        except (OSError, json.JSONDecodeError):
            return {}

    def find(self, level: str, quest: int, env: dict) -> dict | None:
        """A passed validation with exactly this key; never for an incomplete key."""
        if not env["components"].get("complete"):
            return None
        entry = self.entries(level, quest).get(quest_key(level, quest, env))
        if not entry or entry.get("status") != "passed" or entry.get("environment_key") != env["key"]:
            return None
        evidence = entry.get("evidence") or {}
        if not evidence.get("campaign") or not evidence.get("run_id"):
            return None
        return entry

    def record(self, level: str, quest: int, env: dict, status: str, evidence: dict) -> dict:
        path = self._path(level, quest)
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = self.entries(level, quest)
        key = quest_key(level, quest, env)
        entry = {"key": key, "environment_key": env["key"], "level": level, "quest": int(quest),
                 "status": status if env["components"].get("complete") else f"{status} (clé incomplète)",
                 "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "evidence": evidence, "components": env["components"]}
        history = entries.get(key, {}).get("history", [])
        if key in entries:
            history.append({k: entries[key].get(k) for k in ("status", "recorded_at", "evidence")})
        entry["history"] = history[-10:]
        entries[key] = entry
        atelier_core._write_json(path, {"quest": int(quest), "level": level, "entries": entries})
        return entry

    def forget(self, level: str, quest: int) -> bool:
        """Forces a new check of one quest at one level (all keys)."""
        path = self._path(level, quest)
        if path.is_file():
            path.replace(path.with_suffix(".json.forgotten"))
            return True
        return False


def known_quests(project: Path) -> list[int]:
    """The quest ids the game has a mission file for (kQuestIds in mhfu_tools.cpp)."""
    source = Path(project) / "psprecomp-mhfu" / "profiles" / "mhp3rd" / "host" / "debug" / "mhfu_tools.cpp"
    text = source.read_text(encoding="utf-8")
    block = text[text.index("kQuestIds[]"):]
    block = block[:block.index(";")]
    ids = []
    for first, last in re.findall(r"\{(\d+), (\d+)\}", block):
        ids.extend(range(int(first), int(last) + 1))
    return ids


# ----- coverage ---------------------------------------------------------------
def objective_kind(objectives: dict | None, roster: list | None) -> str:
    """The family of a quest from its raw descriptors (docs: 0x11 slay, 0x12 deliver)."""
    if not objectives:
        return "inconnu"
    active = [e for e in objectives.get("current", []) if e.get("flags")]
    if not active:
        return "inconnu"
    kinds = set()
    for entry in active:
        if entry["flags"] & 0x71 == 0x11:
            kinds.add("abattage")
        elif entry["flags"] == 0x12:
            if entry["target"] == PAW_PASS_TICKET:
                kinds.add("livraison (ticket)")
            elif entry["target"] == LIMITED_PAW_PASS:
                kinds.add("capture probable")
            else:
                kinds.add("livraison")
        else:
            kinds.add(f"drapeaux 0x{entry['flags']:X}")
    return " + ".join(sorted(kinds))


def quest_group(quest: int) -> str:
    """The quest list a quest belongs to, by its id range (village, Guild Hall, training…)."""
    if quest < 1000:
        return "spéciales (<1000)"
    return f"série {quest // 100 * 100}"


def coverage(data_dir: Path, project: Path) -> dict:
    """Individual coverage (each quest, each level) and family coverage, kept apart."""
    ledger = Ledger(data_dir / "validations")
    quests = known_quests(project)
    survey_info: dict[int, dict] = {}
    for campaign in sorted((data_dir / "campaigns").glob("*/campagne.json")):
        try:
            data = json.loads(campaign.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for result in data.get("results", []):
            if result.get("reached") and result.get("objectives"):
                survey_info[result["quest"]] = {"stage": result.get("stage"),
                                                "kind": objective_kind(result.get("objectives"), result.get("roster"))}
    rows = []
    for quest in quests:
        row = {"quest": quest, "group": quest_group(quest), **survey_info.get(quest, {"stage": None, "kind": "inconnu"})}
        for level in LEVELS:
            entries = list(ledger.entries(level, quest).values())
            passed = [e for e in entries if e.get("status") == "passed"]
            latest = max(entries, key=lambda e: e.get("recorded_at", ""), default=None)
            row[level] = ("passed" if passed else latest["status"] if latest else "never")
            row[level + "_evidence"] = (passed or [latest])[0]["evidence"] if (passed or latest) else None
        rows.append(row)
    families: dict[str, dict] = {}
    for axis in ("group", "kind", "stage"):
        for row in rows:
            name = f"{axis}:{row[axis]}"
            family = families.setdefault(name, {"axis": axis, "value": row[axis], "quests": 0,
                                                "survey_passed": 0, "gameplay_passed": 0})
            family["quests"] += 1
            family["survey_passed"] += row["survey"] == "passed"
            family["gameplay_passed"] += row["gameplay"] == "passed"
    totals = {level: sum(r[level] == "passed" for r in rows) for level in LEVELS}
    return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "quests": len(rows),
            "passed": totals, "rows": rows, "families": sorted(families.values(), key=lambda f: (f["axis"], str(f["value"]))),
            "note": "Une famille sert à choisir les essais approfondis ; elle ne valide aucune quête non exécutée."}


def write_coverage(data_dir: Path, project: Path, out: Path) -> Path:
    report = coverage(data_dir, project)
    out.mkdir(parents=True, exist_ok=True)
    atelier_core._write_json(out / "couverture.json", report)
    lines = [f"# Couverture des quêtes MHFU ({report['generated_at']})", "",
             f"{report['quests']} quêtes connues ; repérage technique réussi : {report['passed']['survey']} ; "
             f"gameplay validé : {report['passed']['gameplay']}.", "", report["note"], "",
             "## Par famille", "", "| Axe | Famille | Quêtes | Repérage OK | Gameplay OK |", "|---|---|---|---|---|"]
    for family in report["families"]:
        if family["axis"] == "stage" and family["value"] is None:
            continue
        lines.append(f"| {family['axis']} | {family['value']} | {family['quests']} | {family['survey_passed']} "
                     f"| {family['gameplay_passed']} |")
    lines += ["", "## Par quête", "", "| Quête | Groupe | Zone | Type | Repérage | Gameplay |", "|---|---|---|---|---|---|"]
    for row in report["rows"]:
        lines.append(f"| {row['quest']} | {row['group']} | {row['stage']} | {row['kind']} | {row['survey']} "
                     f"| {row['gameplay']} |")
    (out / "couverture.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out / "couverture.md"


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cover = sub.add_parser("coverage", help="couverture individuelle et par famille")
    cover.add_argument("--out", type=Path, default=None)
    forget = sub.add_parser("forget", help="forcer une nouvelle vérification")
    forget.add_argument("level", choices=LEVELS)
    forget.add_argument("quests", nargs="+", type=int)
    args = parser.parse_args(argv)
    workshop = atelier_core.Workshop(HERE.parents[1])
    if args.command == "coverage":
        out = args.out or workshop.data_dir / "coverage" / datetime.now().strftime("%Y%m%d-%H%M%S")
        print("couverture :", write_coverage(workshop.data_dir, workshop.project, out))
    else:
        ledger = Ledger(workshop.data_dir / "validations")
        for quest in args.quests:
            print(quest, "oublié" if ledger.forget(args.level, quest) else "rien à oublier")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
