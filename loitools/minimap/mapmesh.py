"""Decode LoI area map meshes (type-14 entry of a map package) into polygons.

Chunk layout (VIF): ... V3_32 positions (vertex pool, int32, map units x16, z negated),
V3_16 normals, V2_16 uv, then V4_16 index list: idx = (value & 0x7fff)/4 - 1, bit 15 = ADC
(no draw); read as one triangle strip.
"""

import numpy as np

from . import vif


def chunks(data):
    """Yield (offset, positions[n,3], faces[list of index tuples]) for each mesh chunk."""
    u = np.frombuffer(data[: len(data) // 4 * 4], dtype="<u4")
    starts = [int(i) * 4 for i in np.nonzero(((u >> 24) & 0x7F) == 0x68)[0] if (u[i] >> 16) & 0xFF]
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(data)
        pos, faces = None, []
        for off, c, num, imm, dec in vif.walk(data, s, end):
            if c is None:
                break
            if c == 0x68 and pos is None:
                pos = dec.astype(np.int64)
            # the index block is the V4_16 unpack after the positions (the V4_16 blocks before
            # them are chunk headers); it can be as short as one or two rows
            elif c == 0x6D and pos is not None and dec is not None:
                n = len(pos)
                # one continuous strip: bit 15 = "add vertex without drawing" (ADC);
                # a vertex without it completes a triangle with the previous two
                window = []
                for v in dec.astype(np.int64).ravel():
                    i = int(v & 0x7FFF) // 4 - 1
                    if not 0 <= i < n:
                        continue
                    window = (window + [i])[-3:]
                    if not v & 0x8000 and len(window) == 3 and len(set(window)) == 3:
                        faces.append(tuple(window))
                break  # one index block per chunk
        if pos is not None:
            yield s, pos, faces
