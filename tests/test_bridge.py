"""Tests for bridge.py, run against a fake controller and a fake clock (no hardware)."""
import unittest

import bridge
import profiles
from tests.fake_controller import Hold, Replug, Stuck, Unplug, run_bridge


def make_profile(**changes):
    """A profile with a few simple bindings; keyword arguments change top-level parts."""
    p = profiles.normalise({"name": "Test"})
    p["buttons"].update(B="u", A="j", Y="h", ZR="mouse_left")
    p["right_stick"]["mode"] = "mouse"
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(p.get(key), dict):
            p[key].update(value)
        else:
            p[key] = value
    assert profiles.validate(p) == [], profiles.validate(p)
    return p


class StickMaths(unittest.TestCase):
    def test_directions(self):
        c = (2000, 2000)
        self.assertEqual(bridge.stick_directions(2000, 2000, c, 500), set())
        self.assertEqual(bridge.stick_directions(2000, 2600, c, 500), {"up"})
        self.assertEqual(bridge.stick_directions(1400, 1400, c, 500), {"left", "down"})
        self.assertEqual(bridge.stick_directions(2000, 2500, c, 500), set())  # not past it

    def test_mouse_step(self):
        c = (2000, 2000)
        self.assertEqual(bridge.mouse_step(2200, 2000, c, 250, 20), (0, 0))  # inside dead zone
        self.assertEqual(bridge.mouse_step(4000, 2000, c, 250, 20), (20, 0))  # full = max speed
        dx, dy = bridge.mouse_step(2000, 4000, c, 250, 20)
        self.assertEqual((dx, dy), (0, -20))  # stick up = screen up (negative y)
        slow = bridge.mouse_step(2600, 2000, c, 250, 20)[0]
        self.assertTrue(0 < slow < 20)


class Buttons(unittest.TestCase):
    def test_press_and_release(self):
        sim = run_bridge(make_profile(), [Hold(0.5, buttons=["B"]), Hold(0.5)])
        self.assertEqual(sim.held_at(0.25), {"u"})
        self.assertEqual(sim.held_at(0.75), set())

    def test_two_buttons_and_mouse_button(self):
        sim = run_bridge(make_profile(), [Hold(0.5, buttons=["B", "ZR"]), Hold(0.5)])
        self.assertEqual(sim.held_at(0.25), {"u", "mouse_left"})

    def test_unbound_button_sends_nothing(self):
        sim = run_bridge(make_profile(), [Hold(0.5, buttons=["Capture"])])
        self.assertEqual(sim.changes, [])

    def test_stop_button_stops_and_releases_everything(self):
        sim = run_bridge(make_profile(), [Hold(0.5, buttons=["B"]),
                                          Hold(0.5, buttons=["B", "Home"]),
                                          Hold(2, buttons=["A"])])
        self.assertIn("Home pressed - stopping", sim.log)
        self.assertEqual(sim.changes[-1][1], set())
        self.assertLess(sim.changes[-1][0], 0.6)  # stopped right away
        self.assertNotIn("j", set().union(*(s for _, s in sim.changes)))  # A never pressed


class Layers(unittest.TestCase):
    def profile(self):
        layer = profiles.blank_layer("Layer", "ZL")
        layer["buttons"]["B"] = "1"
        return make_profile(layers=[layer])

    def test_layer_binding_and_fall_through(self):
        sim = run_bridge(self.profile(), [Hold(0.5, buttons=["ZL", "B"]),
                                          Hold(0.5, buttons=["ZL", "A"]), Hold(0.5)])
        self.assertEqual(sim.held_at(0.25), {"1"})  # set in the layer
        self.assertEqual(sim.held_at(0.75), {"j"})  # not set: normal binding

    def test_button_keeps_its_key_until_released(self):
        sim = run_bridge(self.profile(), [Hold(0.5, buttons=["B"]),
                                          Hold(0.5, buttons=["B", "ZL"]),  # layer on mid-press
                                          Hold(0.5, buttons=["ZL"]),
                                          Hold(0.5, buttons=["ZL", "B"]),
                                          Hold(0.5, buttons=["B"])])       # layer off mid-press
        self.assertEqual(sim.held_at(0.75), {"u"})
        self.assertEqual(sim.held_at(1.25), set())
        self.assertEqual(sim.held_at(1.75), {"1"})
        self.assertEqual(sim.held_at(2.25), {"1"})

    def test_hold_button_sends_nothing(self):
        sim = run_bridge(self.profile(), [Hold(1, buttons=["ZL"])])
        self.assertEqual(sim.changes, [])


class TurboAndToggle(unittest.TestCase):
    def test_turbo_rate(self):
        for rate in (5, 10, 15):
            with self.subTest(rate=rate):
                p = make_profile(button_modes={"B": "turbo"}, settings={"turbo_rate": rate})
                sim = run_bridge(p, [Hold(1, buttons=["B"]), Hold(0.5)])
                presses = [t for t in sim.presses("u") if t < 1]
                self.assertAlmostEqual(len(presses), rate, delta=1)
                self.assertEqual(sim.held_at(1.25), set())  # released after letting go

    def test_toggle(self):
        p = make_profile(button_modes={"Y": "toggle"})
        sim = run_bridge(p, [Hold(0.3, buttons=["Y"]), Hold(0.5),        # tap: on
                             Hold(0.3, buttons=["Y"]), Hold(0.5),        # tap: off
                             Hold(0.3, buttons=["Y"]), Hold(0.5)])       # tap: on, then stop
        self.assertEqual(sim.held_at(0.6), {"h"})   # still held after letting go
        self.assertEqual(sim.held_at(1.4), set())
        self.assertEqual(sim.held_at(2.2), {"h"})
        self.assertEqual(sim.changes[-1][1], set())  # stopping releases a toggled key


class StickKeys(unittest.TestCase):
    def test_directions_and_diagonals(self):
        sim = run_bridge(make_profile(), [Hold(0.5, left=(0, 900)),
                                          Hold(0.5, left=(-900, -900)), Hold(0.5)])
        self.assertEqual(sim.held_at(0.25), {"w"})
        self.assertEqual(sim.held_at(0.75), {"a", "s"})
        self.assertEqual(sim.held_at(1.25), set())

    def test_per_stick_dead_zone(self):
        small = make_profile(left_stick={"deadzone": 300})
        big = make_profile(left_stick={"deadzone": 800})
        self.assertEqual(run_bridge(small, [Hold(0.5, left=(0, 500))]).held_at(0.25), {"w"})
        self.assertEqual(run_bridge(big, [Hold(0.5, left=(0, 500))]).held_at(0.25), set())

    def test_run_key_on_full_push_with_buffer(self):
        p = make_profile(left_stick={"speed_key": "Shift_L", "speed_key_on": "full"})
        sim = run_bridge(p, [Hold(0.3, left=(0, 700)),    # light
                             Hold(0.3, left=(0, 1300)),   # full
                             Hold(0.3, left=(0, 1050)),   # inside the buffer: stays
                             Hold(0.3, left=(0, 950)),    # below it: run off
                             Hold(0.3, left=(0, 1050))])  # back up from light: not yet
        self.assertEqual(sim.held_at(0.15), {"w"})
        self.assertEqual(sim.held_at(0.45), {"w", "Shift_L"})
        self.assertEqual(sim.held_at(0.75), {"w", "Shift_L"})
        self.assertEqual(sim.held_at(1.05), {"w"})
        self.assertEqual(sim.held_at(1.35), {"w"})

    def test_walk_key_on_light_push(self):
        p = make_profile(left_stick={"speed_key": "Alt_L", "speed_key_on": "light"})
        sim = run_bridge(p, [Hold(0.3, left=(0, 700)), Hold(0.3, left=(0, 1300)),
                             Hold(0.3, left=(0, 300))])
        self.assertEqual(sim.held_at(0.15), {"w", "Alt_L"})
        self.assertEqual(sim.held_at(0.45), {"w"})
        self.assertEqual(sim.held_at(0.75), set())  # inside the dead zone: nothing


class Mouse(unittest.TestCase):
    def test_right_stick_moves_mouse(self):
        sim = run_bridge(make_profile(), [Hold(0.5, right=(1300, 0)), Hold(0.5, right=(0, 1300))])
        right = [m for m in sim.moves if m[0] < 0.5]
        up = [m for m in sim.moves if 0.5 < m[0] < 1]
        self.assertTrue(right and all(dx > 0 and dy == 0 for _, dx, dy in right))
        self.assertTrue(up and all(dy < 0 and dx == 0 for _, dx, dy in up))

    def test_mouse_dead_zone(self):
        p = make_profile(right_stick={"deadzone": 700})
        self.assertEqual(run_bridge(p, [Hold(0.5, right=(500, 0))]).moves, [])
        self.assertNotEqual(run_bridge(p, [Hold(0.5, right=(900, 0))]).moves, [])


class Reconnect(unittest.TestCase):
    def test_unplug_releases_keys_and_reconnects(self):
        sim = run_bridge(make_profile(), [Hold(1, buttons=["B"]), Unplug(2),
                                          Hold(3), Hold(1, buttons=["A"])])
        self.assertEqual(sim.held_at(0.5), {"u"})
        self.assertEqual(sim.held_at(1.5), set())  # released when unplugged
        self.assertIn("Controller disconnected", sim.log)
        self.assertEqual(sim.held_at(6.5), {"j"})  # working again after reconnecting

    def test_stuck_controller_is_reset(self):
        sim = run_bridge(make_profile(), [Stuck(2.5), Hold(4, buttons=["B"])])
        self.assertEqual(len(sim.resets), 1)
        self.assertNotIn("Still no button data", sim.log)
        self.assertEqual(sim.held_at(5), {"u"})

    def test_still_stuck_after_reset_waits_for_replug(self):
        sim = run_bridge(make_profile(), [Stuck(8), Replug(1), Hold(4, buttons=["B"])])
        self.assertEqual(len(sim.resets), 1)
        self.assertIn("Still no button data", sim.log)
        self.assertEqual(sim.held_at(11.5), {"u"})


if __name__ == "__main__":
    unittest.main()
