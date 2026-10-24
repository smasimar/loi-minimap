"""Castlevania Crypt LoI area maps (fan-made by Starion, (c) 2004), and alignment of
in-game map captures onto them.

The images are downloaded to data/crypt/ on first use and are not part of this project.
Source: https://www.castlevaniacrypt.com/loi-maps/
"""

import os
import urllib.request

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "data", "crypt")
BASE = "https://www.castlevaniacrypt.com/wp-content/img/loi/maps/"
MAPS = {
    "castleentrance": "Castle Entrance",
    "houseofsacredremains": "House of Sacred Remains",
    "antisoulmysterieslab": "Anti-Soul Mysteries Lab",
    "gardenforgottenbytime": "Garden Forgotten By Time",
    "darkpalacewaterfalls": "Dark Palace of Waterfalls",
    "ghostlytheatre": "Ghostly Theatre",
    "pagodaofmistymoon": "Pagoda of the Misty Moon",
}
BG = np.array([51, 51, 51])  # map background grey


def ensure_downloaded():
    os.makedirs(DIR, exist_ok=True)
    for name in MAPS:
        path = os.path.join(DIR, name + ".png")
        if not os.path.exists(path):
            urllib.request.urlretrieve(BASE + name + ".png", path)


def _chans(img):
    return (img[..., i].astype(np.int16) for i in range(3))


def room_mask(img):
    """Room fill: light blue rooms and red save/warp rooms (with icons inside)."""
    r, g, b = _chans(img)
    blue = (b > 180) & (g > 110) & (g < 200) & (r < 140)
    red = (r > 180) & (g < 80) & (b < 80)
    m = (blue | red).astype(np.uint8)
    # close small holes left by item icons
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    # drop small blobs: legend icons and stray item markers outside rooms
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m)
    keep = stats[:, cv2.CC_STAT_AREA] >= 300
    keep[0] = False
    return keep[labels]


class CryptMap:
    def __init__(self, name):
        self.name = name
        self.title = MAPS[name]
        self.rgb = np.asarray(Image.open(os.path.join(DIR, name + ".png")).convert("RGB"))
        self.rooms = room_mask(self.rgb)
        # in-game outlines are drawn thicker: tolerate a few pixels when checking containment
        self.rooms_loose = cv2.dilate(self.rooms.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        h, w = self.rooms.shape
        # pixels that belong to the drawn map: anything non-background near a room (excludes legend text)
        near = cv2.dilate(self.rooms.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        drawn = np.abs(self.rgb.astype(np.int16) - BG).max(axis=2) > 25
        self.alpha = (near & drawn) | self.rooms
        # floors = clusters of rooms
        n, labels, stats, _ = cv2.connectedComponentsWithStats(
            cv2.dilate(self.rooms.astype(np.uint8), np.ones((31, 31), np.uint8)))
        self.labels = labels
        self.clusters = {i: tuple(int(v) for v in stats[i, :4]) for i in range(1, n)
                         if stats[i, cv2.CC_STAT_AREA] > 4000}

    def cluster_at(self, x, y):
        x, y = int(np.clip(x, 0, self.labels.shape[1] - 1)), int(np.clip(y, 0, self.labels.shape[0] - 1))
        lab = self.labels[y, x]
        return self.clusters.get(lab)  # (x, y, w, h) or None

    def rgba(self, dim_mask=None):
        """RGBA image with transparent background; rooms outside `dim_mask` darkened."""
        rgb = self.rgb.copy()
        if dim_mask is not None:
            dark = self.alpha & ~dim_mask
            rgb[dark] = (rgb[dark] * 0.35).astype(np.uint8)
        a = np.where(self.alpha, 255, 0).astype(np.uint8)
        return np.dstack([rgb, a])


_cache = {}


def load_all():
    ensure_downloaded()
    for name in MAPS:
        if name not in _cache:
            _cache[name] = CryptMap(name)
    return _cache


# --- alignment ----------------------------------------------------------------

WORK = 0.5  # crypt maps are matched at half resolution in the coarse search


def _coarse(game_mask, crypt, scales):
    """Best (score, scale, tx, ty) placing game_mask (binary) on crypt.rooms by scale + translation."""
    ch, cw = crypt.rooms.shape
    target = cv2.resize(crypt.rooms.astype(np.float32), (int(cw * WORK), int(ch * WORK)), interpolation=cv2.INTER_AREA)
    ys, xs = np.nonzero(game_mask)
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    tpl_full = game_mask[y0:y1, x0:x1].astype(np.float32)
    best = None
    for s in scales:
        sw, sh = int((x1 - x0) * s * WORK), int((y1 - y0) * s * WORK)
        if sw < 8 or sh < 8 or sw >= target.shape[1] or sh >= target.shape[0]:
            continue
        tpl = cv2.resize(tpl_full, (sw, sh), interpolation=cv2.INTER_AREA)
        # IoU of room shapes for every placement: penalizes mismatch both ways, so scale is constrained
        inter = cv2.matchTemplate(target, tpl, cv2.TM_CCORR)
        win = cv2.matchTemplate(target, np.ones_like(tpl), cv2.TM_CCORR)  # crypt room area under window
        iou = inter / (tpl.sum() + win - inter + 1e-6)
        _, score, _, loc = cv2.minMaxLoc(iou)
        if best is None or score > best[0]:
            tx = loc[0] / WORK - x0 * s
            ty = loc[1] / WORK - y0 * s
            best = (score, s, tx, ty)
    return best


def _refine(game_mask, crypt, H0):
    """ECC homography refinement. Returns H (game px -> crypt px)."""
    tpl = cv2.GaussianBlur(game_mask.astype(np.float32), (0, 0), 3)
    inp = cv2.GaussianBlur(crypt.rooms.astype(np.float32), (0, 0), 3)
    # inputMask lives in crypt space: only compare around where the game map currently lands,
    # so unexplored crypt rooms elsewhere don't pull the fit
    h, w = crypt.rooms.shape
    landed = cv2.warpPerspective(game_mask.astype(np.uint8), H0, (w, h), flags=cv2.INTER_NEAREST)
    region = cv2.dilate(landed, np.ones((41, 41), np.uint8))
    warp = H0.astype(np.float32)
    try:
        _, warp = cv2.findTransformECC(tpl, inp, warp, cv2.MOTION_HOMOGRAPHY,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), region, 5)
    except cv2.error:
        return H0
    return warp.astype(np.float64)


def containment(game_mask, crypt, H):
    """Fraction of game room pixels that land on crypt room pixels under H."""
    ys, xs = np.nonzero(game_mask)
    idx = np.random.default_rng(0).choice(len(xs), min(4000, len(xs)), replace=False)
    pts = cv2.perspectiveTransform(np.c_[xs[idx], ys[idx]].astype(np.float32)[None], H)[0]
    h, w = crypt.rooms.shape
    px, py = np.round(pts[:, 0]).astype(int), np.round(pts[:, 1]).astype(int)
    ok = (px >= 0) & (px < w) & (py >= 0) & (py < h)
    hit = np.zeros(len(px), bool)
    hit[ok] = crypt.rooms_loose[py[ok], px[ok]]
    return hit.mean()


def _align(game_mask, c, scales):
    """-> (H, containment, coarse_iou, coarse_scale) or (None, 0, 0, None)."""
    co = _coarse(game_mask, c, scales)
    if co is None:
        return None, 0.0, 0.0, None
    iou, s, tx, ty = co
    H0 = np.array([[s, 0, tx], [0, s, ty], [0, 0, 1]], dtype=np.float64)
    H = _refine(game_mask, c, H0)
    s0, s1 = containment(game_mask, c, H0), containment(game_mask, c, H)
    return (H, s1, iou, s) if s1 >= s0 else (H0, s0, iou, s)


WIDE = np.geomspace(0.3, 2.5, 40)  # the in-game map zoom is freely adjustable by the player
GOOD = 0.85


def register(game_mask, prefer=()):
    """Find the Crypt map and homography for an in-game Fixed-mode map mask.

    `game_mask`: binary room mask in game-frame pixels (e.g. 1024x768).
    `prefer`: map names to try first (current area); accepted on a good fit.
    Returns (map_name, H, score) or (None, None, best_score).
    """
    maps = load_all()
    prefer = [n for i, n in enumerate(prefer) if n in maps and n not in prefer[:i]]
    best = 0.0
    for name in prefer:
        H, score, _, _ = _align(game_mask, maps[name], WIDE)
        best = max(best, score)
        if H is not None and score >= GOOD:
            return name, H, score
    # global search: rank by shape agreement x containment
    results = []
    for name in maps:
        if name in prefer:
            continue
        H, score, iou, _ = _align(game_mask, maps[name], WIDE)
        if H is not None:
            results.append((iou * score, name, H, score))
    results.sort(key=lambda r: r[0], reverse=True)
    if not results or results[0][3] < GOOD:
        return None, None, max([best] + [r[3] for r in results])
    if len(results) > 1 and results[1][0] > 0.9 * results[0][0]:
        return None, None, results[0][3]  # ambiguous: two areas fit about equally well
    _, name, H, score = results[0]
    return name, H, score


def to_crypt(H, u, v, frame=(1024, 768)):
    """Game-frame normalized (u, v) -> crypt pixel (x, y)."""
    p = cv2.perspectiveTransform(np.array([[[u * frame[0], v * frame[1]]]], dtype=np.float32), H)[0, 0]
    return float(p[0]), float(p[1])
