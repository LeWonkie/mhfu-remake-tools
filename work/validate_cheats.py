"""Real-game check of the MHFU test cheats and `loadout best` on one hunt (21001).

In the village (departing phase): money, pouch and state through the game's
commands. On the quest: stamina (Player +0x55A) and sharpness (+0x10F2) read
at each controller event; held cheats should keep them at their most.
"""
import json
import os
from pathlib import Path
import struct
import sys
import time
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "outils/atelier-mhfu"))
os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                  GIT_CONFIG_VALUE_0=str(PROJECT / "psprecomp-mhfu"))
import atelier_campaign  # noqa: E402
import atelier_core  # noqa: E402
import atelier_zones as zones  # noqa: E402
import hunt  # noqa: E402


def main():
    holder, report = [], {"village": {}, "quest": []}

    class CheckSession(atelier_campaign.Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder.append(self)

    def progress(event):
        if not holder or not holder[0].alive():
            return
        session = holder[0]
        phase = event.get("phase")
        try:
            if phase == "departing" and not report["village"]:
                ask = lambda line, expect: (session.ask(line, expect, 6.0) or "").replace("[debug] ", "")
                report["village"] = {"money": ask("money 9999999", "zenny"), "pouch_set": ask("pouch 1 10", "pouch"),
                                     "pouch": ask("pouch", "pouch:"), "state": ask("state", "hunter ")}
                print("VILLAGE", json.dumps(report["village"]), flush=True)
                session.shot("village-gear")
            elif "--menu" in sys.argv and phase in ("observing", "navigating", "combat") and not report.get("menu"):
                # Items and Status captures. Diagnostic only: in run 20261021-084319-ba2d91
                # the Status screen stayed open and the hunt stalled behind it.
                report["menu"] = True
                session.inputs(["0:pad start 4", "30:pad b 4", "70:shot menu-items",
                                "90:pad a 4", "110:pad dpright 4", "140:shot menu-page2", "160:pad b 4",
                                "200:shot menu-page2-open", "220:pad a 4", "240:pad a 4", "260:pad start 4"])
                time.sleep(11)
            elif phase in ("observing", "navigating", "combat"):
                gamesys, manager = (struct.unpack("<I", d)[0] for d in zones.read_many(
                    session, [(zones.GAMESYS_PTR, 4), (zones.PLAYER_MANAGER_PTR, 4)]))
                index = zones.read(session, gamesys + zones.LOCAL_PLAYER, 1)[0]
                player = zones.u32(session, manager + zones.PLAYER_LIST + 4 * index)
                stamina, sharp = zones.read_many(session, [(player + 0x55A, 2), (player + 0x10F2, 2)])
                sample = {"t": event.get("elapsed_seconds"), "phase": phase,
                          "stamina": struct.unpack("<h", stamina)[0], "sharpness": struct.unpack("<h", sharp)[0]}
                report["quest"].append(sample)
        except zones.Unavailable as error:
            print("RETRY", error, flush=True)

    workshop = atelier_core.Workshop(PROJECT)
    with patch.object(atelier_campaign, "Session", CheckSession):
        result = hunt.hunt(workshop, "20261020-153034-ceda7b", 21001, 1609459200123456, 360, progress=progress)
    report.update({k: result.get(k) for k in ("run_id", "state", "success", "returned_to_village",
                                               "loadout", "loadout_answer", "cheats_held")})
    out = PROJECT / "work" / f"validate-cheats-{result['run_id']}.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    q = report["quest"]
    print("CHEATS", json.dumps({k: report[k] for k in ("run_id", "state", "success", "loadout_answer", "cheats_held")}),
          "samples", len(q), "stamina", sorted({s["stamina"] for s in q}), "sharpness", sorted({s["sharpness"] for s in q}),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
