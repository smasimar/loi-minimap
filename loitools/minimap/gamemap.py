"""The game's own area maps, decoded from the LoI disc image (FILEDATA.BIN).

Each area ("world") has a map package with
  * a type-16 room table: per room a 0x60-byte record
      +0x00 game area id (as in RAM; one map can hold several areas)
      +0x01 room number          +0x02 floor (s8: 0 = 1F, -1 = B1F, ...)
      +0x08 flags (low bits 2/3: red save/warp room)
      +0x10 number of mesh parts  +0x1A outline node ids  +0x1E fill node ids
      +0x20..0x2F extra node ids (icons)
      +0x40 room origin (x, y, z) in map units, +0x4C rotation (radians)   +0x50 scale (x, y, z)
  * a type-14 VIF model: map geometry in map units x16 (z negated), split into nodes.

Player position: map = origin + R * (local_x * sx, -local_z * sz) * 1.6, where R is the room's
rotation (+0x4C). Most rooms are turned by quarter turns, but not all: much of the Dark Palace is
at 45 degrees, and the Pagoda and Castle Entrance have odd angles.

The decoded maps are cached as JSON in data/gamemap/, so the ISO is only needed once.
"""

import collections
import glob
import math
import json
import os
import re
import struct

import numpy as np

from .. import DATA_DIR

CACHE = os.path.join(DATA_DIR, "gamemap")
CACHE_VERSION = 3  # bump when decode_world's output changes, so the maps are extracted again
LOCAL_SCALE = 1.6  # mesh units per room-local unit


# --- extraction --------------------------------------------------------------------

def find_iso():
    """Locate the LoI ISO through PCSX2's game directories."""
    ini = os.path.expanduser(r"~\Documents\PCSX2\inis\PCSX2.ini")
    dirs = []
    if os.path.exists(ini):
        for line in open(ini, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*(?:Recursive)?Paths\s*=\s*(.+)", line)
            if m:
                dirs.append(m.group(1).strip())
    for d in dirs:
        for f in glob.glob(os.path.join(glob.escape(d), "**", "*.iso"), recursive=True):
            if "lament" in os.path.basename(f).lower():
                return f
    return None


def _node_chunks(model):
    """node id -> list of (positions, faces) from a type-14 map model."""
    from . import mapmesh
    u = np.frombuffer(model[: len(model) // 4 * 4], dtype="<u4")
    heads = [int(i) * 4 for i in np.nonzero((u[:-3] == 5) & (u[1:-2] == 0) & (u[2:-1] == 0x61) & (u[3:] == 0))[0]]
    chunks = list(mapmesh.chunks(model))
    nodes = collections.defaultdict(list)
    # each chunk's node id sits in the 0x70-byte block right before its own header
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(model)
        nid = struct.unpack_from("<I", model, h - 0x70 + 16)[0] >> 24
        inside = [c for c in chunks if h <= c[0] < end]
        for _, pos, faces in inside:
            nodes[nid].append((pos, faces))
    return nodes


def _tris(parts):
    """[(pos, faces)] -> list of triangles [[x, z] * 3] in mesh units (ints)."""
    out = []
    for pos, faces in parts:
        for f in faces:
            out.append([[int(pos[i][0]), int(pos[i][2])] for i in f])
    return out


def _looks_local(fill, origin):
    """Shape stored in room-local coordinates: starts at (0, 0), far from the room's map origin."""
    if not fill:
        return False
    pts = np.array([p for t in fill for p in t])
    (x0, z0), (x1, z1) = pts.min(0), pts.max(0)
    far = max(x0 - origin[0], origin[0] - x1, z0 - origin[1], origin[1] - z1)
    return far > 2000 and x0 >= -400 and z1 <= 400


def _cos_sin(angle):
    """cos and sin of a room angle, exact for quarter turns (stored rounded, e.g. 1.5708)."""
    q = round(angle / (math.pi / 2))
    if abs(angle - q * math.pi / 2) < 1e-3:
        return [(1, 0), (0, 1), (-1, 0), (0, -1)][q % 4]
    return math.cos(angle), math.sin(angle)


def decode_world(table, model, mesh_dir=1):
    n = struct.unpack_from("<I", table, 0x24)[0]
    world = struct.unpack_from("<h", table, 0x28)[0]
    offs = struct.unpack_from(f"<{n}I", table, 0x50)
    nodes = _node_chunks(model)
    raw = []
    for o in offs:
        r = table[0x20 + o:0x20 + o + 0x60]
        r = r + bytes(0x60 - len(r))
        cnt = r[0x10]
        fill = _tris([p for v in r[0x1E:0x1E + cnt] for p in nodes.get(v, [])])
        outline = _tris([p for v in r[0x1A:0x1A + cnt] for p in nodes.get(v, [])])
        px, _, pz, angle = struct.unpack_from("<4f", r, 0x40)
        sx, _, sz = struct.unpack_from("<3f", r, 0x50)
        raw.append((r, fill, outline, [px * 16, -pz * 16], angle, sx or 1.0, sz or 1.0))
    # Some maps (e.g. Anti-Soul Mysteries Lab) store every room shape in room-local coordinates
    # instead of already placed on the map. Decide per map: a room near (0, 0) can look placed.
    local = bool(sum(_looks_local(f, org) for _, f, _, org, *_ in raw) > len(raw) / 2)
    rooms = {}
    for r, fill, outline, origin, angle, sx, sz in raw:
        if local:
            c, s_ = _cos_sin(angle)
            s_ *= mesh_dir

            def put(t):
                return [[int(origin[0] + c * p[0] * sx - s_ * p[1] * sz),
                         int(origin[1] + s_ * p[0] * sx + c * p[1] * sz)] for p in t]
            fill = [put(t) for t in fill]
            outline = [put(t) for t in outline]
        pts = np.array([p for t in fill for p in t] or [origin])
        (x0, z0), (x1, z1) = pts.min(0), pts.max(0)
        # A room that spans floors has one record per floor (e.g. Ghostly Theatre room 16); the
        # first keeps the plain "area:room" key, the others get the floor appended.
        key = f"{r[0]}:{r[1]}"
        floor = struct.unpack("b", r[2:3])[0]
        rooms[key if key not in rooms else f"{key}@{floor}"] = {
            "key": key, "area": r[0], "room": r[1], "floor": floor,
            "red": (r[8] & 7) in (2, 3),
            "origin": origin, "angle": angle, "rot": int(round(angle / (math.pi / 2))) % 4,
            "scale": [sx, sz],
            "bbox": [int(x0), int(z0), int(x1), int(z1)],
            "fill": fill, "outline": outline, "icons": [],  # +0x20..0x2F: extra nodes, not understood yet
        }
    floors = sorted({r["floor"] for r in rooms.values()})
    return {"version": CACHE_VERSION, "world": world, "floors": floors, "rooms": rooms,
            "local_shapes": local}


def extract_all(iso_path):
    from .filedata import FileData
    fd = FileData(iso_path)
    os.makedirs(CACHE, exist_ok=True)
    done = set()
    for s in fd.packages:
        ents = fd.entries(s)
        t16 = [e for e in ents if e["type"] == 16]
        t14 = [e for e in ents if e["type"] == 14]
        if not t16 or not t14:
            continue
        # the last room record runs past the declared entry size; read some slack (the bytes
        # after an entry belong to its own data, the next entry starts further on)
        e16 = t16[0]
        table = fd.read(s, e16["offset"], e16["size"] + 0x80)
        # the last mesh chunk's index data can run past the declared size (even past where the
        # next entry is said to start). The VIF unpack headers state exactly how much data
        # follows, and the decoder never reads beyond that, so a fixed allowance is safe.
        e14 = t14[0]
        model = fd.read(s, e14["offset"], e14["size"] + 0x100)
        w = decode_world(table, model)
        if w["world"] in done:
            continue
        done.add(w["world"])
        with open(os.path.join(CACHE, f"world_{w['world']}.json"), "w") as f:
            json.dump(w, f, separators=(",", ":"))
    return sorted(done)


# --- runtime -----------------------------------------------------------------------

class GameMaps:
    def __init__(self):
        self.worlds = {}
        for path in glob.glob(os.path.join(CACHE, "world_*.json")):
            with open(path) as f:
                w = json.load(f)
            self.worlds[w["world"]] = w
        # (game area id, room number) -> [(map world id, room key), ...], one per floor the room is
        # on; rooms record their own area
        self.index = collections.defaultdict(list)
        for w in self.worlds.values():
            for k, r in w["rooms"].items():
                self.index[(r["area"], r["room"])].append((w["world"], k))

    @classmethod
    def load(cls, iso_path=None):
        if not glob.glob(os.path.join(CACHE, "world_*.json")) or any(
                w.get("version") != CACHE_VERSION for w in cls().worlds.values()):
            iso_path = iso_path or find_iso()
            if not iso_path:
                raise FileNotFoundError("LoI ISO not found; set iso_path in config.json")
            extract_all(iso_path)
        return cls()

    def rooms(self, area, room):
        """All records of a game area id (RAM) and room number: one per floor it spans."""
        return [self.worlds[w]["rooms"][k] for w, k in self.index.get((area, room), ())]

    def room(self, area, room, floor=None):
        """Room record for a game area id (RAM) and room number, or None. For a room on several
        floors, the record on `floor` if there is one, else its first."""
        recs = self.rooms(area, room)
        return next((r for r in recs if r["floor"] == floor), recs[0] if recs else None)

    def world_for_area(self, area, room):
        hits = self.index.get((area, room))
        return self.worlds[hits[0][0]] if hits else None


def place(r, x, z, rot, mirror):
    """Room-local position -> map position. `rot` is the room's rotation in quarter turns: the
    record's own `rot` uses its exact angle, any other value turns the room from there."""
    lx, lz = x * r["scale"][0], -z * r["scale"][1]
    if mirror:
        lx, lz = -lz, -lx  # swap axes (reflection across the room's diagonal)
    c, s = _cos_sin(r["angle"] + (rot - r["rot"]) * math.pi / 2)
    return (r["origin"][0] + LOCAL_SCALE * (c * lx - s * lz),
            r["origin"][1] + LOCAL_SCALE * (s * lx + c * lz))


# Candidate conventions for turning the player's yaw (RAM, radians) into a room-local
# direction (dx, dz). Which one the game uses is confirmed from movement (overlay).
HEADINGS = [
    lambda y: (math.sin(y), math.cos(y)), lambda y: (math.sin(y), -math.cos(y)),
    lambda y: (-math.sin(y), math.cos(y)), lambda y: (-math.sin(y), -math.cos(y)),
    lambda y: (math.cos(y), math.sin(y)), lambda y: (math.cos(y), -math.sin(y)),
    lambda y: (-math.cos(y), math.sin(y)), lambda y: (-math.cos(y), -math.sin(y)),
]


def map_direction(r, dx, dz, rot, mirror):
    """Room-local direction -> map direction (unit vector in mesh coordinates)."""
    x0, z0 = place(r, 0, 0, rot, mirror)
    x1, z1 = place(r, dx * 100, dz * 100, rot, mirror)
    n = math.hypot(x1 - x0, z1 - z0) or 1.0
    return (x1 - x0) / n, (z1 - z0) / n


def _fill_tris(r):
    """The room's fill triangles as an (n, 3, 2) array, cached on the room."""
    tris = r.get("_tris")
    if tris is None:
        tris = r["_tris"] = np.array([t for t in r["fill"] if len(t) == 3], float).reshape(-1, 3, 2)
    return tris


def _near_tris(tris, pts, margin):
    """For each point in `pts` (m, 2): True if it is inside one of `tris` (n, 3, 2), or closer
    than `margin` to one."""
    p = pts[:, None, :]                                   # (m, 1, 2)
    a, b = tris, np.roll(tris, -1, axis=1)                # edges a[k] -> b[k], (n, 3, 2)
    ab = (b - a)[None]                                    # (1, n, 3, 2)
    ap = p[:, :, None, :] - a[None]                       # (m, n, 3, 2)
    cross = ab[..., 0] * ap[..., 1] - ab[..., 1] * ap[..., 0]
    inside = (cross > 0).all(-1) | (cross < 0).all(-1)    # (m, n)
    if margin > 0:
        t = np.clip((ap * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12), 0, 1)
        d = np.hypot(*np.moveaxis(ap - t[..., None] * ab, -1, 0)).min(-1)
        inside |= d < margin
    return inside.any(-1)


def inside_room(r, x, z, margin=0):
    """True if map point (x, z) is within `margin` mesh units of the room's shape."""
    x0, z0, x1, z1 = r["bbox"]
    if not (x0 - margin <= x <= x1 + margin and z0 - margin <= z <= z1 + margin):
        return False
    return bool(_near_tris(_fill_tris(r), np.array([[x, z]], float), margin)[0])


def path_inside(r, samples, rot):
    """Fraction of observed room-local positions that land inside the room's shape with `rot`."""
    x0, z0, x1, z1 = r["bbox"]
    pts = np.array([place(r, x, z, rot, False) for x, z in samples], float)
    ok = (pts[:, 0] >= x0 - 200) & (pts[:, 0] <= x1 + 200) & (pts[:, 1] >= z0 - 200) & (pts[:, 1] <= z1 + 200)
    if ok.any():
        ok[ok] = _near_tris(_fill_tris(r), pts[ok], 150)
    return ok.sum() / len(samples)


def check_orientation(r, samples):
    """Safety net for the room record's rotation. The game never mirrors rooms, so only the four
    rotations are candidates, and the record wins unless it clearly fails while another clearly fits.

    Returns (rot override or None, record_fit, best_fit), or None with too few samples.
    """
    if len(samples) < 8:
        return None
    fits = [path_inside(r, samples, rot) for rot in range(4)]
    own = fits[r["rot"]]
    best = max(range(4), key=fits.__getitem__)
    override = best if own < 0.7 and fits[best] >= 0.9 else None
    return override, own, fits[best]


FILL = (16, 40, 225, 255)          # explored room, in-game blue
FILL_RED = (225, 30, 30, 255)      # save / warp rooms
FILL_UNEXPLORED = (40, 50, 95, 170)
LINE = (255, 255, 255, 255)
LINE_UNEXPLORED = (120, 125, 150, 170)
CURRENT = (255, 220, 60, 255)


def render_floor(world, floor, explored=None, current=None, max_px=1400, show_unexplored=True):
    """Draw one floor like the in-game map.

    Returns (QImage, to_px) where to_px(x, z) maps mesh coordinates to image pixels.
    `explored`: set of room keys "area:room" (None = all explored); `current`: room key.
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPainterPathStroker, QPolygonF
    rooms = {k: r for k, r in world["rooms"].items() if r["floor"] == floor}
    pts = np.array([p for r in rooms.values() for t in r["fill"] + r["outline"] for p in t])
    (x0, z0), (x1, z1) = pts.min(0), pts.max(0)
    pad = 0.03 * max(x1 - x0, z1 - z0)
    x0, z0, x1, z1 = x0 - pad, z0 - pad, x1 + pad, z1 + pad
    s = max_px / max(x1 - x0, z1 - z0)
    w, h = int((x1 - x0) * s) + 1, int((z1 - z0) * s) + 1
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    qp = QPainter(img)
    qp.setRenderHint(QPainter.Antialiasing)
    stroker = QPainterPathStroker()
    stroker.setWidth(1.0)
    stroker.setJoinStyle(Qt.MiterJoin)

    def draw(tris, color):
        # all of a room's triangles as one path, so shared edges don't leave antialiasing seams;
        # triangles are wound the same way so overlaps (common in outline strips) stay filled
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        for t in tris:
            pts = [QPointF((p[0] - x0) * s, (p[1] - z0) * s) for p in t]
            if len(pts) == 3 and ((pts[1].x() - pts[0].x()) * (pts[2].y() - pts[0].y())
                                  - (pts[1].y() - pts[0].y()) * (pts[2].x() - pts[0].x())) < 0:
                pts.reverse()
            path.addPolygon(QPolygonF(pts))
            path.closeSubpath()
        # merge the triangles, then widen the shape by half a pixel all round, which keeps the
        # thin outlines as bold as the in-game map once the overlay scales the image down;
        # filled in one pass so translucent colours don't show the triangle edges
        shape = path.simplified()
        qp.fillPath(shape.united(stroker.createStroke(shape)), QColor(*color))

    for r in rooms.values():
        seen = explored is None or r["key"] in explored
        if not seen and not show_unexplored:
            continue
        draw(r["fill"], (FILL_RED if r["red"] else FILL) if seen else FILL_UNEXPLORED)
    for r in rooms.values():
        seen = explored is None or r["key"] in explored
        if not seen and not show_unexplored:
            continue
        draw(r["outline"] + r["icons"], (CURRENT if r["key"] == current else LINE) if seen else LINE_UNEXPLORED)
    qp.end()
    return img, (lambda x, z: ((x - x0) * s, (z - z0) * s))
