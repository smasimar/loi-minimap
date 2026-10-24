"""Inject a button press into the game for testing, by briefly overriding the held-buttons load.

  python tools/press.py <button> [seconds]      buttons: select start up down left right
                                                 triangle circle cross square l1 l2 r1 r2
The game's input layer (0x416560) feeds FillPlayerInput the held word from the port struct
(active high, byte2<<8 | byte3 of the libpad buffer). The load at 0x4165C0 is swapped for a
constant for the given time, then restored.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from loicam.pine import Pine

SITE = 0x4165C0
ORIG = 0x96060000  # lhu a2, 0x0(s0)
BITS = {
    "select": 0x0100, "l3": 0x0200, "r3": 0x0400, "start": 0x0800,
    "up": 0x1000, "right": 0x2000, "down": 0x4000, "left": 0x8000,
    "l2": 0x0001, "r2": 0x0002, "l1": 0x0004, "r1": 0x0008,
    "triangle": 0x0010, "circle": 0x0020, "cross": 0x0040, "square": 0x0080,
}


def press(p, button, seconds=0.15):
    assert p.read32(SITE) == ORIG, hex(p.read32(SITE))
    p.write32(SITE, 0x24060000 | BITS[button])  # addiu a2, zero, <bits>
    try:
        time.sleep(seconds)
    finally:
        p.write32(SITE, ORIG)


if __name__ == "__main__":
    with Pine() as p:
        press(p, sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.15)
