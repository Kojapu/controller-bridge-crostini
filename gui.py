#!/usr/bin/env python3
"""gui.py - edit controller profiles and start/stop the bridge.

Run:   python3 gui.py

Click a binding, then press the keyboard key it should send. Use the ▾ menu next to it
for mouse buttons or to clear it. The bridge runs as its own process, so you can close
this window while playing and it keeps going (it uses no CPU while idle either way).
"""
import copy
import os
import signal
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import profiles

PROJECT_DIR = profiles.PROJECT_DIR
LOG_FILE = PROJECT_DIR / "bridge.log"

BUTTON_GROUPS = [
    ("Face buttons", [("B", "B (bottom)"), ("A", "A (right)"), ("Y", "Y (left)"),
                      ("X", "X (top)")]),
    ("Shoulders", [("L", "L"), ("R", "R"), ("ZL", "ZL"), ("ZR", "ZR")]),
    ("D-pad", [("Up", "Up"), ("Down", "Down"), ("Left", "Left"), ("Right", "Right")]),
    ("Menu", [("Plus", "Plus"), ("Minus", "Minus"), ("Capture", "Capture"),
              ("Home", "Home")]),
    ("Stick clicks", [("LStick", "Left stick click"), ("RStick", "Right stick click")]),
]
MOUSE_LABELS = {"mouse_left": "Mouse left", "mouse_middle": "Mouse middle",
                "mouse_right": "Mouse right"}
SLIDERS = [("stick_threshold", "Stick → key threshold", 200, 1200),
           ("mouse_deadzone", "Mouse dead zone", 100, 800),
           ("mouse_speed", "Mouse speed", 5, 60)]


def describe(binding):
    return MOUSE_LABELS.get(binding, binding) or "—"


class App:
    def __init__(self, root):
        self.root = root
        self.profile_id = None
        self.profile = None
        self.saved = None          # copy of the profile as last saved, to detect changes
        self.capturing = None      # (setter, widget) while waiting for a key press
        self.proc = None           # running bridge process
        self.log_pos = 0
        self.binding_widgets = []  # (getter, button widget) to refresh labels

        root.title("Controller Bridge")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<KeyPress>", self.on_key)
        self.build()
        self.refresh_profile_list(profiles.last_used())

    # ---------- layout ----------
    def build(self):
        pad = {"padx": 6, "pady": 3}
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Profile:").pack(side="left")
        self.profile_box = ttk.Combobox(top, state="readonly", width=24)
        self.profile_box.pack(side="left", **pad)
        self.profile_box.bind("<<ComboboxSelected>>", self.on_select_profile)
        for text, cmd in [("New", self.new_profile), ("Duplicate", self.duplicate_profile),
                          ("Rename", self.rename_profile), ("Delete", self.delete_profile),
                          ("Save", self.save_profile)]:
            ttk.Button(top, text=text, command=cmd, takefocus=0).pack(side="left", padx=2)

        body = ttk.Frame(self.root, padding=(8, 0))
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="n", padx=(0, 12))
        row = 0
        for title, buttons in BUTTON_GROUPS:
            ttk.Label(left, text=title, font=("TkDefaultFont", 10, "bold")).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
            row += 1
            for name, label in buttons:
                ttk.Label(left, text=label, width=17).grid(row=row, column=0, sticky="w")
                self.binding_row(left, row,
                                 lambda n=name: self.profile["buttons"][n],
                                 lambda v, n=name: self.set_value(("buttons", n), v))
                row += 1

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="n")
        self.mode_vars = {}
        self.direction_widgets = {}
        for stick, title in [("left_stick", "Left stick"), ("right_stick", "Right stick")]:
            frame = ttk.LabelFrame(right, text=title, padding=6)
            frame.pack(fill="x", pady=(8, 4))
            var = tk.StringVar()
            self.mode_vars[stick] = var
            modes = ttk.Frame(frame)
            modes.grid(row=0, column=0, columnspan=3, sticky="w")
            for mode, text in [("keys", "Keys"), ("mouse", "Mouse"), ("off", "Off")]:
                ttk.Radiobutton(modes, text=text, value=mode, variable=var, takefocus=0,
                                command=lambda s=stick: self.on_mode(s)).pack(side="left")
            self.direction_widgets[stick] = []
            for i, d in enumerate(profiles.DIRECTIONS, start=1):
                ttk.Label(frame, text=d.capitalize(), width=8).grid(row=i, column=0, sticky="w")
                widgets = self.binding_row(frame, i,
                                           lambda s=stick, d=d: self.profile[s][d],
                                           lambda v, s=stick, d=d: self.set_value((s, d), v))
                self.direction_widgets[stick].extend(widgets)

        settings = ttk.LabelFrame(right, text="Settings", padding=6)
        settings.pack(fill="x", pady=4)
        self.slider_vars = {}
        for i, (key, text, lo, hi) in enumerate(SLIDERS):
            ttk.Label(settings, text=text).grid(row=i, column=0, sticky="w")
            var = tk.IntVar()
            self.slider_vars[key] = var
            ttk.Scale(settings, from_=lo, to=hi, variable=var, length=150,
                      command=lambda v, k=key: self.on_slider(k)).grid(row=i, column=1, **pad)
            ttk.Label(settings, textvariable=var, width=5).grid(row=i, column=2)
        ttk.Label(settings, text="Stop button").grid(row=3, column=0, sticky="w")
        self.stop_box = ttk.Combobox(settings, state="readonly", width=10,
                                     values=profiles.BUTTONS)
        self.stop_box.grid(row=3, column=1, sticky="w", **pad)
        self.stop_box.bind("<<ComboboxSelected>>",
                           lambda e: self.set_value(("stop_button",), self.stop_box.get()))

        run = ttk.LabelFrame(right, text="Bridge", padding=6)
        run.pack(fill="x", pady=4)
        self.run_button = ttk.Button(run, text="Start", command=self.toggle_bridge, takefocus=0)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.dry_run = tk.BooleanVar()
        ttk.Checkbutton(run, text="Dry run (show presses, send nothing)",
                        variable=self.dry_run, takefocus=0).grid(row=0, column=1, **pad)
        self.status = ttk.Label(run, text="Stopped")
        self.status.grid(row=1, column=0, columnspan=2, sticky="w")
        self.log = tk.Text(run, height=9, width=48, state="disabled", takefocus=0)
        self.log.grid(row=2, column=0, columnspan=2, pady=(4, 0))

    def binding_row(self, parent, row, getter, setter):
        """A clickable binding (click, then press a key) plus a ▾ menu for mouse/clear."""
        btn = ttk.Button(parent, width=14, takefocus=0)
        btn.configure(command=lambda: self.start_capture(setter, btn))
        btn.grid(row=row, column=1, sticky="w", padx=2, pady=1)
        menu_btn = ttk.Menubutton(parent, text="▾", width=2, takefocus=0)
        menu = tk.Menu(menu_btn, tearoff=0)
        for value, label in MOUSE_LABELS.items():
            menu.add_command(label=label, command=lambda v=value: setter(v))
        menu.add_separator()
        menu.add_command(label="Clear", command=lambda: setter(""))
        menu_btn["menu"] = menu
        menu_btn.grid(row=row, column=2, sticky="w")
        self.binding_widgets.append((getter, btn))
        return [btn, menu_btn]

    # ---------- editing ----------
    def set_value(self, path, value):
        target = self.profile
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        self.refresh_fields()

    def start_capture(self, setter, widget):
        if self.capturing:
            old_widget = self.capturing[1]
            self.capturing = None
            self.refresh_fields()
            if old_widget is widget:
                return  # clicking the same binding again cancels
        self.capturing = (setter, widget)
        widget.configure(text="Press a key…")
        self.root.focus_set()  # key presses go to the window, not to a button

    def on_key(self, event):
        if not self.capturing:
            return None
        setter, _ = self.capturing
        self.capturing = None
        key = event.keysym
        if len(key) == 1:
            key = key.lower()  # "A" with Shift held still means the a key
        setter(key)
        return "break"  # don't let Tab/Space/etc. also do their normal job

    def on_mode(self, stick):
        self.set_value((stick, "mode"), self.mode_vars[stick].get())

    def on_slider(self, key):
        self.profile["settings"][key] = int(float(self.slider_vars[key].get()))
        self.slider_vars[key].set(self.profile["settings"][key])
        self.update_title()

    def refresh_fields(self):
        p = self.profile
        for getter, btn in self.binding_widgets:
            if not self.capturing or self.capturing[1] is not btn:
                btn.configure(text=describe(getter()))
        for stick, var in self.mode_vars.items():
            var.set(p[stick]["mode"])
            state = "normal" if p[stick]["mode"] == "keys" else "disabled"
            for w in self.direction_widgets[stick]:
                w.configure(state=state)
        for key, var in self.slider_vars.items():
            var.set(int(p["settings"][key]))
        self.stop_box.set(p["stop_button"])
        self.update_title()

    def dirty(self):
        return self.profile is not None and self.profile != self.saved

    def update_title(self):
        name = self.profile["name"] if self.profile else ""
        self.root.title(f"Controller Bridge — {name}{' *' if self.dirty() else ''}")

    # ---------- profiles ----------
    def refresh_profile_list(self, select_id=None):
        self.profile_list = profiles.list_profiles()
        self.profile_box["values"] = [name for _, name in self.profile_list]
        ids = [pid for pid, _ in self.profile_list]
        if not ids:
            self.load_profile(None)
            return
        self.load_profile(select_id if select_id in ids else ids[0])

    def load_profile(self, profile_id):
        if profile_id is None:
            self.profile_id, self.profile = None, copy.deepcopy(profiles.BLANK_PROFILE)
        else:
            try:
                self.profile = profiles.load(profile_id)
            except (OSError, ValueError) as e:
                messagebox.showerror("Can't load profile", str(e))
                return
            self.profile_id = profile_id
            ids = [pid for pid, _ in self.profile_list]
            self.profile_box.current(ids.index(profile_id))
        self.saved = copy.deepcopy(self.profile)
        self.refresh_fields()

    def confirm_discard(self):
        """Ask about unsaved changes. Returns False if the user cancelled."""
        if not self.dirty():
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes", f"Save changes to '{self.profile['name']}'?")
        if answer is None:
            return False
        return self.save_profile() if answer else True

    def on_select_profile(self, _event):
        new_id = self.profile_list[self.profile_box.current()][0]
        if new_id == self.profile_id:
            return
        if self.confirm_discard():
            self.load_profile(new_id)
        else:
            ids = [pid for pid, _ in self.profile_list]
            self.profile_box.current(ids.index(self.profile_id))

    def unique_id(self, name):
        base = profiles.slug(name)
        existing = {pid for pid, _ in profiles.list_profiles()}
        pid, n = base, 2
        while pid in existing:
            pid, n = f"{base}-{n}", n + 1
        return pid

    def create_from(self, template, default_name):
        if not self.confirm_discard():
            return
        name = simpledialog.askstring("Profile name", "Name for the profile:",
                                      initialvalue=default_name, parent=self.root)
        if not name or not name.strip():
            return
        profile = copy.deepcopy(template)
        profile["name"] = name.strip()
        try:
            pid = profiles.save(profile, self.unique_id(profile["name"]))
        except (OSError, ValueError) as e:
            messagebox.showerror("Can't create profile", str(e))
            return
        self.refresh_profile_list(pid)

    def new_profile(self):
        self.create_from(profiles.BLANK_PROFILE, "New game")

    def duplicate_profile(self):
        self.create_from(self.profile, f"{self.profile['name']} copy")

    def rename_profile(self):
        if self.profile_id is None:
            return
        name = simpledialog.askstring("Rename profile", "New name:",
                                      initialvalue=self.profile["name"], parent=self.root)
        if name and name.strip():
            self.profile["name"] = name.strip()
            self.save_profile()
            self.refresh_profile_list(self.profile_id)

    def delete_profile(self):
        if self.profile_id is None:
            return
        if messagebox.askyesno("Delete profile",
                               f"Delete '{self.profile['name']}'? This can't be undone."):
            profiles.delete(self.profile_id)
            self.saved = self.profile  # nothing left to save
            self.refresh_profile_list()

    def save_profile(self):
        try:
            self.profile_id = profiles.save(self.profile, self.profile_id)
        except (OSError, ValueError) as e:
            messagebox.showerror("Can't save profile", str(e))
            return False
        self.saved = copy.deepcopy(self.profile)
        self.update_title()
        return True

    # ---------- bridge process ----------
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def toggle_bridge(self):
        if self.running():
            self.proc.send_signal(signal.SIGTERM)  # bridge releases keys + controller
            self.status.configure(text="Stopping…")
            return
        if self.profile_id is None:
            messagebox.showinfo("No profile", "Create a profile first.")
            return
        if self.dirty() and not self.save_profile():
            return
        cmd = [sys.executable, "-u", str(PROJECT_DIR / "bridge.py"),
               "--profile", self.profile_id]
        if self.dry_run.get():
            cmd.append("--dry-run")
        with open(LOG_FILE, "w") as log:
            # Own session: closing this window doesn't kill the bridge.
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, cwd=PROJECT_DIR,
                                         start_new_session=True)
        self.log_pos = 0
        self.set_log("")
        self.run_button.configure(text="Stop")
        self.status.configure(text=f"Running '{self.profile['name']}' (pid {self.proc.pid})")
        self.poll_bridge()

    def poll_bridge(self):
        """Show new log lines. Only scheduled while the bridge runs, so idle costs nothing."""
        try:
            with open(LOG_FILE) as log:
                log.seek(self.log_pos)
                new = log.read()
                self.log_pos = log.tell()
            if new:
                self.append_log(new)
        except OSError:
            pass
        if self.running():
            self.root.after(300, self.poll_bridge)
        else:
            self.run_button.configure(text="Start")
            self.status.configure(text=f"Stopped (exit code {self.proc.returncode})")

    def set_log(self, text):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.configure(state="disabled")

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.delete("1.0", "end-200l")  # keep only the last 200 lines
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_close(self):
        if not self.confirm_discard():
            return
        if self.running():
            keep = messagebox.askyesnocancel(
                "Bridge running",
                "Keep the bridge running after this window closes?\n\n"
                "Yes: keep playing (stop it with the controller's stop button).\n"
                "No: stop the bridge now.")
            if keep is None:
                return
            if not keep:
                self.proc.send_signal(signal.SIGTERM)
        self.root.destroy()


def main():
    if not os.environ.get("DISPLAY"):
        sys.exit("No display found. Run this from the Crostini terminal.")
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
