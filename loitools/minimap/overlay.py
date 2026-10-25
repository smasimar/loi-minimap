"""Minimap overlay: draws the game's own map with the player's position on top of PCSX2.

The map is the game's own map data, decoded from the disc image (see gamemap.py); the
player's room, position and facing are read live from game memory over PINE.

Hotkeys (global, hold Ctrl+Shift):
  M      show / hide overlay
  Z      toggle whole floor / zoomed minimap
  + / -  bigger / smaller (also Up / Down)
  C      move to the next corner (also Left / Right)
  O      cycle opacity
"""

import ctypes
import json
import math
import os
import struct
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .. import DATA_DIR
from ..pine import PineError

# --- game memory (SLUS-20733, NTSC-U) -------------------------------------------
ADDR_WORLD = 0x738E94       # u8  game area id (0 = House of Sacred Remains, ...)
ADDR_ROOM = 0x738E96        # u8  room number within the current area
ADDR_MENU = 0x734E12        # u8  1 while the map or pause menu is open
ADDR_SCREEN = 0x738EAC      # u32 non-zero while the map screen, Start menu or shop is open
# Rooms on two floors: the lower floor is at y = 0 and Leon is on the upper one above the switch
# height (y is negative up). Ghostly Theatre rooms 8 and 16 have their upper level at -2200 and
# below. The long spiral staircases (Castle Entrance rooms 0 and 1, Garden room 50) run from 0 to
# about -7600, and the in-game map switches floors halfway (measured -3630 to -3810).
FLOOR_SWITCH_Y = -1100.0
STAIRCASE_SWITCH_Y = -3800.0
FLOOR_SWITCH_Y_ROOM = {(6, 0): STAIRCASE_SWITCH_Y, (6, 1): STAIRCASE_SWITCH_Y, (3, 50): STAIRCASE_SWITCH_Y}
# Leon's actor (static). 0x792A30 / 0x792A54 hold the same values, but they belong to the camera
# system, which stops updating while the free camera has the camera redirected.
ADDR_POS = 0xB63430         # f32 x, y, z  player position, room-local coordinates
ADDR_YAW = 0xB63474         # f32 player facing (radians, room-local)

# Explored rooms: the game keeps one 12-byte record per room (byte +0: flags, 0 = unexplored),
# area after area, in a game-state block (the same layout as a save slot). 0x8060B8 points to a
# copy of the last loaded save; the live block sits 0x4690 bytes before that copy.
ADDR_SAVE_PTR = 0x8060B8
LIVE_FROM_SAVE_PTR = 4 - 0x4690
ROOM_TABLE = 0xF44                              # first room record (area 0, room 0)
AREA_ROOMS = [74, 55, 57, 65, 10, 49, 6, 10, 58]  # records per game area id 0..8 (area 4's map
                                                  # has rooms 0-3 only, but it keeps 10 records)
RECORD = 12

DEFAULTS = {
    "corner": "top-right",   # top-left, top-right, bottom-right, bottom-left
    "anchor": "image",       # "image": the game picture inside PCSX2's black bars, "view": the whole view
    "size": 0.32,            # overlay width as a fraction of the game view width
    "margin": 0.0,           # gap between the map and the game view edges (fraction of width)
    "opacity": 0.85,
    "zoom": False,
    "zoom_factor": 2.5,
    "aspect": 4 / 3,         # game picture aspect (PCSX2 "Auto 4:3/3:2" shows LoI at 4:3)
    "hide_when_unfocused": True,
    "show_status": True,     # area / room line under the map
    "explored": "game",      # "game": the game's own explored flags, "visited": rooms the overlay saw you in, "all"
    "show_unexplored": True, # draw unexplored rooms dimmed instead of hiding them
    "iso_path": None,        # LoI disc image; found through PCSX2's game folders when unset
    "trace": False,          # debugging: log every drawn frame's position data (trace.jsonl)
}
CORNERS = ["top-left", "top-right", "bottom-right", "bottom-left"]
OPACITIES = [1.0, 0.85, 0.65, 0.45]

user32 = ctypes.windll.user32
VK = {"M": [0x4D], "Z": [0x5A], "O": [0x4F], "C": [0x43],
      "UP": [0x26, 0xBB, 0x6B], "DOWN": [0x28, 0xBD, 0x6D],  # arrow, +/- (main row, numpad)
      "LEFT": [0x25], "RIGHT": [0x27]}

VISITED_FILE = os.path.join(DATA_DIR, "visited.json")   # rooms the overlay has seen you in
ORIENT_FILE = os.path.join(DATA_DIR, "orient.json")     # walked positions per room (rotation check)
ORIENT_CHECK = 3  # bump when room placement changes, so rooms with saved samples are re-checked
HEADING_FILE = os.path.join(DATA_DIR, "heading.json")   # calibrated facing-angle convention


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    # cloud sync (Dropbox) may briefly lock the file: never let that break the overlay
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except OSError:
            pass  # saved again on the next change


def outlined_text(p, rect, flags, text):
    """Light text with a dark outline, readable without a background panel."""
    p.setPen(QColor(0, 0, 0, 220))
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        p.drawText(rect.translated(dx, dy), flags, text)
    p.setPen(QColor(230, 230, 235))
    p.drawText(rect, flags, text)


class Game:
    """Reads the player's state through the shared PINE client (which reconnects by itself)."""

    def __init__(self, pine):
        self.pine = pine
        self.last_log = None

    def log(self, msg):
        if msg != self.last_log:  # report each kind of failure once, not every retry
            print(msg, flush=True)
            self.last_log = msg

    def read(self):
        """-> dict(world, room, menu, x, y, z, yaw) or None when PCSX2/game is unavailable."""
        try:
            world, room, menu, screen, x, y, z, yaw = self.pine.read_many(
                [(1, ADDR_WORLD), (1, ADDR_ROOM), (1, ADDR_MENU), (4, ADDR_SCREEN),
                 (4, ADDR_POS), (4, ADDR_POS + 4), (4, ADDR_POS + 8), (4, ADDR_YAW)])
        except (OSError, PineError) as e:
            self.log(f"minimap: PINE read failed: {e!r}")
            return None
        self.last_log = None
        f = lambda v: struct.unpack("<f", struct.pack("<I", v))[0]
        return {"world": world, "room": room, "menu": int(bool(menu or screen)),
                "x": f(x), "y": f(y), "z": f(z), "yaw": f(yaw)}


class Overlay(QWidget):
    def __init__(self, config, pine):
        """`config`: the shared Config (its "minimap" section is used); `pine`: the shared Pine."""
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("LoI minimap")

        # imported after QApplication exists so Qt owns the DPI-awareness setup
        global window, gamemap
        from . import gamemap, window

        self.config = config
        self.cfg = config.minimap
        for k, v in DEFAULTS.items():
            self.cfg.setdefault(k, v)
        self.gm = gamemap.GameMaps.load(self.cfg.get("iso_path"))
        self.game = Game(pine)
        self.state = None
        self.enabled = True
        self.status = ""
        self.status_until = 0
        self.game_view = None  # (key, qimage, to_px) of the floor currently drawn
        self.floor = None      # floor last shown: picks the record of a room that spans floors
        self.visited = {k: set(v) for k, v in load_json(VISITED_FILE, {}).items()}  # area -> rooms
        self.orient = load_json(ORIENT_FILE, {})  # "area:room" -> {"samples", "orient", "score"}
        self.room_since = (None, 0.0)  # (area, room) and when we entered it
        self.last_good = None  # (area, floor, x, z, time, facing) of the last position inside its room
        self.switch = None  # ((area, room), x, z, time) at the last room change while x/z are stale
        h = load_json(HEADING_FILE, {})
        self.heading = h.get("convention", 0)        # index into gamemap.HEADINGS
        self.heading_votes = h.get("votes", [0.0] * len(gamemap.HEADINGS))
        self.prev_pos = None  # (area, room, x, z) of the previous tick
        self.heading_steps = 0
        self.game_explored = None  # set of "area:room" from the game's own flags
        self.explored_bad = 0
        self.next_explored_read = 0.0
        self.top_hwnd, self.render_rect = None, None
        self.next_window_scan = 0
        self.keys_down = set()

        self.timer = QTimer(self, timeout=self.tick)
        self.timer.start(50)

    # --- helpers ---------------------------------------------------------------

    def flash(self, text, secs=4):
        print(text, flush=True)
        self.status, self.status_until = text, time.time() + secs

    def content_rect(self):
        """Physical-pixel rect of the 4:3 game image inside the PCSX2 render view."""
        l, t, r, b = self.render_rect
        w, h = r - l, b - t
        a = self.cfg["aspect"]
        if w / h > a:
            cw, ch = h * a, h
        else:
            cw, ch = w, w / a
        return l + (w - cw) / 2, t + (h - ch) / 2, cw, ch

    # --- hotkeys ---------------------------------------------------------------

    def poll_hotkeys(self):
        ctrl_shift = (user32.GetAsyncKeyState(0x11) & 0x8000) and (user32.GetAsyncKeyState(0x10) & 0x8000)
        pressed = {k for k, vks in VK.items()
                   if ctrl_shift and any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)}
        new = pressed - self.keys_down
        self.keys_down = pressed
        if not new:
            return
        c = self.cfg
        for k in new:
            if k == "M":
                self.enabled = not self.enabled
            elif k == "Z":
                c["zoom"] = not c["zoom"]
            elif k == "UP":
                c["size"] = min(0.9, round(c["size"] + 0.04, 2))
            elif k == "DOWN":
                c["size"] = max(0.12, round(c["size"] - 0.04, 2))
            elif k in ("LEFT", "RIGHT", "C"):
                i = CORNERS.index(c["corner"]) + (-1 if k == "LEFT" else 1)
                c["corner"] = CORNERS[i % 4]
            elif k == "O":
                i = (OPACITIES.index(c["opacity"]) + 1) if c["opacity"] in OPACITIES else 0
                c["opacity"] = OPACITIES[i % len(OPACITIES)]
        self.config.save()
        self.update()

    # --- main loop -------------------------------------------------------------

    def tick(self):
        self.poll_hotkeys()
        now = time.time()
        if now >= self.next_window_scan:
            self.top_hwnd, self.render_rect = window.find_game_window()
            self.next_window_scan = now + 0.25

        st = self.game.read()
        self.state = st
        # stay visible without game data so a connection problem shows up as a message
        visible = (self.enabled and self.top_hwnd is not None
                   and not window.is_minimized(self.top_hwnd) and not (st and st["menu"]))
        if visible and self.cfg["hide_when_unfocused"]:
            visible = user32.GetForegroundWindow() == self.top_hwnd

        if st is not None:
            seen = self.visited.setdefault(str(st["world"]), set())
            if not st["menu"] and st["room"] not in seen and self.gm.room(st["world"], st["room"]):
                seen.add(st["room"])
                save_json(VISITED_FILE, {k: sorted(s) for k, s in self.visited.items()})
            self.learn_orientation(st)
            self.learn_heading(st)
            if self.cfg["explored"] == "game" and now >= self.next_explored_read:
                self.read_explored(st)
                self.next_explored_read = now + 1.0

        if not visible:
            if self.isVisible():
                self.hide()
            return
        self.place()
        if not self.isVisible():
            self.show()
        self.update()

    def place(self):
        if self.cfg["anchor"] == "view":
            l, t, r, b = self.render_rect
            x, y, w, h = l, t, r - l, b - t
        else:
            x, y, w, h = self.content_rect()
        ow = w * self.cfg["size"]
        oh = ow * 0.75
        m = w * self.cfg["margin"]
        corner = self.cfg["corner"]
        ox = x + m if "left" in corner else x + w - ow - m
        oy = y + m if "top" in corner else y + h - oh - m
        # PCSX2 rects are physical pixels; Qt geometry is logical (DPI-scaled) pixels.
        # On Windows a QScreen's logical top-left equals its physical top-left.
        cx, cy = ox + ow / 2, oy + oh / 2
        screen = QApplication.primaryScreen()
        for sc in QApplication.screens():
            g, d = sc.geometry(), sc.devicePixelRatio()
            if g.x() <= cx < g.x() + g.width() * d and g.y() <= cy < g.y() + g.height() * d:
                screen = sc
                break
        g, d = screen.geometry(), screen.devicePixelRatio()
        geo = QRect(round(g.x() + (ox - g.x()) / d), round(g.y() + (oy - g.y()) / d), round(ow / d), round(oh / d))
        if self.geometry() != geo:
            self.setGeometry(geo)

    # --- room orientation --------------------------------------------------------

    def learn_orientation(self, st):
        """Collect where the player walks in each room; fit the room's map orientation from it."""
        key = (st["world"], st["room"])
        now = time.time()
        if self.room_since[0] != key:
            self.room_since = (key, now)
            return
        if st["menu"] or now - self.room_since[1] < 1.0:  # skip room-load transients
            return
        recs = self.gm.rooms(*key)
        if not recs or len({(tuple(x["origin"]), x["angle"]) for x in recs}) > 1:
            return  # unknown, or placed differently per floor (one sample set can't check both)
        r = recs[0]
        rec = self.orient.setdefault(f"{key[0]}:{key[1]}", {"samples": []})
        sm = rec["samples"]
        added = len(sm) < 80 and not (sm and min(math.hypot(st["x"] - a, st["z"] - b) for a, b in sm[-20:]) < 150)
        if added:
            sm.append([round(st["x"], 1), round(st["z"], 1)])
        recheck = len(sm) >= 8 and rec.get("check") != ORIENT_CHECK
        if not (added and len(sm) in (8, 12, 16, 24, 32, 48, 64, 80)) and not recheck:
            if added:
                save_json(ORIENT_FILE, self.orient)
            return
        res = gamemap.check_orientation(r, sm)
        if res:
            override, own, best = res
            old = rec.get("override")
            rec["override"], rec["fit"], rec["check"] = override, round(own, 3), ORIENT_CHECK
            if override is not None and override != old:
                self.flash(f"room {key[1]}: rotation corrected to {override} "
                           f"({best:.0%} of path inside vs {own:.0%})", 5)
        save_json(ORIENT_FILE, self.orient)

    def read_explored(self, st):
        """Read the game's explored-room flags. Sanity check: the room you've been standing in for
        a few seconds must be explored; otherwise fall back to the overlay's own tracking."""
        try:
            pine = self.game.pine
            base = pine.read32(ADDR_SAVE_PTR) + LIVE_FROM_SAVE_PTR + ROOM_TABLE
            size = RECORD * sum(AREA_ROOMS)
            if not 0x100000 <= base < 0x2000000 - size:
                raise ValueError("bad pointer")
            blk = pine.read_block(base & ~7, (size + (base & 7) + 7) & ~7)[base & 7:]
        except (OSError, PineError, ValueError):
            return
        explored, off = set(), 0
        for area, n in enumerate(AREA_ROOMS):
            explored |= {f"{area}:{r}" for r in range(n) if blk[off + RECORD * r]}
            off += RECORD * n
        settled = self.room_since[0] == (st["world"], st["room"]) and time.time() - self.room_since[1] > 3
        if settled and self.gm.room(st["world"], st["room"]) and f"{st['world']}:{st['room']}" not in explored:
            self.explored_bad += 1
            if self.explored_bad == 3:
                self.game.log("explored flags look wrong here; using the overlay's own tracking")
                self.game_explored = None
            return
        self.explored_bad = 0
        if explored != self.game_explored:
            self.game_explored = explored

    def learn_heading(self, st):
        """Confirm the yaw convention: while walking, facing and movement direction agree."""
        prev, self.prev_pos = self.prev_pos, (st["world"], st["room"], st["x"], st["z"])
        if st["menu"] or not prev or prev[:2] != self.prev_pos[:2]:
            return
        dx, dz = st["x"] - prev[2], st["z"] - prev[3]
        step = math.hypot(dx, dz)
        if not 15 < step < 400:  # standing still, or a teleport / room load
            return
        for i, conv in enumerate(gamemap.HEADINGS):
            hx, hz = conv(st["yaw"])
            self.heading_votes[i] += (hx * dx + hz * dz) / step
        best = max(range(len(self.heading_votes)), key=self.heading_votes.__getitem__)
        if best != self.heading and self.heading_votes[best] > 30:
            self.heading = best
            self.flash(f"arrow direction calibrated (convention {best})", 4)
        self.heading_steps += 1
        if self.heading_steps % 25 == 0:
            save_json(HEADING_FILE, {"convention": self.heading, "votes": self.heading_votes})

    def room_orient(self, area, room):
        """(rot, mirror) if walking showed the room record's rotation to be wrong, else None."""
        rec = self.orient.get(f"{area}:{room}")
        if rec and rec.get("override") is not None:
            return (rec["override"], False)
        return None

    def pick_record(self, st):
        """The room's record for where Leon is. A room that spans floors has one per floor: take
        the one whose shape he's in (a stairway's parts differ), and among stacked ones (same
        shape on both floors) go by his height (see FLOOR_SWITCH_Y)."""
        recs = self.gm.rooms(st["world"], st["room"])
        if len(recs) < 2:
            return recs[0] if recs else None
        inside = [r for r in recs
                  if gamemap.inside_room(r, *gamemap.place(r, st["x"], st["z"], r["rot"], False), margin=400)]
        pool = inside or recs
        floors = sorted(r["floor"] for r in recs)
        switch = FLOOR_SWITCH_Y_ROOM.get((st["world"], st["room"]), FLOOR_SWITCH_Y)
        level = floors[-1] if st["y"] < switch else floors[0]
        return min(pool, key=lambda r: (abs(r["floor"] - level), r["floor"] != self.floor))

    # --- drawing ---------------------------------------------------------------

    def view(self, st):
        """-> (qimage, player px or None, facing (dx, dy) or None, note); qimage None if nothing to draw."""
        room = self.pick_record(st) if st else None
        if room is None:
            # no map data for this room (cutscene, menu, unknown area): keep the last floor, no dot
            note = (f"no map data for area {st['world']} room {st['room']}" if st
                    else "Connecting to PCSX2 (PINE)... is the game running with PINE enabled?")
            return (self.game_view[1] if self.game_view else None), None, None, note
        self.floor = room["floor"]
        w = self.gm.world_for_area(st["world"], st["room"])
        explored = None
        if self.cfg["explored"] == "game" and self.game_explored is not None:
            explored = self.game_explored
        elif self.cfg["explored"] in ("game", "visited"):  # fallback: our own tracking
            explored = {f"{a}:{r}" for a, rooms in self.visited.items() for r in rooms}
        cur = f"{st['world']}:{st['room']}"
        key = (w["world"], room["floor"], cur, len(explored or ()), self.cfg["show_unexplored"])
        if self.game_view is None or self.game_view[0] != key:
            qi, to_px = gamemap.render_floor(w, room["floor"], explored, current=cur,
                                             show_unexplored=self.cfg["show_unexplored"])
            self.game_view = (key, qi, to_px)
        _, qi, to_px = self.game_view
        orient = self.room_orient(st["world"], st["room"]) or (room["rot"], False)
        mx, mz = gamemap.place(room, st["x"], st["z"], *orient)
        facing = gamemap.map_direction(room, *gamemap.HEADINGS[self.heading](st["yaw"]), *orient)
        # During a room transition the room number and position update on different frames,
        # so the position briefly belongs to the other room. Hold the last good spot until the
        # position fits the current room again (or give up holding after 2 s).
        now = time.time()
        floor = room["floor"]
        raw = (mx, mz)
        # On a room change the room number switches first; the position stays frozen at the old
        # room's coordinates (~1.3 s) until the new room is placed. Those stale coordinates can
        # even land inside the new room, so treat "unchanged since the switch" as not yet valid.
        key = (st["world"], st["room"])
        if self.switch is None or self.switch[0] != key:
            first = self.switch is None
            self.switch = (key, st["x"], st["z"], now)
            if first:
                self.switch = (key, None, None, now)  # overlay just started: position is current
        stale = (self.switch[1] is not None and now - self.switch[3] < 3.0
                 and abs(st["x"] - self.switch[1]) < 0.5 and abs(st["z"] - self.switch[2]) < 0.5)
        if not stale and self.switch[1] is not None:
            self.switch = (key, None, None, self.switch[3])  # moved: valid from now on
        inside = not stale and gamemap.inside_room(room, mx, mz, margin=400)
        mode = "live"
        if inside:
            self.last_good = (st["world"], floor, mx, mz, now, facing)
        elif self.last_good and (stale or now - self.last_good[4] < 2.0):
            a0, f0, gx, gz, _, gfacing = self.last_good
            if (a0, f0) != (st["world"], floor):
                self.trace(st, raw, inside, "hidden", None)
                return qi, None, None, ""
            mx, mz, facing = gx, gz, gfacing  # the facing is stale too: hold both
            mode = "held"
        elif stale:  # never draw a stale position
            self.trace(st, raw, inside, "hidden", None)
            return qi, None, None, ""
        px = to_px(mx, mz)
        self.trace(st, raw, inside, mode, px)
        return qi, px, facing, ""

    def trace(self, st, raw, inside, mode, px):
        if not self.cfg["trace"]:
            return
        if not hasattr(self, "_trace_f"):
            d = os.path.join(os.environ.get("LOCALAPPDATA", DATA_DIR), "loi-tools")
            os.makedirs(d, exist_ok=True)
            self._trace_f = open(os.path.join(d, "trace.jsonl"), "a")
        rec = {"t": round(time.time(), 3), "area": st["world"], "room": st["room"], "menu": st["menu"],
               "x": round(st["x"], 1), "y": round(st["y"], 1), "z": round(st["z"], 1),
               "map": [round(raw[0]), round(raw[1])], "inside": inside, "mode": mode,
               "px": px and [round(px[0], 1), round(px[1], 1)]}
        self._trace_f.write(json.dumps(rec) + "\n")
        self._trace_f.flush()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        footer_h = max(14, H / 12)
        MH = H - footer_h  # map area above the footer strip
        p.setOpacity(self.cfg["opacity"])  # no panel background: only the map and marker are drawn
        p.setFont(QFont("Segoe UI", max(7, int(H / 28))))

        st = self.state
        img, pos, facing, note = self.view(st)
        if img is None:
            outlined_text(p, QRectF(8, 0, W - 16, MH), Qt.AlignCenter | Qt.TextWordWrap,
                          note or "Waiting for the game...")
        else:
            src = QRectF(0, 0, img.width(), img.height())
            if self.cfg["zoom"] and pos is not None:
                zw = src.width() / self.cfg["zoom_factor"]
                zh = zw * MH / W
                src = QRectF(pos[0] - zw / 2, pos[1] - zh / 2, zw, zh)
            s = min(W / src.width(), MH / src.height())  # fit, keeping aspect
            dw, dh = src.width() * s, src.height() * s
            # hug the corner the panel is anchored to (flush with the game view's edges)
            corner = self.cfg["corner"]
            dx = 0 if "left" in corner else W - dw
            dy = 0 if "top" in corner else MH - dh
            dst = QRectF(dx, dy, dw, dh)
            p.save()
            p.setClipRect(QRectF(0, 0, W, MH))
            p.drawImage(dst, img, src)
            p.restore()

            if pos is not None:
                mx = dst.x() + (pos[0] - src.x()) * s
                my = dst.y() + (pos[1] - src.y()) * s
                pulse = 0.5 + 0.5 * math.sin(time.time() * 6)
                rad = max(5, H / 30)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 80, 80, int(160 * pulse)), 2))
                p.drawEllipse(QPointF(mx, my), rad * 1.9, rad * 1.9)
                fx, fy = facing or (0.0, -1.0)
                sx, sy = -fy, fx  # perpendicular
                tip = QPointF(mx + fx * rad * 1.6, my + fy * rad * 1.6)
                left = QPointF(mx - fx * rad + sx * rad * 0.9, my - fy * rad + sy * rad * 0.9)
                notch = QPointF(mx - fx * rad * 0.45, my - fy * rad * 0.45)
                right = QPointF(mx - fx * rad - sx * rad * 0.9, my - fy * rad - sy * rad * 0.9)
                p.setPen(QPen(QColor(255, 255, 255), 1.5))
                p.setBrush(QColor(230, 30, 30))
                p.drawPolygon([tip, left, notch, right])

        where = f"area {st['world']} room {st['room']}" if st else ""
        if st and img is not None and not note and self.floor is not None:
            # same naming as the in-game map: floor 0 = 1F, -1 = B1F
            where += f" {self.floor + 1}F" if self.floor >= 0 else f" B{-self.floor}F"
        footer = where + (f" - {note}" if note else "") if st else ""
        if time.time() < self.status_until:
            footer = self.status
        if self.cfg["show_status"]:
            outlined_text(p, QRectF(8, H - footer_h - 2, W - 16, footer_h), Qt.AlignRight | Qt.AlignVCenter, footer)
        p.end()
