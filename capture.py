"""PCSX2 window location + screen capture + LoI map-screen detection."""

import ctypes
import ctypes.wintypes as W

import numpy as np
from PIL import ImageGrab

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


def grab(rect):
    """Screen-grab `rect` and crop away letterbox bars. Returns (RGB ndarray, content_rect)."""
    img = np.asarray(ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB"))
    bright = img.max(axis=2) > 12
    rows, cols = np.nonzero(bright.any(axis=1))[0], np.nonzero(bright.any(axis=0))[0]
    if len(rows) == 0:
        return img, rect
    t, b, l, r = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    return img[t:b, l:r], tuple(int(v) for v in (rect[0] + l, rect[1] + t, rect[0] + r, rect[1] + b))


def map_mask(img):
    """Pixels of explored-room fill: saturated pure blue."""
    r, g, b = (img[..., i].astype(np.int16) for i in range(3))
    return (b > 170) & (r < 60) & (g < 60)


def marker_mask(img):
    """Pixels of the red player marker."""
    r, g, b = (img[..., i].astype(np.int16) for i in range(3))
    return (r > 190) & (g < 60) & (b < 60)


def looks_like_map(img):
    return map_mask(img).mean() > 0.02


def marker_pos(img):
    """Centroid (x, y) of the red player marker in normalized [0,1] coords, or None."""
    m = marker_mask(img)
    if m.sum() < 30:
        return None
    ys, xs = np.nonzero(m)
    h, w = m.shape
    return float(xs.mean() / w), float(ys.mean() / h)
