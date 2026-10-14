#!/usr/bin/env python3
"""gui.py - edit controller profiles and start/stop the bridge.

Run:   python3 gui.py

Click a binding, then press the keyboard key it should send. Use the ▾ menu next to it
for mouse buttons, turbo/toggle, or to clear it. While the bridge runs, pressed buttons
light up and each stick panel shows where the stick is (live view, see live.py). "Editing" switches between the normal bindings and
layers (bindings used while a hold button is held). The bridge runs as its own process, so you can close
this window while playing and it keeps going (it uses no CPU while idle either way).
"""
import copy
import os
import signal
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import live
import profiles

PROJECT_DIR = profiles.PROJECT_DIR
# Tk keeps only the first letter capitalised ("ControllerBridge" would become this).
WINDOW_CLASS = "Controllerbridge"
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
SPEED_KEY_LABELS = {"full": "pushed fully (run)", "light": "pushed lightly (walk)"}
STICK_FULL = 1400  # measured: a full push is ~1400-1650 from the resting point
SLIDERS = [("mouse_speed", "Mouse speed", 5, 60),
           ("turbo_rate", "Turbo presses/second", 2, 15),
           ("full_push", "Full push at (run/walk)", 600, 1400)]


class SliderField:
    """A slider plus a number box for the same value; either one can be used.

    Typed numbers apply on Enter, on leaving the box, or via its arrows, and are
    clamped to the allowed range. Text that isn't a number is reverted.
    """

    def __init__(self, parent, lo, hi, length, increment, on_change, on_focus):
        self.lo, self.hi, self.on_change = lo, hi, on_change
        self.current = lo
        self.slider_var = tk.DoubleVar()
        self.text = tk.StringVar()
        self.scale = ttk.Scale(parent, from_=lo, to=hi, variable=self.slider_var,
                               length=length, command=lambda v: self.commit(float(v)))
        self.box = ttk.Spinbox(parent, from_=lo, to=hi, increment=increment, width=6,
                               textvariable=self.text, command=self.commit_text)
        for event in ("<Return>", "<KP_Enter>", "<FocusOut>"):
            self.box.bind(event, self.commit_text)
        self.box.bind("<FocusIn>", lambda e: on_focus())

    def commit_text(self, _event=None):
        try:
            value = float(self.text.get().strip())
        except ValueError:
            self.show(self.current)  # not a number: put the last good value back
            return
        self.commit(value)

    def commit(self, value):
        value = int(round(min(max(value, self.lo), self.hi)))
        changed = value != self.current
        self.show(value)
        if changed:
            self.on_change(value)

    def show(self, value):
        self.current = value
        self.slider_var.set(value)
        self.text.set(str(value))

    def set_enabled(self, enabled):
        flag = ["!disabled"] if enabled else ["disabled"]
        self.scale.state(flag)
        self.box.state(flag)


class StickView:
    """Small live picture of a stick: its reach, the dead zone, the full-push ring and a
    dot for where the stick is now. The dead zone is a square because the bridge checks
    each direction separately."""

    SIZE = 104           # canvas size in pixels
    RADIUS = 48          # pixels for STICK_REACH
    STICK_REACH = 1700   # a little beyond the largest measured push (~1650)
    DOT_IDLE, DOT_ACTIVE, DOT_OFF = "#8a8a8a", "#2a7ae2", "#c4c4c4"

    def __init__(self, parent):
        c = self.canvas = tk.Canvas(parent, width=self.SIZE, height=self.SIZE,
                                    highlightthickness=0, background="white")
        m, r = self.SIZE / 2, self.RADIUS
        c.create_oval(m - r, m - r, m + r, m + r, outline="#b8b8b8")
        c.create_line(m - r, m, m + r, m, fill="#e6e6e6")
        c.create_line(m, m - r, m, m + r, fill="#e6e6e6")
        self.dead = c.create_rectangle(0, 0, 0, 0, fill="#ececec", outline="#a0a0a0",
                                       dash=(2, 2))
        self.full = c.create_oval(0, 0, 0, 0, outline="#d07070", dash=(3, 3))
        self.dot = c.create_oval(0, 0, 0, 0, outline="", state="hidden")
        self.mode, self.deadzone, self.position = "off", 0, None

    def px(self, value):
        return value * self.RADIUS / self.STICK_REACH

    def set_zones(self, mode, deadzone, full_push=None):
        """full_push: draw the run/walk ring at this distance, or None for no ring."""
        self.mode, self.deadzone = mode, deadzone
        m, d = self.SIZE / 2, self.px(deadzone)
        self.canvas.coords(self.dead, m - d, m - d, m + d, m + d)
        self.canvas.itemconfigure(self.dead, state="hidden" if mode == "off" else "normal")
        if full_push:
            f = self.px(full_push)
            self.canvas.coords(self.full, m - f, m - f, m + f, m + f)
        self.canvas.itemconfigure(self.full, state="normal" if full_push else "hidden")
        self.set_position(self.position)

    def set_position(self, position):
        """position: (dx, dy) from the resting point (up/right positive), or None to hide."""
        self.position = position
        if position is None:
            self.canvas.itemconfigure(self.dot, state="hidden")
            return
        dx, dy = position
        m = self.SIZE / 2
        x, y = m + self.px(dx), m - self.px(dy)  # screen y grows downward
        self.canvas.coords(self.dot, x - 5, y - 5, x + 5, y + 5)
        if self.mode == "off":
            colour = self.DOT_OFF
        elif abs(dx) < self.deadzone and abs(dy) < self.deadzone:
            colour = self.DOT_IDLE      # inside the dead zone: nothing happens
        else:
            colour = self.DOT_ACTIVE
        self.canvas.itemconfigure(self.dot, state="normal", fill=colour)


def describe(binding):
    return MOUSE_LABELS.get(binding, binding) or "—"


class App:
    def __init__(self, root, live_path=None):
        self.root = root
        self.profile_id = None
        self.profile = None
        self.saved = None          # copy of the profile as last saved, to detect changes
        self.capturing = None      # (setter, widget) while waiting for a key press
        self.proc = None           # running bridge process
        self.log_pos = 0
        self.binding_widgets = []  # (getter, button, ▾ menu, controller button or None)
        self.editing = None        # index of the layer being edited, None = normal bindings
        self.layer_only_widgets = []
        self.button_mode_vars = {}  # controller button -> StringVar for its ▾ menu
        self.mode_labels = {}       # controller button -> "turbo"/"toggle" label
        self.speed_on_boxes = {}    # stick -> "held when" combobox
        self.deadzone_fields = {}   # stick -> SliderField for its dead zone
        self.deadzone_labels = {}   # stick -> "35%" label
        self.button_labels = {}     # controller button -> its name label (lights up)
        self.stick_views = {}       # stick -> StickView
        self.lit = set()            # buttons currently shown as pressed
        self.receiver = None        # live view mailbox
        self.live_last = 0.0        # when the last live update arrived
        self.live_timer = None

        root.title("Controller Bridge")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<KeyPress>", self.on_key)
        style = ttk.Style(root)
        style.configure("Pressed.TLabel", background="#2a7ae2", foreground="white")
        self.build()
        self.refresh_profile_list(profiles.last_used())
        self.start_live(live_path)

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
        layer_bar = ttk.Frame(left)
        layer_bar.grid(row=0, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(layer_bar, text="Editing:").pack(side="left")
        self.layer_box = ttk.Combobox(layer_bar, state="readonly", width=16)
        self.layer_box.pack(side="left", padx=4)
        self.layer_box.bind("<<ComboboxSelected>>", self.on_select_layer)
        for text, cmd in [("Add layer", self.add_layer), ("Rename", self.rename_layer),
                          ("Remove", self.remove_layer)]:
            b = ttk.Button(layer_bar, text=text, command=cmd, takefocus=0)
            b.pack(side="left", padx=1)
            if text != "Add layer":
                self.layer_only_widgets.append(b)
        hold_bar = ttk.Frame(left)
        hold_bar.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Label(hold_bar, text="Layer is active while holding:").pack(side="left")
        self.hold_box = ttk.Combobox(hold_bar, state="readonly", width=10,
                                     values=profiles.BUTTONS)
        self.hold_box.pack(side="left", padx=4)
        self.hold_box.bind("<<ComboboxSelected>>", self.on_hold_change)
        self.layer_only_widgets.append(self.hold_box)
        self.layer_hint = ttk.Label(left, foreground="gray")
        self.layer_hint.grid(row=2, column=0, columnspan=3, sticky="w")
        row = 3
        for title, buttons in BUTTON_GROUPS:
            ttk.Label(left, text=title, font=("TkDefaultFont", 10, "bold")).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
            row += 1
            for name, label in buttons:
                name_label = ttk.Label(left, text=label, width=17)
                name_label.grid(row=row, column=0, sticky="w")
                self.button_labels[name] = name_label
                self.binding_row(left, row,
                                 lambda n=name: self.current_buttons()[n],
                                 lambda v, n=name: self.set_binding(n, v), button=name)
                row += 1

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="n")
        self.mode_vars = {}
        self.direction_widgets = {}
        sticks_row = ttk.Frame(right)  # side by side, so the window fits a 1080p screen
        sticks_row.pack(fill="x", pady=(8, 4))
        for stick, title in [("left_stick", "Left stick"), ("right_stick", "Right stick")]:
            frame = ttk.LabelFrame(sticks_row, text=title, padding=6)
            frame.pack(side="left", fill="y", padx=(0, 6))
            view = StickView(frame)
            view.canvas.grid(row=0, column=4, rowspan=8, sticky="n", padx=(8, 0))
            self.stick_views[stick] = view
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
            ttk.Label(frame, text="Run/walk key", width=12).grid(row=5, column=0, sticky="w")
            widgets = self.binding_row(frame, 5,
                                       lambda s=stick: self.profile[s]["speed_key"],
                                       lambda v, s=stick: self.set_value((s, "speed_key"), v))
            self.direction_widgets[stick].extend(widgets)
            ttk.Label(frame, text="Held when", width=12).grid(row=6, column=0, sticky="w")
            box = ttk.Combobox(frame, state="readonly", width=17,
                               values=list(SPEED_KEY_LABELS.values()))
            box.grid(row=6, column=1, columnspan=2, sticky="w", padx=2, pady=1)
            box.bind("<<ComboboxSelected>>", lambda e, s=stick: self.on_speed_on(s))
            self.speed_on_boxes[stick] = box
            # Dead zone: applies in keys and mouse mode, so it isn't part of direction_widgets.
            ttk.Label(frame, text="Dead zone", width=12).grid(row=7, column=0, sticky="w")
            cell = ttk.Frame(frame)
            cell.grid(row=7, column=1, columnspan=3, sticky="w", pady=(4, 1))
            field = SliderField(cell, *profiles.DEADZONE_RANGE, length=110, increment=10,
                                on_change=lambda v, s=stick: self.on_deadzone(s, v),
                                on_focus=self.cancel_capture)
            field.scale.pack(side="left", padx=2)
            field.box.pack(side="left", padx=2)
            self.deadzone_fields[stick] = field
            label = ttk.Label(cell, width=5, foreground="gray")
            label.pack(side="left")
            self.deadzone_labels[stick] = label

        settings = ttk.LabelFrame(right, text="Settings", padding=6)
        settings.pack(fill="x", pady=4)
        self.slider_fields = {}
        for i, (key, text, lo, hi) in enumerate(SLIDERS):
            ttk.Label(settings, text=text).grid(row=i, column=0, sticky="w")
            field = SliderField(settings, lo, hi, length=150, increment=1 if hi <= 100 else 10,
                                on_change=lambda v, k=key: self.on_slider(k, v),
                                on_focus=self.cancel_capture)
            field.scale.grid(row=i, column=1, **pad)
            field.box.grid(row=i, column=2)
            self.slider_fields[key] = field
        stop_row = len(SLIDERS)  # below the sliders, however many there are
        ttk.Label(settings, text="Stop button").grid(row=stop_row, column=0, sticky="w")
        self.stop_box = ttk.Combobox(settings, state="readonly", width=10,
                                     values=profiles.BUTTONS)
        self.stop_box.grid(row=stop_row, column=1, sticky="w", **pad)
        self.stop_box.bind("<<ComboboxSelected>>", self.on_stop_change)

        run = ttk.LabelFrame(right, text="Bridge", padding=6)
        run.pack(fill="x", pady=4)
        self.run_button = ttk.Button(run, text="Start", command=self.toggle_bridge, takefocus=0)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.dry_run = tk.BooleanVar()
        ttk.Checkbutton(run, text="Dry run (show presses, send nothing)",
                        variable=self.dry_run, takefocus=0).grid(row=0, column=1, **pad)
        self.status = ttk.Label(run, text="Stopped")
        self.status.grid(row=1, column=0, columnspan=2, sticky="w")
        self.log = tk.Text(run, height=9, width=62, state="disabled", takefocus=0)
        self.log.grid(row=2, column=0, columnspan=2, pady=(4, 0))

    def binding_row(self, parent, row, getter, setter, button=None):
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
        if button:  # controller buttons (not stick directions) can be turbo/toggle
            menu.add_separator()
            var = tk.StringVar(value="normal")
            self.button_mode_vars[button] = var
            for mode in profiles.BUTTON_MODES:
                menu.add_radiobutton(label=mode.capitalize(), value=mode, variable=var,
                                     command=lambda b=button: self.set_mode(b))
            mode_label = ttk.Label(parent, width=6, foreground="gray")
            mode_label.grid(row=row, column=3, sticky="w")
            self.mode_labels[button] = mode_label
        menu_btn["menu"] = menu
        menu_btn.grid(row=row, column=2, sticky="w")
        self.binding_widgets.append((getter, btn, menu_btn, button))
        return [btn, menu_btn]

    # ---------- editing ----------
    def set_value(self, path, value):
        target = self.profile
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        self.refresh_fields()

    def current_buttons(self):
        if self.editing is None:
            return self.profile["buttons"]
        return self.profile["layers"][self.editing]["buttons"]

    def set_binding(self, button, value):
        self.current_buttons()[button] = value
        self.refresh_fields()

    def confirm_free(self, button, role):
        """Ask before a bound button becomes a stop/hold button and loses its bindings."""
        bound = [describe(self.profile["buttons"][button])] if self.profile["buttons"][button] else []
        bound += [describe(l["buttons"][button]) for l in self.profile["layers"]
                  if l["buttons"][button]]
        if not bound:
            return True
        return messagebox.askyesno(
            "Button is in use", f"{button} is bound to {', '.join(bound)}.\n\n"
            f"Make it the {role} and clear those bindings?")

    def set_mode(self, button):
        mode = self.button_mode_vars[button].get()
        if mode == "normal":
            self.profile["button_modes"].pop(button, None)
        else:
            self.profile["button_modes"][button] = mode
        self.refresh_fields()

    def free_button(self, button):
        """A stop or hold button can't send keys, so clear its bindings everywhere."""
        self.profile["button_modes"].pop(button, None)
        self.profile["buttons"][button] = ""
        for layer in self.profile["layers"]:
            layer["buttons"][button] = ""

    def on_stop_change(self, _event):
        stop = self.stop_box.get()
        if any(layer["hold"] == stop for layer in self.profile["layers"]):
            messagebox.showinfo("Button in use", f"{stop} is a layer's hold button.")
            self.refresh_fields()
            return
        if not self.confirm_free(stop, "stop button"):
            self.refresh_fields()
            return
        self.free_button(stop)
        self.set_value(("stop_button",), stop)

    # ---------- layers ----------
    def on_select_layer(self, _event):
        i = self.layer_box.current()
        self.editing = None if i == 0 else i - 1
        self.refresh_fields()

    def free_hold_choices(self, exclude_layer=None):
        used = {self.profile["stop_button"]}
        used |= {l["hold"] for i, l in enumerate(self.profile["layers"]) if i != exclude_layer}
        return [b for b in profiles.BUTTONS if b not in used]

    def add_layer(self):
        free = self.free_hold_choices()
        if not free:
            return
        hold = "ZL" if "ZL" in free else free[0]
        name = simpledialog.askstring("Add layer", f"Name for the layer (hold {hold} to use "
                                      "it; you can change the button after):",
                                      initialvalue=f"Layer {len(self.profile['layers']) + 1}",
                                      parent=self.root)
        if not name or not name.strip() or not self.confirm_free(hold, "hold button"):
            return
        self.free_button(hold)
        self.profile["layers"].append(profiles.blank_layer(name.strip(), hold))
        self.editing = len(self.profile["layers"]) - 1
        self.refresh_fields()

    def rename_layer(self):
        if self.editing is None:
            return
        layer = self.profile["layers"][self.editing]
        name = simpledialog.askstring("Rename layer", "New name:", initialvalue=layer["name"],
                                      parent=self.root)
        if name and name.strip():
            layer["name"] = name.strip()
            self.refresh_fields()

    def remove_layer(self):
        if self.editing is None:
            return
        layer = self.profile["layers"][self.editing]
        if messagebox.askyesno("Remove layer", f"Remove layer '{layer['name']}'?"):
            del self.profile["layers"][self.editing]
            self.editing = None
            self.refresh_fields()

    def on_hold_change(self, _event):
        hold = self.hold_box.get()
        if hold not in self.free_hold_choices(exclude_layer=self.editing):
            messagebox.showinfo("Button in use",
                                f"{hold} is already the stop button or another layer's.")
        elif self.confirm_free(hold, "hold button"):
            self.free_button(hold)
            self.profile["layers"][self.editing]["hold"] = hold
        self.refresh_fields()

    def display(self, getter, button):
        """Text and state for a binding widget."""
        p = self.profile
        if button is None:  # stick direction: state comes from the stick mode
            return describe(getter()), None
        if button == p["stop_button"]:
            return "stop button", "disabled"
        for layer in p["layers"]:
            if layer["hold"] == button:
                return f"hold: {layer['name']}", "disabled"
        value = getter()
        if self.editing is not None and not value and p["buttons"][button]:
            return f"({describe(p['buttons'][button])})", "normal"  # falls through
        return describe(value), "normal"

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

    def on_speed_on(self, stick):
        label = self.speed_on_boxes[stick].get()
        value = next(k for k, v in SPEED_KEY_LABELS.items() if v == label)
        self.set_value((stick, "speed_key_on"), value)

    def on_mode(self, stick):
        self.set_value((stick, "mode"), self.mode_vars[stick].get())

    def on_deadzone(self, stick, value):
        self.profile[stick]["deadzone"] = value
        self.show_deadzone(stick)
        self.update_title()

    def show_deadzone(self, stick):
        value = self.profile[stick]["deadzone"]
        self.deadzone_fields[stick].show(value)
        self.deadzone_labels[stick].configure(text=f"{value * 100 // STICK_FULL}%")
        self.show_stick_zones(stick)

    def show_stick_zones(self, stick):
        st = self.profile[stick]
        has_speed_key = st["mode"] == "keys" and st["speed_key"]
        self.stick_views[stick].set_zones(
            st["mode"], st["deadzone"],
            self.profile["settings"]["full_push"] if has_speed_key else None)

    def on_slider(self, key, value):
        self.profile["settings"][key] = value
        for stick in self.stick_views:
            self.show_stick_zones(stick)
        self.update_title()

    def cancel_capture(self):
        """Typing into a number box must not end up as a key binding."""
        if self.capturing:
            self.capturing = None
            self.refresh_fields()

    def refresh_fields(self):
        p = self.profile
        if self.editing is not None and self.editing >= len(p["layers"]):
            self.editing = None
        self.layer_box["values"] = ["Normal bindings"] + [
            f"{l['name']} (hold {l['hold']})" for l in p["layers"]]
        self.layer_box.current(0 if self.editing is None else self.editing + 1)
        in_layer = self.editing is not None
        for w in self.layer_only_widgets:
            w.configure(state=("readonly" if w is self.hold_box else "normal")
                        if in_layer else "disabled")
        self.hold_box.set(p["layers"][self.editing]["hold"] if in_layer else "")
        self.layer_hint.configure(text="(in brackets) = not set in this layer, uses the normal binding"
                                  if in_layer else "")
        for getter, btn, menu_btn, button in self.binding_widgets:
            text, state = self.display(getter, button)
            if not self.capturing or self.capturing[1] is not btn:
                btn.configure(text=text)
            if state:
                btn.configure(state=state)
                menu_btn.configure(state=state)
            if button:
                mode = p["button_modes"].get(button, "normal")
                self.button_mode_vars[button].set(mode)
                self.mode_labels[button].configure(text="" if mode == "normal" else mode)
        for stick, var in self.mode_vars.items():
            var.set(p[stick]["mode"])
            state = "normal" if p[stick]["mode"] == "keys" else "disabled"
            for w in self.direction_widgets[stick]:
                w.configure(state=state)
            box = self.speed_on_boxes[stick]
            box.set(SPEED_KEY_LABELS[p[stick]["speed_key_on"]])
            box.configure(state="readonly" if state == "normal" else "disabled")
            self.show_deadzone(stick)
            self.deadzone_fields[stick].set_enabled(p[stick]["mode"] != "off")
        for key, field in self.slider_fields.items():
            field.show(int(p["settings"][key]))
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
        self.editing = None
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

    # ---------- live view ----------
    def start_live(self, path=None):
        try:
            self.receiver = live.Receiver(path)
        except OSError as e:
            self.append_log(f"Live view unavailable: {e}\n")
            return
        # Tk calls on_live when an update arrives, so there's no polling.
        self.root.tk.createfilehandler(self.receiver.sock, tk.READABLE, self.on_live)

    def stop_live(self):
        if self.receiver:
            self.root.tk.deletefilehandler(self.receiver.sock)
            self.receiver.close()
            self.receiver = None

    def on_live(self, _file=None, _mask=None):
        message = self.receiver.latest()
        if not message:
            return
        if message.get("bye"):
            self.clear_live()
            return
        self.light_buttons(set(message.get("buttons", ())))
        for stick, key in (("left_stick", "left"), ("right_stick", "right")):
            self.stick_views[stick].set_position(tuple(message.get(key, (0, 0))))
        self.live_last = time.monotonic()
        if self.live_timer is None:
            self.live_timer = self.root.after(500, self.check_live)

    def check_live(self):
        """Clear the view if updates stopped without a goodbye (e.g. the bridge crashed)."""
        self.live_timer = None
        if time.monotonic() - self.live_last > 1.5:
            self.clear_live()
        else:
            self.live_timer = self.root.after(500, self.check_live)

    def light_buttons(self, pressed):
        for b in self.lit - pressed:
            self.button_labels[b].configure(style="TLabel")
        for b in pressed - self.lit:
            if b in self.button_labels:
                self.button_labels[b].configure(style="Pressed.TLabel")
        self.lit = pressed & set(self.button_labels)

    def clear_live(self):
        self.light_buttons(set())
        for view in self.stick_views.values():
            view.set_position(None)
        if self.live_timer is not None:
            self.root.after_cancel(self.live_timer)
            self.live_timer = None

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
        self.clear_live()
        self.stop_live()
        self.root.destroy()


def main():
    if not os.environ.get("DISPLAY"):
        sys.exit("No display found. Run this from the Crostini terminal.")
    # The class name lets ChromeOS match this window to its launcher shortcut
    # (StartupWMClass in install_shortcut.py), so the shelf shows our icon.
    root = tk.Tk(className=WINDOW_CLASS)
    try:
        root.iconphoto(True, tk.PhotoImage(file=str(PROJECT_DIR / "icon.png")))
    except tk.TclError:
        pass  # no icon is fine
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
