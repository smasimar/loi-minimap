"""FILEDATA.BIN package reader (Castlevania: Lament of Innocence, SLUS-20733).

FILEDATA.BIN is a sequence of sector-aligned packages:
  header (0x20 bytes): u32 magic 0x82734927, u32 0, u32 total_size, u32 0, u32 count, u32 0x20, ...
  entries (16 bytes each, from 0x20): u32 id, u32 type, u32 offset (from package start), u32 size
"""

import struct

import numpy as np

from .iso import ISO

MAGIC = 0x82734927
SECTOR = 2048


class FileData:
    def __init__(self, iso_path=None):
        if iso_path is None:  # find it through PCSX2's game folders
            from .gamemap import find_iso
            iso_path = find_iso()
        self.iso = ISO(iso_path)
        rec = next(r for p, r in self.iso.walk() if p == "/FILEDATA.BIN")
        self.base = rec["lba"] * SECTOR
        self.size = rec["size"]
        self.packages = self._scan()

    def _scan(self):
        out, off, CH = [], 0, 64 << 20
        while off < self.size:
            b = self.iso.read_bytes(self.base + off, min(CH, self.size - off))
            u = np.frombuffer(b[: len(b) // SECTOR * SECTOR], dtype="<u4").reshape(-1, 512)
            for i in np.nonzero(u[:, 0] == MAGIC)[0]:
                out.append(off // SECTOR + int(i))
            off += CH
        return out

    def read(self, sector, offset, size):
        return self.iso.read_bytes(self.base + sector * SECTOR + offset, size)

    def entries(self, sector):
        h = self.read(sector, 0, 0x20)
        _, _, total, _, count, hdr = struct.unpack_from("<6I", h)
        raw = self.read(sector, 0x20, count * 16)
        return [dict(zip(("id", "type", "offset", "size"), struct.unpack_from("<4I", raw, i * 16)))
                for i in range(count)]

    def entry_data(self, sector, e):
        return self.read(sector, e["offset"], e["size"])
