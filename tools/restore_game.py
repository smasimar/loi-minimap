"""Put the game's original camera and controls back, if loi_tools.py was killed without its
normal Ctrl+C clean-up (for example by closing the console window or a crash).

    python tools/restore_game.py [--slot 28011]

Restores the code patches (right stick / R3, camera hand-back) and the camera pointer.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loitools.camera import addrs, patches  # noqa: E402
from loitools.pine import Pine  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", type=int, default=28011)
    a = ap.parse_args()
    sites = patches.RightStickMute.SITES + patches.GameplayCameraRedirect.SITES
    try:
        with Pine(slot=a.slot) as p:
            if p.game_id() != addrs.GAME_ID or p.crc().lower() != addrs.GAME_CRC:
                sys.exit("this isn't the supported LoI build; nothing changed")
            for addr, orig, patched in sites:
                if p.read32(addr) == patched:
                    p.write32(addr, orig)
            if p.read32(addrs.CAM_PTR) == addrs.SCRATCH_CAM:
                p.write32(addrs.CAM_PTR, addrs.GAMEPLAY_CAM)
            ok = all(p.read32(addr) == orig for addr, orig, _ in sites)
            print("original camera and controls restored" if ok else "some code didn't match what was expected")
    except OSError as e:
        sys.exit(f"cannot reach PCSX2 on PINE slot {a.slot}: {e} (close loi_tools.py first)")


if __name__ == "__main__":
    main()
