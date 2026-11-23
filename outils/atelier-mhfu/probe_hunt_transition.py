"""Measure quest 11001 transitions on a fresh, bounded trial."""
from __future__ import annotations

import json
import argparse
import math
import os
from pathlib import Path
import struct
import time

import atelier_campaign as campaign
import atelier_core as core
import atelier_zones as zones


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--walk", action="store_true")
    parser.add_argument("--walk-in", action="store_true", help="Keep walking after the destination begins loading")
    args = parser.parse_args()
    os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                      GIT_CONFIG_VALUE_0=str(Path(__file__).resolve().parents[2] / "psprecomp-mhfu"))
    core.MAX_SECONDS = 600
    workshop = core.Workshop(Path(__file__).resolve().parents[2])
    session = campaign.Session(workshop, "20261020-153034-ceda7b", 600,
                               variant=core.register_rtc_variant("space_trace", 1609459200123456))
    events = []
    began = time.monotonic()

    def note(**event):
        event["t"] = round(time.monotonic() - began, 2)
        events.append(event)
        (session.dir / "transition_probe.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
        print(json.dumps(event), flush=True)

    def snapshot():
        here = zones.current(session)
        manager = zones.u32(session, zones.STAGE_MANAGER_PTR)
        here["manager_stage"] = struct.unpack("<H", zones.read(session, manager + 0x288, 2))[0]
        here["player_stage"] = struct.unpack("<H", zones.read(session, here["player"] + zones.OBJECT_STAGE, 2))[0]
        return here

    def walk_in(destination):
        deadline = time.monotonic() + 15
        while session.alive() and time.monotonic() < deadline:
            state = session.state()
            if state and state[2] == destination:
                session.inputs(["0:axis lefty -1", "240:axis lefty 0"])
                note(kind="walk-in", destination=destination)
                return
            time.sleep(0.2)

    try:
        if not session.wait_task(10, 150):
            raise RuntimeError("village not reached")
        time.sleep(8)
        note(kind="start", answer=session.ask("quest start 11001", "quest", 6))
        if not session.wait_task(11, 90, quest=11001):
            raise RuntimeError("quest not reached")
        session.ask("hold health on", "health", 6)
        time.sleep(8)
        for destination in ((67, 44, 16) if args.walk else (67, 44, 16, 72)):
            here = snapshot()
            note(kind="before", here=here)
            exit = next(e for e in here["exits"] if e["destination"] == destination)
            zones.go_through(session, here, exit)
            note(kind="placed", destination=destination)
            if args.walk_in and destination == 72:
                walk_in(destination)
            for _ in range(1 if args.walk or (args.walk_in and destination != 72) else 4):
                note(kind="after", wanted=destination, here=snapshot())
                time.sleep(2)
            session.shot(f"arrive-{destination}")
        if args.walk:
            here = snapshot()
            player = here["player"]

            def position():
                return struct.unpack("<3f", zones.read(session, player + zones.POSITION, 12))

            def pulse(x, y, frames=30):
                session.inputs([f"0:axis leftx {x}", f"0:axis lefty {y}",
                                f"{frames}:axis leftx 0", f"{frames}:axis lefty 0"])
                time.sleep(frames / 30 + 0.5)

            basis = []
            for x, y in ((1, 0), (0, 1)):
                before = position()
                pulse(x, y)
                after = position()
                basis.append((after[0] - before[0], after[2] - before[2]))
                note(kind="calibration", axis=[x, y], before=before, after=after)
            a, b = basis
            determinant = a[0] * b[1] - a[1] * b[0]
            if abs(determinant) < 10:
                raise RuntimeError("walking calibration did not move the hunter")
            # Solve the measured camera-relative basis for the world's -Z.
            ax, ay = b[0] / determinant, -a[0] / determinant
            length = math.hypot(ax, ay)
            ax, ay = ax / length, ay / length
            zones.place(session, player, (11000, 200, 7600))
            time.sleep(1)
            note(kind="approach", axis=[ax, ay], position=position())
            for attempt in range(8):
                pulse(ax, ay, 45)
                now = snapshot()
                note(kind="walking", attempt=attempt, here=now)
                if now["stage"] == 72:
                    break
            session.shot("walk-arrive-72")
            for _ in range(4):
                note(kind="walk-settled", here=snapshot())
                time.sleep(2)
        here = snapshot()
        note(kind="settled72", here=here)
        for destination in (71, 75):
            here = snapshot()
            if here["stage"] != 72:
                note(kind="unexpected", here=here)
                break
            exit = next(e for e in here["exits"] if e["destination"] == destination)
            zones.go_through(session, here, exit)
            note(kind="placed", destination=destination)
            if args.walk_in:
                walk_in(destination)
            for _ in range(5):
                note(kind="after", wanted=destination, here=snapshot())
                time.sleep(2)
            session.shot(f"try-{destination}")
        note(kind="targets", targets=zones.quest_targets(session), monsters=zones.monsters(session))
    finally:
        session.stop()
        print("PROBE", session.dir, flush=True)


if __name__ == "__main__":
    main()
