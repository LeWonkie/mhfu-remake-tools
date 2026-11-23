"""Catalogue of MHFU's quests, read from the game's disc image.

Writes quest_catalog.json: for each quest id, its title, objective, failure
conditions, description, monsters and client as the game shows them, its
mission flags and time limit. Read once from game-mhfu/disc.iso; the debug
tool (outil_debug.py) lists quests by name from it.

The quests are DATA.BIN's mission_jpn/mNNNNN.mib files (564), decrypted by
mhfu_data.py; the downloaded quests FUComplete adds (60001 and up) are whole
mission files elsewhere on the disc, stored plain. A mission file (as the
game loads it at Quest +0x50, checked on quest 10001 in RAM, run
20261021-163749-ac287a) starts with u32 0x4C and "2NDG"; from +0x4C: u32
mission flags, +0x5C the time limit in frames, +0x60 the offset of the
language list, +0x64 the u16 quest id. The language list holds, once per
language, the offset of a text table: six u32 offsets to title, objective,
failure conditions, description, monsters and client.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

import mhfu_data

TEXTS = ("title", "objective", "failure", "description", "monsters", "client")
FPS = 30
FILE_HEAD = b"\x4C\x00\x00\x002NDG"
TEXT_MOST = 2048


def text(data, at: int) -> str:
    raw = bytes(data[at:data.find(b"\0", at, at + TEXT_MOST)])
    try:
        return raw.decode("utf-8")  # the untranslated quests keep Japanese text
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def mission(data, base: int = 0) -> dict:
    """A mission file's header and texts; ValueError when it is not one."""
    if bytes(data[base:base + 8]) != FILE_HEAD:
        raise ValueError("not a mission file")
    u32 = lambda at: struct.unpack_from("<I", data, base + at)[0]
    table = u32(u32(0x60))
    texts = {name: text(data, base + u32(table + 4 * i)) for i, name in enumerate(TEXTS)}
    return {"id": struct.unpack_from("<H", data, base + 0x64)[0], "flags": u32(0x4C),
            "minutes": u32(0x5C) // (FPS * 60), **texts}


def plain_missions(data):
    """Yield (disc offset, metadata) for readable plain mission headers."""
    at = data.find(FILE_HEAD)
    while at >= 0:
        try:
            quest = mission(data, at)
        except (ValueError, struct.error, IndexError):
            pass
        else:
            yield at, quest
        at = data.find(FILE_HEAD, at + 4)


def downloaded(data) -> dict[int, dict]:
    """The plain mission files anywhere in `data` (the disc's downloaded quests)."""
    found = {}
    for _, quest in plain_missions(data):
        found.setdefault(quest["id"], quest)
    return found


def catalogue(disc: mhfu_data.DataBin) -> dict[int, dict]:
    quests = {}
    for quest_id in disc.quests():
        quest = mission(disc.quest(quest_id))
        quests[quest["id"]] = quest
    for quest_id, quest in downloaded(disc.data).items():
        quests.setdefault(quest_id, quest)
    return dict(sorted(quests.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--disc", type=Path, default=mhfu_data.DISC)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("quest_catalog.json"))
    args = parser.parse_args()
    quests = catalogue(mhfu_data.DataBin.open(args.disc))
    args.out.write_text(json.dumps({"source": args.disc.name, "quests": quests}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"{len(quests)} quests -> {args.out}")


if __name__ == "__main__":
    main()
