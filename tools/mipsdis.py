"""Minimal R5900 (PS2 EE) disassembler for poking at SLUS_207.33.

Covers the integer ISA, COP0/COP1 basics and lq/sq; anything else prints as .word.
Usage: python tools/mipsdis.py <elf> <start_va> [count]
"""
import struct
import sys

REGS = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
        "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
        "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
        "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]

I_OPS = {
    0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu", 0x0C: "andi", 0x0D: "ori",
    0x0E: "xori", 0x18: "daddi", 0x19: "daddiu",
}
MEM_OPS = {
    0x1A: "ldl", 0x1B: "ldr", 0x1E: "lq", 0x1F: "sq", 0x20: "lb", 0x21: "lh", 0x23: "lw",
    0x24: "lbu", 0x25: "lhu", 0x27: "lwu", 0x28: "sb", 0x29: "sh", 0x2B: "sw", 0x37: "ld",
    0x3F: "sd", 0x31: "lwc1", 0x39: "swc1", 0x36: "lqc2", 0x3E: "sqc2",
}
BRANCH_OPS = {0x04: "beq", 0x05: "bne", 0x06: "blez", 0x07: "bgtz",
              0x14: "beql", 0x15: "bnel", 0x16: "blezl", 0x17: "bgtzl"}
SPECIAL = {
    0x00: "sll", 0x02: "srl", 0x03: "sra", 0x04: "sllv", 0x06: "srlv", 0x07: "srav",
    0x08: "jr", 0x09: "jalr", 0x0A: "movz", 0x0B: "movn", 0x0C: "syscall", 0x0D: "break",
    0x0F: "sync", 0x10: "mfhi", 0x12: "mflo", 0x18: "mult", 0x19: "multu", 0x1A: "div",
    0x1B: "divu", 0x20: "add", 0x21: "addu", 0x22: "sub", 0x23: "subu", 0x24: "and",
    0x25: "or", 0x26: "xor", 0x27: "nor", 0x2A: "slt", 0x2B: "sltu", 0x2D: "daddu",
    0x2F: "dsubu", 0x38: "dsll", 0x3A: "dsrl", 0x3B: "dsra", 0x3C: "dsll32",
    0x3E: "dsrl32", 0x3F: "dsra32", 0x14: "dsllv", 0x16: "dsrlv", 0x17: "dsrav",
}
REGIMM = {0x00: "bltz", 0x01: "bgez", 0x02: "bltzl", 0x03: "bgezl",
          0x10: "bltzal", 0x11: "bgezal"}
COP1_S = {0x00: "add.s", 0x01: "sub.s", 0x02: "mul.s", 0x03: "div.s", 0x04: "sqrt.s",
          0x05: "abs.s", 0x06: "mov.s", 0x07: "neg.s", 0x16: "rsqrt.s", 0x18: "adda.s",
          0x1C: "madd.s", 0x1D: "msub.s", 0x24: "cvt.w.s", 0x28: "max.s", 0x29: "min.s",
          0x30: "c.f.s", 0x32: "c.eq.s", 0x34: "c.lt.s", 0x36: "c.le.s"}


class Elf:
    def __init__(self, path):
        self.data = open(path, "rb").read()
        d = self.data
        phoff = struct.unpack_from("<I", d, 0x1C)[0]
        phnum = struct.unpack_from("<H", d, 0x2C)[0]
        self.segs = []
        for i in range(phnum):
            t, off, va, _, fs, _, _, _ = struct.unpack_from("<8I", d, phoff + i * 32)
            if t == 1:
                self.segs.append((va, off, fs))

    def word(self, va):
        for base, off, size in self.segs:
            if base <= va < base + size:
                return struct.unpack_from("<I", self.data, off + va - base)[0]
        return None

    def bytes(self, va, n):
        for base, off, size in self.segs:
            if base <= va < base + size:
                return self.data[off + va - base: off + va - base + n]
        return None


def simm(w):
    v = w & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def disasm(w, pc):
    op = w >> 26
    rs, rt, rd, sa, fn = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31, w & 63
    R = REGS
    if w == 0:
        return "nop"
    if op == 0:
        name = SPECIAL.get(fn)
        if name is None:
            return f".word {w:#010x}"
        if name in ("sll", "srl", "sra", "dsll", "dsrl", "dsra", "dsll32", "dsrl32", "dsra32"):
            return f"{name} {R[rd]}, {R[rt]}, {sa}"
        if name == "jr":
            return f"jr {R[rs]}"
        if name == "jalr":
            return f"jalr {R[rd]}, {R[rs]}"
        if name in ("mfhi", "mflo"):
            return f"{name} {R[rd]}"
        if name in ("mult", "multu", "div", "divu"):
            return f"{name} {R[rs]}, {R[rt]}"
        if name in ("syscall", "break", "sync"):
            return name
        if name.endswith("v") and name[0] in "sd":
            return f"{name} {R[rd]}, {R[rt]}, {R[rs]}"
        return f"{name} {R[rd]}, {R[rs]}, {R[rt]}"
    if op == 1:
        name = REGIMM.get(rt, f"regimm{rt}")
        return f"{name} {R[rs]}, {pc + 4 + simm(w) * 4:#x}"
    if op in (2, 3):
        return f"{'j' if op == 2 else 'jal'} {((pc + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2):#x}"
    if op in BRANCH_OPS:
        name = BRANCH_OPS[op]
        tgt = pc + 4 + simm(w) * 4
        if op in (6, 7, 0x16, 0x17):
            return f"{name} {R[rs]}, {tgt:#x}"
        return f"{name} {R[rs]}, {R[rt]}, {tgt:#x}"
    if op == 0x0F:
        return f"lui {R[rt]}, {w & 0xFFFF:#x}"
    if op in I_OPS:
        name = I_OPS[op]
        imm = w & 0xFFFF if name in ("andi", "ori", "xori") else simm(w)
        return f"{name} {R[rt]}, {R[rs]}, {imm:#x}" if imm >= 0 else f"{name} {R[rt]}, {R[rs]}, -{-imm:#x}"
    if op in MEM_OPS:
        name = MEM_OPS[op]
        tr = f"$f{rt}" if name in ("lwc1", "swc1") else (f"vf{rt}" if name in ("lqc2", "sqc2") else R[rt])
        off = simm(w)
        return f"{name} {tr}, {off:#x}({R[rs]})" if off >= 0 else f"{name} {tr}, -{-off:#x}({R[rs]})"
    if op == 0x11:  # COP1
        fmt = rs
        ft, fs, fd = rt, rd, sa
        if fmt == 0x00:
            return f"mfc1 {R[rt]}, $f{fs}"
        if fmt == 0x04:
            return f"mtc1 {R[rt]}, $f{fs}"
        if fmt == 0x02:
            return f"cfc1 {R[rt]}, $fcr{fs}"
        if fmt == 0x06:
            return f"ctc1 {R[rt]}, $fcr{fs}"
        if fmt == 0x08:
            return f"{['bc1f', 'bc1t', 'bc1fl', 'bc1tl'][rt & 3]} {pc + 4 + simm(w) * 4:#x}"
        if fmt == 0x10:
            name = COP1_S.get(fn, f"cop1.s.{fn:#x}")
            if name.startswith("c."):
                return f"{name} $f{fs}, $f{ft}"
            if name in ("sqrt.s", "abs.s", "mov.s", "neg.s", "cvt.w.s"):
                return f"{name} $f{fd}, $f{fs}" if name != "sqrt.s" else f"{name} $f{fd}, $f{ft}"
            if name == "adda.s":
                return f"{name} $f{fs}, $f{ft}"
            return f"{name} $f{fd}, $f{fs}, $f{ft}"
        if fmt == 0x14 and fn == 0x20:
            return f"cvt.s.w $f{fd}, $f{fs}"
    if op == 0x12:
        return f"cop2 {w:#010x}"
    if op == 0x10:
        return f"cop0 {w:#010x}"
    return f".word {w:#010x}"


def main():
    elf = Elf(sys.argv[1])
    start = int(sys.argv[2], 16) & ~3
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    for i in range(count):
        pc = start + i * 4
        w = elf.word(pc)
        if w is None:
            break
        print(f"{pc:08x}: {w:08x}  {disasm(w, pc)}")


if __name__ == "__main__":
    main()
