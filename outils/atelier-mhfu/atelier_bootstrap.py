"""Locate local Tcl/Tk resources without depending on the launch directory."""
import os
from pathlib import Path
import sys


def configure_tk(project: Path, environment=None, cwd=None, base_prefix=None, version="8.6"):
    environment = os.environ if environment is None else environment
    cwd = Path.cwd() if cwd is None else Path(cwd)
    roots = [Path(project) / "deps/tcl", Path(base_prefix or sys.base_prefix) / "tcl"]
    for root in roots:
        tcl, tk = root / ("tcl" + version), root / ("tk" + version)
        if (tcl / "init.tcl").is_file() and (tk / "tk.tcl").is_file():
            # Relative paths also avoid a Tcl 8.6 absolute-path normalization
            # failure observed with this Windows runtime in the sandbox.
            try:
                paths = [os.path.relpath(path, cwd).replace("\\", "/") for path in (tcl, tk)]
            except ValueError:  # The launcher and resources are on different drives.
                paths = [path.as_posix() for path in (tcl, tk)]
            environment.update(TCL_LIBRARY=paths[0], TK_LIBRARY=paths[1])
            return True
    return False
