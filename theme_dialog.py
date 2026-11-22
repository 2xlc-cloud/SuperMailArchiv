"""Window for choosing one of the designs; the whole program shows each design live while browsing the list."""
import tkinter as tk
from tkinter import ttk

from appearance import PALETTES, THEMES, style_native_widgets, theme_label


def swatch(master, palette, width=64, height=18):
    """Small picture of a design: background, panel, field, accent and text colour side by side."""
    image = tk.PhotoImage(master=master, width=width, height=height)
    parts = ("bg", "button", "field", "accent", "fg")
    stripe = width // len(parts)
    for number, key in enumerate(parts):
        right = width if number == len(parts) - 1 else (number + 1) * stripe
        image.put(palette[key], to=(number * stripe, 0, right, height))
    image.put(palette["border"], to=(0, 0, width, 1))
    image.put(palette["border"], to=(0, height - 1, width, height))
    return image


class ThemeDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.original = app.theme
        self.title("Design wählen")
        self.transient(app.root)
        self.resizable(False, True)
        self.minsize(420, 380)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _: self.cancel())
        self.bind("<Return>", lambda _: self.accept())

        page = ttk.Frame(self, padding=14)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="Design wählen", style="Preview.TLabel").pack(anchor="w")
        hint = ttk.Label(page, style="Muted.TLabel", justify="left", wraplength=380,
                         text="Beim Durchblättern zeigt das ganze Programm das Design sofort an. „Übernehmen“ "
                              "behält es, „Abbrechen“ stellt das bisherige wieder her.")
        hint.pack(anchor="w", fill="x", pady=(4, 10))

        actions = ttk.Frame(page)
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(actions, text="Abbrechen", command=self.cancel).pack(side="right")
        ttk.Button(actions, text="Übernehmen", style="Accent.TButton", command=self.accept).pack(
            side="right", padx=(0, 8))

        lists = ttk.Frame(page)
        lists.pack(fill="both", expand=True)
        self.images = {key: swatch(self, PALETTES[key]) for key in THEMES}
        self.list = ttk.Treeview(lists, show="tree", selectmode="browse", height=14)
        self.list.column("#0", width=330)
        scroll = ttk.Scrollbar(lists, command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.list.pack(side="left", fill="both", expand=True)
        for key in THEMES:
            label = theme_label(key) + ("   ✓ aktuell" if key == self.original else "")
            self.list.insert("", "end", iid=key, text="  " + label, image=self.images[key])
        self.list.selection_set(self.original)
        self.list.see(self.original)
        self.list.focus(self.original)
        self.list.bind("<<TreeviewSelect>>", lambda _: self.preview())
        self.list.bind("<Double-1>", lambda _: self.accept())
        style_native_widgets(self, app.theme)
        self.geometry(f"+{app.root.winfo_rootx() + 80}+{app.root.winfo_rooty() + 60}")
        self.list.focus_set()
        self.grab_set()

    def chosen(self):
        selected = self.list.selection()
        return selected[0] if selected else self.original

    def preview(self):
        """Show the highlighted design in the whole program (not saved yet)."""
        self.app.show_theme(self.chosen())
        style_native_widgets(self, self.chosen())

    def accept(self):
        self.app.set_theme(self.chosen())
        self.destroy()

    def cancel(self):
        self.app.show_theme(self.original)
        self.destroy()
