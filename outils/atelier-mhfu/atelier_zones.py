"""Area changes on an MHFU quest through the game's own exits.

The exits of the current area come from the stage's definitions (the
StageBase vtable's definitions() entry, a lui/addiu/jr function in the area
overlay); see the MHP2G decompilation (CC0): StageManager, StageBase,
stage_definitions and stage_exit. Going through an exit places the hunter
(ObjBase position +0x200 and next position +0x210) inside the exit volume;
the game's own intersecting_exit test then changes the area. Every address
used here was read back in the running game before this was written (see
docs in the atelier README). Only the trial's game memory is written.
"""
from __future__ import annotations

import re
import math
import struct
import time

GAMESYS_PTR = 0x089C7508
STAGE_MANAGER_PTR = 0x09A4ADA0
PLAYER_MANAGER_PTR = 0x09A4AE04
STAGE_ID = 0x6AF0E           # GameSys, u16
LOCAL_PLAYER = 0x28          # GameSys, u8 index into the player list
ENEMY_MANAGER_PTR = 0x09C0D430
ENEMIES, ENEMY_SLOTS = 0x1220, 20
HEALTH = 0x2E4               # ObjBase, s16
OBJECT_STAGE = 0x29A         # ObjBase, u16 area the object is in
OBJECT_TYPE = 0x1E8          # ObjBase, u8 species (pl_type; 6 = Yian Kut-Ku, as its quest em_id) then u8 kind
QUEST_PTR = 0x08A5DD4C
QUEST_TARGETS, QUEST_TARGET_SIZE = 0x768, 0x2C  # QuestTarget[2]: emId +0x14, count +0x1E
# Mission descriptors, initialized at 0x0886E2E4; original copies follow.
# Each is flags u32, target u16, quantity s16. The decrement at 0x0886E624
# applies to slay descriptors (flags & 0x11 == 0x11), not the boss roster.
MISSION_OBJECTIVES = 0x728
ACCEPTED_QUEST_ID = 0x6AF12
# GameSys u8 mission state, the byte GameSys::Game_clear_ck (0x0884F9A0) tests:
# 3..8 once the quest is over. The mission check (0x0886E970) writes 3 with
# the quest's clear event (-1), 0x0886E924 writes 5 with its failure event (-2).
MISSION_STATE = 0x2E
MISSION_CLEAR, MISSION_FAILED = 3, 5
PLAYER_LIST = 0xC            # PlayerManager, six pointers
POSITION, NEXT_POSITION = 0x200, 0x210
DEFINITIONS_SLOT = 0x28      # StageBase vtable, after two header words
EXITS_SLOT, EXIT_COUNT_SLOT = 0x2C, 0x30
MAP_ID = 0x284               # StageManager, u32 (read 11 on map of area 98)
MANAGER_STAGE = 0x288        # StageManager, u16 loaded area
# StageBase's own exits()/exit_count(), read in area 98's vtable; an area
# whose slots hold these goes through definitions().
BASE_EXITS, BASE_EXIT_COUNT = 0x088CD47C, 0x088CD4AC
EXITS, EXIT_COUNT = 0x30, 0x3D
EXIT_SIZE = 0x34
PEEK = re.compile(r"(?:0x)?[0-9A-Fa-f]{8}:((?: [0-9A-F]{2})+)")
PEEK_ROW = re.compile(r"(?:0x)?([0-9A-Fa-f]{8}):((?: [0-9A-F]{2})+)")


class Unavailable(RuntimeError):
    pass


def read_many(session, requests, timeout: float = 6.0) -> list[bytes]:
    """Read independent ranges in one console frame, with complete row checks."""
    requests = list(requests)
    commands, wanted, rows = [], {}, {}
    for address, length in requests:
        if not isinstance(address, int) or not isinstance(length, int) or not (
                0 <= address <= 0xFFFFFFFF and 0 <= length <= 1024 * 1024
                and address + length <= 0x100000000):
            raise ValueError("invalid memory range")
        for offset in range(0, length, 256):
            start, size = address + offset, min(256, length - offset)
            command = f"peek 0x{start:08X} {size}"
            if command not in commands:
                commands.append(command)
            for row in range(0, size, 16):
                at = start + row
                wanted[at] = max(wanted.get(at, 0), min(16, size - row))
    if not commands:
        return [b"" for _ in requests]
    began = time.monotonic()
    session.new_lines()
    session.w.debug_commands(commands)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and session.alive() and len(rows) < len(wanted):
        for line in session.new_lines():
            match = PEEK_ROW.search(line)
            if match and not line.startswith("[debug] > "):
                at = int(match[1], 16)
                block = bytes.fromhex(match[2])
                if at in wanted and len(block) >= wanted[at]:
                    rows[at] = block
        if len(rows) < len(wanted):
            time.sleep(0.01)
    elapsed = time.monotonic() - began
    stats = getattr(session, "memory_stats", None)
    if not isinstance(stats, dict):
        stats = session.memory_stats = {"batches": 0, "commands": 0, "seconds": 0.0}
    stats["batches"] += 1
    stats["commands"] += len(commands)
    stats["seconds"] = round(stats["seconds"] + elapsed, 4)
    if len(rows) != len(wanted):
        missing = [f"0x{at:08X}" for at in wanted if at not in rows]
        raise Unavailable("memory batch incomplete: " + ", ".join(missing[:4]))
    return [b"".join(rows[address + offset][:min(16, length - offset)]
                     for offset in range(0, length, 16)) for address, length in requests]


def peek_rows(session, address: int, length: int, timeout: float = 6.0) -> bytes:
    return read_many(session, [(address, length)], timeout)[0]


def read(session, address: int, length: int) -> bytes:
    return read_many(session, [(address, length)])[0]


def u32(session, address: int) -> int:
    return struct.unpack("<I", read(session, address, 4))[0]


def decode_return_address(words: list[int]) -> int | None:
    """The constant a small `return &table;` function loads into v0."""
    regs = {}
    for word in words:
        op, rs, rt, imm = word >> 26, (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if op == 0x0F:                      # lui
            regs[rt] = imm << 16
        elif op == 0x09:                    # addiu
            regs[rt] = (regs.get(rs, 0) + simm) & 0xFFFFFFFF
        elif op == 0x0D:                    # ori
            regs[rt] = regs.get(rs, 0) | imm
    return regs.get(2) or None


def evaluate_return(words: list[int], a1: int = 0, steps: int = 64) -> int | None:
    """What a small leaf function returns in v0: addiu/lui/ori/addu/or and
    beq/bne (plus their likely forms) up to `jr ra`, with a1 given. None for
    anything else, so an unknown function is never guessed at."""
    regs = {0: 0, 5: a1 & 0xFFFFFFFF}
    index, pending = 0, None   # pending: branch target after the delay slot

    def run(word: int) -> bool | str | None:
        op, rs, rt, imm = word >> 26, (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if word == 0:
            pass                            # nop
        elif op == 0x09:
            regs[rt] = (regs.get(rs, 0) + simm) & 0xFFFFFFFF
        elif op == 0x0F:
            regs[rt] = imm << 16
        elif op == 0x0D:
            regs[rt] = regs.get(rs, 0) | imm
        elif op == 0x00 and word & 0x3F == 0x21:
            regs[(word >> 11) & 31] = (regs.get(rs, 0) + regs.get(rt, 0)) & 0xFFFFFFFF
        elif op == 0x00 and word & 0x3F == 0x25:
            regs[(word >> 11) & 31] = regs.get(rs, 0) | regs.get(rt, 0)
        elif op == 0x00 and word & 0x3F == 0x08 and rs == 31:
            return "return"
        elif op in (0x04, 0x05, 0x14, 0x15):
            equal = regs.get(rs, 0) == regs.get(rt, 0)
            taken = equal if op in (0x04, 0x14) else not equal
            return ("likely-" if op in (0x14, 0x15) else "") + ("taken" if taken else "not")
        else:
            return None
        regs[0] = 0
        return True

    for _ in range(steps):
        if index >= len(words):
            return None
        word = words[index]
        outcome = run(word)
        if outcome is None:
            return None
        if outcome is True:
            index += 1
            continue
        if outcome == "return":
            if index + 1 >= len(words) or run(words[index + 1]) is not True:
                return None
            return regs.get(2)
        simm = word & 0xFFFF
        simm = simm - 0x10000 if simm & 0x8000 else simm
        if outcome == "likely-not":
            index += 2                      # the delay slot is annulled
            continue
        if index + 1 >= len(words) or run(words[index + 1]) is not True:
            return None
        index = index + 1 + simm if outcome.endswith("taken") else index + 2
    return None


def parse_exit(data: bytes) -> dict:
    destination, shape = struct.unpack_from("<Hh", data, 0)
    p = struct.unpack_from("<3f", data, 4)
    size, height = struct.unpack_from("<2f", data, 0x10)
    q = struct.unpack_from("<3f", data, 0x18)
    arrival = struct.unpack_from("<3f", data, 0x24)
    return {"destination": destination, "shape": shape, "p": p, "size": size, "height": height,
            "q": q, "arrival": arrival}


def exit_target(exit: dict) -> tuple[float, float, float]:
    """A point inside the exit volume: its centre, a quarter up its height."""
    if exit["shape"] == 1:
        x = (exit["p"][0] + exit["q"][0]) / 2
        z = (exit["p"][2] + exit["q"][2]) / 2
    else:
        x, z = exit["p"][0], exit["p"][2]
    return x, exit["p"][1] + exit["height"] / 4, z


def exit_targets(exit: dict) -> list[tuple[float, float, float]]:
    """Points inside the exit volume to try in turn: centre at several heights,
    then (for a box) points along its length."""
    x, _, z = exit_target(exit)
    base, height = exit["p"][1], exit["height"]
    points = [(x, base + height * f, z) for f in (0.25, 0.02, 0.5)]
    if exit["shape"] == 1:
        for t in (0.25, 0.75):
            px = exit["p"][0] + (exit["q"][0] - exit["p"][0]) * t
            pz = exit["p"][2] + (exit["q"][2] - exit["p"][2]) * t
            points.append((px, base + height * 0.25, pz))
    return points


def inside_exit(point, exit: dict, margin: float = 150.0) -> bool:
    """Whether a point is in (or within `margin` of) an exit volume: a
    cylinder of radius `size` around p (shape 0), or around the segment p-q
    (shape 1), from p's height up `height`. Placing the hunter there changes
    the area (run 20261021-072744-4251e5: a monster by the 71-72 exit sent
    each placement back to 72)."""
    x, y, z = point
    px, py, pz = exit["p"]
    if not py - margin <= y <= py + exit["height"] + margin:
        return False
    if exit["shape"] == 1:
        qx, qz = exit["q"][0], exit["q"][2]
        dx, dz = qx - px, qz - pz
        length = dx * dx + dz * dz
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - px) * dx + (z - pz) * dz) / length))
        px, pz = px + dx * t, pz + dz * t
    return math.hypot(x - px, z - pz) <= exit["size"] + margin


def usable(exit: dict) -> bool:
    """Area ids are small; a destination with high bits set is a flag, not an area."""
    return (0 < exit["destination"] < 0x1000 and exit["shape"] in (0, 1)
            and exit["size"] > 0 and exit["height"] > 0
            and all(math.isfinite(value) for key in ("p", "q", "arrival") for value in exit[key])
            and math.isfinite(exit["size"]) and math.isfinite(exit["height"]))


def traverse(session, here: dict, exit: dict, wait: float = 10.0, settle: float = 5.0,
             walk_in_frames: int = 240, on_attempt=None) -> dict:
    """Try each point until the destination remains loaded after settling."""
    tried = []
    transient_attempts = 0
    attempts = []
    for point in exit_targets(exit):
        began = time.monotonic()
        walk = walk_in_frames if transient_attempts else 0
        place(session, here["player"], point)
        tried.append([round(v, 1) for v in point])
        arrived = wait_stage(session, exit["destination"], wait, walk_in_frames=walk)
        if arrived:
            time.sleep(settle)
        observed = current(session)["stage"]
        attempt = {"point": tried[-1], "arrived": bool(arrived), "observed_stage": observed,
                   "walk_in_frames": walk, "seconds": round(time.monotonic() - began, 3)}
        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)
        if arrived and observed == exit["destination"]:
            return {"reached": True, "tried": tried, "transient_attempts": transient_attempts,
                    "attempts": attempts}
        if observed != here["stage"]:
            # Never apply another point from the old area's geometry in the
            # new area if the game took a different exit.
            return {"reached": False, "tried": tried, "observed_stage": observed,
                    "attempts": attempts, "cause": "unexpected_area"}
        if arrived:
            transient_attempts += 1
    return {"reached": False, "tried": tried, "observed_stage": observed,
            "transient_attempts": transient_attempts, "attempts": attempts,
            "cause": "returned_to_source" if transient_attempts else "never_arrived"}


def current(session, timeout: float = 15.0) -> dict:
    """Read a coherent loaded area; discard snapshots crossing a transition."""
    deadline = time.monotonic() + timeout
    error = Unavailable("no coherent loaded area")
    while session.alive() and time.monotonic() < deadline:
        try:
            return _current(session)
        except Unavailable as pending:
            error = pending
            time.sleep(0.2)
    raise error


def _current(session) -> dict:
    def word(data):
        return struct.unpack("<I", data)[0]

    def valid(pointer):
        return 0x08800000 <= pointer < 0x0A000000

    gamesys, manager, players = map(word, read_many(session, [
        (GAMESYS_PTR, 4), (STAGE_MANAGER_PTR, 4), (PLAYER_MANAGER_PTR, 4)]))
    if not all(map(valid, (gamesys, manager, players))):
        raise Unavailable("area managers not loaded")
    stage_data, object_data, map_data, index_data = read_many(session, [
        (gamesys + STAGE_ID, 2), (manager, 4), (manager + MAP_ID, 8), (gamesys + LOCAL_PLAYER, 1)])
    stage = struct.unpack("<H", stage_data)[0]
    map_id, manager_stage = struct.unpack_from("<IH", map_data)
    if manager_stage != stage:
        raise Unavailable(f"area loading: GameSys {stage}, manager {manager_stage}")
    stage_object = word(object_data)
    index = index_data[0]
    if not valid(stage_object) or index >= 6:
        raise Unavailable("area object not loaded")
    vtable_data, player_data = read_many(session, [(stage_object, 4), (players + PLAYER_LIST + 4 * index, 4)])
    vtable, player = word(vtable_data), word(player_data)
    if not valid(vtable) or not valid(player):
        raise Unavailable("area vtable or player not loaded")
    slots_data, player_block = read_many(session, [
        (vtable + DEFINITIONS_SLOT, 12), (player + POSITION, OBJECT_STAGE + 2 - POSITION)])
    function, exits_fn, count_fn = struct.unpack("<3I", slots_data)
    if not all(map(valid, (function, exits_fn, count_fn))):
        raise Unavailable("area functions not loaded")
    words = list(struct.unpack("<4I", read(session, function, 16)))
    definitions = decode_return_address(words)
    exits = []
    table = count = 0
    source = None
    if definitions:
        if not valid(definitions):
            raise Unavailable("invalid area definitions")
        table_data = read(session, definitions + EXITS, EXIT_COUNT - EXITS + 1)
        table = struct.unpack_from("<I", table_data)[0]
        count = table_data[EXIT_COUNT - EXITS]
        source = "definitions"
    if not (count and table):
        # An area may override exits()/exit_count() instead: small functions
        # returning a constant table and a constant count.
        # Both take the map id (StageManager +0x284) and may pick a table by it.
        if exits_fn != BASE_EXITS and count_fn != BASE_EXIT_COUNT:
            exits_code, count_code = read_many(session, [(exits_fn, 128), (count_fn, 128)])
            table = evaluate_return(list(struct.unpack("<32I", exits_code)), map_id) or 0
            count = (evaluate_return(list(struct.unpack("<32I", count_code)), map_id) or 0) & 0xFF
            source = f"exits(map {map_id})"
    if count and (not valid(table) or table + EXIT_SIZE * count > 0x0A000000):
        raise Unavailable("invalid area exit table")
    ranges = [(gamesys + STAGE_ID, 2), (manager + MANAGER_STAGE, 2),
              (stage_object, 4), (manager, 4), (player + OBJECT_STAGE, 2)]
    if count:
        ranges.append((table, EXIT_SIZE * count))
    final = read_many(session, ranges)
    final_stage, final_manager_stage = (struct.unpack("<H", block)[0] for block in final[:2])
    player_stage = struct.unpack("<H", final[4])[0]
    if (player_stage != stage or final_stage != stage or final_manager_stage != stage
            or word(final[2]) != vtable or word(final[3]) != stage_object
            or struct.unpack_from("<H", player_block, OBJECT_STAGE - POSITION)[0] != stage):
        raise Unavailable("area changed while reading its exits")
    if count:
        raw = final[5]
        exits = [parse_exit(raw[i * EXIT_SIZE:(i + 1) * EXIT_SIZE]) for i in range(count)]
    position = struct.unpack_from("<3f", player_block)
    if not all(map(math.isfinite, position)):
        raise Unavailable("invalid player position")
    return {"stage": stage, "exits": exits, "player": player, "position": position,
            "manager_stage": manager_stage, "player_stage": player_stage,
            "definitions": definitions, "exit_source": source,
            "vtable_slots": {"definitions": function, "exits": exits_fn, "exit_count": count_fn}}


def monsters(session) -> list[dict]:
    """The quest's monster table, read only."""
    manager = u32(session, ENEMY_MANAGER_PTR)
    if not 0x08800000 <= manager < 0x0A000000:
        return []
    slots = struct.unpack(f"<{ENEMY_SLOTS}I", read(session, manager + ENEMIES, 4 * ENEMY_SLOTS))
    found = []
    candidates = [(slot, enemy) for slot, enemy in enumerate(slots) if 0x08800000 <= enemy < 0x0A000000]
    if not candidates:
        return found
    blocks = read_many(session, [(enemy + OBJECT_TYPE, HEALTH + 2 - OBJECT_TYPE) for _, enemy in candidates]
                       + [(manager + ENEMIES, 4 * ENEMY_SLOTS), (ENEMY_MANAGER_PTR, 4)])
    final_slots = struct.unpack(f"<{ENEMY_SLOTS}I", blocks[-2])
    if struct.unpack("<I", blocks[-1])[0] != manager:
        raise Unavailable("enemy manager changed while reading")
    for (slot, enemy), block in zip(candidates, blocks):
        if final_slots[slot] != enemy:
            continue
        # Kind, position, area and health in one read: +0x1E8 through +0x2E6.
        at = lambda field: field - OBJECT_TYPE  # noqa: E731
        position = struct.unpack_from("<3f", block, at(POSITION))
        stage = struct.unpack_from("<H", block, at(OBJECT_STAGE))[0]
        health = struct.unpack_from("<h", block, at(HEALTH))[0]
        # Slots can be freed/reused while reading. A freed object is not a
        # killed monster; ignore it rather than counting arbitrary bytes.
        if block[1] != 1 or not 0 < stage < 0x1000 or not all(map(math.isfinite, position)):
            continue
        found.append({"slot": slot, "address": enemy, "health": health, "stage": stage, "position": position,
                      "type": block[0], "kind": block[1]})
    return found


def quest_targets(session) -> list[dict]:
    """The quest's two boss roster entries, not the mission's slay objective.
    On 11001 the species 68 count stays 1 after its death; the in-game goal
    is 20 Bullfangos (species 5). Do not interpret this count as kills left.
    """
    quest = u32(session, QUEST_PTR)
    if not 0x08800000 <= quest < 0x0A000000:
        return []
    block = read(session, quest + QUEST_TARGETS, 2 * QUEST_TARGET_SIZE)
    return [{"em_id": struct.unpack_from("<b", block, i * QUEST_TARGET_SIZE + 0x14)[0],
             "count": struct.unpack_from("<b", block, i * QUEST_TARGET_SIZE + 0x1E)[0]} for i in range(2)]


def mission_state(session) -> int | None:
    """GameSys's mission state, read only; None while GameSys is not loaded."""
    gamesys = u32(session, GAMESYS_PTR)
    if not 0x08800000 <= gamesys < 0x0A000000:
        return None
    state, again = read_many(session, [(gamesys + MISSION_STATE, 1), (GAMESYS_PTR, 4)])
    if struct.unpack("<I", again)[0] != gamesys:
        raise Unavailable("GameSys changed while reading the mission state")
    return state[0]


def mission_objectives(session, quest_id: int) -> dict:
    """Read current and original mission descriptors with identity checks.

    Quantities are raw here: interpretation requires a separately verified
    objective. A pointer or accepted-quest change discards the snapshot.
    """
    quest_raw, gs_raw = read_many(session, [(QUEST_PTR, 4), (GAMESYS_PTR, 4)])
    quest, gamesys = struct.unpack("<I", quest_raw)[0], struct.unpack("<I", gs_raw)[0]
    if (not all(0x08800000 <= p < 0x0A000000 for p in (quest, gamesys))
            or quest + MISSION_OBJECTIVES + 32 > 0x0A000000
            or gamesys + ACCEPTED_QUEST_ID + 2 > 0x0A000000):
        raise Unavailable("mission objects not loaded")
    accepted = read(session, gamesys + ACCEPTED_QUEST_ID, 2)
    if struct.unpack("<H", accepted)[0] != quest_id:
        raise Unavailable("accepted quest does not match objective")
    raw, final_quest, final_gs, final_id = read_many(session, [
        (quest + MISSION_OBJECTIVES, 32), (QUEST_PTR, 4), (GAMESYS_PTR, 4),
        (gamesys + ACCEPTED_QUEST_ID, 2)])
    if final_quest != quest_raw or final_gs != gs_raw or final_id != accepted:
        raise Unavailable("quest changed while reading mission objectives")
    entries = [dict(zip(("flags", "target", "quantity"), struct.unpack_from("<IHh", raw, i * 8)))
               for i in range(4)]
    return {"quest": quest_id, "current": entries[:2], "initial": entries[2:]}


def mission_over(state: int | None) -> bool:
    return state is not None and 3 <= state <= 8


def place(session, player: int, position) -> None:
    position = tuple(position)
    if not 0x08800000 <= player < 0x0A000000 or len(position) != 3 or not all(map(math.isfinite, position)):
        raise ValueError("invalid player placement")
    # Six acknowledged commands can straddle an area load and overwrite the
    # game's arrival position. The console consumes a complete batch before
    # returning to the guest, so both vectors change in the same frame.
    commands = []
    for base in (POSITION, NEXT_POSITION):
        for offset, value in zip((0, 4, 8), position):
            bits = struct.unpack("<I", struct.pack("<f", value))[0]
            commands.append(f"poke32 0x{player + base + offset:08X} {bits}")
    session.w.debug_commands(commands)


def go_through(session, here: dict, exit: dict) -> None:
    place(session, here["player"], exit_target(exit))


def wait_stage(session, wanted: int, timeout: float = 30.0, walk_in_frames: int = 0) -> int | None:
    if not isinstance(walk_in_frames, int) or not 0 <= walk_in_frames <= 600:
        raise ValueError("walking must release within 600 input frames")
    deadline = time.monotonic() + timeout
    walking = False
    while time.monotonic() < deadline and session.alive():
        state = session.state()
        if state and state[0] == 11 and state[2] == wanted:
            if walk_in_frames and not walking:
                session.inputs(["0:axis leftx 0", "0:axis lefty -1", f"{walk_in_frames}:axis lefty 0"])
                walking = True
            try:
                here = current(session, timeout=max(0.1, min(6.0, deadline - time.monotonic())))
                if here["stage"] == wanted:
                    return wanted
            except Unavailable:
                pass
        time.sleep(0.1)
    return None
