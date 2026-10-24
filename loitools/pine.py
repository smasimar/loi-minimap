"""PINE client for PCSX2, shared by the minimap and the camera.

On Windows PCSX2 exposes PINE as a TCP socket on 127.0.0.1:<slot> (default 28011).
Enable it in PCSX2: Settings -> Advanced -> "Enable PINE". PCSX2 serves one client at a
time, so both tools go through one Pine object; every message is sent under a lock, which
makes it safe to use from several threads.

Protocol (little endian):
  request : u32 total_size | (u8 opcode, args...)*
  reply   : u32 total_size | u8 result (0 = ok, 0xFF = fail) | data...
Several commands can be batched in one request; their replies are concatenated.
"""

import socket
import struct
import threading
import time

import numpy as np

MSG_READ8, MSG_READ16, MSG_READ32, MSG_READ64 = 0, 1, 2, 3
MSG_WRITE8, MSG_WRITE16, MSG_WRITE32, MSG_WRITE64 = 4, 5, 6, 7
MSG_VERSION, MSG_SAVESTATE, MSG_LOADSTATE = 8, 9, 10
MSG_TITLE, MSG_ID, MSG_UUID, MSG_GAMEVERSION, MSG_STATUS = 11, 12, 13, 14, 15

STATUS_NAMES = {0: "running", 1: "paused", 2: "shutdown"}
EE_RAM_SIZE = 32 * 1024 * 1024
# PCSX2 caps requests at 650000 bytes and replies at 450000 bytes.
_MAX_REPLY = 400000
_MAX_REQUEST = 600000
_RETRY_AFTER = 1.0  # seconds to wait before reconnecting after a failed connect


class PineError(Exception):
    pass


class Pine:
    def __init__(self, slot=28011, host="127.0.0.1", timeout=2.0):
        self.addr = (host, slot)
        self.timeout = timeout
        self.sock = None
        self.lock = threading.RLock()
        self._retry_at = 0.0

    def connect(self):
        with self.lock:
            self.close()
            s = socket.create_connection(self.addr, timeout=self.timeout)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = s
        return self

    def close(self):
        with self.lock:
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
        """Send one message and return its reply data. Connects (and reconnects) on demand."""
        with self.lock:
            if self.sock is None:
                if time.monotonic() < self._retry_at:
                    raise PineError("not connected to PCSX2")
                try:
                    self.connect()
                except OSError:
                    self._retry_at = time.monotonic() + _RETRY_AFTER
                    raise
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

    def read_f32(self, addr):
        return struct.unpack("<f", struct.pack("<I", self.read32(addr)))[0]

    def write8(self, addr, v):
        self._transact(struct.pack("<BIB", MSG_WRITE8, addr, v & 0xFF))

    def write32(self, addr, v):
        self._transact(struct.pack("<BII", MSG_WRITE32, addr, v & 0xFFFFFFFF))

    def write_f32(self, addr, value):
        self.write32(addr, struct.unpack("<I", struct.pack("<f", value))[0])

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

    def crc(self):
        return self._string(MSG_UUID)

    def status(self):
        """"running", "paused" or "shutdown"."""
        code = struct.unpack("<I", self._transact(bytes([MSG_STATUS])))[0]
        return STATUS_NAMES.get(code, "?")

    # --- bulk ------------------------------------------------------------

    def read_block(self, addr, size):
        """Read `size` bytes from `addr` (both multiples of 4) with batched 64/32-bit reads."""
        assert addr % 4 == 0 and size % 4 == 0
        if addr % 8 == 0 and size % 8 == 0:  # fast path for big dumps
            return self.read_u64s(addr + 8 * np.arange(size // 8, dtype=np.int64)).tobytes()
        out = bytearray()
        a, end = addr, addr + size
        while a < end:
            batch, replied = bytearray(), 0
            while a < end and replied < _MAX_REPLY:
                if a % 8 == 0 and end - a >= 8:
                    batch += struct.pack("<BI", MSG_READ64, a); a += 8; replied += 8
                else:
                    batch += struct.pack("<BI", MSG_READ32, a); a += 4; replied += 4
            out += self._transact(bytes(batch))
        return bytes(out)

    def write_block(self, addr, data):
        assert addr % 4 == 0 and len(data) % 4 == 0
        batch = bytearray()
        for i in range(0, len(data), 4):
            batch += struct.pack("<BI", MSG_WRITE32, addr + i) + data[i:i + 4]
            if len(batch) >= _MAX_REQUEST:
                self._transact(bytes(batch))
                batch.clear()
        if batch:
            self._transact(bytes(batch))

    def read_floats(self, addr, count):
        return list(struct.unpack(f"<{count}f", self.read_block(addr, count * 4)))

    def write_floats(self, addr, values):
        self.write_block(addr, struct.pack(f"<{len(values)}f", *values))

    def read_u64s(self, addrs):
        """Batched Read64 at each address in `addrs` (array-like); returns a uint64 array."""
        addrs = np.asarray(addrs, dtype=np.uint32)
        per_batch = _MAX_REPLY // 8
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
        """Batched reads. `reads` is a list of (width_bytes, addr); returns a list of ints."""
        ops = {1: MSG_READ8, 2: MSG_READ16, 4: MSG_READ32, 8: MSG_READ64}
        fmts = {1: "B", 2: "H", 4: "I", 8: "Q"}
        body = b"".join(struct.pack("<BI", ops[w], a) for w, a in reads)
        data = self._transact(body)
        vals, off = [], 0
        for w, _ in reads:
            vals.append(struct.unpack_from("<" + fmts[w], data, off)[0])
            off += w
        return vals

    def batch(self):
        return Batch(self)


class Batch:
    """Queue reads and writes, send them as one PINE message, get the reads back in order.

    Each read_* call returns an index into the list that run() returns.
    """

    def __init__(self, pine):
        self.pine = pine
        self.payload = bytearray()
        self.reads = []  # (kind, count)

    def read_u32(self, addr):
        self.payload += struct.pack("<BI", MSG_READ32, addr)
        self.reads.append(("u32", 1))
        return len(self.reads) - 1

    def read_bytes(self, addr, count):
        """`count` must be a multiple of 4; returned as bytes."""
        for i in range(0, count, 4):
            self.payload += struct.pack("<BI", MSG_READ32, addr + i)
        self.reads.append(("bytes", count // 4))
        return len(self.reads) - 1

    def read_floats(self, addr, count):
        for i in range(count):
            self.payload += struct.pack("<BI", MSG_READ32, addr + 4 * i)
        self.reads.append(("f32", count))
        return len(self.reads) - 1

    def write_u32(self, addr, value):
        self.payload += struct.pack("<BII", MSG_WRITE32, addr, value & 0xFFFFFFFF)

    def write_floats(self, addr, values):
        for i, v in enumerate(values):
            self.payload += struct.pack("<BIf", MSG_WRITE32, addr + 4 * i, v)

    def run(self):
        if not self.payload:
            return []
        data = self.pine._transact(bytes(self.payload))
        out, pos = [], 0
        for kind, n in self.reads:
            chunk = data[pos:pos + 4 * n]
            pos += 4 * n
            if kind == "u32":
                out.append(struct.unpack("<I", chunk)[0])
            elif kind == "f32":
                out.append(list(struct.unpack(f"<{n}f", chunk)))
            else:
                out.append(chunk)
        return out
