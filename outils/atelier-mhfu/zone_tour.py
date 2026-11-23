"""Visit every area reachable from a quest's start through the game's exits.

One fresh trial per quest (its own save copy), the hunter's health held so a
monster cannot end the tour. In each area: read its exits, capture it once,
then go to an unvisited neighbour, or along the shortest known path towards
an area that still has one. Writes tour.json in a new --out folder after
every step; the captures stay in each run's frames folder.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import atelier_campaign  # noqa: E402
import atelier_core  # noqa: E402
import atelier_zones  # noqa: E402

SETTLE_SECONDS = 8


def next_step(graph: dict[int, list[int]], visited: set[int], here: int) -> int | None:
    """The first exit to take: an unvisited neighbour, else the first hop of
    the shortest known path to an area with an unvisited neighbour."""
    for destination in graph.get(here, []):
        if destination not in visited:
            return destination
    seen, queue = {here}, deque((d, d) for d in graph.get(here, []))
    while queue:
        area, first = queue.popleft()
        if area in seen:
            continue
        seen.add(area)
        if any(d not in visited for d in graph.get(area, [])):
            return first
        queue.extend((d, first) for d in graph.get(area, []) if d not in seen)
    return None


def tour(workshop, seed: str, quest: int, rtc_us: int, max_hops: int, save, result: dict) -> dict:
    variant = atelier_core.register_rtc_variant("space_trace", rtc_us)
    session = atelier_campaign.Session(workshop, seed, atelier_core.MAX_SECONDS, variant=variant)
    result.update({"quest": quest, "run_id": session.run_id, "areas": {}, "hops": [], "state": "running"})
    try:
        session.wait_task(10, 150)
        time.sleep(8)
        for _ in range(20):
            answer = session.ask(f"quest start {quest}", "quest", 6.0)
            if not answer or "started quest" in answer:
                break
            time.sleep(2)
        if not session.wait_task(11, 90, quest=quest):
            result["state"] = "quest not reached"
            return result
        session.ask("hold health on", "health", 6.0)
        time.sleep(SETTLE_SECONDS)
        graph: dict[int, list[int]] = {}
        visited: set[int] = set()
        failed: set[tuple[int, int]] = set()
        result["failed_exits"] = []
        for _ in range(max_hops + 1):
            here = atelier_zones.current(session)
            stage = here["stage"]
            usable = [e for e in here["exits"] if atelier_zones.usable(e)]
            # Exits that did not work from here are left out of the routes.
            graph[stage] = [e["destination"] for e in usable if (stage, e["destination"]) not in failed]
            if stage not in visited:
                visited.add(stage)
                shot = session.shot(f"q{quest}-zone{stage:03d}")
                result["areas"][str(stage)] = {
                    "exits": [e["destination"] for e in here["exits"]], "capture": f"{shot}.bmp",
                    "position": here["position"], "exit_source": here["exit_source"],
                    "vtable_slots": here["vtable_slots"],
                    "exit_geometry": [{k: e[k] for k in ("destination", "shape", "p", "q", "size", "height")}
                                      for e in here["exits"]]}
            save()
            step = next_step(graph, visited, stage)
            if step is None:
                result["state"] = "complete"
                break
            target = next(e for e in usable if e["destination"] == step)
            outcome = atelier_zones.traverse(session, here, target)
            result["hops"].append({"from": stage, "to": step, **outcome})
            if not outcome["reached"]:
                failed.add((stage, step))
                result["failed_exits"].append([stage, step])
                # Wherever the attempts left the hunter, carry on from there.
                continue
            time.sleep(SETTLE_SECONDS)
        else:
            result["state"] = "hop limit"
        known = {d for ds in graph.values() for d in ds} | {d for _, d in failed}
        result["unvisited_known"] = sorted(known - visited)
    except atelier_zones.Unavailable as error:
        result["state"] = f"read failed: {error}"
    finally:
        result["alive_at_end"] = session.alive()
        session.stop()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quests", nargs="+", type=int)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rtc-us", type=int, default=1609459200123456)
    parser.add_argument("--max-hops", type=int, default=24)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    atelier_core.MAX_SECONDS = 900
    workshop = atelier_core.Workshop(HERE.parents[1])
    data = {"created_at": datetime.now().isoformat(timespec="seconds"), "seed": args.seed,
            "rtc_us": args.rtc_us, "tours": []}

    def save():
        atelier_core._write_json(args.out / "tour.json", data)

    for quest in args.quests:
        print(f"quest {quest}", flush=True)
        outcome = {"quest": quest, "state": "starting", "areas": {}}
        data["tours"].append(outcome)
        save()
        tour(workshop, args.seed, quest, args.rtc_us, args.max_hops, save, outcome)
        save()
        print(json.dumps({k: outcome.get(k) for k in ("quest", "run_id", "state", "unvisited_known")}) +
              f" areas={sorted(int(a) for a in outcome['areas'])}", flush=True)
    print("TOUR DONE", args.out / "tour.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
