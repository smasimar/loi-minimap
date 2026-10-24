"""Hotkey snapshotter: press Pause/Break or Scroll Lock (works while PCSX2 has focus)
to save a RAM snapshot named s01, s02, ... into scan_state/snaps. Beeps on success.

  python snapper.py [--prefix s] [--start 1]
"""

import argparse
import ctypes
import os
import time
import winsound

from pine import Pine
import scanner

VK_PAUSE, VK_SCROLL = 0x13, 0x91
user32 = ctypes.windll.user32


def pressed(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="s")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--slot", type=int, default=28011)
    a = ap.parse_args()

    os.makedirs(scanner.SNAPS, exist_ok=True)
    n = a.start
    down = False
    print("ready: press Pause/Break or Scroll Lock to snapshot", flush=True)
    while True:
        now = pressed(VK_PAUSE) or pressed(VK_SCROLL)
        if now and not down:
            name = f"{a.prefix}{n:02d}"
            try:
                with Pine(a.slot) as p:
                    ram = p.dump_ee()
                ram.tofile(scanner.snap_path(name))
                m = scanner.load_meta()
                if name in m["snaps"]:
                    m["snaps"].remove(name)
                m["snaps"].append(name)
                scanner.save_meta(m)
                print(time.strftime("%H:%M:%S"), "saved", name, flush=True)
                winsound.Beep(1200, 120)
                n += 1
            except Exception as e:
                print("snapshot failed:", e, flush=True)
                winsound.Beep(300, 400)
        down = now
        time.sleep(0.02)


if __name__ == "__main__":
    main()
