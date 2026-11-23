"""Bounded quest campaigns on isolated save copies, with durable checkpoints."""
from __future__ import annotations

import html
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import atelier_core
import atelier_zones

BOOT = ("1:pad guide 900;90:pad b 12;180:pad b 12;300:pad start 12;420:pad b 12;540:pad b 12;"
        "660:pad b 12;780:pad b 12")
# Walking and turning on the quest, for transitions of the camera and the HUD.
WALK = ["0:axis lefty -1", "90:axis lefty 0", "95:axis rightx 1", "125:axis rightx 0",
        "130:axis lefty -1", "220:axis lefty 0"]
# The end of a quest: the results, then "End" in the reward menu (down, down, confirm).
# A first sweep: the first quests of each village and Guild Hall level, the
# training school and the challenge quests, for as many areas as possible.
DEFAULT_QUESTS = [1001, 1101, 1201, 2001, 2101, 2201, 3001, 3101, 3201, 4001, 10001, 10101, 10201, 10301, 10401,
                  10501, 11001, 11101, 11201, 20001, 21001, 30001]
TASK = re.compile(r"\[debug\] hunter .*; task (\d+) \((\w+)\), quest id (\d+), stage (\d+)")
# Worn before each quest (the game's `loadout best`, docs/MHFU_TOOLS.md) and
# held while the quest is looked at; switched off before the clock ends it.
LOADOUTS = ("best", "none")
CHEATS = ("health", "stamina", "sharpness", "onehit", "timer", "items")
DEFAULT_CHEATS = ("health", "stamina", "sharpness")


@dataclass
class QuestResult:
    quest: int
    started: bool = False
    reached: bool = False
    stage: int | None = None
    load_seconds: float | None = None
    status: str = ""
    returned: bool = False
    return_seconds: float | None = None
    crashed: bool = False
    run_id: str = ""
    shots: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stage_observed_at: str | None = None
    window_size: list[int] | None = None
    loadout_answer: str | None = None
    cheats_held: list[str] = field(default_factory=list)
    # Raw mission descriptors (Quest +0x728: flags, target, quantity) and the
    # boss roster (+0x768), read once the area is loaded; not interpreted here.
    objectives: dict | None = None
    roster: list[dict] | None = None


class Session:
    """One running game with developer commands, driven through its files."""

    def __init__(self, workshop: atelier_core.Workshop, seed_id: str | None, seconds: int,
                 dump_overlays: bool = True, variant: str = "normal", scenario: dict | None = None):
        self.w = workshop
        scenario = scenario or {"name": "campagne", "label": "Campagne automatique", "fast_boot": True,
                                "debug": True, "dump_overlays": dump_overlays, "script": BOOT}
        self.run_id = workshop.start(scenario, variant=variant, seconds=seconds, seed_id=seed_id)
        self.dir = workshop.data_dir / "runs" / self.run_id
        self.read_at = 0
        self.began = time.monotonic()
        # Called with every batch of lines read, whoever reads them (atelier_pilot).
        self.on_lines = None

    def arm_loadout(self, loadout: str, timeout: float = 30.0) -> bool:
        """Sends `loadout NAME` as early as the game reads commands: before the
        save is loaded, the game keeps it and dresses the character as it
        loads, so the hunter is drawn in it (sent later, only the statistics
        follow). The game skips what the command file held before it first
        read it, so wait for an answer to `state` first."""
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline and self.alive():
                if self.ask("state", "[debug] hunter ", 2.0):
                    # "loadout best armed: …" before the save, "wearing …" after it.
                    answer = self.ask(f"loadout {loadout}", "[debug] ", 4.0) or ""
                    return "armed" in answer or "wearing" in answer
        except (RuntimeError, ValueError, OSError):
            pass
        return False

    def alive(self) -> bool:
        cancel = getattr(self, "cancel", None)
        if cancel and cancel.is_set():
            return False
        status = self.w.status()["active"]
        return bool(status) and status["id"] == self.run_id and status["state"] == "running"

    def new_lines(self) -> list[str]:
        # Only the new part of the log: it grows to megabytes over a session.
        try:
            with (self.dir / "stdout.txt").open("rb") as stream:
                stream.seek(self.read_at)
                data = stream.read()
        except OSError:
            return []
        cut = data.rfind(b"\n") + 1
        self.read_at += cut
        lines = data[:cut].decode("utf-8", errors="replace").splitlines()
        if lines and self.on_lines is not None:
            self.on_lines(lines)
        return lines

    def ask(self, line: str, expect: str, timeout: float = 10.0) -> str | None:
        """Sends a command and returns the first answer line containing `expect`."""
        self.new_lines()
        self.w.debug_command(line)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.alive():
            for answer in self.new_lines():
                if expect in answer and not answer.startswith("[debug] > "):
                    return answer
            time.sleep(0.3)
        return None

    def state(self) -> tuple[int, int, int] | None:
        answer = self.ask("state", "[debug] hunter ", 6.0)
        match = TASK.search(answer or "")
        return (int(match[1]), int(match[3]), int(match[4])) if match else None

    def wait_task(self, task: int, timeout: float, quest: int | None = None) -> tuple[int, int, int] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.alive():
            now = self.state()
            if now and now[0] == task and (quest is None or (now[1] == quest and now[2] > 0)):
                return now
            time.sleep(1.5)
        return None

    def inputs(self, lines: list[str]) -> None:
        with (self.dir / "input.txt").open("a", encoding="utf-8") as stream:
            stream.write("".join(line + "\n" for line in lines))

    def shot(self, name: str) -> str:
        self.inputs([f"0:shot {name}"])
        time.sleep(0.6)
        return name

    def stop(self) -> None:
        self.w.stop()


def _interpreted(run_dir: Path) -> list[str]:
    try:
        text = (run_dir / "stderr.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return re.findall(r"\[interpreter\] (0x[0-9A-F]{8}) instructions=(\d+)", text)


def run_quest(session: Session, quest: int, look_seconds: float,
              result: QuestResult | None = None, loadout: str = "best",
              cheats: tuple[str, ...] = DEFAULT_CHEATS) -> QuestResult:
    result = result if result is not None else QuestResult(quest, run_id=session.run_id)
    if loadout != "none":
        worn = session.ask(f"loadout {loadout}", "wearing", 6.0)
        result.loadout_answer = worn.replace("[debug] ", "") if worn else None
        if not worn or "wearing" not in worn:
            result.notes.append(f"équipement non porté : {worn}")
    answer = None
    # The game refuses while the village loads or a dialog is open: try again for a while.
    for _ in range(20):
        answer = session.ask(f"quest start {quest}", "quest", 6.0)
        if not answer or "started quest" in answer or "has no quest" in answer or not session.alive():
            break
        time.sleep(2)
    result.started = bool(answer and "started quest" in answer)
    if not result.started:
        result.notes.append(f"départ refusé : {answer}")
        return result
    began = time.monotonic()
    arrived = session.wait_task(11, 90, quest=quest)
    if not arrived:
        result.notes.append("la quête n'a pas été atteinte en 90 s")
        result.crashed = not session.alive()
        return result
    # The quest task runs before its area is drawn: let the area load.
    time.sleep(6)
    # Read a new state after loading; an early task transition can still expose
    # the previous area. Never attribute another quest's area to this result.
    loaded = session.wait_task(11, 15, quest=quest)
    if not loaded:
        result.notes.append("la quête demandée et sa zone n'ont pas été confirmées après chargement")
        result.crashed = not session.alive()
        return result
    result.reached, result.stage = True, loaded[2]
    result.stage_observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    result.load_seconds = round(time.monotonic() - began, 1)
    for cheat in cheats:
        if session.ask(f"hold {cheat} on", "hold"):
            result.cheats_held.append(cheat)
    try:
        result.objectives = atelier_zones.mission_objectives(session, quest)
        result.roster = atelier_zones.quest_targets(session)
    except Exception as error:  # a survey aid: never stops the campaign
        result.notes.append(f"objectifs non lus : {error}")
    result.shots.append(session.shot(f"q{quest}-arrivee"))
    result.window_size = atelier_core.capture_size(session.dir / "frames" / (result.shots[0] + ".bmp"))
    session.inputs(WALK)
    time.sleep(look_seconds / 2)
    result.shots.append(session.shot(f"q{quest}-marche"))
    time.sleep(look_seconds / 2)
    result.status = (session.ask("quest", "[debug] time left", 5.0) or "").replace("[debug] ", "")
    result.shots.append(session.shot(f"q{quest}-pendant"))
    for cheat in cheats:
        session.ask(f"hold {cheat} off", "hold")
    timed = session.ask("quest time 2", "quest time")
    if not timed or "set to" not in timed:
        result.notes.append(f"chronomètre non réglé : {timed}")
    time.sleep(12)
    result.shots.append(session.shot(f"q{quest}-fin"))
    began = time.monotonic()
    # Banner, then for a cleared quest the reward menu and the payment screen, each
    # with a countdown (120 s, 110 s): cut it to one second and press ○ for the
    # payment screen, until the village loads. A failed quest goes back by itself.
    time.sleep(8)
    result.shots.append(session.shot(f"q{quest}-resultat"))
    back = None
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and session.alive():
        session.ask("quest results", "results countdown", 3.0)
        session.inputs(["0:pad b 6"])
        back = session.wait_task(10, 4)
        if back:
            break
    if back:
        result.returned = True
        result.return_seconds = round(time.monotonic() - began, 1)
        time.sleep(8)
        result.shots.append(session.shot(f"q{quest}-village"))
    if not result.returned:
        result.notes.append("pas de retour au village")
    result.crashed = not session.alive()
    return result


def run_campaign(workshop: atelier_core.Workshop, quests: list[int], seed_id: str | None = None,
                 look_seconds: float = 12.0, log=print, variant: str = "normal",
                 rtc_us: int | None = None, cancel=None, loadout: str = "best",
                 cheats: tuple[str, ...] = DEFAULT_CHEATS) -> Path:
    if loadout not in LOADOUTS or any(cheat not in CHEATS for cheat in cheats):
        raise ValueError(f"équipement parmi {LOADOUTS}, triches parmi {CHEATS}")
    variant = atelier_core.register_rtc_variant(variant, rtc_us)
    seed = workshop._current_seed() if seed_id is None else workshop._seed(seed_id)
    if seed is None:
        raise RuntimeError("préparez d'abord un point de départ pour la campagne")
    # Pin the resolved seed once, even if the atelier's current seed changes
    # before a later session is restarted.
    seed_id = seed["id"]
    out = workshop.data_dir / "campaigns" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    if (out / "campagne.json").exists():
        # Separate same-second campaigns instead of replacing existing results.
        import uuid
        out = out.with_name(out.name + "-" + uuid.uuid4().hex[:6])
        out.mkdir()
    results: list[QuestResult] = []
    metadata = {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "state": "running", "variant": variant, "rtc_us": rtc_us,
                "seed_id": seed_id, "seed_sha256": seed["seed_sha256"],
                "window_size": None, "runs": [], "loadout": loadout, "cheats": list(cheats)}
    session: Session | None = None
    _write_report(workshop, out, results, variant, metadata)
    try:
        for quest in quests:
            if cancel and cancel.is_set():
                metadata["state"] = "cancelled"
                break
            # A run lasts at most MAX_SECONDS: start a new one before a quest could be cut off.
            if session is not None and time.monotonic() - session.began > atelier_core.MAX_SECONDS - 360:
                session.stop()
                session = None
            if session is None or not session.alive():
                if session is not None:
                    session.stop()
                session = Session(workshop, seed_id, atelier_core.MAX_SECONDS, variant=variant)
                session.cancel = cancel
                if loadout != "none":
                    session.arm_loadout(loadout)
                metadata["runs"].append(session.run_id)
                _write_report(workshop, out, results, variant, metadata)
                log(f"jeu lancé (essai {session.run_id})")
                if not session.wait_task(10, 120):
                    log("le village n'a pas été atteint ; campagne arrêtée")
                    result = QuestResult(quest, run_id=session.run_id, notes=["village non atteint au démarrage"])
                    results.append(result)
                    workshop.record_quest_result(session.run_id, result.__dict__)
                    metadata["state"] = "cancelled" if cancel and cancel.is_set() else "failed"
                    _write_report(workshop, out, results, variant, metadata)
                    break
                # The overlay can be executable before lobby initialization finishes.
                # Match the settling time already used after each village return.
                time.sleep(8)
            log(f"quête {quest}…")
            result = QuestResult(quest, run_id=session.run_id)
            results.append(result)
            try:
                run_quest(session, quest, look_seconds, result=result, loadout=loadout, cheats=cheats)
            finally:
                # Keep partial observations if a quest is interrupted or raises.
                workshop.record_quest_result(session.run_id, result.__dict__)
                _write_report(workshop, out, results, variant, metadata)
            log(f"quête {quest} : {'OK' if result.returned else 'problème'} {' ; '.join(result.notes)}")
            if cancel and cancel.is_set():
                metadata["state"] = "cancelled"
                break
            if result.crashed or not result.returned:
                session.stop()
                session = None
        if metadata["state"] == "running":
            metadata["state"] = "completed"
    except KeyboardInterrupt:
        metadata["state"] = "interrupted"
        raise
    except BaseException:
        metadata["state"] = "failed"
        raise
    finally:
        try:
            if session is not None:
                session.stop()
        finally:
            _write_report(workshop, out, results, variant, metadata)
    return out


def _write_report(workshop: atelier_core.Workshop, out: Path, results: list[QuestResult],
                  variant: str = "normal", metadata: dict | None = None) -> None:
    runs = sorted({r.run_id for r in results if r.run_id} | set((metadata or {}).get("runs", [])))
    interpreted: dict[str, int] = {}
    stops = {}
    window_size = None
    for run_id in runs:
        run_dir = workshop.data_dir / "runs" / run_id
        for address, count in _interpreted(run_dir):
            interpreted[address] = interpreted.get(address, 0) + int(count)
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            stops[run_id] = manifest.get("runtime_stop")
            window_size = window_size or manifest.get("window_size")
        except (OSError, json.JSONDecodeError):
            stops[run_id] = None
    data = {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **(metadata or {}), "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "variant": variant, "runs": runs,
            "window_size": next((r.window_size for r in results if r.window_size), window_size),
            "runtime_stop": stops,
            "results": [r.__dict__ for r in results],
            "interpreted_top": sorted(interpreted.items(), key=lambda kv: -kv[1])[:40]}
    # Replace only after a complete write; a terminated process leaves the last
    # valid checkpoint rather than a truncated campaign report.
    pending = out / "campagne.json.tmp"
    pending.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    pending.replace(out / "campagne.json")
    rows = []
    for r in results:
        frames = workshop.data_dir / "runs" / r.run_id / "frames"
        images = "".join(
            f'<img src="{html.escape((frames / (s + ".bmp")).as_uri())}" title="{html.escape(s)}">' for s in r.shots)
        verdict = "OK" if r.returned and not r.crashed else ("PLANTAGE" if r.crashed else "À VOIR")
        rows.append(f"<tr><td>{r.quest}</td><td>{verdict}</td><td>{r.stage}</td><td>{r.load_seconds}</td>"
                    f"<td>{r.return_seconds}</td><td>{html.escape(r.status)}</td>"
                    f"<td>{html.escape('; '.join(r.notes))}</td></tr><tr><td colspan=7>{images}</td></tr>")
    page = ("<!doctype html><meta charset=utf-8><title>Campagne MHFU</title><style>body{font:14px sans-serif;"
            "background:#111;color:#ddd}td{padding:4px;border-bottom:1px solid #333}img{width:240px;margin:2px}"
            "</style><h1>Campagne MHFU</h1><p>Les images ne sont pas jugées automatiquement : regarder les planches."
            f"</p><p>Équipement : {html.escape(str(data.get('loadout', 'none')))} ; triches tenues : "
            f"{html.escape(', '.join(data.get('cheats', [])) or 'aucune')}.</p><table><tr><th>Quête</th><th>Résultat</th><th>Zone</th><th>Chargement (s)</th><th>Retour (s)</th>"
            "<th>État</th><th>Notes</th></tr>" + "".join(rows) + "</table>")
    (out / "campagne.html").write_text(page, encoding="utf-8")


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Campagne automatique de quêtes MHFU")
    parser.add_argument("quests", nargs="+", type=int)
    parser.add_argument("--seed", default=None, help="point de départ (dossier de seeds)")
    parser.add_argument("--look", type=float, default=12.0, help="secondes passées dans chaque quête")
    parser.add_argument("--rtc-us", type=int, default=None,
                        help="horloge RTC figée en microsecondes (2000 à 2100) ; active DEVICE_TEST")
    parser.add_argument("--variant", default="normal", choices=sorted(atelier_core.VARIANTS),
                        help="variante de rendu (atelier_core.VARIANTS)")
    parser.add_argument("--loadout", choices=LOADOUTS, default="best",
                        help="équipement porté avant chaque quête (none : celui de la sauvegarde)")
    parser.add_argument("--cheats", nargs="*", choices=CHEATS, default=list(DEFAULT_CHEATS),
                        help="triches tenues pendant l'observation (défaut : %(default)s)")
    args = parser.parse_args(argv)
    workshop = atelier_core.Workshop(Path(__file__).resolve().parents[2])
    out = run_campaign(workshop, args.quests, args.seed, args.look, log=lambda m: print(m, flush=True),
                       variant=args.variant, rtc_us=args.rtc_us, loadout=args.loadout, cheats=tuple(args.cheats))
    print("rapport :", out / "campagne.html", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
