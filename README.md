# LoI minimap

Always-on minimap overlay for **Castlevania: Lament of Innocence** (NTSC-U, SLUS-20733) running in PCSX2.

It reads the game's memory over PCSX2's PINE interface and draws a transparent,
click-through minimap on top of the PCSX2 game view.

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
- `research/` – earlier experiments (Castlevania Crypt map alignment, map-screen capture, FILEDATA scans)
