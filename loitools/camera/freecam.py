"""Free third-person camera: the right stick orbits the camera around Leon.

R3 toggles between the free camera and the original. Numpad hotkeys (while PCSX2 has focus)
tune it; changes are saved to the "camera" section of config.json. See README for how it works.
"""
import math
import threading
import time
from dataclasses import dataclass

from . import addrs, pad
from .hotkeys import HELP as HOTKEY_HELP, Hotkeys
from .orbit import Orbit, OrbitConfig
from .patches import GameplayCameraRedirect, RightStickMute
from .settings import Settings
from ..pine import Pine, PineError


@dataclass
class CameraOptions:
    """Per-run options (command line); the tunable settings live in Settings / config.json."""
    invert_x: bool = False
    invert_y: bool = False
    smoothing: float = OrbitConfig.smoothing
    hz: float = 240.0         # update rate of the camera loop
    start_off: bool = False   # start with the original camera (R3 turns the free camera on)
    verbose: bool = False
    demo_spin: float = 0.0    # testing: pretend the right stick is held at this x (-1..1)


TELEPORT_DIST = 250.0   # Leon moves ~15 units per frame at most; a bigger jump is a room change
SETTLE_TIME = 0.15      # max seconds to keep snapping behind Leon after a room change, while
SETTLE_MOVE = 5.0       # his facing catches up; stops as soon as he moves this far from landing


def follow_point(leon_pos):
    return (leon_pos[0], leon_pos[1] - addrs.FOLLOW_HEIGHT, leon_pos[2])


class FreeCam:
    def __init__(self, p: Pine, orbit: Orbit, pad_addrs, settings: Settings):
        self.p = p
        self.settings = settings
        self.orbit = orbit
        self.pad_addrs = pad_addrs
        self.active = False

    def engage(self, mode, facing, copy=True):
        """Clone the gameplay camera into scratch RAM and point the renderer at the clone.

        copy=False when the game already handed the pointer back to the clone (see
        GameplayCameraRedirect); the clone still holds the free camera's last state.

        mode: "align"  - start where the game's camera is (no snap; used when toggling on)
              "resume" - keep the orbit angles from before (back from the map screen)
              "behind" - swing round behind Leon (after cutscenes / other camera handovers)
        """
        if copy:
            # The rest of the struct is cleared: game code that ran while CAM_PTR pointed here
            # (cutscenes) may have left a camera mode behind, and that survives save loads.
            cam = self.p.read_block(addrs.GAMEPLAY_CAM, addrs.CAM_SIZE)
            self.p.write_block(addrs.SCRATCH_CAM, cam + bytes(addrs.CAM_STRIDE - addrs.CAM_SIZE))
        if mode == "align":
            eye = self.p.read_floats(addrs.GAMEPLAY_CAM + addrs.CAM_EYE, 3)
            self.orbit.align_to(eye, follow_point(self.p.read_floats(addrs.LEON_POS, 3)))
        elif mode == "behind":
            self.orbit.place_behind(facing)
        else:
            self.orbit.pivot = None
        if copy:
            self.p.write32(addrs.CAM_PTR, addrs.SCRATCH_CAM)
        self.active = True

    def release(self):
        if self.p.read32(addrs.CAM_PTR) == addrs.SCRATCH_CAM:
            self.p.write32(addrs.CAM_PTR, addrs.GAMEPLAY_CAM)
        self.active = False

    def poll(self):
        b = self.p.batch()
        i_ptr = b.read_u32(addrs.CAM_PTR)
        i_leon = b.read_floats(addrs.LEON_POS, 3)
        i_rot = b.read_floats(addrs.LEON_ROT, 3)
        i_map = b.read_u32(addrs.MAP_OPEN)
        i_follow = {c: b.read_u32(c + addrs.CAM_FOLLOWING) for c in (addrs.GAMEPLAY_CAM, addrs.SCRATCH_CAM)}
        i_pads = [b.read_bytes(a, 8) for a in self.pad_addrs]
        r = b.run()
        # All buffers are copies of the same input at different stages; take the most deflected.
        states = [pad.parse(r[i]) for i in i_pads]
        st = max(states, key=lambda s: abs(s.rx) + abs(s.ry)) if states else pad.PadState()
        held1 = held2 = 0
        for s in states:
            held1 |= s.buttons1
            held2 |= s.buttons2
        st.buttons1, st.buttons2 = held1, held2
        ptr = r[i_ptr]
        # a cutscene drives the active camera itself (see addrs.CAM_FOLLOWING)
        cutscene = ptr in i_follow and (r[i_follow[ptr]] & 0xFF) == 0
        return ptr, r[i_leon], r[i_rot][1], st, r[i_map] != 0, cutscene

    def write(self, eye, look):
        b = self.p.batch()
        b.write_floats(addrs.SCRATCH_CAM + addrs.CAM_EYE, eye)
        b.write_floats(addrs.SCRATCH_CAM + addrs.CAM_FOLLOW, look)
        b.write_floats(addrs.SCRATCH_CAM + addrs.CAM_LOOK_A, look)
        b.write_floats(addrs.SCRATCH_CAM + addrs.CAM_LOOK_B, look)
        b.write_u32(addrs.SCRATCH_CAM + addrs.CAM_FOCAL, self.settings.focal)
        b.write_u32(addrs.SCRATCH_CAM + addrs.CAM_FOCAL_TARGET, self.settings.focal)
        b.write_u32(addrs.SCRATCH_CAM + addrs.CAM_MODE, 0)  # see addrs.CAM_MODE
        b.run()


def apply_settings(s: Settings, cfg: OrbitConfig):
    cfg.sens, cfg.distance, cfg.lift = s.sens, s.distance, s.lift


class App:
    """Everything the tool does, one tick at a time (so test scenarios can drive it too)."""

    def __init__(self, p: Pine, args: CameraOptions, settings: Settings, cfg: OrbitConfig, save, hotkeys=True):
        """`save`: called after a hotkey changes `settings`."""
        self.p, self.args, self.settings, self.cfg, self.save = p, args, settings, cfg, save
        gid = p.game_id()
        print(f"{p.version()} | {p.title()} [{gid}] crc {p.crc()} | {p.status()}")
        if gid != addrs.GAME_ID or p.crc().lower() != addrs.GAME_CRC:
            raise SystemExit(f"expected {addrs.GAME_ID} / {addrs.GAME_CRC}; this build has no addresses for that game")

        ram = p.read_block(0, 32 * 1024 * 1024)
        pad_addrs = pad.find_buffers(ram)
        if not pad_addrs:
            raise SystemExit("no libpad buffer found; is the controller in analog mode?")
        print("pad buffers:", ", ".join(f"{a:#x}" for a in pad_addrs))

        # Outside the map screen the right stick belongs to the camera for as long as the tool
        # runs, in either camera mode, so it can never open the item/equipment quick menu.
        self.mute = RightStickMute(p)
        self.mute.check()
        print("right stick hidden from the game" if self.mute.enable() else "WARNING: right stick patch did not stick")

        self.cam = FreeCam(p, Orbit(cfg), pad_addrs, settings)
        self.hotkeys = Hotkeys() if hotkeys else None
        print(settings.describe())
        print("hotkeys:", HOTKEY_HELP)

        # While the free camera is wanted, the game's own "back to gameplay camera" call selects
        # the free camera directly, so nothing is ever rendered from the gameplay camera.
        self.redirect = GameplayCameraRedirect(p)
        self.redirect.check()

        self.want_on = not args.start_off
        print("free camera ON (R3 toggles)" if self.want_on else "free camera OFF (R3 toggles)")
        if self.want_on:
            self.redirect.enable()
        self.r3_was = False
        self.rejoin = "align"     # how to pick the orbit up when the gameplay camera comes back
        self.after_cutscene = False  # the scratch camera was driven by a cutscene: copy it afresh
        self.prev_leon = None
        self.settle_until = 0.0
        self.settle_from = None
        self.last = None
        self.last_log = 0.0

    def tick(self, now):
        cam, args = self.cam, self.args
        dt = 0.0 if self.last is None else min(now - self.last, 0.1)
        self.last = now

        ptr, leon, facing, st, map_open, cutscene = cam.poll()
        follow = follow_point(leon)

        # The map screen (scrolling) and the realtime Orb / Relic menus (held L1 / L2) need the
        # right stick; everywhere else it is hidden from the game so it can't open the
        # item/equipment quick menu. While a realtime menu is up the camera leaves the stick alone.
        menu_held = st.held2(pad.L1) or st.held2(pad.L2)
        game_stick = map_open or menu_held
        if game_stick and self.mute.enabled:
            self.mute.disable()
        elif not game_stick and not self.mute.enabled:
            self.mute.enable()
        if menu_held:
            st.rx = st.ry = 0.0
        if args.demo_spin:
            st.rx = args.demo_spin

        actions = self.hotkeys.poll() if self.hotkeys else []
        if actions:
            for name, step in actions:
                self.settings.adjust(name, step)
            apply_settings(self.settings, self.cfg)
            self.save()
            print(self.settings.describe())

        r3 = st.held1(pad.R3)
        if r3 and not self.r3_was:
            self.want_on = not self.want_on
            self.rejoin = "align"
            if self.want_on:
                self.redirect.enable()
            else:
                self.redirect.disable()
            print("free camera", "ON" if self.want_on else "OFF")
        self.r3_was = r3

        if self.want_on and not cam.active and map_open:
            self.rejoin = "resume"   # the map borrowed the camera; come back where we were

        # Some cutscenes don't switch cameras but take over the active one (the free camera's).
        # Leave it to them, pointer and all, and start afresh once Leon is followed again.
        if cam.active and cutscene:
            cam.active = False
            self.rejoin, self.after_cutscene = "behind", True
            if args.verbose:
                print("cutscene; yielding")

        if self.want_on and not cam.active and not cutscene and ptr in (addrs.GAMEPLAY_CAM, addrs.SCRATCH_CAM):
            copy = ptr == addrs.GAMEPLAY_CAM or self.after_cutscene
            cam.engage(self.rejoin, facing, copy=copy)
            self.after_cutscene = False
            if self.rejoin == "behind":
                self.start_settle(now, leon, facing)
            if args.verbose:
                print(f"engaged ({self.rejoin}, {'copied' if copy else 'handed back'})")
            ptr = addrs.SCRATCH_CAM
        elif cam.active and (not self.want_on or ptr != addrs.SCRATCH_CAM):
            # Off, or the game switched to another camera (map, cutscene, event): step aside
            # and re-engage once the gameplay camera is back.
            if ptr != addrs.SCRATCH_CAM:
                self.rejoin = "resume" if map_open else "behind"
                if args.verbose:
                    print(f"game switched camera to {ptr:#x}; yielding")
            cam.release()

        # A room change (door, stairs, warp) teleports Leon: swing the camera behind him. Re-apply
        # only until his facing has caught up: a few frames at most, and never once he walks
        # off or the right stick is used.
        if cam.active and self.prev_leon is not None and math.dist(leon, self.prev_leon) > TELEPORT_DIST:
            self.start_settle(now, leon, facing)
            if args.verbose:
                print("room change; placing camera behind Leon")
        self.prev_leon = leon
        if self.settle_from is not None:
            if (not cam.active or now >= self.settle_until or cam.orbit.stick_active(st.rx, st.ry)
                    or math.dist(leon, self.settle_from) > SETTLE_MOVE):
                self.settle_from = None
            else:
                cam.orbit.place_behind(facing)

        if cam.active:
            eye, look = cam.orbit.update(dt, st.rx, st.ry, follow)
            cam.write(eye, look)
            if args.verbose and now - self.last_log > 0.5:
                self.last_log = now
                print(f"yaw {math.degrees(cam.orbit.yaw):7.1f} pitch {math.degrees(cam.orbit.pitch):5.1f} "
                      f"leon facing {math.degrees(facing):7.1f} rs ({st.rx:+.2f},{st.ry:+.2f}) "
                      f"follow ({follow[0]:.0f},{follow[1]:.0f},{follow[2]:.0f})")

    def start_settle(self, now, leon, facing):
        self.cam.orbit.place_behind(facing)
        self.settle_until = now + SETTLE_TIME
        self.settle_from = tuple(leon)

    def shutdown(self):
        self.redirect.disable()
        if self.p.read32(addrs.CAM_PTR) == addrs.SCRATCH_CAM:
            self.p.write32(addrs.CAM_PTR, addrs.GAMEPLAY_CAM)
        self.cam.release()
        self.mute.disable()
        print("restored original camera and right stick")


class CameraThread(threading.Thread):
    """Runs the camera in the background on the shared PINE connection.

    Waits until the game is up and in gameplay (the pad buffers have to exist), starts over after
    a lost connection, and always puts the original camera and right stick back when stopped.
    """

    def __init__(self, pine: Pine, config, options: CameraOptions):
        super().__init__(name="camera", daemon=True)
        self.pine, self.config, self.options = pine, config, options
        self.settings = Settings.from_dict(config.camera)
        self.orbit_cfg = OrbitConfig(invert_x=options.invert_x, invert_y=options.invert_y,
                                     smoothing=options.smoothing)
        apply_settings(self.settings, self.orbit_cfg)
        self._stop = threading.Event()
        self._last_msg = None

    def save(self):
        self.config.camera.update(self.settings.to_dict())
        self.config.save()

    def stop(self, timeout=3.0):
        self._stop.set()
        self.join(timeout)

    def _say(self, msg):
        if msg != self._last_msg:  # each kind of wait/failure once, not every retry
            print(f"camera: {msg}", flush=True)
            self._last_msg = msg

    def run(self):
        app = None
        period = 1.0 / self.options.hz
        while not self._stop.is_set():
            now = time.perf_counter()
            try:
                if app is None:
                    app = App(self.pine, self.options, self.settings, self.orbit_cfg, self.save)
                    self._last_msg = None
                app.tick(now)
            except SystemExit as e:   # wrong game, or not in gameplay yet (no pad buffers)
                self._say(f"{e}; retrying")
                app = None
                self._stop.wait(3.0)
                continue
            except (OSError, PineError, RuntimeError) as e:
                self._say(f"waiting for PCSX2 ({e})")
                if app is not None:
                    try:
                        app.shutdown()
                    except (OSError, PineError):
                        pass
                    app = None
                self._stop.wait(2.0)
                continue
            sleep = period - (time.perf_counter() - now)
            if sleep > 0:
                self._stop.wait(sleep)
        if app is not None:
            try:
                app.shutdown()
            except (OSError, PineError):
                print("camera: could not restore the original camera (PCSX2 not reachable)")
