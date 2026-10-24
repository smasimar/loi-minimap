# LoI minimap

Tools for **Castlevania: Lament of Innocence** (NTSC-U, SLUS-20733) running in PCSX2, driven
over PCSX2's PINE interface:

- **Minimap overlay** (`overlay.py`): reads the game's memory and draws a transparent,
  click-through minimap of the game's own map on top of the PCSX2 game view.
- **Third-person camera** (`loi_cam.py`): a free orbit camera on the right stick. See
  [Third-person camera](#third-person-camera).

PCSX2 accepts only one PINE client at a time, so the two tools can't run together yet.

## Setup

1. PCSX2 → Settings → Advanced → **Enable PINE** (slot 28011).
2. Run the game in windowed or borderless fullscreen (exclusive fullscreen hides overlays).
3. `pip install -r requirements.txt`
4. `python overlay.py`

## How the map works

By default the overlay draws the game's **own map data**, decoded from your LoI disc image
(`FILEDATA.BIN`; the ISO is found through PCSX2's game folders, or set `iso_path` in
`config.json`). The 7 area maps are extracted once into `data/gamemap/`.

Each map package has a room table (game area id, room number, floor, save-room flag, room origin
and scale) and a VIF model with every room's fill and outline. The player's room-local position
(RAM) is placed with `origin + R * local * 1.6`, where R is the room's rotation from its record
(+0x4C, radians); it is double-checked per room against where you walk (`data/orient.json`). Explored rooms come from the game's own flags (see below); if those can't be
read, the overlay falls back to the rooms it has seen you in (`data/visited.json`).

Some maps (Anti-Soul Mysteries Lab) store room shapes in room-local coordinates; those rooms are
placed with their origin and rotation (record +0x4C, radians) like the player.

## Hotkeys (Ctrl+Shift + …)

| Key | Action |
|---|---|
| M | show / hide |
| Z | whole floor / zoomed minimap |
| + / - (or ↑ / ↓) | bigger / smaller |
| C (or ← / →) | move to another corner |
| O | cycle opacity |

Settings persist in `config.json`.

## Memory addresses (SLUS-20733)

| Address | Type | Meaning |
|---|---|---|
| `0x738E94` | u8 | game area id (0 HoSR, 1 ASML, 2 DPoW, 3 GFbT, 4/6/7 Castle Entrance parts, 5 Pagoda, 8 Theatre) |
| `0x738E96` | u8 | room number within the current area |
| `0x734E12` | u8 | 1 while the map or pause menu is open |
| `0x792A30` | f32×3 | player x, y, z (room-local) |
| `0x792A54` | f32 | player facing (radians, room-local) |
| `0x8060B8` | u32 | pointer to the last loaded save copy; the live game state is 0x4690 bytes before it |
| live state + `0xF44` | 12 B × room | per-room records, area after area (74, 55, 57, 65, 4, 55, 6, 10, 58 rooms); byte +0 ≠ 0 = explored |

The same per-room records are in the memory card save (`BASLUS-20733cvps2us`, slots 0x21D0 bytes
apart).

## Research tools

- `pine.py` – PINE client (`python pine.py` prints the running game)
- `scanner.py` – snapshot/filter/diff/watch memory scanner
- `snapper.py` – hotkey (Pause / Scroll Lock) RAM snapshots
- `recorder.py` – timed live recording of candidate vectors
- `autorecord.py` – optional RAM snapshots around room changes (`"record": true`)
- `research/ps2mc.py` – PS2 memory card image reader (used to find the explored-room records in the save)

## Third-person camera

Free third-person camera for **Castlevania: Lament of Innocence** (NTSC-U, SLUS-20733 v1.01,
CRC 28270F7D), prototyped as an external tool that drives PCSX2 over PINE IPC.

### Running

1. PCSX2: Settings > Advanced > enable PINE (slot 28011). Only one PINE client can be
   connected at a time.
2. Start the game and get into gameplay.
3. Run the tool with Python 3.10 or later and numpy:

   ```
   python loi_cam.py
   ```

Controls: the right stick orbits. **R3** switches between the free camera and the original.
**Ctrl+C** quits and puts the original camera back.

Numpad hotkeys work while PCSX2 is the focused window. Every change is saved to
`loi_cam.json`:

| Keys | Adjusts |
|---|---|
| 4 / 6 | turn sensitivity, -/+ 0.1x |
| 8 / 2 | distance, closer/farther by 50 |
| 7 / 1 | angle (camera height above Leon), up/down by 25 |
| 9 / 3 | FoV, wider/narrower (focal length -/+ 15) |
| 5 | reset to defaults |

`--sens`, `--distance`, `--lift` and `--focal` override the saved values for that run. Other
flags: `--invert-x`, `--invert-y`, `--smoothing 14`,
`--verbose`, and `--demo-spin 0.5` (spins the camera with no controller input, for testing).

### How it works

- `0x792880` holds a pointer to the active camera struct (`0x792890` during gameplay). The
  renderer and the follow logic both read the camera through this pointer. The authored rail
  camera writes its eye straight into `0x792890`.
- The tool copies that struct to free RAM (`0x1F80000`) and points `0x792880` at the copy.
  The game keeps updating the copy's follow point (Leon plus head height), but nothing writes
  its eye. Each tick the tool writes an orbit eye and both look-at fields, and the game builds
  the view matrix from them. Nothing races the game, so there is no jitter.
- When the pointer is redirected, the game's camera system stops updating entirely,
  follow point included. So the tool pivots on Leon's actor position (`0xB63430`, static)
  plus 300 units of head height.
- While the tool is running, in either camera mode, the right stick is hidden from the game,
  because otherwise it doubles as the d-pad item/equipment quick menu. The exception is the
  map screen (and the Start menu), which uses the stick to scroll. The tool watches the menu
  flag (`0x738EAC`) and passes the stick through while it is set. Gameplay input is built each frame by
  `FillPlayerInput` (`0x416438`) from the raw stick bytes. The two loads that fetch rx and ry
  (`0x4165D4` and `0x4165B8`) are replaced with constant 0x80, which is centred. Quitting the
  tool restores the original instructions.
- The map screen and the Start/status menu borrow the camera (pointer → `0x792C80`) and set the
  menu flag at `0x738EAC`. The orbit angles are kept, and the camera resumes exactly where it
  was when the menu closes. While the free camera is on, `SetActiveCamera` (`0x65C018`) is
  patched so the game's own "back to gameplay camera" call selects the free camera directly.
  So no frame is ever rendered from the gameplay camera on the way back.
- After any other camera handover (cutscene, event), or when Leon's position jumps more than
  250 units in one tick (a door or room change), the camera swings to directly behind him.
  Leon's facing angle is at `0xB63470`. The swing holds for at most 0.15 s while his facing
  catches up, and lets go as soon as he moves or the right stick is used.
- If the pointer moves to another camera (a cutscene or event camera), the tool steps aside.
  It takes over again when the gameplay camera comes back.
- Pad input is read from libpad's buffers in EE RAM, found by signature at startup, so it
  works with any controller PCSX2 supports.

`loicam/addrs.py` lists every known address and struct offset.

### Tools

- `tools/mipsdis.py <elf> <va> [n]`: a minimal R5900 disassembler.
- `tools/dump.py <addr> [bytes]`: dumps live memory as hex and floats.
- `tools/findvec.py eye | find x y z [tol]`: searches all 32 MB of RAM for a float vector.
- `tools/press.py <button> [sec]`: injects a pad button press, for example `select` for the map.
- `tools/scenario.py map_and_door`: runs the real tool logic through a map open/close and a
  simulated room change on one PINE connection, then reports whether the camera behaved.

Extract `SLUS_207.33` from your own disc into `game/`. That folder is git-ignored.
