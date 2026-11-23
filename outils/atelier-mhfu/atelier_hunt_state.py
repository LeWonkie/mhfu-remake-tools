"""Verified objectives and deterministic state used by the hunt controller."""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY = Path(__file__).with_name("quest_objectives.json")
# Read from the game's mission descriptors by campaigns (derive_objectives.py);
# used only for quests the hand-verified registry does not list. The offline
# quest-file registry is a final fallback, after actual campaign observations.
DERIVED = Path(__file__).with_name("quest_objectives_derived.json")
DISC = Path(__file__).with_name("quest_objectives_disc.json")


def derived_entry(goal: dict) -> bool:
    return goal.get("source") in ("mission_descriptors", "quest_file") and goal.get("verified") is False


def objectives(path: Path = REGISTRY, derived: Path | None = DERIVED,
               disc: Path | None = DISC) -> dict[int, dict]:
    result = _registry(path, allow_derived=False)
    for fallback in (derived, disc):
        if fallback is not None and fallback.is_file():
            for quest, goal in _registry(fallback, allow_derived=True).items():
                result.setdefault(quest, goal)
    return result


def _registry(path: Path, allow_derived: bool) -> dict[int, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("Unsupported objective registry")
    result = {}
    for goal in data["quests"]:
        trusted = goal.get("verified") is True or (allow_derived and derived_entry(goal))
        quest, required, species = goal["quest"], goal["required"], goal["species"]
        if (type(quest) is not int or quest <= 0 or quest in result
                or type(required) is not int or not 1 <= required <= 1000
                or not isinstance(species, list) or not species
                or any(type(s) is not int or not 1 <= s <= 127 for s in species)
                or not trusted or not goal.get("evidence")
                or goal.get("kind") not in ("slay", "hunt")
                or any(type(s) is not int or not 0 < s < 4096 for s in goal.get("patrol", []))):
            raise ValueError(f"Invalid or unverified objective: {quest}")
        counter = goal.get("progress_counter")
        if counter is not None and (not isinstance(counter, dict)
                or type(counter.get("flags")) is not int
                or not 0 < counter["flags"] <= 0xFFFFFFFF or counter["flags"] & 0x71 != 0x11
                or not counter.get("evidence") or len(species) != 1):
            raise ValueError(f"Invalid objective counter: {quest}")
        result[quest] = goal
    return result


def resolve_objective(quest, species=None, patrol=None):
    registered = objectives().get(quest)
    if species is None:
        if registered is None:
            raise ValueError(f"Objectif de la quête {quest} non vérifié : consulter Quest Info avant l'automatisation.")
        goal = dict(registered)
    else:
        if not species or any(type(s) is not int or not 1 <= s <= 127 for s in species):
            raise ValueError("Invalid explicit species")
        if registered and set(species) == set(registered["species"]):
            goal = dict(registered)
        else:
            goal = {"quest": quest, "name": "Explicit species diagnostic", "kind": "diagnostic",
                    "species": list(species), "required": None, "verified": False,
                    "evidence": "explicit CLI species", "patrol": []}
    if patrol is not None:
        if not patrol or any(type(s) is not int or not 0 < s < 4096 for s in patrol):
            raise ValueError("Invalid patrol")
        goal["patrol"] = list(patrol)
    return goal


def objective_progress(snapshot, goal):
    """Interpret a registered counter only when its runtime descriptors match.

    The game keeps remaining quantities, not kills. Never turn zero into a
    quest result: GameSys mission state remains the only result authority.
    """
    counter = goal.get("progress_counter")
    trusted = goal.get("verified") is True or derived_entry(goal)
    if not counter or not trusted or snapshot.get("quest") != goal["quest"]:
        return None
    current, initial = snapshot["current"], snapshot["initial"]
    if len(current) != 2 or len(initial) != 2:
        return None
    # Multi-objective missions and other flag meanings have not been checked.
    if any(entry["flags"] for entry in (current[1], initial[1])):
        return None
    now, start = current[0], initial[0]
    flags, target, required = counter["flags"], goal["species"][0], goal["required"]
    if (start != {"flags": flags, "target": target, "quantity": required}
            or now["target"] != target or now["flags"] not in (flags, flags | 0x20)
            or not 0 <= now["quantity"] <= required
            or (now["flags"] & 0x20 and now["quantity"] != 0)):
        return None
    return {"source": "game_mission_objective", "species": target, "required": required,
            "remaining": now["quantity"], "completed": required - now["quantity"]}


class KillTracker:
    """Count live-to-dead transitions confirmed by a second read, never absent,
    reused or moved actors. With `stage`, only actors in the hunter's area count:
    slots of an area being unloaded are not deaths."""

    def __init__(self, species):
        self.species = set(species)
        self.previous = {}
        self.pending = {}
        self.count = 0

    def observe(self, monsters, stage=None):
        now, pending, deaths = {}, {}, []
        for monster in monsters:
            if (monster["type"] not in self.species or monster["kind"] != 1
                    or not 0 < monster["stage"] < 4096):
                continue
            identity = (monster["slot"], monster["address"], monster["type"], monster["kind"])
            health = monster["health"]
            local = stage is None or monster["stage"] == stage
            if health <= 0 and local and self.pending.get(identity) == monster["stage"]:
                deaths.append(monster)
            elif health <= 0 and local and self.previous.get(identity, 0) > 0:
                pending[identity] = monster["stage"]
            now[identity] = health
        self.previous, self.pending = now, pending
        self.count += len(deaths)
        return deaths


class ProgressGuard:
    """An empty patrol or navigation cycle cannot reset the progress deadline."""

    def __init__(self, now, seconds=120):
        self.last_progress = now
        self.seconds = seconds
        self.visited = set()
        self.kills = 0
        self.objective_completed = 0

    def observe(self, stage, kills, now, objective_completed=None):
        if (stage not in self.visited or kills > self.kills
                or (objective_completed is not None and objective_completed > self.objective_completed)):
            self.last_progress = now
        self.visited.add(stage)
        self.kills = max(self.kills, kills)
        if objective_completed is not None:
            self.objective_completed = max(self.objective_completed, objective_completed)
        return now - self.last_progress >= self.seconds
