"""Look at the worn equipment's names in the game's own menu (diagnostic).

Boots with `loadout best` armed, starts quest 21001 and captures the pause
menu's Equipment Detail page, then stops the trial (no hunt is run, so a menu
left open cannot stall anything).
"""
import os
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "outils/atelier-mhfu"))
os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                  GIT_CONFIG_VALUE_0=str(PROJECT / "psprecomp-mhfu"))
import atelier_campaign  # noqa: E402
import atelier_core  # noqa: E402


def main():
    session = atelier_campaign.Session(atelier_core.Workshop(PROJECT), "20261020-153034-ceda7b", 300)
    try:
        print("ARMED", session.arm_loadout("best"), flush=True)
        if not session.wait_task(10, 150):
            return 1
        time.sleep(8)
        session.shot("view-village")
        print("START", session.ask("quest start 21001", "quest", 6.0), flush=True)
        if not session.wait_task(11, 90, quest=21001):
            return 1
        time.sleep(8)
        # Menu page 2: Status, Equipment Detail; then the next pages of the detail.
        session.inputs(["0:pad start 4", "30:pad dpright 4", "60:pad dpdown 4", "80:pad b 4",
                        "130:shot view-detail-1", "150:pad dpright 4", "190:shot view-detail-2",
                        "210:pad dpright 4", "250:shot view-detail-3"])
        time.sleep(12)
        print("RUN", session.run_id, flush=True)
        return 0
    finally:
        session.stop()


if __name__ == "__main__":
    raise SystemExit(main())
