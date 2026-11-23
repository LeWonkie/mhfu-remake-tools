"""Local, dependency-free reports for MHFU runs; never inspect game data or saves.

Log readers stop at the file size observed when opened, including for a live run.
JSON summaries contain parsed diagnostics, not arbitrary source-log text.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
import zipfile

MAX_LOG_BYTES = 10 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_META_BYTES = 1024 * 1024
SAFE_IMAGE = re.compile(r"[A-Za-z0-9_(). -]{1,180}\.(?:png|bmp)", re.I)
NUMBER = r"(\d+(?:\.\d*)?|\.\d+)"
PERF = re.compile(r"\[perf\] fps " + NUMBER + r" game " + NUMBER + r" speed " + NUMBER + r"%")
SPACE = re.compile(r"\bspace vertex " + NUMBER + r" index " + NUMBER + r" MiB\b")
DROP = re.compile(r"\[render\] a frame ran out of vertex space; the draws past it are not drawn \((\d+) so far\)")
MISSING = re.compile(r"\bMissing HLE[^A-Za-z_]*([A-Za-z_][A-Za-z0-9_]*)")
HLE = re.compile(r"^HLE imports: (\d+) total, (\d+) implemented, (\d+) logging stubs \(([^()]*)\)")
SECRET = re.compile(r"(?i)(?:password|passwd|mot.de.passe|api[_ -]?key|\btoken\b|[_ -]token\b|authorization|bearer\s|cookie|private[_ -]?key|\bsecret\b|[_ -]secret\b|credential|secret[_ -]?key|https?://[^\s/:]+:[^\s@]+@)")
MANIFEST_KEYS = ("id", "started_at", "seconds", "scenario", "variant", "settings_overrides", "env_overrides", "build", "state", "exit_code", "stop_reason", "asset_sha256", "seed_sha256", "seed_hash", "capture")
STYLE = """body{font:16px system-ui,sans-serif;max-width:1180px;margin:32px auto;padding:0 24px;color:#dce7ef;background:#101923}h1,h2{color:#fff}h1{font-size:30px}h2{margin-top:30px;font-size:21px}a{color:#78ccff}table{width:100%;border-collapse:collapse;margin:16px 0}td,th{padding:10px;border:1px solid #344757;text-align:left;vertical-align:top}th{background:#203140}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#172532;padding:16px;border-radius:8px}.note{padding:14px 18px;border-left:4px solid #e6bb62;background:#292a23}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.grid img{width:100%;height:auto;background:#000}figure{margin:0}figcaption{overflow-wrap:anywhere;margin:6px 0 16px;color:#aabfcf}.muted{color:#aabfcf}.pill{display:inline-block;background:#244359;border-radius:5px;padding:4px 10px}code{overflow-wrap:anywhere}"""


def _safe_file(root: Path, relative: str) -> Path | None:
    root = root.resolve()
    candidate = root / relative
    if candidate.is_symlink() or any(p.is_symlink() for p in candidate.parents if p != root and root in p.parents):
        return None
    try:
        resolved = candidate.resolve(strict=True)
        if resolved.is_file() and resolved.is_relative_to(root):
            return resolved
    except (OSError, ValueError):
        pass
    return None


def _scrub(value):
    if isinstance(value, dict):
        return {str(k): ("[masqué]" if SECRET.search(str(k)) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return "[information sensible masquée]" if SECRET.search(value) else value[:10000]
    return value


def _manifest(run_dir: Path) -> tuple[dict, str | None]:
    source = _safe_file(run_dir, "manifest.json")
    if not source:
        return {}, "Manifeste absent."
    try:
        if source.stat().st_size > MAX_META_BYTES:
            return {}, "Manifeste trop volumineux ; non lu."
        value = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            return {}, "Manifeste invalide."
        # Explicit fields prevent accidental inclusion of a complete process environment.
        return _scrub({key: value[key] for key in MANIFEST_KEYS if key in value}), None
    except (OSError, ValueError):
        return {}, "Manifeste momentanément indisponible ou invalide."


def _snapshot_lines(source: Path, max_bytes: int | None = None):
    """Read a bounded snapshot, skip overlong lines rather than retaining them."""
    with source.open("rb") as stream:
        size = source.stat().st_size
        offset = max(0, size - max_bytes) if max_bytes is not None else 0
        stream.seek(offset)
        remaining = size - offset
        if offset:
            partial = stream.readline(min(MAX_LINE_BYTES, remaining))
            remaining -= len(partial)
            skipping = not partial.endswith(b"\n")
        else:
            skipping = False
        while remaining > 0:
            raw = stream.readline(min(MAX_LINE_BYTES, remaining))
            if not raw:
                break
            remaining -= len(raw)
            complete = raw.endswith(b"\n") or remaining == 0
            if skipping:
                skipping = not complete
                continue
            if not complete:
                skipping = True
                continue
            yield raw.decode("utf-8-sig", errors="replace").rstrip("\r\n")


def _metric():
    return {"count": 0, "min": None, "max": None, "mean": None, "last": None}


def _add(metric: dict, value: float):
    metric["count"] += 1
    metric["mean"] = value if metric["mean"] is None else metric["mean"] + (value - metric["mean"]) / metric["count"]
    metric["min"] = value if metric["min"] is None else min(metric["min"], value)
    metric["max"] = value if metric["max"] is None else max(metric["max"], value)
    metric["last"] = value


def _captures(run_dir: Path) -> list[dict]:
    frame_dir = run_dir / "frames"
    if not frame_dir.is_dir() or frame_dir.is_symlink():
        return []
    found = []
    for item in frame_dir.iterdir():
        if not SAFE_IMAGE.fullmatch(item.name):
            continue
        source = _safe_file(run_dir, "frames/" + item.name)
        if source:
            try:
                info = source.stat()
                found.append({"file": "frames/" + item.name, "bytes": info.st_size, "modified_ns": info.st_mtime_ns})
            except OSError:
                continue
    return sorted(found, key=lambda entry: (entry["modified_ns"], entry["file"]))


def summarize_run(run_dir: Path) -> dict:
    """Return actual recorded evidence. A missing sample is None, never a pass."""
    run_dir = Path(run_dir).resolve(strict=True)
    manifest, manifest_error = _manifest(run_dir)
    summary = {
        "schema_version": 1, "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": manifest.get("id", run_dir.name), "manifest": manifest,
        "perf": {"samples": 0, "fps": _metric(), "game_fps": _metric(), "speed_percent": _metric(), "vertex_mib_max": None, "index_mib_max": None},
        "vertex_space": {"warning_lines": 0, "dropped_draws_lower_bound": 0},
        "hle": None, "missing_hle_names": [], "runtime_stop": None, "logs": [],
        "captures": _captures(run_dir),
        "capture_proof": {"status": "unavailable", "visual_validation": "non_effectuee", "synchronized_ge_state": False},
        "limitations": [
            "Les mesures sont des échantillons des journaux ; leur moyenne n’est pas pondérée par la durée.",
            "Le compteur de dessins abandonnés est une borne inférieure, pas un nombre d’images.",
            "Aucun avertissement trouvé ne prouve pas un rendu correct. Le HUD en quête exige un contrôle visuel.",
            "Instantané des fichiers : pendant une exécution, les journaux peuvent encore être en tampon.",
            "Les captures ne prouvent ni une identité d’image entre essais, ni un état GE synchronisé.",
        ],
    }
    if manifest_error:
        summary["limitations"].append(manifest_error)
    summary["hunt"] = None
    hunt_file = _safe_file(run_dir, "hunt.json")
    if hunt_file:
        try:
            if hunt_file.stat().st_size <= MAX_META_BYTES:
                data = json.loads(hunt_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    summary["hunt"] = _scrub({key: data.get(key) for key in (
                        "quest", "state", "phase", "reason", "objective", "kills", "stage",
                        "elapsed_seconds", "memory", "result_capture", "mission_state", "success",
                        "returned_to_village", "objective_progress", "objective_progress_available")})
        except (OSError, ValueError):
            summary["limitations"].append("Suivi de chasse momentanément indisponible.")
    missing = set()
    for filename in ("stdout.txt", "stderr.txt"):
        source = _safe_file(run_dir, filename)
        log = {"file": filename, "available": bool(source), "bytes_snapshot": None, "lines_read": 0}
        summary["logs"].append(log)
        if source is None:
            continue
        try:
            log["bytes_snapshot"] = source.stat().st_size
            for line in _snapshot_lines(source):
                log["lines_read"] += 1
                if match := PERF.search(line):
                    perf = summary["perf"]
                    perf["samples"] += 1
                    for key, val in zip(("fps", "game_fps", "speed_percent"), match.groups()):
                        _add(perf[key], float(val))
                if match := SPACE.search(line):
                    for key, val in zip(("vertex_mib_max", "index_mib_max"), match.groups()):
                        previous = summary["perf"][key]
                        summary["perf"][key] = max(previous or 0.0, float(val))
                if match := DROP.search(line):
                    drops = summary["vertex_space"]
                    drops["warning_lines"] += 1
                    drops["dropped_draws_lower_bound"] = max(drops["dropped_draws_lower_bound"], int(match[1]))
                if match := MISSING.search(line):
                    missing.add(match[1])
                if match := HLE.match(line):
                    summary["hle"] = {"total": int(match[1]), "implemented": int(match[2]), "logging_stubs": int(match[3]), "mode": _scrub(match[4])}
                if line.startswith("Runtime stopped:"):
                    reason = line.partition(":")[2].strip()
                    summary["runtime_stop"] = reason if reason in ("window closed", "quit from the menu", "requested stop", "end of replay") else "autre arrêt (voir journal local)"
        except OSError:
            log["error"] = "Lecture indisponible pendant cet instantané."
    summary["missing_hle_names"] = sorted(missing)
    if summary["captures"]:
        summary["capture_proof"]["status"] = "images_presentes_non_validees"
    summary["capture_proof"]["count"] = len(summary["captures"])
    return summary


def _json(path: Path, value) -> Path:
    if path.is_symlink():
        raise ValueError("Le fichier de sortie ne doit pas être un lien symbolique.")
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _page(title: str, body: str) -> str:
    return '<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>' + STYLE + '</style><body><h1>' + escape(title) + '</h1>' + body + '</body></html>'


def _fmt(value, decimals=1):
    if value is None:
        return "Non mesuré"
    return f"{value:.{decimals}f}" if isinstance(value, float) else str(value)


def _rows(summary: dict) -> list[tuple[str, str]]:
    meta = summary["manifest"]
    perf = summary["perf"]
    build = meta.get("build") if isinstance(meta.get("build"), dict) else {}
    return [
        ("Essai", str(summary["run_id"])), ("Scénario", str(meta.get("scenario", "Non enregistré"))),
        ("Variante", str(meta.get("variant", "Non enregistrée"))), ("État", str(meta.get("state", "Non enregistré"))),
        ("Commit / modifications locales", f"{build.get('git_commit', 'Non enregistré')} / {build.get('dirty', 'Non enregistré')}"),
        ("SHA-256 exécutable", str(build.get("executable_sha256", "Non enregistré"))),
        ("Échantillons de performance", str(perf["samples"])),
        ("Images présentées/s — moyenne / dernière", f"{_fmt(perf['fps']['mean'])} / {_fmt(perf['fps']['last'])}"),
        ("Images jeu/s — moyenne / dernière", f"{_fmt(perf['game_fps']['mean'])} / {_fmt(perf['game_fps']['last'])}"),
        ("Vitesse % — moyenne / dernière", f"{_fmt(perf['speed_percent']['mean'])} / {_fmt(perf['speed_percent']['last'])}"),
        ("Sommets / indices — pic observé en Mio", f"{_fmt(perf['vertex_mib_max'])} / {_fmt(perf['index_mib_max'])}"),
        ("Dessins abandonnés — au moins", str(summary["vertex_space"]["dropped_draws_lower_bound"])),
        ("Avertissements de saturation", str(summary["vertex_space"]["warning_lines"])),
        ("HLE manquantes détectées", ", ".join(summary["missing_hle_names"]) or "Aucune signalée dans les journaux lus"),
        ("Arrêt du runtime", summary["runtime_stop"] or "Aucun arrêt enregistré"),
        ("Code de sortie / motif atelier", f"{meta.get('exit_code', 'Non enregistré')} / {meta.get('stop_reason', 'Non enregistré')}"),
        ("Captures présentes / contrôle visuel", f"{len(summary['captures'])} / non effectué"),
    ]


def _table(rows: list[tuple[str, str]]) -> str:
    return '<table><tbody>' + ''.join('<tr><th>' + escape(key) + '</th><td>' + escape(value) + '</td></tr>' for key, value in rows) + '</tbody></table>'


def _images(captures: list[dict], prefix: str = "") -> str:
    if not captures:
        return '<p class="note">Aucune capture disponible. Le rendu et le HUD ne sont pas validés.</p>'
    return '<div class="grid">' + ''.join('<figure><a href="' + escape(prefix + quote(item["file"], safe="/"), quote=True) + '"><img loading="lazy" alt="Capture de l’essai, à examiner" src="' + escape(prefix + quote(item["file"], safe="/"), quote=True) + '"></a><figcaption>' + escape(item["file"]) + '</figcaption></figure>' for item in captures[-8:]) + '</div>'


def _report_body(summary: dict) -> str:
    return '<p class="pill">Instantané de diagnostic · ' + escape(summary["collected_at"]) + '</p>' + _table(_rows(summary)) + _hunt_html(summary.get("hunt")) + '<h2>Réglages et provenance</h2><pre>' + escape(json.dumps(summary["manifest"], indent=2, ensure_ascii=False)) + '</pre><h2>Captures à examiner</h2>' + _images(summary["captures"]) + '<h2>Disponibilité et limites</h2><ul>' + ''.join('<li>' + escape(item) + '</li>' for item in summary["limitations"]) + '</ul><pre>' + escape(json.dumps(summary["logs"], indent=2, ensure_ascii=False)) + '</pre>'


def _hunt_html(hunt):
    if not hunt:
        return ""
    goal = hunt.get("objective") or {}
    rows = [("Quête", str(goal.get("name", hunt.get("quest")))),
            ("Objectif vérifié", str(goal.get("description", goal.get("required", "Non enregistré")))),
            ("Morts observées par le contrôleur", str(hunt.get("kills", 0))),
            ("Résultat selon le jeu", {True: "Réussite", False: "Échec"}.get(hunt.get("success"), "Non établi")
             + f" (état de mission {hunt.get('mission_state')})"),
            ("Retour au village", {True: "Oui", False: "Non"}.get(hunt.get("returned_to_village"), "—")),
            ("État / phase", f"{hunt.get('state')} / {hunt.get('phase')}"),
            ("Motif de fin", str(hunt.get("reason") or "En cours")),
            ("Zone / durée observée", f"{hunt.get('stage')} / {hunt.get('elapsed_seconds')} s")]
    counter = hunt.get("objective_progress")
    if isinstance(counter, dict):
        label = "Objectif du jeu" if hunt.get("objective_progress_available") else "Dernier relevé de l’objectif du jeu"
        rows.insert(2, (label, f"{counter.get('completed')} / {counter.get('required')}"
                       + f" (relevé à {counter.get('observed_at')} s)"))
    return ("<h2>Chasse pilotée</h2>" + _table(rows) + '<p class="note">Les morts observées ne remplacent pas '
            "le compteur du jeu. Le résultat vient de l'état de mission du jeu (GameSys +0x2E : 3 réussite, "
            "5 échec) ; les captures de fin en sont la preuve visible.</p>"
            + "<h3>Lectures mémoire</h3><pre>" + escape(json.dumps(hunt.get("memory"), indent=2)) + "</pre>")


def write_run_report(run_dir: Path) -> Path:
    run_dir = Path(run_dir).resolve(strict=True)
    summary = summarize_run(run_dir)
    _json(run_dir / "diagnostics.json", summary)
    output = run_dir / "rapport.html"
    if output.is_symlink():
        raise ValueError("Le rapport ne doit pas être un lien symbolique.")
    output.write_text(_page("Rapport MHFU — " + str(summary["run_id"]), _report_body(summary)), encoding="utf-8")
    return output


def _comparison_warnings(first: dict, second: dict) -> list[str]:
    left, right = first["manifest"], second["manifest"]
    warnings = []
    for key, label in (("scenario", "Scénario"), ("asset_sha256", "Empreinte des données / de la copie de départ"), ("seed_sha256", "Empreinte de la sauvegarde de départ"), ("seed_hash", "Empreinte de départ"), ("settings_overrides", "Réglages demandés"), ("env_overrides", "Variables demandées")):
        if left.get(key) != right.get(key):
            warnings.append(label + " différents : comparaison à interpréter avec prudence.")
    if not left.get("scenario") or not right.get("scenario"):
        warnings.append("Scénario non enregistré pour au moins un essai.")
    if not any(left.get(key) and right.get(key) for key in ("seed_sha256", "seed_hash", "asset_sha256")):
        warnings.append("Empreinte de départ non disponible pour les deux essais.")
    warnings.append("La variante peut changer plusieurs paramètres. Ces écarts numériques ne prouvent pas la cause d’un changement de vitesse ou de rendu.")
    return warnings


def _copy_capture(source_dir: Path, capture: dict, destination: Path) -> dict | None:
    relative = capture.get("file", "")
    if not relative.startswith("frames/") or not SAFE_IMAGE.fullmatch(relative.removeprefix("frames/")):
        return None
    source = _safe_file(source_dir, relative)
    if source is None:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if destination.is_symlink():
            raise ValueError("La capture de sortie ne doit pas être un lien symbolique.")
        shutil.copyfile(source, destination)
        return {"file": destination.name, "bytes": destination.stat().st_size}
    except OSError:
        return None


def compare_runs(first_dir: Path, second_dir: Path, out_dir: Path) -> Path:
    first_dir, second_dir = Path(first_dir).resolve(strict=True), Path(second_dir).resolve(strict=True)
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    first, second = summarize_run(first_dir), summarize_run(second_dir)
    warnings = _comparison_warnings(first, second)
    data = {"schema_version": 1, "first": first, "second": second, "warnings": warnings, "performance_delta": {}}
    for key in ("fps", "game_fps", "speed_percent"):
        a, b = first["perf"][key]["mean"], second["perf"][key]["mean"]
        data["performance_delta"][key + "_mean_second_minus_first"] = b - a if a is not None and b is not None else None
    _json(out_dir / "comparaison.json", data)
    rows1, rows2 = _rows(first), _rows(second)
    body = '<p class="note">' + '<br>'.join(escape(item) for item in warnings) + '</p><table><thead><tr><th>Mesure</th><th>Premier essai</th><th>Second essai</th></tr></thead><tbody>'
    body += ''.join('<tr><th>' + escape(row1[0]) + '</th><td>' + escape(row1[1]) + '</td><td>' + escape(row2[1]) + '</td></tr>' for row1, row2 in zip(rows1, rows2)) + '</tbody></table>'
    body += '<h2>Deux dernières captures disponibles</h2><p>Images prises à des instants potentiellement différents ; aucune correspondance de scène ou validation automatique.</p><div class="grid">'
    for label, directory, summary, stem in (("Premier essai", first_dir, first, "premier"), ("Second essai", second_dir, second, "second")):
        copied = _copy_capture(directory, summary["captures"][-1], out_dir / (stem + Path(summary["captures"][-1]["file"]).suffix.lower())) if summary["captures"] else None
        body += '<section><h3>' + escape(label) + '</h3>' + _images([copied] if copied else []) + '</section>'
    body += '</div><h2>Différences de moyenne observées</h2><pre>' + escape(json.dumps(data["performance_delta"], indent=2, ensure_ascii=False)) + '</pre>'
    output = out_dir / "comparaison.html"
    if output.is_symlink():
        raise ValueError("Le rapport ne doit pas être un lien symbolique.")
    output.write_text(_page("Comparaison de deux essais MHFU", body), encoding="utf-8")
    return output


def _sanitized_copy(source: Path, destination: Path) -> dict:
    size = source.stat().st_size
    redacted = 0
    private_block = False
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        if size > MAX_LOG_BYTES:
            output.write("[Instantané tronqué : seule la fin du fichier, au plus 10 Mio, est conservée.]\n")
        for line in _snapshot_lines(source, MAX_LOG_BYTES):
            begins_private_block = "-----BEGIN" in line and "PRIVATE KEY-----" in line
            ends_private_block = "-----END" in line and "PRIVATE KEY-----" in line
            if begins_private_block:
                private_block = True
            if private_block or SECRET.search(line):
                line = "[Ligne contenant une information sensible masquée.]"
                redacted += 1
            output.write(line + "\n")
            if ends_private_block:
                private_block = False
    return {"available": True, "bytes_source_snapshot": size, "truncated": size > MAX_LOG_BYTES, "redacted_lines": redacted}


def write_bug_bundle(run_dir: Path, title: str, screenshot: Path | None = None) -> Path:
    """Create an allowlisted, sanitized snapshot directory and adjacent zip archive."""
    run_dir = Path(run_dir).resolve(strict=True)
    data_dir = run_dir.parent.parent if run_dir.parent.name == "runs" else run_dir.parent
    bug_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    destination = data_dir / "bugs" / bug_id
    destination.mkdir(parents=True, exist_ok=False)
    summary = summarize_run(run_dir)
    title = str(_scrub(title.strip()[:500] or "Signalement MHFU"))
    availability = {"manifest": bool(summary["manifest"]), "synchronized_ge_state": False}
    _json(destination / "manifest.json", summary["manifest"])
    for relative in ("stdout.txt", "stderr.txt", "input.txt", "data/settings.ini"):
        source = _safe_file(run_dir, relative)
        target = destination / ("settings.ini" if relative == "data/settings.ini" else relative)
        try:
            availability[relative] = _sanitized_copy(source, target) if source else {"available": False}
        except OSError:
            availability[relative] = {"available": False, "reason": "Lecture indisponible pendant l’instantané."}
    selected = None
    if screenshot is not None:
        try:
            requested = Path(screenshot).resolve(strict=True)
            # Caller cannot turn this API into a way to bundle arbitrary local files.
            selected = next((item for item in summary["captures"] if _safe_file(run_dir, item["file"]) == requested), None)
        except (OSError, ValueError):
            pass
    elif summary["captures"]:
        selected = summary["captures"][-1]
    copied = _copy_capture(run_dir, selected, destination / ("capture" + Path(selected["file"]).suffix.lower())) if selected else None
    availability["screenshot"] = {"available": bool(copied), "requested": screenshot is not None}
    # Bundle diagnostics must refer only to copied captures, never external run paths.
    summary["captures"] = [copied] if copied else []
    summary["capture_proof"] = {"status": "image_presente_non_validee" if copied else "unavailable", "visual_validation": "non_effectuee", "synchronized_ge_state": False, "count": int(bool(copied))}
    summary["bug"] = {"title": title, "id": bug_id, "availability": availability}
    _json(destination / "diagnostics.json", summary)
    html = _page("Signalement MHFU — " + title, '<p class="note">Capture de fichiers au moment du signalement. Aucun enregistrement GE ni dump mémoire. Les captures restent à vérifier visuellement.</p>' + _report_body(summary))
    (destination / "diagnostics.html").write_text(html, encoding="utf-8")
    (destination / "README.md").write_text("# " + title + "\n\nOuvrir diagnostics.html pour examiner cet instantané.\n\nContenu limité : manifeste filtré, réglages et journaux expurgés, diagnostics et une capture lorsqu’elle est disponible. Les journaux sont limités à leur fin (10 Mio chacun). Aucune donnée du jeu, sauvegarde, mémoire ou commande GE n’est collectée.\n\nLa présence d’une image ne valide pas le HUD. Cet instantané peut précéder la fin d’une exécution et ne constitue pas un rejeu déterministe.\n\nDisponibilité :\n\n```json\n" + json.dumps(availability, indent=2, ensure_ascii=False) + "\n```\n", encoding="utf-8")
    allowed = {"manifest.json", "stdout.txt", "stderr.txt", "input.txt", "settings.ini", "diagnostics.json", "diagnostics.html", "README.md", "capture.png", "capture.bmp"}
    archive = destination.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name in sorted(allowed):
            source = _safe_file(destination, name)
            if source:
                package.write(source, arcname=name)
    return destination.resolve()
