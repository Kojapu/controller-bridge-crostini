"""Tests for install_shortcut.py (writes into a temporary folder, never your real one)."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gui
import install_shortcut as shortcut


def fields(text):
    lines = text.splitlines()
    assert lines[0] == "[Desktop Entry]"
    return dict(line.split("=", 1) for line in lines[1:] if line)


class Entry(unittest.TestCase):
    def test_points_at_this_project(self):
        f = fields(shortcut.desktop_entry())
        self.assertEqual(f["Type"], "Application")
        self.assertEqual(f["Terminal"], "false")
        self.assertIn(str(shortcut.PROJECT_DIR / "gui.py"), f["Exec"])
        self.assertTrue(Path(f["Icon"]).is_file())
        self.assertTrue(Path(f["Path"], "gui.py").is_file())

    def test_window_class_matches_the_gui(self):
        """Otherwise ChromeOS shows a second, generic icon on the shelf."""
        self.assertEqual(fields(shortcut.desktop_entry())["StartupWMClass"], gui.WINDOW_CLASS)

    def test_tk_really_uses_that_window_class(self):
        if not os.environ.get("DISPLAY"):
            self.skipTest("needs a display")
        import tkinter as tk
        root = tk.Tk(className=gui.WINDOW_CLASS)
        try:
            self.assertEqual(root.winfo_class(), gui.WINDOW_CLASS)
        finally:
            root.destroy()

    def test_icon_is_a_png(self):
        self.assertEqual((shortcut.PROJECT_DIR / "icon.png").read_bytes()[:8],
                         b"\x89PNG\r\n\x1a\n")

    def test_quoting(self):
        self.assertEqual(shortcut.quote("/home/me/a b"), '"/home/me/a b"')
        self.assertEqual(shortcut.quote('q"x'), '"q\\\\"x"')
        self.assertEqual(shortcut.quote("d$x"), '"d\\\\$x"')
        self.assertEqual(shortcut.quote("b\\x"), '"b\\\\\\\\x"')


class InstallAndRemove(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.target = Path(tmp.name, "applications", shortcut.FILE_NAME)

    def run_script(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["install_shortcut.py", *args]), \
                contextlib.redirect_stdout(out):
            shortcut.main()
        return out.getvalue()

    def test_install_then_remove(self):
        self.run_script()
        self.assertEqual(self.target.read_text(), shortcut.desktop_entry())
        self.run_script()  # installing again just updates it
        self.assertIn("Removed", self.run_script("--remove"))
        self.assertFalse(self.target.exists())
        self.assertIn("No shortcut", self.run_script("--remove"))


if __name__ == "__main__":
    unittest.main()
