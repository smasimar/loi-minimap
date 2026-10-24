"""Automatic RAM recording around room transitions (research: finding the explored-room flags).

Hooked into the overlay's tick (PCSX2 PINE allows one client, so the overlay does the reading):
  * every few seconds while in a room, a snapshot of game-state RAM is kept in memory;
    on a room change that latest one is saved as "<n>_out" (state just before leaving)
  * 1.5 s after entering a room, a snapshot is saved as "<n>_in"
  * mark() (hotkey) flags the most recent arrival as "new on the in-game map"

Files go to %LOCALAPPDATA%/LoI-map/auto/: <n>_<in|out>.npz (compressed RAM 0x100000-0xA00000) and log.jsonl.
"""

import json
import os
import threading
import time

import numpy as np

# several MB per room: keep it out of synced folders (the project lives in Dropbox)
OUT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LoI-map", "auto")
LO, HI = 0x100000, 0xA00000   # game code/data and state; above is mostly streamed room assets
KEEP_EVERY = 3.0              # seconds between rolling "before leaving" snapshots
SETTLE = 1.5                  # seconds after a room change before the "after entering" snapshot


class AutoRecorder:
    def __init__(self, pine):
        self.pine = pine
        os.makedirs(OUT, exist_ok=True)
        self.n = len([f for f in os.listdir(OUT) if f.endswith("_in.npz")])
        self.room = None          # (area, room) we are in
        self.entered = 0.0
        self.in_saved = True
        self.last = None          # (time, ram, state) rolling snapshot in the current room
        self.next_keep = 0.0
        self.busy = 0             # pending background writes

    def _grab(self):
        return np.frombuffer(self.pine.read_block(LO, HI - LO), dtype=np.uint8).copy()

    def _write(self, name, ram, meta):
        def work():
            try:
                np.savez_compressed(os.path.join(OUT, name + ".npz"), ram=ram, base=LO)
                with open(os.path.join(OUT, "log.jsonl"), "a") as f:
                    f.write(json.dumps(dict(meta, file=name)) + "\n")
            except OSError:
                pass
            finally:
                self.busy -= 1
        self.busy += 1
        threading.Thread(target=work, daemon=True).start()

    def tick(self, st, known_before):
        """Call every overlay tick with the game state; `known_before(area, room)` tells whether
        the overlay had seen the room before this visit."""
        if st is None or st["menu"] or self.busy > 2:
            return
        now = time.time()
        key = (st["world"], st["room"])
        meta = {"t": now, "area": st["world"], "room": st["room"], "x": st["x"], "z": st["z"]}
        try:
            if key != self.room:
                if self.room is not None and self.last is not None:
                    t, ram, m = self.last
                    self.n += 1
                    self._write(f"{self.n:04d}_out", ram, dict(m, kind="out", to=list(key)))
                self.room, self.entered, self.in_saved, self.last = key, now, False, None
                self.new_to_tracker = not known_before(*key)
                return
            if not self.in_saved and now - self.entered >= SETTLE:
                ram = self._grab()
                self._write(f"{self.n:04d}_in", ram,
                            dict(meta, kind="in", new_to_tracker=self.new_to_tracker))
                self.in_saved = True
                self.last, self.next_keep = (now, ram, meta), now + KEEP_EVERY
            elif self.in_saved and now >= self.next_keep:
                self.last, self.next_keep = (now, self._grab(), meta), now + KEEP_EVERY
        except OSError:
            pass  # PINE hiccup: the overlay's own reader reconnects

    def mark(self):
        """User says: the room just entered was not on the in-game map before."""
        with open(os.path.join(OUT, "log.jsonl"), "a") as f:
            f.write(json.dumps({"t": time.time(), "mark": "new", "n": self.n,
                                "area": self.room and self.room[0], "room": self.room and self.room[1]}) + "\n")
