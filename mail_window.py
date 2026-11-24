"""Separate window with all details of one archived mail (double-click or Enter in the mail list)."""
import os
import re
import webbrowser
import tkinter as tk
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from appearance import PALETTES, style_native_widgets
from archive import display_text
from attachments import AttachmentPane, write_temporary
from html_view import HtmlView

LINK_PATTERN = re.compile(r"(?:https?://|mailto:|www\.)[^\s<>\"'«»]+[^\s<>\"'«».,;:!?)\]}]", re.I)
HEADER_FIELDS = (("From", "Von"), ("To", "An"), ("Cc", "Cc"), ("Reply-To", "Antwort an"), ("Date", "Datum (Kopfzeile)"))


def header_values(raw):
    """Decoded header fields for the detail view; malformed headers are shown as far as possible."""
    values = {}
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    except Exception:
        return values
    for name, _ in HEADER_FIELDS:
        try:
            value = message.get(name)
        except Exception:  # E.g. an address list the parser cannot decode; the tab Kopfzeilen shows it raw.
            value = None
        if value:
            values[name] = " ".join(str(value).split())
    return values


def header_block(raw):
    """The raw header lines of the mail as text."""
    head = raw[:262144]
    end = min((head.find(mark) for mark in (b"\r\n\r\n", b"\n\n") if mark in head), default=len(head))
    return head[:end].decode("utf-8", "replace").replace("\r\n", "\n")


class Original:
    """The raw mail in the form write_temporary() expects of an attachment."""

    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class LinkText(ttk.Frame):
    """Read-only text with a scrollbar; web and mail addresses are clickable."""

    def __init__(self, parent, status, font=("Segoe UI", 10), links=True, wrap="word"):
        super().__init__(parent)
        self.status = status
        self.text = tk.Text(self, wrap=wrap, font=font, padx=11, pady=9, state="disabled")
        scroll = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        if wrap == "none":
            across = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
            self.text.configure(xscrollcommand=across.set)
            across.pack(side="bottom", fill="x")
        self.text.pack(fill="both", expand=True)
        self.links = {}
        self.with_links = links
        self.text.tag_configure("link", underline=True)
        self.text.tag_bind("link", "<Button-1>", self.open_link)
        self.text.bind("<Motion>", self.hover)
        self.text.bind("<Leave>", self.hover)
        self.hovered = None

    def show(self, content):
        text = self.text
        text.configure(state="normal")
        text.delete("1.0", "end")
        self.links = {}
        position = 0
        for match in (LINK_PATTERN.finditer(content) if self.with_links else ()):
            text.insert("end", content[position:match.start()])
            url = match.group()
            tag = f"link-{len(self.links)}"
            self.links[tag] = "http://" + url if url.lower().startswith("www.") else url
            text.insert("end", match.group(), ("link", tag))
            position = match.end()
        text.insert("end", content[position:])
        text.configure(state="disabled")

    def link_at(self, event):
        return next((self.links[tag] for tag in self.text.tag_names(f"@{event.x},{event.y}")
                     if tag in self.links), None)

    def hover(self, event):
        url = self.link_at(event)
        self.text.configure(cursor="hand2" if url else "")
        if url != self.hovered:
            self.hovered = url
            self.status.set(f"Link öffnen: {url}" if url else "")

    def open_link(self, event):
        url = self.link_at(event)
        if url and re.match(r"(?:https?://|mailto:)", url, re.I):
            try:
                webbrowser.open(url)
            except Exception as error:
                messagebox.showerror("Link öffnen", f"Der Link konnte nicht geöffnet werden.\n{error}", parent=self)


class MailWindow(tk.Toplevel):
    """All details of one mail: header fields, text, attachments, raw headers; save or open the original."""

    def __init__(self, app, mail_id):
        super().__init__(app.root)
        self.app = app
        self.mail_id = mail_id
        row = app.archive.get_mail(mail_id)
        if row is None:
            self.destroy()
            raise LookupError("Die Mail ist nicht mehr im Archiv.")
        self.raw = app.archive.get_mail(mail_id, raw=True)["raw"]
        subject = row["subject"] or "(Ohne Betreff)"
        self.title(subject[:120])
        self.geometry(self.initial_geometry())
        self.minsize(560, 420)
        self.bind("<Escape>", lambda _: self.destroy())
        self.status = tk.StringVar()

        page = ttk.Frame(self, padding=14)
        page.pack(fill="both", expand=True)
        title = ttk.Label(page, text=subject, style="Preview.TLabel")
        title.pack(fill="x")
        title.bind("<Configure>", lambda event: title.configure(wraplength=event.width))

        fields = ttk.Frame(page)
        fields.pack(fill="x", pady=(8, 10))
        fields.columnconfigure(1, weight=1)
        headers = header_values(self.raw)
        rows = [(label, headers.get(name) or (row["sender"] if name == "From" else
                                               row["recipient"] if name == "To" else ""))
                for name, label in HEADER_FIELDS if name != "Date"]
        rows += [("Datum", app.date_label(row["sent"], True)),
                 ("Größe", app.size_label(row["size"])),
                 ("Konto", f"{row['user']} · {row['host']}:{row['port']}"),
                 ("Ordner", app.folder_label(row["folder"]))]
        spam = app.spam_explanation(self.raw)
        if spam:
            rows.append(("Spam", spam))
        line = 0
        for label, value in rows:
            if not value:
                continue
            ttk.Label(fields, text=label, style="Muted.TLabel").grid(row=line, column=0, sticky="nw", padx=(0, 12))
            shown = ttk.Label(fields, text=value, justify="left")
            shown.grid(row=line, column=1, sticky="w")
            shown.bind("<Configure>", lambda event, widget=shown: widget.configure(wraplength=max(event.width, 200)))
            line += 1
        ttk.Label(fields, text="Mail-ID", style="Muted.TLabel").grid(row=line, column=0, sticky="w", padx=(0, 12))
        identifier = ttk.Frame(fields)
        identifier.grid(row=line, column=1, sticky="ew", pady=(2, 0))
        self.identifier = tk.StringVar(value=row["mail_id"])
        ttk.Entry(identifier, textvariable=self.identifier, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(identifier, text="Kopieren", command=self.copy_identifier).pack(side="left", padx=(5, 0))

        ttk.Label(page, textvariable=self.status, style="Muted.TLabel").pack(side="bottom", fill="x", pady=(6, 0))
        actions = ttk.Frame(page)
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(actions, text="Schließen", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Original als .eml speichern …", command=self.save_original).pack(side="left")
        ttk.Button(actions, text="Im Mailprogramm öffnen", command=self.open_original).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Erneut zusenden …",
                   command=lambda: self.app.resend_mails([self.mail_id], parent=self)).pack(side="left", padx=(6, 0))

        self.tabs = ttk.Notebook(page)
        self.tabs.pack(fill="both", expand=True)
        self.body = LinkText(self.tabs, self.status)
        text = display_text(self.raw) or row["body"] or "(Kein Textinhalt. Das Original enthält auch etwaige Anhänge.)"
        self.body.show(text)
        self.tabs.add(self.body, text="Nachricht")
        self.html = HtmlView(self.tabs, self.raw, self.status)
        if self.html.available:
            # HTML mails open in their rendered form; the plain text stays one click away.
            self.tabs.insert(0, self.html, text="HTML")
            self.tabs.select(self.html)
            self.tabs.bind("<<NotebookTabChanged>>", self.tab_changed, add="+")
            self.after_idle(self.tab_changed)
        self.attachments = AttachmentPane(self.tabs, app.theme)
        count = self.attachments.show_mail(self.raw)
        self.tabs.add(self.attachments, text=f"Anhänge ({count})" if count else "Anhänge")
        self.headers = LinkText(self.tabs, self.status, font=("Consolas", 9), links=False, wrap="none")
        self.headers.show(header_block(self.raw))
        self.tabs.add(self.headers, text="Kopfzeilen")
        self.update_theme(app.theme)
        self.focus_set()

    def tab_changed(self, _event=None):
        if self.winfo_exists() and self.tabs.select() == str(self.html):
            try:
                self.html.render()
            except Exception as error:  # E.g. Tkhtml missing: the text view stays available.
                self.status.set(f"HTML-Ansicht nicht verfügbar: {error}")

    def initial_geometry(self):
        """Next to the main window, cascaded when several detail windows are open."""
        offset = 30 * (len(self.app.mail_windows) % 8)
        root = self.app.root
        width, height = 900, 720
        x = max(root.winfo_rootx() + 60 + offset, 0)
        y = max(root.winfo_rooty() + 30 + offset, 0)
        width = min(width, self.winfo_screenwidth() - 40)
        height = min(height, self.winfo_screenheight() - 80)
        x = min(x, max(self.winfo_screenwidth() - width - 20, 0))
        y = min(y, max(self.winfo_screenheight() - height - 60, 0))
        return f"{width}x{height}+{x}+{y}"

    def update_theme(self, theme):
        style_native_widgets(self, theme)
        self.body.text.tag_configure("link", foreground=PALETTES[theme]["accent"])
        self.attachments.theme = theme

    def copy_identifier(self):
        self.clipboard_clear()
        self.clipboard_append(self.identifier.get())
        self.status.set("Mail-ID kopiert")

    def save_original(self):
        destination = filedialog.asksaveasfilename(parent=self, defaultextension=".eml",
                                                   initialfile=f"Mail-{self.mail_id.replace(':', '-')}.eml",
                                                   filetypes=[("E-Mail", "*.eml")])
        if destination:
            try:
                Path(destination).write_bytes(self.raw)
                self.status.set(f"Gespeichert: {destination}")
            except OSError as error:
                messagebox.showerror("Speichern fehlgeschlagen", str(error), parent=self)

    def open_original(self):
        """Open a copy of the original in the program registered for .eml (e.g. Outlook or Thunderbird)."""
        try:
            os.startfile(write_temporary(Original(self.raw), ".eml"))
            self.status.set(f"Im Mailprogramm geöffnet ({datetime.now():%H:%M} Uhr)")
        except OSError as error:
            messagebox.showerror("Im Mailprogramm öffnen", f"Kein Programm für .eml-Dateien gefunden oder "
                                                           f"Öffnen fehlgeschlagen.\n\n{error}", parent=self)
