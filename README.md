# Controller Bridge for Crostini

Turns a wired Switch Pro Controller into keyboard and mouse input for Linux apps (e.g. Steam games) in Crostini on a Chromebook.

## Why

Crostini can pass the controller through over USB, but Linux apps can't use it as a gamepad: the container has no `/dev/input`, `hidraw` or `uinput`. This bridge reads the controller over raw USB and sends key presses and mouse movement to the focused window instead.

## Requirements

- Chromebook with Linux (Crostini) turned on
- Wired Nintendo Switch Pro Controller (USB ID `057e:2009`)
- Python 3, `pyusb` and Tkinter:

```bash
sudo apt install python3-usb python3-tk
```

## Before each session

1. Plug in the controller.
2. ChromeOS: **Settings → Developers → Linux → Manage USB devices** → switch on the Pro Controller.

Repeat this after every unplug or restart.

## Start

```bash
python3 gui.py
```

Or without the GUI:

```bash
python3 bridge.py                      # last used profile
python3 bridge.py --profile <name>     # a specific profile
python3 bridge.py --list               # show profiles
python3 bridge.py --dry-run            # show presses, send nothing
```

Keep the sticks still for the first second (calibration), then click the game window.

## Setting up a game

1. Find the game's keyboard controls (usually in its options or controls menu).
2. In the GUI: **New** (or **Duplicate** an existing profile) and name it after the game.
3. Bind each controller button to the matching key.
4. **Save**, tick **Dry run**, **Start**, and check the log.
5. Untick **Dry run**, **Start** again, and click the game window.

## Using the GUI

- **Profile dropdown**: pick a profile. **New / Duplicate / Rename / Delete** manage them.
- **Change a binding**: click it, then press the key. Click again to cancel.
- **▾ menu**: mouse left / middle / right click, clear, or the button's mode:
  - **Normal**: key held while the button is held.
  - **Turbo**: key pressed repeatedly while held (speed: "Turbo presses/second" in Settings).
  - **Toggle**: one press holds the key down, the next press releases it.
  - The mode shows next to the button and applies in every layer.
- **Sticks**: keys (4 directions), mouse, or off. In keys mode a stick can also hold a
  **run/walk key** along with the directions:
  - "pushed fully (run)": e.g. Shift for sprinting.
  - "pushed lightly (walk)": for games that run by default and have a walk key.
  - "Full push at" in Settings sets where light ends and full begins.
- **Dead zone** (in each stick's panel): how far the stick must move before it presses a
  key or moves the mouse. Raise it if a stick drifts on its own; lower it for quicker
  response. Also shown as a percentage of a full push.
- **Number boxes**: every slider has a box next to it. Type a value and press Enter (or click
  elsewhere), or use its arrows. Values outside the allowed range are clamped.
- **Settings**: mouse speed, turbo speed, full push, stop button.
- **Save**: `*` in the title means unsaved changes.
- **Start / Stop**: runs the bridge. **Dry run** shows presses in the log without sending them.

You can close the GUI while playing. The bridge keeps running.

## Layers

A layer is a second set of bindings, active while you hold a chosen button (e.g. ZL).

- **Add layer**: creates one. Pick its hold button under "Layer is active while holding".
- **Editing** dropdown: switch between "Normal bindings" and your layers.
- Bindings shown `(in brackets)` aren't set in the layer and use the normal binding.
- A button pressed before the layer switches keeps its original key until released.
- Sticks always use the normal bindings.
- The hold button sends no key itself.

## Stop

- The profile's stop button (default **Home**), or
- **Stop** in the GUI, or
- **Ctrl+C** in the terminal.

## Profiles

Each profile is a JSON file in `profiles/`. Edit them with the GUI, or by hand. Key names are X11 key names (`a`, `space`, `Return`, `Escape`, `Shift_L`, `Control_L`, `Up`, …); mouse buttons are `mouse_left`, `mouse_middle`, `mouse_right`.

Included:

- **Generic PC game**: WASD on the left stick, mouse on the right stick, ZR/ZL = left/right click.
- **LEGO Batman**: see the example below.

## Example: LEGO Batman: The Videogame

The included `lego-batman` profile matches the game's keyboard controls:

| Controller | Key | Action |
|---|---|---|
| Left stick | W A S D | Move |
| B | U | Jump |
| A | J | Special |
| Y | H | Action |
| X | K | Tag |
| R / L | Space / Left Ctrl | Character toggle up / down |
| Plus | Enter | Start / pause |
| Minus | Esc | Back |
| D-pad | Arrow keys | Menu cursor |

Tip: on an i3 Chromebook with 4 GB RAM, the game (Steam, Proton) ran smoothly with this launch option: `PROTON_USE_WINED3D=1 %command%`

## Troubleshooting

| Problem | Fix |
|---|---|
| "Waiting for the controller" | Switch it on again under Manage USB devices. The bridge continues by itself. |
| "Controller disconnected" | All keys were released. Plug it back in and switch it on again; the bridge continues by itself. |
| "Still no button data" | The automatic USB reset didn't help. Unplug, replug, switch it on again; the bridge continues by itself. If it happens after stopping with Home, set Capture as the stop button. |
| "Controller is busy" | Another bridge is already running. Stop it first. |
| Nothing happens in the game | Click the game window so it has focus. |
| Character or mouse moves on its own | Raise that stick's dead zone. |

## Limits

- Games see a keyboard and mouse, not a gamepad. Steam Input and controller menus won't detect it.
- Only the wired Switch Pro Controller is supported.
- No rumble or motion controls.
- Only one bridge can run at a time.
- While the controller is passed to Linux, ChromeOS can't use it.
- Uses about 5% of one CPU core while running, mostly spent in Crostini's virtual USB layer.

## Files

| File | Purpose |
|---|---|
| `gui.py` | Profile editor and bridge launcher |
| `bridge.py` | The bridge itself |
| `profiles.py` | Loading, saving and checking profiles |
| `probe.py` | Low-level controller test: prints raw button and stick values |
| `profiles/` | One JSON file per game |
