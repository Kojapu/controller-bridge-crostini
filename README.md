# Controller Bridge for Crostini

Turns a wired Switch Pro Controller into keyboard and mouse input for Linux apps (e.g. Steam games) on a Chromebook.

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
python3 bridge.py --profile lego-batman
python3 bridge.py --list        # show profiles
python3 bridge.py --dry-run     # show presses, send nothing
```

Keep the sticks still for the first second (calibration), then click the game window.

## Using the GUI

- **Profile dropdown**: pick a profile. **New / Duplicate / Rename / Delete** manage them.
- **Change a binding**: click it, then press the key. Click again to cancel.
- **▾ menu**: mouse left / middle / right click, or clear.
- **Sticks**: keys (4 directions), mouse, or off.
- **Settings**: stick-to-key threshold, mouse dead zone, mouse speed, stop button.
- **Save**: `*` in the title means unsaved changes.
- **Start / Stop**: runs the bridge. **Dry run** shows presses in the log without sending them.

You can close the GUI while playing. The bridge keeps running.

## Stop

- The profile's stop button (default **Home**), or
- **Stop** in the GUI, or
- **Ctrl+C** in the terminal.

## Profiles

Stored in `profiles/*.json`. Included:

- **LEGO Batman**: matches the game's keyboard controls.
- **Generic PC game**: WASD on the left stick, mouse on the right stick, ZR/ZL = left/right click.

## LEGO Batman

Steam launch options: `PROTON_USE_WINED3D=1 %command%`

| Controller | Action |
|---|---|
| Left stick | Move |
| B | Jump |
| A | Special |
| Y | Action |
| X | Tag |
| R / L | Character toggle up / down |
| Plus | Start / pause |
| Minus | Back |
| D-pad | Menu cursor |

## Troubleshooting

| Problem | Fix |
|---|---|
| "Controller not found" | Switch it on again under Manage USB devices. |
| "Controller isn't sending button data" | Unplug, replug, switch it on again. If it happens after stopping with Home, set Capture as the stop button. |
| "Controller is busy" | Another bridge is already running. Stop it first. |
| Nothing happens in the game | Click the game window so it has focus. |

## Limits

- Games see a keyboard and mouse, not a gamepad. Steam Input and controller menus won't detect it.
- No rumble or motion controls.
- Only one bridge can run at a time.
- While the controller is passed to Linux, ChromeOS can't use it.
