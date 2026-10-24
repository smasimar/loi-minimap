"""Snapshot EE RAM and search it for float vectors.

  python tools/findvec.py snap <out.bin>             dump all 32 MB of EE RAM
  python tools/findvec.py eye                        derive camera eye from the view matrix
                                                     and search RAM for it
  python tools/findvec.py find x y z [tol]           search for any 3-float vector
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from loicam.pine import Pine
from loicam import addrs

RAM_SIZE = 32 * 1024 * 1024


def snapshot(p):
    return p.read_block(0, RAM_SIZE)


def view_eye(p):
    m = np.array(p.read_floats(addrs.VIEW_MATRIX, 16), dtype=np.float64).reshape(4, 4)
    rot, t = m[:3, :3], m[3, :3]
    # Row-vector convention (v' = v * M): eye = -t * R^T
    return -(t @ rot.T), m


def search(ram, vec, tol):
    f = np.frombuffer(ram, dtype="<f4")
    with np.errstate(invalid="ignore"):
        hit = np.abs(f[:-2] - vec[0]) < tol
        hit &= np.abs(f[1:-1] - vec[1]) < tol
        hit &= np.abs(f[2:] - vec[2]) < tol
    return [int(i) * 4 for i in np.nonzero(hit)[0]]


def main():
    cmd = sys.argv[1]
    with Pine(timeout=10) as p:
        if cmd == "snap":
            open(sys.argv[2], "wb").write(snapshot(p))
            return
        if cmd == "eye":
            eye, m = view_eye(p)
            print("view matrix:\n", np.round(m, 4))
            print("derived eye:", np.round(eye, 3))
            vec, tol = eye, 1.0
        else:
            vec = [float(v) for v in sys.argv[2:5]]
            tol = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
        ram = snapshot(p)
    hits = search(ram, vec, tol)
    for a in hits[:60]:
        vals = struct.unpack_from("<4f", ram, a)
        print(f"{a:08x}: " + " ".join(f"{v:12.4f}" for v in vals))
    print(f"{len(hits)} hits")


if __name__ == "__main__":
    main()
