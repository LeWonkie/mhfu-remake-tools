"""Verify a real UI receives and displays changing game objective counts."""
import json
import os
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "outils/atelier-mhfu"))
os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                  GIT_CONFIG_VALUE_0=str(PROJECT / "psprecomp-mhfu"))
import atelier_ui


def main():
    root = atelier_ui.tk.Tk()
    backend = atelier_ui.Workshop(PROJECT)
    ui = atelier_ui.WorkshopUI(root, backend)
    ui.notebook.select(ui.campaign_tab)
    ui.hunt_seconds.set("240")
    out = PROJECT / "work" / ("atelier-counter-ui-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir()

    def pump(predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return
            time.sleep(0.04)
        raise TimeoutError("counter integration deadline")

    try:
        pump(lambda: str(ui.hunt_button["state"]) == "normal", 20)
        ui.hunt_button.invoke()
        pump(lambda: backend.status()["active"] is not None, 20)
        run = backend.status()["active"]
        path = Path(run["run_dir"]) / "hunt.json"
        def progressed():
            if not path.is_file():
                return False
            result = json.loads(path.read_text(encoding="utf-8"))
            value = result.get("objective_progress")
            return (value and value["completed"] >= 2 and result.get("objective_progress_available")
                    and f"objectif du jeu : {value['completed']}/20" in ui.hunt_status.get())
        pump(progressed, 220)
        before = json.loads(path.read_text(encoding="utf-8"))
        text = ui.hunt_status.get()
        captured = atelier_ui.window_bitmap(root, out / "counter-live.bmp")
        ui.automation_stop.invoke()
        pump(lambda: not ui.busy and backend.status()["active"] is None, 25)
        result = json.loads(path.read_text(encoding="utf-8"))
        summary = {"run_id": run["id"], "ui_label": text, "screenshot": captured,
                   "objective_progress": before["objective_progress"], "state": result["state"],
                   "success": result["success"], "method": "real Tk buttons via invoke; real game",
                   "passed": result["state"] == "cancelled" and result["success"] is None}
        (out / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("COUNTER_UI", out, json.dumps(summary, ensure_ascii=True), flush=True)
        return 0 if summary["passed"] else 1
    finally:
        ui.automation_cancel.set()
        backend.stop()
        ui.closed.set()
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
