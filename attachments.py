import base64
import os
import re
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from archive import attachments_from
from appearance import style_native_widgets


IMAGE_FORMATS = {"image/png", "image/gif"}
OPEN_EXTENSIONS = set("""
    .pdf .txt .csv .log .jpg .jpeg .png .gif .bmp .webp .tif .tiff .heic
    .docx .xlsx .pptx .odt .ods .odp .mp3 .wav .mp4 .mov .eml
""".split())


def safe_filename(name):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .") or "Anhang"
    if re.fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]", name.split(".")[0], re.I):
        name = "_" + name
    suffix = Path(name).suffix
    return name if len(name) <= 180 else name[:150] + suffix[:20]


class AttachmentPane(ttk.Frame):
    def __init__(self, parent, theme):
        super().__init__(parent, padding=8)
        self.theme = theme
        self.items = []
        buttons = ttk.Frame(self)
        buttons.pack(side="bottom", fill="x", pady=(7, 0))
        self.preview_button = ttk.Button(buttons, text="Vorschau", command=self.preview)
        self.open_button = ttk.Button(buttons, text="Öffnen", command=self.open_file)
        self.save_button = ttk.Button(buttons, text="Speichern …", command=self.save_file)
        for button in (self.preview_button, self.open_button, self.save_button):
            button.pack(side="left", padx=(0, 4))
        self.notice = ttk.Label(self, style="Muted.TLabel")
        self.notice.pack(anchor="w", pady=(0, 5))
        self.table = ttk.Treeview(self, columns=("name", "type"), show="headings", selectmode="browse")
        self.table.heading("name", text="Datei")
        self.table.heading("type", text="Typ")
        self.table.column("name", width=185, minwidth=85)
        self.table.column("type", width=110, minwidth=70)
        scroll = ttk.Scrollbar(self, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.update_buttons)
        self.table.bind("<Double-1>", self.activate)
        self.show_mail(None)

    def show_mail(self, raw):
        try:
            self.items = attachments_from(raw) if raw is not None else []
        except Exception:
            # Malformed mails are archived too; their attachments stay available in the EML export.
            self.items = []
        self.table.delete(*self.table.get_children())
        for index, item in enumerate(self.items):
            self.table.insert("", "end", iid=str(index), values=(item.name, item.content_type))
        self.notice.configure(text="Keine Anhänge" if not self.items else "")
        if self.items:
            self.table.selection_set("0")
        self.update_buttons()
        return len(self.items)

    def selected(self):
        selected = self.table.selection()
        return self.items[int(selected[0])] if selected else None

    def update_buttons(self, _=None):
        item = self.selected()
        preview = item and (item.content_type == "text/plain" or item.content_type in IMAGE_FORMATS)
        openable = item and Path(safe_filename(item.name)).suffix.lower() in OPEN_EXTENSIONS
        self.preview_button.configure(state="normal" if preview else "disabled")
        self.open_button.configure(state="normal" if openable else "disabled")
        self.save_button.configure(state="normal" if item else "disabled")

    def activate(self, event):
        if not self.table.identify_row(event.y):
            return
        if not self.preview_button.instate(["disabled"]):
            self.preview()
        elif not self.open_button.instate(["disabled"]):
            self.open_file()

    def preview(self):
        item = self.selected()
        if item is None:
            return
        data = item.read()
        window = tk.Toplevel(self)
        window.title(item.name)
        window.geometry("760x540")
        window.transient(self.winfo_toplevel())
        if item.content_type == "text/plain":
            charset = item.part.get_content_charset() or ("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
            try:
                content = data[:1_000_000].decode(charset, errors="replace")
            except (LookupError, UnicodeError):
                content = data[:1_000_000].decode("utf-8", errors="replace")
            if len(data) > 1_000_000:
                content += "\n\n[Vorschau gekürzt]"
            text = tk.Text(window, wrap="word", font=("Segoe UI", 10), padx=12, pady=10)
            scroll = ttk.Scrollbar(window, command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            text.pack(fill="both", expand=True)
            text.insert("1.0", content)
            text.configure(state="disabled")
        else:
            try:
                if len(data) > 15_000_000:
                    raise ValueError("Für die Vorschau ist das Bild zu groß. Verwende Öffnen oder Speichern.")
                if item.content_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
                    width = int.from_bytes(data[16:20], "big")
                    height = int.from_bytes(data[20:24], "big")
                elif item.content_type == "image/gif" and data.startswith((b"GIF87a", b"GIF89a")):
                    width = int.from_bytes(data[6:8], "little")
                    height = int.from_bytes(data[8:10], "little")
                else:
                    raise ValueError("Dieses Bild kann nicht als Vorschau angezeigt werden.")
                if not width or not height or width * height > 20_000_000:
                    raise ValueError("Für die Vorschau ist das Bild zu groß. Verwende Öffnen oder Speichern.")
                photo = tk.PhotoImage(master=window, data=base64.b64encode(data).decode("ascii"),
                                      format="png" if item.content_type == "image/png" else "gif")
                scale = max(1, (photo.width() + 709) // 710, (photo.height() + 479) // 480)
                window.photo = photo.subsample(scale) if scale > 1 else photo
                ttk.Label(window, image=window.photo).pack(expand=True)
            except (tk.TclError, ValueError) as error:
                window.destroy()
                messagebox.showerror("Anhangsvorschau", str(error), parent=self)
                return
        style_native_widgets(window, self.theme)

    def open_file(self):
        item = self.selected()
        if item is None:
            return
        suffix = Path(safe_filename(item.name)).suffix.lower()
        if suffix not in OPEN_EXTENSIONS:
            return
        try:
            folder = Path(tempfile.mkdtemp(prefix="SuperMailBackup-"))
            destination = folder / ("Anhang" + suffix)
            destination.write_bytes(item.read())
            # Preserve the Internet origin of mail attachments for Windows and Office.
            with open(str(destination) + ":Zone.Identifier", "w", encoding="ascii") as stream:
                stream.write("[ZoneTransfer]\nZoneId=3\n")
            os.startfile(destination)
        except (OSError, ValueError) as error:
            messagebox.showerror("Anhang öffnen", str(error), parent=self)

    def save_file(self):
        item = self.selected()
        if item is None:
            return
        name = safe_filename(item.name)
        destination = filedialog.asksaveasfilename(parent=self, initialfile=name,
                                                   defaultextension=Path(name).suffix)
        if destination:
            try:
                Path(destination).write_bytes(item.read())
            except (OSError, ValueError) as error:
                messagebox.showerror("Anhang speichern", str(error), parent=self)
