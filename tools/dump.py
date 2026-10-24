"""Dump live EE memory as hex + float columns.  python tools/dump.py <addr> [bytes]"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from loicam.pine import Pine


def main():
    addr = int(sys.argv[1], 16)
    size = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x200
    with Pine() as p:
        data = p.read_block(addr, size)
    for row in range(0, len(data), 16):
        words = struct.unpack_from("<4I", data, row)
        floats = struct.unpack_from("<4f", data, row)
        fs = " ".join(f"{f:12.4f}" if 1e-6 < abs(f) < 1e7 or f == 0 else f"{'.':>12}" for f in floats)
        print(f"{addr + row:08x}+{row:03x}: " + " ".join(f"{w:08x}" for w in words) + "  | " + fs)


if __name__ == "__main__":
    main()
