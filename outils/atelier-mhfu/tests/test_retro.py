"""Behavioral contracts for the declarative runner and offline memory tools."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_retro as r
import atelier_pilot as ap
import retro_memory as m


class DataContracts(unittest.TestCase):
    def plan(self): return {"name":"trial", "steps":[{"op":"capture", "name":"proof"}]}

    def test_valid_and_invalid_scenarios(self):
        self.assertEqual(r.validate(self.plan())["name"], "trial")
        for value in (True, float("nan"), 9, 1201, "60"):
            plan=self.plan(); plan["seconds"]=value
            with self.assertRaises(ValueError): r.validate(plan)
        for step in ({"op":"unknown"}, {"op":"dump","name":"../original"},
                     {"op":"command","command":"dump original"}, {"op":"command","command":"state\nquit"},
                     {"op":"button","button":"forever"}, {"op":"advance","until":"anything"},
                     {"op":"select","text":2}, {"op":"capture","name":"proof","timeout":float("inf")}):
            plan=self.plan(); plan["steps"]=[step]
            with self.assertRaises(ValueError): r.validate(plan)

    def test_delta_aggregates_slots_without_losing_negative_counts(self):
        a={"available":True,"money":1000,"item_box":[{"id":202,"count":10}],"pouch":[],
           "equipment_box":[{"type":5,"id":178}],"farm_upgrades_hex":"00","kitchen_skill_ids_hex":"00"}
        b=copy.deepcopy(a);b["money"]=500;b["item_box"][0]["count"]=8
        b["equipment_box"].append({"type":5,"id":3})
        difference=r.delta(a,b)
        self.assertEqual(difference["money_delta"],-500)
        self.assertEqual(difference["item_box"],{"202":-2})
        self.assertEqual(difference["equipment_box"],{"5:3":1})
        self.assertTrue(r.assertion({"field":"equipment_box","key":"5:3","equals":1},difference)["passed"])
        self.assertFalse(r.assertion({"field":"money_delta","equals":0},difference)["passed"])
        self.assertTrue(r.assertion({"field":"item_box","key":"8","equals":0},difference)["passed"])
        with self.assertRaises(ValueError):r.delta({},b)

    def test_symbols_preserve_overlay_scope_and_nearest_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            (p/"one.symbol_addrs.txt").write_text("Npc = 0x08800010; // data\nFunction = 0x08800020;",encoding="utf-8")
            (p/"two.symbol_addrs.txt").write_text("Different = 0x08800010;",encoding="utf-8")
            symbols=m.Symbols.load(p)
            self.assertEqual(symbols.nearest(0x08800011,"one")["name"],"Npc")
            self.assertEqual(symbols.nearest(0x08800011,"two")["name"],"Different")
            self.assertEqual(symbols.nearest(0x08800011,"one")["offset"],1)
            self.assertIsNone(symbols.nearest(0,"one"))
            self.assertEqual(len(symbols.find("npc")),1)

    def test_objects_ignore_unaligned_or_truncated_matches_and_compare_fields(self):
        data=bytearray(64);needle=struct.pack("<I",0x089B6100)
        data[0:4]=needle;data[16:20]=needle;data[20:24]=struct.pack("<I",7)
        data[41:45]=needle;data[60:64]=needle
        result=m.objects(data,0x08800000,0x089B6100,size=16)
        self.assertEqual(result["addresses"],[0x08800000,0x08800010])
        self.assertEqual(result["differing_fields"],[{"offset":4,"values":["00000000","07000000"]}])
        with self.assertRaises(ValueError):m.objects(data,0,1,size=4097)

    def test_duplicate_symbol_filenames_remain_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('one','two'):
                (root/name).mkdir();(root/name/'shared.symbol_addrs.txt').write_text(name+' = 0x08800000;',encoding='utf-8')
            symbols=m.Symbols.load(root)
            self.assertIsNone(symbols.nearest(0x08800000,'shared'))
            self.assertEqual(symbols.nearest(0x08800000,'one/shared')['name'],'one')
            self.assertEqual(symbols.nearest(0x08800000,'two/shared')['name'],'two')

    def test_memory_diff_counts_all_bytes_and_splits_regions(self):
        a=bytes(100);b=b"\x01"*70+bytes(30)
        result=m.memory_diff(a,b,1000,regions=[{"name":"small","address":1010,"size":5}])
        self.assertEqual(result["changed_bytes"],70)
        self.assertEqual(sum(c["length"] for c in result["changes"]),70)
        row=next(c for c in result["changes"] if c["structure"]=="small")
        self.assertEqual((row["address"],row["length"]),(1010,5))
        with self.assertRaises(ValueError):m.memory_diff(b"1",b"22")

    def test_value_search_filters_against_previous_read(self):
        a=struct.pack("<4I",10,10,0,10)
        search=m.ValueSearch(a,0x08800000,4,10)
        self.assertEqual(len(search.values),3)
        b=struct.pack("<4I",9,10,0,8)
        self.assertEqual(len(search.next(b,changed=True)),2)
        self.assertEqual(search.next(b,value=9),{0x08800000:9})
        with self.assertRaises(ValueError):search.next(b"x")


class FakeSession:
    def __init__(self,directory):self.dir=directory;self.sent=[];self.w=self;self.pending=[];self.on_lines=None
    def alive(self):return True
    def debug_commands(self,commands):
        self.sent.extend(commands)
        payload=["[debug] 0x089C7508: AB CD EF 09"] if commands[0]=="peek 0x089C7508 4" else ["[debug] first","[debug] second"]
        self.pending += ["[debug] > "+commands[0],*payload,"[debug] > "+commands[1],"[debug] 0x089C7508: 00 00 00 09"]
    def new_lines(self):rows,self.pending=self.pending,[];return rows
    def inputs(self,lines):self.sent.extend(lines)


class DriverContracts(unittest.TestCase):
    def test_multiline_command_fence_and_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            s=FakeSession(Path(directory));cancel=threading.Event();p=ap.Pilot(s,cancel)
            driver=r.RetroPilot(p,time.monotonic()+5)
            self.assertEqual(driver.command("test"),["first","second"])
            self.assertEqual(driver.command("peek 0x089C7508 4"),["0x089C7508: AB CD EF 09"])
            cancel.set()
            with self.assertRaises(ap.Stopped):driver.command("test")

    def driver(self):
        s=FakeSession(Path(tempfile.gettempdir()));p=ap.Pilot(s)
        return r.RetroPilot(p,time.monotonic()+5)

    def test_selection_uses_observed_cursor_and_fails_on_unknown_label(self):
        d=self.driver();states=[{"menu":{"entries":["Buy","Sell"],"entries_complete":True,"cursor":0}},
                               {"menu":{"entries":["Buy","Sell"],"entries_complete":True,"cursor":1}}]
        buttons=[]
        with patch.object(d,"ui",side_effect=states),patch.object(d,"button",side_effect=buttons.append):d.select("Sell")
        self.assertEqual(buttons,["dpdown","b"])
        with patch.object(d,"ui",return_value=states[0]),self.assertRaises(ValueError):d.select("Missing")

    def test_advance_stops_at_menu_without_extra_confirm(self):
        d=self.driver();buttons=[]
        states=[{"scene":11,"menu":None,"dialogue_open":True},{"scene":11,"menu":{"cursor":0}}]
        with patch.object(d,"ui",side_effect=states),patch.object(d,"button",side_effect=buttons.append):d.advance_dialogue_until()
        self.assertEqual(buttons,["b"])
        with patch.object(d,"ui",return_value={"scene":0,"menu":None}),self.assertRaises(Exception):d.advance_dialogue_until()

    def test_confirmation_selects_on_the_horizontal_axis(self):
        d=self.driver();buttons=[]
        states=[{"menu":{"entries":["Yes","No"],"entries_complete":True,"cursor":1,"axis":"horizontal"}},
                {"menu":{"entries":["Yes","No"],"entries_complete":True,"cursor":0,"axis":"horizontal"}}]
        with patch.object(d,"ui",side_effect=states),patch.object(d,"button",side_effect=buttons.append):d.select("Yes")
        self.assertEqual(buttons,["dpleft","b"])

    def test_back_does_not_mistake_an_idle_house_for_the_village(self):
        d=self.driver()
        house={"task":10,"scene":0,"lobby_running":True,"lobby_loaded":True,"stage":66}
        village={**house,"stage":139}
        with patch.object(d,"ui",return_value={"scene":0}),patch.object(d.pilot,"snapshot",side_effect=[house,village,village]),patch.object(d,"interact") as move:
            d.back_to_village()
        self.assertEqual(move.call_args.args[:2],(0,"x"))

    def test_unknown_difference_field_cannot_pass_by_comparing_nulls(self):
        with self.assertRaises(ValueError):r.assertion({"field":"typo","equals":None},{})

    def test_report_escapes_transcript_and_emits_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/"frames").mkdir()
            r.report(p,{"state":"failed"},[{"elapsed":1,"scene":11,"menu":None,"runs":[{"text":"<script>"}]}])
            self.assertIn("&lt;script&gt;",(p/"retro-report.html").read_text(encoding="utf-8"))
            self.assertIn("<script>",(p/"transcript.txt").read_text(encoding="utf-8"))


if __name__=="__main__":unittest.main()
