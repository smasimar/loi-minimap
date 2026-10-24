# loi-tools

Quality-of-life tools for **Castlevania: Lament of Innocence** (NTSC-U, SLUS-20733 v1.01) running
in [PCSX2](https://pcsx2.net), in one script:

- **Minimap overlay**: the game's own map, always on screen, with your position and facing.
  No need to open the map menu.
- **Free third-person camera**: orbit the camera around Leon with the right stick.

Both work from outside the emulator. They read and write the game's memory over PCSX2's PINE
interface, so nothing is installed into PCSX2 or the game.

## Requirements

- Windows, PCSX2 2.x, and the NTSC-U release of LoI (SLUS-20733, CRC 28270F7D) as a disc image.
- Python 3.10 or newer.
- PCSX2 → Settings → Advanced → **Enable PINE** (slot 28011).
- For the minimap, run the game windowed or borderless fullscreen. Exclusive fullscreen hides overlays.

```
pip install -r requirements.txt
```

## Running

Start the game in PCSX2, then:

```
python loi_tools.py
```

Both tools start. The minimap appears while PCSX2 is the active window, and the camera takes
over as soon as you're in gameplay. **Ctrl+C** in the console quits and puts the game's original
camera back.

PCSX2 serves one PINE client at a time, so close other PINE tools first.

| Option | Effect |
|---|---|
| `--no-camera` / `--no-minimap` | run only one of the two |
| `--start-off` | start with the game's original camera (R3 switches to the free camera) |
| `--distance`, `--lift`, `--focal`, `--sens` | set a camera value and save it to `config.json` |
| `--invert-x`, `--invert-y`, `--smoothing 14` | camera options for this run |
| `--slot N` | PINE slot, if not 28011 |
| `--verbose` | log what the camera does |

## Minimap

On first run, the maps are extracted from your LoI disc image into `data/gamemap/`. The ISO is
found through PCSX2's game folders; set `"iso_path"` in `config.json` if it isn't. Only the map
data is kept, not the disc.

It shows the floor you're on, drawn like the in-game map. Explored rooms are blue, save and warp
rooms are red, unexplored rooms are dimmed, and your current room is outlined in yellow. A red
arrow shows your position and facing. Floors and areas switch automatically, and the overlay hides
while a menu or the map screen is open.

**Hotkeys** (hold **Ctrl+Shift**):

| Key | Action |
|---|---|
| M | show / hide |
| Z | whole floor / zoomed in on you |
| + / − (or ↑ / ↓) | bigger / smaller |
| C (or ← / →) | move to another corner of the game picture |
| O | cycle opacity |

## Camera

The right stick orbits the camera around Leon. **R3** switches between the free camera and the
game's own camera.

While the tool runs, neither the right stick nor R3 opens the item/equipment quick menu, because
both belong to the camera. On the map screen and in the Start menu it still scrolls as usual.
Opening the map or a menu doesn't lose the camera's angle. After a cutscene or a door, the camera
swings round behind Leon.

**Hotkeys** (numpad, while PCSX2 is the active window):

| Keys | Adjusts |
|---|---|
| 4 / 6 | turn sensitivity, −/+ 0.1× |
| 8 / 2 | distance, closer / farther by 50 |
| 7 / 1 | angle (camera height above Leon), up / down by 25 |
| 9 / 3 | field of view, wider / narrower |
| 5 | reset to defaults |

## Settings and data

Hotkey changes are saved immediately to **`config.json`**, which has a `minimap` and a `camera`
section. Older separate settings files (`loi_cam.json`, the minimap's own `config.json`) are
migrated automatically.

`data/` holds the extracted maps and a few files the minimap learns as you play. Both
`config.json` and `data/` stay local (git-ignored).

| Minimap setting | Default | |
|---|---|---|
| `corner` | `top-right` | `top-left`, `top-right`, `bottom-right`, `bottom-left` |
| `anchor` | `image` | `image`: the game picture inside PCSX2's black bars, `view`: the whole view |
| `size`, `margin` | 0.32, 0 | width and edge gap, as fractions of the picture width |
| `opacity` | 0.85 | |
| `zoom`, `zoom_factor` | false, 2.5 | zoomed-in minimap centred on you |
| `aspect` | 4/3 | game picture aspect (PCSX2's "Auto 4:3/3:2" shows LoI at 4:3) |
| `hide_when_unfocused` | true | hide while another window is in front |
| `show_status` | true | area / room line under the map |
| `explored` | `game` | `game`: the game's explored flags, `visited`: rooms the overlay saw you in, `all` |
| `show_unexplored` | true | draw unexplored rooms dimmed instead of hiding them |
| `iso_path` | none | your LoI disc image, if PCSX2's game folders don't have it |
| `trace` | false | debugging: log every drawn frame's position data |

## Helper tools

Optional, in `tools/`. Close `loi_tools.py` first, since they need the PINE connection.

- `python tools/pine_info.py`: checks the PCSX2 connection and whether your game version is supported.
- `python tools/extract_maps.py [iso]`: re-extracts the maps into `data/gamemap/`.
- `python tools/restore_game.py`: puts the original camera and controls back if `loi_tools.py` was
  killed without its normal Ctrl+C clean-up (console window closed, crash).

## How it works

### Minimap

- **The maps** come from the disc's `FILEDATA.BIN`, a chain of packages (magic `0x82734927`)
  holding (id, type, offset, size) entries. Each area has a map package with a **room table**
  (type 16) and a **map model** (type 14).
  - **Room table:** one 0x60-byte record per room.
    - `+0x00` game area id
    - `+0x01` room number
    - `+0x02` floor (signed)
    - `+0x08` flags (low bits 2/3 = save/warp room)
    - `+0x1A`/`+0x1E` outline/fill node ids
    - `+0x40` room origin and, at `+0x4C`, rotation in radians
    - `+0x50` scale
  - **Map model:** PS2 VIF packet data. Each node has a vertex pool (int32 x/y/z, map units ×16)
    and a triangle-strip index list (`index = value/4 − 1`, bit 15 = "don't draw").
  - Most areas store the room shapes already placed on the map. Anti-Soul Mysteries Lab stores
    them in room coordinates, and they're placed by origin and rotation.
- **The player** is read live from Leon's actor. Position and facing are in room coordinates and are placed on the
  map with `origin + R(rotation) · local · 1.6`.
- **Explored rooms** come from the game state. It holds one 12-byte record per room, area after
  area; byte +0 is non-zero once the room is explored. The memory card save uses the same layout.
- **Room changes:** the room number switches about 1.3 s before the position does. The arrow
  holds its last position and facing until the new position arrives.

### Camera

- `0x792880` points to the active camera struct (`0x792890` during gameplay). The tool copies that
  struct to free RAM (`0x1F80000`) and points `0x792880` at the copy. It then writes an orbit eye
  and look-at each tick, and the game builds the view from them. Nothing races the game, so there
  is no jitter.
- The orbit pivots on Leon's actor position (`0xB63430`) plus 300 units of head height, because
  the game's follow logic stops while the pointer is redirected.
- **Right stick:** the two loads that fetch rx/ry for `FillPlayerInput` (`0x416438`) are patched to
  the centred value 0x80, and R3 (`0x0400`) is masked out of the held buttons it passes on
  (`0x4164DC`), so the game never sees the right stick or R3. The patch is lifted while the
  map or a menu is open (flag `0x738EAC`). The real stick is read from libpad's buffers, which are
  found by signature at startup.
- **Map and menus:** `SetActiveCamera` (`0x65C018`) is patched so the game's "back to gameplay
  camera" call selects the free camera directly. The orbit resumes where it was when a menu closes.
- **Cutscenes and doors:** if the pointer moves to another camera (cutscene, event), the tool
  steps aside and takes over again when the gameplay camera returns. After a handover or a door,
  it swings behind Leon (facing at `0xB63470`).
- Quitting restores the original instructions and camera pointer.

### Memory addresses (SLUS-20733)

| Address | Type | Meaning |
|---|---|---|
| `0x738E94` | u8 | game area id: 0 HoSR, 1 Anti-Soul Lab, 2 Dark Palace, 3 Garden, 4/6/7 Castle Entrance parts, 5 Pagoda, 8 Theatre |
| `0x738E96` | u8 | room number within the area |
| `0x734E12` | u8 | 1 while the map or pause menu is open |
| `0x738EAC` | u32 | non-zero while the map screen or Start menu is open |
| `0xB63430` | f32×3 | Leon's position (room coordinates); the camera system mirrors it at `0x792A30` |
| `0xB63474` | f32 | Leon's facing, radians (room coordinates); mirrored at `0x792A54` |
| `0x8060B8` | u32 | pointer to the last loaded save copy; the live game state is 0x4690 bytes before it |
| live state + `0xF44` | 12 B × room | per-room records, area after area (74, 55, 57, 65, 4, 55, 6, 10, 58 rooms); byte +0 ≠ 0 = explored |
| `0x792880` | u32 | active camera pointer |
| `0x792890` | 0x1A0 B | gameplay camera struct (`loitools/camera/addrs.py` lists the offsets) |

The memory card save (`BASLUS-20733cvps2us`) holds the same per-room records; its slots are
0x21D0 bytes apart.

## Project layout

```
loi_tools.py          the script to run
loitools/
  pine.py             PINE client shared by both tools (thread-safe)
  config.py           config.json (minimap and camera sections)
  minimap/            overlay, map decoder (gamemap, mapmesh, vif, filedata, iso), PCSX2 window lookup
  camera/             free camera (freecam), orbit math, pad input, code patches, hotkeys, settings
tools/                optional helpers
```
