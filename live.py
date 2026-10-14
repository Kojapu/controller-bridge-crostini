"""live.py - send what the controller is doing from the bridge to the GUI.

The GUI opens a Unix datagram socket (the "mailbox") while its window is open. The bridge
checks about once a second whether the mailbox exists and only then sends updates, at most
SEND_RATE per second and only when something changed. With no GUI open, the bridge sends
nothing, so it costs nothing.

A message is a small JSON object:
    {"buttons": ["B", "ZL"], "left": [dx, dy], "right": [dx, dy]}
where dx/dy are stick positions relative to the calibrated resting point (up/right > 0).
When the bridge stops it sends {"bye": true}.
"""
import json
import os
import socket
import tempfile

SEND_RATE = 30         # updates per second at most; plenty for the eye
CHECK_INTERVAL = 1.0   # how often the bridge looks for a mailbox when it has none


def socket_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, f"controller-bridge-live-{os.getuid()}.sock")


class Sender:
    """Bridge side. Cheap to call on every report: it decides itself when to send."""

    def __init__(self, path=None):
        self.path = path or socket_path()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.active = False      # is a GUI listening?
        self.next_check = 0.0
        self.last_send = 0.0
        self.pending = None      # newest state not sent yet (rate limit)

    def listening(self, now):
        """False when no GUI is open, so the caller can skip preparing an update."""
        return self.active or now >= self.next_check

    def publish(self, now, buttons, left, right):
        self.pending = {"buttons": sorted(buttons), "left": list(left), "right": list(right)}
        self.tick(now)

    def tick(self, now):
        """Send the pending state if a GUI is listening and enough time has passed."""
        if self.pending is None:
            return
        if not self.active:
            if now < self.next_check:
                return
            self.next_check = now + CHECK_INTERVAL
            self.active = os.path.exists(self.path)
            if not self.active:
                return
        if now - self.last_send < 1 / SEND_RATE:
            return
        if self._send(self.pending):
            self.last_send = now
            self.pending = None
        else:
            self.active = False  # GUI closed; look again in a second
            self.next_check = now + CHECK_INTERVAL

    def _send(self, message):
        try:
            self.sock.sendto(json.dumps(message, separators=(",", ":")).encode(), self.path)
            return True
        except OSError:
            return False

    def close(self):
        if self.active:
            self._send({"bye": True})
        self.sock.close()


class Receiver:
    """GUI side. Owns the mailbox while it exists."""

    def __init__(self, path=None):
        self.path = path or socket_path()
        try:
            os.unlink(self.path)  # left over from a GUI that crashed
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(self.path)
        self.sock.setblocking(False)

    def fileno(self):
        return self.sock.fileno()

    def latest(self):
        """Read everything waiting and return only the newest message (or None)."""
        newest = None
        while True:
            try:
                data = self.sock.recv(4096)
            except (BlockingIOError, InterruptedError):
                return newest
            try:
                newest = json.loads(data)
            except ValueError:
                pass  # ignore anything that isn't ours

    def close(self):
        self.sock.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
