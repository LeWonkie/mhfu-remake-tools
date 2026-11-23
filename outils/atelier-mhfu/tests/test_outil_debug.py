from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import outil_debug


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1


def events_until_end(events: queue.Queue, timeout: float = 5.0) -> list:
    seen, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=0.1)
        except queue.Empty:
            continue
        seen.append(event)
        if event[0] == "ended":
            return seen
    raise AssertionError("the reader did not end")


class GameTests(unittest.TestCase):
    def test_start_copies_the_save_and_turns_the_commands_on(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save = root / "copy/ms0/PSP/SAVEDATA/ULUS10391"
            save.mkdir(parents=True)
            (save / "MHP2NDG.BIN").write_bytes(b"save")
            process = FakeProcess()
            process.returncode = 0
            events = queue.Queue()
            game = outil_debug.Game(root, events, root / "copy/../copy")
            with mock.patch.object(outil_debug.subprocess, "Popen", return_value=process) as popen:
                folder = game.start({"EXTRA": "1"})
            arguments, options = popen.call_args
            self.assertEqual(arguments[0][1], str((root / "copy").resolve()), "the game folder is absolute")
            env = options["env"]
            self.assertEqual(env["MHP3RD_DEBUG_MENU"], "1")
            self.assertEqual(env["MHP3RD_DEBUG_COMMANDS"], str(folder / "debug.txt"))
            self.assertEqual(env["MHP3RD_INPUT_LIVE"], str(folder / "input.txt"))
            self.assertEqual(env["EXTRA"], "1")
            self.assertEqual((folder / "sauvegarde-avant/ULUS10391/MHP2NDG.BIN").read_bytes(), b"save")
            self.assertTrue(folder.name.endswith("-debug") and folder.parent.name == "sessions-psprecomp")
            events_until_end(events)

    def test_answers_go_to_the_command_that_asked(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for name in ("debug.txt", "input.txt", "stdout.txt"):
                (folder / name).write_bytes(b"")
            events = queue.Queue()
            game = outil_debug.Game(folder, events)
            game.process, game.folder = FakeProcess(), folder
            answers = {}
            game.send("item find herb", lambda lines: answers.setdefault("items", lines))
            game.send("pouch", lambda lines: answers.setdefault("pouch", lines))
            game.send("state")
            game.press("pad b 6")
            self.assertEqual((folder / "debug.txt").read_text(encoding="utf-8"), "item find herb\npouch\nstate\n")
            self.assertEqual((folder / "input.txt").read_text(encoding="utf-8"), "0:pad b 6\n")
            with self.assertRaises(ValueError):
                game.send("pouch\nmoney 5")
            (folder / "stdout.txt").write_text(
                "[perf] fps 30\n[debug] > item find herb\n[debug] item 163 Herb\n[debug] item 173 Mountain Herbs\n"
                "[debug] > pouch\n[debug] pouch: 173x5\n[debug] > state\n[debug] hunter H, HR 0\n", encoding="utf-8")
            reader = threading.Thread(target=game._read, args=(game.process, folder / "stdout.txt"))
            reader.start()
            time.sleep(1.0)
            game.process.returncode = 0
            reader.join(5)
            seen = events_until_end(events)
            for event in seen:
                if event[0] == "answer" and event[3]:
                    event[3](event[2])
            self.assertEqual(answers["items"], ["item 163 Herb", "item 173 Mountain Herbs"])
            self.assertEqual(answers["pouch"], ["pouch: 173x5"])
            state = [e for e in seen if e[0] == "answer" and e[1] == "state"]
            self.assertEqual(state[0][2], ["hunter H, HR 0"])
            self.assertIsNone(state[0][3], "state has no handler: the window shows it in its header")
            self.assertNotIn("[perf] fps 30", [e[1] for e in seen if e[0] == "log"])

    def test_nothing_is_sent_without_a_game(self):
        game = outil_debug.Game(Path("."), queue.Queue())
        with self.assertRaises(RuntimeError):
            game.send("state")
        with self.assertRaises(RuntimeError):
            game.press("pad b 6")


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.tool = outil_debug.Tool(Path(cls.temporary.name))
        cls.tool.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.tool.root.destroy()
        cls.temporary.cleanup()

    def test_item_names_are_listed_and_searched(self):
        tool = self.tool
        tool._fill_names(["item 1 Book of Combos 1", "item 163 Herb", "item 173 Mountain Herbs"])
        self.assertEqual(len(tool.item_tree.get_children()), 3)
        tool.search.set("herb")
        rows = [tool.item_tree.item(row, "values") for row in tool.item_tree.get_children()]
        self.assertEqual([(int(i), name) for i, name in rows], [(163, "Herb"), (173, "Mountain Herbs")])
        tool.search.set("173")
        self.assertEqual(len(tool.item_tree.get_children()), 1)
        tool.search.set("")

    def test_pouch_is_shown_with_names(self):
        tool = self.tool
        tool.names.update({173: "Mountain Herbs"})
        tool._show_pouch(["pouch: 173x5 8x10"])
        self.assertEqual(tool.pouch_list.get(0, "end"), ("Mountain Herbs  ×5   (id 173)", "objet 8  ×10   (id 8)"))
        tool._show_pouch(["box: empty"])
        self.assertEqual(tool.pouch_list.get(0, "end"), ("box: empty",))

    def test_quest_ids_and_gathering_points(self):
        tool = self.tool
        tool._fill_quests(["quest ids: 1-3 101 1001-1002"])
        self.assertEqual(list(tool.quest_box.cget("values")), ["1", "2", "3", "101", "1001", "1002"])
        tool._show_gather(["area 93: 2 gathering points",
                           "gather 0 at 11300 1450 5800: kind 0, counters 6 2, table 4: 173 Mountain Herbs 100%",
                           "gather 1 at 10100 1250 7700: kind 0, counters 255 255, table 5: 292 Bone 100%"])
        rows = [tool.gather_tree.item(row, "values") for row in tool.gather_tree.get_children()]
        self.assertEqual(rows[0], ("0", "11300 1450 5800", "6 / 2", "173 Mountain Herbs 100%"))
        self.assertEqual(len(rows), 2)

    def test_where_fills_the_coordinates(self):
        tool = self.tool
        tool._show_where(["area 93 at 11300 1450 -5800"])
        self.assertEqual([var.get() for var in tool.coords], ["11300", "1450", "-5800"])

    def test_actions_without_a_game_say_so(self):
        tool = self.tool
        tool._send("pouch")
        self.assertEqual(tool.status.get(), "le jeu n'est pas lancé")

    def test_status_is_shown_and_the_cheats_follow_it(self):
        tool = self.tool
        tool.catalog[10001] = {"title": "Mountain Herb Picking", "objective": "Deliver 5\nMountain Herbs", "flags": 0x80002}
        tool.names[1] = "known"  # no item list asked for
        tool._show_status(["status task 11 area 93 quest 10001 mission 2 time 125 health 100 stamina 300 "
                           "sharpness 150 zenny 1234 monsters 2 hold timer,still"])
        self.assertEqual(tool.live["task"].get(), "Quête")
        self.assertEqual(tool.live["time"].get(), "2:05")
        self.assertEqual(tool.live["mission"].get(), "en cours")
        self.assertEqual(tool.quest_title.get(), "Mountain Herb Picking — Deliver 5 Mountain Herbs")
        self.assertTrue(tool.cheats["timer"].get() and tool.still.get())
        self.assertFalse(tool.cheats["health"].get())
        tool._show_status(["status task 10 area 139 quest 1 mission - time - health - stamina - sharpness - "
                           "zenny 5 monsters - hold none"])
        self.assertEqual((tool.live["task"].get(), tool.live["time"].get(), tool.quest_title.get()), ("Village", "—", ""))
        self.assertFalse(tool.cheats["timer"].get() or tool.still.get())
        tool._show_status(["status task 11 area 93 quest 1020 mission 5 time 0 health 0 stamina 0 sharpness 0 "
                           "zenny 5 monsters 1 hold none"])
        self.assertEqual(tool.live["mission"].get(), "échouée")

    def test_monsters_are_listed(self):
        tool = self.tool
        tool._show_monsters(["2 monsters", "monster 0 species 6 Yian Kut-Ku, area 93, health 1840, at 4000 0 -5, boss",
                             "monster 2 species 62 Conga, area 94, health 250, at 0 0 0"])
        rows = [tool.monster_tree.item(row, "values") for row in tool.monster_tree.get_children()]
        self.assertEqual(rows[0], ("0", "Yian Kut-Ku (espèce 6)", "93", "1840", "4000 0 -5", "boss"))
        self.assertEqual(rows[1][5], "")
        tool._show_monsters(["no quest running"])
        self.assertEqual(tool.monster_tree.get_children(), ())
        self.assertEqual(tool.status.get(), "no quest running")

    def test_equipment_by_name(self):
        tool = self.tool
        tool._fill_equipment(["equip 5 3 Buster Sword", "equip 5 944 Ukanlos Soul Hatchet", "equip 1 374 White Fatalis Head Z"])
        tool.equip_type.set("Arme lame")
        tool.equip_search.set("ukanlos")
        rows = [tool.equip_tree.item(row, "values") for row in tool.equip_tree.get_children()]
        self.assertEqual([(int(i), n) for i, n in rows], [(944, "Ukanlos Soul Hatchet")])
        tool.equip_type.set("Tête")
        tool.equip_search.set("")
        self.assertEqual(len(tool.equip_tree.get_children()), 1)
        tool._show_worn(["worn 5:944 Ukanlos Soul Hatchet", "worn 1:9"])
        self.assertEqual(tool.worn_list.get(0, "end"), ("Arme lame : Ukanlos Soul Hatchet", "Tête : id 9"))

    def test_quests_are_searched_by_name_and_kind(self):
        tool = self.tool
        tool.catalog = {1020: {"title": "Attack of the Yian Kut-Ku", "objective": "Hunt the Yian Kut-Ku", "flags": 4,
                               "minutes": 50, "monsters": "Yian Kut-Ku", "description": "A bird.", "failure": "", "client": "X"},
                        10001: {"title": "Mountain Herb Picking", "objective": "Deliver 5 Mountain Herbs", "flags": 0x80002,
                                "minutes": 50, "monsters": "Popo", "description": "", "failure": "", "client": ""}}
        tool.quest_search.set("kut-ku")
        rows = [tool.quest_tree.item(row, "values") for row in tool.quest_tree.get_children()]
        self.assertEqual([(int(r[0]), r[3]) for r in rows], [(1020, "Chasse")])
        tool.quest_search.set("")
        tool.quest_filter.set("Livraison")
        tool._filter_quests()
        rows = tool.quest_tree.get_children()
        self.assertEqual([int(tool.quest_tree.item(r, "values")[0]) for r in rows], [10001])
        tool.quest_tree.selection_set(rows[0])
        tool._show_quest()
        self.assertEqual(tool.quest_id.get(), "10001")
        self.assertIn("Deliver 5 Mountain Herbs", tool.quest_text.get("1.0", "end"))
        tool.quest_filter.set("Toutes")
        tool._filter_quests()


class HelperTests(unittest.TestCase):
    def test_status_words_and_quest_kinds(self):
        self.assertEqual(outil_debug.parse_status("status task 11 area 93 hold none"),
                         {"task": "11", "area": "93", "hold": "none"})
        self.assertEqual(outil_debug.parse_status("hunter H"), {})
        self.assertEqual(outil_debug.quest_kind(0x1004001), "Abattage (entraînement)")
        self.assertEqual(outil_debug.quest_kind(0x80002), "Livraison")
        self.assertEqual(outil_debug.quest_kind(0), "Autre")

    def test_save_copies_are_kept_listed_and_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "game/ms0/PSP/SAVEDATA"
            (saves / "ULJM05500").mkdir(parents=True)
            (saves / "ULJM05500/DATA.BIN").write_bytes(b"first")
            launch = root / "sessions-psprecomp/20261001-120000-debug/sauvegarde-avant/ULJM05500"
            launch.mkdir(parents=True)
            (launch / "DATA.BIN").write_bytes(b"launch")
            copies = outil_debug.Saves(root, saves)
            kept = copies.keep()
            self.assertEqual((kept / "ULJM05500/DATA.BIN").read_bytes(), b"first")
            listed = copies.copies()
            self.assertEqual([origin for _, origin, _ in listed], ["manuelle", "lancement de l'outil"])
            before = copies.restore(launch.parent)
            self.assertEqual((saves / "ULJM05500/DATA.BIN").read_bytes(), b"launch")
            self.assertEqual((before / "ULJM05500/DATA.BIN").read_bytes(), b"first")
            self.assertIn("avant restauration", [origin for _, origin, _ in copies.copies()])


if __name__ == "__main__":
    unittest.main()
