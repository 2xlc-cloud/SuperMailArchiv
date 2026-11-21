"""Appearance shared with the SuperPyBackup desktop app."""
import ctypes
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk


PALETTES = {
    "dark": {
        "bg": "#1b1e24", "panel": "#232830", "field": "#14171c", "fg": "#e6eaf2",
        "muted": "#9aa4b5", "accent": "#4c8dff", "border": "#3a4250", "select": "#2f5fb3",
        "button": "#2b313b", "hover": "#38404d", "disabled": "#6b7585",
    },
    "light": {
        "bg": "#f0f0f0", "panel": "#f0f0f0", "field": "#ffffff", "fg": "#1a1a1a",
        "muted": "#555555", "accent": "#0067c0", "border": "#adadad", "select": "#0063b1",
        "button": "#e1e1e1", "hover": "#e5f1fb", "disabled": "#8a8a8a",
    },
}


def apply_theme(root, name):
    palette = PALETTES[name]
    bg, panel, field, fg = (palette[key] for key in ("bg", "panel", "field", "fg"))
    accent, border, selection = (palette[key] for key in ("accent", "border", "select"))
    button, hover, disabled = (palette[key] for key in ("button", "hover", "disabled"))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=bg, foreground=fg, fieldbackground=field, bordercolor=border,
                    lightcolor=border, darkcolor=border, troughcolor=panel, focuscolor=accent,
                    insertcolor=fg, selectbackground=selection, selectforeground="white", font=("Segoe UI", 9))
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground=fg)
    style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
    style.configure("Preview.TLabel", font=("Segoe UI", 13, "bold"))
    style.configure("Muted.TLabel", foreground=palette["muted"])
    style.configure("Developer.TLabel", foreground="#f59b1a" if name == "dark" else "#b35c00",
                    font=("Segoe UI", 10, "bold"))
    style.configure("Status.TLabel", relief="sunken", padding=(8, 3))
    style.configure("TLabelframe", background=bg, bordercolor=border)
    style.configure("TLabelframe.Label", background=bg, foreground=accent, font=("Segoe UI", 9, "bold"))

    style.configure("TButton", background=button, padding=(10, 4), relief="flat")
    style.map("TButton", background=[("disabled", panel), ("pressed", selection), ("active", hover)],
              foreground=[("disabled", disabled), ("pressed", "white")], bordercolor=[("focus", accent)])
    primary_background = accent if name == "dark" else button
    # Dark text keeps sufficient contrast on SuperPyBackup's bright blue in dark mode.
    primary_foreground = "#10141a" if name == "dark" else fg
    style.configure("Accent.TButton", background=primary_background, foreground=primary_foreground,
                    font=("Segoe UI", 10, "bold"), padding=(14, 8))
    style.map("Accent.TButton", background=[("disabled", panel), ("pressed", selection),
                                          ("active", "#6aa0ff" if name == "dark" else hover)],
              foreground=[("disabled", disabled), ("pressed", "white")])

    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=bg, foreground=fg, indicatorbackground=field,
                        indicatorforeground=fg, indicatorcolor=field, upperbordercolor=border,
                        lowerbordercolor=border, padding=(0, 3))
        style.map(widget, background=[("active", bg)], foreground=[("disabled", disabled)],
                  indicatorcolor=[("selected", accent), ("pressed", selection)],
                  indicatorbackground=[("disabled", panel)])

    style.configure("TEntry", padding=4)
    style.map("TEntry", fieldbackground=[("disabled", panel), ("readonly", field)],
              bordercolor=[("focus", accent)], foreground=[("disabled", disabled)])
    style.configure("TCombobox", background=button, arrowcolor=fg, selectbackground=field, selectforeground=fg)
    style.map("TCombobox", fieldbackground=[("readonly", field)], selectbackground=[("readonly", field)],
              selectforeground=[("readonly", fg)], bordercolor=[("focus", accent)])
    for key, value in (("background", field), ("foreground", fg),
                       ("selectBackground", selection), ("selectForeground", "white")):
        root.option_add(f"*TCombobox*Listbox.{key}", value)

    style.configure("TNotebook", background=bg, bordercolor=border, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=panel, foreground=palette["muted"],
                    padding=(14, 6), bordercolor=border)
    style.map("TNotebook.Tab", background=[("selected", bg), ("active", hover)],
              foreground=[("selected", fg)], expand=[("selected", (0, 0, 0, 2))])
    style.configure("Treeview", background=field, fieldbackground=field, rowheight=24)
    style.map("Treeview", background=[("selected", selection)], foreground=[("selected", "white")])
    style.configure("Treeview.Heading", background=button, relief="flat", padding=4)
    style.map("Treeview.Heading", background=[("active", hover)])
    style.configure("TScrollbar", background=button, arrowcolor=fg)
    style.map("TScrollbar", background=[("active", hover)])

    style_native_widgets(root, name)


def style_native_widgets(widget, name):
    palette = PALETTES[name]
    if isinstance(widget, (tk.Tk, tk.Toplevel)):
        widget.configure(background=palette["bg"])
        dark_titlebar(widget, name == "dark")
    elif isinstance(widget, tk.Text):
        widget.configure(background=palette["field"], foreground=palette["fg"],
                         insertbackground=palette["fg"], selectbackground=palette["select"],
                         selectforeground="white", highlightbackground=palette["border"],
                         highlightcolor=palette["accent"], highlightthickness=1, relief="flat")
    for child in widget.winfo_children():
        style_native_widgets(child, name)


def dark_titlebar(window, dark):
    try:
        get_parent = ctypes.windll.user32.GetParent
        get_parent.argtypes = (wintypes.HWND,)
        get_parent.restype = wintypes.HWND
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        set_attribute.restype = ctypes.c_long
        hwnd = get_parent(window.winfo_id()) or window.winfo_id()
        value = wintypes.BOOL(dark)
        if set_attribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)) != 0:
            set_attribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError, tk.TclError):
        # Older Windows versions may leave title bars to the system theme.
        pass
