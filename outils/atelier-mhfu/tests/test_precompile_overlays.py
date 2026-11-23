"""Synthetic overlay recovery tests; never compile or use game data."""
import contextlib
import importlib.util
import io
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "precompile_overlays.py"
SPEC = importlib.util.spec_from_file_location("precompile_overlays", MODULE_PATH)
precompile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = precompile
SPEC.loader.exec_module(precompile)


def synthetic_dump(base=0x09000000, name=b"fixture.ovl", code=b"\0" * 16, data=b"\0" * 8):
    header = bytearray(64)
    header[:4] = b"MWo3"
    struct.pack_into("<4I", header, 4, 1, base, len(code), len(data))
    header[32:32 + len(name)] = name
    return bytes(header) + code + data


class PrecompileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.dumps = root / "dumps"
        self.corpora = root / "corpora"
        self.build = root / "build"
        self.dumps.mkdir()
        self.corpora.mkdir()
        for key, value in (("DUMPS", self.dumps), ("CORPORA", self.corpora), ("BUILD", self.build)):
            patcher = mock.patch.object(precompile, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = self.dumps / "overlay_09000000_fixture_ovl.bin"
        self.image = synthetic_dump()
        self.path.write_bytes(self.image)
        self.overlay = precompile.describe_overlay(self.path, self.image)
        self.calls = []

    def write_corpus(self, overlay=None):
        overlay = overlay or self.overlay
        corpus = self.corpora / overlay.prefix
        corpus.mkdir(exist_ok=True)
        (corpus / "meta.txt").write_text(
            f"base={overlay.base}\nsize={overlay.image_size}\ncode_size={overlay.code_size}\n"
            f"hash={overlay.digest}\nname={overlay.name}\n", encoding="utf-8")
        (corpus / (overlay.prefix + "_registry.cpp")).write_text("synthetic registry", encoding="utf-8")
        (corpus / (overlay.prefix + "_units.hpp")).write_text("synthetic declarations", encoding="utf-8")
        (corpus / "generated_unit_0000.cpp").write_text("synthetic source", encoding="utf-8")
        return corpus

    def fake_process(self, args, **kwargs):
        self.calls.append(args)
        if "--no-build" in args:
            self.write_corpus()
            return subprocess.CompletedProcess(args, 0, f"target: {self.overlay.target}\n", "")
        if "Yakumo" in args:
            library = precompile.library_path(self.overlay)
            library.parent.mkdir(parents=True, exist_ok=True)
            library.write_bytes(b"synthetic library marker")
        return subprocess.CompletedProcess(args, 0, "", "")

    def run_main(self, process=None):
        with mock.patch.object(precompile.subprocess, "run", side_effect=process or self.fake_process):
            with contextlib.redirect_stdout(io.StringIO()):
                return precompile.main()

    def test_identity_ignores_mutable_data(self):
        changed = self.image[:-1] + b"x"
        self.assertEqual(precompile.describe_overlay(self.path, changed), self.overlay)
        changed_code = bytearray(self.image)
        changed_code[64] = 1
        self.assertNotEqual(precompile.describe_overlay(self.path, changed_code).digest, self.overlay.digest)

    def test_partial_directory_is_regenerated(self):
        (self.corpora / self.overlay.prefix).mkdir()
        self.assertFalse(precompile.corpus_complete(self.overlay))
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(sum("--no-build" in call for call in self.calls), 1)
        self.assertTrue(precompile.library_path(self.overlay).is_file())

    def test_interrupted_build_is_resumed_without_regeneration(self):
        self.write_corpus()
        self.assertFalse(precompile.library_path(self.overlay).exists())
        self.assertEqual(self.run_main(), 0)
        self.assertFalse(any("--no-build" in call for call in self.calls))
        self.assertTrue(any(self.overlay.target in call for call in self.calls))
        self.assertTrue(precompile.library_path(self.overlay).is_file())
        self.assertTrue(all(call[-1] == "-j2" for call in self.calls))

    def test_metadata_with_missing_or_empty_sources_is_incomplete(self):
        corpus = self.write_corpus()
        (corpus / "generated_unit_0000.cpp").write_bytes(b"")
        self.assertFalse(precompile.corpus_complete(self.overlay))
        self.assertEqual(self.run_main(), 0)
        self.assertTrue(any("--no-build" in call for call in self.calls))

    def test_wrong_metadata_is_regenerated(self):
        corpus = self.write_corpus()
        metadata = corpus / "meta.txt"
        metadata.write_text(metadata.read_text().replace("code_size=16", "code_size=12"))
        self.assertFalse(precompile.corpus_complete(self.overlay))
        self.assertEqual(self.run_main(), 0)
        self.assertTrue(any("--no-build" in call for call in self.calls))

    def test_generation_failure_returns_error_and_can_retry(self):
        def failing(args, **kwargs):
            if "--no-build" in args:
                (self.corpora / self.overlay.prefix).mkdir(exist_ok=True)
                return subprocess.CompletedProcess(args, 1, "", "synthetic interruption")
            return self.fake_process(args, **kwargs)
        self.assertEqual(self.run_main(failing), 1)
        self.assertFalse(precompile.library_path(self.overlay).exists())
        self.assertEqual(self.run_main(), 0)

    def test_generation_success_without_complete_corpus_is_an_error(self):
        def incomplete(args, **kwargs):
            if "--no-build" in args:
                return subprocess.CompletedProcess(args, 0, f"target: {self.overlay.target}\n", "")
            return self.fake_process(args, **kwargs)
        self.assertEqual(self.run_main(incomplete), 1)

    def test_build_failure_returns_error(self):
        self.write_corpus()
        def failing(args, **kwargs):
            if "Yakumo" in args:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_process(args, **kwargs)
        self.assertEqual(self.run_main(failing), 1)
        self.assertFalse(precompile.library_path(self.overlay).exists())

    def test_build_success_without_library_is_an_error(self):
        self.write_corpus()
        def missing_library(args, **kwargs):
            if "Yakumo" in args:
                return subprocess.CompletedProcess(args, 0, "", "")
            return self.fake_process(args, **kwargs)
        self.assertEqual(self.run_main(missing_library), 1)

    def test_invalid_capture_reports_error(self):
        self.path.write_bytes(self.image[:70])
        self.assertEqual(self.run_main(), 1)
        self.assertFalse(any("--no-build" in call for call in self.calls))

    def test_mismatched_load_address_reports_error(self):
        self.path.write_bytes(synthetic_dump(base=0x09100000))
        self.assertEqual(self.run_main(), 1)

    def test_unknown_slot_format_is_skipped(self):
        self.path.write_bytes(b"not a recognized overlay")
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(len(self.calls), 1)

    def test_missing_dump_directory_reports_error(self):
        self.path.unlink()
        self.dumps.rmdir()
        self.assertEqual(self.run_main(), 1)

    def test_recompiler_build_failure_reports_error(self):
        def failing(args, **kwargs):
            raise subprocess.CalledProcessError(1, args)
        self.assertEqual(self.run_main(failing), 1)


if __name__ == "__main__":
    unittest.main()
