"""A fake Switch Pro Controller and a fake clock, so bridge tests run instantly and
the same way every time, without hardware.

Describe what the "player" does as a timeline of segments, then run the real bridge
loop against it:

    result = run_bridge(profile, [
        Hold(0.5, buttons=["B"]),          # hold B for half a second
        Hold(0.5),                         # then nothing
    ])
    result.held_at(0.25)  ->  {"u"}

Times in results are seconds since the timeline started (after stick calibration).
"""
import contextlib
import errno
import io
from dataclasses import dataclass, field
from unittest import mock

import usb.core

import bridge

CENTRE = 2048
REPORT_INTERVAL = 1 / 75   # the real controller sends ~75 reports per second
CALIBRATION_TIME = 0.5     # idle time before the timeline, for stick calibration

# Byte index and bit for each button in a 0x30 report (matches probe.py).
BUTTON_BITS = {
    "Y": (3, 0x01), "X": (3, 0x02), "B": (3, 0x04), "A": (3, 0x08),
    "R": (3, 0x40), "ZR": (3, 0x80),
    "Minus": (4, 0x01), "Plus": (4, 0x02), "RStick": (4, 0x04), "LStick": (4, 0x08),
    "Home": (4, 0x10), "Capture": (4, 0x20),
    "Down": (5, 0x01), "Up": (5, 0x02), "Right": (5, 0x04), "Left": (5, 0x08),
    "L": (5, 0x40), "ZL": (5, 0x80),
}


@dataclass
class Hold:
    """For `seconds`, hold these buttons and sticks (stick values relative to centre)."""
    seconds: float
    buttons: list = field(default_factory=list)
    left: tuple = (0, 0)
    right: tuple = (0, 0)


@dataclass
class Unplug:
    """The controller is gone: reads fail and it disappears from the USB device list."""
    seconds: float


@dataclass
class Stuck:
    """Connected but sending all-zero packets (the 'empty data' state)."""
    seconds: float


@dataclass
class Replug:
    """Like Unplug, but it comes back at a new USB address (a physical replug)."""
    seconds: float


def make_report(buttons=(), left=(0, 0), right=(0, 0)):
    r = bytearray(64)
    r[0] = 0x30
    for b in buttons:
        i, mask = BUTTON_BITS[b]
        r[i] |= mask
    for offset, (dx, dy) in ((6, left), (9, right)):
        x, y = CENTRE + dx, CENTRE + dy
        r[offset] = x & 0xFF
        r[offset + 1] = (x >> 8) | ((y & 0x0F) << 4)
        r[offset + 2] = y >> 4
    return bytes(r)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeDevice:
    def __init__(self, sim):
        self.sim = sim
        self.bus = 1

    @property
    def address(self):
        return self.sim.address

    def is_kernel_driver_active(self, interface):
        return False

    def reset(self):
        self.sim.resets.append(self.sim.elapsed())


class Simulation:
    def __init__(self, timeline):
        self.clock = FakeClock()
        self.start = self.clock.now + CALIBRATION_TIME
        self.segments = [Hold(CALIBRATION_TIME)] + list(timeline)
        self.end = self.clock.now + sum(s.seconds for s in self.segments)
        self.address = 2
        self.resets = []
        self.changes = []  # (time, set of held bindings) after every change
        self.moves = []    # (time, dx, dy) for every mouse movement
        self.dev = FakeDevice(self)

    def elapsed(self):
        return round(self.clock.now - self.start, 4)

    def current(self):
        t = self.clock.now - (self.start - CALIBRATION_TIME)
        for segment in self.segments:
            if t < segment.seconds:
                return segment
            t -= segment.seconds
        return None

    def gone(self):
        segment = self.current()
        if isinstance(segment, Replug) and self.address == 2:
            self.address = 3  # a replug gives the controller a new address
        return isinstance(segment, (Unplug, Replug))

    # --- replacements for the bridge's USB functions ---
    def read(self, dev, timeout_ms=100):
        self.clock.sleep(REPORT_INTERVAL)
        if self.clock.now >= self.end:
            raise KeyboardInterrupt  # timeline finished: stop like Ctrl+C
        segment = self.current()
        if isinstance(segment, (Unplug, Replug)):
            raise usb.core.USBError("No such device", errno=errno.ENODEV)
        if isinstance(segment, Stuck):
            return bytes(64)
        return make_report(segment.buttons, segment.left, segment.right)

    def find_controller(self):
        if self.clock.now >= self.end:
            raise KeyboardInterrupt
        return None if self.gone() else self.dev

    def controller_address(self):
        return None if self.gone() else (1, self.address)

    # --- what the bridge sends ---
    def held_at(self, t):
        """Bindings held at time t (seconds since the timeline started)."""
        held = set()
        for when, state in self.changes:
            if when > t:
                break
            held = state
        return held

    def presses(self, binding):
        """Times at which `binding` went down."""
        out, before = [], set()
        for when, state in self.changes:
            if binding in state and binding not in before:
                out.append(when)
            before = state
        return out


def run_bridge(profile, timeline):
    """Run the real bridge loop (dry run) against a fake controller. Returns the Simulation."""
    sim = Simulation(timeline)
    real_update = bridge.Output.update
    real_move = bridge.Output.move

    def update(output, wanted):
        changed = real_update(output, wanted)
        if changed:
            sim.changes.append((sim.elapsed(), set(output.held)))
        return changed

    def move(output, dx, dy):
        sim.moves.append((sim.elapsed(), dx, dy))
        real_move(output, dx, dy)

    noop = lambda *args, **kwargs: None
    patches = [
        mock.patch.object(bridge, "time", sim.clock),
        mock.patch.object(bridge, "read", sim.read),
        mock.patch.object(bridge, "handshake", noop),
        mock.patch.object(bridge, "find_controller", sim.find_controller),
        mock.patch.object(bridge, "controller_address", sim.controller_address),
        mock.patch.object(bridge.usb.util, "claim_interface", noop),
        mock.patch.object(bridge.usb.util, "release_interface", noop),
        mock.patch.object(bridge.usb.util, "dispose_resources", noop),
        mock.patch.object(bridge.Output, "update", update),
        mock.patch.object(bridge.Output, "move", move),
    ]
    output = io.StringIO()
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(contextlib.redirect_stdout(output))
        bridge.run(profile, dry_run=True)
    sim.log = output.getvalue()
    return sim
