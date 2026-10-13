#!/usr/bin/env python3
"""bridge.py - turn a wired Switch Pro Controller into keyboard/mouse input for Linux apps.

Reads the controller over raw USB (see probe.py) and injects key presses and mouse
events through X11's XTEST extension into whichever window has focus. What each button
does comes from a profile in profiles/ (edit them with gui.py).

Run:   python3 bridge.py                         (last-used profile)
       python3 bridge.py --profile lego-batman
       python3 bridge.py --list
       python3 bridge.py --dry-run               (print actions, send nothing)

Stop with the profile's stop button (Home by default) or Ctrl+C. If the controller is
unplugged or stops sending data, the bridge releases all keys and waits for it to come back.
Keep the sticks still for the first second: the bridge measures their resting position.
"""
import argparse
import ctypes
import signal
import sys
import time
from pathlib import Path

import usb.backend.libusb1
import usb.core
import usb.util

import profiles
from probe import (INTERFACE, PRODUCT_ID, VENDOR_ID, decode_buttons, decode_stick,
                   handshake, read)

STICK_FULL = 1400          # measured: a full push is ~1400-1650 from centre
CALIBRATION_REPORTS = 20   # ~0.25 s of reports used to find each stick's resting point


class XTest:
    """Minimal wrapper around libX11 + libXtst, loaded with ctypes (no extra packages)."""

    def __init__(self):
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.xtst = ctypes.CDLL("libXtst.so.6")
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XStringToKeysym.restype = ctypes.c_ulong
        self.x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                ctypes.c_int, ctypes.c_ulong]
        self.xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                   ctypes.c_int, ctypes.c_ulong]
        self.xtst.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                           ctypes.c_int, ctypes.c_ulong]
        self.display = self.x11.XOpenDisplay(None)  # uses $DISPLAY (":0" in Crostini)
        if not self.display:
            sys.exit("Couldn't open the X display. Run this from the Crostini terminal.")

    def keycode(self, name):
        sym = self.x11.XStringToKeysym(name.encode())
        return self.x11.XKeysymToKeycode(self.display, sym) if sym else 0

    def key(self, code, down):
        self.xtst.XTestFakeKeyEvent(self.display, code, int(down), 0)

    def button(self, number, down):
        self.xtst.XTestFakeButtonEvent(self.display, number, int(down), 0)

    def move(self, dx, dy):
        self.xtst.XTestFakeRelativeMotionEvent(self.display, dx, dy, 0)

    def flush(self):
        self.x11.XFlush(self.display)

    def close(self):
        self.x11.XCloseDisplay(self.display)


class Output:
    """Holds keys/mouse buttons down or up, only sending changes. Dry-run just prints."""

    def __init__(self, xtest):
        self.x = xtest
        self.held = set()     # bindings currently held down, e.g. {"u", "mouse_left"}
        self.codes = {}       # binding -> X keycode, resolved once at startup

    def resolve(self, binding):
        """Check a binding now, so a typo fails at startup rather than mid-game."""
        if not binding or binding in profiles.MOUSE_BINDINGS or self.x is None:
            return
        code = self.x.keycode(binding)
        if not code:
            sys.exit(f"Unknown key name in profile: {binding!r}")
        self.codes[binding] = code

    def _send(self, binding, down):
        if self.x is None:
            print(f"  {binding:>12} {'down' if down else 'up'}")
        elif binding in profiles.MOUSE_BINDINGS:
            self.x.button(profiles.MOUSE_BINDINGS[binding], down)
        else:
            self.x.key(self.codes[binding], down)

    def update(self, wanted):
        """Make exactly the bindings in `wanted` held down. Returns True if anything changed."""
        changed = False
        for b in self.held - wanted:
            self._send(b, False)
            changed = True
        for b in wanted - self.held:
            self._send(b, True)
            changed = True
        self.held = wanted
        return changed

    def move(self, dx, dy):
        if self.x:
            self.x.move(dx, dy)

    def flush(self):
        if self.x:
            self.x.flush()

    def release_all(self):
        self.update(set())
        self.flush()


def stick_directions(x, y, centre, threshold):
    """Turn a stick position into held directions like {'up', 'left'}."""
    dx, dy = x - centre[0], y - centre[1]
    held = set()
    if dy > threshold: held.add("up")        # up/right increase the raw numbers
    if dy < -threshold: held.add("down")
    if dx > threshold: held.add("right")
    if dx < -threshold: held.add("left")
    return held


def mouse_step(x, y, centre, deadzone, speed):
    """Stick -> mouse movement; speed grows with how far the stick is pushed."""
    def axis(v):
        if abs(v) < deadzone:
            return 0
        frac = min((abs(v) - deadzone) / (STICK_FULL - deadzone), 1.0)
        return round(frac * frac * speed) * (1 if v > 0 else -1)
    # Screen y grows downward, stick y grows upward, so flip it.
    return axis(x - centre[0]), -axis(y - centre[1])


STUCK_AFTER_S = 2.0   # no button data for this long while connected = controller is stuck
WAIT_POLL_S = 1.0     # how often to look for the controller while waiting
WAITING_MESSAGE = ("Waiting for the controller - plug it in and switch it on under "
                   "ChromeOS Settings > Developers > Linux > Manage USB devices...")


class ControllerStuck(Exception):
    """The controller is connected but not sending button data."""


def calibrate(dev, timeout_s=3.0):
    """Average the first few reports to find each stick's resting position."""
    samples = []
    deadline = time.monotonic() + timeout_s
    while len(samples) < CALIBRATION_REPORTS:
        if time.monotonic() > deadline:
            raise ControllerStuck()
        r = read(dev)
        if r is not None and r[0] == 0x30:
            samples.append(decode_stick(*r[6:9]) + decode_stick(*r[9:12]))
    avg = [sum(s[i] for s in samples) / len(samples) for i in range(4)]
    return (avg[0], avg[1]), (avg[2], avg[3])


def controller_address():
    """(bus, device number) of the controller from the kernel's live list, or None."""
    for d in Path("/sys/bus/usb/devices").iterdir():
        try:
            if (int((d / "idVendor").read_text(), 16) == VENDOR_ID
                    and int((d / "idProduct").read_text(), 16) == PRODUCT_ID):
                return int((d / "busnum").read_text()), int((d / "devnum").read_text())
        except (OSError, ValueError):
            continue  # hubs/interfaces without these files, or a device mid-removal
    return None


def find_controller():
    """Open the controller with libusb, or return None if it isn't plugged in.

    Found on this Chromebook: libusb builds its device list once and never hears about
    replugs inside Crostini, so a long-running bridge kept waiting for a controller that
    was already back. The kernel's list in /sys is always current, so check that, and
    only then start a fresh libusb context (pyusb 1.2 caches one in _lib_object).
    """
    if controller_address() is None:
        return None
    usb.backend.libusb1._lib_object = None
    return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID,
                         backend=usb.backend.libusb1.get_backend())


def wait_until(condition, message=None):
    """Check condition() once a second until it returns something truthy, and return that."""
    while not (result := condition()):
        if message:
            print(message, flush=True)
            message = None  # say it once, not every second
        time.sleep(WAIT_POLL_S)
    return result


def run(profile, dry_run=False):
    out = Output(None if dry_run else XTest())
    sticks = {"left": profile["left_stick"], "right": profile["right_stick"]}
    for binding in profile["buttons"].values():
        out.resolve(binding)
    for layer in profile["layers"]:
        for binding in layer["buttons"].values():
            out.resolve(binding)
    for stick in sticks.values():
        if stick["mode"] == "keys":
            for d in profiles.DIRECTIONS:
                out.resolve(stick[d])

    # Precompute what the loop needs so each report costs as little as possible.
    base = profile["buttons"]
    # Each layer as (hold button, full binding table); empty layer bindings fall through.
    layers = [(layer["hold"], {b: layer["buttons"].get(b) or k for b, k in base.items()})
              for layer in profile["layers"]]
    stop = profile["stop_button"]
    s = profile["settings"]
    threshold, deadzone, speed = s["stick_threshold"], s["mouse_deadzone"], s["mouse_speed"]
    mouse_sticks = [name for name, st in sticks.items() if st["mode"] == "mouse"]
    modes = profile["button_modes"]
    turbo_buttons = {b for b, m in modes.items() if m == "turbo"}
    toggle_buttons = {b for b, m in modes.items() if m == "toggle"}
    turbo_half_period = 1 / (2 * s["turbo_rate"])  # key down for half, up for half
    key_sticks = [name for name, st in sticks.items() if st["mode"] == "keys"]
    stuck_count = 0

    def play(dev):
        """Drive one connection. Returns when the stop button is pressed.
        Raises ControllerStuck, or usb.core.USBError if the controller goes away."""
        nonlocal stuck_count
        detached = False
        try:
            if dev.is_kernel_driver_active(INTERFACE):
                dev.detach_kernel_driver(INTERFACE)
                detached = True
            usb.util.claim_interface(dev, INTERFACE)

            print("Handshaking with controller...")
            handshake(dev)
            print("Calibrating sticks - don't touch them...")
            centres = dict(zip(("left", "right"), calibrate(dev)))
            stuck_count = 0
            print(f"Profile '{profile['name']}' running{' (dry run)' if dry_run else ''}. "
                  f"Click the game window. Press {stop} or Ctrl+C to stop.", flush=True)

            last_state = None
            mouse_moving = False
            held_with = {}  # button -> binding it got when pressed (kept until released)
            pressed_at = {}  # button -> time it was pressed (for turbo timing)
            toggled = {}     # toggle button -> binding it is currently holding down
            turbo_running = False
            last_report = time.monotonic()
            while True:
                r = read(dev)
                if r is None or r[0] != 0x30:
                    if time.monotonic() - last_report > STUCK_AFTER_S:
                        raise ControllerStuck()
                    continue
                now = last_report = time.monotonic()
                # Bytes 3-11 hold every button and stick. If they're identical to the last
                # report and nothing is time-based (mouse, turbo), there's nothing to do.
                state = r[3:12]
                if state == last_state and not mouse_moving and not turbo_running:
                    continue
                last_state = state

                pressed = decode_buttons(r)
                if stop in pressed:
                    print(f"{stop} pressed - stopping.")
                    return
                # The first held layer wins; otherwise the normal bindings apply.
                table = next((t for hold, t in layers if hold in pressed), base)
                for b in list(held_with):
                    if b not in pressed:
                        del held_with[b]
                for b in pressed:
                    if b not in held_with:  # just pressed
                        held_with[b] = table.get(b, "")
                        pressed_at[b] = now
                        if b in toggle_buttons and held_with[b]:
                            if b in toggled:
                                del toggled[b]                 # second press: let go
                            else:
                                toggled[b] = held_with[b]      # first press: hold down
                wanted = set(toggled.values())
                turbo_running = False
                for b, k in held_with.items():
                    if not k or b in toggle_buttons:
                        continue
                    if b in turbo_buttons:
                        turbo_running = True
                        if int((now - pressed_at[b]) / turbo_half_period) % 2 == 0:
                            wanted.add(k)
                    else:
                        wanted.add(k)

                positions = {"left": decode_stick(*r[6:9]), "right": decode_stick(*r[9:12])}
                for name in key_sticks:
                    st = sticks[name]
                    for d in stick_directions(*positions[name], centres[name], threshold):
                        if st[d]:
                            wanted.add(st[d])
                changed = out.update(wanted)

                mouse_moving = False
                for name in mouse_sticks:
                    dx, dy = mouse_step(*positions[name], centres[name], deadzone, speed)
                    if dx or dy:
                        out.move(dx, dy)
                        mouse_moving = changed = True
                if changed:
                    out.flush()
        finally:
            out.release_all()  # never leave a key or mouse button stuck down
            # The controller may already be unplugged, so cleanup errors are expected.
            try:
                usb.util.release_interface(dev, INTERFACE)
                if detached:
                    dev.attach_kernel_driver(INTERFACE)
            except usb.core.USBError:
                pass
            usb.util.dispose_resources(dev)

    try:
        while True:
            dev = wait_until(find_controller, WAITING_MESSAGE)
            try:
                play(dev)
                break  # stop button pressed
            except ControllerStuck:
                stuck_count += 1
                if stuck_count == 1:
                    # Tested on this Chromebook: a USB reset keeps the passthrough working.
                    print("The controller isn't sending button data - resetting its USB "
                          "connection...", flush=True)
                    try:
                        dev.reset()
                    except usb.core.USBError:
                        pass
                    usb.util.dispose_resources(dev)
                else:
                    print("Still no button data. Unplug the controller, plug it back in and "
                          "switch it on again under Manage USB devices.", flush=True)
                    # A replug gives the controller a new USB address.
                    old = (dev.bus, dev.address)
                    wait_until(lambda: controller_address() != old)
                    stuck_count = 0
            except usb.core.USBError as e:
                if e.errno == 16:  # EBUSY: someone else has the controller claimed
                    print("The controller is busy - is another bridge already running?")
                    break
                print(f"Controller disconnected ({e.strerror or e}). All keys released.",
                      flush=True)
                time.sleep(WAIT_POLL_S)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        out.release_all()
        if out.x:
            out.x.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", help="profile id (file name without .json)")
    parser.add_argument("--list", action="store_true", help="list profiles and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="print key/mouse actions instead of sending them")
    args = parser.parse_args()
    # Closing the terminal, `kill`, or the GUI's Stop sends SIGTERM; treat it like Ctrl+C.
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    if args.list:
        for pid, name in profiles.list_profiles():
            print(f"{pid:20} {name}")
        return

    profile_id = args.profile or profiles.last_used()
    if not profile_id or not profiles.path_for(profile_id).exists():
        sys.exit(f"Profile {profile_id!r} not found. Available: "
                 + ", ".join(pid for pid, _ in profiles.list_profiles()))
    profile = profiles.load(profile_id)
    problems = profiles.validate(profile)
    if problems:
        sys.exit("Profile has problems:\n  " + "\n  ".join(problems))
    profiles.remember(profile_id)
    run(profile, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
