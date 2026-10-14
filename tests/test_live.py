"""Tests for the live view: live.py, the bridge sending, and the GUI showing it."""
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

import live
import profiles
from tests.fake_controller import Hold, run_bridge
from tests.test_gui import GuiTest


class TempMailbox(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "live.sock")


class SenderAndReceiver(TempMailbox):
    def test_nothing_is_sent_without_a_gui(self):
        sender = live.Sender(self.path)
        self.addCleanup(sender.close)
        sender.publish(10.0, ["B"], (0, 0), (0, 0))
        self.assertFalse(sender.active)
        # Until the next check a second later, it doesn't even prepare messages.
        self.assertFalse(sender.listening(10.5))
        self.assertTrue(sender.listening(11.0))

    def test_gui_opened_later_is_found_within_a_second(self):
        sender = live.Sender(self.path)
        self.addCleanup(sender.close)
        sender.publish(10.0, [], (0, 0), (0, 0))   # no GUI yet
        receiver = live.Receiver(self.path)
        self.addCleanup(receiver.close)
        sender.publish(10.5, ["A"], (0, 0), (0, 0))
        self.assertIsNone(receiver.latest())       # not checked again yet
        sender.publish(11.0, ["B"], (5, -5), (0, 0))
        self.assertEqual(receiver.latest(), {"buttons": ["B"], "left": [5, -5], "right": [0, 0]})

    def test_rate_limit_keeps_the_newest_and_delivers_it_later(self):
        receiver = live.Receiver(self.path)
        self.addCleanup(receiver.close)
        sender = live.Sender(self.path)
        self.addCleanup(sender.close)
        sender.publish(10.0, ["A"], (0, 0), (0, 0))
        sender.publish(10.01, ["B"], (0, 0), (0, 0))   # too soon: held back
        self.assertEqual(receiver.latest()["buttons"], ["A"])
        sender.tick(10.05)                              # later: the held-back one goes out
        self.assertEqual(receiver.latest()["buttons"], ["B"])

    def test_at_most_send_rate_per_second(self):
        receiver = live.Receiver(self.path)
        self.addCleanup(receiver.close)
        sender = live.Sender(self.path)
        self.addCleanup(sender.close)
        sent = 0
        for i in range(75):                 # one second of reports
            sender.publish(10 + i / 75, [], (i, 0), (0, 0))
            if receiver.latest() is not None:
                sent += 1
        self.assertLessEqual(sent, live.SEND_RATE + 1)
        self.assertGreater(sent, live.SEND_RATE // 2)

    def test_close_says_bye(self):
        receiver = live.Receiver(self.path)
        self.addCleanup(receiver.close)
        sender = live.Sender(self.path)
        sender.publish(10.0, [], (0, 0), (0, 0))
        sender.close()
        self.assertEqual(receiver.latest(), {"bye": True})

    def test_closed_gui_is_noticed(self):
        receiver = live.Receiver(self.path)
        sender = live.Sender(self.path)
        self.addCleanup(sender.close)
        sender.publish(10.0, [], (0, 0), (0, 0))
        receiver.close()
        sender.publish(11.0, [], (0, 0), (0, 0))
        self.assertFalse(sender.active)
        self.assertFalse(os.path.exists(self.path))

    def test_leftover_mailbox_is_replaced_and_junk_ignored(self):
        Path(self.path).touch()             # left by a crashed GUI
        receiver = live.Receiver(self.path)
        self.addCleanup(receiver.close)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(b"not json", self.path)
        s.sendto(json.dumps({"buttons": ["X"]}).encode(), self.path)
        s.close()
        self.assertEqual(receiver.latest(), {"buttons": ["X"]})


class BridgeSends(TempMailbox):
    def test_bridge_reports_buttons_and_sticks(self):
        receiver = live.Receiver(self.path)
        self.addCleanup(receiver.close)
        p = profiles.normalise({"name": "Test"})
        run_bridge(p, [Hold(1.5), Hold(1, buttons=["B"], left=(0, 900))], live_path=self.path)
        messages = []
        while True:
            try:
                messages.append(json.loads(receiver.sock.recv(4096)))
            except BlockingIOError:
                break
        self.assertEqual(messages[-1], {"bye": True})
        pressed = [m for m in messages if m.get("buttons") == ["B"]]
        self.assertTrue(pressed)
        self.assertEqual(pressed[-1]["left"], [0, 900])   # relative to the resting point
        self.assertLessEqual(len(messages), live.SEND_RATE * 2.5 + 2)


class GuiShowsIt(GuiTest):
    def send(self, buttons=(), left=(0, 0), right=(0, 0)):
        sender = live.Sender(self.live_path)
        sender.publish(10.0, list(buttons), left, right)
        sender.sock.close()
        self.root.update()  # lets Tk deliver the update to on_live

    def view(self, stick="left_stick"):
        v = self.app.stick_views[stick]
        return v, v.canvas.itemcget(v.dot, "state"), v.canvas.itemcget(v.dot, "fill")

    def test_pressed_buttons_light_up(self):
        self.send(buttons=["B", "ZL"])
        self.assertEqual(str(self.app.button_labels["B"].cget("style")), "Pressed.TLabel")
        self.assertEqual(str(self.app.button_labels["ZL"].cget("style")), "Pressed.TLabel")
        self.assertNotEqual(str(self.app.button_labels["A"].cget("style")), "Pressed.TLabel")
        self.send(buttons=["A"])
        self.assertNotEqual(str(self.app.button_labels["B"].cget("style")), "Pressed.TLabel")
        self.assertEqual(self.app.lit, {"A"})

    def test_stick_dot_and_dead_zone_colour(self):
        _, state, _ = self.view()
        self.assertEqual(state, "hidden")               # no bridge yet: no dot
        self.send(left=(100, 100))                      # inside the 500 dead zone
        _, state, colour = self.view()
        self.assertEqual((state, colour), ("normal", gui_colour("DOT_IDLE")))
        self.send(left=(0, 900))                        # past it
        self.assertEqual(self.view()[2], gui_colour("DOT_ACTIVE"))
        self.app.deadzone_fields["left_stick"].commit(1000)   # bigger dead zone: inside again
        self.assertEqual(self.view()[2], gui_colour("DOT_IDLE"))

    def test_dot_position(self):
        self.send(left=(0, 1700))                       # straight up, full reach
        v, _, _ = self.view()
        x0, y0, x1, y1 = v.canvas.coords(v.dot)
        self.assertAlmostEqual((x0 + x1) / 2, v.SIZE / 2)
        self.assertAlmostEqual((y0 + y1) / 2, v.SIZE / 2 - v.RADIUS)   # top of the circle

    def test_dead_zone_square_follows_the_slider(self):
        v = self.app.stick_views["left_stick"]
        before = v.canvas.coords(v.dead)
        self.app.deadzone_fields["left_stick"].commit(1000)
        after = v.canvas.coords(v.dead)
        self.assertAlmostEqual(after[2] - after[0], 2 * v.px(1000))
        self.assertGreater(after[2] - after[0], before[2] - before[0])

    def test_full_push_ring_only_with_a_speed_key(self):
        v = self.app.stick_views["left_stick"]
        self.assertEqual(v.canvas.itemcget(v.full, "state"), "hidden")
        self.app.set_value(("left_stick", "speed_key"), "Shift_L")
        self.assertEqual(v.canvas.itemcget(v.full, "state"), "normal")

    def test_bye_and_silence_clear_the_view(self):
        self.send(buttons=["B"], left=(0, 900))
        sender = live.Sender(self.live_path)
        sender.publish(10.0, [], (0, 0), (0, 0))
        sender.close()                                  # bridge stopped
        self.root.update()
        self.assertEqual(self.app.lit, set())
        self.assertEqual(self.view()[1], "hidden")
        self.send(buttons=["B"])
        self.app.live_last -= 5                         # pretend it went quiet long ago
        self.app.check_live()
        self.assertEqual(self.app.lit, set())


def gui_colour(name):
    import gui
    return getattr(gui.StickView, name)


if __name__ == "__main__":
    unittest.main()
