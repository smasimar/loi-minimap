"""DualShock 2 state as the game sees it, read from libpad's output buffers in EE RAM.

libpad's padRead() output: [0]=0x00, [1]=mode (0x73 analog, 0x79 DS2 pressure),
[2..3]=buttons (active low), [4]=RX, [5]=RY, [6]=LX, [7]=LY (0..255, 128 = centre).
"""
import re
from dataclasses import dataclass

# Byte 2 / byte 3 masks (active low)
SELECT, L3, R3, START, UP, RIGHT, DOWN, LEFT = (1 << i for i in range(8))
L2, R2, L1, R1, TRIANGLE, CIRCLE, CROSS, SQUARE = (1 << i for i in range(8))

_SIG = re.compile(rb"\x00[\x73\x79][\x00-\xff]{2}[\x40-\xc0]{4}")


@dataclass
class PadState:
    buttons1: int = 0  # pressed bits (already inverted)
    buttons2: int = 0
    rx: float = 0.0    # -1..1, right = +
    ry: float = 0.0    # -1..1, down = +
    lx: float = 0.0
    ly: float = 0.0

    def held1(self, mask):
        return bool(self.buttons1 & mask)

    def held2(self, mask):
        return bool(self.buttons2 & mask)


def find_buffers(ram):
    """Return addresses of the padRead() buffers in a RAM snapshot.

    The signature alone also matches random data, so only buffers followed by libpad's
    zero padding are kept, and of those the largest group with identical contents wins
    (the game keeps several copies of the same frame's input).
    """
    groups = {}
    for m in _SIG.finditer(ram):
        a = m.start()
        if a % 4 or ram[a + 8:a + 20] != bytes(12):
            continue
        groups.setdefault(ram[a:a + 8], []).append(a)
    return max(groups.values(), key=len) if groups else []


def _axis(b):
    return max(-1.0, min(1.0, (b - 128) / 127.0))


def parse(raw):
    return PadState(
        buttons1=~raw[2] & 0xFF,
        buttons2=~raw[3] & 0xFF,
        rx=_axis(raw[4]), ry=_axis(raw[5]),
        lx=_axis(raw[6]), ly=_axis(raw[7]),
    )
