"""(Re-)extract the game's area maps from your LoI disc image into data/gamemap/.

    python tools/extract_maps.py [path/to/Castlevania - Lament of Innocence.iso]

loi_tools.py does this automatically on first run; use this to redo it (e.g. after an update
of the map decoder) or to point at an ISO PCSX2's game folders don't contain.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loitools.minimap.gamemap import CACHE, extract_all, find_iso  # noqa: E402


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else find_iso()
    if not iso:
        sys.exit("ISO not found in PCSX2's game folders; pass its path as an argument")
    print("ISO:", iso)
    worlds = extract_all(iso)
    print(f"extracted {len(worlds)} area maps into {CACHE}")


if __name__ == "__main__":
    main()
