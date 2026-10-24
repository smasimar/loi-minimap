"""Snapshot-and-filter memory scanner for PCSX2 over PINE (Cheat Engine style).

State persists in ./scan_state so each step can be a separate command:

  python scanner.py snap a                # dump EE RAM as snapshot "a"
  python scanner.py new u8                # start a search over all u8 addresses
  python scanner.py snap b
  python scanner.py filter changed        # compare the two latest snaps
  python scanner.py filter eq 3           # value in latest snap == 3
  python scanner.py filter same --a a     # latest snap equal to snap "a"
  python scanner.py list 40
  python scanner.py watch 0x3A1B20 u16 f32@0x3A1B30
  python scanner.py diff a b              # changed-byte regions between two snaps
  python scanner.py undo

Types: u8 u16 u32 s8 s16 s32 f32.
Filters: changed same inc dec eq V ne V gt V lt V range LO HI bitgain (only 0->1 bit flips) bitgain1 (exactly one bit)
"""

import argparse
import json
import os
import sys
import time

import numpy as np

from pine import Pine

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "scan_state")
SNAPS = os.path.join(STATE, "snaps")
META = os.path.join(STATE, "meta.json")

TYPES = {
    "u8": "<u1", "u16": "<u2", "u32": "<u4",
    "s8": "<i1", "s16": "<i2", "s32": "<i4", "f32": "<f4",
}
# Ignore the kernel area; game code/data lives above 1 MB.
DEFAULT_LO, DEFAULT_HI = 0x00100000, 0x02000000


def load_meta():
    if os.path.exists(META):
        with open(META) as f:
            return json.load(f)
    return {"snaps": [], "type": None, "history": 0}


def save_meta(m):
    os.makedirs(STATE, exist_ok=True)
    with open(META, "w") as f:
        json.dump(m, f, indent=1)


def snap_path(name):
    return os.path.join(SNAPS, name + ".bin")


def load_snap(name):
    return np.fromfile(snap_path(name), dtype=np.uint8)


def values(ram, addrs, typ):
    # candidate addresses are always aligned to the type size
    dt = np.dtype(TYPES[typ])
    return ram.view(dt)[addrs // dt.itemsize]


def cand_path(step):
    return os.path.join(STATE, f"cand_{step}.npy")


def load_cands(m):
    return np.load(cand_path(m["history"]))


def push_cands(m, addrs):
    m["history"] += 1
    np.save(cand_path(m["history"]), addrs)
    save_meta(m)


def fmt(v, typ):
    if typ == "f32":
        return f"{v:.4f}"
    if typ.startswith("u"):
        return f"{int(v)} (0x{int(v):X})"
    return str(int(v))


# --- commands -------------------------------------------------------------

def cmd_snap(a):
    m = load_meta()
    os.makedirs(SNAPS, exist_ok=True)
    t = time.perf_counter()
    with Pine(a.slot) as p:
        ram = p.dump_ee()
    ram.tofile(snap_path(a.name))
    if a.name in m["snaps"]:
        m["snaps"].remove(a.name)
    m["snaps"].append(a.name)
    save_meta(m)
    print(f"snap '{a.name}' saved ({time.perf_counter() - t:.1f}s)")


def cmd_new(a):
    m = load_meta()
    if not m["snaps"]:
        sys.exit("take a snap first")
    size = np.dtype(TYPES[a.type]).itemsize
    lo, hi = int(a.lo, 0), int(a.hi, 0)
    addrs = np.arange(lo, hi - size + 1, size, dtype=np.int64)
    # drop old candidate files
    for f in os.listdir(STATE):
        if f.startswith("cand_"):
            os.remove(os.path.join(STATE, f))
    m["type"], m["history"] = a.type, 0
    if a.type == "f32":
        v = values(load_snap(m["snaps"][-1]), addrs, "f32")
        addrs = addrs[np.isfinite(v) & (np.abs(v) < 1e6)]
    push_cands(m, addrs)
    print(f"new {a.type} search: {len(addrs):,} candidates")


def cmd_filter(a):
    m = load_meta()
    typ = m["type"]
    if typ is None:
        sys.exit("run 'new <type>' first")
    addrs = load_cands(m)
    b_name = a.b or m["snaps"][-1]
    a_name = a.a or (m["snaps"][-2] if len(m["snaps"]) > 1 else None)
    cur = values(load_snap(b_name), addrs, typ)
    prev = values(load_snap(a_name), addrs, typ) if a_name else None
    op, args = a.op, [float(x) if typ == "f32" else int(x, 0) for x in a.args]

    def need_prev():
        if prev is None:
            sys.exit("need two snaps")
        return prev

    if op == "changed":
        keep = cur != need_prev()
    elif op == "same":
        keep = cur == need_prev()
    elif op == "inc":
        keep = cur > need_prev()
    elif op == "dec":
        keep = cur < need_prev()
    elif op == "eq":
        keep = cur == args[0]
    elif op == "ne":
        keep = cur != args[0]
    elif op == "gt":
        keep = cur > args[0]
    elif op == "lt":
        keep = cur < args[0]
    elif op == "range":
        keep = (cur >= args[0]) & (cur <= args[1])
    elif op in ("bitgain", "bitgain1"):
        p_ = need_prev().astype(np.uint64)
        c_ = cur.astype(np.uint64)
        gained, lost = c_ & ~p_, p_ & ~c_
        keep = (gained != 0) & (lost == 0)
        if op == "bitgain1":
            keep &= (gained & (gained - np.uint64(1))) == 0
    else:
        sys.exit(f"unknown filter {op}")
    addrs = addrs[keep]
    push_cands(m, addrs)
    print(f"{op} ({a_name} -> {b_name}): {len(addrs):,} candidates left")
    if len(addrs) <= 20:
        show(m, addrs, 20)


def show(m, addrs, n):
    typ = m["type"]
    names = m["snaps"][-4:]
    cols = [values(load_snap(s), addrs[:n], typ) for s in names]
    print("address     " + "".join(f"{s:>22}" for s in names))
    for i, ad in enumerate(addrs[:n]):
        print(f"0x{ad:08X}  " + "".join(f"{fmt(c[i], typ):>22}" for c in cols))


def cmd_list(a):
    m = load_meta()
    addrs = load_cands(m)
    print(f"{len(addrs):,} candidates ({m['type']})")
    show(m, addrs, a.n)


def cmd_undo(a):
    m = load_meta()
    if m["history"] <= 1:
        sys.exit("nothing to undo")
    os.remove(cand_path(m["history"]))
    m["history"] -= 1
    save_meta(m)
    print(f"undone: {len(load_cands(m)):,} candidates")


def parse_watch(spec, default_type):
    typ, _, addr = spec.rpartition("@")
    return (typ or default_type), int(addr, 0)


def cmd_watch(a):
    specs = [parse_watch(s, a.type) for s in a.addrs]
    widths = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "f32": 4}
    with Pine(a.slot) as p:
        last = None
        end = time.time() + a.seconds if a.seconds else None
        while end is None or time.time() < end:
            raw = p.read_many([(widths[t], ad) for t, ad in specs])
            vals = []
            for (t, _), r in zip(specs, raw):
                b = int(r).to_bytes(widths[t], "little")
                vals.append(np.frombuffer(b, dtype=TYPES[t])[0])
            line = "  ".join(f"{ad:08X}={fmt(v, t)}" for (t, ad), v in zip(specs, vals))
            if line != last:
                print(time.strftime("%H:%M:%S"), line, flush=True)
                last = line
            time.sleep(a.interval)


def cmd_diff(a):
    x, y = load_snap(a.a), load_snap(a.b)
    lo, hi = int(a.lo, 0), int(a.hi, 0)
    d = np.nonzero(x[lo:hi] != y[lo:hi])[0] + lo
    print(f"{len(d):,} bytes differ")
    if not len(d):
        return
    # group into regions separated by > gap bytes
    breaks = np.nonzero(np.diff(d) > a.gap)[0]
    starts = np.r_[d[0], d[breaks + 1]]
    ends = np.r_[d[breaks], d[-1]]
    print(f"{len(starts)} regions")
    for s, e in list(zip(starts, ends))[: a.n]:
        n = e - s + 1
        print(f"0x{s:08X}-0x{e:08X} ({n} B)  {x[s:s + min(n, 16)].tobytes().hex(' ')}  ->  {y[s:s + min(n, 16)].tobytes().hex(' ')}")


def cmd_hex(a):
    ram = load_snap(a.snap) if a.snap else None
    start, n = int(a.addr, 0), a.len
    if ram is None:
        with Pine(a.slot) as p:
            data = p.read_block(start & ~7, (n + 15) & ~7)[start & 7:][:n]
    else:
        data = ram[start:start + n].tobytes()
    for off in range(0, n, 16):
        chunk = data[off:off + 16]
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        print(f"0x{start + off:08X}  {chunk.hex(' '):<48}  {asc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=28011)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snap"); s.add_argument("name"); s.set_defaults(f=cmd_snap)
    s = sub.add_parser("new"); s.add_argument("type", choices=TYPES)
    s.add_argument("--lo", default=hex(DEFAULT_LO)); s.add_argument("--hi", default=hex(DEFAULT_HI))
    s.set_defaults(f=cmd_new)
    s = sub.add_parser("filter"); s.add_argument("op"); s.add_argument("args", nargs="*")
    s.add_argument("--a"); s.add_argument("--b"); s.set_defaults(f=cmd_filter)
    s = sub.add_parser("list"); s.add_argument("n", nargs="?", type=int, default=30); s.set_defaults(f=cmd_list)
    s = sub.add_parser("undo"); s.set_defaults(f=cmd_undo)
    s = sub.add_parser("watch"); s.add_argument("addrs", nargs="+"); s.add_argument("--type", default="u32")
    s.add_argument("--interval", type=float, default=0.1); s.add_argument("--seconds", type=float, default=0)
    s.set_defaults(f=cmd_watch)
    s = sub.add_parser("diff"); s.add_argument("a"); s.add_argument("b")
    s.add_argument("--lo", default=hex(DEFAULT_LO)); s.add_argument("--hi", default=hex(DEFAULT_HI))
    s.add_argument("--gap", type=int, default=16); s.add_argument("-n", type=int, default=60)
    s.set_defaults(f=cmd_diff)
    s = sub.add_parser("hex"); s.add_argument("addr"); s.add_argument("len", nargs="?", type=lambda x: int(x, 0), default=256)
    s.add_argument("--snap"); s.set_defaults(f=cmd_hex)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
