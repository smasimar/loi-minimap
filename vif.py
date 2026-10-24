"""Tiny PS2 VIF packet walker: finds UNPACK commands and decodes their payloads."""

import struct

import numpy as np

# words of payload following these VIFcodes (besides UNPACK/MPG/DIRECT)
_EXTRA = {0x20: 1, 0x30: 4, 0x31: 4}
_KNOWN = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x10, 0x11, 0x13, 0x14, 0x15, 0x17, 0x20, 0x30, 0x31}


def unpack_size(cmd, num):
    vn, vl = (cmd >> 2) & 3, cmd & 3
    bits = (32, 16, 8, 5)[vl]
    n = num or 256
    if vl == 3:  # V4-5: 16 bits per vector
        return (n * 2 + 3) // 4, (vn, vl, n)
    return ((vn + 1) * bits * n + 31) // 32, (vn, vl, n)


def decode(cmd, payload, n):
    vn, vl = (cmd >> 2) & 3, cmd & 3
    unsigned = bool(cmd & 0x20000000)
    comps = vn + 1
    if vl == 0:
        a = np.frombuffer(payload, dtype="<i4")
    elif vl == 1:
        a = np.frombuffer(payload, dtype="<u2" if unsigned else "<i2")
    elif vl == 2:
        a = np.frombuffer(payload, dtype="u1" if unsigned else "i1")
    else:
        return None
    n = min(n, len(a) // comps)  # truncated payload: keep the complete vectors
    if n == 0:
        return None
    return a[: comps * n].reshape(n, comps)


def walk(data, start=0, end=None, strict=False):
    """Yield (offset, cmd, num, imm, decoded_or_None) for each VIFcode."""
    end = len(data) if end is None else min(end, len(data))
    i = start
    while i + 4 <= end:
        code = struct.unpack_from("<I", data, i)[0]
        cmd, num, imm = code >> 24, (code >> 16) & 0xFF, code & 0xFFFF
        c = cmd & 0x7F
        i += 4
        if c >= 0x60:
            words, (vn, vl, n) = unpack_size(c, num)
            payload = data[i:i + words * 4]
            yield i - 4, c, num, imm, decode(c, payload, n)
            i += words * 4
        elif c == 0x4A:  # MPG
            i += (num or 256) * 8
            yield i, c, num, imm, None
        elif c in (0x50, 0x51):  # DIRECT / DIRECTHL
            i += (imm or 65536) * 16
            yield i, c, num, imm, None
        elif c in _KNOWN:
            i += _EXTRA.get(c, 0) * 4
            yield i - 4, c, num, imm, None
        else:
            if strict:
                return
            yield i - 4, None, num, imm, None
