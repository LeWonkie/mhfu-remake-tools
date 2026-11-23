"""Bounded check of the new debug commands on a disposable game copy (never game-mhfu).

Boots to the village, reads `status`, the equipment names and what is worn,
leaves for quest 1020 (Yian Kut-Ku), then reads `status`, `quest info`,
`monsters`, sets a monster's health, holds the monsters still for a few
seconds and lets them go. Prints one JSON summary.
"""
import json
from pathlib import Path
import queue
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_campaign as campaign
import outil_debug


def main(game_copy: Path) -> int:
    assert game_copy.resolve() != (ROOT / 'game-mhfu').resolve()
    tool = outil_debug.Tool(ROOT, game_copy)
    tool.root.update()
    tool.root.destroy()
    result = {'window': 'built', 'catalog': len(outil_debug.load_catalog())}
    events = queue.Queue()
    game = outil_debug.Game(ROOT, events, game_copy)
    answers = {}

    def ask(line, timeout=20.0):
        answers.pop(line, None)
        game.send(line, lambda lines, key=line: answers.__setitem__(key, lines))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and game.running():
            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                continue
            if event[0] == 'answer' and event[3]:
                event[3](event[2])
            if line in answers:
                return answers[line]
        return None

    def status():
        return outil_debug.parse_status((ask('status', 4) or [''])[0])

    folder = game.start({'MHP3RD_INPUT_SCRIPT': campaign.BOOT, 'MHP3RD_DATA_DIR': str((game_copy / '../data').resolve())})
    result['folder'] = str(folder)
    try:
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            values = status()
            if values.get('task') == '10' and values.get('area') == '139':
                break
        result['village_status'] = (ask('status', 4) or [None])[0]
        time.sleep(6)
        for kind in (5, 6, 1, 2, 3, 4, 0):
            names = ask(f'equip list {kind}') or []
            result[f'equip_list_{kind}'] = [len(names), names[:1], names[-1:]]
        result['blade_944'] = [n for n in (ask('equip list 5') or []) if n.startswith('equip 5 944 ')]
        result['worn'] = ask('equip')
        result['start'] = ask('quest start 1020')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            values = status()
            if values.get('task') == '11' and values.get('health', '-') != '-':
                break
        time.sleep(5)
        result['quest_status'] = (ask('status', 4) or [None])[0]
        result['quest_info'] = ask('quest info')
        monsters = ask('monsters') or []
        result['monsters'] = monsters
        here = status().get('area')
        rows = [line.split() for line in monsters[1:]]
        local = [r for r in rows if f'area {here},' in ' '.join(r)]
        if rows:
            slot = rows[0][1]
            result['monster_hp'] = ask(f'monster {slot} hp 777')
            result['monsters_after_hp'] = (ask('monsters') or [])[1:2]
            result['goto_other_area'] = ask(f'goto monster {slot}')
        if local:
            slot = local[0][1]
            result['goto_monster'] = ask(f'goto monster {slot}')
            result['where'] = ask('where')
        # Held still for six seconds, then let go for six.
        before = ask('monsters') or []
        result['hold_still'] = ask('hold still on')
        time.sleep(6)
        held = ask('monsters') or []
        result['status_holding'] = (ask('status', 4) or [None])[0]
        ask('hold still off')
        time.sleep(6)
        after = ask('monsters') or []
        result['positions'] = {'before': before[1:], 'held': held[1:], 'after': after[1:]}
    finally:
        game.stop()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1])))
