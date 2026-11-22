import base64
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from app_info import APP_NAME, VERSION_TEXT
from archive import (Account, clean_password, display_text, has_attachments, resolve_period, sender_address,
                     spam_info)
from appearance import PALETTES, apply_theme, theme_label
from attachments import AttachmentPane, remove_old_temporary_files
from archive_set import ArchiveSet
from database_tab import DatabaseTab
from theme_dialog import ThemeDialog
from mail_window import MailWindow
from program_icon import png_icon
from settings import (INTERVALS, MINIMIZED_ARGUMENT, SPAM_THRESHOLDS, Settings, autostart_command, autostart_entry,
                      set_autostart)
from tray import TrayIcon, signal_running_instance
import updater


DATA = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "MailArchiv"
PAGE = 100
FIRST_AUTO_SYNC = 60  # Seconds after program start, so the backup runs soon after Windows starts.


class MailArchive(DatabaseTab):
    def __init__(self, root, data, start_minimized=False):
        self.root = root
        self.data = data
        self.settings = Settings(data)
        self.events = queue.Queue()
        # Created first: its hidden window marks the running instance, also during a long conversion below.
        self.tray = TrayIcon(APP_NAME, lambda action: self.events.put(("tray", action)))
        try:
            # Archive folder with one database file per account; prepared (restore, conversion) before the window.
            self.folder, restore_note = self.prepare_archive()
            self.archive = ArchiveSet(self.folder)
        except BaseException:
            self.tray.stop()
            raise
        remove_old_temporary_files()
        self.theme = self.settings["theme"]
        self.tray.set_visible(self.settings["tray"])
        self.hint_shown = False
        self.cancelled = threading.Event()
        self.busy = False
        self.offset = 0
        self.pending_search = None
        self.account_id = None
        self.accounts = {}
        self.preview_id = None
        self.mail_windows = {}  # Open detail windows by mail id.
        self.visible_unknown = set()  # Mails on the current page whose paperclip is not yet known.
        self.visible_unrated = set()  # Mails on the current page whose spam rating was not yet read.
        # Set while the user interface queries the database; the background work then pauses, because both
        # need Python's global interpreter lock and the interface must not wait for it.
        self.foreground = threading.Event()
        self.maintenance_pause = threading.Event()  # Set during maintenance in the tab Datenbank.
        self.maintenance_running = False
        self.list_preparing = False  # Indexes for the mail list are being created (once, 1.16.1).
        self.index_lock = threading.Lock()
        self.sync_auto = False
        self.next_auto = time.time() + FIRST_AUTO_SYNC
        root.title(f"{APP_NAME} {VERSION_TEXT}")
        self.window_icons = {
            color: [tk.PhotoImage(master=root, data=base64.b64encode(png_icon(size, color)).decode("ascii"),
                                  format="png") for size in (16, 32, 48)]
            for color in ("blue", "orange")
        }
        root.iconphoto(True, *self.window_icons["blue"])
        root.geometry("1120x710")
        root.minsize(810, 600)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self.update_theme()
        self.update_developer_ui()
        accounts = self.archive.saved_accounts()
        self.reload_accounts(next(iter(accounts)) if len(accounts) == 1 else None)
        self.refresh()
        if start_minimized and self.settings["start_hidden"]:
            if self.settings["tray"]:
                root.withdraw()
            else:
                root.iconify()
        if restore_note:
            kind, title, text = restore_note
            root.after(300, lambda: (messagebox.showerror if kind == "error" else messagebox.showinfo)(
                title, text, parent=root))
        root.after(100, self.poll)
        threading.Thread(target=self.background_work, daemon=True, name="Hintergrundarbeit").start()
        root.after(10_000, self.scheduled_update_check)

    def update_theme(self):
        apply_theme(self.root, self.theme)
        self.body.tag_configure("link", foreground=PALETTES[self.theme]["accent"])
        self.attachments.theme = self.theme
        for window in self.mail_windows.values():
            window.update_theme(self.theme)
        self.theme_name.configure(text=theme_label(self.theme))

    def choose_theme(self):
        ThemeDialog(self)

    def show_theme(self, name):
        """Show a design in the whole program without saving it (preview while choosing)."""
        if name in PALETTES and name != self.theme:
            self.theme = name
            self.update_theme()

    def set_theme(self, name):
        if name not in PALETTES:
            return
        self.show_theme(name)
        self.save_setting("theme", name)

    def save_setting(self, key, value):
        try:
            self.settings.set(key, value)
        except OSError:
            self.status.set("Die Einstellung konnte nicht für den nächsten Start gespeichert werden.")

    def build(self):
        header = ttk.Frame(self.root, padding=(16, 12, 16, 4))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        self.theme_button = ttk.Button(header, text="Design …", command=self.choose_theme)
        self.theme_button.pack(side="right")
        self.developer_label = ttk.Label(header, text="Entwicklermodus", style="Developer.TLabel")
        self.status = tk.StringVar(value="Bereit")
        self.status.trace_add("write", lambda *_: self.tray.set_tooltip(f"{APP_NAME} · {self.status.get()}"))
        ttk.Label(self.root, textvariable=self.status, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x"
        )
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=6)
        outer = self.archive_tab = ttk.Frame(self.tabs, padding=12)
        self.accounts_tab = ttk.Frame(self.tabs, padding=12)
        self.settings_tab = ttk.Frame(self.tabs, padding=12)
        self.database_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(outer, text="Archiv")
        self.tabs.add(self.accounts_tab, text="Konten")
        self.tabs.add(self.database_tab, text="Datenbank")
        self.tabs.add(self.settings_tab, text="Einstellungen")

        search_bar = ttk.Frame(outer)
        search_bar.pack(fill="x", pady=(0, 10))
        ttk.Label(search_bar, text="Suche").pack(side="left", padx=(0, 7))
        self.query = tk.StringVar()
        search = ttk.Entry(search_bar, textvariable=self.query)
        search.pack(side="left", fill="x", expand=True)
        search.bind("<Escape>", lambda _: self.query.set(""))
        def focus_search(_):
            self.tabs.select(outer)
            search.focus_set()
        self.root.bind("<Control-f>", focus_search)
        self.query.trace_add("write", self.schedule_search)
        ttk.Button(search_bar, text="Leeren", command=lambda: self.query.set("")).pack(side="left", padx=(7, 0))
        self.sender_filter = None
        self.sender_filter_button = ttk.Button(search_bar, command=lambda: self.filter_sender(None))
        self.only_suspicious = tk.BooleanVar(value=False)
        ttk.Checkbutton(search_bar, text="Nur verdächtige ⚠", variable=self.only_suspicious,
                        command=self.set_filter).pack(side="left", padx=(12, 0))

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)
        folders = ttk.Frame(panes, padding=(0, 0, 6, 0))
        results = ttk.Frame(panes, padding=(6, 0, 6, 0))
        preview = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(folders, weight=0)
        panes.add(results, weight=1)
        panes.add(preview, weight=1)
        self.folder_tree = ttk.Treeview(folders, show="tree", selectmode="browse")
        self.folder_tree.column("#0", width=210, minwidth=120)
        folder_scroll = ttk.Scrollbar(folders, command=self.folder_tree.yview)
        self.folder_tree.configure(yscrollcommand=folder_scroll.set)
        folder_scroll.pack(side="right", fill="y")
        self.folder_tree.pack(fill="both", expand=True)
        self.folder_tree.bind("<<TreeviewSelect>>", lambda _: self.select_folder())
        self.folder_tree.bind("<Button-3>", self.folder_tree_menu)
        self.folder_nodes = {}

        nav = ttk.Frame(results)
        nav.pack(side="bottom", fill="x", pady=(7, 0))
        self.prev = ttk.Button(nav, text="Zurück", command=lambda: self.change_page(-PAGE))
        self.prev.pack(side="left")
        self.next = ttk.Button(nav, text="Weiter", command=lambda: self.change_page(PAGE))
        self.next.pack(side="right")
        self.page_label = ttk.Label(nav, style="Muted.TLabel")
        self.page_label.pack(pady=7)
        self.table = ttk.Treeview(results, columns=tuple(self.COLUMNS), show="headings", selectmode="extended")
        for key, (label, width) in self.COLUMNS.items():
            anchor = self.ANCHORS.get(key, "w")
            # Headings aligned like their values, so short columns do not look spread apart.
            self.table.heading(key, command=lambda key=key: self.sort_by(key), anchor=anchor)
            if key in self.SAMPLES:
                width = self.fit_width(key)
            self.table.column(key, width=width, minwidth=width if key in self.SAMPLES else 60,
                              stretch=key in ("subject", "sender"), anchor=anchor)
        self.update_sort_headings()
        self.table.bind("<Configure>", self.fit_columns, add="+")
        scroll = ttk.Scrollbar(results, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.show_selection)
        self.table.bind("<Button-3>", self.mail_menu)
        self.table.bind("<Delete>", lambda _: self.delete_selected_mails())
        self.table.bind("<Double-1>", self.double_click_mail)
        self.table.bind("<Return>", lambda _: self.open_mail_window())

        self.subject = ttk.Label(preview, text="Mailvorschau", style="Preview.TLabel")
        self.subject.pack(fill="x")
        self.subject.bind("<Configure>", lambda event: self.subject.configure(wraplength=event.width))
        self.meta = ttk.Label(preview, style="Muted.TLabel", justify="left")
        self.meta.pack(fill="x", pady=(5, 10))
        self.meta.bind("<Configure>", lambda event: self.meta.configure(wraplength=event.width))
        identifier = ttk.Frame(preview)
        identifier.pack(fill="x", pady=(0, 8))
        ttk.Label(identifier, text="Mail-ID").pack(side="left", padx=(0, 6))
        self.mail_id = tk.StringVar()
        ttk.Entry(identifier, textvariable=self.mail_id, state="readonly", width=36).pack(
            side="left", fill="x", expand=True
        )
        self.copy_button = ttk.Button(identifier, text="Kopieren", command=self.copy_mail_id, state="disabled")
        self.copy_button.pack(side="left", padx=(5, 0))
        actions = ttk.Frame(preview)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        self.export = ttk.Button(actions, text="Original als .eml speichern",
                                 command=self.export_mail, state="disabled")
        self.export.pack(side="right")
        self.delete_button = ttk.Button(actions, text="Aus Archiv löschen …", command=self.delete_selected_mails,
                                        state="disabled")
        self.preview_tabs = ttk.Notebook(preview)
        self.preview_tabs.pack(fill="both", expand=True)
        message = ttk.Frame(self.preview_tabs)
        self.attachments = AttachmentPane(self.preview_tabs, self.theme)
        self.preview_tabs.add(message, text="Nachricht")
        self.preview_tabs.add(self.attachments, text="Anhänge")
        self.body = tk.Text(message, wrap="word", font=("Segoe UI", 10),
                            padx=11, pady=9, width=36, state="disabled")
        scroll = ttk.Scrollbar(message, command=self.body.yview)
        self.body.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.body.pack(fill="both", expand=True)
        self.links = {}
        self.body.tag_configure("link", underline=True)
        self.body.tag_bind("link", "<Button-1>", self.open_link)
        self.body.bind("<Motion>", self.hover_link)
        self.body.bind("<Leave>", self.hover_link)

        self.build_accounts()
        self.build_settings()
        self.build_database()

    def build_settings(self):
        page = self.settings_tab
        behaviour = ttk.LabelFrame(page, text=" Programmverhalten ", padding=10)
        behaviour.pack(fill="x")
        self.tray_choice = tk.BooleanVar(value=self.settings["tray"])
        ttk.Checkbutton(behaviour, variable=self.tray_choice, command=self.change_tray,
                        text="Beim Schließen im Infobereich (neben der Uhr) weiterlaufen").pack(anchor="w")
        ttk.Label(behaviour, style="Muted.TLabel",
                  text="Ein Klick auf das Symbol öffnet das Fenster. Beenden per Rechtsklick → Beenden.").pack(
            anchor="w", padx=(24, 0), pady=(0, 8))
        self.autostart_choice = tk.BooleanVar()
        ttk.Checkbutton(behaviour, variable=self.autostart_choice, command=self.change_autostart,
                        text="Mit Windows starten").pack(anchor="w")
        self.hidden_choice = tk.BooleanVar(value=self.settings["start_hidden"])
        self.hidden_button = ttk.Checkbutton(
            behaviour, variable=self.hidden_choice, text="Beim Windows-Start nur im Hintergrund starten",
            command=lambda: self.save_setting("start_hidden", self.hidden_choice.get()))
        self.hidden_button.pack(anchor="w", padx=(24, 0), pady=(4, 0))
        self.autostart_note = ttk.Label(behaviour, style="Muted.TLabel", justify="left")
        self.autostart_note.bind("<Configure>", lambda event: self.autostart_note.configure(wraplength=event.width))
        self.autostart_fix = ttk.Button(behaviour, text="Autostart auf dieses Programm ändern",
                                        command=lambda: self.change_autostart(True))

        automatic = ttk.LabelFrame(page, text=" Automatischer Abruf ", padding=10)
        automatic.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(automatic)
        row.pack(fill="x")
        ttk.Label(row, text="Alle Konten abrufen").pack(side="left", padx=(0, 8))
        self.interval_choice = ttk.Combobox(row, state="readonly", width=18, values=list(INTERVALS.values()))
        self.interval_choice.current(list(INTERVALS).index(self.settings["interval"]))
        self.interval_choice.pack(side="left")
        self.interval_choice.bind("<<ComboboxSelected>>", lambda _: self.change_interval())
        self.next_label = ttk.Label(row, style="Muted.TLabel")
        self.next_label.pack(side="left", padx=(12, 0))
        hint = ttk.Label(automatic, style="Muted.TLabel", justify="left",
                         text="Automatisch abgerufen werden nur Konten mit gespeichertem Passwort (Reiter Konten). "
                              "Während eines Abrufs ist das Symbol orange.")
        hint.pack(anchor="w", fill="x", pady=(6, 0))
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=event.width))

        developer = ttk.LabelFrame(page, text=" Entwicklermodus ", padding=10)
        developer.pack(fill="x", pady=(10, 0), side="bottom")
        self.developer_choice = tk.BooleanVar(value=self.settings["developer"])
        ttk.Checkbutton(developer, variable=self.developer_choice, command=self.change_developer,
                        text="Entwicklermodus aktivieren").pack(anchor="w")
        note = ttk.Label(developer, style="Muted.TLabel", justify="left", wraplength=600,
                         text="Erlaubt, archivierte Mails, Ordner und Konten aus dem lokalen Archiv zu löschen "
                              "(Rechtsklick im Reiter Archiv oder Entf-Taste). Auf dem Mailserver wird nichts "
                              "gelöscht.")
        note.pack(anchor="w", fill="x", padx=(24, 0))
        note.bind("<Configure>", lambda event: note.configure(wraplength=event.width))

        row = ttk.Frame(page)
        row.pack(fill="x", pady=(10, 0))
        row.columnconfigure(1, weight=1)
        appearance = ttk.LabelFrame(row, text=" Darstellung ", padding=10)
        appearance.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        spam = ttk.LabelFrame(row, text=" Spam-Erkennung ", padding=10)
        spam.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(10, 0))
        ttk.Label(spam, text="Verdächtig ab").pack(side="left")
        self.spam_choice = ttk.Combobox(spam, state="readonly", width=4,
                                        values=[str(points) for points in SPAM_THRESHOLDS])
        self.spam_choice.set(str(self.settings["spam_threshold"]))
        self.spam_choice.pack(side="left", padx=6)
        self.spam_choice.bind("<<ComboboxSelected>>", lambda _: self.change_spam_threshold())
        ttk.Label(spam, text="Punkten").pack(side="left")
        self.build_updates(row).grid(row=0, column=1, rowspan=2, sticky="nsew")
        self.theme_name = ttk.Label(appearance)
        self.theme_name.pack(side="left", padx=(0, 12))
        ttk.Button(appearance, text="Design wählen …", command=self.choose_theme).pack(side="left")
        self.update_autostart()

    def change_interval(self):
        minutes = list(INTERVALS)[self.interval_choice.current()]
        self.save_setting("interval", minutes)
        self.next_auto = time.time() + minutes * 60
        self.update_next_label()

    def update_next_label(self):
        if not self.settings["interval"]:
            text = ""
        elif self.busy:
            text = "Abruf läuft …"
        else:
            text = f"Nächster Abruf um {datetime.fromtimestamp(self.next_auto):%H:%M} Uhr"
        if self.next_label.cget("text") != text:
            self.next_label.configure(text=text)

    def start_auto_sync(self):
        minutes = self.settings["interval"]
        self.next_auto = time.time() + minutes * 60
        jobs = []
        for key, account in self.archive.saved_accounts().items():
            password = self.archive.account_password(key)
            if password:
                jobs.append((key, account, password))
        if not jobs:
            self.status.set("Automatischer Abruf: Kein Konto mit gespeichertem Passwort")
            return
        self.run_sync(jobs, automatic=True)

    def set_sync_state(self, busy):
        self.busy = busy
        self.tray.set_busy(busy)
        self.root.iconphoto(True, *self.window_icons["orange" if busy else "blue"])
        self.update_account_buttons()
        self.abort_button.configure(state="normal" if busy else "disabled")
        self.update_next_label()

    # --- Developer mode: delete archived data from the local database only -------------------------

    def developer_allowed(self):
        # Single place to add a password check later.
        return self.settings["developer"]

    def change_developer(self):
        enabled = self.developer_choice.get()
        if enabled and not messagebox.askyesno(
                "Entwicklermodus",
                "Im Entwicklermodus lassen sich archivierte Mails, Ordner und Konten endgültig aus dem lokalen "
                "Archiv löschen. Auf dem Mailserver wird nichts gelöscht.\n\nEntwicklermodus aktivieren?",
                parent=self.root):
            self.developer_choice.set(False)
            return
        self.save_setting("developer", enabled)
        self.update_developer_ui()

    def update_developer_ui(self):
        if self.settings["developer"]:
            self.developer_label.pack(side="right", padx=(0, 12))
            self.delete_button.pack(side="left")
        else:
            self.developer_label.pack_forget()
            self.delete_button.pack_forget()

    def confirm_delete(self, title, what, count):
        return messagebox.askyesno(
            title,
            f"{what} endgültig aus dem lokalen Archiv löschen?\n\n"
            f"Betroffen: {count} archivierte Mail{'' if count == 1 else 's'}. Das lässt sich nicht rückgängig machen. "
            "Auf dem Mailserver wird nichts gelöscht. Liegen die Mails dort noch und passen sie zu Ordnerauswahl "
            "und Zeitraum, archiviert der nächste Abruf sie erneut.",
            icon="warning", parent=self.root)

    def popup(self, event, entries):
        menu = tk.Menu(self.root, tearoff=False)
        for label, command in entries:
            menu.add_command(label=label, command=command)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def mail_menu(self, event):
        row = self.table.identify_row(event.y)
        if not row:
            return
        if row not in self.table.selection():
            self.table.selection_set(row)
        address = sender_address(self.table.set(row, "sender"))
        entries = [("In eigenem Fenster öffnen", self.open_mail_window)]
        if address:
            entries.append((f"Nur Mails von {address} anzeigen", lambda: self.filter_sender(address)))
        if self.sender_filter:
            entries.append(("Absenderfilter aufheben", lambda: self.filter_sender(None)))
        if self.developer_allowed():
            count = len(self.table.selection())
            entries.append((f"{count} Mails aus Archiv löschen …" if count > 1 else "Mail aus Archiv löschen …",
                            self.delete_selected_mails))
        if entries:
            self.popup(event, entries)

    def double_click_mail(self, event):
        # Double-clicks on the column headings sort; only a row opens the mail.
        if self.table.identify_region(event.x, event.y) in ("cell", "tree") and self.table.identify_row(event.y):
            self.open_mail_window()
            return "break"
        return None

    def open_mail_window(self):
        """Show the selected mail in its own window; a mail that is already open is brought to the front."""
        selected = self.table.selection()
        if not selected:
            return
        mail_id = selected[0]
        window = self.mail_windows.get(mail_id)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_force()
            return
        try:
            window = MailWindow(self, mail_id)
        except (LookupError, OSError, sqlite3.Error) as error:
            messagebox.showerror("Mail öffnen", str(error), parent=self.root)
            return
        self.mail_windows[mail_id] = window
        window.bind("<Destroy>", lambda event, key=mail_id: self.mail_windows.pop(key, None)
                    if event.widget is self.mail_windows.get(key) else None)

    def filter_sender(self, address):
        """Show only mails from one sender address (None removes the filter)."""
        self.sender_filter = address
        if address:
            self.sender_filter_button.configure(text=f"Von: {address}  ✕")
            self.sender_filter_button.pack(side="left", padx=(7, 0))
        else:
            self.sender_filter_button.pack_forget()
        self.offset = 0
        self.refresh()

    def delete_selected_mails(self):
        ids = list(self.table.selection())
        if not ids or not self.developer_allowed() or self.busy:
            return
        what = "Die ausgewählte Mail" if len(ids) == 1 else f"Die {len(ids)} ausgewählten Mails"
        if not self.confirm_delete("Mails löschen", what, len(ids)):
            return
        self.run_delete(lambda: self.archive.delete_mails(ids))

    def folder_tree_menu(self, event):
        node = self.folder_tree.identify_row(event.y)
        if not node or not self.developer_allowed():
            return
        self.folder_tree.selection_set(node)
        key, folder = self.folder_nodes.get(node, (None, None))
        if key is None:
            return
        if folder is None:
            account = next((a for k, a, _ in self.archive.folder_tree() if k == key), None)
            entries = [("Konto mit allen Mails aus Archiv löschen …", lambda: self.delete_whole_account(key, account))]
        else:
            entries = [("Ordner aus Archiv löschen …", lambda: self.delete_folders(key, [folder], folder))]
            below = self.folders_below(node)
            if len(below) > 1:
                entries.append(("Ordner mit allen Unterordnern aus Archiv löschen …",
                                lambda: self.delete_folders(key, below, f"{folder} mit Unterordnern")))
        self.popup(event, entries)

    def folders_below(self, node):
        folders = [self.folder_nodes[node][1]]
        for child in self.folder_tree.get_children(node):
            folders.extend(self.folders_below(child))
        return folders

    def delete_folders(self, key, folders, label):
        if self.busy:
            messagebox.showinfo("Löschen", "Bitte warte, bis der laufende Abruf beendet ist.", parent=self.root)
            return
        count = self.archive.count_mails(key, folders)
        if self.confirm_delete("Ordner löschen", f"Die Mails im Ordner „{self.folder_label(label)}“", count):
            self.run_delete(lambda: self.archive.delete_folder_mails(key, folders))

    def delete_whole_account(self, key, account):
        if self.busy:
            messagebox.showinfo("Löschen", "Bitte warte, bis der laufende Abruf beendet ist.", parent=self.root)
            return
        name = account.label if account else "Dieses Konto"
        if self.confirm_delete("Konto löschen",
                               f"Das Konto {name} mit gespeichertem Passwort, Ordnerauswahl, Zeitraum und allen "
                               f"Mails", self.archive.count_mails(key)):
            self.run_delete(lambda: self.archive.delete_account(key))

    def run_delete(self, action):
        try:
            deleted = action()
        except (OSError, sqlite3.Error) as error:
            messagebox.showerror("Löschen fehlgeschlagen", str(error), parent=self.root)
            return
        self.preview_id = None
        self.reload_accounts(self.account_id if self.account_id in self.archive.saved_accounts() else None)
        self.refresh()
        self.status.set(f"{deleted} Mail{'' if deleted == 1 else 's'} aus dem lokalen Archiv gelöscht")

    # --- Updates (from SuperPyBackup: GitHub releases, signature, SHA-256, smoke test, rollback) ---------------

    def build_updates(self, parent):
        frame = ttk.LabelFrame(parent, text=" Updates ", padding=10)
        self.update_choice = tk.BooleanVar(value=self.settings["check_updates"])
        ttk.Checkbutton(frame, variable=self.update_choice, command=self.change_update_check,
                        text="Beim Start und alle 12 Stunden auf neue Versionen prüfen").pack(anchor="w")
        self.update_info = ttk.Label(frame, style="Muted.TLabel", justify="left",
                                     text=f"Installiert: {VERSION_TEXT}")
        self.update_info.pack(anchor="w", fill="x", pady=(4, 6))
        self.update_info.bind("<Configure>", lambda event: self.update_info.configure(wraplength=event.width))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w")
        self.update_check_button = ttk.Button(buttons, text="Jetzt prüfen", command=lambda: self.check_updates(True))
        self.update_check_button.pack(side="left")
        self.update_install_button = ttk.Button(buttons, text="Update installieren …", state="disabled",
                                                command=self.install_update)
        self.update_install_button.pack(side="left", padx=(6, 0))
        self.rollback_button = ttk.Button(buttons, text="Vorversion wiederherstellen", command=self.rollback_update)
        folder = updater.installed_dir()
        if folder and any(Path(folder, name + ".old").is_file() for name in updater.ASSETS):
            self.rollback_button.pack(side="left", padx=(6, 0))
        self.available_update = None
        self.update_running = False
        return frame

    def change_update_check(self):
        self.save_setting("check_updates", self.update_choice.get())
        if self.update_choice.get():
            self.check_updates()

    def scheduled_update_check(self):
        """At program start and every 12 hours afterwards."""
        if self.settings["check_updates"]:
            self.check_updates()
        self.root.after(updater.CHECK_INTERVAL_HOURS * 3600 * 1000, self.scheduled_update_check)

    def check_updates(self, manual=False):
        """Ask GitHub for a newer release in the background; downloads nothing."""
        if self.update_running:
            return
        self.update_running = True
        self.update_check_button.configure(state="disabled")
        if manual:
            self.update_info.configure(text="Prüfe auf neue Versionen …")

        def work():
            try:
                self.events.put(("update_check", (manual, updater.check_for_update(), None)))
            except updater.UpdateError as error:
                self.events.put(("update_check", (manual, None, str(error))))
            except Exception as error:  # Never disturb the program because of an update check.
                self.events.put(("update_check", (manual, None, str(error) or type(error).__name__)))

        threading.Thread(target=work, daemon=True, name="Updateprüfung").start()

    def update_checked(self, manual, info, error):
        self.update_running = False
        self.update_check_button.configure(state="normal")
        if error:
            self.update_info.configure(text=f"Installiert: {VERSION_TEXT} · Prüfung nicht möglich: {error}")
            if manual:
                messagebox.showerror("Updates", f"Die Prüfung auf neue Versionen war nicht möglich.\n{error}",
                                     parent=self.root)
            return
        self.available_update = info
        if info is None:
            self.update_info.configure(text=f"Installiert: {VERSION_TEXT} · Das ist die neueste Version.")
            self.update_install_button.configure(state="disabled")
            return
        self.update_info.configure(text=f"Installiert: {VERSION_TEXT} · Neue Version verfügbar: {info.version}")
        self.update_install_button.configure(state="normal")
        if info.forced and updater.installed_dir():
            self.run_update(info, forced=True)
            return
        if manual or not updater.already_notified(self.data, info.version):
            updater.remember_notified(self.data, info.version)
            self.status.set(f"Neue Version {info.version} verfügbar · Einstellungen → Update installieren")
            if self.root.state() == "withdrawn":
                self.tray.balloon(f"{APP_NAME} {info.version} verfügbar",
                                  "Installieren unter Einstellungen → Updates.")

    def install_update(self):
        info = self.available_update
        if info is None:
            return
        if not updater.installed_dir():
            messagebox.showinfo("Update", "Updates lassen sich nur in der installierten EXE-Version einspielen.",
                                parent=self.root)
            return
        if messagebox.askyesno(
                "Update installieren",
                f"Version {info.version} jetzt herunterladen, prüfen und installieren?\n\n"
                "Installiert wird nur, wenn Signatur und Prüfsummen stimmen. Die bisherige Version bleibt als "
                "Vorversion erhalten.", parent=self.root):
            self.run_update(info)

    def run_update(self, info, forced=False):
        if self.update_running:
            return
        self.update_running = True
        for button in (self.update_check_button, self.update_install_button):
            button.configure(state="disabled")
        self.update_info.configure(text=(f"Pflichtupdate: Version {VERSION_TEXT} ist {info.behind} Versionen alt. "
                                         if forced else "") + f"Version {info.version} wird installiert …")

        def work():
            try:
                updater.install_update(info, updater.installed_dir())
                self.events.put(("update_installed", (info, forced, None)))
            except Exception as error:
                self.events.put(("update_installed", (info, forced, str(error) or type(error).__name__)))

        threading.Thread(target=work, daemon=True, name="Update").start()

    def update_installed(self, info, forced, error):
        self.update_running = False
        self.update_check_button.configure(state="normal")
        if error:
            self.update_install_button.configure(state="normal")
            self.update_info.configure(text=f"Update nicht installiert (bisherige Version unverändert): {error}")
            messagebox.showerror("Update", f"Das Update wurde nicht installiert. Die bisherige Version bleibt "
                                           f"unverändert.\n\n{error}", parent=self.root)
            return
        self.available_update = None
        self.update_info.configure(text=f"Version {info.version} installiert. Wird nach einem Neustart aktiv.")
        self.rollback_button.pack(side="left", padx=(6, 0))
        if self.root.state() == "withdrawn":
            self.show_window()
        title = "Pflichtupdate installiert" if forced else "Update installiert"
        if messagebox.askyesno(title, f"{APP_NAME} {info.version} ist installiert. Jetzt neu starten?",
                               parent=self.root):
            self.restart()

    def rollback_update(self):
        folder = updater.installed_dir()
        if not folder or not messagebox.askyesno(
                "Vorversion wiederherstellen", "Die Version vor dem letzten Update wiederherstellen? Danach startet "
                f"{APP_NAME} neu.", parent=self.root):
            return
        try:
            updater.rollback(folder)
        except (updater.UpdateError, OSError) as error:
            messagebox.showerror("Vorversion", str(error), parent=self.root)
            return
        self.restart()

    def restart(self):
        """Start the (new) program shortly after this one has ended (only one instance may run)."""
        program = f'"{sys.executable}"'
        if not getattr(sys, "frozen", False):  # Started as script (development): run the script again.
            program += f' "{Path(sys.argv[0]).resolve()}"'
        if self.quit():
            subprocess.Popen(f'cmd /c ping -n 3 127.0.0.1 >nul & start "" {program}',
                             creationflags=0x08000000)  # No console window.

    def change_spam_threshold(self):
        self.save_setting("spam_threshold", int(self.spam_choice.get()))
        self.refresh()

    def change_tray(self):
        enabled = self.tray_choice.get()
        self.save_setting("tray", enabled)
        self.tray.set_visible(enabled)

    def change_autostart(self, enabled=None):
        enabled = self.autostart_choice.get() if enabled is None else enabled
        try:
            set_autostart(enabled)
        except OSError as error:
            messagebox.showerror("Autostart", f"Der Autostart konnte nicht geändert werden.\n{error}", parent=self.root)
        self.update_autostart()

    def update_autostart(self):
        entry = autostart_entry()
        self.autostart_choice.set(entry is not None)
        self.hidden_button.configure(state="normal" if entry else "disabled")
        moved = entry is not None and entry.casefold() != autostart_command().casefold()
        self.autostart_note.configure(
            text=f"Der Autostart startet ein Programm an einem anderen Ort:\n{entry}" if moved else "")
        if moved:
            self.autostart_note.pack(anchor="w", fill="x", padx=(24, 0), pady=(4, 0))
            self.autostart_fix.pack(anchor="w", padx=(24, 0), pady=(4, 0))
        else:
            self.autostart_note.pack_forget()
            self.autostart_fix.pack_forget()

    PERIOD_CHOICES = (("", "Unbegrenzt"), ("today", "Heute"), ("yesterday", "Gestern"),
                      ("week", "Vor 1 Woche"), ("month", "Monat/Jahr"))
    MONTH_NAMES = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
                   "Oktober", "November", "Dezember")

    def build_period(self, page):
        frame = ttk.LabelFrame(page, text=" Zeitraum für die Archivierung ", padding=10)
        self.period_widgets = {}
        this_year = date.today().year
        years = [str(year) for year in range(this_year, this_year - 40, -1)]
        for column, (side, label) in enumerate((("from", "Von"), ("to", "Bis"))):
            row = ttk.Frame(frame)
            row.grid(row=0, column=column, sticky="w", padx=(0, 24))
            ttk.Label(row, text=label, width=4).pack(side="left")
            kind = ttk.Combobox(row, state="readonly", width=13, values=[name for _, name in self.PERIOD_CHOICES])
            month = ttk.Combobox(row, state="readonly", width=10, values=self.MONTH_NAMES)
            year = ttk.Combobox(row, state="readonly", width=6, values=years)
            for widget in (kind, month, year):
                widget.pack(side="left", padx=(0, 4))
                widget.bind("<<ComboboxSelected>>", lambda _: self.change_period())
            self.period_widgets[side] = (kind, month, year)
        self.period_info = ttk.Label(frame, style="Muted.TLabel")
        self.period_info.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.skip_deleted = tk.BooleanVar(value=True)
        self.skip_deleted_button = ttk.Checkbutton(
            frame, variable=self.skip_deleted, command=self.change_skip_deleted,
            text="Als gelöscht markierte Mails nicht archivieren (vom Mailprogramm ausgeblendet, aber noch "
                 "auf dem Server)")
        self.skip_deleted_button.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        return frame

    def change_skip_deleted(self):
        if self.account_id is None:
            return
        try:
            self.archive.set_skip_deleted(self.account_id, self.skip_deleted.get())
        except (OSError, sqlite3.Error) as error:
            messagebox.showerror("Gelöschte Mails", str(error), parent=self.root)

    def period_spec(self, side):
        kind, month, year = self.period_widgets[side]
        choice = self.PERIOD_CHOICES[kind.current()][0] if kind.current() >= 0 else ""
        if choice != "month":
            return choice
        return f"{int(year.get()):04d}-{month.current() + 1:02d}"

    def show_period(self):
        """Show the archive period of the selected account."""
        saved = self.archive.get_period(self.account_id) if self.account_id is not None else ("", "")
        today = date.today()
        for side, spec in zip(("from", "to"), saved):
            kind, month, year = self.period_widgets[side]
            keys = [key for key, _ in self.PERIOD_CHOICES]
            if spec in keys:
                kind.current(keys.index(spec))
                month.current(today.month - 1)
                year.set(str(today.year))
            else:
                kind.current(keys.index("month"))
                month.current(int(spec[5:7]) - 1)
                year.set(spec[:4])
        self.skip_deleted.set(self.archive.get_skip_deleted(self.account_id) if self.account_id is not None else True)
        self.skip_deleted_button.configure(state="normal" if self.account_id is not None else "disabled")
        self.update_period_widgets()

    def update_period_widgets(self):
        saved = self.account_id is not None
        for kind, month, year in self.period_widgets.values():
            kind.configure(state="readonly" if saved else "disabled")
            by_month = saved and self.PERIOD_CHOICES[kind.current()][0] == "month"
            for widget in (month, year):
                widget.configure(state="readonly" if by_month else "disabled")
        try:
            start, end = resolve_period(self.period_spec("from"), self.period_spec("to"))
        except ValueError as error:
            self.period_info.configure(text=str(error))
            return
        if not saved:
            text = ""
        elif not start and not end:
            text = "Alle Mails werden archiviert, unabhängig vom Datum."
        else:
            if start and end:
                period = f"vom {start:%d.%m.%Y} bis {end:%d.%m.%Y}"
            elif start:
                period = f"ab dem {start:%d.%m.%Y}"
            else:
                period = f"bis einschließlich {end:%d.%m.%Y}"
            text = (f"Archiviert werden Mails {period}. Maßgeblich ist das Eingangsdatum auf dem Server. "
                    "Bereits gesicherte Mails bleiben erhalten.")
        self.period_info.configure(text=text)

    def change_period(self):
        if self.account_id is None:
            return
        try:
            self.archive.set_period(self.account_id, self.period_spec("from"), self.period_spec("to"))
        except (ValueError, OSError, sqlite3.Error) as error:
            messagebox.showwarning("Zeitraum", str(error), parent=self.root)
            self.show_period()
            return
        self.update_period_widgets()

    def build_folder_choice(self, parent):
        frame = ttk.LabelFrame(parent, text=" Zu archivierende Ordner ", padding=8)
        bar = ttk.Frame(frame)
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.load_folders_button = ttk.Button(bar, text="Ordnerliste vom Server laden",
                                              command=self.load_server_folders)
        self.load_folders_button.pack(side="left")
        # Start with a wrap width so the long hint does not claim the space of the account list.
        self.folder_hint = ttk.Label(frame, style="Muted.TLabel", justify="left", wraplength=280)
        self.folder_hint.pack(side="bottom", fill="x", pady=(6, 0))
        self.folder_hint.bind("<Configure>", lambda event: self.folder_hint.configure(wraplength=event.width))
        self.folder_choice = ttk.Treeview(frame, columns=("server", "deleted", "archived"), show="tree headings",
                                          selectmode="browse", height=6)
        self.folder_choice.heading("#0", text="Ordner", anchor="w")
        self.folder_choice.column("#0", width=200, minwidth=120)
        for column, title, width in (("server", "Auf Server", 85), ("deleted", "Gelöscht markiert", 120),
                                     ("archived", "Archiviert", 85)):
            self.folder_choice.heading(column, text=title, anchor="e")
            self.folder_choice.column(column, width=width, minwidth=70, anchor="e", stretch=False)
        scroll = ttk.Scrollbar(frame, command=self.folder_choice.yview)
        self.folder_choice.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.folder_choice.pack(fill="both", expand=True)
        self.folder_choice.bind("<Button-1>", self.click_folder_choice)
        self.folder_choice.bind("<space>", lambda _: self.toggle_folder(self.folder_choice.focus()))
        self.folder_choice.bind("<Button-3>", self.folder_menu)
        self.choice_paths = {}
        self.choice_state = {}
        self.loading_folders = False
        return frame

    def show_account_folders(self):
        """Fill the checkbox tree with the folders of the selected account."""
        tree = self.folder_choice
        opened = {self.choice_paths[node] for node in self.choice_paths if tree.item(node, "open")}
        tree.delete(*tree.get_children())
        self.choice_paths, self.choice_state, self.choice_labels = {}, {}, {}
        folders = self.archive.account_folders(self.account_id) if self.account_id is not None else []
        created = {}
        for name, delimiter, role, chosen, count, on_server, deleted in folders:
            self.choice_state[name] = chosen
            parts = name.split(delimiter) if delimiter else [name]
            parent = ""
            for depth in range(1, len(parts) + 1):
                path = (delimiter or "").join(parts[:depth])
                if path not in created:
                    # Intermediate nodes (e.g. "[Gmail]") may not be folders themselves.
                    created[path] = node = f"c{len(created)}"
                    self.choice_paths[node] = path
                    self.choice_labels[node] = (parts[depth - 1], None, None, None)
                    tree.insert(parent, "end", iid=node, open=path in opened or not opened)
                parent = created[path]
            self.choice_labels[parent] = ("Posteingang" if role == "inbox" else parts[-1], count, on_server, deleted)
        self.update_folder_marks()
        if self.account_id is None:
            hint = ""
        elif not folders:
            hint = ("Noch keine Ordnerliste. Lade sie vom Server, um Ordner vor dem ersten Abruf auszuwählen. "
                    "Ohne Auswahl werden alle Ordner außer Spam und Papierkorb archiviert.")
        else:
            hint = ("Angehakte Ordner werden beim nächsten Abruf archiviert. Ein Klick schaltet nur diesen Ordner, "
                    "ein Rechtsklick bietet an, alle Unterordner mitzuschalten. Abgewählte Ordner behalten ihre "
                    "bereits gesicherten Mails. Spam und Papierkorb sind anfangs abgewählt.")
        self.folder_hint.configure(text=hint)

    def folder_names_below(self, node):
        """Real folders at and below a tree node."""
        names = [self.choice_paths[node]] if self.choice_paths[node] in self.choice_state else []
        for child in self.folder_choice.get_children(node):
            names.extend(self.folder_names_below(child))
        return names

    def update_folder_marks(self):
        for node, path in self.choice_paths.items():
            label, count, on_server, deleted = self.choice_labels[node]
            if path in self.choice_state:
                # A real folder shows its own state; its subfolders have their own boxes.
                mark = "☑" if self.choice_state[path] else "☐"
            else:
                states = {self.choice_state[name] for name in self.folder_names_below(node)}
                mark = "☑" if states == {True} else "☐" if states == {False} else "◩"
            real = path in self.choice_state
            self.folder_choice.item(node, text=f"{mark}  {label}",
                                    values=(self.number(on_server), self.number(deleted), self.number(count))
                                    if real else ("", "", ""))

    @staticmethod
    def number(value):
        """German thousands separator; a dash for an unknown count."""
        return "–" if value is None else f"{value:,}".replace(",", ".")

    def click_folder_choice(self, event):
        node = self.folder_choice.identify_row(event.y)
        if not node or "indicator" in self.folder_choice.identify_element(event.x, event.y):
            return None
        self.folder_choice.focus(node)
        self.folder_choice.selection_set(node)
        self.toggle_folder(node)
        return "break"

    def folder_menu(self, event):
        node = self.folder_choice.identify_row(event.y)
        if not node or self.account_id is None or len(self.folder_names_below(node)) < 2:
            return
        self.folder_choice.focus(node)
        self.folder_choice.selection_set(node)
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Mit allen Unterordnern archivieren",
                         command=lambda: self.choose_folders(self.folder_names_below(node), True))
        menu.add_command(label="Mit allen Unterordnern nicht archivieren",
                         command=lambda: self.choose_folders(self.folder_names_below(node), False))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def toggle_folder(self, node):
        """Click: a real folder switches only itself, e.g. just the mails directly in the inbox.

        A node that is no folder of its own (e.g. "[Gmail]") switches everything below it.
        """
        if not node or self.account_id is None:
            return
        path = self.choice_paths[node]
        names = [path] if path in self.choice_state else self.folder_names_below(node)
        if names:
            self.choose_folders(names, not all(self.choice_state[name] for name in names))

    def choose_folders(self, names, selected):
        try:
            self.archive.set_folder_selection(self.account_id, names, selected)
        except (OSError, sqlite3.Error) as error:
            messagebox.showerror("Ordnerauswahl", str(error), parent=self.root)
            return
        for name in names:
            self.choice_state[name] = selected
        self.update_folder_marks()
        self.reload_filters()

    def account_and_password(self):
        """Saved account of the form with the entered or saved password; raises ValueError."""
        account = self.accounts[self.account_id]
        if self.account_from_form() != account:
            raise ValueError("Speichere zuerst die geänderten Kontodaten.")
        return account, clean_password(self.password.get() or self.archive.account_password(self.account_id) or "")

    def load_server_folders(self):
        if self.account_id is None or self.loading_folders:
            return
        try:
            account, password = self.account_and_password()
        except (ValueError, OSError, sqlite3.Error) as error:
            messagebox.showwarning("Ordnerliste", str(error), parent=self.root)
            return
        self.loading_folders = True
        self.update_account_buttons()
        self.status.set(f"{account.user} · Ordnerliste wird geladen …")
        key = self.account_id

        def work():
            try:
                self.archive.load_folders(key, account, password)
                self.events.put(("folders", (key, None)))
            except Exception as error:
                self.events.put(("folders", (key, str(error) or type(error).__name__)))

        threading.Thread(target=work, daemon=True).start()

    def build_accounts(self):
        page = self.accounts_tab
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        buttons = ttk.Frame(page)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(buttons, text="Neues Konto", command=lambda: self.edit_account()).pack(side="left")
        self.remove_button = ttk.Button(buttons, text="Konto entfernen", command=self.delete_account)
        self.remove_button.pack(side="right")
        top = ttk.Panedwindow(page, orient="horizontal")
        top.grid(row=1, column=0, sticky="nsew")
        listing = ttk.Frame(top, padding=(0, 0, 6, 0))
        top.add(listing, weight=3)
        top.add(self.build_folder_choice(top), weight=2)

        def place_sash(event):
            # Split once when the tab is first shown; afterwards the user's position is kept.
            if event.width > 1 and not getattr(top, "placed", False):
                top.placed = True
                top.sashpos(0, event.width * 45 // 100)

        top.bind("<Configure>", place_sash, add="+")
        self.account_table = ttk.Treeview(listing, columns=("user", "server"),
                                         show="headings", selectmode="browse", height=5)
        for key, title, width in (("user", "Benutzername", 170), ("server", "IMAP-Server", 150)):
            self.account_table.heading(key, text=title)
            self.account_table.column(key, width=width)
        scroll = ttk.Scrollbar(listing, command=self.account_table.yview)
        self.account_table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.account_table.pack(fill="both", expand=True)
        self.account_table.bind("<<TreeviewSelect>>", self.pick_account)

        form = ttk.LabelFrame(page, text=" Verbindung ", padding=10)
        form.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.values = {}
        for index, (key, label, width) in enumerate((
            ("host", "Server", 20), ("port", "TLS-Port", 7),
            ("user", "Benutzername", 25),
        )):
            var = self.values[key] = tk.StringVar()
            ttk.Label(form, text=label).grid(row=0, column=index, sticky="w", padx=3)
            entry = ttk.Entry(form, textvariable=var, width=width)
            entry.grid(row=1, column=index, sticky="ew", padx=3, pady=(3, 0))
            form.columnconfigure(index, weight=0 if key == "port" else 1)
            if key == "host":
                self.host_entry = entry
        ttk.Button(form, text="Konto speichern", command=self.store_account).grid(row=1, column=3, padx=(8, 0))

        self.build_period(page).grid(row=3, column=0, sticky="ew", pady=(9, 0))
        fetch = ttk.LabelFrame(page, text=" Abruf für das ausgewählte Konto ", padding=10)
        fetch.grid(row=4, column=0, sticky="ew", pady=(9, 0))
        self.password = tk.StringVar()
        credentials = ttk.Frame(fetch)
        credentials.pack(fill="x")
        ttk.Label(credentials, text="Passwort / App-Passwort").pack(side="left", padx=(0, 7))
        entry = ttk.Entry(credentials, textvariable=self.password, show="*", width=25)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _: self.start_sync())
        self.sync_button = ttk.Button(credentials, text="Abrufen", style="Accent.TButton", command=self.start_sync)
        self.sync_button.pack(side="left", padx=(8, 0))
        self.abort_button = ttk.Button(credentials, text="Stopp", command=self.stop_sync, state="disabled")
        self.abort_button.pack(side="left", padx=(4, 0))
        options = ttk.Frame(fetch)
        options.pack(fill="x", pady=(6, 0))
        self.store_password_button = ttk.Button(options, text="Passwort speichern", command=self.store_password)
        self.store_password_button.pack(side="left")
        self.clear_password_button = ttk.Button(options, text="Passwort vergessen",
                                                command=lambda: self.store_password(clear=True))
        self.clear_password_button.pack(side="left", padx=5)
        self.password_status = ttk.Label(options, style="Muted.TLabel")
        self.password_status.pack(side="left", padx=5)
        self.has_password = False

    def current_filter(self):
        """(account_id, folder) of the selected tree node; None values mean no restriction."""
        selected = self.folder_tree.selection()
        return self.folder_nodes.get(selected[0], (None, None)) if selected else (None, None)

    def select_folder(self):
        # Rebuilding the tree also fires the selection event; only react to real changes.
        if self.current_filter() != getattr(self, "active_filter", None):
            self.active_filter = self.current_filter()
            self.set_filter()

    def set_filter(self):
        self.offset = 0
        self.refresh()

    def reload_filters(self):
        """Rebuild the folder tree: all accounts → account → folders (nested by the server's delimiter)."""
        current = self.current_filter()
        tree = self.folder_tree
        opened = {self.folder_nodes.get(node) for node in self.folder_nodes if tree.item(node, "open")}
        tree.delete(*tree.get_children())
        self.folder_nodes = {"all": (None, None)}
        tree.insert("", "end", iid="all", text="Alle Konten", open=True)
        for key, account, folders in self.archive.folder_tree():
            account_node = f"a{key}"
            self.folder_nodes[account_node] = (key, None)
            tree.insert("all", "end", iid=account_node, text=f"{account.user} · {account.host}",
                        open=(key, None) in opened or not opened)
            created = {}
            for name, delimiter, role, count in folders:
                parts = name.split(delimiter) if delimiter else [name]
                parent = account_node
                for depth in range(1, len(parts) + 1):
                    path = (delimiter or "").join(parts[:depth])
                    if path not in created:
                        node = f"f{len(self.folder_nodes)}"
                        created[path] = node
                        self.folder_nodes[node] = (key, path)
                        tree.insert(parent, "end", iid=node, text=parts[depth - 1],
                                    open=(key, path) in opened)
                    parent = created[path]
                # Servers name their folders themselves (e.g. "Gesendet"); only INBOX gets a German label.
                label = "Posteingang" if role == "inbox" else parts[-1]
                tree.item(parent, text=f"{label} ({count})" if count else label)
        selection = next((node for node, value in self.folder_nodes.items() if value == current), "all")
        tree.selection_set(selection)
        tree.see(selection)
        self.active_filter = self.folder_nodes[selection]

    def reload_accounts(self, select=None):
        self.reload_filters()
        self.accounts = self.archive.saved_accounts()
        self.account_table.delete(*self.account_table.get_children())
        for key, account in self.accounts.items():
            self.account_table.insert("", "end", iid=str(key),
                                      values=(account.user, f"{account.host}:{account.port}"))
        if select in self.accounts:
            self.account_table.selection_set(str(select))
        self.edit_account(select if select in self.accounts else None, focus=False)

    def pick_account(self, _=None):
        selected = self.account_table.selection()
        if selected and selected[0] != self.account_id:
            self.edit_account(selected[0], focus=False)

    def edit_account(self, key=None, focus=True):
        self.account_id = key
        if key is None:
            self.account_table.selection_remove(*self.account_table.selection())
        account = self.accounts.get(key, Account("", 993, ""))
        for name, value in vars(account).items():
            self.values[name].set(value)
        self.password.set("")
        self.has_password = key is not None and self.archive.account_password(key) is not None
        self.update_account_buttons()
        self.show_account_folders()
        self.show_period()
        if focus:
            self.host_entry.focus_set()

    def update_account_buttons(self):
        saved = self.account_id is not None
        self.remove_button.configure(state="normal" if saved else "disabled")
        self.sync_button.configure(state="normal" if saved and not self.busy and not self.maintenance_running
                                   else "disabled")
        self.load_folders_button.configure(state="normal" if saved and not self.loading_folders else "disabled")
        self.store_password_button.configure(state="normal" if saved else "disabled")
        self.clear_password_button.configure(state="normal" if self.has_password else "disabled")
        self.password_status.configure(text="Passwort verschlüsselt gespeichert" if self.has_password else "")

    def store_password(self, clear=False):
        account = self.accounts.get(self.account_id)
        if account is None:
            return
        try:
            if not clear and self.account_from_form() != account:
                raise ValueError("Speichere zuerst die geänderten Kontodaten.")
            password = None if clear else clean_password(self.password.get())
            self.archive.remember_password(self.account_id, account, password)
        except (OSError, ValueError, sqlite3.Error) as error:
            messagebox.showerror("Passwort speichern", str(error), parent=self.root)
            return
        self.password.set("")
        self.has_password = password is not None
        self.update_account_buttons()

    def account_from_form(self):
        host, port, user = (self.values[k].get().strip() for k in ("host", "port", "user"))
        if not re.fullmatch(r"[^\s/\\]+", host) or not user or not port.isascii() or not port.isdecimal():
            raise ValueError("Bitte Server, TLS-Port und Benutzername eingeben.")
        if any(char in host + user for char in "\r\n\0"):
            raise ValueError("Server und Benutzername müssen jeweils in einer Zeile stehen.")
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("Der TLS-Port muss zwischen 1 und 65535 liegen.")
        return Account(host.lower(), port, user)

    def store_account(self):
        try:
            account = self.account_from_form()
            key = self.archive.save_account(account, self.account_id)
        except (OSError, ValueError, sqlite3.Error) as error:
            text = "Dieses Konto ist bereits eingerichtet." if isinstance(error, sqlite3.IntegrityError) else str(error)
            messagebox.showerror("Konto speichern", text, parent=self.root)
            return
        self.reload_accounts(key)
        self.set_filter()
        if not self.busy:
            self.status.set(f"Konto gespeichert · {account.user}")

    def delete_account(self):
        if self.account_id is None or not messagebox.askyesno(
            "Konto entfernen", "Dieses Konto aus der Kontenliste entfernen? Archivierte Mails bleiben im Archiv.",
            parent=self.root
        ):
            return
        try:
            self.archive.remove_account(self.account_id)
        except (OSError, ValueError, sqlite3.Error) as error:
            messagebox.showerror("Konto entfernen", str(error), parent=self.root)
            return
        self.reload_accounts()
        self.set_filter()

    @staticmethod
    def date_label(timestamp, with_time=False):
        if not timestamp:
            return "—"
        try:
            return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")
        except (ValueError, OverflowError, OSError):
            return "—"

    def schedule_search(self, *_):
        if self.pending_search is not None:
            self.root.after_cancel(self.pending_search)
        self.pending_search = self.root.after(240, self.new_search)

    def new_search(self):
        self.pending_search = None
        self.offset = 0
        self.refresh()

    def change_page(self, delta):
        self.offset += delta
        self.refresh()

    # Narrow fixed columns for short values; subject and sender take the remaining width.
    COLUMNS = {"attach": ("📎", 34), "subject": ("Betreff", 215), "sender": ("Von", 145), "date": ("Datum", 80),
               "time": ("Uhrzeit", 58), "size": ("Größe", 64), "spam": ("Spam", 54)}
    ANCHORS = {"attach": "center", "time": "center", "size": "e", "spam": "e"}
    # Longest values of the short columns; their width is measured from these and the heading with its sort arrow.
    SAMPLES = {"attach": ("📎",), "date": ("88.88.8888",), "time": ("88:88",), "size": ("888 KB", "88,8 MB"),
               "spam": ("⚠ 88,8", "-88,8")}

    def fit_columns(self, _event=None):
        """Subject and sender share the space left by the short columns (60:40), so every column stays visible."""
        fixed = sum(int(self.table.column(key, "width")) for key in self.COLUMNS if key not in ("subject", "sender"))
        free = max(self.table.winfo_width() - fixed - 4, 120)
        subject = int(free * 0.6)
        self.table.column("subject", width=subject)
        self.table.column("sender", width=free - subject)

    def fit_width(self, key):
        """Just wide enough for the column's values and heading (in screen pixels, so it follows the scaling)."""
        text = tkfont.Font(root=self.root, family="Segoe UI", size=9)
        label = self.COLUMNS[key][0]
        needed = max([text.measure(sample) for sample in self.SAMPLES[key]] + [text.measure(label + " ▼")])
        return needed + text.measure("000")  # Cell padding of the table plus a small gap to the next column.
    PAPERCLIP = "📎"

    def sort_by(self, key):
        """Click on a column heading: sort by it; a second click reverses the order."""
        if self.settings["sort"] == key:
            descending = not self.settings["sort_descending"]
        else:
            # Newest, largest and mails with attachments first; texts A–Z.
            descending = key in ("date", "time", "attach", "size", "spam")
        self.save_setting("sort", key)
        self.save_setting("sort_descending", descending)
        self.update_sort_headings()
        self.offset = 0
        self.refresh()

    def background_work(self):
        """First the list and search indexes (needed for a fast list and search), then spam and paperclips."""
        self.prepare_list_indexes()
        self.index_search()
        self.read_spam_ratings()
        self.detect_attachments()

    def read_spam_ratings(self):
        """Background: take over the server's spam rating for mails archived before 1.15.0 (visible ones first)."""
        while True:
            try:
                visible = self.visible_unrated
                self.visible_unrated = set()
                batch = self.archive.unknown_spam(mail_ids=visible) if visible else []
                if not batch:
                    batch = self.archive.unknown_spam()
                if not batch:
                    self.events.put(("spam_progress", None))
                    return
                rated = {}
                for mail_id, head in batch:
                    self.pause_for_interface()
                    try:
                        info = spam_info(head)
                        rated[mail_id] = (info["score"], int(info["flag"]), int(info["dmarc_fail"]), 1)
                    except Exception:
                        rated[mail_id] = (None, 0, 0, 1)
                self.archive.store_spam(rated)
                progress = self.archive.spam_progress()
            except (OSError, sqlite3.Error):
                time.sleep(5)
                continue
            self.events.put(("spam_ratings", rated))
            self.events.put(("spam_progress", progress))
            time.sleep(0.02)

    def spam_suspicious(self, score, flag, dmarc_fail):
        return bool(flag or dmarc_fail or (score is not None and score >= self.settings["spam_threshold"]))

    def spam_label(self, score, flag, dmarc_fail):
        """Score of the server's spam rating, with ⚠ for suspicious mails; empty if the mail was not rated."""
        if score is None and not flag and not dmarc_fail:
            return ""
        text = "" if score is None else f"{score:.1f}".replace(".", ",")
        return ("⚠ " + text).strip() if self.spam_suspicious(score, flag, dmarc_fail) else text

    def spam_explanation(self, raw):
        """Line for the preview: why the server considers a mail suspicious (or that it is not)."""
        try:
            info = spam_info(raw)
        except Exception:
            return ""
        if info["score"] is None and not info["flag"] and not info["dmarc_fail"]:
            return ""
        parts = []
        if info["score"] is not None:
            parts.append(f"{info['score']:.1f}".replace(".", ",") + f" Punkte (Grenze {self.settings['spam_threshold']})")
        if info["flag"]:
            parts.append("vom Server als Spam markiert")
        if info["dmarc_fail"]:
            parts.append("Absender nicht bestätigt (DMARC fail)")
        suspicious = self.spam_suspicious(info["score"], info["flag"], info["dmarc_fail"])
        reasons = f" · Gründe: {', '.join(info['reasons'][:4])}" if suspicious and info["reasons"] else ""
        return ("⚠ Spamverdacht: " if suspicious else "Spam-Bewertung: ") + ", ".join(parts) + reasons

    def prepare_list_indexes(self):
        """Once for archives from before 1.16.1: indexes that let the mail list skip the large mail rows."""
        while True:
            try:
                if self.archive.list_indexes_ready():
                    return
                self.events.put(("list_indexes", False))
                self.archive.create_list_indexes()
                self.events.put(("list_indexes", True))
                return
            except (OSError, sqlite3.Error):
                time.sleep(5)  # E.g. a sync is writing; try again.

    def index_search(self):
        """Add all not yet indexed mails to the search index (one run at a time)."""
        if not self.index_lock.acquire(blocking=False):
            return
        try:
            while True:
                self.pause_for_interface()
                try:
                    indexed, total = self.archive.build_search_index()
                except (OSError, sqlite3.Error):
                    time.sleep(5)  # E.g. database busy; try again later.
                    continue
                self.events.put(("index_progress", (indexed, total)))
                if indexed >= total:
                    break
                time.sleep(0.05)
        finally:
            self.index_lock.release()

    def start_search_index(self):
        """After "Suchindex neu aufbauen": rebuild the index in the background."""
        threading.Thread(target=self.index_search, daemon=True, name="Suchindex").start()

    def pause_for_interface(self):
        while self.foreground.is_set() or self.maintenance_pause.is_set():
            time.sleep(0.01)

    def detect_attachments(self):
        """Background: determine the paperclip for mails archived before 1.12.0.

        Mails visible in the list come first, then all others from new to old, in small batches.
        """
        while True:
            try:
                visible = self.visible_unknown
                self.visible_unknown = set()
                batch = self.archive.unknown_attachment_flags(mail_ids=visible) if visible else []
                if not batch:
                    batch = self.archive.unknown_attachment_flags(100)
                if not batch:
                    self.events.put(("attachment_progress", None))
                    return
                flags = {}
                for mail_id, raw in batch:
                    self.pause_for_interface()
                    flags[mail_id] = has_attachments(raw)
                self.archive.store_attachment_flags(flags)
                progress = self.archive.attachment_progress()
            except (OSError, sqlite3.Error):
                time.sleep(5)  # E.g. database busy; try again later.
                continue
            self.events.put(("attachment_flags", {k: (v or 0) for k, v in flags.items()}))
            self.events.put(("attachment_progress", progress))
            time.sleep(0.02)  # Leave room for the user interface and running syncs.

    def update_sort_headings(self):
        for key, (label, _) in self.COLUMNS.items():
            arrow = (" ▼" if self.settings["sort_descending"] else " ▲") if key == self.settings["sort"] else ""
            self.table.heading(key, text=label + arrow)

    @staticmethod
    def size_label(size):
        """Size of the original mail in B, KB or MB with a German decimal comma."""
        if size is None:
            return "—"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / 1024 / 1024:.1f} MB".replace(".", ",")

    @staticmethod
    def time_label(timestamp):
        if not timestamp:
            return "—"
        try:
            return datetime.fromtimestamp(timestamp).strftime("%H:%M")
        except (ValueError, OverflowError, OSError):
            return "—"

    def refresh(self):
        selected = self.table.selection()
        self.foreground.set()
        try:
            rows = self.archive.find_mails(self.query.get(), self.offset, PAGE + 1, *self.current_filter(),
                              sort=self.settings["sort"], descending=self.settings["sort_descending"],
                              sender=self.sender_filter, only_suspicious=self.only_suspicious.get(),
                              threshold=self.settings["spam_threshold"])
        finally:
            self.foreground.clear()
        self.table.delete(*self.table.get_children())
        # Let the background detection check the mails on this page first.
        self.visible_unknown = {row["key"] for row in rows[:PAGE] if row["has_attachments"] is None}
        self.visible_unrated = {row["key"] for row in rows[:PAGE] if row["spam_checked"] is None}
        for row in rows[:PAGE]:
            self.table.insert("", "end", iid=row["key"],
                              values=(self.PAPERCLIP if row["has_attachments"] else "",
                                      row["subject"] or "(Ohne Betreff)", row["sender"], self.date_label(row["sent"]),
                                      self.time_label(row["sent"]), self.size_label(row["size"]),
                                      self.spam_label(row["spam_score"], row["spam_flag"], row["dmarc_fail"])))
        count = min(len(rows), PAGE)
        self.page_label.configure(text=f"{self.offset + 1}–{self.offset + count}" if count else "Keine Treffer")
        self.prev.configure(state="normal" if self.offset else "disabled")
        self.next.configure(state="normal" if len(rows) > PAGE else "disabled")
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        self.show_selection()

    def show_selection(self, _=None):
        selected = self.table.selection()
        mail_id = selected[0] if selected else None
        row = self.archive.get_mail(mail_id) if mail_id is not None else None
        self.subject.configure(text=(row["subject"] or "(Ohne Betreff)") if row else "Mailvorschau")
        self.meta.configure(text=(
            f"Von: {row['sender'] or '—'}\nAn: {row['recipient'] or '—'}\n"
            f"{self.date_label(row['sent'], True)} · {self.size_label(row['size'])}\n"
            f"Konto: {row['user']} · {row['host']}:{row['port']} · Ordner: {self.folder_label(row['folder'])}"
        ) if row else "Wähle eine Nachricht aus der Liste.")
        self.mail_id.set(row["mail_id"] if row else "")
        self.copy_button.configure(state="normal" if row else "disabled")
        raw = self.archive.get_mail(mail_id, raw=True)["raw"] if row else None
        if raw is not None:
            spam = self.spam_explanation(raw)
            if spam:
                self.meta.configure(text=self.meta.cget("text") + "\n" + spam)
        text = ((display_text(raw) if raw is not None else None) or row["body"]
                or "(Kein Textinhalt. Das Original enthält auch etwaige Anhänge.)") if row else ""
        self.show_body(text)
        self.export.configure(state="normal" if row else "disabled")
        self.delete_button.configure(state="normal" if row else "disabled")
        if mail_id != self.preview_id:
            self.preview_id = mail_id
            count = self.attachments.show_mail(raw)
            self.preview_tabs.tab(self.attachments, text=f"Anhänge ({count})" if count else "Anhänge")
            self.preview_tabs.select(0)

    # http(s) and mailto addresses, plus "www." without a scheme; trailing punctuation is not part of a link.
    LINK_PATTERN = re.compile(r"(?:https?://|mailto:|www\.)[^\s<>\"'«»]+[^\s<>\"'«».,;:!?)\]}]", re.I)

    def show_body(self, text):
        """Show the message text; web and mail addresses become clickable links."""
        body = self.body
        body.configure(state="normal")
        body.delete("1.0", "end")
        for tag in body.tag_names():
            if tag.startswith("link-"):
                body.tag_delete(tag)
        self.links = {}
        position = 0
        for match in self.LINK_PATTERN.finditer(text):
            body.insert("end", text[position:match.start()])
            url = match.group()
            if url.lower().startswith("www."):
                url = "http://" + url
            tag = f"link-{len(self.links)}"
            self.links[tag] = url
            body.insert("end", match.group(), ("link", tag))
            position = match.end()
        body.insert("end", text[position:])
        body.configure(state="disabled")

    def link_at(self, event):
        return next((self.links[tag] for tag in self.body.tag_names(f"@{event.x},{event.y}")
                     if tag in self.links), None)

    def hover_link(self, event):
        url = self.link_at(event)
        self.body.configure(cursor="hand2" if url else "")
        if url != getattr(self, "hovered_link", None):
            self.hovered_link = url
            if url:
                self.status_before_link = self.status.get()
                self.status.set(f"Link öffnen: {url}")
            elif getattr(self, "status_before_link", None) is not None:
                # Restore the previous status unless something (e.g. a sync) has changed it meanwhile.
                if self.status.get().startswith("Link öffnen: "):
                    self.status.set(self.status_before_link)
                self.status_before_link = None

    def open_link(self, event):
        url = self.link_at(event)
        if url and re.match(r"(?:https?://|mailto:)", url, re.I):
            try:
                webbrowser.open(url)
            except Exception as error:
                messagebox.showerror("Link öffnen", f"Der Link konnte nicht geöffnet werden.\n{error}",
                                     parent=self.root)

    def copy_mail_id(self):
        identifier = self.mail_id.get()
        if identifier:
            self.root.clipboard_clear()
            self.root.clipboard_append(identifier)

    def export_mail(self):
        selected = self.table.selection()
        if not selected:
            return
        destination = filedialog.asksaveasfilename(parent=self.root, defaultextension=".eml",
                                                   initialfile=f"Mail-{selected[0].replace(':', '-')}.eml",
                                                   filetypes=[("E-Mail", "*.eml")])
        if destination:
            try:
                Path(destination).write_bytes(self.archive.get_mail(selected[0], raw=True)["raw"])
            except OSError as error:
                messagebox.showerror("Export fehlgeschlagen", str(error), parent=self.root)

    def start_sync(self):
        if self.busy or self.maintenance_running or self.account_id is None:
            return
        if self.list_preparing:
            messagebox.showinfo("Abrufen", "Das Archiv wird gerade einmalig vorbereitet. Bitte in einer Minute "
                                "noch einmal versuchen.", parent=self.root)
            return
        try:
            account, password = self.account_and_password()
        except (ValueError, OSError, sqlite3.Error) as error:
            messagebox.showwarning("IMAP-Konto", str(error), parent=self.root)
            return

        self.password.set("")
        self.run_sync([(self.account_id, account, password)], automatic=False)

    def run_sync(self, jobs, automatic):
        self.sync_auto = automatic
        self.sync_user = jobs[0][1].user
        self.cancelled.clear()
        self.set_sync_state(True)

        def work():
            saved, missing, failed, errors = 0, 0, 0, []
            for key, account, password in jobs:
                if self.cancelled.is_set():
                    break
                self.events.put(("account", account.user))
                try:
                    result = self.archive.sync(key, account, password,
                                  lambda folder, done, total: self.events.put(("progress", (folder, done, total))),
                                  self.cancelled)
                    saved, missing, failed = saved + result[0], missing + result[1], failed + len(result[2])
                except Exception as error:
                    errors.append((account.user, str(error) or type(error).__name__))
            self.events.put(("done", (saved, missing, failed, errors)))

        threading.Thread(target=work, daemon=True).start()

    def stop_sync(self):
        self.cancelled.set()
        self.abort_button.configure(state="disabled")
        self.status.set("Abruf wird beendet …")

    def poll(self):
        while not self.events.empty():
            event, value = self.events.get_nowait()
            if event == "tray":
                if value == "show":
                    self.show_window()
                elif value == "quit" and self.quit():
                    return
                continue
            if self.handle_database_event(event, value):
                continue
            if event == "spam_ratings":
                for mail_id, (score, flag, dmarc_fail, _) in value.items():
                    if self.table.exists(str(mail_id)):
                        self.table.set(str(mail_id), "spam", self.spam_label(score, flag, dmarc_fail))
                continue
            if event == "spam_progress":
                if not self.busy and value is not None:
                    known, total = value
                    self.status.set(f"Spam-Bewertungen werden übernommen: {self.number(known)} von "
                                    f"{self.number(total)} Mails")
                continue
            if event == "attachment_flags":
                for mail_id, flag in value.items():
                    if self.table.exists(str(mail_id)):
                        self.table.set(str(mail_id), "attach", self.PAPERCLIP if flag else "")
                continue
            if event == "update_check":
                self.update_checked(*value)
                continue
            if event == "update_installed":
                self.update_installed(*value)
                continue
            if event == "list_indexes":
                self.list_preparing = not value
                if not value:
                    self.status.set("Archiv wird einmalig für schnelle Listen vorbereitet (bis etwa eine Minute) …")
                else:
                    if self.status.get().startswith("Archiv wird einmalig"):
                        self.status.set("Archiv vorbereitet")
                    self.refresh()
                continue
            if event == "index_progress":
                indexed, total = value
                if not self.busy and indexed < total:
                    self.status.set(f"Suchindex wird aufgebaut: {self.number(indexed)} von {self.number(total)} "
                                    "Mails (Suche bis dahin langsamer)")
                elif not self.busy and self.status.get().startswith("Suchindex wird aufgebaut"):
                    self.status.set("Suchindex bereit")
                continue
            if event == "attachment_progress":
                # Only use the status line while nothing more important (a sync) is shown there.
                if not self.busy and value is not None:
                    known, total = value
                    self.status.set(f"Anhänge werden erkannt: {self.number(known)} von {self.number(total)} Mails")
                elif not self.busy and self.status.get().startswith("Anhänge werden erkannt"):
                    self.status.set("Anhänge aller Mails erkannt")
                continue
            if event == "folders":
                key, error = value
                self.loading_folders = False
                self.update_account_buttons()
                if error:
                    self.status.set("Ordnerliste konnte nicht geladen werden")
                    messagebox.showerror("Ordnerliste", error, parent=self.root)
                else:
                    self.status.set("Ordnerliste geladen")
                    if key == self.account_id:
                        self.show_account_folders()
                    self.reload_filters()
                continue
            if event == "account":
                self.sync_user = value
                if not self.cancelled.is_set():
                    self.status.set(f"{value} · Verbindung zum IMAP-Server wird aufgebaut …")
                continue
            if event == "progress":
                if not self.cancelled.is_set():
                    folder, done, total = value
                    self.status.set(f"{self.sync_user} · {self.folder_label(folder)} · {done} von {total} geprüft")
                    if done % 500 == 0:
                        # New folder (server numbers just stored) or progress: refresh the counts.
                        self.show_account_folders()
                continue
            self.finish_sync(*value)
        if self.settings["interval"] and not self.busy and not self.maintenance_running and \
                not self.list_preparing and \
                time.time() >= self.next_auto:
            self.start_auto_sync()
        self.update_next_label()
        self.root.after(100, self.poll)

    def folder_label(self, folder):
        return "Posteingang" if folder == "INBOX" else folder

    def finish_sync(self, saved, missing, failed, errors):
        if self.sync_auto:
            self.next_auto = time.time() + self.settings["interval"] * 60
        self.set_sync_state(False)
        self.reload_filters()
        self.show_account_folders()  # The sync also refreshed the folder list.
        self.refresh()
        who ="Automatischer Abruf" if self.sync_auto else self.sync_user
        state = "Abruf gestoppt" if self.cancelled.is_set() else "Abruf abgeschlossen"
        text = f"{who} · {state} · {saved} neu gesichert"
        text += f" · {missing} nicht verfügbar" if missing else ""
        text += f" · {failed} Ordner nicht lesbar" if failed else ""
        text += f" · Fehler bei {len(errors)} Konto{'' if len(errors) == 1 else 'en'}" if errors else ""
        if errors and not self.sync_auto:
            self.status.set("IMAP-Abruf fehlgeschlagen")
            messagebox.showerror("IMAP-Abruf", errors[0][1], parent=self.root)
            return
        self.status.set(text)
        hidden = self.root.state() == "withdrawn"
        if errors:
            details = "\n".join(f"{user}: {message}" for user, message in errors)
            if hidden:
                self.tray.balloon("Automatischer Abruf fehlgeschlagen", details)
            else:
                self.status.set(f"{text} – {details.splitlines()[0]}")
        elif hidden and (saved or not self.sync_auto):
            self.tray.balloon(APP_NAME, text)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def close(self):
        if not self.settings["tray"]:
            self.quit()
            return
        self.root.withdraw()
        if not self.hint_shown:
            self.hint_shown = True
            self.tray.balloon(APP_NAME, "Läuft im Infobereich weiter. Beenden per Rechtsklick auf das Symbol.")

    def quit(self):
        if self.maintenance_running:
            self.show_window()
            if not messagebox.askyesno(APP_NAME, "Laufende Wartung abbrechen und beenden? Das Archiv bleibt dabei "
                                                 "unverändert.", parent=self.root):
                return False
            self.maintenance_cancel.set()
        if self.busy:
            self.show_window()
            if not messagebox.askyesno(APP_NAME, "Laufenden Abruf abbrechen und beenden?", parent=self.root):
                return False
        self.cancelled.set()
        self.tray.stop()
        self.root.destroy()
        return True


def print_version():
    """Answer the updater's smoke test; a windowed EXE may have no sys.stdout, so write to the handle directly."""
    text = f"{APP_NAME} {VERSION_TEXT}\n"
    if sys.stdout is not None:
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    import ctypes
    handle = ctypes.windll.kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    data = text.encode("utf-8")
    written = ctypes.c_ulong()
    ctypes.windll.kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None)


if __name__ == "__main__":
    if "--version" in sys.argv[1:]:
        print_version()
        sys.exit(0)
    if signal_running_instance():
        sys.exit(0)
    root = tk.Tk()
    try:
        MailArchive(root, DATA, start_minimized=MINIMIZED_ARGUMENT in sys.argv[1:])
    except (OSError, ValueError, sqlite3.Error) as error:
        messagebox.showerror(f"{APP_NAME} konnte nicht starten", str(error), parent=root)
        root.destroy()
    else:
        root.mainloop()
