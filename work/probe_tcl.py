import ctypes
from pathlib import Path
import sys

library = ctypes.WinDLL(str(Path(sys.base_prefix) / "DLLs/tcl86t.dll"))
library.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
library.Tcl_FindExecutable(sys.executable.encode())
library.Tcl_CreateInterp.restype = ctypes.c_void_p
interp = library.Tcl_CreateInterp()
library.Tcl_Eval.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
library.Tcl_GetStringResult.argtypes = [ctypes.c_void_p]
library.Tcl_GetStringResult.restype = ctypes.c_char_p
path = (Path(__file__).resolve().parents[1] / "deps/tcl/tcl8.6").as_posix()
for command in ("info patchlevel", "array get tcl_platform", "pwd", "file volumes", "glob -nocomplain *",
                "file exists C:/Windows/win.ini", "file exists {deps/tcl/tcl8.6/init.tcl}",
                f"file readable {{{path}/init.tcl}}",
                f"set tcl_library {{{path}}}; source {{{path}/init.tcl}}", "set errorInfo"):
    result = library.Tcl_Eval(interp, command.encode())
    print(result, library.Tcl_GetStringResult(interp).decode(errors="replace"))
