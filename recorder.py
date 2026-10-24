"""Timed live recording of candidate vectors / flags for correlation analysis.

Waits for Pause/Break or Scroll Lock, beeps, then samples for --seconds at --hz.
Candidates: every 16-byte aligned (x, y, z, 1.0f) vector in game RAM, plus extra
byte addresses given with --bytes. Beeps every 4 seconds and at the end.

  python recorder.py out_name [--seconds 30] [--bytes 0x734E12 0x738EAC]
"""

import argparse
import ctypes
import os
import time
import winsound

import numpy as np

from pine import Pine
import scanner

user32 = ctypes.windll.user32


def wait_key():
    while not (user32.GetAsyncKeyState(0x13) & 0x8000 or user32.GetAsyncKeyState(0x91) & 0x8000):
        time.sleep(0.02)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--hz", type=float, default=10)
    ap.add_argument("--bytes", nargs="*", default=[])
    a = ap.parse_args()

    with Pine() as p:
        ram = p.dump_ee()
        f = ram.view("<f4").reshape(-1, 4)
        vec = np.nonzero(f[:, 3] == 1.0)[0] * 16
        vec = vec[vec >= 0x100000].astype(np.uint32)
        byte_addrs = np.array([int(x, 0) for x in a.bytes], dtype=np.uint32)
        # two Read64 per vector (xy, z1), one Read64 per byte (aligned down)
        reads = np.concatenate([vec, vec + 8, byte_addrs & ~np.uint32(7)])
        print(f"{len(vec):,} vector candidates; press Pause/Scroll Lock to start", flush=True)
        wait_key()
        winsound.Beep(1500, 200)
        t0 = time.perf_counter()
        times, samples = [], []
        next_beep = 4
        while (t := time.perf_counter() - t0) < a.seconds:
            raw = p.read_u64s(reads)
            times.append(t)
            samples.append(raw)
            if t >= next_beep:
                winsound.Beep(900, 60)
                next_beep += 4
            time.sleep(max(0, 1 / a.hz - (time.perf_counter() - t0 - t)))
        winsound.Beep(600, 400)

    raw = np.stack(samples)  # (T, reads)
    n = len(vec)
    xy = raw[:, :n].view("<f4").reshape(len(times), n, 2)
    z = raw[:, n:2 * n].view("<f4").reshape(len(times), n, 2)[:, :, 0]
    pos = np.dstack([xy, z])  # (T, n, 3)
    b = raw[:, 2 * n:]
    shift = ((byte_addrs & 7) * 8).astype(np.uint64)
    flags = ((b >> shift) & np.uint64(0xFF)).astype(np.uint8)
    out = os.path.join(scanner.STATE, a.name + ".npz")
    np.savez_compressed(out, t=np.array(times), addrs=vec, pos=pos, byte_addrs=byte_addrs, flags=flags)
    print(f"saved {len(times)} samples to {out}", flush=True)


if __name__ == "__main__":
    main()
