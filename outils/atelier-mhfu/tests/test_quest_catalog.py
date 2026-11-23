from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mhfu_data
import quest_catalog

TEXTS = [b"Attack of the Yian Kut-Ku", b"Hunt the Yian Kut-Ku", b"Reward Zero\nTime Over", b"A bird.", b"Yian Kut-Ku",
         b"Guild"]


def mission_file(quest_id: int, flags: int = 4, frames: int = 90000) -> bytes:
    """A mission file as the game loads it: header from +0x4C, texts from +0x80."""
    data = bytearray(0x80)
    data[0:8] = b"\x4C\x00\x00\x002NDG"
    offsets = []
    for text in TEXTS:
        offsets.append(len(data))
        data += text + b"\0"
    while len(data) % 4:
        data += b"\0"
    table = len(data)
    data += struct.pack("<6I", *offsets)
    languages = len(data)
    data += struct.pack("<5I", *[table] * 5)
    struct.pack_into("<I", data, 0x4C, flags)
    struct.pack_into("<IIH", data, 0x5C, frames, languages, quest_id)
    return bytes(data)


class FakeDisc:
    """What catalogue() reads of a DataBin: its quests and the disc's bytes."""

    def __init__(self, quests: dict[int, bytes], data: bytes):
        self.files, self.data = quests, data

    def quests(self):
        return {quest_id: quest_id for quest_id in self.files}

    def quest(self, quest_id):
        return mhfu_data.decrypt_quest(self.files[quest_id])


class CatalogueTests(unittest.TestCase):
    def test_a_mission_file_reads(self):
        quest = quest_catalog.mission(mission_file(1020))
        self.assertEqual((quest["id"], quest["flags"], quest["minutes"]), (1020, 4, 50))
        self.assertEqual((quest["title"], quest["failure"], quest["client"]),
                         ("Attack of the Yian Kut-Ku", "Reward Zero\nTime Over", "Guild"))
        with self.assertRaises(ValueError):
            quest_catalog.mission(b"\0" * 0x100)

    def test_japanese_text_is_kept(self):
        data = bytearray(mission_file(30001))
        data[0x80:0x80 + len(TEXTS[0])] = "雪山".encode("utf-8").ljust(len(TEXTS[0]), b" ")
        self.assertTrue(quest_catalog.mission(bytes(data))["title"].startswith("雪山"))

    def test_disc_quests_and_downloaded_ones(self):
        disc = FakeDisc({1020: mhfu_data.encrypt_quest(mission_file(1020))},
                        bytes(0x30) + mission_file(60001, 0x60000004, 54000) + bytes(0x10) + mission_file(1020, 8))
        quests = quest_catalog.catalogue(disc)
        self.assertEqual(sorted(quests), [1020, 60001])
        self.assertEqual(quests[1020]["flags"], 4, "the disc's quest file wins over a plain copy")
        self.assertEqual(quests[60001]["minutes"], 30)


class DataBinTests(unittest.TestCase):
    def test_quests_decrypt_and_check(self):
        plain = mission_file(10001)
        sealed = mhfu_data.encrypt_quest(plain, seeds=(1, 0, 0xFFFF, 77))
        self.assertNotIn(b"2NDG", sealed)
        self.assertEqual(mhfu_data.decrypt_quest(sealed + bytes(500)), plain, "sector padding is left out")
        self.assertEqual(mhfu_data.decrypt_quest(mhfu_data.encrypt_quest(plain, salt=mhfu_data.QUEST_SALTS[1])), plain)
        broken = bytearray(sealed)
        broken[40] ^= 1
        with self.assertRaises(ValueError):
            mhfu_data.decrypt_quest(bytes(broken))

    def test_the_table_gives_each_file(self):
        # A table of one sector, three files of 1, 2 and 1 sectors, the second 3000 bytes.
        sectors = [1, 2, 4, 5]
        table = struct.pack("<4I", *sectors) + struct.pack("<2I", 1, 3000)
        data = bytearray(table.ljust(2048, b"\0") + bytes(4 * 2048))
        data[2 * 2048:2 * 2048 + 4] = b"ABCD"
        disc = mhfu_data.DataBin(bytes(data), 0, len(data),
                                 names={0: "system/dummy", 1: "mission_jpn/m01020.mib", 2: "stage/x"})
        self.assertEqual(disc.count, 3)
        self.assertEqual(disc.files, [[2048, 2048], [4096, 3000], [8192, 2048]])
        self.assertEqual(disc.read(1)[:4], b"ABCD")
        self.assertEqual(disc.quests(), {1020: 1})
        self.assertEqual(disc.find("stage/x"), 2)
        self.assertEqual(disc.file_at(4096 + 10), 1)
        with self.assertRaises(ValueError):
            mhfu_data.DataBin(bytes(data), 0, len(data) + 2048, names={})


if __name__ == "__main__":
    unittest.main()
