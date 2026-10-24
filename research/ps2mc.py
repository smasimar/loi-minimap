"""Minimal read-only PS2 memory card image reader (PCSX2 .ps2 files, with ECC)."""

import struct


class MemCard:
    def __init__(self, path):
        self.data = open(path, "rb").read()
        sb = self.data[:340]
        assert sb.startswith(b"Sony PS2 Memory Card Format"), "not a PS2 memory card image"
        (self.page_len, self.pages_per_cluster, self.pages_per_block, _, self.clusters_per_card,
         self.alloc_offset, self.alloc_end, self.rootdir_cluster, self.backup1, self.backup2) = \
            struct.unpack_from("<HHHHIIIIII", sb, 40)
        self.ifc_list = struct.unpack_from("<32I", sb, 80)
        raw = self.page_len + (16 if len(self.data) > self.page_len * self.clusters_per_card * 2 else 0)
        self.raw_page = raw
        self.cluster_size = self.page_len * self.pages_per_cluster
        self._fat = None

    def page(self, n):
        o = n * self.raw_page
        return self.data[o:o + self.page_len]

    def cluster(self, n):
        return b"".join(self.page(n * self.pages_per_cluster + i) for i in range(self.pages_per_cluster))

    def fat(self):
        if self._fat is None:
            per = self.cluster_size // 4
            entries = []
            for ifc in self.ifc_list:
                if not ifc:
                    break
                for ind in struct.unpack(f"<{per}I", self.cluster(ifc)):
                    if ind == 0xFFFFFFFF:
                        continue
                    entries.extend(struct.unpack(f"<{per}I", self.cluster(ind)))
            self._fat = entries
        return self._fat

    def chain(self, start):
        """Data of a cluster chain starting at relative cluster `start`."""
        out, c, fat = [], start, self.fat()
        while True:
            out.append(self.cluster(c + self.alloc_offset))
            nxt = fat[c]
            if nxt & 0x80000000 == 0 or (nxt & 0x7FFFFFFF) == 0x7FFFFFFF:
                break
            c = nxt & 0x7FFFFFFF
        return b"".join(out)

    def listdir(self, cluster, count):
        data = self.chain(cluster)
        ents = []
        for i in range(count):
            e = data[i * 512:(i + 1) * 512]
            mode, _, length = struct.unpack_from("<HHI", e, 0)
            mod = struct.unpack_from("<BBBBBBH", e, 0x18)  # secs, mins, hours, day, month, year
            clus = struct.unpack_from("<I", e, 0x10)[0]
            name = e[0x40:0x60].split(b"\0")[0].decode("ascii", "replace")
            if mode & 0x8000:  # exists
                ents.append({"name": name, "dir": bool(mode & 0x20), "length": length,
                             "cluster": clus, "modified": mod})
        return ents

    def root(self):
        first = self.listdir(self.rootdir_cluster, 1)[0]
        return self.listdir(self.rootdir_cluster, first["length"])

    def read_file(self, ent):
        return self.chain(ent["cluster"])[:ent["length"]]
