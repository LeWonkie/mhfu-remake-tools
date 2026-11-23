"""Bounded check of outil_debug.py on a disposable game copy (never game-mhfu).

Builds the window once, then drives its Game class: boot, item names, pouch,
box, a departure, quest items and gathering points, then stops the game.
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
    result = {'window': 'built'}

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

    folder = game.start({'MHP3RD_INPUT_SCRIPT': campaign.BOOT, 'MHP3RD_DATA_DIR': str((game_copy / '../data').resolve())})
    result['folder'] = str(folder)
    try:
        deadline = time.monotonic() + 120
        state = None
        while time.monotonic() < deadline:
            state = ask('state', 4)
            if state and 'task 10 (village)' in state[0] and 'stage 139' in state[0]:
                break
        result['state'] = state
        time.sleep(6)
        names = ask('item list') or []
        result['item_list'] = [len(names), names[:2], [n for n in names if n.startswith('item 173 ')]]
        result['pouch_put'] = ask('pouch 173 2')
        result['pouch'] = ask('pouch')
        result['box_put'] = ask('box 8 5')
        result['where'] = ask('where')
        result['start'] = ask('quest start 10001')
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = ask('state', 4)
            if state and 'task 11 (quest)' in state[0] and 'stage 98' in state[0]:
                break
        result['quest_state'] = state
        time.sleep(4)
        result['quest_items'] = ask('quest items')
        result['quest_gather'] = ask('quest gather')
    finally:
        game.stop()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    ok = (result['item_list'][0] == 1260 and result['quest_items'] and 'Mountain Herbs x5' in result['quest_items'][0])
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1])))
