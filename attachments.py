import base64
import mimetypes
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from archive import attachments_from
from appearance import PALETTES, style_native_widgets
from thumbnails import pdf_pages, preview_image, shell_image


IMAGE_FORMATS = {"image/png", "image/gif"}
OPEN_EXTENSIONS = set("""
    .pdf .txt .csv .log .jpg .jpeg .png .gif .bmp .webp .tif .tiff .heic
    .docx .xlsx .pptx .odt .ods .odp .mp3 .wav .mp4 .mov .eml
""".split())
# Attachments that get a small picture preview (images, and the first page of PDFs).
VISUAL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".pdf"}
THUMBNAIL_SIZE = (260, 180)
MAX_PREVIEW_BYTES = 50_000_000
LARGE_WIDTH = 940  # Width of pictures and PDF pages in the scrollable preview window.
MAX_PDF_PAGES = 30


def safe_filename(name):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .") or "Anhang"
    if re.fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]", name.split(".")[0], re.I):
        name = "_" + name
    suffix = Path(name).suffix
    return name if len(name) <= 180 else name[:150] + suffix[:20]


def remove_old_temporary_files(max_age_days=1):
    """Delete attachment copies from earlier sessions; opened files may still be in use, so keep recent ones."""
    limit = time.time() - max_age_days * 86400
    for folder in [*Path(tempfile.gettempdir()).glob("SuperNMailArchiv-*"),
                   *Path(tempfile.gettempdir()).glob("SuperMailBackup-*")]:  # Name up to 1.22.0
        try:
            if folder.is_dir() and folder.stat().st_mtime < limit:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def attachment_suffix(item):
    """File extension from the attachment name, else from its content type."""
    suffix = Path(safe_filename(item.name)).suffix.lower()
    return suffix or (mimetypes.guess_extension(item.content_type) or "").lower()


def is_visual(item):
    return attachment_suffix(item) in VISUAL_EXTENSIONS or item.content_type == "application/pdf"


def write_temporary(item, suffix):
    """Write an attachment to a new temporary folder, marked as coming from the Internet; returns the file."""
    folder = Path(tempfile.mkdtemp(prefix="SuperNMailArchiv-"))
    destination = folder / ("Anhang" + suffix)
    destination.write_bytes(item.read())
    # Preserve the Internet origin of mail attachments for Windows and Office.
    with open(str(destination) + ":Zone.Identifier", "w", encoding="ascii") as stream:
        stream.write("[ZoneTransfer]\nZoneId=3\n")
    return destination


def render_pages(item, width, max_pages=MAX_PDF_PAGES):
    """Large pictures for the scrollable preview: ([PNG bytes], page count). PDFs give several pages."""
    pdf = item.content_type == "application/pdf" or attachment_suffix(item) == ".pdf"
    path = write_temporary(item, ".pdf" if pdf else attachment_suffix(item))
    try:
        if pdf:
            return pdf_pages(path, width, 0, max_pages)
        image = shell_image(path, width, 20 * width)  # Fit the width; tall pictures are scrolled.
        return ([image], 1) if image else ([], 0)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


class ScrollPreview(tk.Toplevel):
    """Window with pictures below each other; scrolls with scroll bars, mouse wheel and keys."""

    def __init__(self, parent, title, theme):
        super().__init__(parent)
        self.title(title)
        self.geometry("1000x760")
        self.transient(parent.winfo_toplevel())
        self.images = []
        self.info = ttk.Label(self, style="Muted.TLabel", text="Vorschau wird erstellt …")
        self.info.pack(anchor="w", padx=10, pady=(8, 4))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, highlightthickness=0, background=PALETTES[theme]["field"])
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.canvas.pack(fill="both", expand=True)
        self.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-event.delta // 120, "units"))
        self.bind("<Shift-MouseWheel>", lambda event: self.canvas.xview_scroll(-event.delta // 120, "units"))
        for key, view, amount, what in (("<Up>", "y", -1, "units"), ("<Down>", "y", 1, "units"),
                                        ("<Prior>", "y", -1, "pages"), ("<Next>", "y", 1, "pages"),
                                        ("<Left>", "x", -1, "units"), ("<Right>", "x", 1, "units")):
            scroll = self.canvas.yview_scroll if view == "y" else self.canvas.xview_scroll
            self.bind(key, lambda _, scroll=scroll, amount=amount, what=what: scroll(amount, what))
        self.bind("<Home>", lambda _: self.canvas.yview_moveto(0))
        self.bind("<End>", lambda _: self.canvas.yview_moveto(1))
        self.bind("<Escape>", lambda _: self.destroy())
        self.canvas.bind("<Configure>", lambda _: self.arrange())
        style_native_widgets(self, theme)
        self.focus_set()

    def add(self, photo):
        self.images.append((photo, self.canvas.create_image(0, 0, image=photo, anchor="n")))
        self.arrange()

    def arrange(self):
        """Centre the pictures horizontally, one below the other with a small gap."""
        widest = max((photo.width() for photo, _ in self.images), default=0)
        middle = max(self.canvas.winfo_width(), widest + 24) // 2
        top = 12
        for photo, item in self.images:
            self.canvas.coords(item, middle, top)
            top += photo.height() + 12
        self.canvas.configure(scrollregion=(0, 0, max(self.canvas.winfo_width(), widest + 24), top))


def render_preview(item, width, height):
    """PNG preview of an image or PDF attachment, or None; temporary files are removed again."""
    suffix = ".pdf" if item.content_type == "application/pdf" else attachment_suffix(item)
    path = write_temporary(item, suffix)
    try:
        return preview_image(path, width, height)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


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
        self.thumbnail_frame = ttk.Frame(self)
        self.thumbnail = ttk.Label(self.thumbnail_frame, anchor="center", cursor="hand2")
        self.thumbnail.pack(fill="both", expand=True)
        self.thumbnail.bind("<Button-1>", lambda _: self.preview())
        self.thumbnail_token = 0
        self.thumbnails_pending = 0
        self.thumbnail_results = queue.Queue()
        self.notice = ttk.Label(self, style="Muted.TLabel")
        self.notice.pack(anchor="w", pady=(0, 5))
        # At least four rows stay visible when a preview takes the space below the list.
        self.table = ttk.Treeview(self, columns=("name", "type"), show="headings", selectmode="browse", height=4)
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
        preview = item and (item.content_type == "text/plain" or item.content_type in IMAGE_FORMATS
                            or is_visual(item))
        openable = item and Path(safe_filename(item.name)).suffix.lower() in OPEN_EXTENSIONS
        self.preview_button.configure(state="normal" if preview else "disabled")
        self.open_button.configure(state="normal" if openable else "disabled")
        self.save_button.configure(state="normal" if item else "disabled")
        self.request_thumbnail(item)

    def request_thumbnail(self, item):
        """Show a small preview of the selected image or PDF; it is rendered in the background."""
        self.thumbnail_token += 1
        token = self.thumbnail_token
        if item is None or not is_visual(item):
            self.thumbnail_frame.pack_forget()
            self.table.pack_configure(expand=True)
            return
        # The list keeps its four rows; the preview fills the rest of the tab.
        self.table.pack_configure(expand=False)
        self.thumbnail_frame.pack(fill="both", expand=True, pady=(7, 0))
        self.thumbnail.configure(image="", text="Vorschau wird erstellt …")
        self.thumbnail.image = None

        def work():
            try:
                image = render_preview(item, *THUMBNAIL_SIZE) if len(item.read()) <= MAX_PREVIEW_BYTES else None
            except Exception:
                image = None
            self.thumbnail_results.put((token, image))

        threading.Thread(target=work, daemon=True).start()
        self.thumbnails_pending += 1
        if self.thumbnails_pending == 1:
            self.after(80, self.poll_thumbnail)

    def poll_thumbnail(self):
        """Single polling loop; it runs while preview threads are still working."""
        while True:
            try:
                token, image = self.thumbnail_results.get_nowait()
            except queue.Empty:
                break
            self.thumbnails_pending -= 1
            if token == self.thumbnail_token:  # Results of older selections are dropped.
                self.show_thumbnail(image)
        if self.thumbnails_pending:
            self.after(80, self.poll_thumbnail)

    def show_thumbnail(self, image):
        if image is None:
            self.thumbnail.configure(image="", text="Keine Vorschau verfügbar")
            return
        try:
            photo = tk.PhotoImage(master=self, data=base64.b64encode(image).decode("ascii"), format="png")
        except tk.TclError:
            self.thumbnail.configure(image="", text="Keine Vorschau verfügbar")
            return
        self.thumbnail.image = photo
        self.thumbnail.configure(image=photo, text="")

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
                    window.destroy()
                    self.show_rendered(item)
                    return
                if not width or not height or width * height > 20_000_000:
                    raise ValueError("Für die Vorschau ist das Bild zu groß. Verwende Öffnen oder Speichern.")
                photo = tk.PhotoImage(master=window, data=base64.b64encode(data).decode("ascii"),
                                      format="png" if item.content_type == "image/png" else "gif")
                window.destroy()
                scrolled = ScrollPreview(self, item.name, self.theme)
                scale = (photo.width() + 1599) // 1600  # Only very wide pictures are reduced.
                scrolled.add(photo.subsample(scale) if scale > 1 else photo)
                scrolled.info.configure(text=f"{photo.width()} × {photo.height()} Pixel")
                return
            except (tk.TclError, ValueError) as error:
                window.destroy()
                messagebox.showerror("Anhangsvorschau", str(error), parent=self)
                return
        style_native_widgets(window, self.theme)

    def show_rendered(self, item):
        """Scrollable large preview of JPEG & co. and of the pages of a PDF, rendered in the background."""
        if len(item.read()) > MAX_PREVIEW_BYTES:
            messagebox.showerror("Anhangsvorschau", "Für die Vorschau ist der Anhang zu groß. "
                                 "Verwende Öffnen oder Speichern.", parent=self)
            return
        window = ScrollPreview(self, item.name, self.theme)
        results = queue.Queue()
        threading.Thread(target=lambda: results.put(self.safe_render(item)), daemon=True).start()

        def check():
            if not window.winfo_exists():
                return
            try:
                images, total = results.get_nowait()
            except queue.Empty:
                window.after(100, check)
                return
            if not images:
                window.info.configure(text="Für diesen Anhang gibt es keine Vorschau. Verwende Öffnen oder Speichern.")
                return
            for image in images:
                window.add(tk.PhotoImage(master=window, data=base64.b64encode(image).decode("ascii"), format="png"))
            if total > len(images):
                window.info.configure(text=f"Seite 1–{len(images)} von {total}. Weitere Seiten über Öffnen.")
            elif total > 1:
                window.info.configure(text=f"{total} Seiten")
            else:
                window.info.configure(text="")

        window.after(100, check)

    @staticmethod
    def safe_render(item):
        try:
            return render_pages(item, LARGE_WIDTH)
        except Exception:
            return [], 0

    def open_file(self):
        item = self.selected()
        if item is None:
            return
        suffix = Path(safe_filename(item.name)).suffix.lower()
        if suffix not in OPEN_EXTENSIONS:
            return
        try:
            os.startfile(write_temporary(item, suffix))
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
