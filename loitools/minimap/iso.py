"""Minimal read-only ISO9660 reader (enough for PS2 DVDs)."""

import struct

SECTOR = 2048


class ISO:
    def __init__(self, path):
        self.f = open(path, "rb")
        pvd = self.read(16, 1)
        assert pvd[1:6] == b"CD001", "not an ISO9660 image"
        root = pvd[156:156 + 34]
        self.root = self._rec(root)

    def read(self, lba, nsect=1):
        self.f.seek(lba * SECTOR)
        return self.f.read(nsect * SECTOR)

    def read_bytes(self, offset, n):
        self.f.seek(offset)
        return self.f.read(n)

    @staticmethod
    def _rec(b):
        lba = struct.unpack_from("<I", b, 2)[0]
        size = struct.unpack_from("<I", b, 10)[0]
        flags = b[25]
        nlen = b[32]
        name = b[33:33 + nlen].decode("ascii", "replace").split(";")[0]
        return {"lba": lba, "size": size, "dir": bool(flags & 2), "name": name}

    def listdir(self, d):
        data = self.read(d["lba"], (d["size"] + SECTOR - 1) // SECTOR)
        out, i = [], 0
        while i < len(data):
            n = data[i]
            if n == 0:  # records don't cross sectors
                i = (i // SECTOR + 1) * SECTOR
                continue
            r = self._rec(data[i:i + n])
            if r["name"] not in ("\x00", "\x01"):
                out.append(r)
            i += n
        return out

    def walk(self, d=None, prefix=""):
        d = d or self.root
        for r in self.listdir(d):
            path = prefix + "/" + r["name"]
            yield path, r
            if r["dir"]:
                yield from self.walk(r, path)
