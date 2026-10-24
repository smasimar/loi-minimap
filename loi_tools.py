"""Minimap overlay and free third-person camera for Castlevania: Lament of Innocence on PCSX2.

Needs PCSX2 with PINE enabled (Settings > Advanced, slot 28011) and the game running in
windowed or borderless mode. Both tools share one PINE connection (PCSX2 serves one client).

Minimap hotkeys (hold Ctrl+Shift): M show/hide, Z zoom, +/- size, C corner, O opacity.
Camera: right stick orbits, R3 toggles free/original camera, numpad tunes it (see README).
Settings are saved to config.json. Ctrl+C here quits and puts the original camera back.
"""

import argparse
import signal
import sys
import time

from loitools.camera.freecam import CameraOptions, CameraThread
from loitools.config import Config
from loitools.pine import Pine


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-minimap", action="store_true", help="run the camera only")
    ap.add_argument("--no-camera", action="store_true", help="run the minimap only")
    ap.add_argument("--slot", type=int, help="PINE slot (default 28011, or pine_slot in config.json)")
    cam = ap.add_argument_group("camera (these four also update config.json)")
    cam.add_argument("--distance", type=float)
    cam.add_argument("--lift", type=float, help="extra eye height (camera angle)")
    cam.add_argument("--focal", type=int, help="focal length in pixels; lower = wider FoV (game default ~835)")
    cam.add_argument("--sens", type=float, help="stick sensitivity multiplier")
    run = ap.add_argument_group("camera, this run only")
    run.add_argument("--invert-x", action="store_true")
    run.add_argument("--invert-y", action="store_true")
    run.add_argument("--smoothing", type=float, default=CameraOptions.smoothing)
    run.add_argument("--hz", type=float, default=CameraOptions.hz, help="camera update rate")
    run.add_argument("--start-off", action="store_true", help="start with the original camera")
    run.add_argument("--verbose", action="store_true")
    run.add_argument("--demo-spin", type=float, default=0.0, help=argparse.SUPPRESS)
    return ap.parse_args()


def main():
    args = parse_args()
    config = Config.load()
    overrides = {k: getattr(args, k) for k in ("distance", "lift", "focal", "sens") if getattr(args, k) is not None}
    if overrides:
        config.camera.update(overrides)
        config.save()
    pine = Pine(slot=args.slot or config.pine_slot)

    camera = None
    if not args.no_camera:
        camera = CameraThread(pine, config, CameraOptions(
            invert_x=args.invert_x, invert_y=args.invert_y, smoothing=args.smoothing, hz=args.hz,
            start_off=args.start_off, verbose=args.verbose, demo_spin=args.demo_spin))
        camera.start()

    try:
        if args.no_minimap:
            print("LoI tools running (camera only). Ctrl+C quits.", flush=True)
            while camera and camera.is_alive():
                time.sleep(0.2)
        else:
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            from loitools.minimap.overlay import Overlay

            app = QApplication(sys.argv)
            overlay = Overlay(config, pine)  # keep a reference: an unreferenced widget is garbage-collected
            signal.signal(signal.SIGINT, lambda *_: app.quit())
            heartbeat = QTimer(timeout=lambda: None)  # lets Python notice Ctrl+C while Qt runs
            heartbeat.start(200)
            print("LoI tools running. Ctrl+Shift+M toggles the minimap, Ctrl+C quits.", flush=True)
            app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        if camera:
            camera.stop()  # puts the original camera and right stick back
        pine.close()


if __name__ == "__main__":
    main()
