#!/usr/bin/env python3
"""probe.py - read a wired Switch Pro Controller over raw USB and print its input.

Step 1 of the Crostini controller bridge. This script only READS the controller;
it does not send keys or mouse events to any app.

Run:   python3 probe.py            (press Ctrl+C to stop)
       python3 probe.py --seconds 5
"""
import argparse
import sys
import time

import usb.core
import usb.util

VENDOR_ID = 0x057E   # Nintendo
PRODUCT_ID = 0x2009  # Switch Pro Controller
INTERFACE = 0
EP_IN = 0x81         # controller -> computer
EP_OUT = 0x01        # computer -> controller
PACKET_SIZE = 64

# Bit masks for the three button bytes in a 0x30 "full input" report.
RIGHT_BUTTONS = {"Y": 0x01, "X": 0x02, "B": 0x04, "A": 0x08, "R": 0x40, "ZR": 0x80}
SHARED_BUTTONS = {"Minus": 0x01, "Plus": 0x02, "RStick": 0x04, "LStick": 0x08,
                  "Home": 0x10, "Capture": 0x20}
LEFT_BUTTONS = {"Down": 0x01, "Up": 0x02, "Right": 0x04, "Left": 0x08, "L": 0x40, "ZL": 0x80}

# Neutral rumble data; required filler in every subcommand packet.
NEUTRAL_RUMBLE = [0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40]

STICK_PRINT_THRESHOLD = 150  # sticks are noisy; only print bigger movements


def send(dev, data):
    """Send one packet to the controller, padded to 64 bytes."""
    dev.write(EP_OUT, bytes(data) + bytes(PACKET_SIZE - len(data)))


def read(dev, timeout_ms=100):
    """Read one packet, or return None if nothing arrived in time."""
    try:
        return bytes(dev.read(EP_IN, PACKET_SIZE, timeout=timeout_ms))
    except usb.core.USBTimeoutError:
        return None


def wait_for(dev, matches, timeout_s=1.0):
    """Read packets until one satisfies matches(packet), skipping input reports."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = read(dev)
        if r is not None and matches(r):
            return r
    return None


def handshake(dev):
    """Wake the controller up so it streams button data over USB.

    Tested on this Chromebook: a fresh controller is silent until it gets
    0x80 0x02 (handshake) and 0x80 0x04 (USB only, no timeout). The status (0x80 0x01)
    and high-speed (0x80 0x03) commands other drivers send aren't needed, and 0x03
    briefly pauses the stream. Once started, it keeps streaming until unplugged.
    """
    send(dev, [0x80, 0x02])
    ok = wait_for(dev, lambda r: r[:2] == b"\x81\x02")
    print(f"  handshake (80 02): {'ok' if ok else 'no reply'}")
    send(dev, [0x80, 0x04])  # no reply packet; the controller just starts streaming
    # Subcommand 0x03 (set input report mode) with argument 0x30 (full input report).
    # The reply is a 0x21 report whose bytes 13-14 are 0x80 0x03 (ack for subcommand 3).
    send(dev, [0x01, 0x00] + NEUTRAL_RUMBLE + [0x03, 0x30])
    ok = wait_for(dev, lambda r: r[0] == 0x21 and r[14] == 0x03)
    print(f"  full input mode (subcommand 03 30): {'ok' if ok else 'no reply'}")


def decode_buttons(r):
    pressed = [n for n, m in RIGHT_BUTTONS.items() if r[3] & m]
    pressed += [n for n, m in SHARED_BUTTONS.items() if r[4] & m]
    pressed += [n for n, m in LEFT_BUTTONS.items() if r[5] & m]
    return pressed


def decode_stick(b0, b1, b2):
    """Each stick is two 12-bit numbers (0..4095) packed into 3 bytes."""
    return b0 | ((b1 & 0x0F) << 8), (b1 >> 4) | (b2 << 4)


def stick_moved(old, new):
    return any(abs(a - b) > STICK_PRINT_THRESHOLD for a, b in zip(old, new))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=0,
                        help="stop after this many seconds (default: run until Ctrl+C)")
    args = parser.parse_args()

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        sys.exit("Controller not found. Is it plugged in and enabled under "
                 "ChromeOS Settings > Developers > Linux > Manage USB devices?")

    detached = False
    try:
        # Linux's own HID driver holds the controller; ask it to let go for now.
        if dev.is_kernel_driver_active(INTERFACE):
            dev.detach_kernel_driver(INTERFACE)
            detached = True
        usb.util.claim_interface(dev, INTERFACE)

        print("Handshaking with controller...")
        handshake(dev)
        print("Ready. Press buttons and move sticks (Ctrl+C to stop).\n")

        last_buttons, last_sticks = None, None
        reports = 0
        deadline = time.monotonic() + args.seconds if args.seconds else None
        while deadline is None or time.monotonic() < deadline:
            r = read(dev)
            if r is None:
                continue
            if r[0] != 0x30:
                print(f"  other report: {r[:12].hex(' ')}")
                continue
            reports += 1
            buttons = decode_buttons(r)
            sticks = decode_stick(*r[6:9]) + decode_stick(*r[9:12])
            if buttons != last_buttons or last_sticks is None or stick_moved(last_sticks, sticks):
                print(f"buttons={buttons or '-'}  "
                      f"L=({sticks[0]:4},{sticks[1]:4})  R=({sticks[2]:4},{sticks[3]:4})")
                last_buttons, last_sticks = buttons, sticks
        print(f"\nReceived {reports} full input reports.")
    except KeyboardInterrupt:
        print("\nStopped.")
    except usb.core.USBError as e:
        sys.exit(f"USB error: {e}")
    finally:
        # Hand the controller back to Linux so things are as they were.
        usb.util.release_interface(dev, INTERFACE)
        if detached:
            try:
                dev.attach_kernel_driver(INTERFACE)
            except usb.core.USBError:
                print("Note: couldn't hand the controller back to Linux; unplug/replug to reset.")


if __name__ == "__main__":
    main()
