"""Free third-person camera for Castlevania: Lament of Innocence (NTSC-U) over PCSX2 PINE.

Run while the game is in PCSX2 (Settings > Advanced > Enable PINE, slot 28011).
Right stick orbits, R3 toggles between free and original camera, Ctrl+C quits and restores.
Numpad hotkeys (while PCSX2 has focus) tune the camera; changes are saved to loi_cam.json.
"""
import argparse
import math
import time

from loicam import addrs, pad
from loicam.hotkeys import HELP as HOTKEY_HELP, Hotkeys
from loicam.orbit import Orbit, OrbitConfig
from loicam.patches import GameplayCameraRedirect, RightStickMute
from loicam.pine import Pine
from loicam.settings import Settings


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", type=int, default=28011)
    # These override (and get saved over) loi_cam.json
    ap.add_argument("--distance", type=float)
    ap.add_argument("--lift", type=float, help="extra eye height (camera angle)")
    ap.add_argument("--focal", type=int, help="focal length in pixels; lower = wider FoV (game default ~835)")
    ap.add_argument("--sens", type=float, help="stick sensitivity multiplier")
    ap.add_argument("--invert-x", action="store_true")
    ap.add_argument("--invert-y", action="store_true")
    ap.add_argument("--smoothing", type=float, default=OrbitConfig.smoothing)
    ap.add_argument("--hz", type=float, default=240.0, help="update rate of this tool")
    ap.add_argument("--start-off", action="store_true", help="start with the original camera")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--duration", type=float, default=0, help="quit after N seconds (testing)")
    ap.add_argument("--demo-spin", type=float, default=0, metavar="RX",
                    help="pretend the right stick is held at RX (-1..1), for testing hands-free")
    return ap.parse_args()


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
            self.p.write_block(addrs.SCRATCH_CAM, self.p.read_block(addrs.GAMEPLAY_CAM, addrs.CAM_SIZE))
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
        return r[i_ptr], r[i_leon], r[i_rot][1], st, r[i_map] != 0

    def write(self, eye, look):
        b = self.p.batch()
        b.write_floats(addrs.SCRATCH_CAM + addrs.CAM_EYE, eye)
        b.write_floats(addrs.SCRATCH_CAM + addrs.CAM_FOLLOW, look)
        b.write_floats(addrs.SCRATCH_CAM + addrs.CAM_LOOK_A, look)
        b.write_floats(addrs.SCRATCH_CAM + addrs.CAM_LOOK_B, look)
        b.write_u32(addrs.SCRATCH_CAM + addrs.CAM_FOCAL, self.settings.focal)
        b.write_u32(addrs.SCRATCH_CAM + addrs.CAM_FOCAL_TARGET, self.settings.focal)
        b.run()


def apply_settings(s: Settings, cfg: OrbitConfig):
    cfg.sens, cfg.distance, cfg.lift = s.sens, s.distance, s.lift


def main():
    args = parse_args()
    settings = Settings.load()
    for name in ("sens", "distance", "lift", "focal"):
        if getattr(args, name) is not None:
            setattr(settings, name, getattr(args, name))
    cfg = OrbitConfig(invert_x=args.invert_x, invert_y=args.invert_y, smoothing=args.smoothing)
    apply_settings(settings, cfg)

    with Pine(slot=args.slot, timeout=5) as p:
        app = App(p, args, settings, cfg)
        period = 1.0 / args.hz
        stop_at = time.perf_counter() + args.duration if args.duration > 0 else math.inf
        try:
            while True:
                now = time.perf_counter()
                if now >= stop_at:
                    break
                app.tick(now)
                sleep = period - (time.perf_counter() - now)
                if sleep > 0:
                    time.sleep(sleep)
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown()


class App:
    """Everything the tool does, one tick at a time (so test scenarios can drive it too)."""

    def __init__(self, p: Pine, args, settings: Settings, cfg: OrbitConfig, hotkeys=True):
        self.p, self.args, self.settings, self.cfg = p, args, settings, cfg
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
        self.prev_leon = None
        self.settle_until = 0.0
        self.settle_from = None
        self.last = None
        self.last_log = 0.0

    def tick(self, now):
        cam, args = self.cam, self.args
        dt = 0.0 if self.last is None else min(now - self.last, 0.1)
        self.last = now

        ptr, leon, facing, st, map_open = cam.poll()
        follow = follow_point(leon)

        # The map screen needs the right stick (scrolling); everywhere else it is hidden from
        # the game so it can't open the item/equipment quick menu.
        if map_open and self.mute.enabled:
            self.mute.disable()
        elif not map_open and not self.mute.enabled:
            self.mute.enable()
        if args.demo_spin:
            st.rx = args.demo_spin

        actions = self.hotkeys.poll() if self.hotkeys else []
        if actions:
            for name, step in actions:
                self.settings.adjust(name, step)
            apply_settings(self.settings, self.cfg)
            self.settings.save()
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

        if self.want_on and not cam.active and ptr in (addrs.GAMEPLAY_CAM, addrs.SCRATCH_CAM):
            cam.engage(self.rejoin, facing, copy=ptr == addrs.GAMEPLAY_CAM)
            if self.rejoin == "behind":
                self.start_settle(now, leon, facing)
            if args.verbose:
                print(f"engaged ({self.rejoin}, {'copied' if ptr == addrs.GAMEPLAY_CAM else 'handed back'})")
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


if __name__ == "__main__":
    main()
