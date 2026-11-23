"""Dump VRAM and the game's display-list memory in the village and on a quest.

Read-only for the game: only the debug console's `dump` command is used, in a
new atelier run (its own save copy). Dumps go to --out, which must not exist
and must have no spaces (the console splits its arguments on whitespace).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import atelier_campaign  # noqa: E402
import atelier_core  # noqa: E402

VRAM = (0x04000000, 0x00200000)


def dump(session, out: Path, name: str, start: int, length: int, log: list) -> None:
    path = out / f"{name}.bin"
    answer = session.ask(f"dump {path} 0x{start:08X} 0x{length:X}", "dumped", 20.0)
    log.append({"name": name, "start": f"0x{start:08X}", "length": length, "answer": answer,
                "size": path.stat().st_size if path.is_file() else None})
    print(name, answer, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--quest", type=int, default=11001)
    parser.add_argument("--rtc-us", type=int, default=1609459200123456)
    parser.add_argument("--variant", default="space_trace")
    parser.add_argument("--region", action="append", default=[],
                        help="NAME:START:LENGTH (hex) dumped in both places, e.g. src:0x09447D10:0x3DEC0")
    parser.add_argument("--walk", action="store_true", help="walk forward, then dump again")
    parser.add_argument("--no-vram", action="store_true", help="leave VRAM out of the dumps")
    args = parser.parse_args()
    if " " in str(args.out):
        parser.error("--out must not contain spaces")
    args.out.mkdir(parents=True, exist_ok=False)
    regions = [] if args.no_vram else [("vram", *VRAM)]
    for item in args.region:
        name, start, length = item.split(":")
        regions.append((name, int(start, 16), int(length, 16)))
    atelier_core.MAX_SECONDS = 360
    workshop = atelier_core.Workshop(HERE.parents[1])
    variant = atelier_core.register_rtc_variant(args.variant, args.rtc_us)
    log = {"seed": args.seed, "quest": args.quest, "variant": variant, "dumps": [], "states": []}
    session = atelier_campaign.Session(workshop, args.seed, atelier_core.MAX_SECONDS, variant=variant)
    log["run_id"] = session.run_id
    try:
        log["states"].append(session.wait_task(10, 150))
        time.sleep(8)
        for name, start, length in regions:
            dump(session, args.out, f"village-{name}", start, length, log["dumps"])
        answer = None
        for _ in range(20):
            answer = session.ask(f"quest start {args.quest}", "quest", 6.0)
            if not answer or "started quest" in answer or not session.alive():
                break
            time.sleep(2)
        log["start_answer"] = answer
        log["states"].append(session.wait_task(11, 90, quest=args.quest))
        time.sleep(8)
        log["states"].append(session.state())
        session.shot(f"q{args.quest}-dump")
        for name, start, length in regions:
            dump(session, args.out, f"quest-{name}", start, length, log["dumps"])
        if args.walk:
            # About two seconds forward, then the same regions again: what
            # moved with the hunter is its position.
            session.inputs(["0:axis lefty -1", "60:axis lefty 0"])
            time.sleep(4)
            log["states"].append(session.state())
            for name, start, length in regions:
                dump(session, args.out, f"walk-{name}", start, length, log["dumps"])
    finally:
        session.stop()
        (args.out / "probe.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print("DUMP COMPLETE", args.out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
