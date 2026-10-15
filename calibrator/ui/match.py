"""Match screens by eye: the same test pattern on every screen, adjusted live, side by side.

Everything is backed up and marked as pending before the first change, so Cancel, Esc,
closing the app or even a crash puts every screen back the way it was. Only Keep saves.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import backup, blacks, ddc, engine, gamma, laptop
from ..logs import log
from . import scenes, theme
from . import widgets as w
from .compare import BeforeAfterBar
from .theme import C, F

PLUGE = (0.02, 0.04, 0.06, 0.10)  # squares just above black, like a broadcast PLUGE pattern
GRAYS = (0.25, 0.5, 0.75)

STEPS = (
    {"key": "brightness", "title": "Brightness", "pattern": "white",
     "many": "Both screens show white. Bring them to the same brightness. Turning the brighter one down usually "
             "works best, and it makes its blacks darker too.",
     "one": "The screen shows white. Pick a brightness that suits the room, bright but not glaring. Lower "
            "brightness also means darker blacks."},
    {"key": "white", "title": "White", "pattern": "white",
     "many": "Make the two whites the same color. Look at both at once, near where the screens meet.",
     "one": "Make white look neutral: not yellow, blue, green or pink. A sheet of paper nearby helps."},
    {"key": "grays", "title": "Grays", "pattern": "grays",
     "many": "Match the gray bands across the screens. If a screen's grays look lighter, raise its gamma. "
             "If they look darker, lower it.",
     "one": "The three bands should step evenly from dark to light. If the middle one looks too light, raise "
            "the gamma. If it looks too dark, lower it."},
    {"key": "blacks", "title": "Blacks", "pattern": "pluge",
     "many": "Dim the room if you can. On each screen, the first faint square should be only just visible and "
             "plain black as dark as it gets. Then compare the two blacks.",
     "one": "Dim the room if you can. The first faint square should be only just visible and plain black as "
            "dark as it gets."},
    {"key": "check", "title": "Check", "pattern": "scene",
     "many": "Every screen now shows the same picture. Switch between Before and After to see what changed, and "
             "try both pictures. Press Keep when you're happy.",
     "one": "Switch between Before and After to see what changed, and try both pictures. Press Keep when "
            "you're happy."},
)


def _gray(level: float) -> str:
    value = round(255 * level)
    return f"#{value:02x}{value:02x}{value:02x}"


class PatternWindow:
    """One full-screen test pattern."""

    def __init__(self, root, display, on_click, on_escape, on_space=None):
        self.display = display
        left, top, right, bottom = display.rect
        self.size = (right - left, bottom - top)
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{self.size[0]}x{self.size[1]}+{left}+{top}")
        self.win.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.win, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", lambda _e: on_click())
        self.win.bind("<Escape>", lambda _e: on_escape())
        if on_space is not None:
            self.win.bind("<space>", lambda _e: on_space())

    def show(self, pattern: str):
        canvas, (width, height) = self.canvas, self.size
        canvas.delete("all")
        if pattern.startswith("scene:"):
            scenes.draw(canvas, pattern.split(":", 1)[1], width, height)
            return
        if pattern == "white":
            canvas.configure(bg="#ffffff")
            label = "#a8a8a8"
        elif pattern == "grays":
            canvas.configure(bg="#000000")
            for index, level in enumerate(GRAYS):
                canvas.create_rectangle(index * width / 3, 0, (index + 1) * width / 3 + 1, height,
                                        fill=_gray(level), outline="")
            label = "#808080"
        else:  # PLUGE: black with faint squares just above it
            canvas.configure(bg="#000000")
            side = min(width, height) // 7
            gap = side // 2
            x0 = (width - len(PLUGE) * side - (len(PLUGE) - 1) * gap) // 2
            y0 = int(height * 0.26)
            for index, level in enumerate(PLUGE):
                x = x0 + index * (side + gap)
                canvas.create_rectangle(x, y0, x + side, y0 + side, fill=_gray(level), outline="")
                canvas.create_text(x + side / 2, y0 + side + 18, text=f"{round(level * 100)}%", fill="#262626",
                                   font=F["small"])
            label = "#262626"
        canvas.create_text(26, 22, anchor="nw", text=f"{self.display.index}   {self.display.name.upper()}",
                           fill=label, font=F["caps"])

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class MatchWizard:
    def __init__(self, app):
        self.app = app
        self.displays = list(app.displays)
        self.states, self.snapshots = {}, {}
        self.gpu, self.gpu_start, self.hw, self.hw_start = {}, {}, {}, {}
        self.enhanced: dict[str, bool] = {}
        self.windows: dict[str, PatternWindow] = {}
        self.panel: tk.Toplevel | None = None
        self.step = 0
        self.closed = False
        self.flush_job = None
        self.dirty: set[str] = set()
        self.scene = "shadows"  # picture for the Check step
        self.after = True  # Check step: new settings on screen (False = the old ones)
        app.applying = True
        app._set_busy(True, "Getting the screens ready…")
        app.worker.submit(self._prepare, self._ready, self._failed)

    # --- start: back everything up and mark it pending ------------------------------------------

    def _prepare(self):
        prepared = []
        try:
            for display in self.displays:
                state = engine.probe(display)
                snapshot = engine.snapshot_of(state)
                backup.save_original_if_missing(snapshot)
                backup.save(snapshot, "previous")
                backup.set_pending(snapshot)
                prepared.append((display, state, snapshot))
        except Exception:
            for display, _state, _snapshot in prepared:
                backup.clear_pending(display.key)  # nothing was changed yet
            raise
        return prepared

    def _failed(self, exc):
        self.app.applying = False
        self.app.matcher = None
        self.app._error(exc)

    def _ready(self, prepared):
        for display, state, snapshot in prepared:
            key = display.key
            self.states[key], self.snapshots[key] = state, snapshot
            if state.ramp is not None:
                info = gamma.analyse(state.ramp)
                start = {"temperature": info["kelvin"] or 6500, "tint": 0,
                         "gamma": round(info["gamma"] / 0.05) * 0.05, "shadows": 0, "white_level": 100}
                self.gpu[key], self.gpu_start[key] = dict(start), dict(start)
            hw = {}
            if ddc.BRIGHTNESS in state.ddc_values:
                hw.update(kind="ddc", brightness=state.ddc_current(ddc.BRIGHTNESS),
                          brightness_max=state.ddc_values[ddc.BRIGHTNESS][1] or 100)
            elif state.brightness is not None:
                hw.update(kind="panel", brightness=state.brightness, brightness_max=100)
            if all(c in state.ddc_values for c in ddc.BLACK_LEVELS):
                hw.update(black=round(sum(state.ddc_current(c) for c in ddc.BLACK_LEVELS) / 3),
                          black_max=state.ddc_values[ddc.RED_BLACK][1] or 100)
            self.hw[key], self.hw_start[key] = dict(hw), dict(hw)
        self.app._set_busy(False, "Matching screens. Esc puts everything back.")
        for display in self.displays:
            self.windows[display.key] = PatternWindow(self.app.root, display, self._raise_panel,
                                                      lambda: self.finish(False), self._space)
        self._build_panel()
        self._show_step()

    # --- the floating control panel -----------------------------------------------------------

    def _build_panel(self):
        self.panel = tk.Toplevel(self.app.root)
        self.panel.withdraw()  # shown once it is in place, not at the default spot first
        self.panel.title("Match screens" if len(self.displays) > 1 else "Fine-tune")
        self.panel.configure(bg=C["panel"])
        self.panel.resizable(False, False)
        self.panel.attributes("-topmost", True)
        self.panel.protocol("WM_DELETE_WINDOW", lambda: self.finish(False))
        self.panel.bind("<Escape>", lambda _e: self.finish(False))
        self.panel.bind("<space>", lambda _e: self._space())
        width = 330 * len(self.displays)
        body = ttk.Frame(self.panel, style="Panel.TFrame", padding=(24, 18))
        body.pack(fill="both", expand=True)
        self.heading = ttk.Label(body, style="Panel.Caps.TLabel")
        self.heading.pack(anchor="w")
        self.title = ttk.Label(body, style="Panel.H2.TLabel")
        self.title.pack(anchor="w", pady=(4, 0))
        self.text = ttk.Label(body, style="Panel.Dim.TLabel", wraplength=width, justify="left")
        self.text.pack(anchor="w", pady=(4, 16))
        self.columns = ttk.Frame(body, style="Panel.TFrame")
        self.columns.pack(fill="x")
        self.note = ttk.Label(body, style="Panel.Small.TLabel", wraplength=width, justify="left")
        self.note.pack(anchor="w", pady=(4, 0))
        nav = ttk.Frame(body, style="Panel.TFrame")
        nav.pack(fill="x", pady=(16, 0))
        ttk.Button(nav, text="Cancel", style="Panel.TButton", command=lambda: self.finish(False)).pack(side="left")
        self.next_button = ttk.Button(nav, style="Primary.TButton", command=self._next)
        self.next_button.pack(side="right")
        self.back_button = ttk.Button(nav, text="Back", style="Panel.TButton", command=self._back)
        self.back_button.pack(side="right", padx=(0, 8))
        theme.dark_title_bar(self.panel, C["dark"])

    def _place_panel(self):
        self.panel.update_idletasks()
        home = next((d for d in self.displays if d.key == self.app.display.key), self.displays[0])
        left, top, right, bottom = home.rect
        width, height = self.panel.winfo_reqwidth(), self.panel.winfo_reqheight()
        self.panel.geometry(f"+{left + (right - left - width) // 2}+{max(top, bottom - height - 80)}")
        self.panel.deiconify()
        self._raise_panel()

    def _raise_panel(self):
        if self.panel is not None:
            self.panel.lift()
            self.panel.focus_force()

    def _show_step(self):
        step = STEPS[self.step]
        many = len(self.displays) > 1
        pattern = f"scene:{self.scene}" if step["key"] == "check" else step["pattern"]
        for window in self.windows.values():
            window.show(pattern)
        self.heading.configure(text=f"{'MATCH SCREENS' if many else 'FINE-TUNE'} (EXPERIMENTAL)   ·   "
                                    f"STEP {self.step + 1} OF {len(STEPS)}")
        self.title.configure(text=step["title"])
        self.text.configure(text=step["many" if many else "one"])
        w.clear(self.columns)
        if step["key"] == "check":
            self.bar = BeforeAfterBar(self.columns, self._show_scene, self._show_state, self.scene, self.after)
            self.bar.frame.pack(anchor="w")
        for index, display in enumerate(self.displays if step["key"] != "check" else ()):
            column = ttk.Frame(self.columns, style="Panel.TFrame")
            column.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 26, 0))
            self.columns.columnconfigure(index, weight=1, uniform="screens", minsize=300)
            ttk.Label(column, text=f"{display.index}   {display.name}", style="Panel.Strong.TLabel").pack(
                anchor="w", pady=(0, 8))
            self._controls(column, display, step["key"])
            if len(column.winfo_children()) == 1:
                ttk.Label(column, text="Nothing to change on this screen in this step.",
                          style="Panel.Dim.TLabel", wraplength=290).pack(anchor="w")
        notes = []
        washed = [d.name for d in self.displays if d.hdr_enabled]
        if washed and step["key"] != "brightness":
            notes.append(f"Windows manages color itself on {', '.join(washed)} (HDR or automatic color "
                         "management), so color changes may not show there.")
        if step["key"] == "blacks":
            notes.append("If black stays dark gray even at low brightness, the PC may be sending limited-range "
                         "video. Set it to Full in your Intel, NVIDIA or AMD graphics settings.")
        self.note.configure(text="\n".join(notes))
        self.back_button.state(["disabled"] if self.step == 0 else ["!disabled"])
        self.next_button.configure(text="Keep" if self.step == len(STEPS) - 1 else "Next")
        self._place_panel()

    def _slider(self, column, label, low, high, step, value, fmt, on_change, gradient=None):
        slider = w.Slider(column, label, low, high, step, value, fmt, gradient, panel=True)
        slider.on_change = lambda: on_change(slider.get())
        slider.frame.pack(fill="x", pady=(0, 10))
        return slider

    def _controls(self, column, display, step):
        key = display.key
        gpu, hw = self.gpu.get(key), self.hw[key]
        if step == "brightness":
            if "brightness" in hw:
                self._slider(column, "Brightness", 5, hw["brightness_max"], 1, hw["brightness"], "{:.0f}",
                             lambda v: self._set_hw(display, "brightness", int(v)))
            elif gpu is not None:
                self._slider(column, "Brightness, through the graphics card", 40, 100, 1, gpu["white_level"],
                             "{:.0f}%", lambda v: self._set_gpu(display, "white_level", v))
        elif step == "white" and gpu is not None:
            self._slider(column, "Temperature, warm to cool", 3500, 9000, 50, gpu["temperature"], "{:.0f} K",
                         lambda v: self._set_gpu(display, "temperature", v), w.temperature_color)
            self._slider(column, "Tint, green to magenta", -20, 20, 1, gpu["tint"], "{:+.0f}",
                         lambda v: self._set_gpu(display, "tint", v), w.tint_color)
        elif step == "grays" and gpu is not None:
            self._slider(column, "Gamma", 1.8, 2.8, 0.05, gpu["gamma"], "{:.2f}",
                         lambda v: self._set_gpu(display, "gamma", v))
        elif step == "blacks" and (gpu is not None or "black" in hw):
            sliders = {}
            row = ttk.Frame(column, style="Panel.TFrame")
            row.pack(anchor="w", pady=(0, 10))
            enhanced = tk.BooleanVar(value=self.enhanced.get(key, False))
            ttk.Checkbutton(row, text="Enhanced blacks", variable=enhanced, style="Panel.TCheckbutton",
                            command=lambda: self._enhance(display, enhanced.get(), sliders)).pack(side="left")
            w.InfoButton(row, blacks.INFO_TITLE, blacks.info_text(), panel=True).pack(side="left", padx=(6, 0))
            if gpu is not None:
                sliders["shadows"] = self._slider(column, "Shadows, deeper to more detail", -10, 10, 1,
                                                  gpu["shadows"], "{:+.0f}",
                                                  lambda v: self._set_gpu(display, "shadows", v))
            if "black" in hw:
                sliders["black"] = self._slider(column, "Monitor black level", 0, hw["black_max"], 1, hw["black"],
                                                "{:.0f}", lambda v: self._set_hw(display, "black", int(v)))

    def _enhance(self, display, on: bool, sliders):
        key = display.key
        self.enhanced[key] = on
        if "shadows" in sliders:
            sliders["shadows"].set(blacks.SHADOWS if on else self.gpu_start[key]["shadows"])
        if "black" in sliders:
            start = self.hw_start[key]["black"]
            sliders["black"].set(blacks.black_level_target(start, self.hw[key]["black_max"]) if on else start)

    def _space(self):
        if STEPS[self.step]["key"] == "check":
            self._show_state(not self.after)

    def _show_scene(self, scene: str):
        self.scene = scene
        for window in self.windows.values():
            window.show(f"scene:{scene}")
        self.bar.update(scene=scene)
        self._raise_panel()

    def _show_state(self, after: bool):
        """Check step: put the old (Before) or the new (After) settings on every screen."""
        if after == self.after or self.closed:
            return
        self.after = after
        targets = []
        for display in self.displays:
            key, snapshot = display.key, self.snapshots[display.key]
            if key in self.gpu and self.gpu[key] != self.gpu_start[key] and snapshot.ramp is not None:
                try:
                    gamma.write(display.gdi_name, gamma.from_settings(self.gpu[key]) if after else snapshot.ramp)
                except gamma.GammaError:
                    pass
            if self.hw[key] != self.hw_start[key]:
                now = self._monitor_values(key, self.hw[key])
                values = now if after else {code: snapshot.ddc[code] for code in now if code in snapshot.ddc}
                panel = self.hw[key].get("brightness") if after else snapshot.brightness
                targets.append((display, values, panel if self.hw[key].get("kind") == "panel" else None, after))
        if targets:
            self.app.worker.submit(lambda: self._write_states(targets), None,
                                   lambda exc: log.warning("before/after switch: %s", exc))
        if getattr(self, "bar", None) is not None and self.panel is not None:
            self.bar.update(after=after)

    @staticmethod
    def _write_states(targets):
        for display, values, panel, after in targets:
            if values:
                with ddc.open_monitor(display.hmonitor) as monitor:
                    for code in ddc.APPLY_ORDER if after else ddc.RESTORE_ORDER:
                        if code in values:
                            monitor.set(code, values[code])
            if panel is not None:
                laptop.set_brightness(display.device_path, panel)

    def _next(self):
        if self.step == len(STEPS) - 1:
            self.finish(True)
        else:
            self.step += 1
            self._show_step()

    def _back(self):
        if self.step:
            self._show_state(True)  # never leave the Check step showing the old settings
            self.step -= 1
            self._show_step()

    # --- live changes -------------------------------------------------------------------------

    def _set_gpu(self, display, field, value):
        self.gpu[display.key][field] = value
        try:
            gamma.write(display.gdi_name, gamma.from_settings(self.gpu[display.key]))
        except gamma.GammaError:
            self.note.configure(text=f"Windows refused that on {display.name}. Try a smaller change.")

    def _set_hw(self, display, field, value):
        """Monitor and panel settings are slow to send, so slider moves are bundled every 200 ms."""
        self.hw[display.key][field] = value
        self.dirty.add(display.key)
        if self.flush_job is not None:
            self.app.root.after_cancel(self.flush_job)
        self.flush_job = self.app.root.after(200, self._flush)

    def _flush(self):
        self.flush_job = None
        targets = [(display, dict(self.hw[display.key])) for display in self.displays if display.key in self.dirty]
        self.dirty.clear()
        if targets:
            self.app.worker.submit(lambda: self._write_hw(targets), None,
                                   lambda exc: self.note.configure(text=f"The monitor didn't take that: {exc}"))

    def _write_hw(self, targets):
        for display, hw in targets:
            values = self._monitor_values(display.key, hw)
            if values:
                with ddc.open_monitor(display.hmonitor) as monitor:
                    for code, value in values.items():
                        monitor.set(code, value)
            if hw.get("kind") == "panel":
                laptop.set_brightness(display.device_path, hw["brightness"])

    def _monitor_values(self, key, hw) -> dict[int, int]:
        """The monitor-side settings as they stand now (VCP code -> value)."""
        state, start = self.states[key], self.hw_start[key]
        values = {}
        if hw.get("kind") == "ddc":
            values[ddc.BRIGHTNESS] = hw["brightness"]
        if "black" in hw:
            offset = hw["black"] - start["black"]
            for code in ddc.BLACK_LEVELS:
                values[code] = max(0, min(hw["black_max"], state.ddc_current(code) + offset))
        return values

    # --- the end: keep or put everything back ---------------------------------------------------

    def finish(self, keep: bool):
        if self.closed:
            return
        if keep:
            self._show_state(True)  # Keep saves what's on screen, so put the new settings back first
        self.closed = True
        if self.flush_job is not None:
            self.app.root.after_cancel(self.flush_job)
            self._flush()  # queued ahead of the save/restore below
        self._close_windows()
        self.app._set_busy(True, "Saving…" if keep else "Putting everything back…")
        self.app.worker.submit(self._save if keep else self._restore, self._done, self._finish_failed)

    def _close_windows(self):
        for window in self.windows.values():
            window.close()
        if self.panel is not None:
            try:
                self.panel.destroy()
            except tk.TclError:
                pass
            self.panel = None

    def _save(self) -> list[str]:
        messages = []
        for display in self.displays:
            key = display.key
            others = [d.name for d in self.displays if d.key != key]
            title = f"Matched to {', '.join(others)}" if others else "Fine-tuned by eye"
            hw = self.hw[key]
            calibration = engine.tuned_calibration(
                self.states[key], self.gpu.get(key), self.gpu_start.get(key), self._monitor_values(key, hw),
                hw.get("brightness") if hw.get("kind") == "panel" else None, title)
            if calibration is None:
                engine.restore(display, self.snapshots[key])  # touched, then set back: restore it exactly
            else:
                backup.save_confirmed(key, calibration)
                backup.log(key, f"kept “{title}” ({calibration.changes()})")
                messages.append(f"Kept the new settings for {display.name}.")
            backup.clear_pending(key)
        return messages or ["Nothing was changed."]

    def _restore(self) -> list[str]:
        problems = []
        for display in self.displays:
            problems += engine.restore(display, self.snapshots[display.key])
            backup.clear_pending(display.key)
            backup.log(display.key, "went back after matching")
        return ["Put every screen back the way it was."] + ([f"Problems: {'; '.join(problems)}"] if problems else [])

    def _done(self, messages):
        self.app.applying = False
        self.app.matcher = None
        for message in messages:
            self.app.log(message)
        self.app.rescan()

    def _finish_failed(self, exc):
        self.app.applying = False
        self.app.matcher = None
        self.app._error(exc)  # the pending markers stay, so the next start still undoes it

    def restore_now(self):
        """The app is closing mid-session: put everything back before it goes."""
        if self.closed:
            return
        self.closed = True
        self._close_windows()
        if self.snapshots:
            try:
                self._restore()
            except Exception:  # the pending markers stay, so the next start still undoes it
                log.exception("could not undo the match session on close")
