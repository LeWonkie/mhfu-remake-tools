"""MHFU Workshop: a small, local and responsive Tkinter frontend.

Only the Tk thread touches widgets. Backend calls and bounded log reads run
outside that thread and report their results through a queue.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
from atelier_bootstrap import configure_tk
# Tcl caches its environment when _tkinter loads, before the first Tk window.
configure_tk(Path(__file__).resolve().parents[2])
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

import atelier_campaign
import atelier_pilot
import hunt
from atelier_hunt_state import objectives
from atelier_core import Workshop, VARIANTS


BG = "#101720"
PANEL = "#182331"
FIELD = "#0c131b"
FG = "#e4edf5"
MUTED = "#9aaec1"
ACCENT = "#60d6bc"
LINE = "#2b3c4d"
WARN = "#f5c575"
# Pilot tab: the game's held cheats (`hold NAME on|off`), labels as shown.
CHEAT_TOGGLES = [("Invincible", "health"), ("Endurance", "stamina"), ("Tranchant", "sharpness"),
                 ("Monstres 1 PV", "onehit"), ("Chrono figé", "timer"), ("Objets infinis", "items")]
DEFAULT_PROJECT = Path.home() / "Documents" / "MHFU REMAKE"
CAMPAIGN_CAPTURES = {"Essentielles": "fast", "Détaillées": "detailed"}
AUTOMATION_RTC_US = 1609459200123456


def short_time(value: Any) -> str:
    if not value:
        return "—"
    return str(value).replace("T", " ")[:19]


def scenario_label(scenario: dict) -> str:
    return str(scenario.get("label") or scenario.get("name") or "Scénario")


class WorkshopUI:
    def __init__(self, root: tk.Tk, backend: Workshop, smoke: bool = False):
        self.root, self.backend, self.smoke = root, backend, smoke
        self.events: queue.Queue = queue.Queue()
        self.closed = threading.Event()
        self.busy = False
        self.closing = False
        self.status_busy = False
        self.history_busy = False
        self.automation_active = False
        self.automation_cancel = threading.Event()
        self.hunt_status = tk.StringVar(value="Choisissez un objectif vérifié avant de lancer la chasse.")
        self.status_data: dict = {}
        self.run_rows: dict[str, dict] = {}
        self.scenario_rows: list[dict] = []
        self.last_report: Path | None = None
        self.last_log_key = ""
        self.local_events: list[str] = []
        self.last_log_text = ""
        self.mutable: list[ttk.Button] = []
        self.live_controls: list[ttk.Button] = []
        self.always_buttons: list[ttk.Button] = []
        self.variant_keys = list(VARIANTS)
        self.scenario_var = tk.StringVar()
        self.variant_var = tk.StringVar()
        self.duration_var = tk.StringVar(value="60")
        self.bug_title = tk.StringVar()
        self.editor_name = tk.StringVar(value="Mon parcours")
        self.editor_duration = tk.StringVar(value="60")
        self.compare_a = tk.StringVar()
        self.compare_b = tk.StringVar()
        self.footer_var = tk.StringVar(value="Lecture de l'état du projet…")
        self.status_var = tk.StringVar(value="Vérification…")
        self.project_var = tk.StringVar()
        self.details_var = tk.StringVar(value="Préparez l'atelier pour isoler les essais et les sauvegardes.")
        self.scenario_hint = tk.StringVar(value="Choisissez un parcours à rejouer.")
        self.session_hint = tk.StringVar(value="Sélectionnez une session pour consulter ses fichiers.")
        self._style()
        self._layout()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(70, self._drain)
        self.root.after(100, self.refresh_status)
        self.root.after(150, self.refresh_resources)
        self.root.after(500, self._log_tick)

    def _style(self) -> None:
        self.root.title("Atelier MHFU · PSPRecomp")
        self.root.geometry("1160x860")  # room for the bottom status line
        self.root.minsize(1030, 780)
        self.root.configure(bg=BG)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Panel.TLabel", background=PANEL)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Small.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Hero.TLabel", font=("Segoe UI Semibold", 24))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 12))
        style.configure("Accent.TLabel", foreground=ACCENT, font=("Segoe UI Semibold", 11))
        style.configure("TButton", padding=(12, 8), background=LINE, borderwidth=0)
        style.map("TButton", background=[("active", "#3a5369"), ("disabled", "#1c2834")],
                  foreground=[("disabled", "#63778a")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#102622",
                        font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#8ae9d2"), ("disabled", "#26413e")],
                  foreground=[("disabled", "#78938d")])
        style.configure("Danger.TButton", background="#53313b", foreground="#ffd4d9")
        style.map("Danger.TButton", background=[("active", "#78404e"), ("disabled", "#25232d")])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG,
                        bordercolor=LINE, padding=7)
        style.configure("TCombobox", fieldbackground=FIELD, background=LINE, foreground=FG,
                        arrowcolor=ACCENT, bordercolor=LINE, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", LINE)
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 8))
        style.configure("TNotebook.Tab", background=PANEL, padding=(24, 10), foreground=MUTED)
        style.map("TNotebook.Tab", background=[("selected", LINE)], foreground=[("selected", FG)])
        style.configure("Treeview", fieldbackground=FIELD, background=FIELD,
                        foreground=FG, rowheight=30, bordercolor=LINE)
        style.configure("Treeview.Heading", background=LINE, foreground=FG,
                        font=("Segoe UI Semibold", 9), padding=6)
        style.map("Treeview", background=[("selected", "#284d54")])
        style.configure("Horizontal.TProgressbar", troughcolor=FIELD, background=ACCENT,
                        borderwidth=0, thickness=3)
        style.configure("TLabelframe", background=BG, bordercolor=LINE)
        style.configure("TLabelframe.Label", foreground=MUTED, background=BG)

    def _button(self, parent, text: str, callback: Callable, *, live=False,
                always=False, style="TButton") -> ttk.Button:
        button = ttk.Button(parent, text=text, command=callback, style=style)
        if live:
            self.live_controls.append(button)
        elif always:
            self.always_buttons.append(button)
        else:
            self.mutable.append(button)
        return button

    def _layout(self) -> None:
        page = ttk.Frame(self.root, padding=(24, 18))
        page.pack(fill="both", expand=True)
        header = ttk.Frame(page)
        header.pack(fill="x", pady=(0, 15))
        title = ttk.Frame(header)
        title.pack(side="left", fill="x", expand=True)
        ttk.Label(title, text="ATELIER MHFU", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(title, text="Préparer · rejouer · capturer · comparer", style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
        badge = ttk.Frame(header, padding=(16, 10), style="Panel.TFrame")
        badge.pack(side="right")
        ttk.Label(badge, textvariable=self.status_var, style="Panel.TLabel").pack(anchor="e")
        ttk.Label(badge, text="Aperçu sans action" if self.smoke else "Essais isolés du jeu principal",
                  style="Panel.TLabel", font=("Segoe UI", 9)).pack(anchor="e", pady=(3, 0))
        self.notebook = ttk.Notebook(page)
        self.notebook.pack(fill="both", expand=True)
        self.pilot_tab = ttk.Frame(self.notebook, padding=(2, 8))
        self.scenario_tab = ttk.Frame(self.notebook, padding=(2, 8))
        self.sessions_tab = ttk.Frame(self.notebook, padding=(2, 8))
        self.notebook.add(self.pilot_tab, text="01   Pilotage")
        self.notebook.add(self.scenario_tab, text="02   Scénarios")
        self.notebook.add(self.sessions_tab, text="03   Sessions & comparaison")
        self.campaign_tab = ttk.Frame(self.notebook, padding=(2, 8))
        self.notebook.add(self.campaign_tab, text="04   Campagnes")
        self._pilot()
        self._scenarios()
        self._sessions()
        self._campaigns()
        bottom = ttk.Frame(page)
        bottom.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=84)
        self.progress.pack(side="right", padx=(10, 0))
        ttk.Label(bottom, textvariable=self.footer_var, style="Small.TLabel", wraplength=970).pack(side="left", anchor="w")

    def _pilot(self) -> None:
        frame = self.pilot_tab
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(2, weight=1)
        project = ttk.Frame(frame, padding=12, style="Panel.TFrame")
        project.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(project, textvariable=self.project_var, style="Panel.TLabel",
                  font=("Segoe UI Semibold", 10)).pack(anchor="w")
        ttk.Label(project, textvariable=self.details_var, style="Panel.TLabel", wraplength=1010).pack(anchor="w", pady=(4, 0))
        left = ttk.Frame(frame)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        ttk.Label(left, text="Un essai reproductible", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        run_fields = ttk.Frame(left)
        run_fields.pack(fill="x")
        run_fields.columnconfigure(0, weight=1)
        run_fields.columnconfigure(1, weight=1)
        ttk.Label(run_fields, text="Scénario", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(run_fields, text="Variante de rendu", style="Small.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.scenario_combo = ttk.Combobox(run_fields, textvariable=self.scenario_var, state="readonly")
        self.scenario_combo.grid(row=1, column=0, sticky="ew", pady=(3, 5))
        self.scenario_combo.bind("<<ComboboxSelected>>", self._scenario_changed)
        self.variant_combo = ttk.Combobox(run_fields, textvariable=self.variant_var, state="readonly",
                                          values=[v["label"] for v in VARIANTS.values()])
        self.variant_combo.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(3, 5))
        if self.variant_keys:
            self.variant_combo.current(0)
        ttk.Label(left, textvariable=self.scenario_hint, style="Small.TLabel", wraplength=600).pack(anchor="w", pady=(0, 8))
        duration = ttk.Frame(left)
        duration.pack(fill="x", pady=(0, 10))
        ttk.Label(duration, text="Durée maximale").pack(side="left")
        self.duration_entry = ttk.Spinbox(duration, from_=1, to=1800, textvariable=self.duration_var, width=7)
        self.duration_entry.pack(side="left", padx=9)
        ttk.Label(duration, text="secondes", style="Small.TLabel").pack(side="left")
        controls = ttk.Frame(left)
        controls.pack(fill="x")
        self.start_button = self._button(controls, "▶  Lancer l'essai", self.start, style="Accent.TButton")
        self.start_button.pack(side="left", padx=(0, 7))
        self.stop_button = self._button(controls, "■  Arrêter", self.stop, live=True, style="Danger.TButton")
        self.stop_button.pack(side="left", padx=(0, 7))
        self._button(controls, "Capture", lambda: self.send_control("screenshot"), live=True).pack(side="left", padx=(0, 7))
        self._button(controls, "A/B automatique", self.auto_compare).pack(side="left")
        dev = ttk.Frame(left)
        dev.pack(fill="x", pady=(10, 12))
        self._button(dev, "Préparer l'atelier", self.prepare).pack(side="left", padx=(0, 7))
        self._button(dev, "Compiler", lambda: self.build(False)).pack(side="left", padx=(0, 7))
        self._button(dev, "Compiler + tests", lambda: self.build(True)).pack(side="left")
        right = ttk.Frame(frame)
        right.grid(row=1, column=1, sticky="nsew")
        ttk.Label(right, text="Capturer le contexte d'un bug", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Entry(right, textvariable=self.bug_title).pack(fill="x")
        ttk.Label(right, text="Titre facultatif : « HUD manquant en quête »", style="Small.TLabel").pack(anchor="w", pady=(3, 7))
        actions = ttk.Frame(right)
        actions.pack(fill="x")
        self.report_button = self._button(actions, "Signaler le bug", self.bug_report)
        self.report_button.pack(side="left", padx=(0, 7))
        self.checkpoint_button = self._button(actions, "Figer les sauvegardes", self.checkpoint)
        self.checkpoint_button.pack(side="left")
        ttk.Label(right, text="Le rapport regroupe journaux, paramètres et contexte.\nFigez les sauvegardes entre deux essais pour rejouer le même point de départ.",
                  style="Small.TLabel", wraplength=420).pack(anchor="w", pady=(8, 7))
        pad = ttk.Frame(right)
        pad.pack(fill="x")
        ttk.Label(pad, text="Caméra", style="Small.TLabel").pack(side="left", padx=(0, 8))
        for label, command in [("↑", "dpup"), ("↓", "dpdown"), ("←", "dpleft"), ("→", "dpright"), ("✕", "a"), ("○", "b"), ("Start", "start")]:
            button = self._button(pad, label, lambda c=command: self.send_control(f"pad {c} 12"), live=True)
            button.configure(width=3 if label != "Start" else 5, padding=(3, 5))
            button.pack(side="left", padx=(0, 3))
        movement = ttk.Frame(right)
        movement.pack(fill="x", pady=(5, 0))
        ttk.Label(movement, text="Marche", style="Small.TLabel").pack(side="left", padx=(0, 8))
        for label, direction in [("↑", "up"), ("↓", "down"), ("←", "left"), ("→", "right")]:
            button = self._button(movement, label, lambda d=direction: self.send_control(f"move {d}"), live=True)
            button.configure(width=3, padding=(3, 5))
            button.pack(side="left", padx=(0, 3))
        quest_row = ttk.Frame(right)
        quest_row.pack(fill="x", pady=(5, 0))
        self.quest_var = tk.StringVar(value="1001")
        ttk.Label(quest_row, text="Quête n°", style="Small.TLabel").pack(side="left", padx=(0, 8))
        ttk.Entry(quest_row, textvariable=self.quest_var, width=7).pack(side="left", padx=(0, 5))
        self._button(quest_row, "Partir en quête", self.start_quest, live=True).pack(side="left")
        # Test cheats, through the game's own developer commands (docs/MHFU_TOOLS.md).
        cheat_row = ttk.Frame(right)
        cheat_row.pack(fill="x", pady=(5, 0))
        ttk.Label(cheat_row, text="Triches", style="Small.TLabel").pack(side="left", padx=(0, 8))
        self.cheat_vars = {}
        for label, cheat in CHEAT_TOGGLES:
            var = self.cheat_vars[cheat] = tk.BooleanVar(value=False)
            box = ttk.Checkbutton(cheat_row, text=label, variable=var,
                                  command=lambda c=cheat: self.send_debug(
                                      f"hold {c} {'on' if self.cheat_vars[c].get() else 'off'}"))
            box.pack(side="left", padx=(0, 4))
            self.live_controls.append(box)
        gear_row = ttk.Frame(right)
        gear_row.pack(fill="x", pady=(5, 0))
        self._button(gear_row, "Meilleur équipement", lambda: self.send_debug("loadout best"), live=True).pack(side="left", padx=(0, 5))
        self._button(gear_row, "Argent max", lambda: self.send_debug("money 9999999"), live=True).pack(side="left")
        ttk.Label(gear_row, text="équipement : au village, avant de partir", style="Small.TLabel").pack(side="left", padx=(8, 0))
        logs_frame = ttk.Frame(frame)
        logs_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        logs_header = ttk.Frame(logs_frame)
        logs_header.pack(fill="x", pady=(0, 5))
        ttk.Label(logs_header, text="Activité & journaux", style="Title.TLabel").pack(side="left")
        self._button(logs_header, "Ouvrir le dossier de l'essai", self.open_active, always=True).pack(side="right")
        self.logs = tk.Text(logs_frame, background=FIELD, foreground="#b7c9d9", borderwidth=0,
                            font=("Consolas", 9), wrap="none", height=8, padx=10, pady=8,
                            insertbackground=FG, state="disabled")
        scrollbar = ttk.Scrollbar(logs_frame, orient="vertical", command=self.logs.yview)
        self.logs.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.logs.pack(fill="both", expand=True)

    def _campaigns(self) -> None:
        frame = self.campaign_tab
        self.campaign_quests = tk.StringVar(value=" ".join(str(q) for q in atelier_campaign.DEFAULT_QUESTS))
        self.campaign_look = tk.StringVar(value="12")
        self.campaign_speed = tk.StringVar(value="8")
        self.campaign_captures = tk.StringVar(value="Essentielles")
        self.campaign_force = tk.BooleanVar(value=False)
        ttk.Label(frame, text="Campagne automatique de quêtes", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text="Repérage technique : départ, observation, fin par le chronomètre et retour au village. "
                              "Les validations identiques sont réutilisées avec leurs preuves. Une fin forcée "
                              "ne valide pas le gameplay. Examinez les captures du rapport.",
                  style="Small.TLabel", wraplength=1000).pack(anchor="w", pady=(0, 10))
        ttk.Label(frame, text="Quêtes (numéros séparés par des espaces)", style="Small.TLabel").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.campaign_quests).pack(fill="x", pady=(3, 8))
        look = ttk.Frame(frame)
        look.pack(fill="x", pady=(0, 10))
        ttk.Label(look, text="Observation par quête").pack(side="left")
        ttk.Spinbox(look, from_=1, to=120, textvariable=self.campaign_look, width=6).pack(side="left", padx=9)
        ttk.Label(look, text="secondes de jeu", style="Small.TLabel").pack(side="left")
        ttk.Label(look, text="Vitesse demandée ×").pack(side="left", padx=(18, 6))
        ttk.Combobox(look, textvariable=self.campaign_speed, values=list(atelier_pilot.SPEEDS),
                     state="readonly", width=3).pack(side="left")
        ttk.Label(look, text="Captures").pack(side="left", padx=(18, 6))
        ttk.Combobox(look, textvariable=self.campaign_captures, values=list(CAMPAIGN_CAPTURES),
                     state="readonly", width=13).pack(side="left")
        ttk.Checkbutton(frame, text="Tout revérifier, même si une validation identique existe",
                        variable=self.campaign_force).pack(anchor="w", pady=(0, 10))
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        self.campaign_button = self._button(actions, "▶  Lancer la campagne", self.run_campaign, style="Accent.TButton")
        self.campaign_button.pack(side="left", padx=(0, 7))
        self._button(actions, "Ouvrir le dernier rapport", self.open_campaign, always=True).pack(side="left")
        ttk.Label(frame, text="Une campagne occupe le jeu : ne lancez pas d'essai pendant ce temps. "
                              "Les quêtes connues du jeu : 1001-1022, 1101-1126, 1201-1220 (village), 2001…, 3001… ; "
                              "10001… et 11001… (guilde) ; 20001…, 21001…, 30001-30090 (entraînement, défis).",
                  style="Small.TLabel", wraplength=1000).pack(anchor="w", pady=(12, 0))
        ttk.Separator(frame).pack(fill="x", pady=16)
        ttk.Label(frame, text="Chasse pilotée · objectif vérifié", style="Title.TLabel").pack(anchor="w")
        self.hunt_goals = objectives()
        self.hunt_quest = tk.StringVar(value=next(iter(str(q) for q in self.hunt_goals), ""))
        self.hunt_seconds = tk.StringVar(value="600")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=8)
        combo = ttk.Combobox(row, textvariable=self.hunt_quest, state="readonly", width=10,
                             values=[str(q) for q in self.hunt_goals])
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", self._hunt_goal_changed)
        ttk.Label(row, text="Durée maximale (s)").pack(side="left", padx=(18, 8))
        ttk.Spinbox(row, from_=30, to=1200, textvariable=self.hunt_seconds, width=7).pack(side="left")
        self.hunt_button = self._button(row, "▶  Lancer la chasse", self.run_hunt, style="Accent.TButton")
        self.hunt_button.pack(side="left", padx=12)
        self.automation_stop = self._button(row, "■  Arrêter", self.stop, live=True, style="Danger.TButton")
        self.automation_stop.pack(side="left")
        ttk.Label(frame, textvariable=self.hunt_status, wraplength=1000).pack(anchor="w", pady=(3, 8))
        ttk.Label(frame, text="Essai neuf, meilleur équipement (Ukanlos Soul Hatchet, White Fatalis Z), vie, endurance et "
                              "tranchant tenus, monstres à 1 PV, attaques réelles. Les morts observées ne remplacent pas le compteur "
                              "du jeu ; la capture de fin reste à examiner pour confirmer une victoire.",
                  style="Small.TLabel", wraplength=1000).pack(anchor="w")
        self._hunt_goal_changed()

    def _hunt_goal_changed(self, _event=None):
        goal = self.hunt_goals.get(int(self.hunt_quest.get()))
        if goal:
            self.hunt_status.set(f"{goal['name']} · {goal.get('description', str(goal['required']))}")

    def run_hunt(self):
        if self.busy or self._running() or self.smoke:
            return
        try:
            quest = int(self.hunt_quest.get())
            seconds = int(self.hunt_seconds.get())
            if not 30 <= seconds <= 1200:
                raise ValueError("Durée de chasse : 30 à 1200 secondes.")
            seed = self.backend._current_seed()
            if not seed:
                raise ValueError("Préparez d'abord un point de départ.")
        except ValueError as error:
            self._input_error(error)
            return
        self.automation_cancel.clear()
        self.automation_active = True
        def done(result):
            self.automation_active = False
            self._hunt_progress(result)
            self._states()
            if result.get("run_id"):
                self.last_report = self.backend.data_dir / "runs" / result["run_id"] / "rapport.html"
        self._task("Chasse pilotée", lambda: hunt.hunt(self.backend, seed["id"], quest,
                   AUTOMATION_RTC_US, seconds, cancel=self.automation_cancel,
                   progress=lambda value: self.events.put(("hunt_progress", value))), done)

    def _hunt_progress(self, result):
        phases = {"booting": "Démarrage", "departing": "Départ", "loading": "Chargement",
                  "observing": "Observation", "navigating": "Déplacement", "combat": "Combat",
                  "results": "Écran de fin", "finished": "Terminé"}
        goal = result.get("objective", {})
        text = f"{phases.get(result.get('phase'), result.get('state', ''))} · zone {result.get('stage') or '—'}"
        counter = result.get("objective_progress")
        if counter:
            label = "objectif du jeu" if result.get("objective_progress_available") else "dernier relevé du jeu"
            text += f" · {label} : {counter['completed']}/{counter['required']}"
        text += f" · morts observées : {result.get('kills', 0)}"
        if not counter:
            text += f" ; objectif : {goal.get('required', '—')}"
        if result.get("phase") == "finished":
            states = {"cancelled": "Arrêt demandé", "no_route": "Aucun trajet accessible",
                      "stalled": "Arrêt : absence de progrès", "read_failed": "Lecture du jeu indisponible",
                      "start_failed": "Départ non atteint", "time_limit": "Durée maximale atteinte",
                      "runtime_stopped": "Le jeu s'est arrêté", "interrupted": "Essai interrompu",
                      "failed": "Erreur, consulter le rapport"}
            ended = {"quest_cleared": "Quête réussie selon le jeu",
                     "quest_failed": "Quête échouée selon le jeu",
                     "quest ended": f"Fin de quête (état {result.get('mission_state')})"}
            state = result.get("state")
            if state in ended:
                text += " · " + ended[state] + (" ; retour au village" if result.get("returned_to_village")
                                                 else " ; écrans de fin non quittés")
            else:
                text += " · " + states.get(state, "Essai arrêté")
        self.hunt_status.set(text)

    def run_campaign(self) -> None:
        if self.busy or self._running() or self.smoke:
            return
        try:
            quests = [int(q) for q in self.campaign_quests.get().split()]
            look = float(self.campaign_look.get())
            speed = int(self.campaign_speed.get())
            captures = CAMPAIGN_CAPTURES.get(self.campaign_captures.get())
            force = self.campaign_force.get()
            if not quests or any(q <= 0 for q in quests):
                raise ValueError("Indiquez au moins un numéro de quête positif.")
            if not 1 <= look <= 120:
                raise ValueError("Observation : de 1 à 120 secondes de jeu.")
            if speed not in atelier_pilot.SPEEDS or captures is None:
                raise ValueError("Vitesse : 1 à 8 ; captures essentielles ou détaillées.")
            seed = self.backend._current_seed()
            if not seed:
                raise ValueError("Préparez d'abord un point de départ.")
        except ValueError as error:
            self._input_error(error)
            return
        log = lambda message: self.events.put(("note", "Campagne : " + message))
        self.automation_cancel.clear()
        self.automation_active = True
        def done(out):
            self.automation_active = False
            self._states()
            self._report_ready(out / "campagne.html")
        self._task(f"Campagne de {len(quests)} quête(s)",
                   lambda: atelier_pilot.run_campaign(self.backend, quests, seed_id=seed["id"],
                       rtc_us=AUTOMATION_RTC_US, look_seconds=look, speed=speed, captures=captures,
                       force=force, log=log, cancel=self.automation_cancel), done)

    def open_campaign(self) -> None:
        folder = self.backend.data_dir / "campaigns"
        reports = sorted(folder.glob("*/campagne.html")) if folder.is_dir() else []
        if reports:
            self.open_path(reports[-1])
        else:
            self.footer_var.set("Aucune campagne pour l'instant.")

    def send_debug(self, line: str) -> None:
        """One developer command to the running trial; the game's answer lands in the journal."""
        def worker():
            try:
                self.backend.debug_command(line)
                self.events.put(("control", f"{line} (réponse dans le journal : [debug] …)"))
            except Exception as exc:
                self.events.put(("control_error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="atelier-debug").start()

    def start_quest(self) -> None:
        try:
            quest = int(self.quest_var.get())
        except ValueError:
            self.footer_var.set("Numéro de quête : un nombre (ex. 1001).")
            return
        def worker():
            try:
                self.backend.debug_command(f"quest start {quest}")
                self.events.put(("control", f"quest start {quest} (résultat dans le journal : [debug] started…)"))
            except Exception as exc:
                self.events.put(("control_error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="atelier-quest").start()

    def _scenarios(self) -> None:
        frame = self.scenario_tab
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="Transformer un parcours en essai rejouable", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        fields = ttk.Frame(frame)
        fields.grid(row=1, column=0, sticky="ew", padx=(0, 20), pady=(0, 10))
        ttk.Label(fields, text="Nom").pack(side="left")
        ttk.Entry(fields, textvariable=self.editor_name, width=24).pack(side="left", padx=(8, 12), fill="x", expand=True)
        ttk.Label(fields, text="Durée (s)").pack(side="left")
        ttk.Spinbox(fields, from_=1, to=1800, textvariable=self.editor_duration, width=6).pack(side="left", padx=(8, 0))
        editor_box = ttk.Frame(frame)
        editor_box.grid(row=2, column=0, sticky="nsew", padx=(0, 20))
        self.editor = tk.Text(editor_box, background=FIELD, foreground=FG, insertbackground=ACCENT,
                              borderwidth=0, padx=14, pady=12, font=("Consolas", 11), wrap="none", undo=True)
        self.editor.insert("1.0", "# Une action par ligne : image:commande\n# Exemple : Start après 10 secondes (à 60 images/s).\n600:pad start 12\n900:shot menu\n")
        editor_scroll = ttk.Scrollbar(editor_box, command=self.editor.yview)
        self.editor.configure(yscrollcommand=editor_scroll.set)
        editor_scroll.pack(side="right", fill="y")
        self.editor.pack(fill="both", expand=True)
        help_box = ttk.Frame(frame, padding=16, style="Panel.TFrame")
        help_box.grid(row=1, column=1, rowspan=2, sticky="nsew")
        ttk.Label(help_box, text="Un parcours, plusieurs rendus", style="Panel.TLabel", font=("Segoe UI Semibold", 12)).pack(anchor="w", pady=(0, 12))
        help_text = (
            "1. Lancez un essai dans Pilotage.\n"
            "2. Naviguez avec les boutons de l'atelier.\n"
            "3. Utilisez « Enregistrer les commandes ».\n"
            "4. Rejouez avec une autre variante, puis comparez.\n\n"
            "Seuls les boutons de l'atelier sont enregistrés, pas la manette physique ni le clavier du jeu.\n\n"
            "Syntaxe\n"
            "600:pad start 12\n"
            "720:pad b 12\n"
            "900:shot hud\n\n"
            "Le nombre à gauche est l'image de déclenchement. Un appui pad se relâche après sa durée en images.\n\n"
            "Les essais repartent des sauvegardes figées. Les temps de chargement peuvent varier.\n\n"
            "A/B automatique : rendu normal puis variante sélectionnée (Legacy32 si Normal). Arrêter interrompt la séquence.\n\n"
            "Quêtes, objets et triches : adaptation MHFU à venir."
        )
        ttk.Label(help_box, text=help_text, style="Panel.TLabel", wraplength=360, justify="left").pack(anchor="nw", fill="x")
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._button(actions, "Enregistrer le scénario", self.save_scenario, style="Accent.TButton").pack(side="left", padx=(0, 8))
        self._button(actions, "Charger le scénario sélectionné", self.load_scenario, always=True).pack(side="left", padx=(0, 8))
        self._button(actions, "Enregistrer les commandes de l'essai", self.record_scenario).pack(side="left")

    def _sessions(self) -> None:
        frame = self.sessions_tab
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        heading = ttk.Frame(frame)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(heading, text="Retrouver les preuves, comparer les variantes", style="Title.TLabel").pack(side="left")
        self._button(heading, "Actualiser", self.refresh_resources, always=True).pack(side="right")
        table = ttk.Frame(frame)
        table.grid(row=1, column=0, sticky="nsew")
        columns = ("time", "scenario", "variant", "state", "exit")
        self.history = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        for key, label, width in [("time", "Début", 165), ("scenario", "Scénario", 270),
                                  ("variant", "Rendu", 210), ("state", "État", 125), ("exit", "Code sortie", 85)]:
            self.history.heading(key, text=label)
            self.history.column(key, width=width, minwidth=65, stretch=key == "scenario")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.history.pack(fill="both", expand=True)
        self.history.bind("<<TreeviewSelect>>", self._history_selected)
        ttk.Label(frame, textvariable=self.session_hint, style="Small.TLabel", wraplength=1050).grid(row=2, column=0, sticky="w", pady=8)
        row = ttk.Frame(frame)
        row.grid(row=3, column=0, sticky="ew")
        self._button(row, "Dossier de la session", self.open_selected, always=True).pack(side="left", padx=(0, 8))
        self._button(row, "Choisir comme A", lambda: self.choose_compare("a"), always=True).pack(side="left", padx=(0, 8))
        self._button(row, "Choisir comme B", lambda: self.choose_compare("b"), always=True).pack(side="left")
        compare = ttk.Frame(frame, padding=14, style="Panel.TFrame")
        compare.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        compare.columnconfigure(1, weight=1)
        ttk.Label(compare, text="A", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.compare_a_combo = ttk.Combobox(compare, textvariable=self.compare_a, state="readonly")
        self.compare_a_combo.grid(row=0, column=1, sticky="ew")
        ttk.Label(compare, text="B", style="Panel.TLabel").grid(row=1, column=0, padx=(0, 8), pady=(8, 0))
        self.compare_b_combo = ttk.Combobox(compare, textvariable=self.compare_b, state="readonly")
        self.compare_b_combo.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        self._button(compare, "Comparer A / B", self.compare, style="Accent.TButton").grid(row=0, column=2, rowspan=2, padx=(14, 0))
        self._button(compare, "Dernier rapport", self.open_report, always=True).grid(row=0, column=3, rowspan=2, padx=(8, 0))
        ttk.Label(frame, text="La comparaison aide au diagnostic. Elle ne certifie pas à elle seule que le HUD est correct.",
                  style="Small.TLabel").grid(row=5, column=0, sticky="w", pady=(8, 0))

    def _event_log(self, text: str) -> None:
        self.local_events.append(f"[{time.strftime('%H:%M:%S')}] {text}")
        self.local_events = self.local_events[-40:]
        self._render_log()

    def _render_log(self) -> None:
        content = "\n".join(self.local_events)
        if self.last_log_text:
            content += "\n\n── Journaux de la session ──\n" + self.last_log_text
        old_scroll = self.logs.yview()
        self.logs.configure(state="normal")
        self.logs.delete("1.0", "end")
        self.logs.insert("1.0", content)
        if not old_scroll or old_scroll[1] >= 0.97:
            self.logs.see("end")
        self.logs.configure(state="disabled")

    def _active(self) -> dict | None:
        active = self.status_data.get("active")
        return active if isinstance(active, dict) else None

    def _running(self) -> bool:
        active = self._active()
        return bool(active and active.get("state", "running") in ("starting", "running", "stopping"))

    def _states(self) -> None:
        available = not self.busy and not self.smoke and not self.closing
        running = self._running()
        for button in self.mutable:
            button.configure(state="normal" if available else "disabled")
        for button in self.live_controls:
            button.configure(state="normal" if running and not self.smoke and not self.automation_active else "disabled")
        for button in (self.stop_button, self.automation_stop):
            button.configure(state="normal" if (running or self.automation_active) and not self.smoke else "disabled")
        for button in (self.hunt_button, self.campaign_button):
            button.configure(state="normal" if available and not running and self.status_data.get("prepared") else "disabled")
        self.start_button.configure(state="normal" if available and not running and self.status_data.get("prepared") else "disabled")
        self.checkpoint_button.configure(state="normal" if available and not running and self.status_data.get("prepared") else "disabled")

    def _task(self, label: str, function: Callable, done: Callable | None = None) -> None:
        if self.busy or self.smoke:
            return
        self.busy = True
        self.footer_var.set(label + "…")
        self.progress.start(12)
        self._states()
        self._event_log(label)
        def worker():
            try:
                result = function()
                self.events.put(("done", label, result, done))
            except Exception as exc:
                self.events.put(("error", label, str(exc)))
        threading.Thread(target=worker, daemon=True, name="atelier-action").start()

    def _drain(self) -> None:
        if self.closed.is_set():
            return
        for _ in range(50):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind in ("done", "error"):
                self.busy = False
                self.progress.stop()
                if kind == "error":
                    self.automation_active = False
                    self.footer_var.set("Action interrompue : " + event[2])
                    self._event_log(event[1] + " — ERREUR : " + event[2])
                    messagebox.showerror("Atelier MHFU", event[2], parent=self.root)
                else:
                    self.footer_var.set(event[1] + " : terminé.")
                    self._event_log(event[1] + " : terminé.")
                    if event[3]:
                        try:
                            event[3](event[2])
                        except Exception as exc:
                            self._event_log("Lecture du résultat : " + str(exc))
                self._states()
                if self.closed.is_set():
                    return
                self.refresh_status(reschedule=False)
                self.refresh_resources()
            elif kind == "status":
                self.status_busy = False
                self._show_status(event[1])
            elif kind == "status_error":
                self.status_busy = False
                self.status_var.set("État indisponible")
                self.footer_var.set("Lecture du projet : " + event[1])
            elif kind == "resources":
                self.history_busy = False
                self._show_resources(event[1], event[2])
            elif kind == "resources_error":
                self.history_busy = False
                self._event_log("Lecture des sessions : " + event[1])
            elif kind == "logs":
                if event[1] != self.last_log_key:
                    self.last_log_key = event[1]
                    self.last_log_text = event[2]
                    self._render_log()
            elif kind == "control_error":
                self._event_log("Commande refusée : " + event[1])
                self.footer_var.set("Commande refusée : " + event[1])
            elif kind == "note":
                self._event_log(event[1])
                self.footer_var.set(event[1])
            elif kind == "hunt_progress":
                self._hunt_progress(event[1])
            elif kind == "control":
                self._event_log("Commande envoyée : " + event[1])
                self.footer_var.set("Commande envoyée. Vérifiez son effet dans le jeu.")
            elif kind == "closed":
                self._finish_close()
                return
            elif kind == "close_error":
                self.closing = False
                self._states()
                messagebox.showerror("Fermeture interrompue", "L'essai n'a pas pu être arrêté : " + event[1], parent=self.root)
        self.root.after(70, self._drain)

    def refresh_status(self, reschedule: bool = True) -> None:
        if self.closed.is_set():
            return
        if not self.status_busy:
            self.status_busy = True
            def worker():
                try:
                    self.events.put(("status", self.backend.status()))
                except Exception as exc:
                    self.events.put(("status_error", str(exc)))
            threading.Thread(target=worker, daemon=True, name="atelier-status").start()
        if reschedule:
            self.root.after(1500, self.refresh_status)

    def _show_status(self, data: dict) -> None:
        previous_active = (self._active() or {}).get("id")
        self.status_data = data
        active = self._active()
        self.project_var.set(str(data.get("project") or self.backend.project))
        git = data.get("git") or {}
        branch = git.get("branch") or "branche inconnue"
        commit = str(git.get("commit") or git.get("head") or "")[:9]
        git_hint = f" · {branch}" + (f" · {commit}" if commit else "")
        if self._running():
            self.status_var.set("●  Essai en cours")
            self.details_var.set(f"Session {active.get('id', '')} · {active.get('variant', 'normal')}" + git_hint)
        elif data.get("prepared"):
            self.status_var.set("●  Atelier prêt")
            self.details_var.set("Données de test : " + str(data.get("data_dir", "")) + git_hint)
        else:
            self.status_var.set("○  Atelier à préparer")
            self.details_var.set("Préparez une copie de travail des données du jeu avant le premier essai." + git_hint)
        if previous_active != (active or {}).get("id"):
            if previous_active and not active:
                self._announce_end(previous_active)
            self.refresh_resources()
        self._states()

    def _announce_end(self, run_id: str) -> None:
        """Say why a run ended: a deadline must not look like a crash."""
        try:
            manifest = json.loads((Path(self.status_data.get("data_dir", "")) / "runs" / run_id / "manifest.json")
                                  .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        state, reason = manifest.get("state"), manifest.get("stop_reason") or manifest.get("runtime_stop")
        if reason == "échéance atteinte":
            text = f"Essai {run_id} terminé : durée maximale atteinte ({manifest.get('seconds')} s). Ce n'est pas un plantage."
        elif reason == "arrêt demandé":
            detail = f" ; code de sortie {manifest.get('exit_code')}" if state == "failed" else ""
            text = f"Essai {run_id} : arrêt demandé{detail}."
        elif state == "failed":
            text = f"Essai {run_id} arrêté par le jeu : {reason}. Utilisez « Signaler le bug »."
        else:
            text = f"Essai {run_id} terminé ({reason or 'fermeture normale'})."
        self._event_log(text)
        self.status_var.set("●  " + text[:90])

    def refresh_resources(self) -> None:
        if self.history_busy or self.closed.is_set():
            return
        self.history_busy = True
        def worker():
            try:
                self.events.put(("resources", self.backend.scenarios(), self.backend.runs()))
            except Exception as exc:
                self.events.put(("resources_error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="atelier-history").start()

    def _show_resources(self, scenarios: list[dict], runs: list[dict]) -> None:
        old_scenario = self.scenario_var.get()
        self.scenario_rows = scenarios
        labels = [scenario_label(s) for s in scenarios]
        self.scenario_combo.configure(values=labels)
        if old_scenario in labels:
            self.scenario_var.set(old_scenario)
        elif labels:
            self.scenario_combo.current(0)
            self._scenario_changed()
        selected = self.history.selection()
        self.history.delete(*self.history.get_children())
        self.run_rows = {}
        for row in runs:
            run_id = str(row.get("id", ""))
            if not run_id or run_id in self.run_rows:
                continue
            self.run_rows[run_id] = row
            variant = row.get("variant", "normal")
            variant_label = VARIANTS.get(variant, {}).get("label", variant)
            scenario = row.get("scenario", "—")
            if isinstance(scenario, dict):
                scenario = scenario_label(scenario)
            state = {"running": "En cours", "starting": "Démarrage", "stopped": "Arrêté", "completed": "Terminé", "finished": "Terminé", "failed": "Échec", "timeout": "Durée atteinte"}.get(row.get("state"), row.get("state", "—"))
            self.history.insert("", "end", iid=run_id, values=(short_time(row.get("started_at")), scenario,
                                variant_label, state, row.get("exit_code") if row.get("exit_code") is not None else "—"))
        if selected and selected[0] in self.run_rows:
            self.history.selection_set(selected[0])
        ids = list(self.run_rows)
        self.compare_a_combo.configure(values=ids)
        self.compare_b_combo.configure(values=ids)
        if not self.compare_a.get() and ids:
            self.compare_a.set(ids[0])
        if not self.compare_b.get() and len(ids) > 1:
            self.compare_b.set(ids[1])

    def _scenario(self) -> dict:
        index = self.scenario_combo.current()
        if index < 0 or index >= len(self.scenario_rows):
            raise ValueError("Choisissez un scénario avant de lancer l'essai.")
        return self.scenario_rows[index]

    def _scenario_changed(self, _event=None) -> None:
        try:
            scenario = self._scenario()
        except ValueError:
            return
        self.duration_var.set(str(scenario.get("seconds", 60)))
        hint = scenario.get("description") or scenario.get("name") or ""
        if scenario.get("requires_checkpoint"):
            hint += " · Nécessite des sauvegardes figées au point de départ."
        self.scenario_hint.set(hint)

    @staticmethod
    def _duration(value: str) -> int:
        try:
            result = int(value)
        except ValueError as exc:
            raise ValueError("La durée doit être un nombre entier de secondes.") from exc
        if not 1 <= result <= 1800:
            raise ValueError("Choisissez une durée comprise entre 1 et 1 800 secondes.")
        return result

    def _input_error(self, exc: Exception) -> None:
        messagebox.showerror("Vérifier les paramètres", str(exc), parent=self.root)

    def prepare(self) -> None:
        self._task("Préparation de l'atelier", lambda: self.backend.prepare())

    def build(self, tests: bool) -> None:
        self._task("Compilation et tests" if tests else "Compilation", lambda: self.backend.build(run_tests=tests),
                   self._build_result)

    def _build_result(self, result: dict) -> None:
        if isinstance(result, dict):
            path = result.get("log") or result.get("log_path")
            if path:
                self._event_log("Journal de compilation : " + str(path))

    def start(self) -> None:
        try:
            scenario = dict(self._scenario())
            duration = self._duration(self.duration_var.get())
            index = self.variant_combo.current()
            variant = self.variant_keys[max(index, 0)]
        except (ValueError, IndexError) as exc:
            self._input_error(exc)
            return
        self.last_log_text = ""
        self.last_log_key = ""
        self._task("Lancement de l'essai", lambda: self.backend.start(scenario, variant, duration),
                   lambda run_id: self._event_log("Session : " + str(run_id)))

    def stop(self) -> None:
        # Stop is independent of the long build worker so it remains reachable.
        self.footer_var.set("Arrêt de l'essai demandé…")
        self.automation_cancel.set()
        def worker():
            try:
                self.backend.stop()
                self.events.put(("control", "arrêt de l'essai"))
            except Exception as exc:
                self.events.put(("control_error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="atelier-stop").start()

    def auto_compare(self) -> None:
        try:
            scenario = dict(self._scenario())
            seconds = self._duration(self.duration_var.get())
            index = self.variant_combo.current()
            variant = self.variant_keys[max(index, 0)]
            if variant == "normal":
                variant = "legacy32"
        except (ValueError, IndexError) as exc:
            self._input_error(exc)
            return
        self._task("Essais A/B : normal puis " + VARIANTS.get(variant, {}).get("label", variant),
                   lambda: self.backend.compare_variants(scenario, first="normal", second=variant, seconds=seconds),
                   self._report_ready)

    def send_control(self, command: str) -> None:
        if self.smoke or not self._running():
            return
        def worker():
            try:
                self.backend.command(command)
                self.events.put(("control", command))
            except Exception as exc:
                self.events.put(("control_error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="atelier-control").start()

    def bug_report(self) -> None:
        title = self.bug_title.get().strip()
        self._task("Création du rapport de bug", lambda: self.backend.bug_report(title), self._report_ready)

    def _report_ready(self, path: Path) -> None:
        self.last_report = Path(path)
        self._event_log("Rapport disponible : " + str(path))
        self.open_path(path)

    def checkpoint(self) -> None:
        self._task("Création du point de départ", lambda: self.backend.checkpoint(),
                   lambda path: self._event_log("Sauvegardes figées : " + str(path)))

    def load_scenario(self) -> None:
        try:
            scenario = self._scenario()
        except ValueError as exc:
            self._input_error(exc)
            return
        script = str(scenario.get("script", ""))
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", script)
        self.editor_name.set(str(scenario.get("name") or scenario_label(scenario)))
        self.editor_duration.set(str(scenario.get("seconds", 60)))
        self.notebook.select(self.scenario_tab)

    def save_scenario(self) -> None:
        name = self.editor_name.get().strip()
        script = self.editor.get("1.0", "end-1c")
        try:
            if not name:
                raise ValueError("Donnez un nom au scénario.")
            seconds = self._duration(self.editor_duration.get())
        except ValueError as exc:
            self._input_error(exc)
            return
        self._task("Enregistrement du scénario", lambda: self.backend.save_scenario(name, script, seconds),
                   lambda path: self._event_log("Scénario enregistré : " + str(path)))

    def record_scenario(self) -> None:
        name = self.editor_name.get().strip() or "Parcours enregistré"
        self._task("Enregistrement des commandes de l'essai", lambda: self.backend.record_scenario(name),
                   lambda path: self._event_log("Parcours enregistré : " + str(path)))

    def _selected(self) -> dict | None:
        selection = self.history.selection()
        return self.run_rows.get(selection[0]) if selection else None

    def _history_selected(self, _event=None) -> None:
        row = self._selected()
        if row:
            self.session_hint.set(str(row.get("id", "")) + " · " + str(row.get("summary") or row.get("run_dir") or "Journaux et captures disponibles dans le dossier de la session."))

    def choose_compare(self, slot: str) -> None:
        row = self._selected()
        if not row:
            self.footer_var.set("Sélectionnez une session dans la liste.")
            return
        (self.compare_a if slot == "a" else self.compare_b).set(str(row["id"]))

    def compare(self) -> None:
        first, second = self.compare_a.get(), self.compare_b.get()
        if not first or not second or first == second:
            self._input_error(ValueError("Choisissez deux sessions différentes pour la comparaison."))
            return
        self._task("Comparaison des sessions", lambda: self.backend.compare(first, second), self._report_ready)

    def open_path(self, path: Path | str) -> None:
        target = Path(path)
        if not target.exists():
            self.footer_var.set("Le fichier ou dossier n'existe pas : " + str(target))
            return
        try:
            os.startfile(str(target.resolve()))
        except OSError as exc:
            self.footer_var.set("Ouverture impossible : " + str(exc))

    def _run_dir(self, row: dict) -> Path:
        if row.get("run_dir"):
            return Path(row["run_dir"])
        data_dir = self.status_data.get("data_dir") or (Path(self.backend.project) / "atelier-psprecomp")
        return Path(data_dir) / "runs" / str(row["id"])

    def open_active(self) -> None:
        active = self._active()
        if active:
            self.open_path(self._run_dir(active))
        elif self.run_rows:
            self.open_path(self._run_dir(next(iter(self.run_rows.values()))))
        else:
            self.footer_var.set("Aucun essai n'a encore été créé.")

    def open_selected(self) -> None:
        row = self._selected()
        if row:
            self.open_path(self._run_dir(row))
        else:
            self.footer_var.set("Sélectionnez une session dans la liste.")

    def open_report(self) -> None:
        if self.last_report:
            self.open_path(self.last_report)
        else:
            self.footer_var.set("Créez un rapport de bug ou une comparaison pour l'ouvrir ici.")

    @staticmethod
    def _tail(path: Path, limit: int = 48_000) -> str:
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - limit))
                data = stream.read(limit)
            text = data.decode("utf-8", errors="replace")
            if size > limit:
                text = "[Fin du journal uniquement]\n" + text.partition("\n")[2]
            return text
        except OSError:
            return ""

    def _log_tick(self) -> None:
        if self.closed.is_set():
            return
        row = self._active()
        if row is None and self.run_rows:
            row = next(iter(self.run_rows.values()))
        if row:
            run_dir = self._run_dir(row)
            run_id = str(row.get("id", ""))
            def worker():
                parts = []
                stamps = []
                for name in ("stdout.txt", "stderr.txt"):
                    file = run_dir / name
                    tail = self._tail(file)
                    if tail:
                        parts.append(f"[{name}]\n{tail}")
                    try:
                        info = file.stat()
                        stamps.append((name, info.st_size, info.st_mtime_ns))
                    except OSError:
                        pass
                self.events.put(("logs", str((run_id, stamps)), "\n".join(parts)))
            threading.Thread(target=worker, daemon=True, name="atelier-logs").start()
        self.root.after(1300, self._log_tick)

    def close(self) -> None:
        self.automation_cancel.set()
        if self.smoke:
            self.closed.set()
            self.root.destroy()
            return
        if self.closing:
            return
        self.closing = True
        self.footer_var.set("Arrêt de l'essai et fermeture de l'atelier…")
        self._states()
        def worker():
            try:
                self.backend.stop()
                self.events.put(("closed",))
            except Exception as exc:
                self.events.put(("close_error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="atelier-close").start()

    def _finish_close(self) -> None:
        self.closed.set()
        self.root.destroy()


def window_bitmap(root: tk.Tk, destination: Path) -> bool:
    """Capture only this tool's window using PrintWindow, never the desktop."""
    if os.name != "nt":
        return False
    import struct
    from ctypes import wintypes
    # Private DLL objects with full prototypes: 64-bit handles must not be
    # truncated to C int (default ctypes conversion).
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    user32.GetParent.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    handle = user32.GetParent(root.winfo_id()) or root.winfo_id()
    rect = wintypes.RECT()
    user32.GetWindowRect(handle, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    source = user32.GetDC(handle)
    memory = gdi32.CreateCompatibleDC(source)
    bitmap = gdi32.CreateCompatibleBitmap(source, width, height)
    previous = gdi32.SelectObject(memory, bitmap)
    try:
        if not user32.PrintWindow(handle, memory, 2):
            return False
        gdi32.SelectObject(memory, previous)
        header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        info = ctypes.create_string_buffer(header)
        if not gdi32.GetDIBits(memory, bitmap, 0, height, pixels, info, 0):
            return False
        destination.write_bytes(struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54) + header + pixels.raw)
        return True
    finally:
        gdi32.SelectObject(memory, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(handle, source)


def main() -> None:
    parser = argparse.ArgumentParser(description="Atelier local de développement MHFU")
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke-ui", type=Path, metavar="LAYOUT_JSON", help="Vérifier l'interface sans action et fermer après export de son agencement.")
    parser.add_argument("--smoke-tab", type=int, default=0, choices=range(4))
    args = parser.parse_args()
    root = tk.Tk()
    backend = Workshop(args.project, data_dir=args.data_dir)
    ui = WorkshopUI(root, backend, smoke=bool(args.smoke_ui))
    if args.smoke_ui:
        ui.notebook.select(args.smoke_tab)
        def export():
            root.update_idletasks()
            widgets = []
            def visit(widget):
                widgets.append({"class": widget.winfo_class(), "x": widget.winfo_rootx(), "y": widget.winfo_rooty(),
                                "width": widget.winfo_width(), "height": widget.winfo_height(), "mapped": bool(widget.winfo_ismapped())})
                for child in widget.winfo_children():
                    visit(child)
            visit(root)
            args.smoke_ui.parent.mkdir(parents=True, exist_ok=True)
            screenshot = args.smoke_ui.with_suffix(".bmp")
            captured = window_bitmap(root, screenshot)
            payload = {"window": root.title(), "geometry": root.geometry(), "tabs": len(ui.notebook.tabs()),
                       "widgets": widgets, "status": ui.status_var.get(), "screenshot": str(screenshot) if captured else None}
            args.smoke_ui.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            ui.close()
        root.after(2200, export)
    try:
        root.mainloop()
    finally:
        ui.automation_cancel.set()
        backend.stop()


if __name__ == "__main__":
    main()
