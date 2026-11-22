"""Program settings and the Windows autostart entry for the current user."""
import json
import os
import sys
import winreg
from pathlib import Path

from app_info import APP_NAME

DEFAULTS = {"theme": "dark", "tray": True, "start_hidden": True, "interval": 0, "developer": False,
            "sort": "date", "sort_descending": True, "check_updates": True, "spam_threshold": 5,
            # Tab Datenbank: chosen archive folder ("" = standard archive; an archive file chosen with 1.16/1.17 is
            # converted next to it), backup folder or file to restore at the next start,
            # last backup {"time", "file"}.
            "database": "", "restore_from": "", "last_backup": {}}
# Points of the server's spam rating from which a mail is marked as suspicious.
SPAM_THRESHOLDS = (3, 4, 5, 6, 7, 8, 10)
# Automatic sync interval in minutes; 0 switches it off.
INTERVALS = {0: "Aus", 10: "Alle 10 Minuten", 30: "Alle 30 Minuten", 60: "Jede Stunde", 120: "Alle 2 Stunden"}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MINIMIZED_ARGUMENT = "--minimized"


class Settings:
    def __init__(self, folder):
        self.path = folder / "einstellungen.json"
        self.legacy_theme = folder / "darstellung.txt"
        self.values = dict(DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            stored = {"theme": self._legacy_theme()}
        except (OSError, ValueError):
            stored = {}
        for key, default in DEFAULTS.items():
            if isinstance(stored, dict) and isinstance(stored.get(key), type(default)):
                self.values[key] = stored[key]
        if self.values["theme"] not in ("dark", "light"):
            self.values["theme"] = "dark"
        if self.values["spam_threshold"] not in SPAM_THRESHOLDS:
            self.values["spam_threshold"] = 5
        if self.values["interval"] not in INTERVALS:
            self.values["interval"] = 0
        if self.values["sort"] not in ("attach", "subject", "sender", "date", "time", "size", "spam"):
            self.values["sort"] = "date"

    def _legacy_theme(self):
        try:
            return self.legacy_theme.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return DEFAULTS["theme"]

    def __getitem__(self, key):
        return self.values[key]

    def set(self, key, value):
        """Store one setting. Raises OSError if the file cannot be written."""
        self.values[key] = value
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.values, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {MINIMIZED_ARGUMENT}'
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    script = Path(__file__).resolve().with_name("app.py")
    return f'"{windowless if windowless.exists() else python}" "{script}" {MINIMIZED_ARGUMENT}'


def autostart_entry():
    """Return the registered autostart command, or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value if isinstance(value, str) else None
    except OSError:
        return None


def set_autostart(enabled):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
