"""Learned map data, expressed on the Castlevania Crypt area maps.

Every capture of the in-game map (Fixed mode) is aligned to a Crypt map. From that:
  * rooms:    samples of (room-local x, z) -> player arrow position on the Crypt map
  * explored: which Crypt rooms have been seen explored on the in-game map

Room numbers are only unique within an area, so rooms are keyed "<map>:<room>".
"""

import json
import os
import time

import cv2
import numpy as np
from PIL import Image

import crypt

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB_FILE = os.path.join(DATA, "learned.json")
FRAME = (1024, 768)  # captures are resized to this before analysis


# --- in-game map screen analysis ------------------------------------------------

def _chans(img):
    return (img[..., i].astype(np.int16) for i in range(3))


def blue_mask(img):
    r, g, b = _chans(img)
    return (b > 170) & (r < 70) & (g < 70)


def compass_box(h, w):
    return int(0.64 * h), int(0.93 * h), int(0.76 * w), int(0.99 * w)


def game_room_mask(img):
    """Explored rooms on the in-game map: blue fill + red save/warp rooms."""
    h, w = img.shape[:2]
    r, g, b = _chans(img)
    red = (r > 180) & (g < 80) & (b < 80)
    t, bt, l, rt = compass_box(h, w)
    red[t:bt, l:rt] = False
    red[int(0.88 * h):] = False  # bottom bar
    m = blue_mask(img) | red
    m[int(0.88 * h):] = False
    return m


def find_arrow(img):
    """Player arrow (dark red, inside a glow) -> normalized (u, v), or None."""
    h, w = img.shape[:2]
    r, g, b = _chans(img)
    m = (r > 100) & (r < 200) & (g < 45) & (b < 80)
    t, bt, l, rt = compass_box(h, w)
    m[t:bt, l:rt] = False
    if m.sum() < 25:
        return None
    ys, xs = np.nonzero(m)
    cx, cy = np.median(xs), np.median(ys)
    for _ in range(3):  # converge on the densest cluster
        k = (np.abs(xs - cx) < 0.035 * w) & (np.abs(ys - cy) < 0.035 * w)
        if k.sum() < 15:
            return None
        cx, cy = xs[k].mean(), ys[k].mean()
    return float(cx / w), float(cy / h)


def is_fixed_mode(img):
    """Fixed mode: compass needle's red tip points straight up, and the arrow is not
    pinned to the screen centre (Rotate mode keeps it there)."""
    h, w = img.shape[:2]
    t, b, l, r_ = compass_box(h, w)
    r, g, bl = _chans(img[t:b, l:r_])
    red = (r > 150) & (g < 70) & (bl < 70)
    if red.sum() < 20:
        return False
    ys, xs = np.nonzero(red)
    dy, dx = ys.mean() - (b - t) / 2, xs.mean() - (r_ - l) / 2
    if not (dy < -0.15 * (b - t) and abs(dx) < 0.25 * abs(dy)):
        return False
    arrow = find_arrow(img)
    return arrow is None or np.hypot(arrow[0] - 0.5, arrow[1] - 0.5) > 0.015


# --- per-room similarity fit ----------------------------------------------------

def fit_room(samples):
    """samples [[x, z, cx, cy], ...] -> (a, b, mirror) with crypt = a*p + b (complex), or None."""
    s = np.asarray(samples, dtype=float)
    if len(s) < 2:
        return None
    p = s[:, 0] + 1j * s[:, 1]
    q = s[:, 2] + 1j * s[:, 3]
    if np.abs(p - p.mean()).max() < 150:  # too close together to resolve scale/rotation
        return None
    best = None
    for mirror in (False, True):
        pp = np.conj(p) if mirror else p
        A = np.c_[pp, np.ones(len(pp))]
        (a, b), *_ = np.linalg.lstsq(A, q, rcond=None)
        res = np.abs(A @ np.array([a, b]) - q).mean()
        if best is None or res < best[0]:
            best = (res, complex(a), complex(b), mirror)
    return best[1:]


def apply_fit(fit, x, z):
    a, b, mirror = fit
    p = complex(x, z)
    w = a * (p.conjugate() if mirror else p) + b
    return w.real, w.imag


# --- database --------------------------------------------------------------------

class MapDB:
    def __init__(self):
        os.makedirs(DATA, exist_ok=True)
        self.maps = crypt.load_all()
        self.rooms = {}       # "<map>:<room>" -> [[x, z, cx, cy], ...]
        self.last_map = None
        if os.path.exists(DB_FILE):
            with open(DB_FILE) as f:
                d = json.load(f)
            self.rooms, self.last_map = d["rooms"], d.get("last_map")
        self.fits = {k: fit_room(v) for k, v in self.rooms.items()}
        self.explored = {}    # map -> bool mask in crypt pixels
        for name, c in self.maps.items():
            path = os.path.join(DATA, f"explored_{name}.png")
            self.explored[name] = (np.asarray(Image.open(path)) > 0) if os.path.exists(path) \
                else np.zeros(c.rooms.shape, bool)
        self.version = time.time()  # bumped whenever images need redrawing
        self._rgba = {}

    def save(self):
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"rooms": self.rooms, "last_map": self.last_map}, f)
        os.replace(tmp, DB_FILE)

    def maps_with_room(self, room):
        return [k.split(":")[0] for k in self.rooms if k.split(":")[1] == str(room)]

    def add_capture(self, img, room, x, z):
        """Learn from one Fixed-mode map capture (RGB ndarray, letterbox removed)."""
        img = np.asarray(Image.fromarray(img).resize(FRAME, Image.BILINEAR))
        gm = game_room_mask(img)
        if gm.sum() < 500:
            return "no rooms visible"
        arrow = find_arrow(img)
        prefer = [m for m in self.maps_with_room(room)] + ([self.last_map] if self.last_map else [])
        name, H, score = crypt.register(gm, prefer=prefer)
        if name is None:
            return f"could not align to any area map (best fit {score:.2f})"
        self.last_map = name
        c = self.maps[name]

        # explored rooms: project the in-game room mask onto the crypt map
        h, w = c.rooms.shape
        warped = cv2.warpPerspective(gm.astype(np.uint8), H, (w, h), flags=cv2.INTER_NEAREST) > 0
        self.explored[name] |= warped & c.rooms
        Image.fromarray(self.explored[name].astype(np.uint8) * 255).save(
            os.path.join(DATA, f"explored_{name}.png"))
        self._rgba.pop(name, None)
        self.version = time.time()

        msg = f"{c.title} (fit {score:.2f})"
        if arrow is None:
            msg += ", arrow not found"
        elif room is not None:
            cx, cy = crypt.to_crypt(H, *arrow, frame=FRAME)
            key = f"{name}:{room}"
            samples = self.rooms.setdefault(key, [])
            if all(np.hypot(x - s[0], z - s[1]) > 60 for s in samples):
                samples.append([x, z, cx, cy])
                del samples[:-12]
            self.fits[key] = fit_room(samples)
            msg += f", room {room}: {len(samples)} sample(s){' + exact' if self.fits[key] else ''}"
        self.save()
        return msg

    def locate(self, room, x, z):
        """-> (map, (cx, cy) or None, exact) for the player, using the current area if known."""
        cands = self.maps_with_room(room)
        if not cands:
            return self.last_map, None, False
        name = self.last_map if self.last_map in cands else cands[0]
        self.last_map = name
        key = f"{name}:{room}"
        fit = self.fits.get(key)
        if fit:
            return name, apply_fit(fit, x, z), True
        s = np.asarray(self.rooms[key])
        return name, (float(s[:, 2].mean()), float(s[:, 3].mean())), False

    def map_rgba(self, name, dim_unexplored=True):
        """Crypt map with transparent background; unexplored rooms dimmed."""
        k = (name, dim_unexplored)
        if k not in self._rgba:
            c = self.maps[name]
            dim = None
            if dim_unexplored:
                # a crypt room counts as explored when >= 30% of it was seen explored in-game
                n, labels = cv2.connectedComponents(c.rooms.astype(np.uint8))
                seen = np.bincount(labels[self.explored[name]], minlength=n)
                total = np.bincount(labels.ravel(), minlength=n)
                explored_ids = (seen / np.maximum(total, 1)) >= 0.3
                explored_ids[0] = False
                room_ok = explored_ids[labels]
                # keep outlines/icons around explored rooms bright as well
                dim = cv2.dilate(room_ok.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
            self._rgba[k] = np.ascontiguousarray(c.rgba(dim))
        return self._rgba[k]
