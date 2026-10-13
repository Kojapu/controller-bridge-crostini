"""profiles.py - load, save, check and list controller mapping profiles.

A profile is one JSON file in the profiles/ folder, for example profiles/lego-batman.json.
A binding is a string: an X11 key name ("u", "space", "Return", "Control_L", "Up"...),
"mouse_left", "mouse_right", "mouse_middle", or "" for nothing.

A profile can have layers: while a layer's hold button is held, its bindings replace the
normal ones. Empty bindings in a layer fall through to the normal binding.

A button can have a mode (in every layer): "turbo" repeats its key while held, "toggle"
holds its key down on one press and releases it on the next.

A stick in "keys" mode can have a speed key, held along with the direction keys either
on a full push (run, e.g. Shift) or on a light push (walk, for games that run by default).
"""
import copy
import json
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PROFILES_DIR = PROJECT_DIR / "profiles"
CONFIG_FILE = PROJECT_DIR / "bridge-config.json"  # remembers the last profile used

# Every button the Switch Pro Controller reports (names match probe.decode_buttons).
BUTTONS = ["A", "B", "X", "Y", "L", "R", "ZL", "ZR", "Plus", "Minus", "Home", "Capture",
           "LStick", "RStick", "Up", "Down", "Left", "Right"]
DIRECTIONS = ["up", "down", "left", "right"]
STICK_MODES = ["keys", "mouse", "off"]
MOUSE_BINDINGS = {"mouse_left": 1, "mouse_middle": 2, "mouse_right": 3}
BUTTON_MODES = ["normal", "turbo", "toggle"]
SPEED_KEY_ON = ["full", "light"]

DEFAULT_SETTINGS = {
    "stick_threshold": 500,  # stick distance before a direction key is pressed (full ~1400)
    "mouse_deadzone": 250,   # stick distance before the mouse starts moving
    "mouse_speed": 20,       # pixels per report (~75 reports/second) at full push
    "turbo_rate": 10,        # turbo presses per second (reports arrive ~75/s, so max ~15)
    "full_push": 1100,       # stick distance counted as a full push (for speed keys)
}

BLANK_PROFILE = {
    "name": "New profile",
    "buttons": {b: "" for b in BUTTONS},
    "left_stick": {"mode": "keys", "up": "w", "down": "s", "left": "a", "right": "d",
                   "speed_key": "", "speed_key_on": "full"},
    "right_stick": {"mode": "mouse", "up": "", "down": "", "left": "", "right": "",
                    "speed_key": "", "speed_key_on": "full"},
    "stop_button": "Home",
    "settings": dict(DEFAULT_SETTINGS),
    "layers": [],
    "button_modes": {},      # e.g. {"B": "turbo"}; buttons not listed are "normal"
}


def blank_layer(name, hold):
    return {"name": name, "hold": hold, "buttons": {b: "" for b in BUTTONS}}


def slug(name):
    """Turn a profile name into a safe file name: 'LEGO Batman' -> 'lego-batman'."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "profile"


def path_for(profile_id):
    return PROFILES_DIR / f"{profile_id}.json"


def list_profiles():
    """Return [(id, display name)] sorted by name."""
    found = []
    for p in sorted(PROFILES_DIR.glob("*.json")):
        try:
            found.append((p.stem, json.loads(p.read_text()).get("name", p.stem)))
        except (OSError, json.JSONDecodeError):
            found.append((p.stem, f"{p.stem} (unreadable)"))
    return sorted(found, key=lambda item: item[1].lower())


def normalise(profile):
    """Fill in anything missing so older or hand-edited files still work."""
    out = copy.deepcopy(BLANK_PROFILE)
    out["name"] = str(profile.get("name", out["name"]))
    out["buttons"].update({b: str(k) for b, k in profile.get("buttons", {}).items()})
    for stick in ("left_stick", "right_stick"):
        out[stick].update(profile.get(stick, {}))
    out["stop_button"] = profile.get("stop_button", out["stop_button"])
    out["settings"].update(profile.get("settings", {}))
    for layer in profile.get("layers", []):
        full = blank_layer(str(layer.get("name", "Layer")), layer.get("hold", ""))
        full["buttons"].update({b: str(k) for b, k in layer.get("buttons", {}).items()})
        out["layers"].append(full)
    out["button_modes"] = {b: m for b, m in profile.get("button_modes", {}).items()
                           if m != "normal"}
    return out


def validate(profile):
    """Return a list of problems (empty if the profile is fine)."""
    problems = []
    for b in profile["buttons"]:
        if b not in BUTTONS:
            problems.append(f"Unknown controller button {b!r}")
    for stick in ("left_stick", "right_stick"):
        if profile[stick]["mode"] not in STICK_MODES:
            problems.append(f"{stick}: mode must be one of {STICK_MODES}")
        if profile[stick]["speed_key_on"] not in SPEED_KEY_ON:
            problems.append(f"{stick}: speed_key_on must be one of {SPEED_KEY_ON}")
    stop = profile["stop_button"]
    if stop not in BUTTONS:
        problems.append(f"Stop button {stop!r} is not a controller button")
    elif profile["buttons"].get(stop):
        problems.append(f"{stop} is the stop button, so it can't also be bound to a key")
    holds = []
    for layer in profile["layers"]:
        name, hold = layer["name"], layer["hold"]
        if hold not in BUTTONS:
            problems.append(f"Layer '{name}': hold button {hold!r} is not a controller button")
            continue
        if hold == stop:
            problems.append(f"Layer '{name}': {hold} is already the stop button")
        if hold in holds:
            problems.append(f"Layer '{name}': {hold} is already another layer's hold button")
        holds.append(hold)
        if profile["buttons"].get(hold) or layer["buttons"].get(hold):
            problems.append(f"Layer '{name}': {hold} is its hold button, so it can't also "
                            "be bound to a key")
        if layer["buttons"].get(stop):
            problems.append(f"Layer '{name}': {stop} is the stop button, so it can't be bound")
        for b in layer["buttons"]:
            if b not in BUTTONS:
                problems.append(f"Layer '{name}': unknown controller button {b!r}")
    for b, mode in profile["button_modes"].items():
        if b not in BUTTONS:
            problems.append(f"Button mode for unknown controller button {b!r}")
        elif mode not in BUTTON_MODES:
            problems.append(f"{b}: mode must be one of {BUTTON_MODES}")
    for key, value in profile["settings"].items():
        if not isinstance(value, (int, float)) or value <= 0:
            problems.append(f"Setting {key} must be a positive number")
    if profile["settings"]["full_push"] <= profile["settings"]["stick_threshold"]:
        problems.append("'Full push at' must be larger than the stick-to-key threshold")
    return problems


def load(profile_id):
    data = json.loads(path_for(profile_id).read_text())
    return normalise(data)


def save(profile, profile_id=None):
    """Write the profile atomically (temp file + rename), so a crash can't corrupt it."""
    problems = validate(profile)
    if problems:
        raise ValueError("; ".join(problems))
    profile_id = profile_id or slug(profile["name"])
    PROFILES_DIR.mkdir(exist_ok=True)
    target = path_for(profile_id)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile, indent=2) + "\n")
    tmp.replace(target)
    return profile_id


def delete(profile_id):
    path_for(profile_id).unlink(missing_ok=True)


def last_used():
    try:
        pid = json.loads(CONFIG_FILE.read_text()).get("last_profile")
        if pid and path_for(pid).exists():
            return pid
    except (OSError, json.JSONDecodeError):
        pass
    profiles = list_profiles()
    return profiles[0][0] if profiles else None


def remember(profile_id):
    try:
        CONFIG_FILE.write_text(json.dumps({"last_profile": profile_id}) + "\n")
    except OSError:
        pass  # remembering is a convenience; never fail because of it
