"""Known EE addresses for Castlevania: Lament of Innocence, NTSC-U SLUS-20733 v1.01 (CRC 28270F7D)."""

GAME_ID = "SLUS-20733"
GAME_CRC = "28270f7d"

# Global render/screen state; built by the projection setup at 0x712310.
SCREEN = 0x734E00
PROJ_MATRIX = SCREEN + 0x100     # 4x4 float, three variants at +0x100/+0x140/+0x180
VIEW_MATRIX = SCREEN + 0x1C0     # 4x4 float, row-vector world->view (rows 0-2 rotation, row 3 translation)
VIEW_MATRIX_COPY = SCREEN + 0x200

# Active camera. The renderer and the follow logic both go through this pointer; the
# authored camera controller (rails etc.) writes the eye straight into GAMEPLAY_CAM. Pointing
# CAM_PTR at our own copy leaves the game's rail camera running untouched in GAMEPLAY_CAM
# while rendering uses ours.
CAM_PTR = 0x792880
GAMEPLAY_CAM = 0x792890
CAM_SIZE = 0x1A0

# Offsets into a camera struct (world space, Y is down: negative Y is up)
CAM_FOLLOW = 0x00     # vec4 follow point (Leon + head height); rewritten by the game each frame
CAM_LOOK_A = 0x30     # vec4 smoothed look-at, eased toward CAM_FOLLOW; drives the view
CAM_EYE = 0x40        # vec4 eye position
CAM_LOOK_B = 0x90     # vec4 second smoothed look-at; also drives the view
CAM_VIEW = 0xB0       # 4x4 world->view
CAM_INV_VIEW = 0xF0   # 4x4 view->world (row 3 = eye)
CAM_FOCAL = 0x174     # int, current focal length in pixels (eases toward CAM_FOCAL_TARGET);
CAM_FOCAL_TARGET = 0x178  # copied to SCREEN+0x40 for the projection. Lower = wider FoV.
                          # 835 is ~42 deg horizontal over 640 px; rooms author their own.

# While CAM_PTR is redirected the game's camera system stops updating altogether (including the
# follow point), so the pivot has to come from Leon himself.

# Leon's actor (static, in .bss). Position is repeated at +0x170/+0x180/+0x190 (current and
# previous frames); facing yaw in radians is the Y component of +0x1B0, with forward =
# (sin yaw, 0, cos yaw), the same convention as the orbit.
LEON = 0xB632C0
LEON_POS = LEON + 0x170
LEON_ROT = LEON + 0x1B0
FOLLOW_HEIGHT = 300.0   # the game's follow point sits this far above Leon's feet

# Game-side copy of the sticks (rx, ry, lx, ly bytes), current frame then previous frame.
STICKS = 0xA09A08
STICKS_PREV = 0xA09A0C

# 1 while the map screen is open, else 0. Set/cleared only by the map module (0x439xxx-0x43Axxx)
# through 0x453080 / 0x453090. Item/equipment quick menu and gameplay leave it 0.
MAP_OPEN = 0x738EAC

# Free RAM inside the unused heap top (0x1D5A000-0x1FED000 stays zero in gameplay).
SCRATCH_CAM = 0x1F80000

# libpad output buffers live on the heap, so they're found by signature at runtime (see pad.py).
# Seen at 0xA09AC4 / 0xA09B00 / 0xA09B80 in one boot.
