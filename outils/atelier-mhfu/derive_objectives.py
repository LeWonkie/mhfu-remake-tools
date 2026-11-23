"""Objectives read from quest files or recorded runtime mission descriptors.

`--disc game-mhfu/disc.iso` writes quest_objectives_disc.json: both raw
objectives of every catalogue mission, delivery item quantities, and a
conservative hunt/slay fallback. File-derived goals remain unverified in
game; special objective types and empty quests are never automated. The
runtime snapshot must still match before its progress counter is used.

Reads campaign reports (campagne.json, whose quests recorded Quest +0x728 and
the boss roster +0x768 once their area was loaded) and writes
quest_objectives_derived.json, used by the hunt controller for quests the
hand-verified registry (quest_objectives.json) does not list. Kept:

- slay descriptors (flags & 0x71 == 0x11, the ones 0x0886E5B4 counts down):
  target = species, quantity = kills; one descriptor also gives the game's
  progress counter.

Left out, and listed: every delivery, including Paw Pass Ticket (927) with
a boss roster. The roster does not make it a hunt: quest 2002's Quest Info
requires a ticket delivery (real-game capture, 2026-10-21). Limited Paw Pass
(928) remains unsupported; a capture objective has not been verified. The
quest result stays GameSys's mission state whatever is derived here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

import mhfu_data
import quest_catalog

HERE = Path(__file__).resolve().parent
OUT = HERE / "quest_objectives_derived.json"
DISC_OUT = HERE / "quest_objectives_disc.json"
PAW_PASS_TICKET, LIMITED_PAW_PASS = 927, 928


def disc_mission(data, evidence: str, base: int = 0, expected_id: int | None = None):
    """Record both file descriptors; derive only ordinary hunt/slay goals.

    File descriptors at +0x6C/+0x74 are u32 type, u16 target, u16
    quantity. The runtime adds 0x10 to ordinary hunt/slay flags (observed
    in the campaign snapshots). Special types keep their raw fields: their
    final word is not established as a kill quantity. No spawn areas are
    inferred from text or the boss roster.
    """
    if base < 0 or base + 0x7C > len(data):
        raise ValueError("truncated mission objectives")
    metadata = quest_catalog.mission(data, base)
    quest = metadata["id"]
    if quest <= 0 or (expected_id is not None and quest != expected_id):
        raise ValueError("mission id does not match its archive name")
    descriptors = [dict(zip(("type", "target", "quantity"),
                           struct.unpack_from("<IHH", data, base + offset)))
                   for offset in (0x6C, 0x74)]
    active = [entry for entry in descriptors if entry["type"]]
    record = {"quest": quest, "name": metadata["title"], "objective_text": metadata["objective"],
              "source": "quest_file", "evidence": evidence, "verified": False,
              "descriptors": descriptors, "kind": "unsupported"}
    reason = None
    if metadata["title"].strip() in ("", "**********"):
        reason = "empty quest placeholder"
    elif not active:
        reason = "no active objectives"
    elif all(entry["type"] == 2 for entry in active):
        record["kind"] = "delivery"
        record["items"] = [{"item": entry["target"], "quantity": entry["quantity"]} for entry in active]
        reason = "delivery: excluded from hunt automation"
    elif not all(entry["type"] in (1, 0x201) for entry in active):
        reason = "special objective types: " + ", ".join(hex(entry["type"]) for entry in active)
    elif not all(1 <= entry["target"] <= 127 and 1 <= entry["quantity"] <= 1000 for entry in active):
        reason = "hunt target or quantity outside supported range"
    elif sum(entry["quantity"] for entry in active) > 1000:
        reason = "combined hunt quantity outside supported range"
    if reason:
        record["automation_exclusion"] = reason
        return record, None
    record["kind"] = "slay" if any(entry["type"] == 0x201 for entry in active) else "hunt"
    record["species"] = sorted({entry["target"] for entry in active})
    record["required"] = sum(entry["quantity"] for entry in active)
    goal = {key: record[key] for key in ("quest", "name", "source", "evidence", "verified",
                                        "kind", "species", "required")}
    goal.update(patrol=[], description=metadata["objective"])
    if len(active) == 1:
        goal["progress_counter"] = {"flags": active[0]["type"] | 0x10, "evidence": evidence}
    return record, goal


def derive_disc(disc):
    """Every catalogue quest, plus a conservative hunt-controller fallback."""
    missions, goals, excluded = {}, {}, {}

    def add(data, evidence, base=0, expected_id=None):
        record, goal = disc_mission(data, evidence, base, expected_id)
        quest = record["quest"]
        if quest in missions:
            return
        missions[quest] = record
        if goal is not None:
            goals[quest] = goal
        else:
            excluded[quest] = record["automation_exclusion"]

    for quest_id, file_id in disc.quests().items():
        add(disc.quest(quest_id), f"DATA.BIN:{disc.name(file_id)} (file {file_id})",
            expected_id=quest_id)
    for offset, metadata in quest_catalog.plain_missions(disc.data):
        if metadata["id"] not in missions:
            add(disc.data, f"disc.iso:0x{offset:X}", base=offset)
    return {"schema": 1, "source": "quest_files", "quests": [goals[q] for q in sorted(goals)],
            "missions": [missions[q] for q in sorted(missions)],
            "not_supported": {str(q): excluded[q] for q in sorted(excluded)}}


def derive(result: dict) -> tuple[dict | None, str]:
    """A registry entry for one campaign quest result, or None and why."""
    objectives = result.get("objectives")
    if not objectives or objectives.get("quest") != result["quest"]:
        return None, "descripteurs non lus"
    active = [e for e in objectives["current"] if e["flags"]]
    initial = [e for e in objectives["initial"] if e["flags"]]
    if not active or active != initial:
        return None, "descripteurs absents ou déjà entamés"
    entry = {"quest": result["quest"], "verified": False, "source": "mission_descriptors",
             "evidence": result["campaign"], "patrol": []}
    if all(e["flags"] & 0x71 == 0x11 and 1 <= e["target"] <= 127 and 1 <= e["quantity"] <= 1000
           for e in active):
        entry.update(kind="slay" if any(e["quantity"] > 1 for e in active) else "hunt",
                     species=sorted({e["target"] for e in active}),
                     required=sum(e["quantity"] for e in active),
                     description="Abattre " + ", ".join(f"{e['quantity']} × espèce {e['target']}" for e in active))
        if len(active) == 1:
            entry["progress_counter"] = {"flags": active[0]["flags"], "evidence": result["campaign"]}
        return entry, "abattage"
    if len(active) == 1 and active[0]["flags"] == 0x12:
        item = active[0]["target"]
        if item == LIMITED_PAW_PASS:
            return None, "capture (Limited Paw Pass) : non prise en charge"
        if item == PAW_PASS_TICKET:
            return None, "livraison d'un Paw Pass Ticket : non prise en charge (la liste des monstres ne définit pas l'objectif)"
        return None, f"livraison de l'objet {item} × {active[0]['quantity']} : non prise en charge"
    return None, "drapeaux " + ", ".join(hex(e["flags"]) for e in active) + " : non pris en charge"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaigns", nargs="*", type=Path, help="campaign folders or campagne.json files")
    parser.add_argument("--disc", type=Path, help="derive all objectives directly from this ISO (read only)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.disc and args.campaigns:
        parser.error("choose --disc or campaign reports")
    if not args.disc and not args.campaigns:
        parser.error("provide --disc or at least one campaign report")
    if args.disc:
        report = derive_disc(mhfu_data.DataBin.open(args.disc))
        out = args.out or DISC_OUT
        _write_report(out, report)
        print(f"{len(report['missions'])} missions, {len(report['quests'])} hunt/slay objectives -> {out}")
        return 0
    derived, skipped = {}, {}
    for path in args.campaigns:
        path = path / "campagne.json" if path.is_dir() else path
        data = json.loads(path.read_text(encoding="utf-8"))
        for result in data.get("results", []):
            result = dict(result, campaign=str(path))
            entry, why = derive(result)
            if entry:
                derived[entry["quest"]] = entry
                skipped.pop(entry["quest"], None)
            elif result["quest"] not in derived:
                skipped[result["quest"]] = why
    report = {"schema": 1, "quests": [derived[q] for q in sorted(derived)],
              "not_supported": {str(q): why for q, why in sorted(skipped.items())}}
    out = args.out or OUT
    _write_report(out, report)
    print(f"{len(derived)} objectifs déduits, {len(skipped)} quêtes non prises en charge -> {out}")
    return 0


def _write_report(out: Path, report: dict):
    pending = out.with_suffix(".tmp")
    pending.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pending.replace(out)


if __name__ == "__main__":
    raise SystemExit(main())
