"""Tab "Datenbank": archive folder (one file per account), overview, check and maintenance, backup and restore.

Mixed into MailArchive (app.py); uses its folder, archive (ArchiveSet), settings, events, status and restart().
"""
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import maintenance
from app_info import APP_NAME
from appearance import apply_theme
from archive_set import account_files

ARCHIVE_TYPES = [(f"{APP_NAME}-Archiv", "*.sqlite3"), ("Alle Dateien", "*.*")]


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
    INFO_ROWS = (("size", "Größe gesamt"), ("reusable", "Davon ungenutzt"), ("mails", "Mails"),
                 ("files", "Konto-Dateien"), ("period", "Zeitraum"), ("index", "Suchindex"),
                 ("disk", "Frei auf dem Laufwerk"), ("format", "Format"))

    # --- Program start: which folder, pending restore, conversion of an older single-file archive -------------

    def default_folder(self):
        return self.data / "Konten"

    def archive_locations(self):
        """(archive folder, single-file archive of version 1.17 or older that belongs to it)."""
        chosen = self.settings["database"]
        if not chosen:
            return self.default_folder(), self.data / "archiv.sqlite3"
        path = Path(chosen)
        if path.suffix.lower() == ".sqlite3":  # Chosen with 1.16/1.17: an archive file, converted next to it.
            return path.with_name(f"{path.stem} Konten"), path
        return path, None

    def prepare_archive(self):
        """Return (archive folder, note for the user). Runs before the main window is built."""
        folder, legacy = self.archive_locations()
        if self.settings["database"] and not folder.exists() and not (legacy and legacy.exists()):
            if not messagebox.askyesno(APP_NAME, f"Der gewählte Archivordner wurde nicht gefunden:\n{folder}\n\n"
                                                 "(Ist das Laufwerk angeschlossen?)\n\nStattdessen das "
                                                 "Standardarchiv öffnen?", parent=self.root):
                raise OSError(f"Archivordner nicht gefunden: {folder}")
            self.save_setting("database", "")
            folder, legacy = self.archive_locations()
        note = None
        source = self.settings["restore_from"]
        if source:
            self.save_setting("restore_from", "")  # Never try again on every start.
            try:
                kept = self.with_progress("Sicherung wird eingespielt …", lambda progress: maintenance.restore_archive(
                    folder, Path(source), progress))
                note = ("info", "Sicherung wiederherstellen", f"Die Sicherung wurde eingespielt:\n{source}" +
                        (f"\n\nDas bisherige Archiv wurde aufbewahrt in:\n{kept}" if kept else ""))
            except (OSError, ValueError, maintenance.sqlite3.Error) as error:
                note = ("error", "Sicherung wiederherstellen",
                        f"Die Sicherung wurde nicht eingespielt; das Archiv ist unverändert.\n\n{error}")
        if legacy is not None and maintenance.split_pending(legacy, folder):
            created = self.with_progress(
                "Das Archiv wird auf eine Datei je Konto umgestellt (einmalig) …",
                lambda progress: maintenance.split_archive(legacy, folder, progress))
            if self.settings["database"]:
                self.save_setting("database", str(folder))
            if created and not note:
                note = ("info", "Archiv umgestellt", f"Das Archiv wurde auf eine Datei je Konto umgestellt "
                                                     f"({len(created)} Konto-Dateien):\n{folder}")
        return folder, note

    def with_progress(self, title, work):
        """Run work(progress) in a thread while the (still empty) main window shows its progress."""
        apply_theme(self.root, self.settings["theme"])
        self.root.title(APP_NAME)
        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, style="Preview.TLabel").pack(anchor="w")
        text = ttk.Label(frame, style="Muted.TLabel")
        text.pack(anchor="w", pady=(10, 6))
        bar = ttk.Progressbar(frame, length=460, mode="determinate")
        bar.pack(anchor="w", fill="x")
        ttk.Label(frame, style="Muted.TLabel", text="Bitte das Programm währenddessen nicht beenden.").pack(
            anchor="w", pady=(10, 0))
        updates = queue.Queue()
        outcome = {}

        def run():
            try:
                outcome["result"] = work(lambda done, total, message: updates.put((done, total, message)))
            except BaseException as error:  # Shown below; the original archive stays as it was.
                outcome["error"] = error

        worker = threading.Thread(target=run, daemon=True, name="Archivvorbereitung")
        worker.start()
        while worker.is_alive():
            while not updates.empty():
                done, total, message = updates.get_nowait()
                text.configure(text=message)
                if total:
                    bar.stop()
                    bar.configure(mode="determinate", maximum=total, value=done)
                elif str(bar.cget("mode")) != "indeterminate":
                    # A phase without measurable progress (e.g. checking a large file): keep the bar moving.
                    bar.configure(mode="indeterminate")
                    bar.start(15)
            self.root.update()
            time.sleep(0.05)
        frame.destroy()
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("result")

    # --- Layout ---------------------------------------------------------------------------------------------

    def build_database(self):
        page = self.database_tab
        self.maintenance_running = False
        self.maintenance_cancel = threading.Event()
        self.maintenance_buttons = []

        place = ttk.LabelFrame(page, text=" Archivordner ", padding=10)
        place.pack(fill="x")
        self.archive_location = tk.StringVar(value=str(self.folder))
        ttk.Entry(place, textvariable=self.archive_location, state="readonly").pack(fill="x")
        buttons = ttk.Frame(place)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Im Explorer zeigen", command=lambda: os.startfile(self.folder)).pack(side="left")
        for text, command in (("Anderen Archivordner öffnen …", self.open_other_archive),
                              ("Neuen Archivordner anlegen …", self.create_archive)):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(side="left", padx=(6, 0))
            self.maintenance_buttons.append(button)
        self.default_button = ttk.Button(buttons, text="Standardarchiv verwenden",
                                         command=lambda: self.switch_archive(self.default_folder()))
        if self.folder.resolve() != self.default_folder().resolve():
            self.default_button.pack(side="left", padx=(6, 0))
            self.maintenance_buttons.append(self.default_button)
        hint = ttk.Label(place, style="Muted.TLabel", justify="left",
                         text="Jedes Konto hat eine eigene Datei (Konto-1.sqlite3 …) mit seinen Mails, der "
                              "Ordnerauswahl und dem gespeicherten Passwort. Beim Wechsel startet das Programm neu.")
        hint.pack(anchor="w", fill="x", pady=(6, 0))
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=event.width))

        row = ttk.Frame(page)
        row.pack(fill="x", pady=(10, 0))
        row.columnconfigure(1, weight=1)
        overview = ttk.LabelFrame(row, text=" Übersicht ", padding=10)
        overview.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.info_labels = {}
        for line, (key, label) in enumerate(self.INFO_ROWS):
            ttk.Label(overview, text=label, style="Muted.TLabel").grid(row=line, column=0, sticky="w", padx=(0, 12))
            self.info_labels[key] = ttk.Label(overview, text="…")
            self.info_labels[key].grid(row=line, column=1, sticky="w")

        files = ttk.LabelFrame(row, text=" Konto-Dateien ", padding=10)
        files.grid(row=0, column=1, sticky="nsew")
        self.file_table = ttk.Treeview(files, columns=("file", "account", "mails", "size"), show="headings",
                                       height=4, selectmode="none")
        for column, label, width, anchor in (("file", "Datei", 120, "w"), ("account", "Konto", 200, "w"),
                                             ("mails", "Mails", 70, "e"), ("size", "Größe", 80, "e")):
            self.file_table.heading(column, text=label, anchor=anchor)
            self.file_table.column(column, width=width, anchor=anchor, stretch=column == "account")
        self.file_table.pack(fill="both", expand=True)
        backup = ttk.Frame(files)
        backup.pack(fill="x", pady=(8, 0))
        for text, command in (("Sicherung erstellen …", self.create_backup),
                              ("Sicherung wiederherstellen …", self.restore_backup)):
            button = ttk.Button(backup, text=text, command=command)
            button.pack(side="left", padx=(0, 6))
            self.maintenance_buttons.append(button)
        self.last_backup_label = ttk.Label(files, style="Muted.TLabel", justify="left")
        self.last_backup_label.pack(anchor="w", fill="x", pady=(6, 0))
        self.last_backup_label.bind("<Configure>",
                                    lambda event: self.last_backup_label.configure(wraplength=event.width))
        self.update_last_backup()

        care = ttk.LabelFrame(page, text=" Prüfen und warten (alle Konto-Dateien) ", padding=10)
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
        self.check_table.column("check", width=300, stretch=False)
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

    def file_labels(self):
        """{file name: account labels} of the enabled accounts."""
        labels = {}
        for key, account in self.archive.saved_accounts().items():
            path, _ = self.archive.account_place(key)
            labels.setdefault(path.name, []).append(account.label)
        return labels

    def load_database_info(self):
        def work():
            try:
                files = self.archive.files()
                infos = [(path, maintenance.database_info(path)) for path in files]
                self.events.put(("db_info", ((infos, self.file_labels()), None)))
            except (OSError, ValueError, maintenance.sqlite3.Error) as error:
                self.events.put(("db_info", (None, str(error))))
        threading.Thread(target=work, daemon=True, name="Archivübersicht").start()

    def show_database_info(self, info, error):
        if error:
            for label in self.info_labels.values():
                label.configure(text="–")
            self.info_labels["size"].configure(text=f"Nicht lesbar: {error}")
            return
        infos, labels = info
        mails = sum(item["mails"] for _, item in infos)
        indexed = sum(item["indexed"] for _, item in infos)
        reusable = sum(item["reusable"] for _, item in infos)
        oldest = min((item["oldest"] for _, item in infos if item["oldest"]), default=None)
        newest = max((item["newest"] for _, item in infos if item["newest"]), default=None)
        disk = infos[0][1]["disk_free"] if infos else None
        values = {
            "size": size_text(sum(item["size"] + item["wal"] for _, item in infos)),
            "reusable": size_text(reusable) + (" – „Komprimieren“ gibt ihn frei" if reusable > 50 * 1024 ** 2
                                               else ""),
            "mails": self.number(mails),
            "files": self.number(len(infos)),
            "period": f"{day_text(oldest)} bis {day_text(newest)}" if mails else "–",
            "index": "vollständig" if indexed >= mails else f"{self.number(indexed)} von {self.number(mails)} Mails",
            "disk": size_text(disk),
            "format": f"Version {infos[0][1]['schema']} · SQLite {infos[0][1]['sqlite']}" if infos else "–",
        }
        for key, text in values.items():
            self.info_labels[key].configure(text=text)
        self.file_table.delete(*self.file_table.get_children())
        for path, item in infos:
            self.file_table.insert("", "end", values=(path.name, ", ".join(labels.get(path.name, ["(entfernt)"])),
                                                      self.number(item["mails"]),
                                                      size_text(item["size"] + item["wal"])))

    def update_last_backup(self):
        last = self.settings["last_backup"]
        try:
            when = datetime.fromisoformat(last["time"]).strftime("%d.%m.%Y um %H:%M Uhr")
            self.last_backup_label.configure(text=f"Letzte Sicherung: {when} · {last['file']}")
        except (TypeError, KeyError, ValueError):
            self.last_backup_label.configure(text="Noch keine Sicherung mit diesem Programm erstellt.")

    # --- Running an operation in the background -------------------------------------------------------------

    def run_maintenance(self, label, work, finished):
        """work(progress, cancel) runs in a thread; finished(result) afterwards in the interface."""
        if self.maintenance_running:
            return
        if self.busy or self.list_preparing:
            messagebox.showinfo(label, "Bitte warte, bis der laufende Abruf bzw. die Vorbereitung beendet ist.",
                                parent=self.root)
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
        # Komprimieren of a large file needs a while to roll back.
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
            self.maintenance_info.configure(text=f"{label}: abgebrochen; nicht bearbeitete Dateien sind unverändert.")
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

    def each_file(self, work, progress):
        """Run work(path, progress) for every account file; returns [(path, result)]."""
        files = self.archive.files()
        results = []
        for number, path in enumerate(files, 1):
            prefix = f"{path.stem} ({number}/{len(files)})"
            results.append((path, work(path, lambda text, prefix=prefix: progress(f"{prefix}: {text}"))))
        return results

    # --- Check and maintenance ------------------------------------------------------------------------------

    def check_archive(self, full):
        label = "Vollständige Prüfung" if full else "Schnellprüfung"
        labels = self.file_labels()

        def finished(results):
            self.check_table.delete(*self.check_table.get_children())
            problems = 0
            for path, checks in results:
                who = ", ".join(labels.get(path.name, [path.stem]))
                for name, ok, detail in checks:
                    problems += not ok
                    self.check_table.insert("", "end", values=(("✔ " if ok else "✖ ") + f"{who}: {name}", detail))
            text = f"{label} abgeschlossen: " + ("keine Fehler gefunden" if not problems else
                                                  f"{problems} Problem{'e' if problems > 1 else ''} gefunden")
            self.maintenance_info.configure(text=f"{text} ({datetime.now():%H:%M} Uhr)")
            self.status.set(text)
            if problems:
                messagebox.showwarning(label, f"{text}.\n\nTipp: Erstelle zuerst eine Sicherung. Fehler im "
                                              "Suchindex behebt „Suchindex neu aufbauen“.", parent=self.root)

        self.run_maintenance(label, lambda progress, cancel: self.each_file(
            lambda path, report: maintenance.check_database(path, full, report, cancel), progress), finished)

    def compact_archive(self):
        if not messagebox.askyesno(
                "Komprimieren", "Alle Konto-Dateien werden neu geschrieben und ungenutzter Platz freigegeben. Das "
                "kann bei großen Archiven einige Minuten dauern; währenddessen ist kein Abruf möglich.\n\nJetzt "
                "komprimieren?", parent=self.root):
            return

        def finished(results):
            before = sum(sizes[0] for _, sizes in results)
            after = sum(sizes[1] for _, sizes in results)
            text = f"Komprimiert: {size_text(before)} → {size_text(after)}"
            self.maintenance_info.configure(text=text)
            self.status.set(text)

        self.run_maintenance("Komprimieren", lambda progress, cancel: self.each_file(
            lambda path, report: maintenance.compact_database(path, report, cancel), progress), finished)

    def rebuild_index(self):
        if not messagebox.askyesno(
                "Suchindex neu aufbauen", "Der Suchindex wird geleert und im Hintergrund neu aufgebaut. Bis er "
                "fertig ist, ist die Suche langsamer.\n\nJetzt neu aufbauen?", parent=self.root):
            return

        def finished(_):
            self.maintenance_info.configure(text="Suchindex geleert; er wird jetzt im Hintergrund neu aufgebaut.")
            self.start_search_index()

        self.run_maintenance("Suchindex neu aufbauen", lambda progress, cancel: self.each_file(
            lambda path, report: maintenance.reset_search_index(path), progress), finished)

    # --- Backup and restore ---------------------------------------------------------------------------------

    def create_backup(self):
        last = self.settings["last_backup"]
        start = Path(last["file"]).parent if isinstance(last, dict) and last.get("file") else Path.home()
        parent = filedialog.askdirectory(parent=self.root, title="Ort für die Sicherung wählen (es wird ein neuer "
                                                                 "Ordner angelegt)",
                                         initialdir=start if start.exists() else None)
        if not parent:
            return
        target = Path(parent) / maintenance.default_backup_folder_name()
        if target.exists():
            messagebox.showerror("Sicherung", f"Den Ordner gibt es schon:\n{target}", parent=self.root)
            return

        def finished(size):
            self.save_setting("last_backup", {"time": datetime.now().isoformat(timespec="seconds"),
                                              "file": str(target)})
            self.update_last_backup()
            text = f"Sicherung erstellt ({size_text(size)})"
            self.maintenance_info.configure(text=f"{text}: {target}")
            self.status.set(text)

        self.run_maintenance("Sicherung", lambda progress, cancel: maintenance.backup_archive(
            self.archive.files(), target, progress, cancel), finished)

    def restore_backup(self):
        if self.busy:
            messagebox.showinfo("Wiederherstellen", "Bitte warte, bis der laufende Abruf beendet ist.",
                                parent=self.root)
            return
        if messagebox.askyesno("Sicherung wiederherstellen", "Einen Sicherungsordner wählen?\n\n„Nein“ wählt "
                                                             "stattdessen eine einzelne Archivdatei (Sicherung von "
                                                             "Version 1.17 oder älter).", parent=self.root):
            source = filedialog.askdirectory(parent=self.root, title="Sicherungsordner wählen")
        else:
            source = filedialog.askopenfilename(parent=self.root, title="Archivdatei wählen",
                                                filetypes=ARCHIVE_TYPES)
        if not source:
            return
        source = Path(source)
        problem = maintenance.backup_problem(source)
        if not problem and source.resolve() == self.folder.resolve():
            problem = "Das ist der geöffnete Archivordner selbst."
        if problem:
            messagebox.showerror("Wiederherstellen", f"{source}\n\n{problem}", parent=self.root)
            return
        try:
            sources = account_files(source) if source.is_dir() else [source]
            mails = sum(maintenance.archive_summary(path)[0] for path in sources)
            newest = max((maintenance.archive_summary(path)[1] or 0) for path in sources)
            current = self.archive.count_mails()
        except maintenance.sqlite3.Error as error:
            messagebox.showerror("Wiederherstellen", str(error), parent=self.root)
            return
        if not messagebox.askyesno(
                "Sicherung wiederherstellen",
                f"Das geöffnete Archiv ({self.number(current)} Mails) wird durch die Sicherung ersetzt:\n{source}\n"
                f"({self.number(mails)} Mails, neueste vom {day_text(newest)})\n\nDie bisherigen Konto-Dateien "
                f"bleiben in einem Unterordner erhalten. {APP_NAME} startet dazu neu.\n\nJetzt wiederherstellen?",
                icon="warning", parent=self.root):
            return
        self.save_setting("restore_from", str(source))
        self.restart()

    # --- Switching the archive folder -----------------------------------------------------------------------

    def open_other_archive(self):
        folder = filedialog.askdirectory(parent=self.root, title="Archivordner öffnen", initialdir=self.folder.parent)
        if not folder:
            return
        folder = Path(folder)
        problem = maintenance.backup_problem(folder)
        if problem:
            messagebox.showerror("Archivordner", f"{folder}\n\n{problem}\n\nFür einen leeren Ordner „Neuen "
                                                 "Archivordner anlegen …“ wählen; ältere Einzeldateien über "
                                                 "„Sicherung wiederherstellen …“ einspielen.", parent=self.root)
            return
        self.switch_archive(folder)

    def create_archive(self):
        folder = filedialog.askdirectory(parent=self.root, title="Leeren Ordner für das neue Archiv wählen")
        if not folder:
            return
        folder = Path(folder)
        if any(folder.iterdir()):
            messagebox.showerror("Neuer Archivordner", f"Der Ordner ist nicht leer:\n{folder}\n\nBitte einen "
                                                       "leeren Ordner wählen oder anlegen.", parent=self.root)
            return
        self.switch_archive(folder)

    def switch_archive(self, folder):
        if folder.resolve() == self.folder.resolve():
            messagebox.showinfo("Archiv", "Dieser Archivordner ist bereits geöffnet.", parent=self.root)
            return
        if self.busy or self.maintenance_running:
            messagebox.showinfo("Archiv", "Bitte warte, bis der laufende Abruf bzw. die Wartung beendet ist.",
                                parent=self.root)
            return
        if not messagebox.askyesno("Archiv wechseln", f"Zu diesem Archivordner wechseln?\n{folder}\n\n{APP_NAME} "
                                                      "startet dazu neu.", parent=self.root):
            return
        self.save_setting("database", "" if folder.resolve() == self.default_folder().resolve() else
                          str(folder.resolve()))
        self.restart()
