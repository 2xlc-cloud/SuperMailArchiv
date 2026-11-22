"""Tab "Datenbank": overview, check and maintenance, backup and restore, switching to another archive file.

Mixed into MailArchive (app.py); uses its path, settings, events queue, status line and restart().
"""
import os
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import maintenance
from app_info import APP_NAME
from archive import initialize

ARCHIVE_TYPES = [("SuperMailBackup-Archiv", "*.sqlite3"), ("Alle Dateien", "*.*")]


def size_text(size):
    if size is None:
        return "–"
    if size < 1024 ** 2:
        return f"{size / 1024:.0f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB".replace(".", ",")
    return f"{size / 1024 ** 3:.2f} GB".replace(".", ",")


def day_text(timestamp):
    try:
        return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y") if timestamp else "–"
    except (ValueError, OverflowError, OSError):
        return "–"


class DatabaseTab:
    INFO_ROWS = (("size", "Dateigröße"), ("reusable", "Davon ungenutzt"), ("mails", "Mails"),
                 ("accounts", "Konten"), ("folders", "Ordner mit Mails"), ("period", "Zeitraum"),
                 ("index", "Suchindex"), ("disk", "Frei auf dem Laufwerk"), ("format", "Format"))

    def default_archive(self):
        return self.data / "archiv.sqlite3"

    def choose_archive_path(self):
        """Archive file to use: the one chosen in the tab Datenbank, otherwise the standard archive."""
        chosen = self.settings["database"]
        if not chosen:
            return self.default_archive()
        path = Path(chosen)
        if path.exists():
            return path
        if messagebox.askyesno(APP_NAME, f"Das gewählte Archiv wurde nicht gefunden:\n{path}\n\n(Ist das Laufwerk "
                                         "angeschlossen?)\n\nStattdessen das Standardarchiv öffnen?",
                               parent=self.root):
            self.save_setting("database", "")
            return self.default_archive()
        raise OSError(f"Archiv nicht gefunden: {path}")

    def apply_pending_restore(self):
        """A restore chosen in the tab Datenbank runs at program start, before anything uses the archive."""
        source = self.settings["restore_from"]
        if not source:
            return None
        self.save_setting("restore_from", "")  # Never try again on every start.
        try:
            kept = maintenance.restore_database(self.path, Path(source))
        except (OSError, ValueError, maintenance.sqlite3.Error) as error:
            return ("error", f"Die Sicherung wurde nicht eingespielt; das Archiv ist unverändert.\n\n{error}")
        note = f"\n\nDas bisherige Archiv wurde aufbewahrt als:\n{kept}" if kept else ""
        return ("info", f"Die Sicherung wurde eingespielt:\n{source}{note}")

    # --- Layout ---------------------------------------------------------------------------------------------

    def build_database(self):
        page = self.database_tab
        self.maintenance_running = False
        self.maintenance_cancel = threading.Event()
        self.maintenance_buttons = []

        place = ttk.LabelFrame(page, text=" Archivdatei ", padding=10)
        place.pack(fill="x")
        self.archive_location = tk.StringVar(value=str(self.path))
        ttk.Entry(place, textvariable=self.archive_location, state="readonly").pack(fill="x")
        buttons = ttk.Frame(place)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Im Explorer zeigen", command=self.show_archive_file).pack(side="left")
        for text, command in (("Anderes Archiv öffnen …", self.open_other_archive),
                              ("Neues Archiv anlegen …", self.create_archive)):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(side="left", padx=(6, 0))
            self.maintenance_buttons.append(button)
        self.default_button = ttk.Button(buttons, text="Standardarchiv verwenden",
                                         command=lambda: self.switch_archive(self.default_archive()))
        if self.path != self.default_archive():
            self.default_button.pack(side="left", padx=(6, 0))
            self.maintenance_buttons.append(self.default_button)
        hint = ttk.Label(place, style="Muted.TLabel", justify="left",
                         text="Beim Wechsel startet das Programm neu. Konten, gespeicherte Passwörter und die "
                              "Ordnerauswahl gehören zum jeweiligen Archiv.")
        hint.pack(anchor="w", fill="x", pady=(6, 0))
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=event.width))

        row = ttk.Frame(page)
        row.pack(fill="x", pady=(10, 0))
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)
        overview = ttk.LabelFrame(row, text=" Übersicht ", padding=10)
        overview.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        overview.columnconfigure(1, weight=1)
        self.info_labels = {}
        for line, (key, label) in enumerate(self.INFO_ROWS):
            ttk.Label(overview, text=label, style="Muted.TLabel").grid(row=line, column=0, sticky="w", padx=(0, 12))
            self.info_labels[key] = ttk.Label(overview, text="…")
            self.info_labels[key].grid(row=line, column=1, sticky="w")

        backup = ttk.LabelFrame(row, text=" Sicherung ", padding=10)
        backup.grid(row=0, column=1, sticky="nsew")
        for text, command in (("Sicherung erstellen …", self.create_backup),
                              ("Sicherung wiederherstellen …", self.restore_backup)):
            button = ttk.Button(backup, text=text, command=command, width=28)
            button.pack(anchor="w", pady=(0, 6))
            self.maintenance_buttons.append(button)
        self.last_backup_label = ttk.Label(backup, style="Muted.TLabel", justify="left")
        self.last_backup_label.pack(anchor="w", fill="x", pady=(4, 0))
        note = ttk.Label(backup, style="Muted.TLabel", justify="left",
                         text="Die Sicherung ist eine vollständige, geprüfte Kopie. Während eines Abrufs ist keine "
                              "Sicherung oder Wartung möglich (und umgekehrt). Gespeicherte Passwörter lassen sich "
                              "nur unter diesem Windows-Benutzer wieder entschlüsseln.")
        note.pack(anchor="w", fill="x", pady=(8, 0))
        for label in (self.last_backup_label, note):
            label.bind("<Configure>", lambda event, widget=label: widget.configure(wraplength=event.width))
        self.update_last_backup()

        care = ttk.LabelFrame(page, text=" Prüfen und warten ", padding=10)
        care.pack(fill="both", expand=True, pady=(10, 0))
        buttons = ttk.Frame(care)
        buttons.pack(fill="x")
        for text, command in (("Schnell prüfen", lambda: self.check_archive(False)),
                              ("Vollständig prüfen", lambda: self.check_archive(True)),
                              ("Komprimieren", self.compact_archive),
                              ("Suchindex neu aufbauen", self.rebuild_index)):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(side="left", padx=(0, 6))
            self.maintenance_buttons.append(button)
        self.maintenance_stop = ttk.Button(buttons, text="Abbrechen", state="disabled",
                                           command=self.cancel_maintenance)
        self.maintenance_stop.pack(side="right")
        self.maintenance_info = ttk.Label(care, style="Muted.TLabel")
        self.maintenance_info.pack(anchor="w", pady=(8, 4))
        self.check_table = ttk.Treeview(care, columns=("check", "result"), show="headings", height=5,
                                        selectmode="none")
        self.check_table.heading("check", text="Prüfung", anchor="w")
        self.check_table.heading("result", text="Ergebnis", anchor="w")
        self.check_table.column("check", width=260, stretch=False)
        self.check_table.column("result", width=400)
        scroll = ttk.Scrollbar(care, orient="vertical", command=self.check_table.yview)
        self.check_table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.check_table.pack(fill="both", expand=True)
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self.database_tab_shown(), add="+")

    def database_tab_shown(self):
        if self.tabs.select() == str(self.database_tab):
            self.load_database_info()

    # --- Overview -------------------------------------------------------------------------------------------

    def load_database_info(self):
        def work():
            try:
                self.events.put(("db_info", (maintenance.database_info(self.path), None)))
            except (OSError, maintenance.sqlite3.Error) as error:
                self.events.put(("db_info", (None, str(error))))
        threading.Thread(target=work, daemon=True, name="Archivübersicht").start()

    def show_database_info(self, info, error):
        if error:
            for label in self.info_labels.values():
                label.configure(text="–")
            self.info_labels["size"].configure(text=f"Nicht lesbar: {error}")
            return
        values = {
            "size": size_text(info["size"] + info["wal"]),
            "reusable": size_text(info["reusable"]) + (" – „Komprimieren“ gibt ihn frei"
                                                        if info["reusable"] > 50 * 1024 ** 2 else ""),
            "mails": self.number(info["mails"]),
            "accounts": self.number(info["accounts"]),
            "folders": self.number(info["folders"]),
            "period": f"{day_text(info['oldest'])} bis {day_text(info['newest'])}" if info["mails"] else "–",
            "index": "vollständig" if info["indexed"] >= info["mails"] else
                     f"{self.number(info['indexed'])} von {self.number(info['mails'])} Mails",
            "disk": size_text(info["disk_free"]),
            "format": f"Version {info['schema']} · SQLite {info['sqlite']}",
        }
        for key, text in values.items():
            self.info_labels[key].configure(text=text)

    def update_last_backup(self):
        last = self.settings["last_backup"]
        try:
            when = datetime.fromisoformat(last["time"]).strftime("%d.%m.%Y um %H:%M Uhr")
            self.last_backup_label.configure(text=f"Letzte Sicherung: {when}\n{last['file']}")
        except (TypeError, KeyError, ValueError):
            self.last_backup_label.configure(text="Noch keine Sicherung mit diesem Programm erstellt.")

    def show_archive_file(self):
        try:
            subprocess.Popen(["explorer", "/select,", str(self.path)])
        except OSError:
            os.startfile(self.path.parent)

    # --- Running an operation in the background -------------------------------------------------------------

    def run_maintenance(self, label, work, finished):
        """work(progress, cancel) runs in a thread; finished(result) afterwards in the interface."""
        if self.maintenance_running:
            return
        if self.busy:
            messagebox.showinfo(label, "Bitte warte, bis der laufende Abruf beendet ist.", parent=self.root)
            return
        self.maintenance_running = True
        self.maintenance_cancel.clear()
        self.maintenance_pause.set()  # Background work (spam, paperclips) waits meanwhile.
        self.set_maintenance_state(True)
        self.maintenance_info.configure(text=f"{label} …")
        self.status.set(f"{label} …")

        def run():
            try:
                result = work(lambda text: self.events.put(("db_progress", text)), self.maintenance_cancel)
                outcome = ("ok", result)
            except maintenance.Cancelled:
                outcome = ("cancelled", None)
            except Exception as error:  # Shown to the user; the archive stays consistent (SQLite transactions).
                outcome = ("error", str(error) or type(error).__name__)
            self.events.put(("db_done", (label, finished, outcome)))

        threading.Thread(target=run, daemon=True, name=label).start()

    def cancel_maintenance(self):
        # Komprimieren of a large archive needs a while to roll back.
        self.maintenance_cancel.set()
        self.maintenance_stop.configure(state="disabled")
        self.maintenance_info.configure(text="Wird abgebrochen … (das Archiv bleibt unverändert)")

    def set_maintenance_state(self, running):
        for button in self.maintenance_buttons:
            button.configure(state="disabled" if running else "normal")
        self.maintenance_stop.configure(state="normal" if running else "disabled")
        self.update_account_buttons()

    def maintenance_finished(self, label, finished, outcome):
        self.maintenance_running = False
        self.maintenance_pause.clear()
        self.set_maintenance_state(False)
        state, value = outcome
        if state == "cancelled":
            self.maintenance_info.configure(text=f"{label}: abgebrochen, das Archiv ist unverändert.")
            self.status.set(f"{label} abgebrochen")
        elif state == "error":
            self.maintenance_info.configure(text=f"{label}: fehlgeschlagen.")
            self.status.set(f"{label} fehlgeschlagen")
            messagebox.showerror(label, value, parent=self.root)
        else:
            finished(value)
        self.load_database_info()

    def handle_database_event(self, event, value):
        """Called by poll(); returns True if the event belonged to this tab."""
        if event == "db_info":
            self.show_database_info(*value)
        elif event == "db_progress":
            if not self.maintenance_cancel.is_set():
                self.maintenance_info.configure(text=value)
        elif event == "db_done":
            self.maintenance_finished(*value)
        else:
            return False
        return True

    # --- Check and maintenance ------------------------------------------------------------------------------

    def check_archive(self, full):
        label = "Vollständige Prüfung" if full else "Schnellprüfung"

        def finished(results):
            self.check_table.delete(*self.check_table.get_children())
            for name, ok, detail in results:
                self.check_table.insert("", "end", values=(("✔ " if ok else "✖ ") + name, detail))
            problems = sum(not ok for _, ok, _ in results)
            text = f"{label} abgeschlossen: " + ("keine Fehler gefunden" if not problems else
                                                  f"{problems} Problem{'e' if problems > 1 else ''} gefunden")
            self.maintenance_info.configure(text=f"{text} ({datetime.now():%H:%M} Uhr)")
            self.status.set(text)
            if problems:
                messagebox.showwarning(label, f"{text}.\n\nTipp: Erstelle zuerst eine Sicherung. Fehler im "
                                              "Suchindex behebt „Suchindex neu aufbauen“.", parent=self.root)

        self.run_maintenance(label, lambda progress, cancel: maintenance.check_database(
            self.path, full, progress, cancel), finished)

    def compact_archive(self):
        if not messagebox.askyesno(
                "Komprimieren", "Das Archiv wird neu geschrieben und ungenutzter Platz freigegeben. Das kann bei "
                "großen Archiven einige Minuten dauern; währenddessen ist kein Abruf möglich.\n\nJetzt "
                "komprimieren?", parent=self.root):
            return

        def finished(sizes):
            before, after = sizes
            text = f"Komprimiert: {size_text(before)} → {size_text(after)}"
            self.maintenance_info.configure(text=text)
            self.status.set(text)

        self.run_maintenance("Komprimieren", lambda progress, cancel: maintenance.compact_database(
            self.path, progress, cancel), finished)

    def rebuild_index(self):
        if not messagebox.askyesno(
                "Suchindex neu aufbauen", "Der Suchindex wird geleert und im Hintergrund neu aufgebaut. Bis er "
                "fertig ist, ist die Suche langsamer.\n\nJetzt neu aufbauen?", parent=self.root):
            return

        def finished(_):
            self.maintenance_info.configure(text="Suchindex geleert; er wird jetzt im Hintergrund neu aufgebaut.")
            self.start_search_index()

        self.run_maintenance("Suchindex neu aufbauen",
                             lambda progress, cancel: maintenance.reset_search_index(self.path), finished)

    # --- Backup and restore ---------------------------------------------------------------------------------

    def create_backup(self):
        last = self.settings["last_backup"]
        folder = Path(last["file"]).parent if isinstance(last, dict) and last.get("file") else Path.home()
        target = filedialog.asksaveasfilename(
            parent=self.root, title="Sicherung speichern unter", initialdir=folder if folder.exists() else None,
            initialfile=maintenance.default_backup_name(), defaultextension=".sqlite3", filetypes=ARCHIVE_TYPES)
        if not target:
            return
        target = Path(target)

        def finished(size):
            self.save_setting("last_backup", {"time": datetime.now().isoformat(timespec="seconds"),
                                              "file": str(target)})
            self.update_last_backup()
            text = f"Sicherung erstellt ({size_text(size)})"
            self.maintenance_info.configure(text=f"{text}: {target}")
            self.status.set(text)

        self.run_maintenance("Sicherung", lambda progress, cancel: maintenance.backup_database(
            self.path, target, progress, cancel), finished)

    def restore_backup(self):
        if self.busy:
            messagebox.showinfo("Wiederherstellen", "Bitte warte, bis der laufende Abruf beendet ist.",
                                parent=self.root)
            return
        source = filedialog.askopenfilename(parent=self.root, title="Sicherung wählen", filetypes=ARCHIVE_TYPES)
        if not source:
            return
        source = Path(source)
        problem = maintenance.archive_problem(source)
        if not problem and source.resolve() == self.path.resolve():
            problem = "Das ist das geöffnete Archiv selbst."
        if problem:
            messagebox.showerror("Wiederherstellen", f"{source}\n\n{problem}", parent=self.root)
            return
        try:
            mails, newest = maintenance.archive_summary(source)
            current = maintenance.archive_summary(self.path)[0]
        except maintenance.sqlite3.Error as error:
            messagebox.showerror("Wiederherstellen", str(error), parent=self.root)
            return
        if not messagebox.askyesno(
                "Sicherung wiederherstellen",
                f"Das geöffnete Archiv ({self.number(current)} Mails) wird durch die Sicherung ersetzt:\n{source}\n"
                f"({self.number(mails)} Mails, neueste vom {day_text(newest)})\n\nDas bisherige Archiv bleibt als "
                f"Kopie daneben erhalten. {APP_NAME} startet dazu neu.\n\nJetzt wiederherstellen?",
                icon="warning", parent=self.root):
            return
        self.save_setting("restore_from", str(source))
        self.restart()

    # --- Switching the archive file -------------------------------------------------------------------------

    def open_other_archive(self):
        source = filedialog.askopenfilename(parent=self.root, title="Archiv öffnen", filetypes=ARCHIVE_TYPES,
                                            initialdir=self.path.parent)
        if source:
            self.switch_archive(Path(source))

    def create_archive(self):
        target = filedialog.asksaveasfilename(parent=self.root, title="Neues Archiv anlegen",
                                              initialfile="Archiv.sqlite3", defaultextension=".sqlite3",
                                              filetypes=ARCHIVE_TYPES)
        if not target:
            return
        target = Path(target)
        if target.exists():
            problem = maintenance.archive_problem(target)
            if problem:
                messagebox.showerror("Neues Archiv", f"Die Datei existiert bereits und wird nicht überschrieben:\n"
                                                     f"{target}\n\n{problem}", parent=self.root)
                return
        else:
            try:
                initialize(target)
            except (OSError, ValueError, maintenance.sqlite3.Error) as error:
                messagebox.showerror("Neues Archiv", str(error), parent=self.root)
                return
        self.switch_archive(target)

    def switch_archive(self, path):
        if path.resolve() == self.path.resolve():
            messagebox.showinfo("Archiv", "Dieses Archiv ist bereits geöffnet.", parent=self.root)
            return
        if self.busy or self.maintenance_running:
            messagebox.showinfo("Archiv", "Bitte warte, bis der laufende Abruf bzw. die Wartung beendet ist.",
                                parent=self.root)
            return
        problem = maintenance.archive_problem(path)
        if problem:
            messagebox.showerror("Archiv", f"{path}\n\n{problem}", parent=self.root)
            return
        if not messagebox.askyesno("Archiv wechseln", f"Zu diesem Archiv wechseln?\n{path}\n\n{APP_NAME} startet "
                                                      "dazu neu.", parent=self.root):
            return
        self.save_setting("database", "" if path.resolve() == self.default_archive().resolve() else
                          str(path.resolve()))
        self.restart()
