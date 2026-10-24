"""Live code patches applied over PINE while the free camera is on.

Gameplay reads the pad through the game's own input layer: once per frame, 0x416560 walks the
two ports and calls FillPlayerInput (0x416438) with the held buttons and the four *raw* stick
bytes from the libpad port struct (+0xC rx, +0xD ry, +0xE lx, +0xF ly). FillPlayerInput turns
the left stick into the struct at player+0x40 and the right stick into player+0x90, which the
d-pad item/equipment quick menu also reads.

To take the right stick away from the game entirely, the two byte loads that fetch rx/ry are
replaced with constants of 0x80 (centred), the same value the game itself substitutes when no
pad is connected. The tool still reads the real stick from libpad's raw buffer. (The pad
module's GetPadValue dispatcher at 0x403528 is not used by gameplay; patching it does nothing.)
"""
from . import addrs

# (address, original instruction, patched instruction)
_RIGHT_STICK_SITES = [
    (0x4165B8, 0x920A000D, 0x240A0080),  # lbu t2, 0xd(s0)  ->  addiu t2, zero, 0x80   (ry)
    (0x4165D4, 0x9209000C, 0x24090080),  # lbu t1, 0xc(s0)  ->  addiu t1, zero, 0x80   (rx)
    # FillPlayerInput hands the held buttons (standard PAD_* layout, 1 = pressed) to the button
    # state builder at 0x415F30; masking 0x0400 (R3) there keeps R3 away from the game too, so
    # it can't open the item/equipment quick menu. The tool reads R3 from libpad's raw buffer.
    (0x4164DC, 0x32E5FFFF, 0x32E5FBFF),  # andi a1, s7, 0xffff  ->  andi a1, s7, 0xfbff  (R3)
]


class Patch:
    """A set of (address, original, patched) words that can be switched on and off."""

    SITES = []

    def __init__(self, pine):
        self.p = pine
        self.enabled = False

    def check(self):
        """Refuse to patch if the code isn't what this build expects."""
        for addr, orig, patched in self.SITES:
            word = self.p.read32(addr)
            if word not in (orig, patched):
                raise RuntimeError(f"unexpected instruction {word:#010x} at {addr:#x}")

    def enable(self):
        b = self.p.batch()
        for addr, _, patched in self.SITES:
            b.write_u32(addr, patched)
        b.run()
        self.enabled = all(self.p.read32(a) == patched for a, _, patched in self.SITES)
        return self.enabled

    def disable(self):
        b = self.p.batch()
        for addr, orig, _ in self.SITES:
            b.write_u32(addr, orig)
        b.run()
        self.enabled = False


class GameplayCameraRedirect(Patch):
    """Make SetActiveCamera(0) select the free camera instead of the gameplay camera.

    SetActiveCamera(i) at 0x65C018 sets CAM_PTR to 0x792890 + i*0x3F0 for i < 2 (0 = gameplay,
    1 = map screen). Closing the map calls it with 0, and the game would render one frame from
    its own camera before the tool could take over again. With this patch the hand-back goes
    straight to SCRATCH_CAM. Rewritten in place (same 12 words):

        slti  a1, a0, 2
        beq   a1, zero, ret
        lui   v1, 0x79
        bne   a0, zero, idx1
        lui   v0, 0x01F8          ; SCRATCH_CAM (0x1F80000)
        jr    ra
        sw    v0, 0x2880(v1)
    idx1:
        lui   v0, 0x79
        addiu v0, v0, 0x2C80      ; map camera, as before
        sw    v0, 0x2880(v1)
    ret:
        jr    ra
        nop
    """

    _ORIG = [0x00041180, 0x3C030079, 0x00441023, 0x28850002, 0x24632880, 0x10A00004,
             0x00021100, 0x00431021, 0x24420010, 0xAC620000, 0x03E00008, 0x00000000]
    _NEW = [0x28850002, 0x10A00008, 0x3C030079, 0x14800003, 0x3C020000 | (addrs.SCRATCH_CAM >> 16),
            0x03E00008, 0xAC622880, 0x3C020079, 0x24422C80, 0xAC622880, 0x03E00008, 0x00000000]
    SITES = [(0x65C018 + 4 * i, o, n) for i, (o, n) in enumerate(zip(_ORIG, _NEW))]


class RightStickMute(Patch):
    """Hide the right stick and R3 from the game while enabled."""

    SITES = _RIGHT_STICK_SITES
