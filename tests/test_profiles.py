"""Tests for profiles.py: loading, saving, checking and converting old profiles."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import profiles


def valid_profile():
    return profiles.normalise({"name": "Test"})


class TempProfilesDir(unittest.TestCase):
    """Point profiles.py at an empty temporary folder for each test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name, value in [("PROFILES_DIR", self.dir / "profiles"),
                            ("CONFIG_FILE", self.dir / "bridge-config.json")]:
            patcher = mock.patch.object(profiles, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class IncludedProfiles(unittest.TestCase):
    def test_included_profiles_are_valid(self):
        ids = [pid for pid, _ in profiles.list_profiles()]
        self.assertIn("lego-batman", ids)
        self.assertIn("generic-pc", ids)
        for pid in ids:
            with self.subTest(profile=pid):
                self.assertEqual(profiles.validate(profiles.load(pid)), [])

    def test_lego_batman_matches_the_games_controls(self):
        p = profiles.load("lego-batman")
        self.assertEqual(p["buttons"]["B"], "u")  # jump
        self.assertEqual(p["buttons"]["A"], "j")  # special
        self.assertEqual(p["buttons"]["Y"], "h")  # action
        self.assertEqual(p["buttons"]["X"], "k")  # tag
        self.assertEqual(p["left_stick"]["mode"], "keys")


class Normalise(unittest.TestCase):
    def test_empty_profile_gets_every_field(self):
        p = profiles.normalise({})
        self.assertEqual(set(p), set(profiles.BLANK_PROFILE))
        self.assertEqual(set(p["buttons"]), set(profiles.BUTTONS))
        self.assertEqual(profiles.validate(p), [])

    def test_old_shared_dead_zones_move_to_the_matching_stick(self):
        old = {"left_stick": {"mode": "keys"}, "right_stick": {"mode": "mouse"},
               "settings": {"stick_threshold": 650, "mouse_deadzone": 300}}
        p = profiles.normalise(old)
        self.assertEqual(p["left_stick"]["deadzone"], 650)
        self.assertEqual(p["right_stick"]["deadzone"], 300)
        self.assertNotIn("stick_threshold", p["settings"])
        self.assertNotIn("mouse_deadzone", p["settings"])

    def test_layer_buttons_are_filled_in(self):
        p = profiles.normalise({"layers": [{"name": "L1", "hold": "ZL", "buttons": {"B": "1"}}]})
        self.assertEqual(set(p["layers"][0]["buttons"]), set(profiles.BUTTONS))
        self.assertEqual(p["layers"][0]["buttons"]["B"], "1")

    def test_normal_button_modes_are_dropped(self):
        p = profiles.normalise({"button_modes": {"B": "normal", "Y": "turbo"}})
        self.assertEqual(p["button_modes"], {"Y": "turbo"})


class Validate(unittest.TestCase):
    def problems_after(self, change):
        p = valid_profile()
        change(p)
        return profiles.validate(p)

    def assertProblem(self, change, text):
        problems = self.problems_after(change)
        self.assertTrue(any(text in pr for pr in problems), f"{text!r} not in {problems}")

    def test_valid_profile_has_no_problems(self):
        self.assertEqual(profiles.validate(valid_profile()), [])

    def test_stop_button_cannot_be_bound(self):
        self.assertProblem(lambda p: p["buttons"].update(Home="x"), "stop button")

    def test_layer_hold_button_rules(self):
        def hold_on_bound_button(p):
            p["buttons"]["B"] = "u"
            p["layers"].append(profiles.blank_layer("L", "B"))
        self.assertProblem(hold_on_bound_button, "hold button")
        self.assertProblem(lambda p: p["layers"].append(profiles.blank_layer("L", "Home")),
                           "already the stop button")

        def same_hold_twice(p):
            p["layers"] += [profiles.blank_layer("A", "ZL"), profiles.blank_layer("B", "ZL")]
        self.assertProblem(same_hold_twice, "another layer")

    def test_bad_modes(self):
        self.assertProblem(lambda p: p["button_modes"].update(B="spin"), "mode must be")
        self.assertProblem(lambda p: p["left_stick"].update(mode="fly"), "mode must be")
        self.assertProblem(lambda p: p["left_stick"].update(speed_key_on="fast"),
                           "speed_key_on")

    def test_dead_zone_range(self):
        lo, hi = profiles.DEADZONE_RANGE
        self.assertProblem(lambda p: p["left_stick"].update(deadzone=lo - 1), "dead zone")
        self.assertProblem(lambda p: p["right_stick"].update(deadzone=hi + 1), "dead zone")
        self.assertEqual(self.problems_after(lambda p: p["left_stick"].update(deadzone=lo)), [])

    def test_full_push_must_exceed_dead_zone_when_speed_key_is_used(self):
        def speed_key_with_big_dead_zone(p):
            p["left_stick"].update(speed_key="Shift_L", deadzone=1150)
        self.assertProblem(speed_key_with_big_dead_zone, "Full push")
        # Without a speed key the full push setting doesn't matter for that stick.
        self.assertEqual(self.problems_after(lambda p: p["left_stick"].update(deadzone=1150)), [])

    def test_settings_must_be_positive(self):
        self.assertProblem(lambda p: p["settings"].update(turbo_rate=0), "positive")


class SaveAndLoad(TempProfilesDir):
    def test_round_trip(self):
        p = valid_profile()
        p["buttons"]["B"] = "space"
        p["layers"].append(profiles.blank_layer("Layer 1", "ZL"))
        p["button_modes"]["B"] = "turbo"
        pid = profiles.save(p)
        self.assertEqual(pid, "test")
        self.assertEqual(profiles.load(pid), p)
        self.assertEqual(profiles.list_profiles(), [("test", "Test")])

    def test_invalid_profile_is_not_saved(self):
        p = valid_profile()
        p["buttons"]["Home"] = "x"
        with self.assertRaises(ValueError):
            profiles.save(p, "bad")
        self.assertFalse(profiles.path_for("bad").exists())

    def test_no_temp_file_left_behind(self):
        profiles.save(valid_profile(), "test")
        self.assertEqual([f.name for f in profiles.PROFILES_DIR.iterdir()], ["test.json"])

    def test_delete(self):
        profiles.save(valid_profile(), "test")
        profiles.delete("test")
        self.assertEqual(profiles.list_profiles(), [])
        profiles.delete("test")  # deleting twice is fine

    def test_unreadable_file_is_listed_not_fatal(self):
        profiles.PROFILES_DIR.mkdir()
        (profiles.PROFILES_DIR / "broken.json").write_text("{not json")
        self.assertEqual(profiles.list_profiles(), [("broken", "broken (unreadable)")])

    def test_remembers_last_profile(self):
        profiles.save(valid_profile(), "a")
        profiles.save(valid_profile(), "b")
        profiles.remember("b")
        self.assertEqual(profiles.last_used(), "b")
        profiles.delete("b")
        self.assertEqual(profiles.last_used(), "a")  # falls back to an existing one


class Slug(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(profiles.slug("LEGO Batman"), "lego-batman")
        self.assertEqual(profiles.slug("  Half-Life 2!! "), "half-life-2")
        self.assertEqual(profiles.slug("???"), "profile")


if __name__ == "__main__":
    unittest.main()
