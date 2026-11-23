"""Atelier MHFU engine: isolated, bounded game runs of the PSPRecomp build.

Every run starts from a private copy of the reference saves (originals and
earlier runs are never written), with a purged environment (strict HLE, no
Debug page), a deadline, and a manifest recording what was launched. Only the
process the workshop started is ever stopped. Scripts and live commands are
validated strictly before they reach the game's input parser.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import threading
import time
import uuid

import atelier_reports

VARIANTS = {
    "normal": {"label": "Normal", "env": {}},
    "fps30": {"label": "30 images/s", "env": {"MHP3RD_FRAME_RATE": "30"}},
    "legacy32": {"label": "Tampon de sommets 32 Mio (ancien)", "env": {"MHP3RD_VERTEX_BUFFER_MB": "32"}},
    "no_merge": {"label": "Sans fusion de dessins", "env": {"MHP3RD_NO_DRAW_MERGE": "1"}},
    "no_alpha": {"label": "Sans variantes alpha", "env": {"MHP3RD_NO_ALPHA_VARIANTS": "1"}},
    "no_fb": {"label": "Sans textures de framebuffer", "env": {"MHP3RD_NO_FB_TEXTURES": "1"}},
    # Measurement only: the draws that fill a frame's vertex region are logged.
    "space_trace": {"label": "Trace du tampon de sommets", "env": {"MHP3RD_TRACE_FRAME_SPACE": "16"}},
    # Render targets, DMA copies into VRAM and frame space, to see what overlaps
    # the display lists the game keeps in VRAM.
    "fb_trace": {"label": "Trace sommets + cibles/copies VRAM",
                 "env": {"MHP3RD_TRACE_FRAME_SPACE": "16", "MHP3RD_TRACE_FB_TEXTURES": "1"}},
    # A/B: the same trace without the write-back of displayed frames to VRAM.
    "no_fb_space": {"label": "Sans réécriture VRAM + trace sommets",
                    "env": {"MHP3RD_NO_FB_TEXTURES": "1", "MHP3RD_TRACE_FRAME_SPACE": "16"}},
    # A/B of sparse indexed prims: the previous whole-span decode.
    "no_sparse_space": {"label": "Décodage par plage complète (ancien) + trace sommets",
                        "env": {"MHP3RD_NO_SPARSE_VERTICES": "1", "MHP3RD_TRACE_FRAME_SPACE": "16"}},
    "space_flow": {"label": "Trace du tampon de sommets + sauts GE vers la VRAM",
                   "env": {"MHP3RD_TRACE_FRAME_SPACE": "16", "MHP3RD_TRACE_GE_FLOW": "1"}},
    "vb128_trace": {"label": "Tampon de sommets 128 Mio + trace",
                    "env": {"MHP3RD_VERTEX_BUFFER_MB": "128", "MHP3RD_TRACE_FRAME_SPACE": "16"}},
    "vb256_trace": {"label": "Tampon de sommets 256 Mio + trace",
                    "env": {"MHP3RD_VERTEX_BUFFER_MB": "256", "MHP3RD_TRACE_FRAME_SPACE": "16"}},
}

RTC_MIN_US = 946684800000000
RTC_MAX_US = 4102444800000000


def register_rtc_variant(variant: str, rtc_us: int | None) -> str:
    """Compose a variant after environment purging, without changing its base."""
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant: {variant}")
    if rtc_us is None:
        return variant
    if isinstance(rtc_us, bool) or not isinstance(rtc_us, int) or not RTC_MIN_US <= rtc_us <= RTC_MAX_US:
        raise ValueError(f"RTC epoch must be an integer in {RTC_MIN_US}..{RTC_MAX_US} microseconds")
    name = f"{variant}_rtc_{rtc_us}"
    VARIANTS[name] = {"label": f"{VARIANTS[variant]['label']} (RTC {rtc_us} us)",
                      "env": {**VARIANTS[variant]["env"], "MHP3RD_DEVICE_TEST": "1",
                              "MHP3RD_DEVICE_TEST_RTC_US": str(rtc_us)}}
    return name


def capture_size(path: Path) -> list[int] | None:
    """Read actual window pixels from a BMP header; unavailable is not a guess."""
    try:
        with Path(path).open("rb") as stream:
            header = stream.read(26)
        if len(header) != 26 or header[:2] != b"BM" or struct.unpack_from("<I", header, 14)[0] < 40:
            return None
        width, height = struct.unpack_from("<ii", header, 18)
        return [width, abs(height)] if width > 0 and height != 0 else None
    except (OSError, struct.error):
        return None


def _capture_metadata(run_dir: Path, manifest: dict) -> None:
    sizes = []
    for path in sorted((run_dir / "frames").glob("*.bmp")):
        # Automatic frame_N images capture the render target, not the window.
        # Unnamed scripted shots share that ambiguous name, so omit them too.
        if re.fullmatch(r"frame_\d+", path.stem):
            continue
        size = capture_size(path)
        if size and size not in sizes:
            sizes.append(size)
    manifest["window_sizes"] = sizes
    manifest["window_size"] = sizes[0] if sizes else None
    manifest["window_size_source"] = "capture_bmp_pixels" if sizes else None

MAX_SECONDS = 1800
MAX_FRAME = 10_000_000
MAX_HOLD_FRAMES = 36_000
PAD_BUTTONS = {"a", "b", "x", "y", "back", "guide", "start", "leftstick", "rightstick", "leftshoulder",
               "rightshoulder", "dpup", "dpdown", "dpleft", "dpright"}
AXES = {"leftx", "lefty", "rightx", "righty", "lefttrigger", "righttrigger"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
MOVES = {"up": ("lefty", -1), "down": ("lefty", 1), "left": ("leftx", -1), "right": ("leftx", 1)}
MOVE_FRAMES = 30
TEST_TARGETS = ("mhp3rd_ge_state_tests", "mhp3rd_render_tests", "mhp3rd_vertex_buffer_budget_tests",
                "mhp3rd_renderer_ui_tests", "mhp3rd_opaque_alpha_tests", "mhp3rd_interpolation_tests",
                "mhp3rd_media_hle_tests")
ACTIVE_STATES = ("starting", "running", "stopping")
# Launched from pythonw (no console), every console program would open its own
# window: git status polls made cmd windows flash endlessly. Hide them all.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GIT_CACHE_SECONDS = 5.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_digest(root: Path) -> str:
    """Hash of every file (relative path + content) below root."""
    h = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0" + _digest(path).encode() + b"\n")
    return h.hexdigest()


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _hold_frames(text: str) -> int:
    if not re.fullmatch(r"\d{1,9}", text):
        raise ValueError(f"durée invalide : {text!r}")
    frames = int(text)
    if not 1 <= frames <= MAX_HOLD_FRAMES:
        raise ValueError(f"durée hors bornes (1..{MAX_HOLD_FRAMES} images) : {frames}")
    return frames


def validate_action(action: str) -> str:
    """One game input action, normalized; raises ValueError for anything else."""
    if not isinstance(action, str) or any(c in action for c in "\r\n;:\0"):
        raise ValueError("une seule action sans saut de ligne, ';' ni ':'")
    words = action.split()
    if not words:
        raise ValueError("action vide")
    verb, rest = words[0], words[1:]
    if verb == "pad":
        if not 1 <= len(rest) <= 2:
            raise ValueError("pad <bouton[+bouton]> [images]")
        buttons = rest[0].split("+")
        if not buttons or any(b not in PAD_BUTTONS for b in buttons):
            raise ValueError(f"bouton inconnu : {rest[0]}")
        return " ".join(["pad", rest[0]] + ([str(_hold_frames(rest[1]))] if len(rest) == 2 else []))
    if verb == "axis":
        if len(rest) != 2 or rest[0] not in AXES:
            raise ValueError("axis <axe> <valeur>")
        try:
            value = float(rest[1])
        except ValueError:
            raise ValueError(f"valeur d'axe invalide : {rest[1]}") from None
        if not math.isfinite(value) or not -1.0 <= value <= 1.0:
            raise ValueError("valeur d'axe hors de [-1, 1]")
        return f"axis {rest[0]} {rest[1]}"
    if verb == "shot":
        if len(rest) > 1 or (rest and not NAME.fullmatch(rest[0])):
            raise ValueError("shot [nom simple : lettres, chiffres, _ et -]")
        return " ".join(["shot", *rest])
    if verb == "quit":
        if rest:
            raise ValueError("quit ne prend pas d'argument")
        return "quit"
    raise ValueError(f"action non autorisée : {verb}")


def validate_script(script: str) -> str:
    """`frame:action;...` with bounded, non-decreasing frames; newlines count as ';'."""
    if not isinstance(script, str):
        raise ValueError("le script doit être du texte")
    entries = [e.strip() for e in script.replace("\r\n", "\n").replace("\n", ";").split(";")]
    normalized, previous = [], -1
    for entry in entries:
        if not entry:
            continue
        frame_text, sep, action = entry.partition(":")
        frame_text = frame_text.strip()
        if not sep or not re.fullmatch(r"\d{1,8}", frame_text):
            raise ValueError(f"étape invalide (attendu image:action) : {entry!r}")
        frame = int(frame_text)
        if frame > MAX_FRAME:
            raise ValueError(f"image hors bornes (max {MAX_FRAME}) : {frame}")
        if frame < previous:
            raise ValueError(f"images non croissantes : {frame} après {previous}")
        previous = frame
        normalized.append(f"{frame}:{validate_action(action.strip())}")
    return ";".join(normalized)


# Private settings a scenario may change in its trial's settings.ini: the
# engine's fast-forward and fast loading only (profile README, "Fast-forward"). Ad hoc play
# keeps it off whatever is written here.
SETTING_VALUES = {"video.fast_forward": ("hold", "toggle", "off"), "video.fast_loading": ("0", "1"),
                  "video.fast_forward_speed": tuple(str(n) for n in range(2, 9))}


def validate_settings(settings: dict) -> dict:
    if not isinstance(settings, dict):
        raise ValueError("réglages : un dictionnaire")
    for key, value in settings.items():
        if key not in SETTING_VALUES or value not in SETTING_VALUES[key]:
            raise ValueError(f"réglage non autorisé : {key}={value}")
    return dict(settings)


def _validate_seconds(seconds) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds != int(seconds):
        raise ValueError("durée en secondes entière")
    seconds = int(seconds)
    if not 1 <= seconds <= MAX_SECONDS:
        raise ValueError(f"durée hors bornes (1..{MAX_SECONDS} s) : {seconds}")
    return seconds


class Workshop:
    def __init__(self, project, data_dir=None, process_command=None):
        self.project = Path(project).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir else self.project / "atelier-psprecomp"
        self.repo = self.project / "psprecomp-mhfu"
        self.build_dir = self.repo / "out" / "mhfu-probe"
        self.executable = self.build_dir / "bin" / "Yakumo.exe"
        self.process_command = list(process_command) if process_command else None
        self.builtin_scenarios = Path(__file__).resolve().parent / "scenarios"
        self._lock = threading.RLock()
        self._active: dict | None = None
        self._busy = False

    # ----- paths and state -------------------------------------------------
    @property
    def _assets(self) -> Path:
        return self.data_dir / "assets"

    def _current_seed(self) -> dict | None:
        current = self.data_dir / "current.json"
        if not current.is_file():
            return None
        info = json.loads(current.read_text(encoding="utf-8"))
        return info if (self.data_dir / "seeds" / info["id"] / "ms0").is_dir() else None

    def _seed(self, seed_id: str) -> dict:
        """A named starting point (campaigns), without changing the current one."""
        info = self.data_dir / "seeds" / str(seed_id) / "seed.json"
        if not NAME.fullmatch(str(seed_id)) or not info.is_file():
            raise ValueError(f"point de départ inconnu : {seed_id}")
        return json.loads(info.read_text(encoding="utf-8"))

    def _git(self) -> dict:
        def run(*args):
            r = subprocess.run(["git", "-c", "safe.directory=" + str(self.repo), *args], cwd=self.repo, capture_output=True, text=True,
                               creationflags=NO_WINDOW)
            return r.stdout.strip() if r.returncode == 0 else None
        if not self.repo.is_dir():
            return {}
        cached = getattr(self, "_git_cache", None)
        if cached and time.monotonic() - cached[0] < GIT_CACHE_SECONDS:
            return dict(cached[1])
        status = run("status", "--porcelain")
        info = {"branch": run("rev-parse", "--abbrev-ref", "HEAD"), "commit": run("rev-parse", "HEAD"),
                "dirty": bool(status) if status is not None else None}
        self._git_cache = (time.monotonic(), info)
        return dict(info)

    def status(self) -> dict:
        with self._lock:
            active = None
            if self._active:
                active = {k: self._active[k] for k in ("id", "run_dir", "state", "pid", "scenario", "variant")}
                active["run_dir"] = str(active["run_dir"])
            seed = self._current_seed()
            return {"project": str(self.project), "data_dir": str(self.data_dir),
                    "executable": str(self.executable) if self.executable.is_file() else None,
                    "prepared": seed is not None and (self._assets / "reference.json").is_file(),
                    "seed": seed, "git": self._git(), "active": active}

    # ----- preparation, builds ----------------------------------------------
    def prepare(self, seed_ms0=None) -> Path:
        """Private copies of EBOOT.ELF and disc.iso (once) and a new seed from `seed_ms0`."""
        with self._lock:
            if self._active or self._busy:
                raise RuntimeError("un essai ou une compilation est en cours")
            game = self.project / "game-mhfu"
            seed_ms0 = Path(seed_ms0) if seed_ms0 else game / "ms0"
            if not seed_ms0.is_dir():
                raise ValueError(f"sauvegardes de départ introuvables : {seed_ms0}")
            self._assets.mkdir(parents=True, exist_ok=True)
            reference = self._assets / "reference.json"
            if not reference.is_file():
                for name in ("EBOOT.ELF", "disc.iso"):
                    if not (game / name).is_file():
                        raise ValueError(f"fichier de jeu introuvable : {game / name}")
                    shutil.copy2(game / name, self._assets / name)
                _write_json(reference, {"source": str(game), "copied_at": _now(),
                                        "eboot_sha256": _digest(self._assets / "EBOOT.ELF"),
                                        "disc_sha256": _digest(self._assets / "disc.iso")})
            return self._new_seed(seed_ms0, label=f"préparé depuis {seed_ms0}")

    def _new_seed(self, source_ms0: Path, label: str, name: str = "") -> Path:
        if name and not NAME.fullmatch(name):
            raise ValueError("nom de point de départ : lettres, chiffres, _ et -")
        seed_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + (name or uuid.uuid4().hex[:6])
        seed = self.data_dir / "seeds" / seed_id
        shutil.copytree(source_ms0, seed / "ms0")
        info = {"id": seed_id, "label": label, "created_at": _now(), "seed_sha256": _tree_digest(seed / "ms0")}
        _write_json(seed / "seed.json", info)
        _write_json(self.data_dir / "current.json", info)
        return seed

    def build(self, run_tests=False) -> dict:
        with self._lock:
            if self._active or self._busy:
                raise RuntimeError("un essai ou une compilation est en cours")
            self._busy = True
        try:
            logs = self.data_dir / "builds"
            logs.mkdir(parents=True, exist_ok=True)
            log = logs / (datetime.now().strftime("%Y%m%d-%H%M%S") + ("-tests" if run_tests else "") + ".log")
            targets = ["Yakumo", *(TEST_TARGETS if run_tests else ())]
            env_cmd = self.project / "deps" / "env.cmd"
            command = f'call "{env_cmd}" && cmake --build "{self.build_dir}" -j2 --target {" ".join(targets)}'
            with log.open("w", encoding="utf-8", errors="replace") as out:
                # One raw command line: a list would make Python escape the quotes as \" ,
                # which cmd.exe does not understand.
                code = subprocess.run(f'cmd /d /s /c "{command}"', stdout=out, stderr=subprocess.STDOUT,
                                      creationflags=NO_WINDOW).returncode
                results = {}
                if code == 0 and run_tests:
                    for target in TEST_TARGETS:
                        exe = self.build_dir / "bin" / f"{target}.exe"
                        r = subprocess.run([str(exe)], cwd=exe.parent, capture_output=True, text=True, creationflags=NO_WINDOW,
                                           encoding="utf-8", errors="replace")
                        out.write(f"\n== {target} (exit {r.returncode})\n{r.stdout}{r.stderr}")
                        results[target] = r.returncode
            ok = code == 0 and all(v == 0 for v in results.values())
            return {"ok": ok, "exit_code": code, "tests": results, "log": str(log)}
        finally:
            with self._lock:
                self._busy = False

    # ----- scenarios ---------------------------------------------------------
    def scenarios(self) -> list[dict]:
        found = {}
    …698 tokens truncated…stance(scenario, dict):
            item = dict(scenario)
        else:
            item = next((s for s in self.scenarios() if s["name"] == scenario), None)
            if item is None:
                raise ValueError(f"scénario inconnu : {scenario}")
        item["script"] = validate_script(item.get("script", ""))
        return item

    # ----- runs --------------------------------------------------------------
    def start(self, scenario, variant="normal", seconds=60, seed_id=None) -> str:
        seconds = _validate_seconds(seconds)
        if variant not in VARIANTS:
            raise ValueError(f"variante inconnue : {variant}")
        item = self._scenario(scenario)
        overrides = validate_settings(item.get("settings") or {})
        every = item.get("screenshot_every", 600)
        if isinstance(every, bool) or not isinstance(every, int) or not 0 <= every <= 100000:
            raise ValueError("captures automatiques : 0 (aucune) à 100000 images")
        with self._lock:
            if self._active or self._busy:
                raise RuntimeError("un essai ou une compilation est déjà en cours")
            seed = self._current_seed() if seed_id is None else self._seed(seed_id)
            reference = self._assets / "reference.json"
            if seed is None or not reference.is_file():
                raise RuntimeError("préparez d'abord l'atelier (copie privée et sauvegardes de départ)")
            if self.process_command is None and not self.executable.is_file():
                raise RuntimeError(f"exécutable absent : {self.executable}")
            save_source = self.data_dir / "seeds" / seed["id"] / "ms0"
            if item.get("private_ms0") is not None:
                save_source = Path(item["private_ms0"]).resolve()
                private_runs = (self.data_dir / "runs").resolve()
                if (not save_source.is_dir() or save_source.name != "ms0" or save_source.parent.name != "game"
                        or save_source.parent.parent.parent != private_runs):
                    raise ValueError("rechargement : ms0 doit appartenir à un essai privé de cet atelier")
                source_manifest = save_source.parent.parent / "manifest.json"
                if not source_manifest.is_file() or json.loads(source_manifest.read_text(encoding="utf-8")).get("state") == "running":
                    raise ValueError("rechargement : l'essai source doit être arrêté")
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            run_dir = self.data_dir / "runs" / run_id
            game, data, frames = run_dir / "game", run_dir / "data", run_dir / "frames"
            game.mkdir(parents=True)
            data.mkdir()
            frames.mkdir()
            for name in ("EBOOT.ELF", "disc.iso"):
                try:
                    os.link(self._assets / name, game / name)  # read-only use: hard link
                except OSError:
                    shutil.copy2(self._assets / name, game / name)
            shutil.copytree(save_source, game / "ms0")
            self._settings(data / "settings.ini", fast_boot=bool(item.get("fast_boot")), overrides=overrides)
            live = run_dir / "input.txt"
            live.write_text("", encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("MHP3RD_", "PSPRECOMP_"))}
            applied = {"MHP3RD_STRICT_HLE": "1", "MHP3RD_DEBUG_MENU": "0", "MHP3RD_DATA_DIR": str(data),
                       "MHP3RD_INPUT_LIVE": str(live), "MHP3RD_SCREENSHOT_DIR": str(frames),
                       "MHP3RD_SCREENSHOT_EVERY": str(every), "MHP3RD_PERF": "log",
                       "MHP3RD_WINDOW_TITLE": "Atelier MHFU - " + str(item.get("label", item.get("name", "essai")))}
            if item["script"]:
                applied["MHP3RD_INPUT_SCRIPT"] = item["script"]
            if item.get("debug"):
                # Developer command file (memory tools, MHFU commands): campaigns only.
                (run_dir / "debug.txt").write_text("", encoding="utf-8")
                applied["MHP3RD_DEBUG_MENU"] = "1"
                applied["MHP3RD_DEBUG_COMMANDS"] = str(run_dir / "debug.txt")
            if item.get("dump_overlays"):
                # Overlays with no recompiled library are saved here, for add_overlay.py.
                applied["MHP3RD_DUMP_OVERLAYS"] = str(self.data_dir / "overlay-dumps")
            applied.update(VARIANTS[variant]["env"])
            env.update(applied)
            command = [*(self.process_command or [str(self.executable)]), str(game)]
            cwd = self.executable.parent if self.process_command is None else run_dir
            manifest = {"id": run_id, "run_dir": str(run_dir), "scenario": item.get("name"), "label": item.get("label"),
                        "script": item["script"], "variant": variant, "seconds": seconds, "state": "starting",
                        "started_at": _now(), "seed_id": seed["id"], "seed_sha256": seed["seed_sha256"],
                        "rtc_us": int(applied["MHP3RD_DEVICE_TEST_RTC_US"]) if
                        applied.get("MHP3RD_DEVICE_TEST") == "1" and "MHP3RD_DEVICE_TEST_RTC_US" in applied else None,
                        "rtc_epoch_confirmed": None, "window_size": None, "window_sizes": [], "quest_results": [],
                        "asset_sha256": json.loads(reference.read_text(encoding="utf-8")),
                        "git": self._git(), "env": {k: v for k, v in applied.items() if k != "MHP3RD_WINDOW_TITLE"},
                        "settings_overrides": overrides,
                        "executable": command[0],
                        "executable_sha256": _digest(Path(command[0])) if Path(command[0]).is_file() else None,
                        "exit_code": None, "stop_reason": None}
            manifest["asset_sha256"] = hashlib.sha256(json.dumps(manifest["asset_sha256"], sort_keys=True).encode()).hexdigest()
            if item.get("private_ms0") is not None:
                manifest["reload_source"] = str(save_source)
                manifest["reload_source_sha256"] = _tree_digest(save_source)
            _write_json(run_dir / "manifest.json", manifest)
            stdout = (run_dir / "stdout.txt").open("wb")
            stderr = (run_dir / "stderr.txt").open("wb")
            try:
                process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                           creationflags=NO_WINDOW)
            except OSError:
                stdout.close()
                stderr.close()
                manifest.update(state="failed", stop_reason="lancement impossible", ended_at=_now())
                _write_json(run_dir / "manifest.json", manifest)
                raise
            manifest.update(state="running", pid=process.pid)
            _write_json(run_dir / "manifest.json", manifest)
            self._active = {"id": run_id, "run_dir": run_dir, "state": "running", "pid": process.pid,
                            "scenario": item.get("name"), "variant": variant, "process": process,
                            "manifest": manifest, "live": live, "files": (stdout, stderr),
                            "deadline": time.monotonic() + seconds, "stop_requested": False,
                            "done": threading.Event()}
            threading.Thread(target=self._monitor, args=(self._active,), daemon=True, name="atelier-run").start()
            return run_id

    def _settings(self, destination: Path, fast_boot: bool, overrides: dict | None = None) -> None:
        source = Path(os.environ.get("APPDATA", "")) / "Yakumo" / "MHP3rd" / "settings.ini"
        text = source.read_text(encoding="utf-8") if source.is_file() else ""
        overrides = overrides or {}
        replaced = ("input.background_gamepad=", "input.pad.fast_forward=", *(key + "=" for key in overrides))
        lines = [l for l in text.splitlines() if not l.startswith(replaced)]
        lines.append("input.background_gamepad=1")  # scripted pad works while the window is unfocused
        lines.append("input.pad.fast_forward=" + ("Pad Guide" if fast_boot else ""))
        lines.extend(f"{key}={value}" for key, value in overrides.items())
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _monitor(self, run: dict) -> None:
        process = run["process"]
        try:
            while process.poll() is None:
                if time.monotonic() >= run["deadline"] and not run["stop_requested"]:
                    self._request_quit(run, "échéance atteinte")
                if run["stop_requested"] and time.monotonic() >= run["stop_requested"] + 3:
                    process.kill()  # only the process this workshop started
                time.sleep(0.05)
        finally:
            for stream in run["files"]:
                stream.close()
            reason = None
            stdout = Path(run["run_dir"]) / "stdout.txt"
            output = stdout.read_text(encoding="utf-8", errors="replace")
            for line in output.splitlines():
                if line.startswith("Runtime stopped:"):
                    reason = line.split(":", 1)[1].strip()
            with self._lock:
                manifest = run["manifest"]
                manifest["exit_code"] = process.returncode
                manifest["ended_at"] = _now()
                manifest["runtime_stop"] = reason
                if manifest.get("rtc_us") is not None:
                    manifest["rtc_epoch_confirmed"] = (
                        f"[device-test] RTC epoch {manifest['rtc_us']} us" in output)
                _capture_metadata(Path(run["run_dir"]), manifest)
                requested = manifest.get("stop_reason")
                clean = reason in (None, "window closed", "quit from the menu") and process.returncode in (0, 4)
                manifest["state"] = ("stopped" if requested == "arrêt demandé" else "completed") if clean else "failed"
                if not clean and not requested:
                    manifest["stop_reason"] = reason or f"code de sortie {process.returncode}"
                _write_json(Path(run["run_dir"]) / "manifest.json", manifest)
            try:
                atelier_reports.write_run_report(Path(run["run_dir"]))
            except Exception as error:  # the report is a convenience; the run stays recorded
                with self._lock:
                    manifest["report_error"] = str(error)
                    _write_json(Path(run["run_dir"]) / "manifest.json", manifest)
            with self._lock:
                if self._active is run:
                    self._active = None
            run["done"].set()

    def record_quest_result(self, run_id: str, result: dict) -> None:
        """Checkpoint campaign observations in this run, serialized with shutdown."""
        with self._lock:
            run_dir = self._run_dir(run_id)
            if self._active and self._active["id"] == run_id:
                manifest = self._active["manifest"]
            else:
                manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            # Take a JSON copy: later changes to the caller's lists cannot alter the run.
            manifest.setdefault("quest_results", []).append(json.loads(json.dumps(result)))
            _capture_metadata(run_dir, manifest)
            _write_json(run_dir / "manifest.json", manifest)

    def _request_quit(self, run: dict, reason: str) -> None:
        if run["stop_requested"]:
            return
        run["stop_requested"] = time.monotonic()
        run["state"] = "stopping"
        run["manifest"]["stop_reason"] = reason
        with run["live"].open("a", encoding="utf-8") as stream:
            stream.write("0:quit\n")

    def stop(self, timeout=10) -> None:
        """Ask the game to quit, then (after 3 s) kill it; returns once the run is recorded."""
        with self._lock:
            run = self._active
        if run is None:
            return
        self._request_quit(run, "arrêt demandé")
        if not run["done"].wait(timeout):
            run["process"].kill()  # the monitor thread could not finish: last resort, own process only
            run["done"].wait(5)

    def wait(self, timeout=60) -> bool:
        with self._lock:
            run = self._active
        return True if run is None else run["done"].wait(timeout)

    def command(self, action: str) -> str:
        """One live control: pad/axis/shot/quit, plus UI macros `move <dir>` and `screenshot`."""
        with self._lock:
            run = self._active
            if run is None or run["state"] != "running":
                raise RuntimeError("aucun essai en cours")
        if not isinstance(action, str) or any(c in action for c in "\r\n;\0"):
            raise ValueError("une seule action")
        words = action.split()
        if words[:1] == ["move"] and len(words) == 2 and words[1] in MOVES:
            axis, value = MOVES[words[1]]
            lines = [f"0:axis {axis} {value}", f"{MOVE_FRAMES}:axis {axis} 0"]
        elif action.strip() == "screenshot":
            name = "capture-" + datetime.now().strftime("%H%M%S") + "-" + uuid.uuid4().hex[:4]
            lines = [self._shot_line(run, name)]
        else:
            lines = [f"0:{validate_action(action)}"]
        with run["live"].open("a", encoding="utf-8") as stream:
            stream.write("".join(line + "\n" for line in lines))
        return lines[-1].split(":", 1)[1]

    def debug_command(self, line: str) -> None:
        """One line for the game's developer command file (runs with `debug` only)."""
        self.debug_commands([line])

    def debug_commands(self, lines: list[str]) -> None:
        """Append a validated batch in one write, consumed by one console frame."""
        if not lines or any(not isinstance(line, str) or any(c in line for c in "\r\n\0") for line in lines):
            raise ValueError("une ou plusieurs commandes, une par ligne")
        with self._lock:
            run = self._active
            if run is None or run["state"] != "running":
                raise RuntimeError("aucun essai en cours")
        path = Path(run["run_dir"]) / "debug.txt"
        if not path.is_file():
            raise RuntimeError("cet essai n'a pas les commandes de développement")
        with path.open("ab") as stream:
            stream.write("".join(line + "\n" for line in lines).encode("utf-8"))

    @staticmethod
    def _shot_line(run: dict, name: str) -> str:
        """The game keeps one window capture per frame: space shots asked close together."""
        now = time.monotonic()
        offset = 10 if now - run.get("last_shot", -10.0) < 1.0 else 0
        run["last_shot"] = now
        return f"{offset}:shot {name}"

    def runs(self) -> list[dict]:
        found = []
        for manifest in (self.data_dir / "runs").glob("*/manifest.json") if (self.data_dir / "runs").is_dir() else ():
            try:
                found.append(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(found, key=lambda m: m.get("id", ""), reverse=True)

    def _run_dir(self, run_id) -> Path:
        if not isinstance(run_id, str) or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", run_id):
            raise ValueError(f"identifiant d'essai invalide : {run_id}")
        path = self.data_dir / "runs" / run_id
        if not path.is_dir():
            raise ValueError(f"essai introuvable : {run_id}")
        return path

    def checkpoint(self, name="") -> Path:
        """New starting point from the saves of the last finished run (originals untouched)."""
        with self._lock:
            if self._active or self._busy:
                raise RuntimeError("attendez la fin de l'essai en cours")
        run = next((r for r in self.runs() if r.get("state") not in ACTIVE_STATES), None)
        if run is None:
            raise RuntimeError("aucun essai terminé")
        return self._new_seed(Path(run["run_dir"]) / "game" / "ms0", label=f"sauvegardes après l'essai {run['id']}",
                              name=name or "")

    def compare(self, first, second) -> Path:
        out = self.data_dir / "comparisons" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4])
        out.mkdir(parents=True)
        return atelier_reports.compare_runs(self._run_dir(first), self._run_dir(second), out)

    def compare_variants(self, scenario, first="normal", second="legacy32", seconds=60) -> Path:
        ids = []
        for variant in (first, second):
            ids.append(self.start(scenario, variant, seconds))
            if not self.wait(timeout=seconds + 30):
                self.stop()
                self.wait(timeout=10)
        return self.compare(*ids)

    def bug_report(self, title="") -> Path:
        """Bug folder + zip: manifest, settings, filtered logs and a capture asked from the game."""
        with self._lock:
            run = self._active
        screenshot = None
        if run is not None and run["state"] == "running":
            name = "bug-" + uuid.uuid4().hex[:8]
            with run["live"].open("a", encoding="utf-8") as stream:
                stream.write(self._shot_line(run, name) + "\n")
            target = Path(run["run_dir"]) / "frames" / f"{name}.bmp"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not target.is_file():
                time.sleep(0.1)
            screenshot = target if target.is_file() else None
            run_dir = Path(run["run_dir"])
        else:
            last = next(iter(self.runs()), None)
            if last is None:
                raise RuntimeError("aucun essai à signaler")
            run_dir = Path(last["run_dir"])
        return atelier_reports.write_bug_bundle(run_dir, title, screenshot)
