"""Boots game-mhfu with normal settings: every four seconds reads the status
and, until the village runs, presses START then ○ in turn, a capture after
each press. Stops the game without saving."""
from pathlib import Path
import queue, sys, time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import outil_debug
events = queue.Queue()
game = outil_debug.Game(ROOT, events)
folder = game.start()
last, n = None, 0
deadline = time.monotonic() + 330
try:
    time.sleep(10)
    while time.monotonic() < deadline and game.running():
        game.send('status')
        time.sleep(2)
        while not events.empty():
            e = events.get()
            if e[0] == 'answer' and e[1] == 'status' and e[2]:
                last = e[2][0]
        if last and ' task 10 ' in last and ' area 255 ' not in last:
            time.sleep(8)
            game.press('shot village')
            time.sleep(2)
            break
        game.press('pad start 12' if n % 2 == 0 else 'pad b 12')
        n += 1
        time.sleep(1.5)
        game.press(f'shot p{n:02d}')
finally:
    time.sleep(1)
    game.stop()
print(folder.name, n, last)
