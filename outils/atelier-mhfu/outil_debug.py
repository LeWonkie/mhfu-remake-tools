"""Outil debug MHFU: plays the real game with the developer commands at hand.

Launches Yakumo on game-mhfu (the player's own save, copied aside first) with
the command file and live inputs on, then drives the MHFU commands of
psprecomp-mhfu/docs/MHFU_TOOLS.md: the game's state read back every second,
quests by name (quest_catalog.json, read from the disc by quest_catalog.py),
the monsters of the quest, items by name into the pouch or the box, the active
quest's delivery items, gathering points and teleports, held cheats, money,
equipment by name, and copies of the save. Only the Tk thread touches
widgets; the game's output is read by a thread and handed over through a
queue.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time

from atelier_bootstrap import configure_tk
configure_tk(Path(__file__).resolve().parents[2])
import tkinter as tk
from tkinter import messagebox, ttk

BG = "#101720"
PANEL = "#182331"
FIELD = "#0c131b"
FG = "#e4edf5"
MUTED = "#9aaec1"
ACCENT = "#60d6bc"
LINE = "#2b3c4d"
WARN = "#f5c575"
CHEATS = [("Invincible", "health"), ("Endurance infinie", "stamina"), ("Tranchant infini", "sharpness"),
          ("Monstres à 1 PV", "onehit"), ("Chrono figé", "timer"), ("Objets infinis", "items")]
EQUIP_TYPES = [("Arme lame", 5), ("Arme tir", 6), ("Tête", 1), ("Torse", 2), ("Bras", 3), ("Taille", 4), ("Jambes", 0)]
# Mission flags (the MHP2G decompilation's MissionFlag), as quest kinds.
QUEST_KINDS = [(0x4, "Chasse"), (0x1, "Abattage"), (0x2, "Livraison"), (0x10, "Trésor"), (0x80, "Repousser"),
               (0x40000, "Forteresse"), (0x8, "Spéciale")]
QUEST_TAGS = [(1 << 25, "urgente"), (1 << 26, "clé"), (1 << 24, "entraînement"), (1 << 29, "rang G")]
MISSION = {3: "réussie", 5: "échouée"}
STATUS_FIELDS = [("Lieu", "task"), ("Zone", "area"), ("Quête", "quest"), ("Mission", "mission"), ("Temps", "time"),
                 ("PV", "health"), ("Endurance", "stamina"), ("Tranchant", "sharpness"), ("Argent", "zenny"),
                 ("Monstres", "monsters")]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ITEM = re.compile(r"^item (\d+) (.*)$")
EQUIP = re.compile(r"^equip (\d+) (\d+) (.*)$")
GATHER = re.compile(r"^gather (\d+) at (\S+ \S+ \S+): kind (\d+), counters (\d+) (\d+), table \d+: (.*)$")
MONSTER = re.compile(r"^monster (\d+) species (\d+) (.*), area (\d+), health (-?\d+), at (\S+ \S+ \S+)(, boss)?$")
POUCH = re.compile(r"(\d+)x(-?\d+)")
CATALOG = Path(__file__).with_name("quest_catalog.json")
QUIET = re.compile(r"^\[debug\] (> )?status\b")


def quest_kind(flags: int) -> str:
    kind = next((name for bit, name in QUEST_KINDS if flags & bit), "Autre")
    tags = [name for bit, name in QUEST_TAGS if flags & bit]
    return kind + (f" ({', '.join(tags)})" if tags else "")


def parse_status(line: str) -> dict[str, str]:
    """`status task 11 area 93 …` as {"task": "11", "area": "93", …}."""
    words = line.split()
    if not words or words[0] != "status":
        return {}
    return dict(zip(words[1::2], words[2::2]))


def load_catalog(path: Path = CATALOG) -> dict[int, dict]:
    try:
        return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8"))["quests"].items()}
    except (OSError, ValueError, KeyError):
        return {}


class Game:
    """One Yakumo process, its command file and its output, answer blocks by command."""

    def __init__(self, project: Path, events: queue.Queue, game: Path | None = None):
        self.project = project
        self.game_dir = (game or project / "game-mhfu").resolve()
        self.events = events
        self.process: subprocess.Popen | None = None
        self.folder: Path | None = None
        self.waiting: list[tuple[str, callable]] = []
        self.lock = threading.Lock()

    @property
    def saves(self) -> Path:
        return self.game_dir / "ms0/PSP/SAVEDATA"

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, extra_env: dict | None = None) -> Path:
        binary = self.project / "psprecomp-mhfu/out/mhfu-probe/bin"
        game = self.game_dir
        folder = self.project / "sessions-psprecomp" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-debug")
        (folder / "frames").mkdir(parents=True)
        if self.saves.is_dir():
            # The commands write the character's pouch, box, money and gear; the
            # game saves them as usual. This copy is the way back.
            shutil.copytree(self.saves, folder / "sauvegarde-avant")
        for name in ("debug.txt", "input.txt"):
            (folder / name).write_bytes(b"")
        env = dict(os.environ, MHP3RD_STRICT_HLE="1", MHP3RD_DEBUG_MENU="1",
                   MHP3RD_DEBUG_COMMANDS=str(folder / "debug.txt"), MHP3RD_INPUT_LIVE=str(folder / "input.txt"),
                   MHP3RD_SCREENSHOT_DIR=str(folder / "frames"), MHP3RD_SCREENSHOT_EVERY="0", **(extra_env or {}))
        with (folder / "stdout.txt").open("wb") as out, (folder / "stderr.txt").open("wb") as err:
            self.process = subprocess.Popen([str(binary / "Yakumo.exe"), str(game)], cwd=binary, env=env,
                                            stdout=out, stderr=err, creationflags=NO_WINDOW)
        self.folder = folder
        self.waiting.clear()
        threading.Thread(target=self._read, args=(self.process, folder / "stdout.txt"), daemon=True).start()
        return folder

    def stop(self) -> None:
        if self.running():
            self.process.terminate()

    def send(self, line: str, on_answer=None) -> None:
        """Appends a command; `on_answer(lines)` gets its answer lines (Tk thread)."""
        if not self.running() or self.folder is None:
            raise RuntimeError("le jeu n'est pas lancé")
        if any(c in line for c in "\r\n\0"):
            raise ValueError("une commande par ligne")
        if on_answer is not None:
            # Commands sent before the game first reads the file are never
            # answered: only waiting handlers are kept, and not forever.
            with self.lock:
                self.waiting = self.waiting[-50:] + [(line.strip(), on_answer)]
        with (self.folder / "debug.txt").open("ab") as stream:
            stream.write((line.strip() + "\n").encode("utf-8"))

    def press(self, action: str) -> None:
        if not self.running() or self.folder is None:
            raise RuntimeError("le jeu n'est pas lancé")
        with (self.folder / "input.txt").open("a", encoding="utf-8") as stream:
            stream.write(f"0:{action}\n")

    def _deliver(self, command: str, lines: list[str]) -> None:
        with self.lock:
            index = next((i for i, (sent, _) in enumerate(self.waiting) if sent == command), None)
            handler = self.waiting.pop(index)[1] if index is not None else None
        self.events.put(("answer", command, lines, handler))

    def _read(self, process: subprocess.Popen, path: Path) -> None:
        offset, block, last = 0, None, time.monotonic()
        while True:
            alive = process.poll() is None
            try:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    data = stream.read()
            except OSError:
                data = b""
            cut = data.rfind(b"\n") + 1
            offset += cut
            for raw in data[:cut].decode("utf-8", errors="replace").splitlines():
                if not raw.startswith("[debug] "):
                    if raw.startswith(("Runtime stopped", "Missing HLE")):
                        self.events.put(("log", raw))
                    continue
                text = raw[len("[debug] "):]
                if text.startswith("> "):
                    if block:
                        self._deliver(*block)
                    block, last = (text[2:].strip(), []), time.monotonic()
                elif block:
                    block[1].append(text)
                    last = time.monotonic()
                self.events.put(("log", raw))
            if block and time.monotonic() - last > 0.6:
                self._deliver(*block)
                block = None
            if not alive and not cut:
                if block:
                    self._deliver(*block)
                self.events.put(("ended", process.returncode))
                return
            time.sleep(0.15)


class Saves:
    """Copies of the save folder: made at each launch, by hand, or before a restore."""

    def __init__(self, project: Path, saves: Path):
        self.sessions = project / "sessions-psprecomp"
        self.kept = self.sessions / "sauvegardes"
        self.saves = saves

    def copies(self) -> list[tuple[str, str, Path]]:
        """(date, origin, folder) of every copy, newest first."""
        found = []
        if self.sessions.is_dir():
            for folder in self.sessions.glob("*-debug/sauvegarde-avant"):
                found.append((folder.parent.name[:15], "lancement de l'outil", folder))
        if self.kept.is_dir():
            for folder in self.kept.iterdir():
                if folder.is_dir():
                    origin = "avant restauration" if folder.name.endswith("-avant-restauration") else "manuelle"
                    found.append((folder.name[:15], origin, folder))
        return sorted(found, key=lambda row: row[0], reverse=True)

    def keep(self, label: str = "") -> Path:
        if not self.saves.is_dir():
            raise FileNotFoundError(f"pas de sauvegarde dans {self.saves}")
        name = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "")
        target = self.kept / name
        shutil.copytree(self.saves, target)
        return target

    def restore(self, copy: Path) -> Path:
        """Puts `copy` back as the save; the save it replaces is kept first."""
        if not copy.is_dir():
            raise FileNotFoundError(str(copy))
        before = self.keep("avant-restauration") if self.saves.is_dir() else None
        if self.saves.is_dir():
            shutil.rmtree(self.saves)
        shutil.copytree(copy, self.saves)
        return before


class Tool:
    def __init__(self, project: Path, game: Path | None = None):
        self.project = project
        self.events: queue.Queue = queue.Queue()
        self.game = Game(project, self.events, game)
        self.saves = Saves(project, self.game.saves)
        self.names: dict[int, str] = {}
        self.equip_names: dict[int, dict[int, str]] = {}
        self.catalog = load_catalog()
        self.gather_points: list[dict] = []
        self.ticks = 0
        self.root = tk.Tk()
        self.root.title("Outil debug MHFU")
        self.root.geometry("1180x800")
        self.root.minsize(960, 660)
        self.root.configure(bg=BG)
        self._style()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._pump)
        self.root.after(1000, self._tick)
        if not self.catalog and (project / "game-mhfu/disc.iso").is_file():
            self._build_catalog()

    # ----- look ---------------------------------------------------------
    def _style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10), fieldbackground=FIELD,
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG)
        style.configure("Small.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, font=("Segoe UI Semibold", 16))
        style.configure("Head.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI Semibold", 11))
        style.configure("Status.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Key.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 8))
        style.configure("Value.TLabel", background=PANEL, foreground=FG, font=("Segoe UI Semibold", 11))
        style.configure("TButton", background=PANEL, foreground=FG, padding=6, borderwidth=1)
        style.map("TButton", background=[("active", LINE)])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG, font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#7ee6d0")])
        style.configure("TCheckbutton", background=PANEL, foreground=FG)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", LINE)], foreground=[("selected", FG)])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG)
        style.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG)
        style.configure("TCombobox", fieldbackground=FIELD, foreground=FG)
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=FG, rowheight=24)
        style.configure("Treeview.Heading", background=PANEL, foreground=MUTED)
        style.map("Treeview", background=[("selected", LINE)])

    def _panel(self, parent, title: str, hint: str = "") -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        ttk.Label(frame, text=title, style="Head.TLabel").pack(anchor="w")
        if hint:
            ttk.Label(frame, text=hint, style="Small.TLabel", wraplength=520, justify="left").pack(anchor="w", pady=(2, 8))
        return frame

    def _row(self, parent) -> ttk.Frame:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        return row

    def _tree(self, parent, columns, height: int = 12) -> ttk.Treeview:
        """A Treeview with a scroll bar; `columns` as (name, title, width, stretch)."""
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.pack(fill="both", expand=True, pady=6)
        tree = ttk.Treeview(box, columns=[c[0] for c in columns], show="headings", height=height)
        for name, title, width, stretch in columns:
            tree.heading(name, text=title)
            tree.column(name, width=width, stretch=stretch, anchor="e" if name in ("id", "n") else "w")
        bar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        return tree

    # ----- layout -------------------------------------------------------
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=(16, 12, 16, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Outil debug MHFU", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Arrêter le jeu", command=self._stop).pack(side="right", padx=(6, 0))
        ttk.Button(top, text="Lancer le jeu (debug)", style="Accent.TButton", command=self._launch).pack(side="right")

        # What the game says every second.
        live = ttk.Frame(self.root, style="Panel.TFrame", padding=(16, 8))
        live.pack(fill="x", padx=16)
        self.live: dict[str, tk.StringVar] = {}
        for column, (title, key) in enumerate(STATUS_FIELDS):
            ttk.Label(live, text=title, style="Key.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 18))
            var = tk.StringVar(value="—")
            self.live[key] = var
            ttk.Label(live, textvariable=var, style="Value.TLabel").grid(row=1, column=column, sticky="w", padx=(0, 18))
        self.quest_title = tk.StringVar(value="")
        ttk.Label(live, textvariable=self.quest_title, style="Small.TLabel").grid(
            row=2, column=0, columnspan=len(STATUS_FIELDS), sticky="w", pady=(4, 0))

        self.status = tk.StringVar(value="Jeu arrêté. « Lancer le jeu » démarre MHFU avec les commandes de debug.")
        ttk.Label(self.root, textvariable=self.status, style="Status.TLabel", padding=(16, 6, 16, 6)).pack(fill="x")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.pages = {}
        for title, build in (("Quêtes", self._tab_quests), ("En quête", self._tab_quest), ("Monstres", self._tab_monsters),
                             ("Objets", self._tab_items), ("Récolte et téléport", self._tab_gather),
                             ("Personnage", self._tab_character), ("Sauvegardes", self._tab_saves),
                             ("Console", self._tab_console)):
            page = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(page, text=title)
            self.pages[title] = page
            build(page)

    def _tab_quests(self, page) -> None:
        left = self._panel(page, "Toutes les quêtes",
                           "Lues sur le disque du jeu (quest_catalog.json). Cherche par nom, monstre ou numéro, "
                           "puis « Partir » depuis le village ou le hall, sans menu ouvert. Le prix et le déblocage "
                           "ne sont pas vérifiés.")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        row = self._row(left)
        ttk.Label(row, text="Chercher", style="Panel.TLabel").pack(side="left")
        self.quest_search = tk.StringVar()
        self.quest_search.trace_add(…4319 tokens truncated…) as error:
            self.status.set(str(error))

    def _press(self, action: str) -> None:
        try:
            self.game.press(action)
        except RuntimeError as error:
            self.status.set(str(error))

    def _launch(self) -> None:
        if self.game.running():
            self.status.set("Le jeu tourne déjà.")
            return
        binary = self.project / "psprecomp-mhfu/out/mhfu-probe/bin/Yakumo.exe"
        if not binary.is_file():
            messagebox.showerror("Outil debug MHFU", f"Yakumo introuvable : {binary}")
            return
        folder = self.game.start()
        self.names.clear()
        self.equip_names.clear()
        self.status.set(f"Lancement… journaux dans {folder}")
        self._list_saves()

    def _stop(self) -> None:
        if self.game.running() and messagebox.askyesno("Outil debug MHFU", "Fermer le jeu sans passer par ses menus ?"):
            self.game.stop()

    def _load_names(self) -> None:
        self._send("item list", self._fill_names)

    def _load_equipment(self) -> None:
        for _, kind in EQUIP_TYPES:
            self._send(f"equip list {kind}", self._fill_equipment)

    def _selected(self, tree: ttk.Treeview, what: str) -> tuple | None:
        selection = tree.selection()
        if not selection:
            self.status.set(f"Choisis d'abord {what} dans la liste.")
            return None
        return tree.item(selection[0], "values")

    def _selected_item(self) -> int | None:
        values = self._selected(self.item_tree, "un objet")
        return int(values[0]) if values else None

    def _give(self, where: str) -> None:
        item = self._selected_item()
        if item is not None:
            count = max(1, min(99, int(self.count.get() or 1)))
            self._send(f"{where} {item} {count}", self._show_first)

    def _remove(self) -> None:
        item = self._selected_item()
        if item is not None:
            self._send(f"pouch {item} 0", self._show_first)

    def _hold(self, name: str, var: tk.BooleanVar) -> None:
        self._send(f"hold {name} {'on' if var.get() else 'off'}", self._show_first)

    def _depart(self) -> None:
        # Choosing a quest in the list writes its number in the field.
        quest = self.quest_id.get().strip()
        if not quest.isdigit():
            self.status.set("Choisis une quête ou tape son numéro.")
            return
        self._send(f"quest start {quest}", self._show_first)

    def _goto_point(self) -> None:
        values = self._selected(self.gather_tree, "un point (« Lister les points »)")
        if values:
            self._send(f"goto gather {values[0]}", self._show_first)

    def _gather_five(self) -> None:
        # One press per two seconds: a gathering's animation takes about that long.
        for i in range(5):
            self.root.after(i * 2000, lambda: self._press("pad b 6"))

    def _goto_coords(self) -> None:
        try:
            x, y, z = (float(var.get().replace(",", ".")) for var in self.coords)
        except ValueError:
            self.status.set("X, Y et Z doivent être des nombres.")
            return
        self._send(f"goto {x} {y} {z}", self._show_first)

    def _monster(self, template: str) -> None:
        values = self._selected(self.monster_tree, "un monstre")
        if values:
            self._send(template.format(n=values[0]), self._after_monster)

    def _refresh_monsters(self) -> None:
        self._send("monsters", self._show_monsters)

    def _equip(self) -> None:
        values = self._selected(self.equip_tree, "une pièce")
        if values:
            kind = dict(EQUIP_TYPES)[self.equip_type.get()]
            self._send(f"equip {kind} {values[0]}", self._after_equip)

    def _equip_by_id(self) -> None:
        kind = dict(EQUIP_TYPES)[self.equip_type.get()]
        self._send(f"equip {kind} {self.equip_id.get().strip()}", self._after_equip)

    def _free_command(self) -> None:
        line = self.command.get().strip()
        if line:
            self._send(line, self._show_lines)
            self.command.set("")

    def _build_catalog(self) -> None:
        disc = self.project / "game-mhfu/disc.iso"
        if not disc.is_file():
            self.status.set(f"Disque introuvable : {disc}")
            return
        self.catalog_note.set("Lecture du disque…")

        def work():
            try:
                import mhfu_data
                import quest_catalog
                quests = quest_catalog.catalogue(mhfu_data.DataBin.open(disc))
                CATALOG.write_text(json.dumps({"source": disc.name, "quests": quests}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
                self.events.put(("catalog", None))
            except Exception as error:  # shown to the user, the window keeps running
                self.events.put(("catalog", str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _keep_save(self) -> None:
        try:
            target = self.saves.keep()
        except OSError as error:
            self.status.set(f"Copie impossible : {error}")
            return
        self.status.set(f"Sauvegarde copiée dans {target}")
        self._list_saves()

    def _restore_save(self) -> None:
        values = self._selected(self.save_tree, "une copie")
        if not values:
            return
        if self.game.running():
            self.status.set("Arrête d'abord le jeu : il garde la sauvegarde en mémoire et la réécrirait.")
            return
        if not messagebox.askyesno("Outil debug MHFU", f"Remplacer ta sauvegarde par la copie du {values[0]} ?\n"
                                                       "La sauvegarde actuelle sera d'abord copiée."):
            return
        try:
            before = self.saves.restore(Path(values[2]))
        except OSError as error:
            self.status.set(f"Restauration impossible : {error}")
            return
        self.status.set(f"Copie du {values[0]} restaurée." + (f" Ancienne sauvegarde : {before}" if before else ""))
        self._list_saves()

    def _open_save(self) -> None:
        values = self.save_tree.selection() and self.save_tree.item(self.save_tree.selection()[0], "values")
        folder = Path(values[2]) if values else self.saves.saves
        if folder.is_dir() and hasattr(os, "startfile"):
            os.startfile(folder)

    # ----- answers ------------------------------------------------------
    def _named(self, text: str) -> str:
        """`173x5` entries as `Mountain Herbs ×5`, when the names are known."""
        return ", ".join(f"{self.names.get(int(i), 'objet ' + i)} ×{n}" for i, n in POUCH.findall(text))

    def _show_first(self, lines: list[str]) -> None:
        self.status.set(lines[0] if lines else "(pas de réponse)")

    def _show_lines(self, lines: list[str]) -> None:
        self._show_first(lines)
        if lines and lines[0].startswith("quest "):
            self._set_text(self.info_text, "\n".join(lines))

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _fill_names(self, lines: list[str]) -> None:
        for line in lines:
            if match := ITEM.match(line):
                self.names[int(match[1])] = match[2]
        self.status.set(f"{len(self.names)} objets chargés depuis le jeu." if self.names else (lines[0] if lines else ""))
        self._filter_items()

    def _fill_equipment(self, lines: list[str]) -> None:
        for line in lines:
            if match := EQUIP.match(line):
                self.equip_names.setdefault(int(match[1]), {})[int(match[2])] = match[3]
        if self.equip_names:
            self.status.set(f"Noms d'équipement chargés ({sum(map(len, self.equip_names.values()))} pièces).")
        elif lines:
            self.status.set(lines[0])
        self._filter_equipment()

    def _filter_items(self) -> None:
        wanted = self.search.get().strip().lower()
        self.item_tree.delete(*self.item_tree.get_children())
        for item, name in self.names.items():
            if not wanted or wanted in name.lower() or wanted == str(item):
                self.item_tree.insert("", "end", values=(item, name))

    def _filter_equipment(self) -> None:
        wanted = self.equip_search.get().strip().lower()
        kind = dict(EQUIP_TYPES)[self.equip_type.get()]
        self.equip_tree.delete(*self.equip_tree.get_children())
        for piece, name in self.equip_names.get(kind, {}).items():
            if not wanted or wanted in name.lower() or wanted == str(piece):
                self.equip_tree.insert("", "end", values=(piece, name))

    def _filter_quests(self) -> None:
        wanted = self.quest_search.get().strip().lower()
        kind = self.quest_filter.get()
        self.quest_tree.delete(*self.quest_tree.get_children())
        shown = 0
        for quest, entry in self.catalog.items():
            label = quest_kind(entry.get("flags", 0))
            if kind != "Toutes" and not label.startswith(kind):
                continue
            text = " ".join((str(quest), entry.get("title", ""), entry.get("objective", ""), entry.get("monsters", ""))).lower()
            if wanted and wanted not in text:
                continue
            self.quest_tree.insert("", "end", values=(quest, entry.get("title", ""),
                                                      entry.get("objective", "").replace("\n", " "), label))
            shown += 1
        self.catalog_note.set(f"{shown} quêtes sur {len(self.catalog)}" if self.catalog
                              else "Pas de catalogue : « Relire le disque » le construit.")

    def _show_quest(self) -> None:
        values = self.quest_tree.selection() and self.quest_tree.item(self.quest_tree.selection()[0], "values")
        if not values:
            return
        quest = int(values[0])
        self.quest_id.set(str(quest))
        entry = self.catalog.get(quest, {})
        parts = [f"{entry.get('title', '')}  (n° {quest})", "",
                 f"Objectif : {entry.get('objective', '').replace(chr(10), ' ')}",
                 f"Type : {quest_kind(entry.get('flags', 0))}, {entry.get('minutes', '?')} min",
                 f"Échec : {entry.get('failure', '').replace(chr(10), ', ')}",
                 f"Monstres : {entry.get('monsters', '').replace(chr(10), ', ') or '—'}",
                 f"Client : {entry.get('client', '')}", "", entry.get("description", "")]
        self._set_text(self.quest_text, "\n".join(parts))

    def _show_pouch(self, lines: list[str]) -> None:
        self.pouch_list.delete(0, "end")
        text = lines[0] if lines else ""
        for item, count in POUCH.findall(text.split(":", 1)[-1]):
            self.pouch_list.insert("end", f"{self.names.get(int(item), 'objet ' + item)}  ×{count}   (id {item})")
        if not POUCH.findall(text):
            self.pouch_list.insert("end", text)
        self.status.set(text.split(":", 1)[0] + " lue.")

    def _show_worn(self, lines: list[str]) -> None:
        self.worn_list.delete(0, "end")
        names = {kind: name for name, kind in EQUIP_TYPES}
        for line in lines:
            if match := re.match(r"^worn (\d+):(\d+) ?(.*)$", line):
                self.worn_list.insert("end", f"{names.get(int(match[1]), '?')} : {match[3] or 'id ' + match[2]}")
            else:
                self.worn_list.insert("end", line)

    def _after_equip(self, lines: list[str]) -> None:
        self._show_first(lines)
        self._send("equip", self._show_worn)

    def _fill_quests(self, lines: list[str]) -> None:
        ids = []
        for first, last in re.findall(r"(\d+)(?:-(\d+))?", lines[0].split(":", 1)[-1] if lines else ""):
            ids += [str(i) for i in range(int(first), int(last or first) + 1)]
        self.quest_box.configure(values=ids)
        self.status.set(f"{len(ids)} quêtes ont un fichier de mission.")

    def _show_gather(self, lines: list[str]) -> None:
        self.gather_tree.delete(*self.gather_tree.get_children())
        for line in lines:
            if match := GATHER.match(line):
                self.gather_tree.insert("", "end", values=(match[1], match[2], f"{match[4]} / {match[5]}", match[6]))
        self.status.set(lines[0] if lines else "(pas de réponse)")

    def _show_monsters(self, lines: list[str]) -> None:
        selected = self.monster_tree.selection() and self.monster_tree.item(self.monster_tree.selection()[0], "values")
        self.monster_tree.delete(*self.monster_tree.get_children())
        for line in lines:
            if match := MONSTER.match(line):
                row = self.monster_tree.insert("", "end", values=(
                    match[1], f"{match[3]} (espèce {match[2]})", match[4], match[5], match[6], "boss" if match[7] else ""))
                if selected and selected[0] == match[1]:
                    self.monster_tree.selection_set(row)
        if lines and not any(MONSTER.match(line) for line in lines):
            self.status.set(lines[0])

    def _after_monster(self, lines: list[str]) -> None:
        self._show_first(lines)
        self._refresh_monsters()

    def _show_where(self, lines: list[str]) -> None:
        self._show_first(lines)
        if lines and (match := re.search(r"at (\S+) (\S+) (\S+)$", lines[0])):
            for var, value in zip(self.coords, match.groups()):
                var.set(value)

    def _show_status(self, lines: list[str]) -> None:
        values = parse_status(lines[0]) if lines else {}
        if not values:
            return
        task = values.get("task", "-")
        place = {"10": "Village", "11": "Quête"}.get(task, "Chargement" if task == "-" else f"Tâche {task}")
        quest = values.get("quest", "-")
        mission = values.get("mission", "-")
        if mission.isdigit():
            state = int(mission)
            mission = MISSION.get(state, "en cours" if state < 3 else f"terminée ({state})")
        shown = dict(values, task=place, mission=mission)
        if values.get("time", "-") != "-":
            seconds = int(values["time"])
            shown["time"] = f"{seconds // 60}:{seconds % 60:02d}"
        for key, var in self.live.items():
            text = shown.get(key, "-")
            var.set("—" if text == "-" else text)
        entry = self.catalog.get(int(quest)) if quest.isdigit() and task == "11" else None
        self.quest_title.set(f"{entry['title']} — {entry['objective'].replace(chr(10), ' ')}" if entry else "")
        held = set(values.get("hold", "none").split(","))
        for name, var in self.cheats.items():
            var.set(name in held)
        if task != "-" and not self.names:
            self._load_names()
        if values.get("zenny", "-") != "-" and not self.equip_names and self.ticks % 10 == 0:
            self._load_equipment()

    def _list_saves(self) -> None:
        self.save_tree.delete(*self.save_tree.get_children())
        for date, origin, folder in self.saves.copies():
            shown = f"{date[6:8]}/{date[4:6]} {date[9:11]}:{date[11:13]}" if len(date) >= 13 else date
            self.save_tree.insert("", "end", values=(shown, origin, str(folder)))

    def _log(self, line: str) -> None:
        self.console.configure(state="normal")
        self.console.insert("end", line + "\n")
        if int(self.console.index("end-1c").split(".")[0]) > 3000:
            self.console.delete("1.0", "1000.0")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _pump(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    if not QUIET.match(event[1]):
                        self._log(event[1])
                elif event[0] == "answer":
                    _, command, lines, handler = event
                    if command == "status":
                        self._show_status(lines)
                    elif handler:
                        handler(lines)
                elif event[0] == "ended":
                    self.status.set(f"Le jeu s'est arrêté (code {event[1]}). Journaux : {self.game.folder}")
                    for var in self.live.values():
                        var.set("—")
                elif event[0] == "catalog":
                    if event[1]:
                        self.catalog_note.set(f"Lecture du disque impossible : {event[1]}")
                    else:
                        self.catalog = load_catalog()
                        self._filter_quests()
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _tick(self) -> None:
        # The game's state every second; the monsters every two while their
        # tab is open.
        self.ticks += 1
        if self.game.running():
            self._send("status")
            page = self.notebook.select()
            if (self.ticks % 2 == 0 and self.auto_monsters.get() and page == str(self.pages["Monstres"])
                    and self.live["task"].get() == "Quête"):
                self._refresh_monsters()
        self.root.after(1000, self._tick)

    def _close(self) -> None:
        if self.game.running() and not messagebox.askyesno(
                "Outil debug MHFU", "Le jeu tourne encore. Fermer l'outil et le jeu ?"):
            return
        self.game.stop()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--game", type=Path, help="another game folder than game-mhfu (a copy to try things on)")
    args = parser.parse_args()
    Tool(args.project.resolve(), args.game).root.mainloop()


if __name__ == "__main__":
    main()
