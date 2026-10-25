"""The game's own area maps, decoded from the LoI disc image (FILEDATA.BIN).

Each area ("world") has a map package with
  * a type-16 room table: per room a 0x60-byte record
      +0x00 game area id (as in RAM; one map can hold several areas)
      +0x01 room number          +0x02 floor (s8: 0 = 1F, -1 = B1F, ...)
      +0x08 flags (low bits 2/3: red save/warp room; bit 7: not drawn on the map)
      +0x10..0x18 node counts (u16 each) for the five groups whose ids (4 bytes each) follow:
      +0x1A outline  +0x1E fill  +0x22 sub-weapon spots  +0x26 locked doors  +0x2A labels
      (floor-change labels and the "S" start point)
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
CACHE_VERSION = 12  # bump when decode_world's output changes, so the maps are extracted again
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


# Sub-weapon spots of maps stored in room-local coordinates, (game area id, room) -> map position
# of the spot's centre, measured from in-game map screenshots (Anti-Soul Mysteries Lab 1F, 2F
# and 3F). The map data holds no position for them; they sit on the corridor walls, likely where the
# candelabras are.
LOCAL_SPOTS = {(1, 38): (-21346, 6389), (1, 40): (20113, 9700), (1, 39): (-595, 17078),
               (1, 46): (-623, 17308), (1, 49): (-640, 16960)}


def _place_local_icons(icons, fill, origin, angle, key):
    """Place the icons of a map stored in room-local coordinates (Anti-Soul Mysteries Lab).

    Their glyphs are upright and centred on (0, 0), and no position is stored for them, so they
    go where the in-game map shows them: sub-weapon spots at LOCAL_SPOTS (else the centre of the
    room's shape) and the "S" at the centre of the room's shape; a floor-change label with its
    arrow on the room's outermost point in the label's direction (the exit it marks); a locked
    door at the end of the room farthest from its origin. `key`: (game area id, room)."""
    pts = np.array([p for t in fill for p in t], float)
    centre = (pts.min(0) + pts.max(0)) / 2
    spot = np.array(LOCAL_SPOTS.get(key, centre), float)

    corners = np.unique(pts, axis=0)  # each corner once: shared ones would weigh the mean

    def outermost(d, tol=200.0):  # mean of the fill corners furthest along direction d
        dots = corners @ d
        return corners[dots >= dots.max() - tol].mean(0)

    def moved(tris, by):
        return [[[int(p[0] + by[0]), int(p[1] + by[1])] for p in t] for t in tris]
    out = {"markers": moved(icons["markers"], spot), "labels": []}
    for lab in icons["labels"]:
        parts = _arrow_parts(lab)
        if parts is None:
            g = np.array([p for t in lab for p in t], float)
            out["labels"].append(moved(lab, centre - (g.min(0) + g.max(0)) / 2))
        else:
            a, d = parts
            out["labels"].append(moved(lab, outermost(d) - a))
    locks = icons["locks"]
    if locks:
        g = np.array([p for t in locks for p in t], float)
        # towards the far end: the room axis closest to origin -> centre (that line itself runs
        # diagonally when the origin is a corner, which would pick a single corner)
        away = centre - np.array(origin, float)
        ca, sa = math.cos(angle), math.sin(angle)
        axes = [np.array(a) for a in ((ca, sa), (-sa, ca), (-ca, -sa), (sa, -ca))]
        at = outermost(max(axes, key=lambda a: a @ away)) if np.hypot(*away) else centre
        out["locks"] = moved(locks, at - (g.min(0) + g.max(0)) / 2)
    else:
        out["locks"] = []
    return out


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
        # icon node groups: count at +0x14/+0x16/+0x18, ids at +0x22/+0x26/+0x2A
        icons = {name: _tris([p for v in r[ids:ids + r[n_at]] for p in nodes.get(v, [])])
                 for name, n_at, ids in (("markers", 0x14, 0x22), ("locks", 0x16, 0x26))}
        # labels stay one list per node: each is text plus its own arrow (see _label_arrow)
        icons["labels"] = [t for t in (_tris(nodes.get(v, [])) for v in r[0x2A:0x2A + r[0x18]]) if t]
        # A boss door's skull (Castle Entrance room 2: one shape of 71 triangles, plus a small bar)
        # is stored with the labels; show it as a locked door. ("S" is one shape of 26.)
        if any(len(t) > 40 and len(_components(t)) == 1 for t in icons["labels"]):
            icons["locks"] += [tri for t in icons["labels"] for tri in t]
            icons["labels"] = []
        px, _, pz, angle = struct.unpack_from("<4f", r, 0x40)
        sx, _, sz = struct.unpack_from("<3f", r, 0x50)
        raw.append((r, fill, outline, icons, [px * 16, -pz * 16], angle, sx or 1.0, sz or 1.0))
    # Some maps (e.g. Anti-Soul Mysteries Lab) store every room shape in room-local coordinates
    # instead of already placed on the map. Decide per map: a room near (0, 0) can look placed.
    local = bool(sum(_looks_local(f, org) for _, f, _, _, org, *_ in raw) > len(raw) / 2)
    rooms = {}
    for r, fill, outline, icons, origin, angle, sx, sz in raw:
        if local:
            c, s_ = _cos_sin(angle)
            s_ *= mesh_dir

            def put(t):
                return [[int(origin[0] + c * p[0] * sx - s_ * p[1] * sz),
                         int(origin[1] + s_ * p[0] * sx + c * p[1] * sz)] for p in t]
            fill = [put(t) for t in fill]
            outline = [put(t) for t in outline]
            icons = _place_local_icons(icons, fill, origin, angle, (r[0], r[1]))
        pts = np.array([p for t in fill for p in t] or [origin])
        (x0, z0), (x1, z1) = pts.min(0), pts.max(0)
        # A room that spans floors has one record per floor (e.g. Ghostly Theatre room 16); the
        # first keeps the plain "area:room" key, the others get the floor appended.
        key = f"{r[0]}:{r[1]}"
        floor = struct.unpack("b", r[2:3])[0]
        rooms[key if key not in rooms else f"{key}@{floor}"] = {
            "key": key, "area": r[0], "room": r[1], "floor": floor,
            "red": (r[8] & 7) in (2, 3),
            # bit 7: not on the in-game map (only House of Sacred Remains room 0, a round room
            # laid over room 72)
            "hidden": bool(r[8] & 0x80),
            "origin": origin, "angle": angle, "rot": int(round(angle / (math.pi / 2))) % 4,
            "scale": [sx, sz],
            "bbox": [int(x0), int(z0), int(x1), int(z1)],
            "fill": fill, "outline": outline,
            # sub-weapon spots (circles), locked doors (keyhole), floor-change labels and the "S"
            # start point
            **icons,
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
LABEL = (190, 110, 255, 255)       # floor-change labels
LOCK = (225, 30, 30, 255)          # locked doors (generic: the in-game key colour isn't decoded)
MARKER = (255, 210, 40, 255)       # sub-weapon spots, yellow like the candles of the candelabra


ARROW_SHIFT = 300.0  # mesh units a floor-change arrow is moved away from the exit it marks
ARROW_SCALE = 2.0
ARROW_OUTLINE_PX = 6.0  # white outline, in render pixels (the overlay shows the floor ~3x smaller)


def _components(tris):
    """Split triangles into groups that share corners (separate shapes of one node)."""
    parent = list(range(len(tris)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    owner = {}
    for i, t in enumerate(tris):
        for p in map(tuple, t):
            parent[root(i)] = root(owner.setdefault(p, i))
    groups = collections.defaultdict(list)
    for i, t in enumerate(tris):
        groups[root(i)].append(t)
    return list(groups.values())


def _arrow_parts(tris):
    """(arrow centre, unit direction from the arrow to the text) of a floor-change label, or None
    for the group's other nodes (the "S" start point, small marks drawn as part of the map). A
    label is text plus one arrow triangle (the only triangle sharing no corner with another)
    between the exit it marks and the text, so the direction points away from the exit."""
    parts = _components(tris)
    arrows = [p for p in parts if len(p) == 1]
    if len(arrows) != 1 or len(tris) < 4:
        return None
    a = np.mean(arrows[0][0], axis=0)
    text = np.mean([p for part in parts if part is not arrows[0] for t in part for p in t], axis=0)
    d, n = text - a, np.hypot(*(text - a))
    return (a, d / n) if n else None


def _label_arrow(tris):
    """The arrow of a floor-change label, enlarged and moved away from the exit it marks, or None
    for the group's other nodes (see _arrow_parts). Only the arrow is drawn, not the text."""
    parts = _arrow_parts(tris)
    if parts is None:
        return None
    a, d = parts
    arrow = next(np.array(p[0], float) for p in _components(tris) if len(p) == 1)
    centre = a + d * ARROW_SHIFT
    return [[tuple(centre + (p - a) * ARROW_SCALE) for p in arrow]]


def _lock_opened(r, rooms, explored, reach=600):
    """True once the room behind a locked door is explored: it can only be entered through the
    door, and the in-game map drops the keyhole once the door is open. The room behind is the
    other room of the floor within `reach` mesh units of the keyhole. With no explored set
    (everything shown as explored) the door stays drawn."""
    if explored is None:
        return False
    pts = np.array([p for t in r["locks"] for p in t], float)
    c = ((pts.min(0) + pts.max(0)) / 2)[None]
    return any(o["key"] in explored for o in rooms.values()
               if o["key"] != r["key"] and _near_tris(_fill_tris(o), c, reach)[0])


def _lock_bar(tris, angle, floor_tris, thickness=0.3):
    """A bar across the corridor where the game draws its keyhole (which doesn't read well at
    minimap size), like a closed door: centred on the keyhole, as long as the keyhole's longest
    extent (about the corridor's width), `thickness` of that wide. "Across" is whichever of the
    room's two axes (from its rotation `angle`) leaves the floor's shapes (`floor_tris`,
    (n, 3, 2)) sooner. Two triangles."""
    pts = np.array([p for t in tris for p in t], float)
    c = (pts.min(0) + pts.max(0)) / 2
    ca, sa = math.cos(angle), math.sin(angle)

    def span(u):  # free width through c along u
        steps = np.arange(40, 4000, 40)[:, None] * u
        out = [~_near_tris(floor_tris, c + sign * steps, 0) for sign in (1, -1)]
        return sum(int(np.argmax(o)) if o.any() else len(o) for o in out) * 40 + 40
    axes = [np.array(a) for a in ((ca, sa), (-sa, ca))]
    u = min(axes, key=span)
    half = (pts.max(0) - pts.min(0)).max() / 2
    v = np.array([-u[1], u[0]])
    a, b = u * half, v * half * thickness
    q = [c - a - b, c + a - b, c + a + b, c - a + b]
    return [[tuple(q[0]), tuple(q[1]), tuple(q[2])], [tuple(q[0]), tuple(q[2]), tuple(q[3])]]


def render_floor(world, floor, explored=None, current=None, max_px=1400, show_unexplored=True):
    """Draw one floor like the in-game map.

    Returns (QImage, to_px) where to_px(x, z) maps mesh coordinates to image pixels.
    `explored`: set of room keys "area:room" (None = all explored); `current`: room key.
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPainterPathStroker, QPolygonF
    rooms = {k: r for k, r in world["rooms"].items() if r["floor"] == floor and not r["hidden"]}
    pts = np.array([p for r in rooms.values()
                    for t in r["fill"] + r["outline"] + [t for lab in r["labels"] for t in lab] for p in t])
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

    def draw(tris, color, widen=1.0):
        # all of a room's triangles as one path, so shared edges don't leave antialiasing seams;
        # triangles are wound the same way so overlaps (common in outline strips) stay filled.
        # `widen`: stroke width in pixels added around the shape (more = an outline behind it)
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
        stroker.setWidth(widen)
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
        draw(r["outline"], (CURRENT if r["key"] == current else LINE) if seen else LINE_UNEXPLORED)
    floor_tris = None  # all fill triangles of the floor, built on first use (locked doors)
    for r in rooms.values():  # icons on top: labels and "S", locked doors, sub-weapon spots
        seen = explored is None or r["key"] in explored
        if not seen and not show_unexplored:
            continue
        for lab in r["labels"]:
            arrow = _label_arrow(lab)
            if arrow is None:  # "S" and small marks: white, where they are
                draw(lab, LINE if seen else LINE_UNEXPLORED)
            else:  # floor change: just the arrow, outlined so it reads on the room colours
                draw(arrow, LINE if seen else LINE_UNEXPLORED, widen=ARROW_OUTLINE_PX)
                draw(arrow, LABEL if seen else LINE_UNEXPLORED)
        if r["locks"] and not _lock_opened(r, rooms, explored):
            if floor_tris is None:
                floor_tris = np.concatenate([_fill_tris(x) for x in rooms.values()])
            bar = _lock_bar(r["locks"], r["angle"], floor_tris)
            draw(bar, LINE if seen else LINE_UNEXPLORED, widen=ARROW_OUTLINE_PX)
            draw(bar, LOCK if seen else LINE_UNEXPLORED)
        if r["markers"]:
            # generic: which sub-weapon lies there is game state, not map data. A white rim
            # around a yellow disc.
            # A room can have several spots in one node: shrink each circle about its own centre.
            disc = []
            for circle in _components(r["markers"]):
                pts = np.array([p for t in circle for p in t], float)
                c = (pts.min(0) + pts.max(0)) / 2
                disc += [[c + (np.array(p) - c) * 0.7 for p in t] for t in circle]
            draw(r["markers"], LINE if seen else LINE_UNEXPLORED)
            draw(disc, MARKER if seen else LINE_UNEXPLORED)
    qp.end()
    return img, (lambda x, z: ((x - x0) * s, (z - z0) * s))
