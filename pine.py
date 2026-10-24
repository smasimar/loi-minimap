"""Minimal PINE client for PCSX2.

On Windows PCSX2 exposes PINE as a TCP socket on 127.0.0.1:<slot> (default 28011).
Enable it in PCSX2: Settings -> Advanced -> "Enable PINE".

Protocol (little endian):
  request : u32 total_size | (u8 opcode, args...)*
  reply   : u32 total_size | u8 result (0 = ok, 0xFF = fail) | data...
Multiple commands may be batched in a single request; their replies are concatenated.
"""

import socket
import struct

import numpy as np

MSG_READ8, MSG_READ16, MSG_READ32, MSG_READ64 = 0, 1, 2, 3
MSG_WRITE8, MSG_WRITE16, MSG_WRITE32, MSG_WRITE64 = 4, 5, 6, 7
MSG_VERSION, MSG_SAVESTATE, MSG_LOADSTATE = 8, 9, 10
MSG_TITLE, MSG_ID, MSG_UUID, MSG_GAMEVERSION, MSG_STATUS = 11, 12, 13, 14, 15

STATUS_NAMES = {0: "running", 1: "paused", 2: "shutdown"}

EE_RAM_SIZE = 32 * 1024 * 1024
# PCSX2 caps requests at 650000 bytes and replies at 450000 bytes.
_MAX_BATCH_BYTES = 400000


class PineError(Exception):
    pass


class Pine:
    def __init__(self, slot=28011, host="127.0.0.1", timeout=5.0):
        self.addr = (host, slot)
        self.timeout = timeout
        self.sock = None

    def connect(self):
        self.close()
        s = socket.create_connection(self.addr, timeout=self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = s
        return self

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # --- transport -------------------------------------------------------

    def _recv_exact(self, n):
        buf = bytearray(n)
        view = memoryview(buf)
        got = 0
        while got < n:
            k = self.sock.recv_into(view[got:])
            if k == 0:
                raise PineError("connection closed by PCSX2")
            got += k
        return bytes(buf)

    def _transact(self, body):
        if self.sock is None:
            self.connect()
        try:
            self.sock.sendall(struct.pack("<I", len(body) + 4) + body)
            size = struct.unpack("<I", self._recv_exact(4))[0]
            reply = self._recv_exact(size - 4)
        except (OSError, PineError):
            self.close()
            raise
        if reply[0] != 0:
            raise PineError("PCSX2 reported failure (is a game running?)")
        return reply[1:]

    # --- single commands -------------------------------------------------

    def read8(self, addr):
        return self._transact(struct.pack("<BI", MSG_READ8, addr))[0]

    def read16(self, addr):
        return struct.unpack("<H", self._transact(struct.pack("<BI", MSG_READ16, addr)))[0]

    def read32(self, addr):
        return struct.unpack("<I", self._transact(struct.pack("<BI", MSG_READ32, addr)))[0]

    def read64(self, addr):
        return struct.unpack("<Q", self._transact(struct.pack("<BI", MSG_READ64, addr)))[0]

    def read_float(self, addr):
        return struct.unpack("<f", struct.pack("<I", self.read32(addr)))[0]

    def write8(self, addr, v):
        self._transact(struct.pack("<BIB", MSG_WRITE8, addr, v & 0xFF))

    def write32(self, addr, v):
        self._transact(struct.pack("<BII", MSG_WRITE32, addr, v & 0xFFFFFFFF))

    def _string(self, op):
        data = self._transact(bytes([op]))
        n = struct.unpack("<I", data[:4])[0]
        return data[4:4 + n].split(b"\0", 1)[0].decode("utf-8", "replace")

    def version(self):
        return self._string(MSG_VERSION)

    def game_id(self):
        return self._string(MSG_ID)

    def title(self):
        return self._string(MSG_TITLE)

    def status(self):
        return struct.unpack("<I", self._transact(bytes([MSG_STATUS])))[0]

    # --- bulk reads ------------------------------------------------------

    def read_block(self, start, length):
        """Read `length` bytes from EE memory starting at `start` (both multiples of 8)."""
        assert start % 8 == 0 and length % 8 == 0
        per_batch = _MAX_BATCH_BYTES // 8  # reply is the larger side (8 bytes vs 5 request)
        out = bytearray()
        addr, end = start, start + length
        while addr < end:
            n = min(per_batch, (end - addr) // 8)
            req = np.empty(n, dtype=[("op", "u1"), ("addr", "<u4")])
            req["op"] = MSG_READ64
            req["addr"] = addr + 8 * np.arange(n, dtype=np.uint32)
            out += self._transact(req.tobytes())
            addr += 8 * n
        return bytes(out)

    def read_u64s(self, addrs):
        """Batched Read64 at each address in `addrs` (numpy array); returns uint64 array."""
        addrs = np.asarray(addrs, dtype=np.uint32)
        per_batch = _MAX_BATCH_BYTES // 8
        out = []
        for i in range(0, len(addrs), per_batch):
            chunk = addrs[i:i + per_batch]
            req = np.empty(len(chunk), dtype=[("op", "u1"), ("addr", "<u4")])
            req["op"] = MSG_READ64
            req["addr"] = chunk
            out.append(np.frombuffer(self._transact(req.tobytes()), dtype="<u8"))
        return np.concatenate(out) if out else np.empty(0, dtype="<u8")

    def dump_ee(self):
        """Full 32 MB EE RAM snapshot as a numpy uint8 array."""
        return np.frombuffer(self.read_block(0, EE_RAM_SIZE), dtype=np.uint8)

    def read_many(self, reads):
        """Batched reads. `reads` is a list of (width_bytes, addr); returns list of ints."""
        ops = {1: MSG_READ8, 2: MSG_READ16, 4: MSG_READ32, 8: MSG_READ64}
        fmts = {1: "B", 2: "H", 4: "I", 8: "Q"}
        body = b"".join(struct.pack("<BI", ops[w], a) for w, a in reads)
        data = self._transact(body)
        vals, off = [], 0
        for w, _ in reads:
            vals.append(struct.unpack_from("<" + fmts[w], data, off)[0])
            off += w
        return vals


if __name__ == "__main__":
    import time

    with Pine() as p:
        print("PCSX2  :", p.version())
        print("status :", STATUS_NAMES.get(p.status(), "?"))
        print("game   :", p.game_id(), "-", p.title())
        t = time.perf_counter()
        ram = p.dump_ee()
        print(f"dumped {len(ram) // 1024 // 1024} MB EE RAM in {time.perf_counter() - t:.2f}s")
