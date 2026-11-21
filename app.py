import os
import queue
import re
import sqlite3
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from archive import (Account, account_password, archived_accounts, clean_password, find_mails,
                     get_mail, initialize, remember_password, remove_account, save_account,
                     saved_accounts, sync)
from attachments import AttachmentPane


DATA = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "MailArchiv"
APP_NAME = "SuperMailArchiv"
BG, PANEL, FIELD = "#111622", "#171f2e", "#202a3b"
FG, MUTED, ACCENT = "#ecf1fa", "#a9b8ce", "#315e9c"
PAGE = 100


class MailArchive:
    def __init__(self, root, data):
        self.root = root
        self.path = data / "archiv.sqlite3"
        initialize(self.path)
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.busy = False
        self.offset = 0
        self.pending_search = None
        self.account_id = None
        self.accounts = {}
        self.filters = []
        self.preview_id = None
        root.title(APP_NAME)
        root.geometry("1120x710")
        root.minsize(810, 500)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.style()
        self.build()
        accounts = saved_accounts(self.path)
        self.reload_accounts(next(iter(accounts)) if len(accounts) == 1 else None)
        self.refresh()
        root.after(100, self.poll)

    def style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10),
                        bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, troughcolor=BG)
        style.configure("TEntry", fieldbackground=FIELD, insertcolor=FG, padding=6)
        style.map("TEntry", fieldbackground=[("readonly", FIELD)])
        style.configure("TButton", background=FIELD, padding=(11, 7))
        style.configure("Accent.TButton", background=ACCENT)
        style.map("Accent.TButton", background=[("active", "#3c73ba")])
        style.map("TButton", background=[("active", "#2d3d54")],
                  foreground=[("disabled", MUTED)])
        style.configure("TLabelframe.Label", foreground=MUTED)
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, rowheight=29)
        style.configure("Treeview.Heading", background=FIELD, font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", FG)])
        style.map("Treeview.Heading", background=[("active", FIELD)])
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TNotebook.Tab", background=FIELD, padding=(13, 7))
        style.map("TNotebook.Tab", background=[("selected", ACCENT), ("active", "#2d3d54")])
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)

    def build(self):
        shell = ttk.Frame(self.root, padding=(19, 13))
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text=APP_NAME, font=("Segoe UI", 23, "bold")).pack(anchor="w", pady=(0, 9))
        self.status = tk.StringVar(value="Bereit")
        ttk.Label(shell, textvariable=self.status, style="Muted.TLabel").pack(side="bottom", anchor="w", pady=(8, 0))
        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        outer = self.archive_tab = ttk.Frame(self.tabs, padding=12)
        self.accounts_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(outer, text="Archiv")
        self.tabs.add(self.accounts_tab, text="Konten")

        search_bar = ttk.Frame(outer)
        search_bar.pack(fill="x", pady=(0, 10))
        ttk.Label(search_bar, text="Konto").pack(side="left", padx=(0, 6))
        self.account_filter = ttk.Combobox(search_bar, state="readonly", width=30)
        self.account_filter.pack(side="left", padx=(0, 14))
        self.account_filter.bind("<<ComboboxSelected>>", lambda _: self.set_filter())
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

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)
        results = ttk.Frame(panes, padding=(0, 0, 6, 0))
        preview = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(results, weight=1)
        panes.add(preview, weight=1)

        nav = ttk.Frame(results)
        nav.pack(side="bottom", fill="x", pady=(7, 0))
        self.prev = ttk.Button(nav, text="Zurück", command=lambda: self.change_page(-PAGE))
        self.prev.pack(side="left")
        self.next = ttk.Button(nav, text="Weiter", command=lambda: self.change_page(PAGE))
        self.next.pack(side="right")
        self.page_label = ttk.Label(nav, style="Muted.TLabel")
        self.page_label.pack(pady=7)
        self.table = ttk.Treeview(results, columns=("subject", "sender", "date"),
                                  show="headings", selectmode="browse")
        for key, label, width in (("subject", "Betreff", 215), ("sender", "Von", 145), ("date", "Datum", 95)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=65, stretch=key != "date")
        scroll = ttk.Scrollbar(results, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.show_selection)

        self.subject = ttk.Label(preview, text="Mailvorschau", font=("Segoe UI", 16, "bold"))
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
        self.export = ttk.Button(preview, text="Original als .eml speichern",
                                 command=self.export_mail, state="disabled")
        self.export.pack(side="bottom", anchor="e", pady=(8, 0))
        self.preview_tabs = ttk.Notebook(preview)
        self.preview_tabs.pack(fill="both", expand=True)
        message = ttk.Frame(self.preview_tabs)
        self.attachments = AttachmentPane(self.preview_tabs, PANEL, FG)
        self.preview_tabs.add(message, text="Nachricht")
        self.preview_tabs.add(self.attachments, text="Anhänge")
        self.body = tk.Text(message, wrap="word", bg=PANEL, fg=FG, selectbackground=ACCENT,
                            font=("Segoe UI", 11), padx=13, pady=11, relief="flat",
                            highlightthickness=0, width=36, state="disabled")
        scroll = ttk.Scrollbar(message, command=self.body.yview)
        self.body.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.body.pack(fill="both", expand=True)

        self.build_accounts()

    def build_accounts(self):
        page = self.accounts_tab
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        buttons = ttk.Frame(page)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(buttons, text="Neues Konto", command=lambda: self.edit_account()).pack(side="left")
        self.remove_button = ttk.Button(buttons, text="Konto entfernen", command=self.delete_account)
        self.remove_button.pack(side="right")
        listing = ttk.Frame(page)
        listing.grid(row=1, column=0, sticky="nsew")
        self.account_table = ttk.Treeview(listing, columns=("user", "server", "folder"),
                                         show="headings", selectmode="browse", height=5)
        for key, title, width in (("user", "Benutzername", 250), ("server", "IMAP-Server", 235),
                                  ("folder", "Ordner", 110)):
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
            ("user", "Benutzername", 25), ("folder", "Ordner", 15),
        )):
            var = self.values[key] = tk.StringVar()
            ttk.Label(form, text=label).grid(row=0, column=index, sticky="w", padx=3)
            entry = ttk.Entry(form, textvariable=var, width=width)
            entry.grid(row=1, column=index, sticky="ew", padx=3, pady=(3, 0))
            form.columnconfigure(index, weight=0 if key == "port" else 1)
            if key == "host":
                self.host_entry = entry
        ttk.Button(form, text="Konto speichern", command=self.store_account).grid(row=1, column=4, padx=(8, 0))

        fetch = ttk.LabelFrame(page, text=" Abruf für das ausgewählte Konto ", padding=10)
        fetch.grid(row=3, column=0, sticky="ew", pady=(9, 0))
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
        index = self.account_filter.current()
        return self.filters[index - 1] if index > 0 else None

    def set_filter(self):
        self.offset = 0
        self.refresh()

    def reload_filters(self):
        current = self.current_filter()
        self.filters = archived_accounts(self.path)
        self.account_filter.configure(values=["Alle Konten", *(item.label for item in self.filters)])
        self.account_filter.current(self.filters.index(current) + 1 if current in self.filters else 0)

    def reload_accounts(self, select=None):
        self.reload_filters()
        self.accounts = saved_accounts(self.path)
        self.account_table.delete(*self.account_table.get_children())
        for key, account in self.accounts.items():
            self.account_table.insert("", "end", iid=str(key),
                                      values=(account.user, f"{account.host}:{account.port}", account.folder))
        if select in self.accounts:
            self.account_table.selection_set(str(select))
        self.edit_account(select if select in self.accounts else None, focus=False)

    def pick_account(self, _=None):
        selected = self.account_table.selection()
        if selected and int(selected[0]) != self.account_id:
            self.edit_account(int(selected[0]), focus=False)

    def edit_account(self, key=None, focus=True):
        self.account_id = key
        if key is None:
            self.account_table.selection_remove(*self.account_table.selection())
        account = self.accounts.get(key, Account("", 993, "", "INBOX"))
        for name, value in vars(account).items():
            self.values[name].set(value)
        self.password.set("")
        self.has_password = key is not None and account_password(self.path, key) is not None
        self.update_account_buttons()
        if focus:
            self.host_entry.focus_set()

    def update_account_buttons(self):
        saved = self.account_id is not None
        self.remove_button.configure(state="normal" if saved else "disabled")
        self.sync_button.configure(state="normal" if saved and not self.busy else "disabled")
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
            remember_password(self.path, self.account_id, account, password)
        except (OSError, ValueError, sqlite3.Error) as error:
            messagebox.showerror("Passwort speichern", str(error), parent=self.root)
            return
        self.password.set("")
        self.has_password = password is not None
        self.update_account_buttons()

    def account_from_form(self):
        host, port, user, folder = (self.values[k].get().strip() for k in ("host", "port", "user", "folder"))
        if not re.fullmatch(r"[^\s/\\]+", host) or not user or not folder or not port.isascii() or not port.isdecimal():
            raise ValueError("Bitte Server, TLS-Port, Benutzername und Ordner eingeben.")
        if any(char in host + user + folder for char in "\r\n\0"):
            raise ValueError("Server, Benutzername und Ordner müssen jeweils in einer Zeile stehen.")
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("Der TLS-Port muss zwischen 1 und 65535 liegen.")
        return Account(host.lower(), port, user, "INBOX" if folder.upper() == "INBOX" else folder)

    def store_account(self):
        try:
            account = self.account_from_form()
            key = save_account(self.path, account, self.account_id)
        except (OSError, ValueError, sqlite3.Error) as error:
            text = "Dieses Konto mit diesem Ordner ist bereits eingerichtet." if isinstance(error, sqlite3.IntegrityError) else str(error)
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
            remove_account(self.path, self.account_id)
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

    def refresh(self):
        selected = self.table.selection()
        rows = find_mails(self.path, self.query.get(), self.offset, PAGE + 1, self.current_filter())
        self.table.delete(*self.table.get_children())
        for row in rows[:PAGE]:
            self.table.insert("", "end", iid=str(row["id"]),
                              values=(row["subject"] or "(Ohne Betreff)", row["sender"], self.date_label(row["sent"])))
        count = min(len(rows), PAGE)
        self.page_label.configure(text=f"{self.offset + 1}–{self.offset + count}" if count else "Keine Treffer")
        self.prev.configure(state="normal" if self.offset else "disabled")
        self.next.configure(state="normal" if len(rows) > PAGE else "disabled")
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        self.show_selection()

    def show_selection(self, _=None):
        selected = self.table.selection()
        mail_id = int(selected[0]) if selected else None
        row = get_mail(self.path, mail_id) if mail_id is not None else None
        self.subject.configure(text=(row["subject"] or "(Ohne Betreff)") if row else "Mailvorschau")
        self.meta.configure(text=(
            f"Von: {row['sender'] or '—'}\nAn: {row['recipient'] or '—'}\n"
            f"{self.date_label(row['sent'], True)}\n"
            f"Konto: {row['user']} · {row['host']}:{row['port']} / {row['folder']}"
        ) if row else "Wähle eine Nachricht aus der Liste.")
        self.mail_id.set(row["mail_id"] if row else "")
        self.copy_button.configure(state="normal" if row else "disabled")
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        if row:
            self.body.insert("1.0", row["body"] or "(Kein Textinhalt. Das Original enthält auch etwaige Anhänge.)")
        self.body.configure(state="disabled")
        self.export.configure(state="normal" if row else "disabled")
        if mail_id != self.preview_id:
            self.preview_id = mail_id
            raw = get_mail(self.path, mail_id, raw=True)["raw"] if row else None
            count = self.attachments.show_mail(raw)
            self.preview_tabs.tab(self.attachments, text=f"Anhänge ({count})" if count else "Anhänge")
            self.preview_tabs.select(0)

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
                                                   initialfile=f"Mail-{selected[0]}.eml",
                                                   filetypes=[("E-Mail", "*.eml")])
        if destination:
            try:
                Path(destination).write_bytes(get_mail(self.path, int(selected[0]), raw=True)["raw"])
            except OSError as error:
                messagebox.showerror("Export fehlgeschlagen", str(error), parent=self.root)

    def start_sync(self):
        if self.busy or self.account_id is None:
            return
        account = self.accounts[self.account_id]
        try:
            if self.account_from_form() != account:
                raise ValueError("Speichere zuerst die geänderten Kontodaten.")
            password = clean_password(self.password.get() or account_password(self.path, self.account_id) or "")
        except (ValueError, OSError, sqlite3.Error) as error:
            messagebox.showwarning("IMAP-Konto", str(error), parent=self.root)
            return

        self.password.set("")
        self.busy = True
        self.sync_user = account.user
        self.cancelled.clear()
        self.update_account_buttons()
        self.abort_button.configure(state="normal")
        self.status.set(f"{account.user} · Verbindung zum IMAP-Server wird aufgebaut …")

        def work():
            try:
                result = sync(self.path, account, password,
                              lambda done, total: self.events.put(("progress", (done, total))), self.cancelled)
                self.events.put(("done", result))
            except Exception as error:
                self.events.put(("error", str(error) or type(error).__name__))

        threading.Thread(target=work, daemon=True).start()

    def stop_sync(self):
        self.cancelled.set()
        self.abort_button.configure(state="disabled")
        self.status.set("Abruf wird beendet …")

    def poll(self):
        while not self.events.empty():
            event, value = self.events.get_nowait()
            if event == "progress":
                if not self.cancelled.is_set():
                    self.status.set(f"{self.sync_user} · {value[0]} von {value[1]} geprüft")
                continue
            self.busy = False
            self.update_account_buttons()
            self.abort_button.configure(state="disabled")
            self.reload_filters()
            self.refresh()
            if event == "error":
                self.status.set("IMAP-Abruf fehlgeschlagen")
                messagebox.showerror("IMAP-Abruf", value, parent=self.root)
            else:
                state = "Abruf gestoppt" if self.cancelled.is_set() else "Abruf abgeschlossen"
                missing = f" · {value[1]} nicht verfügbar" if value[1] else ""
                self.status.set(f"{self.sync_user} · {state} · {value[0]} neu gesichert{missing}")
        self.root.after(100, self.poll)

    def close(self):
        if not self.busy or messagebox.askyesno(APP_NAME, "Laufenden Abruf abbrechen und schließen?", parent=self.root):
            self.cancelled.set()
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        MailArchive(root, DATA)
    except (OSError, ValueError, sqlite3.Error) as error:
        messagebox.showerror(f"{APP_NAME} konnte nicht starten", str(error), parent=root)
        root.destroy()
    else:
        root.mainloop()
