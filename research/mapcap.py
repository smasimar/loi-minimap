"""Capture the in-game map screen each time it is opened (research / calibration helper).

Saves captures/NNN.png + NNN.json with the player world position at that moment.
"""

import json
import os
import time
import winsound

from PIL import Image

import capture
from pine import Pine

MENU_FLAG = 0x734E12
PLAYER_POS = 0x792A30
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")


def read_pos(p):
    import struct
    v = p.read_many([(4, PLAYER_POS), (4, PLAYER_POS + 4), (4, PLAYER_POS + 8)])
    return [struct.unpack("<f", struct.pack("<I", x))[0] for x in v]


def main():
    os.makedirs(OUT, exist_ok=True)
    n = len([f for f in os.listdir(OUT) if f.endswith(".png")])
    prev = 0
    with Pine() as p:
        print("watching for map screen...", flush=True)
        while True:
            flag = p.read8(MENU_FLAG)
            if flag and not prev:
                pos = read_pos(p)
                time.sleep(0.7)  # let the map fade in
                _, rect = capture.find_game_window()
                img, content = capture.grab(rect)
                if capture.looks_like_map(img):
                    n += 1
                    Image.fromarray(img).save(os.path.join(OUT, f"{n:03d}.png"))
                    with open(os.path.join(OUT, f"{n:03d}.json"), "w") as f:
                        json.dump({"pos": pos, "content_rect": content,
                                   "marker": capture.marker_pos(img), "t": time.time()}, f)
                    print(f"captured {n:03d} pos={[round(x, 1) for x in pos]} marker={capture.marker_pos(img)}", flush=True)
                    winsound.Beep(1200, 100)
                else:
                    print("menu opened, not the map", flush=True)
            prev = flag
            time.sleep(0.1)


if __name__ == "__main__":
    main()
