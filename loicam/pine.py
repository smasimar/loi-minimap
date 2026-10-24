"""PINE IPC client for PCSX2.

Wire format (little endian): every message is u32 total_size + payload, where payload is one
or more commands (u8 opcode + args) back to back. The reply is u32 total_size + u8 result
(0 = OK, 0xFF = FAIL) followed by each command's return data in order. PCSX2 serves one
client at a time; on Windows it listens on TCP 127.0.0.1:<slot> (default 28011).
"""
import socket
import struct

OP_READ8, OP_READ16, OP_READ32, OP_READ64 = 0, 1, 2, 3
OP_WRITE8, OP_WRITE16, OP_WRITE32, OP_WRITE64 = 4, 5, 6, 7
OP_VERSION, OP_SAVESTATE, OP_LOADSTATE = 8, 9, 10
OP_TITLE, OP_ID, OP_UUID, OP_GAMEVER, OP_STATUS = 11, 12, 13, 14, 15

STATUS = {0: "running", 1: "paused", 2: "shutdown"}
_READ_SIZE = {OP_READ8: 1, OP_READ16: 2, OP_READ32: 4, OP_READ64: 8}
_MAX_BATCH = 60000  # PCSX2's buffer is ~650 KB; stay well under it


class PineError(RuntimeError):
    pass


class Pine:
    def __init__(self, slot=28011, host="127.0.0.1", timeout=2.0):
        self.sock = socket.create_connection((host, slot), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- transport --------------------------------------------------------------------------

    def _recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise PineError("PCSX2 closed the connection")
            buf += chunk
        return bytes(buf)

    def _transact(self, payload):
        self.sock.sendall(struct.pack("<I", 4 + len(payload)) + payload)
        size = struct.unpack("<I", self._recv_exact(4))[0]
        reply = self._recv_exact(size - 4)
        if reply[0] != 0:
            raise PineError("PINE command failed")
        return reply[1:]

    # -- single commands --------------------------------------------------------------------

    def _string(self, op):
        data = self._transact(bytes([op]))
        n = struct.unpack_from("<I", data)[0]
        return data[4:4 + n].rstrip(b"\0").decode(errors="replace")

    def version(self):
        return self._string(OP_VERSION)

    def title(self):
        return self._string(OP_TITLE)

    def game_id(self):
        return self._string(OP_ID)

    def crc(self):
        return self._string(OP_UUID)

    def status(self):
        return STATUS.get(struct.unpack("<I", self._transact(bytes([OP_STATUS])))[0], "?")

    def read32(self, addr):
        return struct.unpack("<I", self._transact(struct.pack("<BI", OP_READ32, addr)))[0]

    def write32(self, addr, value):
        self._transact(struct.pack("<BII", OP_WRITE32, addr, value & 0xFFFFFFFF))

    def read_f32(self, addr):
        return struct.unpack("<f", struct.pack("<I", self.read32(addr)))[0]

    def write_f32(self, addr, value):
        self.write32(addr, struct.unpack("<I", struct.pack("<f", value))[0])

    # -- bulk -------------------------------------------------------------------------------

    def read_block(self, addr, size):
        """Read `size` bytes starting at `addr` (4-byte aligned) using batched read64s."""
        assert addr % 4 == 0
        out = bytearray()
        a, end = addr, addr + size
        while a < end:
            batch, ops = bytearray(), []
            while a < end and len(batch) < _MAX_BATCH:
                if a % 8 == 0 and end - a >= 8:
                    batch += struct.pack("<BI", OP_READ64, a); ops.append(8); a += 8
                else:
                    batch += struct.pack("<BI", OP_READ32, a); ops.append(4); a += 4
            out += self._transact(bytes(batch))
        return bytes(out[:size])

    def write_block(self, addr, data):
        assert addr % 4 == 0 and len(data) % 4 == 0
        batch = bytearray()
        for i in range(0, len(data), 4):
            batch += struct.pack("<BI", OP_WRITE32, addr + i) + data[i:i + 4]
            if len(batch) >= _MAX_BATCH:
                self._transact(bytes(batch)); batch.clear()
        if batch:
            self._transact(bytes(batch))

    def batch(self):
        return Batch(self)

    def read_floats(self, addr, count):
        return list(struct.unpack(f"<{count}f", self.read_block(addr, count * 4)))

    def write_floats(self, addr, values):
        self.write_block(addr, struct.pack(f"<{len(values)}f", *values))


class Batch:
    """Queue reads and writes, send them as one PINE message, get reads back in order.

    Each read_* call returns an index into the list that run() returns.
    """

    def __init__(self, pine):
        self.pine = pine
        self.payload = bytearray()
        self.reads = []  # (kind, count)

    def read_u32(self, addr):
        self.payload += struct.pack("<BI", OP_READ32, addr)
        self.reads.append(("u32", 1))
        return len(self.reads) - 1

    def read_bytes(self, addr, count):
        """`count` must be a multiple of 4; returned as bytes."""
        for i in range(0, count, 4):
            self.payload += struct.pack("<BI", OP_READ32, addr + i)
        self.reads.append(("bytes", count // 4))
        return len(self.reads) - 1

    def read_floats(self, addr, count):
        for i in range(count):
            self.payload += struct.pack("<BI", OP_READ32, addr + 4 * i)
        self.reads.append(("f32", count))
        return len(self.reads) - 1

    def write_u32(self, addr, value):
        self.payload += struct.pack("<BII", OP_WRITE32, addr, value & 0xFFFFFFFF)

    def write_floats(self, addr, values):
        for i, v in enumerate(values):
            self.payload += struct.pack("<BIf", OP_WRITE32, addr + 4 * i, v)

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
