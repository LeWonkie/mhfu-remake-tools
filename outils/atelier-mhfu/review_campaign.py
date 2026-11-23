"""Read-only campaign evidence summary and contact sheets.

Usage: python review_campaign.py --campaign <campagne.json> --out <new-folder>
       python review_campaign.py --run <run-dir> --out <new-folder>

Only a new output folder is written. Source manifests, logs, saves and images
are never changed. Images are evidence for human review, not a rendering verdict.
Near-black means full-image BT.601 mean luminance <= 8/255. Near-uniform means
the largest RGB channel standard deviation <= 3/255. Both thresholds are
configurable; normal fades and loading screens can trigger these heuristics.
Log intervals run from the previous shot (or quest start) through this shot.
They correlate nearby saturation with a capture, not its exact rendered frame.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

PHASES = ("arrivee", "marche", "pendant", "fin", "resultat", "village")
PILOT_PHASES = ("arrivee", "marche", "pendant", "fin", "fin2", "resultat", "village")
STATS = re.compile(r"\[interpreter\] instructions=(\d+) entries=(\d+) unique_addresses=(\d+)")
ENTRY = re.compile(r"\[interpreter\] (0x[0-9a-fA-F]{8}) instructions=(\d+)")
STATE = re.compile(r"\[debug\] hunter .*; task (\d+) \((\w+)\), quest id (\d+), stage (\d+)")
QUEST = re.compile(r"q(\d+)-(arrivee|marche|pendant|fin2?|resultat|village)\.bmp$")
DROP = re.compile(r"a frame ran out of vertex space; the draws past it are not drawn \((\d+) so far\)")
VERTEX_WARNING = re.compile(r"ran out of vertex space", re.IGNORECASE)
FRAME_SPACE = re.compile(r"\[framespace\] frame (\d+): (\d+) draws, ([\d.]+) MiB, (\d+) dropped, (\d+) sites")
SHOT = re.compile(r"\[script\] frame (\d+): shot q(\d+)-(\w+)")
QUEST_START = re.compile(r"\[debug\] > quest start (\d+)")
AUTO_SHOT = re.compile(r"\[render\] frame (\d+) \((\d+) draws\) -> (.*frame_\d+\.bmp)")
DEFAULT_BLACK_MEAN = 8.0
# Catches nearly black scenes with a few triangles left (q11001-fin, mean
# 18.1) without widening near_black; night scenes and fades can trigger it.
DEFAULT_DARK_MEAN = 20.0
DEFAULT_UNIFORM_STDDEV = 3.0


def empty_interval(stream, start_line, boundary):
    return {"stream": stream, "start_line": start_line, "end_line": None,
            "start_boundary": boundary, "warning_messages": 0,
            "warning_counter_first": None, "warning_counter_peak": None,
            "warning_log_lines": [], "framespace_lines": [],
            "framespace_summaries": 0, "framespace_dropped_draws": 0,
            "framespace_peak_mib": None, "saturation_observed": False}


def add_log_evidence(interval, line_number, line):
    if VERTEX_WARNING.search(line):
        interval["warning_messages"] += 1
        interval["warning_log_lines"].append(line_number)
        interval["saturation_observed"] = True
        if match := DROP.search(line):
            counter = int(match[1])
            if interval["warning_counter_first"] is None:
                interval["warning_counter_first"] = counter
            interval["warning_counter_peak"] = max(interval["warning_counter_peak"] or 0, counter)
    if "[framespace]" in line:
        interval["framespace_lines"].append({"line": line_number, "text": line.strip()})
        if match := FRAME_SPACE.search(line):
            try:
                mib = float(match[3])
            except ValueError:
                return
            dropped = int(match[4])
            interval["framespace_summaries"] += 1
            interval["framespace_dropped_draws"] += dropped
            interval["framespace_peak_mib"] = max(interval["framespace_peak_mib"] or 0, mib)
            interval["saturation_observed"] |= dropped > 0


def capture_intervals(text, stream):
    """Use log order within one stream; never merge stdout/stderr by position."""
    captures, unassigned = {}, []
    interval = empty_interval(stream, 1, "start of log")
    current_quest = None
    for line_number, line in enumerate(text.split("\n"), 1):
        if match := QUEST_START.search(line):
            if interval["warning_messages"] or interval["framespace_lines"]:
                interval["end_line"] = line_number - 1
                unassigned.append(interval)
            current_quest = int(match[1])
            interval = empty_interval(stream, line_number, f"quest start {current_quest}")
        add_log_evidence(interval, line_number, line)
        if match := SHOT.search(line):
            quest, phase = int(match[2]), match[3]
            interval["end_line"] = line_number
            interval["capture_frame"] = int(match[1])
            interval["quest_matches_start"] = current_quest == quest
            captures.setdefault(quest, {})[phase] = interval
            current_quest = quest
            interval = empty_interval(stream, line_number + 1, f"shot q{quest}-{phase}")
    if interval["warning_messages"] or interval["framespace_lines"]:
        interval["end_line"] = len(text.split("\n"))
        unassigned.append(interval)
    return captures, unassigned


def auto_capture_intervals(text, stream):
    """Intervals of automatic frame_N captures, delimited by the renderer's own
    "[render] frame N (D draws) -> path" lines, independent of named shots."""
    captures = {}
    interval = empty_interval(stream, 1, "start of log")
    for line_number, line in enumerate(text.split("\n"), 1):
        add_log_evidence(interval, line_number, line)
        if match := AUTO_SHOT.search(line):
            name = Path(match[3].strip()).name
            interval["end_line"] = line_number
            interval["capture_frame"] = int(match[1])
            # Cumulative over the run (renderer.draws_submitted()), not per frame.
            interval["draws_submitted"] = int(match[2])
            captures[name] = interval
            interval = empty_interval(stream, line_number + 1, f"auto {name}")
    return captures


def image_metrics(rgb, black_mean, uniform_stddev, dark_mean=DEFAULT_DARK_MEAN):
    """Measure every pixel; black/color bars and the HUD remain part of the image.
    "dark" covers means above the near-black threshold up to dark_mean, so the
    two flags never overlap and the conservative near-black class is unchanged."""
    luminance = ImageStat.Stat(rgb.convert("L"))
    channels = ImageStat.Stat(rgb)
    mean = luminance.mean[0]
    maximum_stddev = max(channels.stddev)
    return {"mean_luminance": mean, "luminance_stddev": luminance.stddev[0],
            "rgb_stddev": channels.stddev, "max_rgb_stddev": maximum_stddev,
            "near_black": mean <= black_mean,
            "dark": black_mean < mean <= dark_mean,
            "near_uniform": maximum_stddev <= uniform_stddev}


def read_text(path):
    try:
        # Preserve CRCRLF in runtime logs so recorded LF line numbers match rg.
        return path.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return ""


def read_json(path):
    try:
        return json.loads(read_text(path))
    except ValueError:
        return {}


def font(size):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def inspect_run(path):
    manifest = read_json(path / "manifest.json")
    stdout, stderr = read_text(path / "stdout.txt"), read_text(path / "stderr.txt")
    blocks = []
    for line in stderr.splitlines():
        m = STATS.search(line)
        if m:
            blocks.append({"instructions": int(m[1]), "entries": int(m[2]),
                           "unique_addresses": int(m[3]), "top": []})
        elif (m := ENTRY.search(line)):
            if not blocks:
                blocks.append({"instructions": None, "entries": None,
                               "unique_addresses": None, "top": []})
            blocks[-1]["top"].append([m[1].upper().replace("0X", "0x"), int(m[2])])
    states = [{"task": int(m[1]), "task_name": m[2], "quest": int(m[3]), "stage": int(m[4])}
              for m in STATE.finditer(stdout)]
    corpora = re.findall(r"Overlay corpora: (\d+).*", stdout)
    installed = sorted(set(re.findall(r"\[overlay\] installed .*", stdout)))
    missing = sorted(set(re.findall(r"\[overlay\] no recompiled corpus .*", stderr)))
    stop = [line for line in stdout.splitlines() if line.startswith("Runtime stopped:")]
    frames = {}
    for image in (path / "frames").glob("q*-*.bmp"):
        m = QUEST.fullmatch(image.name)
        if m:
            frames.setdefault(int(m[1]), {})[m[2]] = str(image)
    # Attribute messages to the command interval and latest captured phase.
    # The warning's counter is cumulative over the run; never call it a frame
    # count. Phase names label intervals after the shot, not image verdicts.
    saturation, current_quest, phase = {}, None, "before_arrivee"
    for line_number, line in enumerate(stdout.split("\n"), 1):
        if m := re.search(r"\[debug\] > quest start (\d+)", line):
            current_quest, phase = int(m[1]), "before_arrivee"
        if m := re.search(r"\[script\] frame (\d+): shot q(\d+)-(\w+)", line):
            current_quest, phase = int(m[2]), m[3]
        if m := DROP.search(line):
            bucket = saturation.setdefault(str(current_quest), {"warning_messages": 0, "counter_first": int(m[1]),
                         "counter_peak": 0, "first_log_line": line_number, "last_log_line": line_number,
                         "by_phase_interval": {}})
            bucket["warning_messages"] += 1
            bucket["counter_peak"] = max(bucket["counter_peak"], int(m[1]))
            bucket["last_log_line"] = line_number
            bucket["by_phase_interval"][phase] = bucket["by_phase_interval"].get(phase, 0) + 1
    intervals, unassigned = capture_intervals(stdout, "stdout.txt")
    stderr_intervals, stderr_unassigned = capture_intervals(stderr, "stderr.txt")
    auto_intervals = auto_capture_intervals(stdout, "stdout.txt")
    for quest, phases in stderr_intervals.items():
        for phase, interval in phases.items():
            target = intervals.setdefault(quest, {})
            if phase in target:
                target[phase]["stderr_interval"] = interval
            else:
                target[phase] = interval
    return {"run_id": path.name, "run_dir": str(path), "manifest": manifest,
            "quest_frames": frames, "states": states,
            "profile_blocks": blocks, "profile": blocks[-1] if blocks else None,
            "overlay_corpora": [int(n) for n in corpora], "installed": installed,
            "missing_corpora": missing, "runtime_stops": stop, "vertex_saturation": saturation,
            "capture_intervals": intervals, "auto_capture_intervals": auto_intervals,
            "unassigned_log_evidence": unassigned + stderr_unassigned,
            "stderr_other": [line for line in stderr.splitlines()
                             if line and not line.startswith(("[interpreter]", "[savedata]", "[guest]", "[module]"))]}


def audit_capture(image_path, quest, run_id, phase, interval=None,
                  black_mean=DEFAULT_BLACK_MEAN, uniform_stddev=DEFAULT_UNIFORM_STDDEV,
                  dark_mean=DEFAULT_DARK_MEAN):
    audit = {"quest": quest, "run_id": run_id, "phase": phase,
             "path": image_path, "exists": bool(image_path and Path(image_path).is_file()),
             "log_interval": interval}
    audit["vertex_saturation"] = (any(
        item.get("saturation_observed", False)
        for item in (interval, interval.get("stderr_interval", {}))) if interval else None)
    audit["flags"] = ["vertex_saturation"] if audit["vertex_saturation"] else []
    if audit["exists"]:
        try:
            with Image.open(image_path) as source:
                audit["size"] = list(source.size)
                rgb = source.convert("RGB")
                audit["extrema"] = rgb.getextrema()
                audit.update(image_metrics(rgb, black_mean, uniform_stddev, dark_mean))
                audit["flags"].extend(flag for flag in ("near_black", "dark", "near_uniform") if audit[flag])
        except OSError as error:
            audit["error"] = str(error)
    return audit


def sheets(records, output, per_sheet, black_mean=DEFAULT_BLACK_MEAN,
           uniform_stddev=DEFAULT_UNIFORM_STDDEV, dark_mean=DEFAULT_DARK_MEAN):
    width, image_h, label_h, heading_h = 256, 145, 25, 48
    row_h = heading_h + label_h + image_h
    names, image_audit = [], []
    for start in range(0, len(records), per_sheet):
        batch = records[start:start + per_sheet]
        phases = [phase for phase in PILOT_PHASES
                  if any(phase in record.get("expected_phases", PHASES) for record in batch)]
        canvas = Image.new("RGB", (width * len(phases), row_h * len(batch)), "#171a20")
        draw = ImageDraw.Draw(canvas)
        for row, record in enumerate(batch):
            y = row * row_h
            label = f"Quete {record['quest']} | {record['run_id']} | {record.get('verdict', 'capture partielle')}"
            draw.text((8, y + 5), label, font=font(18), fill="#ffffff")
            for column, phase in enumerate(phases):
                x = column * width
                draw.text((x + 6, y + heading_h), phase, font=font(15), fill="#c7d0df")
                if phase not in record.get("expected_phases", PHASES):
                    draw.text((x + 6, y + heading_h + label_h + 40), "NON DEMANDEE", font=font(15), fill="#c7d0df")
                    continue
                image_path = record["frames"].get(phase)
                interval = record.get("capture_intervals", {}).get(phase)
                audit = audit_capture(image_path, record["quest"], record["run_id"], phase, interval,
                                      black_mean, uniform_stddev, dark_mean)
                if audit["exists"]:
                    try:
                        with Image.open(image_path) as source:
                            rgb = source.convert("RGB")
                            thumb = ImageOps.contain(rgb, (width - 4, image_h - 4))
                            canvas.paste(thumb, (x + (width - thumb.width) // 2,
                                               y + heading_h + label_h + (image_h - thumb.height) // 2))
                    except OSError as error:
                        audit["error"] = str(error)
                else:
                    draw.text((x + 6, y + heading_h + label_h + 40), "CAPTURE ABSENTE", font=font(15), fill="#ffb09f")
                image_audit.append(audit)
        name = f"planche-{start // per_sheet + 1:02d}.png"
        canvas.save(output / name)
        names.append(name)
    return names, image_audit


def indicators_html(audit):
    rows = []
    for item in audit:
        interval = item.get("log_interval")
        intervals = [value for value in (interval, (interval or {}).get("stderr_interval")) if value]
        evidence = "; ".join(
            f"{value['stream']}:{value['start_line']}-{value['end_line']}, "
            f"warnings={value['warning_messages']}, traces={value['framespace_summaries']}, "
            f"dropped={value['framespace_dropped_draws']}, peak MiB={value['framespace_peak_mib']}"
            for value in intervals)
        metrics = (f"{item['mean_luminance']:.2f} / {item['max_rgb_stddev']:.2f}"
                   if "mean_luminance" in item else "unavailable")
        status = ", ".join(item["flags"]) or ("no heuristic flag" if item.get("exists") else "missing")
        if "error" in item:
            status = "image decode error"
        original = (f'<a href="{html.escape(Path(item["path"]).resolve().as_uri(), quote=True)}">'
                    f'{html.escape(item["phase"])}</a>' if item.get("path") else html.escape(item["phase"]))
        rows.append(f'<tr class="{"suspect" if item["flags"] else ""}">'
                    f'<td>{html.escape(str(item["quest"]))}</td><td>{html.escape(item["run_id"])}</td>'
                    f'<td>{original}</td><td>{html.escape(metrics)}</td><td>{html.escape(status)}</td>'
                    f'<td>{html.escape(evidence or "capture interval unavailable")}</td></tr>')
    return ('<h2>Capture indicators</h2><table><tr><th>Quest</th><th>Run</th><th>Capture</th>'
            '<th>Mean luminance / max RGB stddev (0-255)</th><th>Flags</th><th>Log interval</th></tr>'
            + "".join(rows) + '</table>')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("C:/Users/Hugo/Documents/MHFU REMAKE/atelier-psprecomp"))
    parser.add_argument("--campaign", type=Path, action="append", default=[])
    parser.add_argument("--run", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-sheet", type=int, default=3)
    parser.add_argument("--black-mean", type=float, default=DEFAULT_BLACK_MEAN,
                        help="near-black mean luminance threshold, inclusive, 0..255 (default: 8)")
    parser.add_argument("--uniform-stddev", type=float, default=DEFAULT_UNIFORM_STDDEV,
                        help="near-uniform maximum RGB standard deviation, inclusive, 0..255 (default: 3)")
    parser.add_argument("--dark-mean", type=float, default=DEFAULT_DARK_MEAN,
                        help="dark (above near-black) mean luminance threshold, inclusive, 0..255 (default: 20)")
    args = parser.parse_args(argv)
    if not args.campaign and not args.run:
        parser.error("specify --campaign or --run")
    if not 1 <= args.per_sheet <= 12:
        parser.error("--per-sheet must be between 1 and 12")
    for name, value in (("--black-mean", args.black_mean), ("--uniform-stddev", args.uniform_stddev),
                        ("--dark-mean", args.dark_mean)):
        if not math.isfinite(value) or not 0 <= value <= 255:
            parser.error(f"{name} must be finite and between 0 and 255")
    sources, run_paths, results = [], {p.resolve() for p in args.run}, []
    expected_phases = {}
    for source in args.campaign:
        if source.is_dir():
            source /= "campagne.json"
        campaign = read_json(source)
        if not campaign:
            raise SystemExit(f"cannot read campaign: {source}")
        sources.append({"path": str(source), "data": campaign})
        for run_id in campaign.get("runs", []):
            run_paths.add((args.data_dir / "runs" / run_id).resolve())
        results.extend(campaign.get("results", []))
        if campaign.get("driver") == "atelier_pilot":
            mode = campaign.get("settings", {}).get("captures")
            if mode in ("fast", "detailed"):
                phases = [phase for phase in PILOT_PHASES
                          if mode == "detailed" or phase not in ("marche", "resultat")]
                for result in campaign.get("results", []):
                    expected_phases[(result["quest"], result["run_id"])] = phases
    protected = run_paths | {Path(source["path"]).resolve().parent for source in sources}
    if any(args.out.resolve().is_relative_to(path) for path in protected):
        parser.error("--out must be outside source run and campaign folders")
    if args.out.exists():
        parser.error("--out must be a new folder; existing evidence is never overwritten")
    args.out.mkdir(parents=True, exist_ok=False)
    runs = [inspect_run(p) for p in sorted(run_paths)]
    lookup = {r["run_id"]: r for r in runs}
    records, seen = [], set()
    for result in results:
        key = (result["quest"], result["run_id"])
        if key in seen:
            continue
        seen.add(key)
        run = lookup.get(result["run_id"], {})
        frames = run.get("quest_frames", {}).get(result["quest"], {})
        records.append({"quest": result["quest"], "run_id": result["run_id"], "frames": frames,
                        "expected_phases": expected_phases.get(key, PHASES),
                        "capture_intervals": run.get("capture_intervals", {}).get(result["quest"], {}),
                        "result": result,
                        "verdict": "retour signale" if result.get("returned") else "retour non confirme"})
    for run in runs:
        for quest, frames in sorted(run["quest_frames"].items()):
            if (quest, run["run_id"]) not in seen:
                records.append({"quest": quest, "run_id": run["run_id"], "frames": frames,
                                "capture_intervals": run["capture_intervals"].get(quest, {})})
    names, audit = sheets(records, args.out, args.per_sheet, args.black_mean, args.uniform_stddev,
                          args.dark_mean)
    audited_paths = {Path(item["path"]).resolve() for item in audit if item.get("path")}
    for run in runs:
        for image_path in sorted((Path(run["run_dir"]) / "frames").iterdir() if
                                 (Path(run["run_dir"]) / "frames").is_dir() else []):
            if image_path.suffix.lower() in (".bmp", ".png", ".jpg", ".jpeg") and image_path.resolve() not in audited_paths:
                audit.append(audit_capture(str(image_path), None, run["run_id"], image_path.name,
                                           run["auto_capture_intervals"].get(image_path.name),
                                           black_mean=args.black_mean, uniform_stddev=args.uniform_stddev,
                                           dark_mean=args.dark_mean))
    top = Counter()
    for run in runs:
        if run["profile"]:
            top.update(dict(run["profile"]["top"]))
    summary = {"sources": sources, "runs": runs, "records": records, "image_audit": audit,
               "image_detection": {"black_mean_max": args.black_mean, "dark_mean_max": args.dark_mean,
                                   "uniform_rgb_stddev_max": args.uniform_stddev,
                                   "scale": "0..255", "luminance": "Pillow L (BT.601)",
                                   "scope": "all pixels, including HUD and borders",
                                   "flag_counts": dict(Counter(flag for item in audit for flag in item["flags"]))},
               "interpreted_top_from_last_block_per_run": top.most_common(40), "sheets": names,
               "limits": ["A reported return is not a quest victory or a rendering verdict.",
                          "Statistics are cumulative over boot and quests, and limited to the emitted top entries.",
                          "No profile block means unavailable, never zero interpreted instructions.",
                          "Overlay slots reuse addresses; one address is not necessarily one function identity.",
                          "Image presence and decode success do not validate appearance.",
                          "Black/uniform heuristics can flag normal fades and loading screens; human review is required.",
                          "Capture intervals precede each named shot in log order, from the previous shot or quest start.",
                          "Log intervals correlate evidence; they do not identify the exact rendered frame.",
                          "Automatic frame_N captures use the renderer's own '[render] frame N' lines as interval ends; "
                          "a capture without that line has an unavailable interval.",
                          "'dark' (mean above near-black, up to the dark threshold) also flags night scenes and fades.",
                          "Missing saturation logs are not proof of a correct image; counters are cumulative, not image counts.",
                          "Framespace MiB alone is not saturation; a positive dropped count is evidence.",
                          "Unassigned stdout/stderr evidence is retained separately, never attributed across streams."]}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Contrôle des preuves de campagne", "", f"Essais : {len(runs)} ; quêtes avec captures : {len(records)}.", "",
             "Les retours signalés par le runner ne prouvent ni une victoire ni la correction du rendu.", ""]
    for run in runs:
        manifest, profile = run["manifest"], run["profile"]
        lines.extend([f"## {run['run_id']}", "",
                      f"État enregistré : `{manifest.get('state')}` ; sortie : `{manifest.get('exit_code')}` ; arrêt : `{manifest.get('runtime_stop')}`.",
                      f"Profils interpréteur : {len(run['profile_blocks'])} ; instructions : {profile['instructions'] if profile else 'indisponible'}.",
                      f"Corpora au lancement : {run['overlay_corpora']} ; installations distinctes : {len(run['installed'])}.", ""])
        for quest, data in run["vertex_saturation"].items():
            lines.append(f"- Saturation sommets, quête {quest} : {data['warning_messages']} messages ; compteur cumulé {data['counter_first']} à {data['counter_peak']}. Ce compteur ne dénombre pas les images.")
        if run["vertex_saturation"]:
            lines.append("")
    lines.extend(["## Automatic capture indicators", "",
                  f"Near-black: mean luminance <= {args.black_mean:g}/255. "
                  f"Dark: above that, up to {args.dark_mean:g}/255. "
                  f"Near-uniform: maximum RGB channel standard deviation <= {args.uniform_stddev:g}/255.",
                  "Heuristics require human review; normal fades and loading screens can trigger them.",
                  "Saturation is correlated with the preceding log interval, not the exact captured frame.", "",
                  "| Quest | Run | Capture | Flags |", "| --- | --- | --- | --- |"])
    for item in audit:
        if item["flags"]:
            lines.append(f"| {item['quest']} | {item['run_id']} | {item['phase']} | {', '.join(item['flags'])} |")
    lines.append("")
    lines.extend(["## Vérifications visuelles", "", "- Arrivée et marche : décor, chasseur, textures, éclairage, caméra et absence de corruption.",
                  "- HUD : vie, endurance, carte, inventaire, police et icônes lisibles.",
                  "- Fin et résultat : bannière et menus cohérents ; vérifier si une quête échoue volontairement au chronomètre.",
                  "- Retour : village effectivement affiché et personnage contrôlable.",
                  "- En cas d'anomalie : ouvrir la capture BMP originale liée dans summary.json.", ""])
    (args.out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    page = '<!doctype html><meta charset="utf-8"><title>Preuves campagne MHFU</title><style>body{background:#111;color:#ddd;font:16px sans-serif}img{display:block;width:100%;max-width:1536px;margin:16px 0}a{color:#aaf}</style><h1>Preuves campagne MHFU</h1><p>Contrôle visuel requis. Un retour au village ne prouve ni une victoire ni la correction du rendu.</p><p><a href="summary.md">Résumé</a> · <a href="summary.json">Détails et captures originales</a></p>'
    page += ('<style>table{border-collapse:collapse;font-size:13px}td,th{padding:5px;border:1px solid #444}'
             '.suspect{background:#492b18}</style>'
             f'<p>Near-black: mean luminance &le; {args.black_mean:g}/255. Dark: above that, up to '
             f'{args.dark_mean:g}/255. Near-uniform: maximum RGB '
             f'channel standard deviation &le; {args.uniform_stddev:g}/255. Measured over all pixels, '
             'including HUD and borders. Fades and loading screens can trigger these heuristics.</p>'
             '<p>Saturation evidence is from the preceding log interval (previous shot or quest start '
             'through this shot), not the exact captured frame. Warning counters are cumulative, not '
             'image counts. Positive framespace dropped counts indicate saturation; high MiB alone does '
             'not. Missing logs are not proof of correct rendering. Automatic captures use the '
             '[render] frame lines of the renderer as interval ends.</p>')
    page += indicators_html(audit)
    for run in runs:
        evidence = run["unassigned_log_evidence"]
        if evidence:
            page += (f'<p>{html.escape(run["run_id"])}: unassigned log evidence: '
                     f'{sum(value["warning_messages"] for value in evidence)} vertex warnings, '
                     f'{sum(len(value["framespace_lines"]) for value in evidence)} framespace lines '
                     '(see JSON for stream and line numbers).</p>')
    for name in names:
        page += f'<a href="{html.escape(name)}"><img src="{html.escape(name)}"></a>'
    (args.out / "index.html").write_text(page, encoding="utf-8")
    print(json.dumps({"output": str(args.out.resolve()), "runs": len(runs), "quests": len(records),
                      "sheets": names, "missing_captures": sum(not x["exists"] for x in audit),
                      "suspect_captures": sum(bool(x["flags"]) for x in audit),
                      "flag_counts": summary["image_detection"]["flag_counts"],
                      "invalid_images": sum("error" in x for x in audit)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
