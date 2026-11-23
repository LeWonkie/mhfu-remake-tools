"""Drives the real debug window on game-mhfu (Hugo's save), reading only.

Boots with the workshop's boot presses, waits for the village, reads the
status and the worn equipment, leaves for quest 1020 from the Quests tab,
opens the Monsters tab, grabs the window, then closes the game without
saving (the window copied the save aside at launch). Prints a JSON summary.
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'outils/atelier-mhfu'))
import atelier_campaign as campaign
import outil_debug
from PIL import ImageGrab

OUT = ROOT / 'work/try-real-save'


def main() -> int:
    OUT.mkdir(exist_ok=True)
    tool = outil_debug.Tool(ROOT)
    result = {'save_copy_before': None}
    began = time.monotonic()
    stage = {'name': 'boot'}

    def grab(name):
        tool.root.update()
        x, y = tool.root.winfo_rootx(), tool.root.winfo_rooty()
        w, h = tool.root.winfo_width(), tool.root.winfo_height()
        path = OUT / f'{name}.png'
        ImageGrab.grab((x, y, x + w, y + h)).save(path)
        result.setdefault('shots', []).append(str(path))

    def live():
        return {key: var.get() for key, var in tool.live.items()}

    def step():
        elapsed = time.monotonic() - began
        if not tool.game.running() and stage['name'] != 'boot':
            result['ended_early'] = stage['name']
            tool.root.quit()
            return
        if stage['name'] == 'boot' and not tool.game.running():
            folder = tool.game.start({'MHP3RD_INPUT_SCRIPT': campaign.BOOT,
                                      'MHP3RD_DATA_DIR': str(ROOT / 'work/outil-debug-check-v2/data')})
            result['folder'] = str(folder)
            result['save_copy_before'] = (folder / 'sauvegarde-avant').is_dir()
            stage['name'] = 'village'
        elif stage['name'] == 'village' and live()['task'] == 'Village' and live()['area'] not in ('—', '255'):
            result['village'] = live()
            stage.update(name='settle', at=elapsed)
        elif stage['name'] == 'settle' and elapsed - stage['at'] > 8:
            tool._send('equip', lambda lines: result.__setitem__('worn', lines))
            tool._send('pouch', lambda lines: result.__setitem__('pouch', lines))
            tool.notebook.select(tool.pages['Personnage'])
            tool._send('equip', tool._show_worn)
            stage.update(name='gear', at=elapsed)
        elif stage['name'] == 'gear' and elapsed - stage['at'] > 6:
            result['equipment_names'] = sum(map(len, tool.equip_names.values()))
            result['item_names'] = len(tool.names)
            grab('1-personnage')
            tool.notebook.select(tool.pages['Quêtes'])
            tool.quest_search.set('Attack of the Yian Kut-Ku')
            rows = tool.quest_tree.get_children()
            if rows:
                tool.quest_tree.selection_set(rows[0])
                tool._show_quest()
            grab('2-quetes')
            tool._depart()
            stage.update(name='quest', at=elapsed)
        elif stage['name'] == 'quest' and live()['task'] == 'Quête' and live()['health'] not in ('—', '-'):
            result['quest'] = live()
            result['quest_title'] = tool.quest_title.get()
            tool.notebook.select(tool.pages['Monstres'])
            stage.update(name='monsters', at=elapsed)
        elif stage['name'] == 'monsters' and elapsed - stage['at'] > 6:
            result['monsters'] = [tool.monster_tree.item(r, 'values') for r in tool.monster_tree.get_children()]
            grab('3-monstres')
            stage['name'] = 'done'
            tool.game.stop()
            tool.root.quit()
            return
        if elapsed > 300:
            result['timeout'] = stage['name']
            tool.game.stop()
            tool.root.quit()
            return
        tool.root.after(500, step)

    tool.root.after(500, step)
    tool.root.mainloop()
    tool.game.stop()
    tool.root.destroy()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
