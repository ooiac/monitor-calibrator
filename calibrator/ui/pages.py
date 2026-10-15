"""The four pages: Overview, Adjust, Phone camera, History & safety."""
from __future__ import annotations

import hashlib
import json
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import backup, blacks, ddc, engine, gamma, presets, profiles, requirements, startup
from . import widgets as w
from .theme import C, F


def friendly_details(display) -> str:
    width, height = display.size
    parts = ["Built-in screen" if display.internal else "External monitor"]
    if not display.internal and display.connection != "unknown connection":
        parts.append(display.connection)
    parts.append(f"{width} × {height}")
    if display.primary:
        parts.append("main display")
    return " · ".join(parts)


def ramp_words(ramp) -> str:
    text = gamma.describe(ramp)
    return "neutral" if text.startswith("neutral") else text


def _intro(body, title: str, text: str = ""):
    ttk.Label(body, text=title, style="H1.TLabel").pack(anchor="w")
    if text:
        ttk.Label(body, text=text, style="Dim.TLabel", wraplength=720, justify="left").pack(anchor="w", pady=(4, 0))


def _button_row(parent, buttons, panel=False):
    row = ttk.Frame(parent, style="Panel.TFrame" if panel else "TFrame")
    row.pack(anchor="w", pady=(16, 0))
    for text, command in buttons:
        ttk.Button(row, text=text, command=command, style="Panel.TButton" if panel else "TButton").pack(
            side="left", padx=(0, 8))
    return row


# --- Overview ----------------------------------------------------------------------------------


class OverviewPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.scroll = w.ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)

    def refresh(self):
        body = self.scroll.body
        w.clear(body)
        app, display, state = self.app, self.app.display, self.app.state
        head = ttk.Frame(body)
        head.pack(fill="x")
        ttk.Label(head, text=display.name, style="H1.TLabel").pack(side="left")
        app.button(head, "Check again", app.rescan).pack(side="right", anchor="s")
        ttk.Label(body, text=friendly_details(display), style="Dim.TLabel").pack(anchor="w", pady=(2, 18))
        if state is None or state.display.key != display.key:
            panel = w.Panel(body, C["faint"])
            panel.pack(fill="x")
            ttk.Label(panel.body, text="Reading this screen", style="Panel.State.TLabel").pack(anchor="w")
            ttk.Label(panel.body, text="Asking the graphics card and the monitor what they can do. "
                                       "Takes a second or two.", style="Panel.Dim.TLabel").pack(anchor="w", pady=(4, 0))
            return
        self._status(body, display, state)
        self._abilities(body, display, state)
        self._found(body, state)

    def _status(self, body, display, state):
        app = self.app
        confirmed = backup.load_confirmed(display.key)
        best = profiles.best(app.candidates)
        if confirmed and engine.is_active(state, confirmed):
            stripe, word = C["ok"], "Calibrated"
            text = f"Using “{confirmed.title}”. You kept it, so it stays."
        elif best and engine.is_active(state, best):
            stripe, word = C["ok"], "Looks right"
            text = f"“{best.title}” is already on. Nothing to do here."
        elif best:
            stripe, word = C["info"], "Ready to calibrate"
            text = f"Suggested: {best.title}. {best.detail}"
        else:
            stripe, word = C["faint"], "Nothing found yet"
            text = ("There's no profile for this model on file. You can adjust it by eye, measure it with your "
                    "phone, or look for a profile online.")
        panel = w.Panel(body, stripe)
        panel.pack(fill="x")
        ttk.Label(panel.body, text="STATUS", style="Panel.Caps.TLabel").pack(anchor="w")
        ttk.Label(panel.body, text=word, style="Panel.State.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(panel.body, text=text, style="Panel.Dim.TLabel", wraplength=660, justify="left").pack(anchor="w", pady=(4, 0))
        actions = ttk.Frame(panel.body, style="Panel.TFrame")
        actions.pack(anchor="w", pady=(14, 0))
        if confirmed and engine.is_active(state, confirmed):
            if best and best.id != confirmed.id and best.score > confirmed.score:
                app.button(actions, f"Try {best.title}", lambda: app.apply(best), "Primary.TButton").pack(side="left", padx=(0, 8))
            app.button(actions, "Undo", lambda: app.undo("previous"), "Panel.TButton").pack(side="left")
        elif best and engine.is_active(state, best):
            actions.pack_forget()
        elif best:
            app.button(actions, "Calibrate", app.calibrate, "Primary.TButton").pack(side="left")
            ttk.Label(actions, text=f"You'll have {app.timeout} seconds to keep it, otherwise it goes back.",
                      style="Panel.Small.TLabel").pack(side="left", padx=14)
        else:
            app.button(actions, "Adjust by eye", lambda: app.show_page("adjust"), "Primary.TButton").pack(side="left")
            ttk.Button(actions, text="Measure with my phone", style="Panel.TButton",
                       command=lambda: app.show_page("camera")).pack(side="left", padx=8)
            ttk.Button(actions, text="Search online", style="Panel.TButton", command=app.search_online).pack(side="left")

    def _abilities(self, body, display, state):
        box = w.section(body, "What this screen lets me change")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        has_gpu, has_ddc = state.ramp is not None, bool(state.ddc_values)
        has_brightness = ddc.BRIGHTNESS in state.ddc_values or state.brightness is not None
        for text, on in (("Colors, through the graphics card", has_gpu), ("The monitor's own settings", has_ddc),
                         ("Brightness", has_brightness)):
            w.indicator(row, text, "on" if on else "off").pack(side="left", padx=(0, 22))
        facts = []
        if has_gpu:
            facts.append(f"Colors right now: {ramp_words(state.ramp)}.")
        if ddc.COLOR_PRESET in state.ddc_values:
            facts.append(f"Monitor mode: {ddc.PRESETS.get(state.ddc_current(ddc.COLOR_PRESET), 'unknown')}.")
        if state.brightness is not None:
            facts.append(f"Brightness {state.brightness}%.")
        elif ddc.BRIGHTNESS in state.ddc_values:
            facts.append(f"Brightness {state.ddc_current(ddc.BRIGHTNESS)}.")
        if facts:
            ttk.Label(box, text="  ".join(facts), style="Dim.TLabel", wraplength=720).pack(anchor="w", pady=(10, 0))
        notes = []
        if not has_ddc and not display.internal:
            notes.append(("Turn on “DDC/CI” in the monitor's own menu and I can change its settings too.", "info"))
        if state.night_light:
            notes.append(("Night Light is on. It uses the same color controls I do, so we'd undo each other. "
                          "Switch it off while calibrating: Settings › System › Display › Night light.", "warn"))
        if display.hdr_enabled:
            notes.append(("HDR or automatic color management is on for this screen. Windows handles color "
                          "itself then, so changes made through the graphics card may not show.", "warn"))
        for text, kind in notes:
            w.Note(box, text, kind).pack(fill="x", pady=(12, 0))

    def _found(self, body, state):
        app = self.app
        box = w.section(body, "Calibrations found", "Best match first.")
        best = profiles.best(app.candidates)
        if not app.candidates:
            ttk.Label(box, text="None yet.", style="Dim.TLabel").pack(anchor="w")
        for index, cal in enumerate(app.candidates):
            if index:
                tk.Frame(box, bg=C["line"], height=1).pack(fill="x", pady=12)
            row = ttk.Frame(box)
            row.pack(fill="x")
            active = engine.is_active(state, cal)
            app.button(row, "On" if active else "Apply", lambda c=cal: app.apply(c), enabled=not active).pack(
                side="right", anchor="n", padx=(16, 0))
            info = ttk.Frame(row)
            info.pack(side="left", fill="x", expand=True)
            title = ttk.Frame(info)
            title.pack(anchor="w")
            ttk.Label(title, text=cal.title, style="Strong.TLabel", wraplength=470, justify="left").pack(side="left")
            if cal is best:
                tk.Label(title, text="BEST MATCH", bg=C["bg"], fg=C["info"], font=F["caps"]).pack(side="left", padx=(10, 0))
            source = cal.source if not cal.confidence else f"{cal.source} · {cal.confidence} confidence"
            ttk.Label(info, text=source, style="Small.TLabel").pack(anchor="w", pady=(1, 0))
            ttk.Label(info, text=cal.detail, style="Dim.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(4, 0))
        _button_row(box, (("Load an ICC file…", app.load_icc), ("Search online…", app.search_online),
                           ("Open the profiles folder", lambda: app.open_folder(profiles.PROFILE_DIR))))


# --- Adjust ------------------------------------------------------------------------------------


class AdjustPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.bar = ttk.Frame(self, padding=(34, 12))
        self.bar.pack(side="bottom", fill="x")
        tk.Frame(self, bg=C["line"], height=1).pack(side="bottom", fill="x")
        self.scroll = w.ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self.sliders: dict = {}
        self.mode_var: tk.StringVar | None = None
        self.mode_initial = ""
        self.selected: dict | None = None
        self.curve: w.ToneCurve | None = None
        self._build_bar()

    def _build_bar(self):
        app = self.app
        # Packed in priority order: the buttons keep their size, the note gives way on narrow windows.
        self.preview_button = app.button(self.bar, "Preview", self.preview, "Primary.TButton")
        self.preview_button.pack(side="left")
        self.compare_button = app.button(self.bar, "Compare", lambda: app.start_compare(self.calibration()))
        self.compare_button.pack(side="left", padx=(8, 0))
        app.button(self.bar, "Reset", self.refresh).pack(side="left", padx=8)
        ttk.Button(self.bar, text="Save for this monitor", command=self.save_profile).pack(side="right")
        ttk.Button(self.bar, text="Save preset", command=self.save_preset).pack(side="right", padx=8)
        self.changes = ttk.Label(self.bar, style="Dim.TLabel")
        self.changes.pack(side="left", padx=10)

    def refresh(self):
        body = self.scroll.body
        w.clear(body)
        self.sliders, self.mode_var, self.selected, self.curve = {}, None, None, None
        app, state = self.app, self.app.state
        _intro(body, "Adjust", f"Pick a preset or move the sliders. Nothing changes until you press Preview, "
                               f"and then you have {app.timeout} seconds to keep it.")
        if state is None or state.display.key != app.display.key:
            ttk.Label(body, text="Reading this screen…", style="Dim.TLabel").pack(anchor="w", pady=(20, 0))
            self._update()
            return
        self._presets(body)
        if state.ramp is not None:
            info = gamma.analyse(state.ramp)
            box = w.section(body, "Color", "Works on any screen. Night Light changes these same curves.")
            columns = ttk.Frame(box)
            columns.pack(fill="x")
            left = ttk.Frame(columns)
            left.pack(side="left", fill="x", expand=True, padx=(0, 28))
            self._add(left, "temperature", "Temperature, warm to cool", 3000, 9300, 50, info["kelvin"] or 6500,
                      "{:.0f} K", w.temperature_color)
            self._add(left, "tint", "Tint, green to magenta", -30, 30, 1, 0, "{:+.0f}", w.tint_color)
            self._add(left, "gamma", "Gamma, lower shows more shadow detail", 1.6, 2.8, 0.05, info["gamma"], "{:.2f}")
            right = ttk.Frame(columns)
            right.pack(side="left", anchor="n")
            self.curve = w.ToneCurve(right, 230, 190)
            self.curve.pack()
            swatch_row = ttk.Frame(right)
            swatch_row.pack(anchor="w", pady=(6, 0))
            self.swatch = tk.Canvas(swatch_row, width=26, height=14, highlightthickness=1,
                                    highlightbackground=C["line_strong"], bd=0)
            self.swatch.pack(side="left")
            ttk.Label(swatch_row, text="white, before and after", style="Small.TLabel").pack(side="left", padx=8)
        self._blacks(body, state)
        if state.ddc_values:
            box = w.section(body, "Monitor", "Its own settings, the same ones its buttons change.")
            caps = state.ddc_caps
            if ddc.COLOR_PRESET in state.ddc_values and caps and caps.presets():
                row = ttk.Frame(box)
                row.pack(fill="x", pady=(0, 12))
                ttk.Label(row, text="Color mode").pack(side="left")
                options = [ddc.PRESETS[v] for v in caps.presets()]
                current = ddc.PRESETS.get(state.ddc_current(ddc.COLOR_PRESET), "")
                if current and current not in options:
                    options.append(current)
                self.mode_var = tk.StringVar(value=current)
                self.mode_initial = current
                choice = ttk.Combobox(row, textvariable=self.mode_var, values=options, state="readonly", width=12)
                choice.pack(side="right")
                choice.bind("<<ComboboxSelected>>", lambda _e: self._update())
            for code, label in ((ddc.BRIGHTNESS, "Brightness"), (ddc.CONTRAST, "Contrast"), (ddc.RED_GAIN, "Red"),
                                (ddc.GREEN_GAIN, "Green"), (ddc.BLUE_GAIN, "Blue")):
                if code in state.ddc_values and code not in self.sliders:
                    current, maximum = state.ddc_values[code]
                    self._add(box, code, label, 0, maximum or 100, 1, current, "{:.0f}")
            if any(c in state.ddc_values for c in ddc.GAINS):
                ttk.Label(box, text="Red, green and blue only work in a User color mode. I switch to it when needed.",
                          style="Small.TLabel").pack(anchor="w")
        if state.brightness is not None:
            box = w.section(body, "Screen brightness")
            self._add(box, "panel_brightness", "Brightness", 5, 100, 1, state.brightness, "{:.0f}%")
        if not self.sliders and self.mode_var is None:
            w.Note(body, "Nothing on this screen can be adjusted from here.", "info").pack(fill="x", pady=(20, 0))
        self._update()

    def _add(self, parent, key, label, low, high, step, value, fmt, gradient=None):
        slider = w.Slider(parent, label, low, high, step, value, fmt, gradient, self._update)
        slider.frame.pack(fill="x", pady=(0, 14))
        self.sliders[key] = slider

    def _blacks(self, body, state):
        has_levels = all(c in state.ddc_values for c in ddc.BLACK_LEVELS)
        if state.ramp is None and not has_levels:
            return
        box = w.section(body, "Blacks", "LCD screens leak a little backlight through black. These shape what you "
                                        "can still change about it.")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(0, 14))
        self.enhanced = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text=blacks.LABEL, variable=self.enhanced, command=self._enhance).pack(side="left")
        w.InfoButton(row, blacks.INFO_TITLE, blacks.info_text()).pack(side="left", padx=(8, 0))
        if state.ramp is not None:
            self._add(box, "shadows", "Shadows, deeper blacks to more detail", -10, 10, 1, 0, "{:+.0f}")
        if has_levels:
            current = [state.ddc_values[c] for c in ddc.BLACK_LEVELS]
            self._add(box, "black_level", "Monitor black level, lower is darker", 0, current[0][1] or 100, 1,
                      round(sum(value for value, _ in current) / 3), "{:.0f}")
            ttk.Label(box, text="Not every monitor uses this setting. If nothing changes when you preview, "
                                "yours ignores it.", style="Small.TLabel").pack(anchor="w")

    def _enhance(self):
        on = self.enhanced.get()
        if "shadows" in self.sliders:
            shadows = self.sliders["shadows"]
            shadows.set(blacks.SHADOWS if on else shadows.initial)
        if "black_level" in self.sliders:
            level = self.sliders["black_level"]
            level.set(blacks.black_level_target(int(level.initial), int(level.high)) if on else level.initial)

    def _presets(self, body):
        box = w.section(body, "Presets", top=22)
        grid = ttk.Frame(box)
        grid.pack(fill="x")
        self.tiles = []
        for index, preset in enumerate(presets.all_presets()):
            tile = w.Tile(grid, preset["name"], preset.get("about", ""),
                          lambda p=preset: self.load_preset(p), own=preset["user"])
            tile.grid(row=index // 3, column=index % 3, sticky="nsew", padx=(0, 6), pady=(0, 6))
            self.tiles.append((preset, tile))
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="tiles")
        self.delete_link = ttk.Button(box, text="Delete this preset", command=self.delete_preset)

    # --- values ---

    def _value(self, key, default=None):
        slider = self.sliders.get(key)
        return slider.get() if slider else default

    def _changed(self, key) -> bool:
        slider = self.sliders.get(key)
        return bool(slider and slider.changed)

    def _gpu_settings(self) -> dict:
        return {"temperature": self._value("temperature", 6500), "tint": self._value("tint", 0),
                "gamma": self._value("gamma", 2.2), "shadows": self._value("shadows", 0)}

    def _black_levels(self) -> dict[int, int]:
        """Absolute black-level values: the slider moves red, green and blue together, keeping their offsets."""
        state, slider = self.app.state, self.sliders.get("black_level")
        if slider is None:
            return {}
        offset = int(slider.get()) - int(slider.initial)
        return {code: max(0, min(state.ddc_values[code][1] or 100, state.ddc_current(code) + offset))
                for code in ddc.BLACK_LEVELS}

    def _update(self):
        count = sum(s.changed for s in self.sliders.values())
        if self.mode_var is not None and self.mode_var.get() != self.mode_initial:
            count += 1
        self.changes.configure(text=f"{count} change{'s' if count != 1 else ''}" if count else "No changes yet")
        self.preview_button.base_enabled = bool(count)
        self.compare_button.base_enabled = bool(count)
        self.app.refresh_buttons()
        if self.curve is not None:
            settings = self._gpu_settings()
            self.curve.draw(w.ramp_curves(gamma.from_settings(settings)), reference=w.power_curve(1.0))
            self.swatch.delete("all")
            self.swatch.create_rectangle(0, 0, 13, 15, fill="#ffffff", outline="")
            self.swatch.create_rectangle(13, 0, 27, 15, fill=w.white_color(settings["temperature"], settings["tint"]),
                                         outline="")

    def calibration(self) -> profiles.Calibration | None:
        state = self.app.state
        gpu_changed = any(self._changed(k) for k in ("temperature", "tint", "gamma", "shadows"))
        ramp = gamma.from_settings(self._gpu_settings()) if gpu_changed else None
        values = {code: int(self._value(code)) for code in (ddc.BRIGHTNESS, ddc.CONTRAST, *ddc.GAINS) if self._changed(code)}
        if self._changed("black_level"):
            values.update(self._black_levels())
        if self.mode_var is not None and self.mode_var.get() != self.mode_initial:
            values[ddc.COLOR_PRESET] = ddc.preset_value(self.mode_var.get())
        mode = values.get(ddc.COLOR_PRESET, state.ddc_current(ddc.COLOR_PRESET))
        if (any(c in values for c in ddc.GAINS) and mode is not None and mode not in ddc.USER_PRESETS
                and state.ddc_caps and ddc.USER1 in state.ddc_caps.presets()):
            values[ddc.COLOR_PRESET] = ddc.USER1
        brightness = int(self._value("panel_brightness")) if self._changed("panel_brightness") else None
        if ramp is None and not values and brightness is None:
            return None
        ordered = {c: values[c] for c in ddc.APPLY_ORDER if c in values}
        fingerprint = hashlib.sha1(json.dumps([self._gpu_settings() if ramp else None, ordered, brightness]).encode()).hexdigest()[:10]
        only_blacks = not any(self._changed(k) for k in self.sliders if k not in ("shadows", "black_level"))
        if self.selected:
            title = self.selected["name"]
        elif getattr(self, "enhanced", None) is not None and self.enhanced.get() and only_blacks:
            title = "Enhanced blacks"
        else:
            title = "Manual adjustment"
        return profiles.Calibration(
            id=f"manual:{fingerprint}", title=title, source="manual", confidence="", auto=False,
            ramp=ramp, ddc=ordered, brightness=brightness, settings={"gpu": self._gpu_settings()} if ramp else {},
        )

    def preview(self):
        cal = self.calibration()
        if cal is not None:
            self.app.apply(cal)

    # --- presets & profiles ---

    def load_preset(self, preset: dict):
        self.selected = preset
        for item, tile in self.tiles:
            tile.select(item is preset)
        if preset["user"]:
            self.delete_link.pack(anchor="w", pady=(4, 0))
        else:
            self.delete_link.pack_forget()
        missing = []
        for key in ("temperature", "tint", "gamma", "shadows"):
            if key in preset and key in self.sliders:
                self.sliders[key].set(preset[key])
            elif key in preset and preset[key] not in (6500, 0, 2.2):
                missing.append("colors")
        if "brightness" in preset:
            target = self.sliders.get(ddc.BRIGHTNESS) or self.sliders.get("panel_brightness")
            if target:
                target.set(preset["brightness"])
        for name in ("contrast", "red_gain", "green_gain", "blue_gain"):
            if name in preset and ddc.CODES[name] in self.sliders:
                self.sliders[ddc.CODES[name]].set(preset[name])
        if "monitor_preset" in preset:
            options = [ddc.PRESETS[v] for v in self.app.state.ddc_caps.presets()] if self.app.state.ddc_caps else []
            if self.mode_var is not None and preset["monitor_preset"] in options:
                self.mode_var.set(preset["monitor_preset"])
            else:
                missing.append(f"the monitor's {preset['monitor_preset']} mode")
        self._update()
        note = f"Loaded {preset['name']}. Press Preview to try it."
        if missing:
            note += f" This screen can't do {', '.join(dict.fromkeys(missing))}."
        self.app.status(note)

    def save_preset(self):
        name = simpledialog.askstring("Save preset", "Name for this preset:", parent=self.app.root)
        if not name or not name.strip():
            return
        preset = {"name": name.strip()[:60], "about": "Saved by you."}
        preset.update({k: self._value(k) for k in ("temperature", "tint", "gamma") if k in self.sliders})
        if self.mode_var is not None:
            preset["monitor_preset"] = self.mode_var.get()
        brightness = self._value(ddc.BRIGHTNESS, self._value("panel_brightness"))
        if brightness is not None:
            preset["brightness"] = int(brightness)
        for key in ("contrast", "red_gain", "green_gain", "blue_gain"):
            if ddc.CODES[key] in self.sliders:
                preset[key] = int(self._value(ddc.CODES[key]))
        presets.save_user_preset(preset)
        self.app.log(f"Saved the preset {preset['name']}.")
        self.refresh()

    def delete_preset(self):
        if self.selected and self.selected["user"] and messagebox.askyesno(
                "Delete preset", f"Delete {self.selected['name']}?", parent=self.app.root):
            presets.delete_user_preset(self.selected["name"])
            self.refresh()

    def save_profile(self):
        settings = {}
        gpu = self._gpu_settings()
        if "temperature" in self.sliders and not gamma.is_neutral_settings(gpu):
            settings["gpu"] = gpu
        values = {}
        if self.mode_var is not None:
            values["color_preset"] = self.mode_var.get()
        for code in ddc.GAINS:
            if code in self.sliders:
                values[ddc.NAMES[code]] = int(self._value(code))
        for code, value in self._black_levels().items():
            values[ddc.NAMES[code]] = value
        if values:
            settings["ddc"] = values
        if not settings:
            messagebox.showinfo("Save for this monitor", "Nothing to save. The colors are at neutral.", parent=self.app.root)
            return
        display = self.app.display
        path = profiles.PROFILE_DIR / f"{display.pnp_id or display.key}.json"
        if path.exists() and not messagebox.askyesno("Save for this monitor", "Replace the one saved before?",
                                                      parent=self.app.root):
            return
        profiles.save_profile(display, settings)
        self.app.log(f"Saved these settings for {display.name}. Calibrate will use them from now on.")
        self.app.rescan()


# --- Match screens -----------------------------------------------------------------------------


class MatchPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.scroll = w.ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)

    def refresh(self):
        body = self.scroll.body
        w.clear(body)
        app = self.app
        many = len(app.displays) > 1
        _intro(body, "Match screens", (
            "Put the same test pattern on every screen and adjust them side by side until they look alike: "
            "brightness, white, grays and blacks, then a before and after check."
            if many else
            "With one screen this is a live fine-tune: the same steps against test patterns, then a before "
            "and after check."))
        w.Note(body, "Experimental. This hasn't been tested on real screens much yet. Everything is backed up first "
                     "and put back if you cancel, so it's safe to try.", "warn").pack(fill="x", pady=(14, 0))
        box = w.section(body, "Your screens")
        for display in app.displays:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{display.index}", style="Value.TLabel", width=3).pack(side="left")
            ttk.Label(row, text=display.name, style="Strong.TLabel").pack(side="left")
            ttk.Label(row, text=f"   {friendly_details(display)}", style="Dim.TLabel").pack(side="left")
        actions = ttk.Frame(box)
        actions.pack(anchor="w", pady=(16, 0))
        app.button(actions, "Start matching" if many else "Start fine-tuning", app.start_match,
                   "Primary.TButton").pack(side="left")
        if many:
            ttk.Button(actions, text="Match with my phone instead", command=app.start_camera_match).pack(
                side="left", padx=8)
        ttk.Label(box, text="Everything is backed up first. Cancel or Esc puts it all back, and nothing is kept "
                            "until you press Keep at the end.", style="Small.TLabel", wraplength=700).pack(
            anchor="w", pady=(10, 0))

        box = w.section(body, "What to expect")
        for text in (
                "Brightness, white, grays and shadows can be matched closely.",
                "Strong reds, greens and blues may still differ. If one screen shows fewer colors than the other, "
                "which is common on laptops, software can't add the missing ones.",
                "On LCD screens black is never fully black, because the backlight leaks a little. Lower brightness, "
                "a lower monitor black level and the Enhanced blacks option all help. OLED screens don't have this."):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text="■", style="Dim.TLabel").pack(side="left", anchor="n", padx=(0, 10))
            ttk.Label(row, text=text, wraplength=680, justify="left").pack(side="left", fill="x")

        box = w.section(body, "Black looks dark gray?")
        ttk.Label(box, wraplength=720, justify="left", text=(
            "Often the graphics card is sending limited-range video, where black is level 16 instead of 0, and the "
            "monitor shows it as gray. Set the range to Full:")).pack(anchor="w")
        for vendor, where in (("Intel", "Intel Graphics Command Center › Display › Quantization range: Full"),
                              ("NVIDIA", "NVIDIA Control Panel › Change resolution › Output dynamic range: Full"),
                              ("AMD", "AMD Software › Display › Pixel format: RGB 4:4:4 Full RGB")):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=(6, 0))
            ttk.Label(row, text=vendor, style="Strong.TLabel", width=9).pack(side="left")
            ttk.Label(row, text=where, style="Dim.TLabel").pack(side="left")
        ttk.Label(box, text="Some monitors also have an HDMI black level setting in their own menu. It has to agree "
                            "with the PC: Full usually goes with Normal or High, Limited with Low. Names vary by brand.",
                  style="Small.TLabel", wraplength=720, justify="left").pack(anchor="w", pady=(10, 0))


# --- Phone camera ------------------------------------------------------------------------------


class CameraPage(ttk.Frame):
    QR_SIZE = 200

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.result = None
        self.scroll = w.ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)

    def refresh(self):
        body = self.scroll.body
        w.clear(body)
        app, session = self.app, self.app.camera
        _intro(body, "Phone camera", "Take a photo of a test chart and this PC works out how the screen really "
                                     "behaves. Your phone's own color and contrast settings don't affect the result.")
        matching = session is not None and session.mode == "match"
        if len(app.displays) > 1:
            box = w.section(body, "What to measure", top=22)
            row = ttk.Frame(box)
            row.pack(anchor="w")
            for mode, label in (("single", "This screen"), ("match", "All screens together (experimental)")):
                active = (mode == "match") == matching
                ttk.Button(row, text=label, style="Primary.TButton" if active else "TButton",
                           command=lambda m=mode: app.set_camera_mode(m)).pack(side="left", padx=(0, 8))
            ttk.Label(box, wraplength=700, justify="left", style="Small.TLabel", text=(
                "One photo with every screen in it. The camera treats all of them the same way, so their whites "
                "and brightness can be compared directly, even with the camera on auto."
                if matching else f"Measures {app.display.name} on its own: gamma and gray balance.")).pack(
                anchor="w", pady=(8, 0))
        box = w.section(body, "How it goes")
        steps = (
            "Press Start and scan the code with your phone. It has to be on the same Wi-Fi as this PC.",
            "On the phone, tap Show the chart. A chart appears on every screen." if matching else
            "On the phone, tap Show the chart. It fills this screen.",
            "Step back until every screen fits in one photo, and take it. Slightly blurry is fine." if matching else
            "Step back about 2 meters and take the photo. Slightly blurry is fine, too close is not.",
            "Check the result, apply it, then take one more photo to confirm.")
        for number, text in enumerate(steps, 1):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=f"{number}", style="Value.TLabel", width=3).pack(side="left", anchor="n")
            ttk.Label(row, text=text, wraplength=640, justify="left").pack(side="left", fill="x")
        if not matching:
            ttk.Label(box, text="To measure the white point as well, lock the camera's white balance (Pro mode, "
                                "6500 K) or include a screen you trust in the photo.",
                      style="Small.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=(10, 0))
        actions = ttk.Frame(box)
        actions.pack(anchor="w", pady=(14, 0))
        if session is None:
            app.button(actions, "Start", app.start_camera, "Primary.TButton").pack(side="left")
        else:
            ttk.Button(actions, text="Stop", command=app.stop_camera).pack(side="left")
            self._connection(body, session)
            if self.result is not None:
                if self.result.get("mode") == "match":
                    self._match_result(body)
                else:
                    self._result(body)

    def _connection(self, body, session):
        app = self.app
        box = w.section(body, "Connection")
        if session.address.startswith("127."):
            w.Note(box, "This PC doesn't seem to be on a network, so a phone can't reach it. Connect to Wi-Fi or "
                        "Ethernet, then press Stop and Start again.", "warn").pack(fill="x", pady=(0, 14))
        row = ttk.Frame(box)
        row.pack(fill="x")
        code = tk.Canvas(row, width=self.QR_SIZE, height=self.QR_SIZE, highlightthickness=0, bg="#ffffff")
        code.pack(side="left", anchor="n", padx=(0, 26))
        right = ttk.Frame(row)
        right.pack(side="left", fill="both", expand=True)
        device = session.device_info()
        if device:
            self._connected(code, device)
            w.indicator(right, f"{device['kind']} connected").pack(anchor="w")
            ttk.Label(right, text="Other phones are turned away until you disconnect this one.",
                      style="Dim.TLabel", wraplength=380).pack(anchor="w", pady=(6, 14))
        else:
            self._qr(code, session.url)
            w.indicator(right, "Waiting for a phone", "warn").pack(anchor="w")
            ttk.Label(right, text="Point the phone's camera at the code. One phone at a time.",
                      style="Dim.TLabel", wraplength=380).pack(anchor="w", pady=(6, 14))
        link = ttk.Frame(right)
        link.pack(fill="x")
        entry = ttk.Entry(link)
        entry.insert(0, session.url)
        entry.configure(state="readonly")
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(link, text="Copy", command=lambda: app.copy(session.url)).pack(side="left", padx=(6, 0))
        actions = ttk.Frame(right)
        actions.pack(anchor="w", pady=(12, 0))
        app.button(actions, "Show the charts" if session.mode == "match" else "Show the chart here",
                   lambda: app.show_chart(True)).pack(side="left")
        ttk.Button(actions, text="Disconnect phone", command=app.disconnect_device,
                   state="normal" if device else "disabled").pack(side="left", padx=8)
        qr_helper = requirements.PACKAGES["qrcode"][0]
        if qr_helper in requirements.missing_packages():
            app.button(right, "Install the QR helper", lambda: app.install_extras(
                [qr_helper], "The QR code needs a small helper called qrcode.", then=self.refresh)).pack(
                anchor="w", pady=(10, 0))
        ttk.Label(right, text="If Windows asks about the firewall, allow private networks only.",
                  style="Small.TLabel", wraplength=380).pack(anchor="w", pady=(12, 0))

    def _qr(self, canvas, url):
        try:
            import qrcode
        except ImportError:
            canvas.create_text(self.QR_SIZE // 2, self.QR_SIZE // 2, width=self.QR_SIZE - 30, justify="center",
                               fill="#000000", text="For a QR code run:\npip install qrcode\n\nor type the link.")
            return
        code = qrcode.QRCode(border=2)
        code.add_data(url)
        code.make(fit=True)
        matrix = code.get_matrix()
        cell = max(2, self.QR_SIZE // len(matrix))
        offset = (self.QR_SIZE - cell * len(matrix)) // 2
        for y, line in enumerate(matrix):
            for x, dark in enumerate(line):
                if dark:
                    canvas.create_rectangle(offset + x * cell, offset + y * cell, offset + (x + 1) * cell,
                                            offset + (y + 1) * cell, fill="#000000", outline="")

    def _connected(self, canvas, device):
        middle = self.QR_SIZE // 2
        canvas.configure(bg=C["panel"])
        canvas.create_text(middle, middle - 12, text="Connected", font=F["h2"], fill=C["text"])
        canvas.create_text(middle, middle + 16, text=device["address"], font=F["small"], fill=C["dim"])

    def _result(self, body):
        app, result = self.app, self.result
        box = w.section(body, "Last photo")
        for text in result.get("warnings", []):
            w.Note(box, text, "warn").pack(fill="x", pady=(0, 10))
        if not result.get("ok"):
            return
        columns = ttk.Frame(box)
        columns.pack(fill="x")
        left = ttk.Frame(columns)
        left.pack(side="left", fill="x", expand=True, padx=(0, 26))
        readouts = ttk.Frame(left)
        readouts.pack(anchor="w")
        for name in ("gray", "red", "green", "blue"):
            cell = ttk.Frame(readouts)
            cell.pack(side="left", padx=(0, 22))
            ttk.Label(cell, text=name.upper(), style="Caps.TLabel").pack(anchor="w")
            value = result["gammas"].get(name)
            ttk.Label(cell, text=f"{value:.2f}" if value else "–", style="Readout.TLabel").pack(anchor="w")
        ttk.Label(left, text="Gamma per channel. 2.20 is the standard.", style="Small.TLabel").pack(anchor="w", pady=(2, 12))
        for text in (result["tracking"], result["white"]["text"]):
            ttk.Label(left, text=text, wraplength=420, justify="left").pack(anchor="w", pady=(0, 6))
        plot = w.ToneCurve(columns, 230, 190)
        plot.pack(side="left", anchor="n")
        plot.draw([(w.CHANNEL_COLORS[name], w.power_curve(value)) for name, value in result["gammas"].items() if value],
                  reference=w.power_curve(2.2), x_label="signal", y_label="light")

        options = ttk.Frame(box)
        options.pack(anchor="w", pady=(16, 0))
        ttk.Label(options, text="Target gamma").pack(side="left")
        target = tk.StringVar(value="2.2")
        ttk.Combobox(options, textvariable=target, values=("2.0", "2.2", "2.4"), width=5, state="readonly").pack(
            side="left", padx=(8, 18))
        can_fix_white = result["white"]["mode"] != "auto"
        fix_white = tk.BooleanVar(value=can_fix_white)
        ttk.Checkbutton(options, text="Fix the white point too", variable=fix_white,
                        state="normal" if can_fix_white else "disabled").pack(side="left")
        via = tk.StringVar(value="graphics card")
        if app.state and all(c in app.state.ddc_values for c in ddc.GAINS):
            ttk.Label(options, text="using the").pack(side="left", padx=(18, 6))
            ttk.Combobox(options, textvariable=via, values=("graphics card", "monitor"), width=13, state="readonly").pack(side="left")
        app.button(box, "Apply the correction", lambda: app.apply_camera({
            "target_gamma": float(target.get()), "fix_gamma": True, "fix_white": fix_white.get(),
            "via": "monitor" if via.get() == "monitor" else "gpu"}), "Primary.TButton").pack(anchor="w", pady=(16, 0))


    def _match_result(self, body):
        app, result = self.app, self.result
        box = w.section(body, "Comparison")
        for text in result.get("warnings", []):
            w.Note(box, text, "warn").pack(fill="x", pady=(0, 10))
        if not result.get("ok"):
            return
        for screen in result["screens"]:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=(4, 10))
            title = ttk.Frame(row)
            title.pack(anchor="w")
            ttk.Label(title, text=f"{screen['index']}   {screen['name']}", style="Strong.TLabel").pack(side="left")
            if screen["key"] == result["reference"]:
                tk.Label(title, text="REFERENCE", bg=C["bg"], fg=C["info"], font=F["caps"]).pack(side="left", padx=10)
            gammas = screen.get("gammas") or {}
            ttk.Label(row, style="Dim.TLabel", text="Gamma   " + "   ".join(
                f"{name} {value:.2f}" for name, value in gammas.items() if value)).pack(anchor="w", pady=(2, 0))
            comparison = screen.get("comparison")
            ttk.Label(row, wraplength=680, justify="left", text=comparison["text"] if comparison else
                      "The other screens are matched to this one.").pack(anchor="w", pady=(2, 0))
        options = ttk.Frame(box)
        options.pack(anchor="w", pady=(8, 0))
        choices = {"match_white": tk.BooleanVar(value=True), "match_brightness": tk.BooleanVar(value=True),
                   "fix_gamma": tk.BooleanVar(value=True)}
        for key, label in (("match_white", "Match white"), ("match_brightness", "Match brightness"),
                           ("fix_gamma", "Fix gamma to")):
            ttk.Checkbutton(options, text=label, variable=choices[key]).pack(side="left", padx=(0, 14))
        target = tk.StringVar(value="2.2")
        ttk.Combobox(options, textvariable=target, values=("2.0", "2.2", "2.4"), width=5, state="readonly").pack(side="left")
        ttk.Label(box, text="For brightness, the brighter screen is turned down, which also makes its blacks darker.",
                  style="Small.TLabel").pack(anchor="w", pady=(8, 0))
        app.button(box, "Apply to the screens", lambda: app.apply_camera(
            {**{key: var.get() for key, var in choices.items()}, "target_gamma": float(target.get())}),
            "Primary.TButton").pack(anchor="w", pady=(14, 0))


# --- History & safety --------------------------------------------------------------------------


class HistoryPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.scroll = w.ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self.log_view = None

    def refresh(self):
        body = self.scroll.body
        w.clear(body)
        app = self.app
        _intro(body, "History & safety")
        box = w.section(body, "Going back",
                        f"Before every change the current settings are saved. If you don't confirm within "
                        f"{app.timeout} seconds, the change is undone. If the app closes halfway, it's undone the "
                        "next time it starts. In an emergency, run restore.bat in the app folder.", top=22)
        row = ttk.Frame(box)
        row.pack(anchor="w")
        app.button(row, "Undo the last change", lambda: app.undo("previous")).pack(side="left")
        app.button(row, "Restore the original settings", lambda: app.undo("original")).pack(side="left", padx=8)
        ttk.Button(row, text="Open the backups folder",
                   command=lambda: app.open_folder(backup.display_dir(app.display.key))).pack(side="left")

        box = w.section(body, "Options")
        for text, value, command in (
                ("Calibrate the screen this window is on by itself, at start and when I move the window",
                 app.settings.get("auto_on_launch", True), app.toggle_auto),
                ("Put my calibration back when I sign in to Windows (it's forgotten at every restart)",
                 startup.is_enabled(), app.toggle_startup),
                ("Put my calibration back if Windows resets it while this window is open",
                 app.settings.get("guard", True), app.toggle_guard),
                ("Keep my last 5 phone photos on this PC to look into odd results (location data is removed)",
                 app.settings.get("keep_uploads", False), app.toggle_uploads)):
            var = tk.BooleanVar(value=value)
            ttk.Checkbutton(box, text=text, variable=var, command=lambda v=var, c=command: c(v.get())).pack(anchor="w", pady=4)

        box = w.section(body, "Extras", "Only the phone camera needs these. Everything else works without them.")
        items = [item for item in requirements.scan() if item.package or item.name.startswith(("Pillow", "qrcode"))]
        for item in items:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=2)
            w.indicator(row, item.name, "on" if item.ok else "warn").pack(side="left")
            ttk.Label(row, text=f"   {item.detail}", style="Dim.TLabel").pack(side="left")
        missing = requirements.missing_packages(items)
        if missing:
            app.button(box, "Install what's missing", lambda: app.install_extras(
                missing, "These extras are missing.", then=self.refresh)).pack(anchor="w", pady=(10, 0))

        box = w.section(body, "Activity")
        self.log_view = tk.Text(box, height=12, wrap="word", relief="flat", bd=0, bg=C["panel"], fg=C["dim"],
                                font=F["mono"], padx=12, pady=10, highlightthickness=0)
        self.log_view.pack(fill="both", expand=True)
        self.log_view.insert("end", "\n".join(app.log_lines[-300:]))
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def append(self, line: str):
        if self.log_view is not None and self.log_view.winfo_exists():
            self.log_view.configure(state="normal")
            self.log_view.insert("end", ("\n" if self.log_view.get("1.0", "end").strip() else "") + line)
            self.log_view.see("end")
            self.log_view.configure(state="disabled")
