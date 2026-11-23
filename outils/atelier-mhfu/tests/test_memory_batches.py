"""Console protocol and coherent snapshots with synthetic memory only."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_zones as zones


class Console:
    def __init__(self, pattern=False):
        self.w = self
        self.memory = {}
        self.pattern = pattern
        self.lines = []
        self.batches = []
        self.before_batch = None

    def put(self, address, data):
        self.memory.update({address + i: byte for i, byte in enumerate(data)})

    def pack(self, address, fmt, *values):
        self.put(address, struct.pack(fmt, *values))

    def alive(self):
        return True

    def new_lines(self):
        lines, self.lines = self.lines, []
        return lines

    def debug_commands(self, commands):
        self.batches.append(commands)
        if self.before_batch:
            self.before_batch(commands)
        for command in commands:
            _, address, length = command.split()
            address, length = int(address, 16), int(length)
            for offset in range(0, length, 16):
                start = address + offset
                data = bytes(self.memory.get(start + i, (start + i) & 255 if self.pattern else 0)
                             for i in range(min(16, length - offset)))
                self.lines.append(f"[debug] 0x{start:08X}:" + "".join(f" {byte:02X}" for byte in data))
        self.lines.reverse()  # Ordering must not be relied on.


class BatchTests(unittest.TestCase):
    def test_independent_ranges_and_chunking_use_one_submission(self):
        session = Console(pattern=True)
        result = zones.read_many(session, [(0x09000000, 520), (0x09001003, 5)])
        self.assertEqual(result[0], bytes(i & 255 for i in range(520)))
        self.assertEqual(result[1], bytes(range(3, 8)))
        self.assertEqual(len(session.batches), 1)
        self.assertEqual(len(session.batches[0]), 4)
        self.assertEqual(session.memory_stats["batches"], 1)

    def test_duplicate_and_overlapping_rows_do_not_truncate(self):
        session = Console(pattern=True)
        result = zones.read_many(session, [(0x09000000, 4), (0x09000000, 16), (0x09000000, 4)])
        self.assertEqual(result, [bytes(range(4)), bytes(range(16)), bytes(range(4))])
        self.assertEqual(len(session.batches[0]), 2)

    def test_empty_ranges_send_nothing(self):
        session = Console()
        self.assertEqual(zones.read_many(session, [(0x09000000, 0)]), [b""])
        self.assertEqual(session.batches, [])

    def test_incomplete_or_short_response_is_rejected(self):
        session = Console()
        session.debug_commands = Mock()
        session.alive = Mock(side_effect=[True, False])
        session.new_lines = Mock(side_effect=[[], ["[debug] 0x09000000: 01 02"]])
        with self.assertRaisesRegex(zones.Unavailable, "incomplete"):
            zones.read_many(session, [(0x09000000, 4)])

    def test_invalid_ranges_are_rejected_before_sending(self):
        for request in ((-1, 4), (0xFFFFFFFF, 2), (0, -1), (0, 1024 * 1024 + 1)):
            session = Console()
            with self.subTest(request=request), self.assertRaises(ValueError):
                zones.read_many(session, [request])
            self.assertEqual(session.batches, [])


def loaded_area():
    session = Console()
    gs, manager, players = 0x09000000, 0x09010000, 0x09020000
    area, vtable, function, definitions, table, player = (
        0x09030000, 0x09040000, 0x09050000, 0x09060000, 0x09070000, 0x09080000)
    for pointer, value in ((zones.GAMESYS_PTR, gs), (zones.STAGE_MANAGER_PTR, manager),
                           (zones.PLAYER_MANAGER_PTR, players), (manager, area), (area, vtable),
                           (players + zones.PLAYER_LIST, player)):
        session.pack(pointer, "<I", value)
    session.pack(gs + zones.STAGE_ID, "<H", 72)
    session.pack(manager + zones.MAP_ID, "<IH2x", 12, 72)
    session.pack(vtable + zones.DEFINITIONS_SLOT, "<3I", function, zones.BASE_EXITS, zones.BASE_EXIT_COUNT)
    session.pack(function, "<4I", 0x3C020906, 0x03E00008, 0x24420000, 0)
    session.pack(definitions + zones.EXITS, "<I", table)
    session.put(definitions + zones.EXIT_COUNT, b"\x01")
    session.pack(table, "<Hh3f2f3f3f4x", 71, 0, 1, 2, 3, 50, 100, 0, 0, 0, 4, 5, 6)
    session.pack(player + zones.POSITION, "<3f", 4, 5, 6)
    session.pack(player + zones.OBJECT_STAGE, "<H", 72)
    return session, (gs, manager, player, table)


class SnapshotTests(unittest.TestCase):
    def test_real_layout_contract_is_read_in_bounded_batches(self):
        session, _ = loaded_area()
        result = zones.current(session)
        self.assertEqual(result["stage"], 72)
        self.assertEqual(result["position"], (4, 5, 6))
        self.assertEqual(result["exits"][0]["destination"], 71)
        self.assertLessEqual(len(session.batches), 8)
        self.assertTrue(all(command.startswith("peek ") for batch in session.batches for command in batch))

    def test_area_change_during_exit_table_read_is_discarded(self):
        session, (gs, manager, player, table) = loaded_area()
        def transition(commands):
            if any(f"0x{table:08X}" in command for command in commands):
                for address in (gs + zones.STAGE_ID, manager + zones.MANAGER_STAGE, player + zones.OBJECT_STAGE):
                    session.pack(address, "<H", 16)
        session.before_batch = transition
        with self.assertRaisesRegex(zones.Unavailable, "changed"):
            zones._current(session)

    def test_invalid_player_index_is_rejected(self):
        session, (gs, _, _, _) = loaded_area()
        session.put(gs + zones.LOCAL_PLAYER, b"\x06")
        with self.assertRaises(zones.Unavailable):
            zones._current(session)

    def test_invalid_placement_never_writes_memory(self):
        session = Mock()
        for position in ((1, 2), (1, float("nan"), 3), (1, 2, float("inf"))):
            with self.assertRaises(ValueError):
                zones.place(session, 0x09000000, position)
        session.w.debug_commands.assert_not_called()

    def test_arrival_walking_is_bounded_and_released(self):
        session = Mock()
        session.state.return_value = (11, 11001, 72)
        with patch.object(zones, "current", return_value={"stage": 72}):
            self.assertEqual(zones.wait_stage(session, 72, walk_in_frames=240), 72)
        session.inputs.assert_called_once_with(["0:axis leftx 0", "0:axis lefty -1", "240:axis lefty 0"])
        with self.assertRaises(ValueError):
            zones.wait_stage(session, 72, walk_in_frames=601)


if __name__ == "__main__":
    unittest.main()
