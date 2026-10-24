---
name: loi-tools
description: Working on loi-tools, the minimap overlay and free third-person camera for Castlevania - Lament of Innocence (SLUS-20733) in PCSX2 over PINE. Use for any change, bug, test run or memory research in this repo - it holds the workflow rules (test, confirm, then commit with README and wiki), how to run and test against the live game, the known memory addresses and game internals, and the research techniques that worked.
---

# loi-tools

One script, `loi_tools.py`, runs both tools against PCSX2 over PINE: a click-through PySide6
**minimap overlay** drawn from the game's own map data, and a **free third-person camera** on the
right stick. Public repo: https://github.com/smasimar/loi-tools (wiki: `loi-tools.wiki.git`).

## Workflow rules

1. **Commit only after the user has tested the change in the game and said it works.** Build it,
   run it for them, then wait. "Works" / "confirmed" from the user is the trigger; never commit
   on your own test results alone. Push when they say "commit and push" / "commit and sync".
2. **Update the README and the wiki in the same step as the commit**, wherever the change affects
   what they describe:
   - README: user-facing behaviour, hotkeys, options, settings table, helper tools.
   - Wiki `How-the-minimap-works` / `How-the-camera-works`: how it works, memory addresses (each
     page lists only the addresses its tool uses; shared ones like Leon's position go in both).
   - Wiki edits: clone `https://github.com/smasimar/loi-tools.wiki.git` into the scratchpad, edit,
     commit, push, then delete the clone. Check the page renders (built-in browser, get_page_text).
3. Commit messages: a short subject, then what was wrong and what changed. End with the
   Co-Authored-By line from the session's attribution reminder.
4. **The repo is public.** Never commit game files (ELF, ISO), `data/` (extracted maps, learned
   play data), `config.json`, or anything personal. `.gitignore` covers these, plus `research/`.
5. Research scripts and RAM snapshots go in the scratchpad, never the repo; delete them when the
   investigation is done. The repo's `research/` folder is the user's own (gitignored) - leave it.
6. Hotkeys for any recorder you give the user: easy to press, e.g. Ctrl+Q / Ctrl+A / Ctrl+Z,
   Ctrl+X to finish (the user asked for these over Ctrl+Shift+digits).

## Running and testing against the live game

- **PINE serves one client at a time.** Before any research script, stop the running tool. A
  `TimeoutError` / "not connected" usually means another client (the tool, a watcher you left
  running, or the user's own copy) holds the connection, or PCSX2 isn't running / is paused.
- Find a copy the user started: `Get-CimInstance Win32_Process -Filter "Name like 'python%'"`.
- **A hard kill (TaskStop, Stop-Process) skips the tool's clean-up** and leaves the game patched.
  Always follow it with `python tools/restore_game.py` (restores code patches and CAM_PTR).
- Start the tool for the user in the background, unbuffered:
  `python -u loi_tools.py --verbose` (run_in_background). Without `-u` the camera's prints are
  block-buffered and the log looks empty. Check the log after a few seconds for
  "LoI tools running" and "engaged".
- The overlay hides while PCSX2 isn't the foreground window, so screenshots taken while another
  window is focused won't show it. Ask the user for a screenshot instead.
- Offline checks: render floors with `QT_QPA_PLATFORM=offscreen` and compare images; the overlay
  module imports `gamemap` lazily (set `overlay.gamemap = gamemap` when calling its methods
  directly in a test).
- Git Bash on Windows: in Python heredocs use raw strings / `os.path.join` for Windows paths
  (`'\f'`, `'\'` + quote break), and prefer the Edit tool for multi-line source edits.

## Layout

```
loi_tools.py            entry point: CameraThread + Qt Overlay on one shared Pine
loitools/pine.py        thread-safe PINE client (RLock, auto-reconnect, batch API)
loitools/config.py      config.json {pine_slot, minimap:{...}, camera:{...}}
loitools/minimap/       overlay.py, gamemap.py (decode/place/render), mapmesh/vif/filedata/iso, window.py
loitools/camera/        freecam.py (FreeCam, App, CameraThread), addrs.py, patches.py, pad.py, orbit.py, hotkeys.py
tools/                  pine_info.py, extract_maps.py, restore_game.py
```

Dependencies: numpy and PySide6 only.

## Memory map (SLUS-20733, CRC 28270F7D)

| Address | Type | Meaning |
|---|---|---|
| `0x738E94` | u8 | game area id (0 HoSR, 1 Anti-Soul Lab, 2 Dark Palace, 3 Garden, 4/6/7 Castle Entrance, 5 Pagoda, 8 Theatre) |
| `0x738E96` | u8 | room number |
| `0x734E12` | u8 | 1 while the map or pause menu is open |
| `0x738EAC` | u32 | non-zero while the map screen, Start menu **or shop** is open |
| `0xB63430` | f32×3 | Leon's position, room coordinates, y = height (negative up) |
| `0xB63474` | f32 | Leon's facing (Y of the rotation vec at `0xB63470`) |
| `0x792A30` / `0x792A54` | | camera-system copies of position/facing - **freeze while CAM_PTR is redirected**; don't use |
| `0x8060B8` | u32 | last loaded save copy; live game state = value + 4 − 0x4690 |
| live state + `0xF44` | 12 B × room | explored records, areas in order (74,55,57,65,4,55,6,10,58); byte 0 ≠ 0 = explored |
| `0x792880` | u32 | CAM_PTR, active camera (`0x792890` gameplay, `0x792C80` map screen) |
| `0x792890` | 0x3F0 B | gameplay camera struct (cameras are 0x3F0 apart) |
| `0x1F80000` | 0x3F0 B | free RAM used for the free camera's copy (nothing else uses it, map open or not) |
| libpad buffers | 8 B | found by signature at startup (`00 73/79 ...` + 12 zero bytes); seen at 0xA09AC4/B00/B80 |

Camera struct offsets: `+0x00` follow point, `+0x18` distance, `+0x20` pitch, `+0x24` yaw,
`+0x30`/`+0x90` look-at, `+0x40` eye, `+0xB0` view, `+0xF0` inverse view (row 3 = eye),
`+0x174`/`+0x178` focal, **`+0x190` u8 following-Leon flag** (1 in gameplay, rewritten every
frame; cutscenes set 0 on the active camera), **`+0x3E0` camera mode** (non-zero: the game builds
the eye from distance/pitch/yaw and stops building the view from the eye).

## Minimap internals

- Maps come from `FILEDATA.BIN` packages (magic `0x82734927`): type 16 room table (0x60-byte
  records: area, room, floor s8, flags, outline/fill node ids, origin xyz + angle at `+0x40`,
  scale at `+0x50`) and type 14 VIF model. Cached as JSON in `data/gamemap/`;
  bump `gamemap.CACHE_VERSION` whenever `decode_world`'s output changes.
- Placement: `map = origin + R(angle) · (x·sx, −z·sz) · 1.6`. **Use the record's exact angle** -
  ~50 rooms aren't quarter turns (Dark Palace at 45°, Pagoda/Castle Entrance odd angles). Quarter
  turns are stored rounded (1.5708): `_cos_sin` snaps within 1e-3 rad. The game never mirrors rooms.
- Rotation safety net (`check_orientation`): walked positions per room in `data/orient.json`; a
  quarter-turn override only when the record clearly fails and another clearly fits.
  `ORIENT_CHECK` in overlay.py forces a re-check when placement logic changes.
- **Rooms on two floors** have one record per floor (Theatre 8:8, 8:16, Garden 3:50, Castle
  Entrance 6:0, 6:1). Keys: first record `"a:r"`, others `"a:r@floor"`; every record has `"key"`.
  `pick_record`: records whose shape contains Leon, then by height: the lower floor is at y = 0,
  the upper one above a switch height (`FLOOR_SWITCH_Y` = -1100 default; `FLOOR_SWITCH_Y_ROOM`
  per room, e.g. 6:0 = -3630, a spiral staircase from 0 at B1F to -7600 at 1F). New odd rooms:
  log Leon's y while the user walks it and ask where the in-game map switches.
  No game "current floor" variable was found (two rounds of floor-labelled snapshots, map open
  and closed); the map screen probably derives it on open.
- Door transitions: room id switches ~1.3 s before the position; hold the last good position and
  facing while the position is unchanged since the switch.
- Overlay attaches only to a window whose title has "Lament of Innocence" **and** whose process
  exe starts with `pcsx2` (a browser tab matched before). Hidden while `0x734E12` or `0x738EAC`.
- Status line (right-aligned): `area A room R 2F` (floor 0 = 1F, −1 = B1F).

## Camera internals

- Engage: copy the gameplay struct's first 0x1A0 bytes into `0x1F80000`, **zero the rest up to
  0x3F0**, point CAM_PTR at it; each tick write eye/look/focal and hold `+0x3E0` = 0. The game
  builds the view from that copy.
- Patches (`patches.py`): rx/ry loads at `0x4165B8`/`0x4165D4` → `0x80`; R3 masked at `0x4164DC`
  (`andi a1,s7,0xffff` → `0xfbff`); `SetActiveCamera` at `0x65C018` rewritten so camera 0 selects
  the free camera. The stick patch is lifted while `0x738EAC` is set or L1/L2 is held (realtime
  Orb/Relic menus); the camera ignores the stick while L1/L2 is held.
- Yield when CAM_PTR moves to another camera (map, events), and **when the active camera's
  `+0x190` byte is 0 (cutscene taking over the free camera's copy)** - leave the pointer, stop
  writing, and re-engage with a fresh copy behind Leon when it's back to 1.
- History of that bug: copying only 0x1A0 bytes left a cutscene's `+0x3E0` mode in the scratch
  tail, which survived save loads (only a reset zeroes it) and froze the free camera.

## Research techniques that worked

- **Hotkey snapshot recorder**: full 32 MB RAM snapshot per keypress, labelled by state; keep
  bytes constant within each label and different between labels. Save snapshots to disk.
- **Label with ground truth, not just binary**: e.g. floor from the room table in ordinary rooms
  plus upper/lower in the special room, across many rooms.
- **Interleave the states** (A, B, A, B...). Taking all A first then all B correlates the label
  with time and floods the result with heap noise.
- Prefer candidates in static data (0x700000-0x800000, `.bss` up to ~0xC00000) over heap
  (0xA3xxxx and up changes per room).
- **Confirm a candidate by writing it**: copy the "bad" value into a working game (one group at a
  time), test, restore.
- **Catch transitions**: a monitor that tests a condition every ~1.5 s and saves the last-good and
  first-bad snapshots seconds apart.
- Rule out code patches by comparing RAM against the ELF's exec segment read from the ISO
  (`loitools.minimap.iso.ISO`, file `SLUS_207.33`).
- Camera checks: redirect CAM_PTR to a copy, nudge the eye a few units, and see whether the
  copy's inverse-view row 3 follows (invisible to the player).
- The user's notes are ground truth but presses can be mislabelled - when one snapshot
  contradicts the rest (e.g. an "upper" one at ground height), ask.
