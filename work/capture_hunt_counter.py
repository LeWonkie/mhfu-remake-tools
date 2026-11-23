"""Inspect Quest Info during a navigation settling window, then close it."""
import json
from pathlib import Path
import time

run = Path(__file__).resolve().parents[1] / "atelier-psprecomp/runs/20261021-064042-d33157"
deadline = time.monotonic() + 40
previous = None
while time.monotonic() < deadline:
    data = json.loads((run / "hunt.json").read_text(encoding="utf-8"))
    if data["state"] != "running":
        print("Trial already ended")
        break
    events = data["events"]
    latest = events[-1]
    identity = (latest["kind"], latest["t"])
    if latest["kind"] == "navigate" and identity != previous and time.time() - (run / "hunt.json").stat().st_mtime < 0.5:
        time.sleep(1.3)
        with (run / "input.txt").open("a", encoding="utf-8") as stream:
            stream.write("0:pad start 4\n12:pad dpdown 4\n24:pad dpdown 4\n36:pad b 4\n"
                         "70:shot h11001-counter-check\n90:pad a 4\n102:pad start 4\n")
        print("Quest Info capture queued", latest["frm"], latest["to"], latest["t"])
        break
    previous = identity
    time.sleep(0.05)
else:
    print("No suitable navigation window")
