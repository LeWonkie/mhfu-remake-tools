"""Reproducible campaign benchmark: the same quests, seed and RTC, timed by phase.

    python bench_atelier.py --mode legacy|fast [--repeat N] [QUESTS...]

Each repetition runs one campaign (`atelier_campaign` for `legacy`, the state
driver `atelier_pilot` for `fast`) and writes `bench.json` in
`atelier-psprecomp/bench/<date>-<mode>/`:

- real seconds of the whole campaign, of the boot and of each quest phase;
- emulated seconds, from the game's `[perf]` lines (one a real second, with
  the emulation speed) attributed to the phase in which the driver read them,
  and the fast-forward stretches the game reports itself;
- the driver's own CPU time (`time.process_time`), its fixed pauses and its
  memory batches, apart from the time spent waiting for the game.

Only one game runs at a time. A repetition that does not end with every quest
back in the village is reported as such, never averaged as a success.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import atelier_campaign
import atelier_core

SEED = "20261020-153034-ceda7b"
RTC_US = 1609459200123456
QUESTS = [1001, 1004, 11001, 21001]
PERF = re.compile(r"^\[perf\] fps [\d.]+ game ([\d.]+) speed (\d+)%")
FF_OFF = re.compile(r"^\[fast-forward\] off \((\w+)\): (\d+) ms of game time, \d+ vblanks, in (\d+) ms real")


class Meter:
    """Times phases in real time, emulated time and driver CPU time."""

    def __init__(self):
        self.began = time.monotonic()
        self.cpu_began = time.process_time()
        self.phase = "boot"
        self.quest = None
        self.phases: list[dict] = []
        self.fixed_sleep = 0.0
        self.ff_stretches: list[dict] = []
        self._open = self._new(self.phase, None)

    def _new(self, phase, quest):
        return {"phase": phase, "quest": quest, "real": time.monotonic(), "cpu": time.process_time(),
                "emulated": 0.0, "perf_lines": 0, "sleep": self.fixed_sleep}

    def enter(self, phase: str, quest=None) -> None:
        now, cpu = time.monotonic(), time.process_time()
        done = self._open
        self.phases.append({"phase": done["phase"], "quest": done["quest"],
                            "real_s": round(now - done["real"], 3), "cpu_s": round(cpu - done["cpu"], 3),
                            "emulated_s": round(done["emulated"], 2), "perf_lines": done["perf_lines"],
                            "fixed_sleep_s": round(self.fixed_sleep - done["sleep"], 2)})
        self._open = self._new(phase, quest)

    def close(self, phase: str, quest=None) -> None:
        """Names the interval that just ended (the state driver knows it only at its end)."""
        self._open["phase"], self._open["quest"] = phase, quest
        self.enter("between", quest)

    def lines(self, lines: list[str]) -> None:
        for line in lines:
            match = PERF.match(line)
            if match:
                self._open["emulated"] += int(match[2]) / 100.0
                self._open["perf_lines"] += 1
                continue
            match = FF_OFF.match(line)
            if match:
                self.ff_stretches.append({"reason": match[1], "game_ms": int(match[2]), "real_ms": int(match[3])})

    def summary(self) -> dict:
        self.enter("_closed")
        totals: dict[str, dict] = {}
        for item in self.phases:
            if item["phase"] == "_closed":
                continue
            total = totals.setdefault(item["phase"], {"real_s": 0.0, "cpu_s": 0.0, "emulated_s": 0.0,
                                                      "fixed_sleep_s": 0.0, "count": 0})
            for key in ("real_s", "cpu_s", "emulated_s", "fixed_sleep_s"):
                total[key] = round(total[key] + item[key], 3)
            total["count"] += 1
        ff = [s for s in self.ff_stretches if s["real_ms"] > 0]
        return {"real_s": round(time.monotonic() - self.began, 2),
                "cpu_s": round(time.process_time() - self.cpu_began, 2),
                "fixed_sleep_s": round(self.fixed_sleep, 2), "phases": totals, "phase_log": self.phases,
                "ff_stretches": len(ff),
                "ff_ratio": round(sum(s["game_ms"] for s in ff) / sum(s["real_ms"] for s in ff), 2) if ff else None}


def _legacy(workshop, quests, meter: Meter, look: float) -> Path:
    """The existing campaign, unchanged, with its calls observed."""
    session_cls = atelier_campaign.Session
    original = {"new_lines": session_cls.new_lines, "ask": session_cls.ask, "shot": session_cls.shot}
    real_sleep = time.sleep

    class Clock:
        @staticmethod
        def sleep(seconds):
            meter.fixed_sleep += seconds
            real_sleep(seconds)

        def __getattr__(self, name):
            return getattr(time, name)

    def new_lines(self):
        lines = original["new_lines"](self)
        meter.lines(lines)
        return lines

    def ask(self, line, expect, timeout=10.0):
        words = line.split()
        if words[:2] == ["quest", "start"]:
            meter.enter("start", int(words[2]))
        elif words[:2] == ["quest", "time"]:
            meter.enter("end", meter._open["quest"])
        answer = original["ask"](self, line, expect, timeout)
        return answer

    def shot(self, name):
        quest = meter._open["quest"]
        if name.endswith("-arrivee"):
            meter.enter("look", quest)
        elif name.endswith("-resultat"):
            meter.enter("return", quest)
        elif name.endswith("-village"):
            pass
        result = original["shot"](self, name)
        if name.endswith("-village"):
            meter.enter("between", quest)
        return result

    wait_task = session_cls.wait_task

    def wait(self, task, timeout, quest=None):
        found = wait_task(self, task, timeout, quest)
        if task == 11 and found and meter._open["phase"] == "start":
            meter.enter("load", meter._open["quest"])
        return found

    clock = Clock()
    patches = {"new_lines": new_lines, "ask": ask, "shot": shot, "wait_task": wait}
    saved_time = atelier_campaign.time
    try:
        for name, value in patches.items():
            setattr(session_cls, name, value)
        atelier_campaign.time = clock
        return atelier_campaign.run_campaign(workshop, quests, SEED, look, log=lambda m: print(m, flush=True),
                                             rtc_us=RTC_US)
    finally:
        atelier_campaign.time = saved_time
        for name, value in original.items():
            setattr(session_cls, name, value)
        session_cls.wait_task = wait_task


def _fast(workshop, quests, meter: Meter, look: float, speed: int, captures: str) -> Path:
    import atelier_pilot
    return atelier_pilot.run_campaign(workshop, quests, SEED, rtc_us=RTC_US, log=lambda m: print(m, flush=True),
                                      mode="fast", speed=speed, captures=captures, look_seconds=look,
                                      meter=meter, use_cache=False)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Benchmark des campagnes MHFU")
    parser.add_argument("quests", nargs="*", type=int, default=QUESTS)
    parser.add_argument("--mode", choices=("legacy", "fast"), required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--look", type=float, default=12.0)
    parser.add_argument("--speed", type=int, default=8)
    parser.add_argument("--captures", choices=("fast", "detailed"), default="fast")
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 5:
        parser.error("1..5 répétitions")
    workshop = atelier_core.Workshop(Path(__file__).resolve().parents[2])
    out = workshop.data_dir / "bench" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + args.mode)
    out.mkdir(parents=True)
    repetitions = []
    for index in range(args.repeat):
        meter = Meter()
        if args.mode == "legacy":
            campaign = _legacy(workshop, args.quests, meter, args.look)
        else:
            campaign = _fast(workshop, args.quests, meter, args.look, args.speed, args.captures)
        summary = meter.summary()
        data = json.loads((campaign / "campagne.json").read_text(encoding="utf-8"))
        results = data["results"]
        ok = [r for r in results if r.get("returned") and not r.get("crashed")]
        summary.update(campaign=str(campaign), state=data.get("state"), quests=len(args.quests),
                       returned=len(ok), all_returned=len(ok) == len(args.quests),
                       per_quest_s=round(summary["real_s"] / len(args.quests), 2))
        repetitions.append(summary)
        atelier_core._write_json(out / "bench.json", {"mode": args.mode, "quests": args.quests, "seed": SEED,
                                                      "rtc_us": RTC_US, "speed": args.speed, "look": args.look,
                                                      "captures": args.captures, "repetitions": repetitions})
        print(f"[bench] {args.mode} #{index + 1}: {summary['real_s']} s, {summary['returned']}/{len(args.quests)} "
              f"de retour, {summary['per_quest_s']} s/quête, CPU pilote {summary['cpu_s']} s, "
              f"pauses fixes {summary['fixed_sleep_s']} s, ×{summary['ff_ratio']}", flush=True)
    good = [r["real_s"] for r in repetitions if r["all_returned"]]
    if good:
        print(f"[bench] {args.mode}: {len(good)}/{len(repetitions)} complètes, moyenne {statistics.mean(good):.1f} s"
              + (f", écart-type {statistics.stdev(good):.1f} s" if len(good) > 1 else ""), flush=True)
    print("[bench] ->", out / "bench.json", flush=True)
    return 0 if len(good) == len(repetitions) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
