"""Pure parts of the area tour: no game or runtime needed."""
from pathlib import Path
import struct
import json
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_zones  # noqa: E402
import zone_tour  # noqa: E402


class DecodeTests(unittest.TestCase):
    def test_lui_addiu_return(self):
        # The definitions() of area 98, as read in the running game.
        words = [0x3C0209D6, 0x03E00008, 0x2442E298, 0x00000000]
        self.assertEqual(atelier_zones.decode_return_address(words), 0x09D5E298)

    def test_negative_addiu_and_ori(self):
        self.assertEqual(atelier_zones.decode_return_address([0x3C020900, 0x2442FFF0]), 0x08FFFFF0)
        self.assertEqual(atelier_zones.decode_return_address([0x3C020900, 0x34420010]), 0x09000010)

    def test_no_v0_constant(self):
        self.assertIsNone(atelier_zones.decode_return_address([0x03E00008, 0x00000000]))

    def test_exits_chosen_by_map_id(self):
        # Area 89's exits(map_id), as read in the running game.
        words = [int(w, 16) for w in (
            "24020016 50A2000D 3C0209D6 2402000A 50A20008 3C0209D6 24020009 50A20003 3C0209D6 "
            "10000006 00001021 10000004 2442E348 10000002 2442E380 2442E3B8 03E00008 00000000").split()]
        self.assertEqual(atelier_zones.evaluate_return(words, 22), 0x09D5E3B8)
        self.assertEqual(atelier_zones.evaluate_return(words, 10), 0x09D5E380)
        self.assertEqual(atelier_zones.evaluate_return(words, 9), 0x09D5E348)
        self.assertEqual(atelier_zones.evaluate_return(words, 5), 0)
        count = [int(w, 16) for w in (
            "24020016 50A20008 24020001 2402000A 50A20005 24020001 24020009 50A20002 24020001 "
            "00001021 03E00008 00000000").split()]
        self.assertEqual(atelier_zones.evaluate_return(count, 10), 1)
        self.assertEqual(atelier_zones.evaluate_return(count, 3), 0)
        self.assertEqual(atelier_zones.evaluate_return([0x3C0209D6, 0x03E00008, 0x2442E298], 0), 0x09D5E298)
        self.assertIsNone(atelier_zones.evaluate_return([0x8C820000, 0x03E00008, 0x00000000]))  # lw: unknown

    def test_exit_layout_and_targets(self):
        raw = struct.pack("<Hh3f2f3f3f4x", 99, 0, 13200, 0, 13900, 500, 1000, 0, 0, 0, 18000, 50, 12600)
        self.assertEqual(len(raw), atelier_zones.EXIT_SIZE)
        exit = atelier_zones.parse_exit(raw)
        self.assertEqual((exit["destination"], exit["shape"], exit["size"], exit["height"]), (99, 0, 500, 1000))
        self.assertEqual(exit["arrival"], (18000, 50, 12600))
        self.assertEqual(atelier_zones.exit_target(exit), (13200, 250, 13900))
        box = dict(exit, shape=1, p=(0.0, 10.0, 0.0), q=(100.0, 0.0, 50.0))
        self.assertEqual(atelier_zones.exit_target(box), (50, 260, 25))


class PlacementTests(unittest.TestCase):
    def test_both_position_vectors_are_submitted_together(self):
        session = Mock()
        atelier_zones.place(session, 0x09000000, (1.0, -2.0, 3.0))
        session.ask.assert_not_called()
        session.w.debug_commands.assert_called_once_with([
            "poke32 0x09000200 1065353216", "poke32 0x09000204 3221225472",
            "poke32 0x09000208 1077936128", "poke32 0x09000210 1065353216",
            "poke32 0x09000214 3221225472", "poke32 0x09000218 1077936128"])

    def test_transition_snapshot_is_discarded_then_retried(self):
        session = Mock()
        loaded = {"stage": 72, "exits": [{"destination": 71}]}
        with patch.object(atelier_zones, "_current", side_effect=[
                atelier_zones.Unavailable("area changed while reading its exits"), loaded]), \
                patch.object(atelier_zones.time, "sleep"):
            self.assertEqual(atelier_zones.current(session), loaded)

    def test_stopped_session_does_not_claim_an_area(self):
        session = Mock()
        session.alive.return_value = False
        with self.assertRaises(atelier_zones.Unavailable):
            atelier_zones.current(session)

    def test_unexpected_destination_stops_old_area_writes(self):
        session = Mock()
        here = {"stage": 72, "player": 0x09000000}
        exit = {"destination": 71, "shape": 0, "p": (9400, -750, 5249), "height": 1500}
        with patch.object(atelier_zones, "place") as place, \
                patch.object(atelier_zones, "wait_stage", return_value=None), \
                patch.object(atelier_zones, "current", return_value={"stage": 16}):
            outcome = atelier_zones.traverse(session, here, exit)
        self.assertFalse(outcome["reached"])
        self.assertEqual(outcome["observed_stage"], 16)
        self.assertEqual(place.call_count, 1)

    def test_transient_arrival_tries_the_next_height_before_rejecting_exit(self):
        here = {"stage": 72, "player": 0x09000000}
        exit = {"destination": 71, "shape": 0, "p": (9400, -750, 5249), "height": 1500}
        with patch.object(atelier_zones, "place") as place, \
                patch.object(atelier_zones, "wait_stage", return_value=71), \
                patch.object(atelier_zones, "current", side_effect=[{"stage": 72}, {"stage": 71}]), \
                patch.object(atelier_zones.time, "sleep"):
            outcome = atelier_zones.traverse(Mock(), here, exit)
        self.assertTrue(outcome["reached"])
        self.assertEqual(outcome["transient_attempts"], 1)
        self.assertEqual(place.call_count, 2)


class RouteTests(unittest.TestCase):
    def test_unvisited_neighbour_first(self):
        self.assertEqual(zone_tour.next_step({1: [2, 3]}, {1, 2}, 1), 3)

    def test_shortest_path_towards_frontier(self):
        graph = {1: [2], 2: [1, 3], 3: [2, 4], 4: [3, 5]}
        self.assertEqual(zone_tour.next_step(graph, {1, 2, 3, 4}, 1), 2)
        self.assertEqual(zone_tour.next_step(graph, {1, 2, 3, 4}, 3), 4)

    def test_complete_when_nothing_left(self):
        self.assertIsNone(zone_tour.next_step({1: [2], 2: [1]}, {1, 2}, 2))


class HuntRouteTests(unittest.TestCase):
    def test_explicit_slay_species_never_falls_back_to_optional_boss(self):
        import hunt
        boss = {"type": 68, "health": 1}
        bullfango = {"type": 5, "health": 1}
        dead = {"type": 5, "health": 0}
        self.assertEqual(hunt.combat_targets([boss, dead], {5}, strict=True), [])
        self.assertEqual(hunt.combat_targets([boss, bullfango, dead], {5}, strict=True), [bullfango])
        self.assertEqual(hunt.combat_targets([boss], {5}), [boss])

    def test_patrol_advances_and_skips_an_unreachable_waypoint(self):
        import hunt
        graph = {68: [69], 69: [68, 73], 73: [69]}
        self.assertEqual(hunt.patrol_step(graph, 68, [68, 16, 73], 0), (69, 2))
        self.assertEqual(hunt.patrol_step(graph, 73, [68, 16, 73], 2), (69, 0))
        self.assertEqual(hunt.patrol_step({68: []}, 68, [68, 16], 0)[0], None)

    def test_prior_tour_routes_filter_flags_and_self_edges(self):
        import hunt
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tour.json"
            path.write_text(json.dumps({"tours": [{"quest": 11001, "areas": {
                "68": {"exits": [67, 75, 16384]}, "75": {"exits": [70]},
                "72": {"exits": [72, 71]}}}]}), encoding="utf-8")
            routes = hunt.load_routes(path, 11001)
            self.assertEqual(hunt.first_hop(routes, 68, 70), 75)
            self.assertEqual(routes[72], [71])
            self.assertNotIn(16384, routes[68])
            self.assertEqual(hunt.load_routes(path, 11101), {})

    def test_first_hop_towards_target(self):
        import hunt
        graph = {1: [2, 5], 2: [1, 3], 3: [2, 4], 5: [1]}
        self.assertEqual(hunt.first_hop(graph, 1, 4), 2)
        self.assertEqual(hunt.first_hop(graph, 1, 5), 5)
        self.assertIsNone(hunt.first_hop(graph, 1, 9))
        self.assertIsNone(hunt.first_hop(graph, 1, 1))


class MonsterSnapshotTests(unittest.TestCase):
    def test_freed_or_invalid_actor_does_not_count_as_a_dead_monster(self):
        for kind, stage, expected in ((1, 72, 1), (0, 72, 0), (1, 48461, 0)):
            block = bytearray(atelier_zones.HEALTH + 2 - atelier_zones.OBJECT_TYPE)
            block[0], block[1] = 68, kind
            struct.pack_into("<H", block, atelier_zones.OBJECT_STAGE - atelier_zones.OBJECT_TYPE, stage)
            slots = struct.pack("<20I", 0x09010000, *([0] * 19))
            with self.subTest(kind=kind, stage=stage), \
                    patch.object(atelier_zones, "u32", return_value=0x09000000), \
                    patch.object(atelier_zones, "read", return_value=slots), \
                    patch.object(atelier_zones, "read_many", return_value=[bytes(block), slots, struct.pack("<I", 0x09000000)]):
                self.assertEqual(len(atelier_zones.monsters(Mock())), expected)


if __name__ == "__main__":
    unittest.main()
