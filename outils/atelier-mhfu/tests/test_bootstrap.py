from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atelier_bootstrap import configure_tk


class BootstrapTests(unittest.TestCase):
    def test_resources_are_resolved_relative_to_another_launch_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("deps/tcl/tcl8.6/init.tcl", "deps/tcl/tk8.6/tk.tcl"):
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_text("synthetic resource", encoding="utf-8")
            environment = {"TCL_LIBRARY": "missing"}
            self.assertTrue(configure_tk(root, environment, cwd=root / "work", base_prefix=root / "missing"))
            self.assertEqual(environment["TCL_LIBRARY"], "../deps/tcl/tcl8.6")
            self.assertEqual(environment["TK_LIBRARY"], "../deps/tcl/tk8.6")

    def test_missing_resources_do_not_overwrite_user_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"TCL_LIBRARY": "user-library"}
            self.assertFalse(configure_tk(root, environment, cwd=root, base_prefix=root / "missing"))
            self.assertEqual(environment, {"TCL_LIBRARY": "user-library"})
