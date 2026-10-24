"""Drive loi_cam's App through scripted events on one PINE connection (PINE allows one client).

  python tools/scenario.py map_and_door
"""
import math
import os
import sys
import time

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import loi_cam
from loicam import addrs
from loicam.orbit import OrbitConfig
from loicam.pine import Pine
from loicam.settings import Settings
from press import press


def deg(r):
    return (math.degrees(r) + 180) % 360 - 180


def run(app, seconds, hz=240):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.tick(time.perf_counter())
        time.sleep(1 / hz)


def map_and_door(p, app):
    orbit = app.cam.orbit
    run(app, 0.5)
    facing = p.read_floats(addrs.LEON_ROT, 3)[1]
    orbit.yaw = facing + math.pi + 1.2          # pretend the player swung the camera ~70 deg off
    run(app, 0.3)
    before = orbit.yaw
    print(f"set yaw {deg(before):.1f} (behind Leon would be {deg(facing + math.pi):.1f})")

    press(p, "select"); run(app, 1.5)
    print(f"map open: flag {p.read32(addrs.MAP_OPEN)} ptr {p.read32(addrs.CAM_PTR):#x} active {app.cam.active}")
    press(p, "select")
    # Sample the pointer as fast as PINE allows while the map closes; the gameplay camera must
    # never be selected (that's the one-frame flash).
    seen = set()
    end = time.perf_counter() + 1.5
    while time.perf_counter() < end:
        seen.add(p.read32(addrs.CAM_PTR))
        app.tick(time.perf_counter())
    print(f"map closed: ptr {p.read32(addrs.CAM_PTR):#x} active {app.cam.active} "
          f"yaw {deg(orbit.yaw):.1f} -> {'KEPT' if abs(deg(orbit.yaw - before)) < 1 else 'CHANGED'}; "
          f"pointers seen {sorted(hex(s) for s in seen)} -> "
          f"{'FLASH' if addrs.GAMEPLAY_CAM in seen else 'NO FLASH'}")

    pos = p.read_floats(addrs.LEON_POS, 3)
    moved = [pos[0] + 600, pos[1], pos[2]]
    for off in (0, 0x10, 0x20):
        p.write_floats(addrs.LEON_POS + off, moved)
    run(app, 0.8)
    facing = p.read_floats(addrs.LEON_ROT, 3)[1]
    print(f"after teleport: yaw {deg(orbit.yaw):.1f}, behind Leon {deg(facing + math.pi):.1f} -> "
          f"{'BEHIND' if abs(deg(orbit.yaw - facing - math.pi)) < 2 else 'NOT BEHIND'}")

    # Walk out of the "door": the snap must let go as soon as Leon moves, so the player can steer.
    for off in (0, 0x10, 0x20):
        p.write_floats(addrs.LEON_POS + off, pos)
    t0 = time.perf_counter()
    app.tick(t0)                                  # second teleport (back) starts a new settle
    released_after = None
    for step in range(1, 40):
        walk = [pos[0] + 3 * step, pos[1], pos[2]]
        for off in (0, 0x10, 0x20):
            p.write_floats(addrs.LEON_POS + off, walk)
        app.tick(time.perf_counter())
        if app.settle_from is None:
            released_after = (step, time.perf_counter() - t0)
            break
    print(f"walking off after teleport: snap released after "
          f"{released_after[0]} ticks / {released_after[1] * 1000:.0f} ms" if released_after
          else "walking off after teleport: snap NOT released")
    for off in (0, 0x10, 0x20):
        p.write_floats(addrs.LEON_POS + off, pos)
    run(app, 0.3)


SCENARIOS = {"map_and_door": map_and_door}

if __name__ == "__main__":
    name = sys.argv[1]
    sys.argv = [sys.argv[0], "--verbose"]
    args = loi_cam.parse_args()
    settings = Settings.load()
    cfg = OrbitConfig(smoothing=args.smoothing)
    loi_cam.apply_settings(settings, cfg)
    with Pine() as p:
        app = loi_cam.App(p, args, settings, cfg, hotkeys=False)
        try:
            SCENARIOS[name](p, app)
        finally:
            app.shutdown()
