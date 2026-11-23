"""Generate and build the captured MHFU overlay libraries, with resumable builds.

Run through precompile_overlays.cmd to use the configured MSVC environment.
Complete generated corpora are reused, but their CMake targets are always built:
an interrupted build may have left no DLL even when generation succeeded.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
REPO = PROJECT / "psprecomp-mhfu"
BUILD = REPO / "out" / "mhfu-probe"
DUMPS = PROJECT / "atelier-psprecomp" / "overlay-dumps"
ADD = REPO / "profiles" / "mhp3rd" / "tools" / "add_overlay.py"
CORPORA = REPO / "profiles" / "mhp3rd" / "overlays"


def fnv1a64(data: bytes) -> int:
    value = 0xCBF29CE484222325
    for byte in data:
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


@dataclass(frozen=True)
class Overlay:
    base: int
    name: str
    image_size: int
    code_size: int
    digest: int

    @property
    def prefix(self) -> str:
        return f"ovl{self.base:08X}_{self.name}_{self.digest:016X}"

    @property
    def target(self) -> str:
        return "overlay_" + self.prefix


def describe_overlay(path: Path, data: bytes) -> Overlay:
    """Use exactly the same identity and name normalization as add_overlay.py."""
    match = re.fullmatch(r"overlay_([0-9A-Fa-f]{8})(?:_[^.]+)?\.bin", path.name)
    if match is None:
        raise ValueError("nom de capture invalide")
    if len(data) < 64 or data[:4] != b"MWo3":
        raise ValueError("en-tête MWo3 absent ou tronqué")
    base = int(match[1], 16)
    _image_id, load, code_size, data_size = struct.unpack_from("<4I", data, 4)
    if load != base or base % 4:
        raise ValueError("adresse de chargement incohérente")
    image_size = 64 + code_size + data_size
    if code_size == 0 or code_size % 4 or len(data) < image_size:
        raise ValueError("tailles de code/données invalides ou capture tronquée")
    name = data[32:64].split(b"\0", 1)[0].decode("ascii", "replace")
    name = re.sub(r"\W", "_", os.path.splitext(name)[0])
    if re.fullmatch(r"[A-Za-z0-9_]+", name) is None:
        raise ValueError("nom d'overlay invalide")
    return Overlay(base, name, image_size, code_size, fnv1a64(data[:64 + code_size]))


def corpus_complete(overlay: Overlay) -> bool:
    """Metadata is written last by add_overlay.py; a directory alone proves nothing."""
    corpus = CORPORA / overlay.prefix
    try:
        metadata = dict(line.split("=", 1) for line in
                        (corpus / "meta.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
        if any(int(metadata[key], 0) != expected for key, expected in (
                ("base", overlay.base), ("size", overlay.image_size),
                ("code_size", overlay.code_size), ("hash", overlay.digest))):
            return False
        if metadata.get("name") != overlay.name:
            return False
        required = [corpus / f"{overlay.prefix}_registry.cpp",
                    corpus / f"{overlay.prefix}_units.hpp"]
        units = list(corpus.glob("generated_unit_*.cpp"))
        return bool(units) and all(path.is_file() and path.stat().st_size > 0
                                   for path in [*required, *units])
    except (OSError, ValueError, KeyError):
        return False


def library_path(overlay: Overlay) -> Path:
    suffix = ".dll" if os.name == "nt" else ".dylib" if sys.platform == "darwin" else ".so"
    return BUILD / "bin" / "overlays" / (overlay.prefix + suffix)


def main() -> int:
    try:
        subprocess.run(["cmake", "--build", str(BUILD), "--target", "psp_recomp", "-j2"], check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"ÉCHEC construction de psp_recomp : {error}", flush=True)
        return 1
    if not DUMPS.is_dir():
        print(f"ÉCHEC dossier de captures absent : {DUMPS}", flush=True)
        return 1
    overlays: dict[str, Overlay] = {}
    generated = 0
    failures = 0
    for path in sorted(DUMPS.glob("overlay_*.bin")):
        try:
            data = path.read_bytes()
            # Unrecognized slots can also be dumped by the host for diagnosis.
            if data[:4] != b"MWo3":
                print("ignoré (format inconnu)", path.name, flush=True)
                continue
            overlay = describe_overlay(path, data)
            if overlay.target in overlays:
                continue
            if not corpus_complete(overlay):
                print("génération", path.name, flush=True)
                result = subprocess.run(
                    [sys.executable, str(ADD), "--no-build", str(BUILD), str(path), f"0x{overlay.base:08X}"],
                    capture_output=True, text=True)
                target = re.search(r"^target: (\S+)\s*$", result.stdout, re.MULTILINE)
                if result.returncode != 0 or target is None or target[1] != overlay.target:
                    detail = (result.stderr + "\n" + result.stdout)[-2000:].strip()
                    raise ValueError(f"génération interrompue ou cible inattendue : {detail}")
                if not corpus_complete(overlay):
                    raise ValueError("corpus incomplet après génération")
                generated += 1
            overlays[overlay.target] = overlay
        except (OSError, ValueError) as error:
            failures += 1
            print("ÉCHEC", path.name, str(error), flush=True)
    if overlays:
        # Always ask CMake to finish all matching targets, including cached ones.
        try:
            subprocess.run(["cmake", "--build", str(BUILD), "--target", "Yakumo", *overlays, "-j2"], check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"ÉCHEC compilation des overlays : {error}", flush=True)
            return 1
        for overlay in overlays.values():
            library = library_path(overlay)
            if not library.is_file() or library.stat().st_size == 0:
                failures += 1
                print(f"ÉCHEC bibliothèque absente ou vide : {library}", flush=True)
    print(f"{generated} nouvel(s) overlay(s) généré(s) ; {len(overlays)} cible(s) de bibliothèque vérifiée(s) ; "
          f"{failures} échec(s). Bibliothèques : {BUILD / 'bin' / 'overlays'}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
