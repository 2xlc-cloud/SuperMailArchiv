"""Notification area icon (next to the clock) and single-instance signal via the Win32 API."""
import ctypes
import threading
from ctypes import wintypes

from program_icon import png_icon

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY, WM_CLOSE, WM_NULL, WM_APP = 0x0002, 0x0010, 0x0000, 0x8000
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0203, 0x0205, 0x007B
CALLBACK = WM_APP + 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
MENU_OPEN, MENU_QUIT = 1, 2
CLASS_NAME = "SuperMailBackup.Instance"


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
for name, args, result in (
    ("DefWindowProcW", (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM), LRESULT),
    ("RegisterClassW", (ctypes.POINTER(WNDCLASSW),), wintypes.ATOM),
    ("CreateWindowExW", (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                         wintypes.HINSTANCE, wintypes.LPVOID), wintypes.HWND),
    ("DestroyWindow", (wintypes.HWND,), wintypes.BOOL),
    ("GetMessageW", (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT), wintypes.BOOL),
    ("TranslateMessage", (ctypes.POINTER(wintypes.MSG),), wintypes.BOOL),
    ("DispatchMessageW", (ctypes.POINTER(wintypes.MSG),), LRESULT),
    ("PostMessageW", (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM), wintypes.BOOL),
    ("PostQuitMessage", (ctypes.c_int,), None),
    ("RegisterWindowMessageW", (wintypes.LPCWSTR,), wintypes.UINT),
    ("FindWindowW", (wintypes.LPCWSTR, wintypes.LPCWSTR), wintypes.HWND),
    ("GetWindowThreadProcessId", (wintypes.HWND, ctypes.POINTER(wintypes.DWORD)), wintypes.DWORD),
    ("AllowSetForegroundWindow", (wintypes.DWORD,), wintypes.BOOL),
    ("SetForegroundWindow", (wintypes.HWND,), wintypes.BOOL),
    ("CreatePopupMenu", (), wintypes.HMENU),
    ("AppendMenuW", (wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR), wintypes.BOOL),
    ("SetMenuDefaultItem", (wintypes.HMENU, wintypes.UINT, wintypes.UINT), wintypes.BOOL),
    ("TrackPopupMenu", (wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        wintypes.HWND, wintypes.LPVOID), wintypes.BOOL),
    ("DestroyMenu", (wintypes.HMENU,), wintypes.BOOL),
    ("GetCursorPos", (ctypes.POINTER(wintypes.POINT),), wintypes.BOOL),
    ("GetSystemMetrics", (ctypes.c_int,), ctypes.c_int),
    ("CreateIconFromResourceEx", (ctypes.c_char_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT), wintypes.HICON),
    ("DestroyIcon", (wintypes.HICON,), wintypes.BOOL),
):
    function = getattr(user32, name)
    function.argtypes, function.restype = args, result
shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

SHOW_MESSAGE = user32.RegisterWindowMessageW("SuperMailBackup.ShowWindow")
TASKBAR_CREATED = user32.RegisterWindowMessageW("TaskbarCreated")


def signal_running_instance():
    """Ask an already running instance to show its window. Returns True if one exists."""
    hwnd = user32.FindWindowW(CLASS_NAME, None)
    if not hwnd:
        return False
    process = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
    user32.AllowSetForegroundWindow(process.value)
    user32.PostMessageW(hwnd, SHOW_MESSAGE, 0, 0)
    return True


class TrayIcon:
    """Runs a hidden window with its own message loop; actions are reported through `notify(name)`."""

    def __init__(self, tooltip, notify):
        self.tooltip = tooltip
        self.notify = notify
        self.hwnd = None
        self.visible = False
        self.want_visible = False
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.procedure = WNDPROC(self.window_procedure)  # Keep a reference for the lifetime of the window.
        size = user32.GetSystemMetrics(49) or 16  # SM_CXSMICON
        self.icons = {}
        for color in ("blue", "orange"):
            data = png_icon(size, color)
            self.icons[color] = user32.CreateIconFromResourceEx(data, len(data), True, 0x00030000, size, size, 0)
        self.icon = self.icons["blue"]
        threading.Thread(target=self.run, name="TrayIcon", daemon=True).start()
        self.ready.wait(5)

    def run(self):
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW(lpfnWndProc=self.procedure, hInstance=instance, lpszClassName=CLASS_NAME)
        user32.RegisterClassW(ctypes.byref(window_class))
        self.hwnd = user32.CreateWindowExW(0, CLASS_NAME, CLASS_NAME, 0, 0, 0, 0, 0,
                                           None, None, instance, None)
        self.ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def data(self, flags=0):
        record = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1, uFlags=flags,
                                 uCallbackMessage=CALLBACK, hIcon=self.icon)
        record.szTip = self.tooltip[:127]
        return record

    def set_visible(self, visible):
        with self.lock:
            self.want_visible = visible
            if not self.hwnd or visible == self.visible:
                return
            command = NIM_ADD if visible else NIM_DELETE
            if shell32.Shell_NotifyIconW(command, ctypes.byref(self.data(NIF_MESSAGE | NIF_ICON | NIF_TIP))):
                self.visible = visible
            elif not visible:
                self.visible = False

    def set_tooltip(self, text):
        with self.lock:
            self.tooltip = text
            if self.visible:
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.data(NIF_TIP)))

    def set_busy(self, busy):
        """Show the orange icon while mails are being fetched."""
        with self.lock:
            self.icon = self.icons["orange" if busy else "blue"]
            if self.visible:
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.data(NIF_ICON)))

    def balloon(self, title, text):
        with self.lock:
            if self.visible:
                record = self.data(NIF_INFO)
                record.szInfoTitle, record.szInfo, record.dwInfoFlags = title[:63], text[:255], 0x01
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(record))

    def stop(self):
        self.set_visible(False)
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def show_menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, MENU_OPEN, "Öffnen")
        user32.AppendMenuW(menu, 0x0800, 0, None)  # MF_SEPARATOR
        user32.AppendMenuW(menu, 0, MENU_QUIT, "Beenden")
        user32.SetMenuDefaultItem(menu, MENU_OPEN, False)
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)  # Required so the menu closes when clicking elsewhere.
        choice = user32.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if choice == MENU_OPEN:
            self.notify("show")
        elif choice == MENU_QUIT:
            self.notify("quit")

    def window_procedure(self, hwnd, message, wparam, lparam):
        if message == CALLBACK:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.notify("show")
            elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self.show_menu()
            return 0
        if message == SHOW_MESSAGE:
            self.notify("show")
            return 0
        if message == TASKBAR_CREATED:
            # Explorer restarted; add the icon again if it should be visible.
            with self.lock:
                self.visible = False
            self.set_visible(self.want_visible)
            return 0
        if message == WM_DESTROY:
            for icon in self.icons.values():
                if icon:
                    user32.DestroyIcon(icon)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)
