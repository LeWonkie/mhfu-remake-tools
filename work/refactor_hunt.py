from pathlib import Path

path = Path(__file__).resolve().parents[1] / "outils/atelier-mhfu/hunt.py"
text = path.read_text(encoding="utf-8")
text = text.replace("import zone_tour  # noqa: E402", "import zone_tour  # noqa: E402\nfrom atelier_hunt_state import resolve_objective, KillTracker, ProgressGuard")
start, end = text.index("def hunt("), text.index("\ndef main()")
body = '''def hunt(workshop, seed: str, quest: int, rtc_us: int, seconds: float, routes=None,
         species=None, patrol=None, cancel=None, progress=None, stall_seconds=120,
         walk_in_frames=240) -> dict:
    if not 1 <= seconds <= 1200 or not 15 <= stall_seconds <= 600:
        raise ValueError("Hunt duration must be 1..1200 s; progress deadline 15..600 s")
    goal = resolve_objective(quest, species, patrol)
    if routes is None and goal.get("routes"):
        route_path = workshop.project / goal["routes"]
        if route_path.is_file():
            routes = load_routes(route_path, quest)
    if cancel and cancel.is_set():
        return {"quest": quest, "state": "cancelled", "kills": 0, "run_id": None}
    variant = atelier_core.register_rtc_variant("space_trace", rtc_us)
    session = atelier_campaign.Session(workshop, seed, min(atelier_core.MAX_SECONDS, int(seconds) + 240), variant=variant)
    log = {"schema": 2, "quest": quest, "run_id": session.run_id, "events": [], "kills": 0,
           "state": "running", "phase": "booting", "objective": goal, "route_hints": routes or {},
           "combat_species": goal["species"], "patrol": goal.get("patrol", []),
           "success": None, "visual_review": "not_performed", "stage": None,
           "stall_seconds": stall_seconds, "budget_seconds": seconds}
    out = session.dir / "hunt.json"
    began = time.monotonic()

    def active():
        return not (cancel and cancel.is_set()) and session.alive()

    def pause(duration):
        if cancel:
            cancel.wait(duration)
        else:
            time.sleep(duration)

    def note(**event):
        event["t"] = round(time.monotonic() - began, 2)
        if "phase" in event:
            log["phase"] = event["phase"]
        log["events"].append(event)
        log["elapsed_seconds"] = event["t"]
        log["memory"] = dict(getattr(session, "memory_stats", {}))
        atelier_core._write_json(out, log)
        if progress:
            progress({key: log.get(key) for key in ("quest", "run_id", "state", "phase", "stage",
                      "kills", "objective", "elapsed_seconds", "reason")})
        print(json.dumps(event), flush=True)

    try:
        note(kind="phase", phase="booting")
        if not session.wait_task(10, 150):
            log.update(state="start_failed", reason="village_not_reached")
            return log
        pause(8)
        note(kind="phase", phase="departing")
        started = False
        for _ in range(20):
            if not active():
                break
            answer = session.ask(f"quest start {quest}", "quest", 6.0)
            if answer and "started quest" in answer:
                started = True
                break
            pause(1)
        if not started or not session.wait_task(11, 90, quest=quest):
            log.update(state="start_failed" if active() else "cancelled", reason="quest_not_reached")
            return log
        session.ask("hold health on", "health", 6.0)
        session.ask("hold onehit on", "onehit", 6.0)
        pause(5)
        combat_began = time.monotonic()
        guard = ProgressGuard(combat_began, stall_seconds)
        tracker = KillTracker(goal["species"])
        target_ids = set(goal["species"])
        graph = {stage: list(exits) for stage, exits in (routes or {}).items()}
        failed, visited = set(), set()
        side = patrol_index = read_failures = 0
        patrol = goal.get("patrol", [])
        session.shot(f"h{quest}-debut")
        note(kind="objective", phase="observing", boss_roster=atelier_zones.quest_targets(session), objective=goal)
        while active() and time.monotonic() - combat_began < seconds:
            try:
                countdown = atelier_zones.results_countdown(session)
                if countdown is not None:
                    note(kind="end_screens", phase="results", countdown_s=countdown // 30)
                    pause(2)
                    session.shot(f"h{quest}-fin")
                    log.update(state="quest ended", reason="results_countdown_observed",
                               result_capture=f"frames/h{quest}-fin.bmp")
                    note(kind="final_roster", boss_roster=atelier_zones.quest_targets(session))
                    break
                here = atelier_zones.current(session, timeout=4)
                found = atelier_zones.monsters(session)
                read_failures = 0
            except atelier_zones.Unavailable as error:
                read_failures += 1
                note(kind="read_retry", phase="loading", attempt=read_failures, error=str(error))
                if not active():
                    break
                if read_failures >= 3:
                    log.update(state="read_failed", reason=str(error))
                    break
                pause(0.2)
                continue
            stage = log["stage"] = here["stage"]
            usable = [e for e in here["exits"] if atelier_zones.usable(e)
                      and (stage, e["destination"]) not in failed and e["destination"] != stage]
            graph[stage] = [e["destination"] for e in usable]
            visited.add(stage)
            for monster in tracker.observe(found):
                log["kills"] = tracker.count
                note(kind="kill", phase="combat", slot=monster["slot"], stage=monster["stage"],
                     species=monster["type"], observed_kills=tracker.count)
            if guard.observe(stage, tracker.count, time.monotonic()):
                log.update(state="stalled", reason="no_new_area_or_target_death")
                break
            alive = combat_targets(found, target_ids, strict=True)
            local = [m for m in alive if m["stage"] == stage]
            if local:
                target = local[0]
                dx, dz = SIDES[side % len(SIDES)]
                side += 1
                x, y, z = target["position"]
                note(kind="attack", phase="combat", target_slot=target["slot"], species=target["type"])
                atelier_zones.place(session, here["player"], (x + dx, y, z + dz))
                session.inputs(ATTACK)
                pause(2.8)
                continue
            wanted = {m["stage"] for m in alive}
            step = None
            if patrol:
                step, patrol_index = patrol_step(graph, stage, patrol, patrol_index)
            if step is None:
                for target_stage in sorted(wanted):
                    step = first_hop(graph, stage, target_stage)
                    if step is not None:
                        break
            if step is None:
                step = zone_tour.next_step(graph, visited, stage)
            if step is None:
                log.update(state="no_route", reason="no_reachable_target_or_frontier")
                break
            exit = next(e for e in usable if e["destination"] == step)
            note(kind="navigate", phase="navigating", frm=stage, to=step)
            outcome = atelier_zones.traverse(session, here, exit, walk_in_frames=walk_in_frames,
                      on_attempt=lambda attempt: note(kind="exit_attempt", frm=stage, to=step, **attempt))
            if not outcome["reached"]:
                failed.add((stage, step))
                graph[stage] = [d for d in graph[stage] if d != step]
            note(kind="hop", phase="observing", frm=stage, to=step, **outcome)
        if log["state"] == "running":
            log.update(state="time_limit" if active() else "cancelled",
                       reason="hunt_budget_exhausted" if active() else "trial_stopped")
    except atelier_zones.Unavailable as error:
        log.update(state="read_failed" if active() else "cancelled", reason=str(error))
    except Exception as error:
        log.update(state="failed", reason=f"{type(error).__name__}: {error}")
        raise
    finally:
        if not active() and log["state"] not in ("quest ended", "failed"):
            log.update(state="cancelled", reason="trial_stopped")
        if session.alive() and log["state"] != "quest ended":
            session.shot(f"h{quest}-stop")
        log["phase"] = "finished"
        note(kind="finished", state=log["state"], reason=log.get("reason"))
        workshop.record_quest_result(session.run_id, {"kind": "hunt", "quest": quest,
            "state": log["state"], "reason": log.get("reason"), "objective": goal,
            "observed_kills": log["kills"], "visual_review": "not_performed", "file": "hunt.json"})
        session.stop()
    return log

'''
text = text[:start] + body + text[end:]
text = text.replace('    atelier_core.MAX_SECONDS = 900\n', '')
text = text.replace('    parser.add_argument("--patrol",', '    parser.add_argument("--no-walk-in", action="store_true", help="Disable bounded arrival walking for comparison")\n    parser.add_argument("--stall-seconds", type=float, default=120)\n    parser.add_argument("--patrol",')
text = text.replace('species=args.species, patrol=args.patrol)', 'species=args.species, patrol=args.patrol,\n                      stall_seconds=args.stall_seconds, walk_in_frames=0 if args.no_walk_in else 240)')
path.write_text(text, encoding="utf-8")
