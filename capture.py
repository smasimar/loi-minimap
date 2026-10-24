"""Locating the PCSX2 window that runs LoI."""

import ctypes
import ctypes.wintypes as W

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: physical pixels
except (AttributeError, OSError):
    pass

GAME_TITLE = "Lament of Innocence"


def _rect(h):
    r = W.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def find_game_window():
    """Return (top_hwnd, render_rect) for the PCSX2 window running LoI, or (None, None).

    PCSX2-qt renders into a native child window; the largest visible child is the game view.
    """
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)
    def top_cb(h, _):
        # InternalGetWindowText never sends messages, so a hung window elsewhere can't block us
        # (GetWindowText would wait on that window's message loop indefinitely).
        buf = ctypes.create_unicode_buffer(512)
        user32.InternalGetWindowText(h, buf, 512)
        if GAME_TITLE in buf.value and user32.IsWindowVisible(h):
            found.append(h)
        return True

    user32.EnumWindows(top_cb, 0)
    if not found:
        return None, None
    top = found[0]
    kids = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)
    def kid_cb(h, _):
        if user32.IsWindowVisible(h):
            kids.append(_rect(h))
        return True

    user32.EnumChildWindows(top, kid_cb, 0)
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    rect = max(kids, key=area) if kids else _rect(top)
    return top, rect


def is_minimized(h):
    return bool(user32.IsIconic(h))
