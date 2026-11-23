"""The files of MHFU's DATA.BIN, read in place from game-mhfu/disc.iso.

DATA.BIN (PSP_GAME/USRDIR, stored decrypted by the FUComplete patch) starts
with a table of u32 sector numbers (2048-byte sectors): its first word is the
table's own length in sectors, then each file's first sector, the end of the
last file closing the list. After it, (index, exact size) pairs give the files
whose size is not a whole number of sectors. This is the layout svanheulen's
mhff (psp/data.py, GPL-3.0) reads; this module is written from that
description, not from its code.

The file names come from FUCTool's res/filelist.csv ("id,path", 6602 rows),
kept in references/FUCTool (GPL-3.0), not copied here.

The quests (mission_jpn/mNNNNN.mib) are kept encrypted as the downloaded
quests are (the scheme of svanheulen's mhef QuestCipher, GPL-3.0, written
again here from its description): four u16 seeds, then each u16 XORed with a
key per seed taken modulo-multiplied at every use; the plain text starts with
the u32 size and the SHA-1 of the quest file and a salt, which checks it.
All 564 quests of the disc check with the Japanese salt.

    mhfu_data.py info
    mhfu_data.py list [TEXT]          files whose path holds TEXT
    mhfu_data.py extract ID|PATH OUT  one file to OUT
    mhfu_data.py quest ID OUT         quest ID's file, decrypted
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import mmap
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
DISC = ROOT / "game-mhfu/disc.iso"
NAMES = ROOT / "references/FUCTool/res/filelist.csv"
SECTOR = 2048
DATA_BIN = ("PSP_GAME", "USRDIR", "DATA.BIN")
QUEST_FILE = re.compile(r"^mission_jpn/m(\d{5})\.mib$")
QUEST_MODULUS = (0xFF9D, 0xFFA9, 0xFFC7, 0xFFF1)
QUEST_FACTOR = (0x1709, 0x3DF3, 0x747B, 0xB381)
QUEST_SALTS = (b"37wyS2Jfc3x5w9oG", b"Vd6gh8F30wA86Ex5")  # Japanese, then North American


def _quest_keys(seeds) -> list[int]:
    return [seed or QUEST_MODULUS[i] for i, seed in enumerate(seeds)]


def decrypt_quest(raw: bytes) -> bytes:
    """A quest file from its encrypted form; ValueError when it does not check."""
    words = struct.unpack(f"<{len(raw) // 2}H", raw[:len(raw) // 2 * 2])
    if len(words) < 4 + 12:
        raise ValueError("too short for a quest")
    keys = _quest_keys(words[:4])
    plain = []
    for i, word in enumerate(words[4:]):
        k = i % 4
        keys[k] = keys[k] * QUEST_FACTOR[k] % QUEST_MODULUS[k]
        plain.append(word ^ keys[k])
    body = struct.pack(f"<{len(plain)}H", *plain)
    size = struct.unpack_from("<I", body)[0]
    digest, data = body[4:24], body[24:24 + size]
    if size > len(body) - 24 or not any(hashlib.sha1(data + salt).digest() == digest for salt in QUEST_SALTS):
        raise ValueError("the quest's hash does not check")
    return data


def encrypt_quest(data: bytes, seeds=(0x1234, 0x5678, 0x9ABC, 0xDEF0), salt: bytes = QUEST_SALTS[0]) -> bytes:
    """The encrypted form of a quest file (the inverse of decrypt_quest)."""
    body = struct.pack("<I", len(data)) + hashlib.sha1(data + salt).digest() + data
    if len(body) % 2:
        body += b"\0"
    keys = _quest_keys(seeds)
    words = list(struct.unpack(f"<{len(body) // 2}H", body))
    for i in range(len(words)):
        k = i % 4
        keys[k] = keys[k] * QUEST_FACTOR[k] % QUEST_MODULUS[k]
        words[i] ^= keys[k]
    return struct.pack("<4H", *seeds) + struct.pack(f"<{len(words)}H", *words)


def iso_entry(data, path: tuple[str, ...]) -> tuple[int, int]:
    """(offset, size) of a file in an ISO 9660 image, by its path."""
    root = 16 * SECTOR + 156  # the primary volume descriptor's root record
    extent, size = struct.unpack_from("<I", data, root + 2)[0], struct.unpack_from("<I", data, root + 10)[0]
    for part in path:
        found = None
        at, end = extent * SECTOR, extent * SECTOR + size
        while at < end:
            length = data[at]
            if length == 0:  # records do not cross sectors
                at = (at // SECTOR + 1) * SECTOR
                continue
            name_length = data[at + 32]
            name = bytes(data[at + 33:at + 33 + name_length]).decode("ascii", "replace").split(";")[0]
            if name.upper() == part.upper():
                found = struct.unpack_from("<I", data, at + 2)[0], struct.unpack_from("<I", data, at + 10)[0]
                break
            at += length
        if found is None:
            raise FileNotFoundError("/".join(path))
        extent, size = found
    return extent * SECTOR, size


def load_names(path: Path = NAMES) -> dict[int, str]:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            return {int(row[0]): row[1] for row in csv.reader(stream) if len(row) >= 2 and row[0].isdigit()}
    except OSError:
        return {}


class DataBin:
    """DATA.BIN's files: id -> (offset in the disc image, exact size)."""

    def __init__(self, data, offset: int, size: int, names: dict[int, str] | None = None):
        self.data, self.base, self.size = data, offset, size
        words = struct.unpack_from("<I", data, offset)[0] * SECTOR // 4
        table = struct.unpack_from(f"<{words}I", data, offset)
        end = size // SECTOR
        if end not in table:
            raise ValueError("DATA.BIN's table does not end at its size (still encrypted?)")
        self.count = table.index(end)
        self.files = [[table[i] * SECTOR, (table[i + 1] - table[i]) * SECTOR] for i in range(self.count)]
        for i in range(self.count + 1, len(table) - 1, 2):
            index, exact = table[i], table[i + 1]
            if index <= 0 or index >= self.count:
                break
            self.files[index][1] = exact
        self.names = names if names is not None else load_names()

    @classmethod
    def open(cls, disc: Path = DISC) -> "DataBin":
        stream = disc.open("rb")
        data = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        offset, size = iso_entry(data, DATA_BIN)
        return cls(data, offset, size)

    def name(self, index: int) -> str:
        return self.names.get(index, "")

    def find(self, wanted: str) -> int:
        """A file's id from its id or its exact path."""
        if wanted.isdigit():
            return int(wanted)
        for index, name in self.names.items():
            if name == wanted:
                return index
        raise KeyError(wanted)

    def read(self, index: int) -> bytes:
        offset, size = self.files[index]
        return bytes(self.data[self.base + offset:self.base + offset + size])

    def quests(self) -> dict[int, int]:
        """Quest id -> file id, from the file names."""
        found = {}
        for index, name in self.names.items():
            if match := QUEST_FILE.match(name):
                found[int(match[1])] = index
        return dict(sorted(found.items()))

    def quest(self, quest_id: int) -> bytes:
        """Quest `quest_id`'s file, decrypted."""
        return decrypt_quest(self.read(self.quests()[quest_id]))

    def file_at(self, disc_offset: int) -> int | None:
        """The file holding a byte of the disc image, by its offset."""
        relative = disc_offset - self.base
        for index, (offset, size) in enumerate(self.files):
            if offset <= relative < offset + max(size, 1):
                return index
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--disc", type=Path, default=DISC)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("info")
    listing = sub.add_parser("list")
    listing.add_argument("text", nargs="?", default="")
    extract = sub.add_parser("extract")
    extract.add_argument("file")
    extract.add_argument("out", type=Path)
    quest = sub.add_parser("quest")
    quest.add_argument("id", type=int)
    quest.add_argument("out", type=Path)
    args = parser.parse_args()
    data = DataBin.open(args.disc)
    if args.action == "info":
        print(f"DATA.BIN at 0x{data.base:X}, {data.size} bytes, {data.count} files, {len(data.names)} names")
    elif args.action == "list":
        for index, (offset, size) in enumerate(data.files):
            name = data.name(index)
            if args.text.lower() in name.lower() or args.text == str(index):
                print(f"{index:04d} {size:>10} {name}")
    elif args.action == "quest":
        plain = data.quest(args.id)
        args.out.write_bytes(plain)
        print(f"quest {args.id} -> {args.out} ({len(plain)} bytes, decrypted)")
    else:
        index = data.find(args.file)
        args.out.write_bytes(data.read(index))
        print(f"{index:04d} {data.name(index)} -> {args.out} ({len(data.read(index))} bytes)")


if __name__ == "__main__":
    sys.exit(main())
