"""Check the PCSX2 connection: prints the emulator, game and whether LoI tools support it.

    python tools/pine_info.py [--slot 28011]

Close loi_tools.py first: PCSX2 serves one PINE client at a time.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loitools.camera import addrs  # noqa: E402
from loitools.pine import Pine  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", type=int, default=28011)
    a = ap.parse_args()
    try:
        with Pine(slot=a.slot) as p:
            gid, crc = p.game_id(), p.crc()
            print(f"PCSX2  : {p.version()} ({p.status()})")
            print(f"game   : {gid} - {p.title()} (CRC {crc})")
    except OSError as e:
        sys.exit(f"cannot reach PCSX2 on PINE slot {a.slot}: {e}\n"
                 "Enable PINE in PCSX2 (Settings > Advanced) and close other PINE clients.")
    ok = gid == addrs.GAME_ID and crc.lower() == addrs.GAME_CRC
    print("supported" if ok else f"not supported: LoI tools expect {addrs.GAME_ID} with CRC {addrs.GAME_CRC.upper()}")


if __name__ == "__main__":
    main()
