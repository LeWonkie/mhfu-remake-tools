"""Exercise real Tk buttons and real trials; no mouse input is simulated."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "outils" / "atelier-mhfu"))
os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                  GIT_CONFIG_VALUE_0=str(PROJECT / "psprecomp-mhfu"))
import atelier_ui


def main():
    root = atelier_ui.tk.Tk()
    backend = atelier_ui.Workshop(PROJECT)
    ui = atelier_ui.WorkshopUI(root, backend)
    ui.notebook.select(ui.campaign_tab)
    ui.hunt_seconds.set("120")
    out = PROJECT / "work" / ("atelier-ui-stop-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir()
    report = {"method": "Tk Button.invoke; real UI event loop and backend", "checks": []}

    def pump_until(predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return
            time.sleep(0.03)
        raise TimeoutError(f"UI probe timed out after {timeout} s")

    def capture(name):
        root.update_idletasks()
        atelier_ui.window_bitmap(root, out / f"{name}.bmp")

    def check(name, condition, **details):
        row = {"check": name, "passed": bool(condition), **details}
        report["checks"].append(row)
        print(json.dumps(row), flush=True)
        (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        pump_until(lambda: str(ui.hunt_button["state"]) == "normal", 20)
        for phase in ("boot", "quest"):
            pump_until(lambda: str(ui.hunt_button["state"]) == "normal", 15)
            ui.hunt_button.invoke()
            pump_until(lambda: backend.status()["active"] is not None, 20)
            run = backend.status()["active"]
            if phase == "quest":
                pump_until(lambda: any(text in ui.hunt_status.get() for text in
                           ("Combat", "Déplacement")), 140)
            check(phase + "_stop_enabled", str(ui.automation_stop["state"]) == "normal", run_id=run["id"])
            capture(phase + "-before-stop")
            began = time.monotonic()
            ui.automation_stop.invoke()
            pump_until(lambda: not ui.busy and backend.status()["active"] is None, 25)
            elapsed = round(time.monotonic() - began, 3)
            result = json.loads((Path(run["run_dir"]) / "hunt.json").read_text(encoding="utf-8"))
            check(phase + "_cancelled", result["state"] == "cancelled", state=result["state"],
                  stop_seconds=elapsed, label=ui.hunt_status.get(), run_id=run["id"])
            pump_until(lambda: str(ui.hunt_button["state"]) == "normal"
                       and str(ui.automation_stop["state"]) == "disabled", 15)
            check(phase + "_ready_again", str(ui.hunt_button["state"]) == "normal"
                  and str(ui.automation_stop["state"]) == "disabled")
            capture(phase + "-after-stop")

        # Hold only the launch boundary: prove Stop remains available when
        # automation is active but there is no current game process.
        gate = threading.Event()
        original_hunt = atelier_ui.hunt.hunt
        def gated_hunt(*args, **kwargs):
            if not gate.wait(15):
                raise TimeoutError("launch gate expired")
            return original_hunt(*args, **kwargs)
        before = set((backend.data_dir / "runs").iterdir())
        with patch.object(atelier_ui.hunt, "hunt", gated_hunt):
            ui.hunt_button.invoke()
            root.update()
            check("between_sessions_stop_enabled", ui.automation_active and backend.status()["active"] is None
                  and str(ui.automation_stop["state"]) == "normal")
            capture("between-sessions-before-stop")
            ui.automation_stop.invoke()
            gate.set()
            pump_until(lambda: not ui.busy, 15)
        after = set((backend.data_dir / "runs").iterdir())
        check("between_sessions_no_launch", before == after and ui.automation_cancel.is_set()
              and not ui.automation_active, label=ui.hunt_status.get())
        capture("between-sessions-after-stop")
    finally:
        ui.automation_cancel.set()
        backend.stop()
        ui.closed.set()
        root.destroy()
    report["passed"] = all(row["passed"] for row in report["checks"])
    (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("UI_RESULT", out, report["passed"], flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
