"""Force a timeout in a fresh trial and exercise the real hunt end controller.

Only the quest clock is changed, through the existing developer command.
The mission byte is observed, never written. No movement or attack should
be reached before the timeout. Inspect the captures before claiming visual
confirmation. The session substitution is confined to this diagnostic.
"""
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

import atelier_campaign
import atelier_core
import atelier_zones
import hunt


class TimeoutSession(atelier_campaign.Session):
    def ask(self, line, expect, timeout=10.0):
        answer = super().ask(line, expect, timeout)
        if line == "hold onehit on":
            before = atelier_zones.mission_state(self)
            timed = super().ask("quest time 2", "quest time", 6.0)
            atelier_core._write_json(self.dir / "failure_probe.json", {
                "kind": "forced_timeout", "quest_clock_seconds": 2,
                "mission_state_before": before, "clock_response": timed,
                "visual_review": "not_performed",
            })
            if before != 2 or not timed or "set to" not in timed:
                raise RuntimeError(f"Timeout precondition not confirmed: {before}, {timed}")
        return answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quest", type=int, choices=(11001, 21001), nargs="?", default=11001)
    parser.add_argument("--seed", default="20261020-153034-ceda7b")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                      GIT_CONFIG_VALUE_0=str(root / "psprecomp-mhfu"))
    workshop = atelier_core.Workshop(root)
    with patch.object(atelier_campaign, "Session", TimeoutSession):
        result = hunt.hunt(workshop, args.seed, args.quest, 1609459200123456, 30)
    print("FAILURE_PROBE", json.dumps({key: result[key] for key in
          ("run_id", "state", "mission_states", "success", "returned_to_village")}), flush=True)
    return 0 if (result["state"] == "quest_failed" and result["success"] is False
                 and result["returned_to_village"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
