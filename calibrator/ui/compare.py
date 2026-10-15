"""Before and after: a sample picture full screen, with a switch between the old and the new settings.

The change is applied through the normal backup path first (backed up, marked pending), so
Go back, Esc, closing the app or a crash all return the screen to how it was.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import ddc, engine, gamma, laptop
from ..logs import log
from . import scenes, theme
from .theme import C


class SceneWindow:
    """A sample picture, full screen on one display."""

    def __init__(self, root, display, on_click, on_escape, on_space):
        left, top, right, bottom = display.rect
        self.size = (right - left, bottom - top)
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{self.size[0]}x{self.size[1]}+{left}+{top}")
        self.win.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.win, highlightthickness=0, bd=0, bg="#000000")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", lambda _e: on_click())
        self.win.bind("<Escape>", lambda _e: on_escape())
        self.win.bind("<space>", lambda _e: on_space())

    def show(self, scene: str):
        scenes.draw(self.canvas, scene, *self.size)

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class BeforeAfterBar:
    """Which picture to show, and Before / After. The active choice is the filled button."""

    def __init__(self, parent, on_scene, on_switch, scene: str = "shadows", after: bool = True):
        self.frame = ttk.Frame(parent, style="Panel.TFrame")
        pictures = ttk.Frame(self.frame, style="Panel.TFrame")
        pictures.pack(anchor="w")
        ttk.Label(pictures, text="Picture", style="Panel.Dim.TLabel", width=9).pack(side="left")
        self.scene_buttons = {}
        for name, label in scenes.SCENES:
            button = ttk.Button(pictures, text=label, command=lambda n=name: on_scene(n))
            button.pack(side="left", padx=(0, 6))
            self.scene_buttons[name] = button
        showing = ttk.Frame(self.frame, style="Panel.TFrame")
        showing.pack(anchor="w", pady=(8, 0))
        ttk.Label(showing, text="Showing", style="Panel.Dim.TLabel", width=9).pack(side="left")
        self.state_buttons = {
            False: ttk.Button(showing, text="Before", command=lambda: on_switch(False)),
            True: ttk.Button(showing, text="After", command=lambda: on_switch(True)),
        }
        self.state_buttons[False].pack(side="left", padx=(0, 6))
        self.state_buttons[True].pack(side="left")
        ttk.Label(showing, text="Space switches", style="Panel.Small.TLabel").pack(side="left", padx=12)
        self.update(scene, after)

    def update(self, scene: str | None = None, after: bool | None = None):
        if scene is not None:
            for name, button in self.scene_buttons.items():
                button.configure(style="Primary.TButton" if name == scene else "Panel.TButton")
        if after is not None:
            for state, button in self.state_buttons.items():
                button.configure(style="Primary.TButton" if state == after else "Panel.TButton")


class CompareSession:
    """Before / after for a change from the Adjust page that hasn't been kept yet."""

    def __init__(self, app, display, calibration):
        self.app, self.display, self.calibration = app, display, calibration
        self.snapshot = None
        self.after = True
        self.scene = "shadows" if self._mostly_blacks() else "colors"
        self.closed = False
        self.window: SceneWindow | None = None
        self.panel: tk.Toplevel | None = None
        app.applying = True
        app._set_busy(True, "Getting the comparison ready…")
        app.worker.submit(lambda: engine.begin(display, calibration), self._ready, self._failed)

    def _mostly_blacks(self) -> bool:
        gpu = self.calibration.settings.get("gpu") or {}
        touches_levels = any(code in ddc.BLACK_LEVELS for code in self.calibration.ddc)
        return touches_levels or (gpu.get("shadows", 0) != 0 and gpu.get("temperature", 6500) == 6500
                                  and gpu.get("tint", 0) == 0)

    def _failed(self, exc):
        self.app.applying = False
        self.app.comparer = None
        self.app._error(exc)

    def _ready(self, snapshot):
        self.snapshot = snapshot
        self.app._set_busy(False, "Comparing. Space switches before and after, Esc goes back.")
        self.window = SceneWindow(self.app.root, self.display, self._raise_panel, lambda: self.finish(False),
                                  lambda: self.switch(not self.after))
        self.window.show(self.scene)
        self._build_panel()

    def _build_panel(self):
        panel = self.panel = tk.Toplevel(self.app.root)
        panel.withdraw()  # shown once it is in place, not at the default spot first
        panel.title("Before and after")
        panel.configure(bg=C["panel"])
        panel.resizable(False, False)
        panel.attributes("-topmost", True)
        panel.protocol("WM_DELETE_WINDOW", lambda: self.finish(False))
        panel.bind("<Escape>", lambda _e: self.finish(False))
        panel.bind("<space>", lambda _e: self.switch(not self.after))
        body = ttk.Frame(panel, style="Panel.TFrame", padding=(24, 18))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="BEFORE AND AFTER", style="Panel.Caps.TLabel").pack(anchor="w")
        ttk.Label(body, text=self.calibration.title, style="Panel.H2.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(body, text="The change is on. Switch to Before to see the screen as it was. Nothing is kept "
                             "until you press Keep.", style="Panel.Dim.TLabel", wraplength=440,
                  justify="left").pack(anchor="w", pady=(4, 14))
        self.bar = BeforeAfterBar(body, self.show_scene, self.switch, self.scene, self.after)
        self.bar.frame.pack(anchor="w")
        nav = ttk.Frame(body, style="Panel.TFrame")
        nav.pack(fill="x", pady=(18, 0))
        ttk.Button(nav, text="Go back", style="Panel.TButton", command=lambda: self.finish(False)).pack(side="left")
        ttk.Button(nav, text="Keep", style="Primary.TButton", command=lambda: self.finish(True)).pack(side="right")
        theme.dark_title_bar(panel, C["dark"])
        panel.update_idletasks()
        left, top, right, bottom = self.display.rect
        width, height = panel.winfo_reqwidth(), panel.winfo_reqheight()
        panel.geometry(f"+{left + (right - left - width) // 2}+{max(top, bottom - height - 80)}")
        panel.deiconify()
        self._raise_panel()

    def _raise_panel(self):
        if self.panel is not None:
            self.panel.lift()
            self.panel.focus_force()

    def show_scene(self, scene: str):
        self.scene = scene
        if self.window is not None:
            self.window.show(scene)
        self.bar.update(scene=scene)
        self._raise_panel()

    def switch(self, after: bool):
        """Put the old (Before) or the new (After) settings on screen."""
        if self.closed or self.snapshot is None or after == self.after:
            return
        self.after = after
        cal, snap, display = self.calibration, self.snapshot, self.display
        if cal.ramp is not None and snap.ramp is not None:
            try:
                gamma.write(display.gdi_name, cal.ramp if after else snap.ramp)
            except gamma.GammaError as exc:
                log.warning("compare: %s", exc)
        if cal.ddc or cal.brightness is not None:
            values = dict(cal.ddc) if after else {code: snap.ddc[code] for code in cal.ddc if code in snap.ddc}
            order = ddc.APPLY_ORDER if after else ddc.RESTORE_ORDER
            brightness = cal.brightness if after else snap.brightness

            def job():
                if values:
                    with ddc.open_monitor(display.hmonitor) as monitor:
                        for code in order:
                            if code in values:
                                monitor.set(code, values[code])
                if cal.brightness is not None and brightness is not None:
                    laptop.set_brightness(display.device_path, brightness)

            self.app.worker.submit(job, None, lambda exc: log.warning("compare switch: %s", exc))
        if self.panel is not None:
            self.bar.update(after=after)

    def finish(self, keep: bool):
        if self.closed:
            return
        if keep and not self.after:
            self.switch(True)  # queued ahead of the save below
        self.closed = True
        self._close_windows()
        if self.snapshot is None:
            return
        self.app._set_busy(True, "Keeping it…" if keep else "Going back…")
        display, cal, snapshot = self.display, self.calibration, self.snapshot
        self.app.worker.submit(lambda: engine.finish(display, cal, snapshot, keep),
                               lambda problems: self._done(keep, problems), self._finish_failed)

    def _close_windows(self):
        if self.window is not None:
            self.window.close()
        if self.panel is not None:
            try:
                self.panel.destroy()
            except tk.TclError:
                pass
            self.panel = None

    def _done(self, keep, problems):
        self.app.applying = False
        self.app.comparer = None
        self.app.log(f"Kept “{self.calibration.title}”." if keep else
                     f"Went back from “{self.calibration.title}”." + (f" Problems: {'; '.join(problems)}" if problems else ""))
        self.app.rescan()

    def _finish_failed(self, exc):
        self.app.applying = False
        self.app.comparer = None
        self.app._error(exc)  # the pending marker stays, so the next start still undoes it

    def restore_now(self):
        """The app is closing mid-comparison: put the screen back before it goes."""
        if self.closed:
            return
        self.closed = True
        self._close_windows()
        if self.snapshot is not None:
            try:
                engine.finish(self.display, self.calibration, self.snapshot, False)
            except Exception:  # the pending marker stays, so the next start still undoes it
                log.exception("could not undo the comparison on close")
