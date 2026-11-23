#!/usr/bin/env python3
"""Atelier MHFU : interface locale et commandes pour la boucle de vérification."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def load_core() -> Any:
    """Le moteur se trouve à côté de ce lanceur, dans tools/."""
    try:
        return importlib.import_module("atelier_core")
    except ModuleNotFoundError as exc:
        if exc.name != "atelier_core":
            raise
        raise RuntimeError(
            "Le moteur atelier_core.py est introuvable. Placez les deux fichiers "
            "dans le même dossier tools."
        ) from exc


class AtelierApp:
    BG = "#f1f4f7"
    WHITE = "#ffffff"
    INK = "#203046"
    MUTED = "#617084"
    BLUE = "#215ed1"
    GREEN = "#087f67"
    BORDER = "#dce3ec"

    def __init__(self, root: Any, core: Any | None = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = root
        self.core = core if core is not None else load_core()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.closing = False
        self.stop_requested = threading.Event()
        self.controls: list[tuple[Any, str]] = []
        self.worker: threading.Thread | None = None
        self.reference_ids: dict[str, str | None] = {"Dernière référence": None}

        root.title("Atelier MHFU — Vérifier et mesurer")
        root.geometry("1080x860")
        root.minsize(940, 850)
        root.configure(bg=self.BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.option_add("*Font", ("Segoe UI", 10))
        self._styles()

        shell = tk.Frame(root, bg=self.BG, padx=30, pady=16)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=self.BG)
        header.pack(fill="x")
        self._label(header, "ATELIER MHFU", font=("Segoe UI", 10, "bold"), fg=self.BLUE).pack(anchor="w")
        self._label(header, "Une idée. Une mesure. Une décision.", font=("Segoe UI", 24, "bold")).pack(anchor="w", pady=(5, 5))
        self._label(header, "Figez une référence avant un changement, puis vérifiez et comparez.", fg=self.MUTED).pack(anchor="w")

        cards = tk.Frame(shell, bg=self.BG)
        cards.pack(fill="x", pady=(22, 18))
        cards.columnconfigure((0, 1, 2), weight=1, uniform="cards")
        self._card(cards, 0, "01", "Essai rapide", "Vérifier la partie modifiée\net obtenir un premier bilan.", "Lancer l’essai", self.run_quick)
        self._card(cards, 1, "02", "Comparer la vitesse", "Mesurer le changement face\nà une référence conservée.", "Mesurer", self.run_compare)
        self._card(cards, 2, "03", "Valider avant commit", "Lancer la vérification complète\navant de conserver le correctif.", "Valider", self.run_validate, green=True)

        options = tk.Frame(shell, bg=self.WHITE, padx=18, pady=14, highlightthickness=1, highlightbackground=self.BORDER)
        options.pack(fill="x")
        options.columnconfigure((0, 1, 2, 3), weight=1, uniform="options")
        self.group = tk.StringVar(value="Automatique")
        self.reference = tk.StringVar(value="Dernière référence")
        self.duration = tk.StringVar(value="Courte")
        self.threads = tk.StringVar(value="Images stables")
        self.force = tk.BooleanVar(value=False)
        self.group_combo = self._combo(options, 0, "Partie à vérifier", self.group, ["Automatique", "Rendu", "Processeur", "Fenêtre"])
        self.reference_combo = self._combo(options, 1, "Référence de comparaison", self.reference, ["Dernière référence"])
        self._combo(options, 2, "Durée du scénario", self.duration, ["Courte", "Complète", "Démarrage"])
        self._combo(options, 3, "Mode de mesure", self.threads, ["Images stables", "Jeu en direct"])
        self._label(options, "La mesure « Démarrage » vérifie le lancement ; elle ne représente pas la fluidité en jeu.", bg=self.WHITE, fg=self.MUTED, font=("Segoe UI", 9)).grid(row=2, column=0, columnspan=4, sticky="w", pady=(9, 0))
        force = ttk.Checkbutton(options, text="Recalculer même si un résultat est déjà disponible", variable=self.force, style="White.TCheckbutton")
        force.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.controls.append((force, "normal"))

        actions = tk.Frame(shell, bg=self.BG)
        actions.pack(fill="x", pady=(14, 18))
        self._button(actions, "Figer une référence", self.run_freeze, "Secondary.TButton").pack(side="left")
        self._button(actions, "Ouvrir les résultats", self.open_results, "Secondary.TButton").pack(side="left", padx=9)
        self.stop_button = ttk.Button(actions, text="Arrêter", command=self.stop, style="Stop.TButton", state="disabled")
        self.stop_button.pack(side="right")

        status = tk.Frame(shell, bg=self.WHITE, padx=18, pady=13, highlightthickness=1, highlightbackground=self.BORDER)
        status.pack(fill="x")
        self.status_badge = tk.Label(status, text="PRÊT", bg="#e9eef6", fg=self.MUTED, padx=9, pady=4, font=("Segoe UI", 9, "bold"))
        self.status_badge.pack(anchor="w")
        self.summary = tk.Label(status, text="Choisissez une action pour commencer.", bg=self.WHITE, fg=self.INK, anchor="w", justify="left", wraplength=940, font=("Segoe UI", 11))
        self.summary.pack(fill="x", pady=(8, 5))
        self.progress = ttk.Progressbar(status, mode="indeterminate", style="Atelier.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(4, 0))

        log_header = tk.Frame(shell, bg=self.BG)
        log_header.pack(fill="x", pady=(18, 7))
        self._label(log_header, "DÉROULEMENT", fg=self.MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
        self._label(log_header, "Les détails restent disponibles dans les résultats.", fg=self.MUTED, font=("Segoe UI", 9)).pack(side="right")
        log_frame = tk.Frame(shell, bg=self.WHITE, highlightthickness=1, highlightbackground=self.BORDER)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, bg=self.WHITE, fg=self.INK, relief="flat", padx=13, pady=10, height=3, wrap="word", state="disabled", font=("Consolas", 9), selectbackground="#d7e4fd")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self._label(shell, "L’atelier prépare les preuves ; vous décidez ensuite de conserver ou d’annuler le changement.", fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 0))
        # Reserve space for the footer and let the journal absorb window resizing.
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(6, weight=1, minsize=24)
        layout = [(child, child.pack_info().get('pady', 0)) for child in shell.winfo_children()]
        for child, spacing in layout:
            child.pack_forget()
        for row, (child, spacing) in enumerate(layout):
            child.grid(row=row, column=0, sticky='nsew' if row == 6 else 'ew', pady=spacing)
        self.refresh_references()
        self.root.after(80, self._poll)

    def _styles(self) -> None:
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Primary.TButton", background=self.BLUE, foreground="white", borderwidth=0, padding=(15, 10), font=("Segoe UI", 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#b9c9e6"), ("active", "#164cac")], foreground=[("disabled", "#f4f7fc")])
        style.configure("Green.TButton", background=self.GREEN, foreground="white", borderwidth=0, padding=(15, 10), font=("Segoe UI", 10, "bold"))
        style.map("Green.TButton", background=[("disabled", "#a8cfc6"), ("active", "#05634f")])
        style.configure("Secondary.TButton", background="#e5ebf3", foreground=self.INK, borderwidth=0, padding=(12, 8))
        style.map("Secondary.TButton", background=[("active", "#d9e2ee"), ("disabled", "#ebeff4")], foreground=[("disabled", "#9aa5b3")])
        style.configure("Stop.TButton", background="#f7e5e3", foreground="#a2382e", borderwidth=0, padding=(15, 8))
        style.map("Stop.TButton", background=[("disabled", "#ebeff4"), ("active", "#efd3cf")], foreground=[("disabled", "#9aa5b3")])
        style.configure("TCombobox", padding=6, fieldbackground=self.WHITE, bordercolor=self.BORDER, arrowcolor=self.MUTED)
        style.configure("White.TCheckbutton", background=self.WHITE, foreground=self.MUTED, font=("Segoe UI", 9))
        style.map("White.TCheckbutton", background=[("active", self.WHITE)])
        style.configure("Atelier.Horizontal.TProgressbar", troughcolor="#edf1f7", background=self.BLUE, borderwidth=0, thickness=4)

    def _label(self, parent: Any, text: str, **kwargs: Any) -> Any:
        return self.tk.Label(parent, text=text, bg=kwargs.pop("bg", self.BG), fg=kwargs.pop("fg", self.INK), **kwargs)

    def _button(self, parent: Any, text: str, command: Callable[[], None], style: str) -> Any:
        button = self.ttk.Button(parent, text=text, command=command, style=style)
        self.controls.append((button, "normal"))
        return button

    def _card(self, parent: Any, column: int, number: str, title: str, description: str, action: str, command: Callable[[], None], green: bool = False) -> None:
        card = self.tk.Frame(parent, bg=self.WHITE, padx=18, pady=12, highlightbackground=self.BORDER, highlightthickness=1)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 7, 0 if column == 2 else 7))
        self._label(card, number, bg=self.WHITE, fg=self.GREEN if green else self.BLUE, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self._label(card, title, bg=self.WHITE, font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(8, 7))
        self._label(card, description, bg=self.WHITE, fg=self.MUTED, justify="left", anchor="w", font=("Segoe UI", 10)).pack(anchor="w")
        self._button(card, action, command, "Green.TButton" if green else "Primary.TButton").pack(fill="x", pady=(18, 0))

    def _combo(self, parent: Any, column: int, label: str, variable: Any, values: list[str]) -> Any:
        self._label(parent, label, bg=self.WHITE, fg=self.MUTED, font=("Segoe UI", 9)).grid(row=0, column=column, sticky="w", padx=(0, 11))
        combo = self.ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=20)
        combo.grid(row=1, column=column, sticky="ew", padx=(0, 11 if column < 3 else 0), pady=(5, 0))
        self.controls.append((combo, "readonly"))
        return combo

    def refresh_references(self) -> None:
        selected = self.reference_ids.get(self.reference.get())
        labels: dict[str, str | None] = {"Dernière référence": None}
        try:
            for item in self.core.references():
                identity = str(item["id"])
                created = datetime.fromisoformat(str(item['created'])).astimezone()
                labels[f'{created:%d/%m %H:%M} · {identity[-6:]}'] = identity
        except Exception as exc:
            self._append(f"Références : {exc}")
        self.reference_ids = labels
        self.reference_combo.configure(values=list(labels))
        self.reference.set(next((label for label, identity in labels.items() if identity == selected), "Dernière référence"))

    def _append(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {message.rstrip()}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for control, normal_state in self.controls:
            control.configure(state="disabled" if busy else normal_state)
        self.stop_button.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _start(self, title: str, function: Callable[..., dict[str, Any]], **kwargs: Any) -> None:
        if self.busy or self.closing:
            return
        self.stop_requested.clear()
        self._set_busy(True)
        self.status_badge.configure(text="EN COURS", bg="#e4edff", fg=self.BLUE)
        self.summary.configure(text=title, fg=self.INK)
        self._append(title)

        def emit(*parts: Any) -> None:
            self.events.put(("log", " ".join(str(part) for part in parts)))

        def work() -> None:
            try:
                if self.stop_requested.is_set():
                    result = {"status": "incomplete", "summary": "Action arrêtée avant son démarrage."}
                else:
                    result = function(emit=emit, **kwargs)
                    if self.stop_requested.is_set():
                        result = dict(result) if isinstance(result, dict) else {}
                        result.update(status="incomplete", summary="Arrêt demandé pendant l’action. Consultez les résultats pour connaître son état final.")
                if not isinstance(result, dict):
                    result = {"status": "incomplete", "summary": "Le moteur n’a pas fourni de bilan exploitable. Consultez le déroulement."}
                self.events.put(("result", result))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__} : {exc}"))

        self.worker = threading.Thread(target=work, name="atelier-job", daemon=True)
        self.worker.start()

    def run_quick(self) -> None:
        groups = {"Automatique": "auto", "Rendu": "renderer", "Processeur": "cpu", "Fenêtre": "window"}
        self._start("Essai rapide en cours…", self.core.quick, group=groups[self.group.get()], force=self.force.get())

    def run_compare(self) -> None:
        durations = {"Courte": 200000, "Complète": 500000, "Démarrage": 10000}
        modes = {"Images stables": 1, "Jeu en direct": 16}
        self._start("Comparaison avec la référence en cours…", self.core.compare, reference=self.reference_ids.get(self.reference.get()), dispatches=durations[self.duration.get()], threads=modes[self.threads.get()], force=self.force.get())

    def run_validate(self) -> None:
        self._start("Vérification complète en cours…", self.core.validate, force=self.force.get())

    def run_freeze(self) -> None:
        self._start("Conservation d’une nouvelle référence…", self.core.freeze)

    def open_results(self) -> None:
        folder = Path(self.core.ROOT) / "builds" / "atelier"
        try:
            if not folder.is_dir():
                self.summary.configure(text="Aucun résultat pour le moment. Lancez un essai ou une mesure.", fg=self.MUTED)
                return
            report = folder/'dernier-bilan.txt'
            os.startfile(str(report if report.is_file() else folder))  # type: ignore[attr-defined]
        except OSError as exc:
            self._append(f"Impossible d’ouvrir les résultats : {exc}")

    def stop(self) -> None:
        if not self.busy:
            return
        self.stop_requested.set()
        self.stop_button.configure(state="disabled")
        self.status_badge.configure(text="ARRÊT DEMANDÉ", bg="#fff0d9", fg="#98610b")
        self.summary.configure(text="Arrêt demandé. L’atelier attend la fin du processus en cours.", fg=self.INK)
        self._append("Arrêt demandé.")
        self._cancel_engine()

    def _cancel_engine(self) -> None:
        try:
            self.core.cancel()
        except Exception as exc:
            self._append(f"Demande d’arrêt : {exc}")

    def _finish(self, result: dict[str, Any]) -> None:
        self._set_busy(False)
        status = str(result.get("status", "incomplete"))
        appearance = {
            "passed": ("TERMINÉ", "#def3ec", self.GREEN),
            "cached": ("RÉSULTAT RÉUTILISÉ", "#def3ec", self.GREEN),
            "failed": ("ÉCHEC", "#fae4e1", "#a2382e"),
            "incomplete": ("INCOMPLET", "#fff0d9", "#98610b"),
        }
        label, background, color = appearance.get(status, ("À EXAMINER", "#fff0d9", "#98610b"))
        summary = str(result.get("summary") or "Le moteur n’a pas fourni de résumé. Consultez les résultats.")
        self.status_badge.configure(text=label, bg=background, fg=color)
        self.summary.configure(text=summary, fg=self.INK)
        self._append(f"{label} — {summary}")
        self.refresh_references()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append(str(payload))
                elif kind == "result":
                    self._finish(payload)
                elif kind == "error":
                    self._finish({"status": "failed", "summary": str(payload)})
        except queue.Empty:
            pass
        if self.busy and self.stop_requested.is_set():
            self._cancel_engine()
        if self.closing and not self.busy:
            self.root.destroy()
            return
        self.root.after(80, self._poll)

    def close(self) -> None:
        if self.busy:
            self.closing = True
            self.stop()
            self.summary.configure(text="Arrêt demandé avant la fermeture…")
        else:
            self.root.destroy()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("la valeur doit être supérieure à zéro")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atelier MHFU : vérifier, mesurer et préparer une décision.")
    parser.add_argument("command", nargs="?", default="gui", choices=["quick", "freeze", "compare", "validate", "gui"])
    parser.add_argument("--group", default="auto", choices=["auto", "renderer", "cpu", "window"])
    parser.add_argument("--reference", help="identifiant de référence ; dernière référence par défaut")
    parser.add_argument("--source", help="exécutable à conserver comme référence")
    parser.add_argument("--candidate", help="exécutable à mesurer")
    parser.add_argument("--dispatches", type=positive_int, default=200000)
    parser.add_argument("--threads", type=int, choices=[1, 16], default=1)
    parser.add_argument("--force", action="store_true", help="recalculer les résultats déjà disponibles")
    args = parser.parse_args(argv)
    try:
        core = load_core()
        if args.command == "gui":
            import tkinter as tk
            root = tk.Tk()
            try:
                AtelierApp(root, core=core)
                root.mainloop()
            finally:
                try:
                    root.destroy()
                except tk.TclError:
                    pass
            return 0
        if args.command == "quick":
            result = core.quick(group=args.group, force=args.force, emit=print)
        elif args.command == "freeze":
            result = core.freeze(source=args.source, emit=print)
        elif args.command == "compare":
            result = core.compare(reference=args.reference, dispatches=args.dispatches, threads=args.threads, candidate=args.candidate, force=args.force, emit=print)
        else:
            result = core.validate(force=args.force, emit=print)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        status = result.get("status") if isinstance(result, dict) else "incomplete"
        return 0 if status in {"passed", "cached"} else 2 if status == "incomplete" else 1
    except KeyboardInterrupt:
        try:
            core.cancel()
        except Exception:
            pass
        print("Action interrompue.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Atelier MHFU : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
