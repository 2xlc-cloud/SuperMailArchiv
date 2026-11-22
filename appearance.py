"""Appearance shared with the SuperPyBackup desktop app."""
import ctypes
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk


# Base colours of the designs: (German name, background, input fields, text, accent). Everything else (panels,
# buttons, borders, selection, muted text) is derived below, with the contrast of text, links and selections
# checked, so every design stays readable. Mostly dark designs; "light" is the classic bright one.
THEMES = {
    "dark": ("Nachtblau (Standard)", "#1b1e24", "#14171c", "#e6eaf2", "#4c8dff"),
    "graphite": ("Graphit", "#202124", "#17181a", "#e8eaed", "#8ab4f8"),
    "anthracite": ("Anthrazit", "#262626", "#1c1c1c", "#ececec", "#9e9e9e"),
    "slate": ("Schiefer", "#1e2329", "#161a1f", "#dfe5ec", "#7aa2c8"),
    "midnight": ("Mitternacht", "#0f1626", "#0a0f1b", "#dde6f7", "#5b8def"),
    "polar": ("Polarnacht", "#2e3440", "#262b35", "#eceff4", "#88c0d0"),
    "deepsea": ("Tiefsee", "#0f1f24", "#0a1619", "#d8eef0", "#3fb6c6"),
    "petrol": ("Petrol", "#13262b", "#0d1c20", "#dceceb", "#2fa39a"),
    "forest": ("Waldgrün", "#16211a", "#101812", "#dfeee2", "#5cbf7a"),
    "moss": ("Moos", "#22241a", "#191b13", "#ebeedb", "#a3b84a"),
    "emerald": ("Smaragd", "#121a18", "#0c1311", "#ddf0ea", "#2ecc8f"),
    "aubergine": ("Aubergine", "#231a2b", "#1a1320", "#efe4f5", "#b07ae0"),
    "violet": ("Violette Nacht", "#1d1b2e", "#151422", "#e6e3f7", "#9d8cff"),
    "bordeaux": ("Bordeaux", "#2a1619", "#1f1013", "#f5e4e6", "#e06c7a"),
    "espresso": ("Espresso", "#231c18", "#1a1411", "#f2e8e0", "#c8916a"),
    "copper": ("Kupfer", "#1f1b19", "#161311", "#f0e6df", "#e07b39"),
    "amber": ("Bernstein", "#141414", "#0c0c0c", "#ece6d8", "#f0b429"),
    "contrast": ("Hoher Kontrast", "#000000", "#000000", "#ffffff", "#ffd400"),
    "fog": ("Nebelgrau", "#3a3d42", "#2f3236", "#f1f2f4", "#8fb8ff"),
    "light": ("Hell", "#f0f0f0", "#ffffff", "#1a1a1a", "#0067c0"),
}


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(value))):02x}" for value in rgb)


def mix(first, second, share):
    """first moved share (0…1) of the way towards second."""
    a, b = _rgb(first), _rgb(second)
    return _hex(x + (y - x) * share for x, y in zip(a, b))


def luminance(color):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(value) for value in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(first, second):
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _readable(color, background, towards, minimum):
    """color moved towards `towards` until it reaches the minimum contrast against background."""
    for step in range(21):
        candidate = mix(color, towards, step / 20)
        if contrast(candidate, background) >= minimum:
            return candidate
    return towards


def build_palette(bg, field, fg, accent):
    dark = luminance(bg) < 0.3
    lift = "#ffffff" if dark else "#000000"
    accent = _readable(accent, field, "#ffffff" if dark else "#000000", 4.5)
    accent = _readable(accent, bg, "#ffffff" if dark else "#000000", 4.5)
    # Selected rows show white text: the selection colour must be dark enough for it.
    select = _readable(mix(accent, bg, 0.35) if dark else accent, "#ffffff", "#000000", 4.5)
    return {
        "dark": dark, "bg": bg, "field": field, "fg": fg, "accent": accent, "select": select,
        "panel": mix(bg, lift, 0.04) if dark else bg,
        "muted": _readable(mix(fg, bg, 0.38), bg, fg, 4.5),
        "border": mix(bg, lift, 0.17 if dark else 0.28),
        "button": mix(bg, lift, 0.08 if dark else 0.06),
        "hover": mix(bg, lift, 0.15) if dark else "#e5f1fb",
        "disabled": mix(fg, bg, 0.55),
        # Text on accent-coloured buttons.
        "on_accent": "#10141a" if contrast("#10141a", accent) >= contrast("#ffffff", accent) else "#ffffff",
        "warning": "#f59b1a" if dark else "#b35c00",
    }


PALETTES = {key: build_palette(*colors[1:]) for key, colors in THEMES.items()}
# The original designs keep their exact colours.
PALETTES["dark"].update(panel="#232830", muted="#9aa4b5", border="#3a4250", select="#2f5fb3", button="#2b313b",
                        hover="#38404d", disabled="#6b7585", on_accent="#10141a")
PALETTES["light"].update(panel="#f0f0f0", muted="#555555", border="#adadad", select="#0063b1", button="#e1e1e1",
                         hover="#e5f1fb", disabled="#8a8a8a")


def theme_label(name):
    return THEMES.get(name, THEMES["dark"])[0]


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
    style.configure("Developer.TLabel", foreground=palette["warning"],
                    font=("Segoe UI", 10, "bold"))
    style.configure("Status.TLabel", relief="sunken", padding=(8, 3))
    style.configure("TLabelframe", background=bg, bordercolor=border)
    style.configure("TLabelframe.Label", background=bg, foreground=accent, font=("Segoe UI", 9, "bold"))

    style.configure("TButton", background=button, padding=(10, 4), relief="flat")
    style.map("TButton", background=[("disabled", panel), ("pressed", selection), ("active", hover)],
              foreground=[("disabled", disabled), ("pressed", "white")], bordercolor=[("focus", accent)])
    primary_background = accent if palette["dark"] else button
    # Dark or white text, whichever contrasts more with the design's accent colour.
    primary_foreground = palette["on_accent"] if palette["dark"] else fg
    style.configure("Accent.TButton", background=primary_background, foreground=primary_foreground,
                    font=("Segoe UI", 10, "bold"), padding=(14, 8))
    style.map("Accent.TButton", background=[("disabled", panel), ("pressed", selection),
                                          ("active", mix(accent, "#ffffff", 0.2) if palette["dark"] else hover)],
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
        dark_titlebar(widget, palette["dark"])
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
