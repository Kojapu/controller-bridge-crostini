"""Tests for gui.py. The window stays hidden; dialogs are answered automatically.

Skipped when there is no display (e.g. on a server).
"""
import collections
import os
import shutil
import tempfile
import tkinter as tk
import types
import unittest
from pathlib import Path
from unittest import mock

import profiles

import gui

# One hidden Tk application for all tests; each test gets its own window inside it.
# (Creating and destroying several Tk applications makes ttk print theme errors.)
ROOT = None


def setUpModule():
    global ROOT
    if os.environ.get("DISPLAY"):
        try:
            ROOT = tk.Tk()
            ROOT.withdraw()
        except tk.TclError:
            ROOT = None


def tearDownModule():
    if ROOT is not None:
        ROOT.destroy()


class GuiTest(unittest.TestCase):
    def setUp(self):
        if ROOT is None:
            self.skipTest("needs a display")
        # Work on copies of the included profiles in a temporary folder.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name) / "profiles"
        shutil.copytree(profiles.PROFILES_DIR, folder)
        for target, value in [(profiles, ("PROFILES_DIR", folder)),
                              (profiles, ("CONFIG_FILE", Path(tmp.name) / "config.json"))]:
            patcher = mock.patch.object(target, *value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Answer dialogs: askyesno -> self.answer, askstring -> its suggested value.
        self.answer = True
        self.asked = []
        for name, fake in [("askyesno", self.fake_yes_no),
                           ("askyesnocancel", self.fake_yes_no),
                           ("showinfo", lambda title, msg, **k: self.asked.append(msg)),
                           ("showerror", lambda title, msg, **k: self.asked.append(msg))]:
            patcher = mock.patch.object(gui.messagebox, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(gui.simpledialog, "askstring",
                                    lambda *a, **k: k.get("initialvalue"))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.root = tk.Toplevel(ROOT)
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = gui.App(self.root)
        self.app.load_profile("lego-batman")

    def fake_yes_no(self, title, msg, **kwargs):
        self.asked.append(msg)
        return self.answer

    def row(self, button):
        """(binding button, ▾ menu) for a controller button's row."""
        for getter, btn, menu_btn, name in self.app.binding_widgets:
            if name == button:
                return btn, menu_btn
        raise KeyError(button)

    def press_key(self, keysym):
        return self.app.on_key(types.SimpleNamespace(keysym=keysym))


class Bindings(GuiTest):
    def test_click_then_press_a_key(self):
        btn, _ = self.row("B")
        btn.invoke()
        self.assertEqual(btn.cget("text"), "Press a key…")
        self.assertEqual(self.press_key("Tab"), "break")  # Tab is bound, not used to move focus
        self.assertEqual(self.app.profile["buttons"]["B"], "Tab")
        self.assertTrue(self.app.dirty())

    def test_shift_letter_is_stored_lowercase(self):
        self.row("B")[0].invoke()
        self.press_key("Q")
        self.assertEqual(self.app.profile["buttons"]["B"], "q")

    def test_clicking_a_number_box_cancels_capture(self):
        btn, _ = self.row("B")
        btn.invoke()
        self.app.slider_fields["mouse_speed"].box.event_generate("<FocusIn>", when="now")
        self.root.update()
        self.assertIsNone(self.app.capturing)
        self.assertIsNone(self.press_key("7"))  # typing goes to the box, not a binding
        self.assertEqual(self.app.profile["buttons"]["B"], "u")

    def test_reloading_clears_unsaved_flag(self):
        self.app.set_binding("B", "x")
        self.assertTrue(self.app.dirty())
        self.app.load_profile("lego-batman")
        self.assertFalse(self.app.dirty())


class NumberBoxes(GuiTest):
    def type_into(self, field, text):
        field.text.set(text)
        field.commit_text()  # what Enter / leaving the box runs

    def test_typing_updates_profile_and_slider(self):
        field = self.app.deadzone_fields["left_stick"]
        self.type_into(field, "720")
        self.assertEqual(self.app.profile["left_stick"]["deadzone"], 720)
        self.assertEqual(field.slider_var.get(), 720)
        self.assertEqual(self.app.deadzone_labels["left_stick"].cget("text"), "51%")

    def test_clamp_round_and_revert(self):
        field = self.app.deadzone_fields["left_stick"]
        lo, hi = profiles.DEADZONE_RANGE
        self.type_into(field, "5000")
        self.assertEqual(self.app.profile["left_stick"]["deadzone"], hi)
        self.type_into(field, "1")
        self.assertEqual(self.app.profile["left_stick"]["deadzone"], lo)
        self.type_into(field, "333.6")
        self.assertEqual(self.app.profile["left_stick"]["deadzone"], 334)
        self.type_into(field, "abc")
        self.assertEqual(field.text.get(), "334")

    def test_slider_updates_box(self):
        field = self.app.slider_fields["full_push"]
        field.scale.set(900)
        self.root.update()
        self.assertEqual(field.text.get(), "900")
        self.assertEqual(self.app.profile["settings"]["full_push"], 900)

    def test_enter_and_leaving_the_box_apply_the_value(self):
        box = self.app.deadzone_fields["left_stick"].box
        for event in ("<Return>", "<KP_Enter>", "<FocusOut>"):
            self.assertTrue(box.bind(event), event)


class Sticks(GuiTest):
    def test_off_disables_dead_zone(self):
        self.app.mode_vars["left_stick"].set("off")
        self.app.on_mode("left_stick")
        field = self.app.deadzone_fields["left_stick"]
        self.assertTrue(field.scale.instate(["disabled"]))
        self.assertTrue(field.box.instate(["disabled"]))
        self.assertEqual(str(self.app.speed_on_boxes["left_stick"].cget("state")), "disabled")

    def test_dead_zones_are_independent(self):
        self.app.deadzone_fields["right_stick"].commit(800)
        self.assertEqual(self.app.profile["right_stick"]["deadzone"], 800)
        self.assertEqual(self.app.profile["left_stick"]["deadzone"], 500)


class LayersAndModes(GuiTest):
    def test_add_layer_and_fall_through(self):
        self.app.add_layer()
        self.assertEqual(self.app.profile["layers"][0]["hold"], "ZL")
        self.assertEqual(self.row("ZL")[0].cget("text"), "hold: Layer 1")
        self.assertEqual(str(self.row("ZL")[0].cget("state")), "disabled")
        self.assertEqual(self.row("B")[0].cget("text"), "(u)")  # falls through
        self.row("B")[0].invoke()
        self.press_key("1")
        self.assertEqual(self.app.profile["layers"][0]["buttons"]["B"], "1")
        self.assertEqual(self.app.profile["buttons"]["B"], "u")  # normal binding untouched

    def test_asks_before_clearing_a_bound_button(self):
        self.app.add_layer()
        self.answer = False
        self.app.hold_box.set("R")  # R is bound to space
        self.app.on_hold_change(None)
        self.assertEqual(self.app.profile["layers"][0]["hold"], "ZL")
        self.assertEqual(self.app.profile["buttons"]["R"], "space")
        self.answer = True
        self.app.hold_box.set("R")  # the refusal reset the box to ZL; choose R again
        self.app.on_hold_change(None)
        self.assertEqual(self.app.profile["layers"][0]["hold"], "R")
        self.assertEqual(self.app.profile["buttons"]["R"], "")

    def test_stop_button_cannot_be_a_hold_button(self):
        self.app.add_layer()
        self.app.hold_box.set("Home")
        self.app.on_hold_change(None)
        self.assertEqual(self.app.profile["layers"][0]["hold"], "ZL")
        self.assertTrue(self.asked)

    def test_turbo_mode(self):
        self.app.button_mode_vars["B"].set("turbo")
        self.app.set_mode("B")
        self.assertEqual(self.app.profile["button_modes"], {"B": "turbo"})
        self.assertEqual(self.app.mode_labels["B"].cget("text"), "turbo")


class SavingProfiles(GuiTest):
    def test_new_profile_is_saved_and_selected(self):
        self.app.new_profile()
        self.assertEqual(self.app.profile_id, "new-game")
        self.assertTrue(profiles.path_for("new-game").exists())

    def test_save_keeps_changes(self):
        self.app.deadzone_fields["left_stick"].commit(650)
        self.assertTrue(self.app.save_profile())
        self.assertEqual(profiles.load("lego-batman")["left_stick"]["deadzone"], 650)
        self.assertFalse(self.app.dirty())


class Layout(GuiTest):
    def test_no_widgets_overlap(self):
        """Two widgets in the same grid cell draw on top of each other."""
        clashes = []

        def check(widget):
            cells = collections.defaultdict(list)
            for child in widget.grid_slaves():
                info = child.grid_info()
                cells[(int(info["row"]), int(info["column"]))].append(str(child))
            clashes.extend(v for v in cells.values() if len(v) > 1)
            for child in widget.winfo_children():
                check(child)
        check(self.root)
        self.assertEqual(clashes, [])

    def test_fits_a_1080p_screen(self):
        self.root.update_idletasks()
        self.assertLessEqual(self.root.winfo_reqwidth(), 1920)
        self.assertLessEqual(self.root.winfo_reqheight(), 1040)  # room for the title bar


if __name__ == "__main__":
    unittest.main()
