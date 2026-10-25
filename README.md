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
arrow shows your position and facing. The map's markers are drawn too: yellow discs for sub-weapon
pickups, red bars for locked doors (gone once the room behind is explored), purple arrows at exits
to another floor, and the "S" start point. Floors and areas switch automatically, also inside rooms that
span two floors, and the overlay hides while a menu, the shop or the map screen is open, and in
rooms the in-game map doesn't cover (like the final boss arena). The line
under the map shows the area, room and floor (e.g. `area 8 room 16 2F`, named like the in-game map).

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
both belong to the camera. On the map screen and in the Start menu it still scrolls as usual, and
while you hold L1 (Orb menu) or L2 (Relic menu) it picks from the menu while the camera holds still.
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
section.

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
| `show_status` | true | area / room / floor line under the map |
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

The wiki explains both tools, with the memory addresses each one uses:

- [How the minimap works](https://github.com/smasimar/loi-tools/wiki/How-the-minimap-works): how
  the maps are decoded from the disc, and how the player and explored rooms are read.
- [How the camera works](https://github.com/smasimar/loi-tools/wiki/How-the-camera-works): how the
  free camera takes over the game's camera, and what it patches in the game.

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
