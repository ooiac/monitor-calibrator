"""The window: which screen it's on, scanning, applying changes safely, the phone camera, background guards."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import backup, confirm, engine, profiles, requirements, startup
from . import displays as displays_mod
from . import winapi as w
from .logs import log
from .ui import theme, widgets
from .ui.pages import AdjustPage, CameraPage, HistoryPage, MatchPage, OverviewPage
from .ui.theme import C, F

NAV = (
    ("overview", "Overview", ""),
    ("adjust", "Adjust", ""),
    ("camera", "Phone camera", ""),
    ("match", "Match screens", "Experimental"),
    ("history", "History & safety", ""),
)
_REDRAW = object()  # forces the phone-connection panel to redraw


class Worker:
    """Runs slow hardware calls (DDC/CI can take seconds) off the UI thread, one at a time."""

    def __init__(self, root, on_error):
        self.root = root
        self.on_error = on_error
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()
        self._poll()

    def submit(self, job, on_done=None, on_error=None):
        self.jobs.put((job, on_done, on_error))

    def _run(self):
        while True:
            job, on_done, on_error = self.jobs.get()
            try:
                self.results.put((on_done, job(), None, on_error))
            except Exception as exc:  # reported in the window
                log.exception("background job failed")
                self.results.put((on_done, None, exc, on_error))

    def _poll(self):
        while True:
            try:
                on_done, result, exc, on_error = self.results.get_nowait()
            except queue.Empty:
                break
            try:
                if exc is not None:
                    (on_error or self.on_error)(exc)
                elif on_done is not None:
                    on_done(result)
            except Exception as callback_exc:
                log.exception("window callback failed")
                self.on_error(callback_exc)
        if self.root.winfo_exists():
            self.root.after(50, self._poll)


class ChartWindow:
    """The camera chart, full screen and pixel-exact on one display."""

    def __init__(self, root, display, on_close, code: int):
        from PIL import ImageTk

        from .camera import chart

        width, height = display.size
        left, top, _, _ = display.rect
        self.on_close = on_close
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{width}x{height}+{left}+{top}")
        self.win.attributes("-topmost", True)
        # Plain characters only: the chart's built-in font has no dashes or fractions.
        caption = (f"Camera check · {display.name} · stand back 1.5-2 m (screen about 1/4 of the photo width) · "
                   "blur is fine · Esc or click to close")
        self.image = ImageTk.PhotoImage(chart.render(width, height, caption, code))
        tk.Label(self.win, image=self.image, borderwidth=0, highlightthickness=0, padx=0, pady=0).pack()
        self.win.bind("<Escape>", lambda _e: self.close())
        self.win.bind("<Button-1>", lambda _e: self.close())
        self.win.focus_force()

    def close(self):
        if self.win is not None:
            self.win.destroy()
            self.win = None
            self.on_close()


class App:
    def __init__(self, root: tk.Tk, display, timeout: int, auto_on_launch: bool | None):
        self.root = root
        self.display = display
        self.timeout = timeout
        self.state: engine.DisplayState | None = None
        self.candidates: list[profiles.Calibration] = []
        self.displays = displays_mod.list_displays()
        self.busy = False
        self.applying = False  # a change is on screen waiting for keep/revert, or being undone
        self.pending = None  # [(display, calibration, snapshot), ...] while waiting for keep/revert
        self.closing = False
        self.settings = backup.load_settings()
        self.auto_allowed = auto_on_launch is not False  # --no-auto switches it off for the whole session
        self.auto_this_launch = self.settings.get("auto_on_launch", True) if auto_on_launch is None else auto_on_launch
        self.buttons: list[ttk.Button] = []
        self.log_lines: list[str] = []
        self.camera = None
        self.camera_events = queue.Queue()
        self.chart_windows: dict[str, ChartWindow] = {}  # one per screen showing the camera chart
        self.latest_photo = None
        self.device_shown = _REDRAW
        self.poll_count = 0
        self.move_check = None
        self.matcher = None  # a running Match screens session
        self.comparer = None  # a running before/after comparison

        theme.apply(root)
        self.worker = Worker(root, self._error)
        self._build()
        theme.dark_title_bar(root, C["dark"])
        self._place_on(display)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.bind("<Configure>", self._on_configure, add="+")
        root.after(100, self._startup)
        root.after(1500, self._watch_monitor)
        root.after(100, self._camera_poll)
        root.after(5000, self._guard)

    # --- layout ------------------------------------------------------------------------------

    def _build(self):
        root = self.root
        root.title("Monitor Calibrator")
        root.minsize(1000, 660)
        side = tk.Frame(root, bg=C["side"], width=220)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=C["side"])
        brand.pack(fill="x", padx=18, pady=(22, 26))
        widgets.ColorBars(brand, width=91, height=8).pack(anchor="w")
        tk.Label(brand, text="MONITOR CALIBRATOR", bg=C["side"], fg=C["text"], font=F["brand"]).pack(anchor="w", pady=(8, 0))
        self.nav = {}
        for key, label, note in NAV:
            item = widgets.NavItem(side, label, lambda k=key: self.show_page(k), note)
            item.pack(fill="x", pady=0)
            self.nav[key] = item

        where = tk.Frame(side, bg=C["side"])
        where.pack(side="bottom", fill="x", padx=18, pady=20)
        tk.Label(where, text="SCREENS", bg=C["side"], fg=C["dim"], font=F["caps"]).pack(anchor="w")
        self.screen_map = widgets.ScreenMap(where, self.move_to)
        self.screen_map.pack(anchor="w", pady=(8, 8))
        self.here = tk.Label(where, bg=C["side"], fg=C["dim"], font=F["small"], wraplength=180, justify="left")
        self.here.pack(anchor="w")

        main = ttk.Frame(root)
        main.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(main, padding=(34, 7))
        bar.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bar, mode="determinate", length=110)
        self.progress.pack(side="right")
        self.status_label = ttk.Label(bar, text="Starting", style="Small.TLabel", wraplength=640)
        self.status_label.pack(side="left")
        tk.Frame(main, bg=C["line"], height=1).pack(side="bottom", fill="x")
        holder = ttk.Frame(main)
        holder.pack(fill="both", expand=True)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        self.pages = {
            "overview": OverviewPage(holder, self),
            "adjust": AdjustPage(holder, self),
            "match": MatchPage(holder, self),
            "camera": CameraPage(holder, self),
            "history": HistoryPage(holder, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")
            page.refresh()
        self.show_page("overview")

    def show_page(self, key: str):
        widgets.InfoButton.close_all()
        for name, item in self.nav.items():
            item.set_active(name == key)
        self.pages[key].tkraise()

    def render(self):
        """Redraw everything that depends on the current screen."""
        self.root.title(f"Monitor Calibrator · {self.display.name}")
        self.screen_map.draw(self.displays, self.display.key)
        self.here.configure(text=f"This window is on screen {self.display.index}, {self.display.name}. "
                                 "Drag it or click another screen to switch.")
        for page in self.pages.values():
            page.refresh()

    def button(self, parent, text, command, style="TButton", enabled=True) -> ttk.Button:
        """A button that greys out while the app is busy (and stays off if enabled=False)."""
        button = ttk.Button(parent, text=text, command=command, style=style)
        button.base_enabled = enabled
        self.buttons.append(button)
        button.state(["disabled"] if (self.busy or not enabled) else ["!disabled"])
        return button

    def refresh_buttons(self):
        self.buttons = [b for b in self.buttons if b.winfo_exists()]
        for button in self.buttons:
            button.state(["disabled"] if (self.busy or not button.base_enabled) else ["!disabled"])

    def _place_on(self, display):
        self.root.update_idletasks()
        width, height = max(self.root.winfo_reqwidth(), 1060), max(self.root.winfo_reqheight(), 720)
        left, top, right, bottom = display.rect
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 3)
        self.root.geometry(f"{min(width, right - left)}x{min(height, bottom - top - 60)}+{x}+{y}")

    def status(self, message: str):
        self.status_label.configure(text=message)

    def log(self, message: str):
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.log_lines.append(line)
        log.info(message)
        self.pages["history"].append(line)

    def _set_busy(self, busy: bool, message: str | None = None):
        self.busy = busy
        self.refresh_buttons()
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
        if message:
            self.status(message)

    def _error(self, exc: Exception):
        self._set_busy(False, f"Something went wrong: {exc}")
        self.log(f"Problem: {exc}")
        if not self.closing:
            messagebox.showwarning("Monitor Calibrator", str(exc), parent=self.root)

    def open_folder(self, path):
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status("Link copied.")

    def quit(self):
        """Closing mid-change undoes the change first, instead of leaving it for the next start."""
        self.closing = True
        if self.pending is not None:
            self.status("Undoing the change you didn't confirm…")
            self.root.update_idletasks()
            try:
                for entry in self.pending:
                    engine.finish(*entry, keep=False)
            except Exception:  # the pending marker stays, so the next start still undoes it
                log.exception("could not undo on close")
            self.pending = None
        for session in (self.matcher, self.comparer):
            if session is not None:
                session.restore_now()
        if self.camera is not None:
            self.camera.stop()
        self.root.destroy()

    def start_match(self):
        """Match screens by eye (or fine-tune one screen) with live test patterns."""
        if self.busy or self.applying or self.matcher is not None:
            return
        from .ui.match import MatchWizard

        self.show_chart(False)
        self.matcher = MatchWizard(self)

    def start_compare(self, calibration):
        """A sample picture with a before/after switch, for a change that isn't kept yet."""
        if self.busy or self.applying or calibration is None or self.comparer is not None:
            return
        from .ui.compare import CompareSession

        self.show_chart(False)
        self.comparer = CompareSession(self, self.display, calibration)

    # --- startup & following the window across screens ------------------------------------------

    def _startup(self):
        self.render()
        self.log(f"Started on {self.display.name}.")
        self._set_busy(True, "Checking for changes a previous run didn't finish…")
        self.worker.submit(lambda: engine.recover_pending(displays_mod.list_displays()), self._recovered)

    def _recovered(self, messages):
        for message in messages:
            self.log(message)
        self.rescan(auto=self.auto_allowed and self.auto_this_launch, on_launch=True)

    def _on_configure(self, event):
        if event.widget is not self.root:
            return
        if self.move_check is not None:
            self.root.after_cancel(self.move_check)
        self.move_check = self.root.after(250, self._check_monitor)

    def _watch_monitor(self):
        """Slow fallback: also catches screens being plugged in, unplugged or rearranged."""
        try:
            self._check_monitor()
        finally:
            if not self.closing:
                self.root.after(1500, self._watch_monitor)

    def _check_monitor(self):
        self.move_check = None
        if self.applying or self.closing:
            return  # finish the keep/revert first; the fallback check picks the move up afterwards
        hmonitor = w.user32.MonitorFromWindow(int(self.root.wm_frame(), 16), w.MONITOR_DEFAULTTONEAREST)
        if hmonitor == self.display.hmonitor:
            return
        self.displays = displays_mod.list_displays()
        moved_to = displays_mod.by_hmonitor(self.displays, hmonitor)
        if moved_to is None:
            return
        previous, self.display = self.display, moved_to
        if moved_to.key == previous.key:
            self.screen_map.draw(self.displays, self.display.key)
            return  # same screen, Windows just handed out a new handle (layout change)
        self.log(f"Moved to {moved_to.name}. Now working on that screen.")
        self.state, self.candidates = None, []
        if self.camera is not None:
            self.show_chart(False)
            self.camera.retarget(moved_to)
            self.latest_photo = None
            self.pages["camera"].result = None
        self.render()
        self.rescan(auto=self.auto_allowed and self.settings.get("auto_on_launch", True), on_launch=True)

    def move_to(self, display):
        """Clicked a screen on the map: move the window there (the move itself triggers the switch)."""
        if display.key == self.display.key:
            return
        if self.root.state() == "zoomed":
            self.root.state("normal")
        self.root.update_idletasks()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        left, top, right, bottom = display.rect
        self.root.geometry(f"+{left + max(0, (right - left - width) // 2)}+{top + max(0, (bottom - top - height) // 3)}")

    # --- scanning ------------------------------------------------------------------------------

    def rescan(self, auto: bool = False, on_launch: bool = False):
        display = self.display
        self._set_busy(True, f"Reading {display.name}…")

        def work():
            state = engine.probe(display)
            return state, profiles.find(display, state)

        self.worker.submit(work, lambda result: self._scanned(display, *result, auto=auto, on_launch=on_launch))

    def _scanned(self, display, state, candidates, auto, on_launch):
        if display.key != self.display.key:
            return  # the window moved on meanwhile; the scan for the new screen is queued behind this one
        self.state, self.candidates = state, candidates
        self._set_busy(False, f"Ready. {len(candidates)} calibration{'s' if len(candidates) != 1 else ''} "
                              f"found for {display.name}.")
        self.render()
        if auto and not self.applying:
            self.calibrate(on_launch)

    # --- applying changes ----------------------------------------------------------------------

    def calibrate(self, on_launch: bool = False):
        if self.state is None:
            return
        plan = engine.plan_auto(self.display, self.state, self.candidates, on_launch)
        self.log(plan.message)
        self.status(plan.message)
        if plan.action == "apply":
            self.apply(plan.calibration)
        elif plan.action == "reapply":
            display = self.display
            self.worker.submit(lambda: engine.apply_saved(display), lambda _msg: self.rescan())

    def apply(self, calibration, display=None, done=None):
        """Back up → apply → ask to keep (reverts by itself) → finish. done(state, message) for callers."""
        self.apply_many([(display or self.display, calibration)], done)

    def apply_many(self, pairs, done=None):
        """Apply to one or several screens: back each up, apply, one keep/revert for all, then finish."""
        self.applying = True
        title = pairs[0][1].title if len(pairs) == 1 else f"changes on {len(pairs)} screens"
        self._set_busy(True, f"Applying {title}…")
        if self.state and self.state.night_light and any(cal.ramp is not None for _d, cal in pairs):
            self.log("Night Light is on and uses the same color controls; it may undo this change.")

        def begin_all():
            begun = []
            try:
                for display, calibration in pairs:
                    begun.append((display, calibration, engine.begin(display, calibration)))
            except Exception:
                for entry in begun:  # one screen failed: put the ones already changed back
                    engine.finish(*entry, keep=False)
                raise
            return begun

        def failed(exc):
            self.applying = False
            self.pending = None
            self._error(exc)
            if done:
                done("error", f"Not applied: {exc}")

        def applied(begun):
            self.pending = begun
            if len(begun) == 1:
                summary = f"{begun[0][1].title}\nChanged: {begun[0][1].changes()}"
            else:
                summary = "\n".join(f"{display.name}: {cal.changes()}" for display, cal, _snap in begun)
            keep = confirm.ask_keep(begun[0][0] if len(begun) == 1 else self.display, self.timeout,
                                    master=self.root, summary=summary)
            if self.closing:
                return  # quit() already undid it

            def finish_all():
                problems = []
                for entry in begun:
                    problems += engine.finish(*entry, keep=keep)
                return problems

            self.status("Keeping…" if keep else "Switching back…")
            self.worker.submit(finish_all, lambda problems: finished(keep, problems), failed)

        def finished(keep, problems):
            self.applying = False
            self.pending = None
            if keep:
                message = f"Kept {title if len(pairs) > 1 else f'“{title}”'}."
                if self.camera is not None:
                    self.camera.screen_changed()
            else:
                message = f"Switched back from {title if len(pairs) > 1 else f'“{title}”'}." + (
                    f" Problems: {'; '.join(problems)}" if problems else "")
            self.log(message)
            if done:
                done("kept" if keep else "reverted", message)
            self.rescan()

        self.worker.submit(begin_all, applied, failed)

    def undo(self, kind: str):
        if kind == "original" and not messagebox.askyesno(
                "Restore original settings",
                "Put this screen back the way it was before this app first changed it?", parent=self.root):
            return
        display = self.display
        self.applying = True
        self._set_busy(True, "Restoring…")

        def done(problems):
            self.applying = False
            self.log(f"Restored the {'original' if kind == 'original' else 'previous'} settings."
                     + (f" Problems: {'; '.join(problems)}" if problems else ""))
            if self.camera is not None:
                self.camera.screen_changed()
            self.rescan()

        def failed(exc):
            self.applying = False
            self._error(exc)

        self.worker.submit(lambda: engine.undo(display, kind), done, failed)

    def load_icc(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose an ICC profile",
                                          filetypes=[("ICC profiles", "*.icc *.icm"), ("All files", "*.*")])
        if not path:
            return
        try:
            cal = profiles.from_icc_file(path)
        except (OSError, ValueError) as exc:
            messagebox.showwarning("ICC profile", str(exc), parent=self.root)
            return
        self.candidates.insert(0, cal)
        self.pages["overview"].refresh()
        if messagebox.askyesno("ICC profile", "Remember it for this monitor, so it's found automatically next time?",
                               parent=self.root):
            import shutil

            profiles.PROFILE_DIR.mkdir(exist_ok=True)
            shutil.copyfile(path, profiles.PROFILE_DIR / f"{self.display.pnp_id}_{os.path.basename(path)}")
            self.log(f"Saved the ICC profile for {self.display.name}.")

    def search_online(self):
        menu = tk.Menu(self.root, tearoff=False)
        for label, url in profiles.search_urls(self.display):
            menu.add_command(label=label, command=lambda u=url: webbrowser.open(u))
        menu.add_separator()
        menu.add_command(label="Open the profiles folder (put downloaded .icc/.icm files here)",
                         command=lambda: self.open_folder(profiles.PROFILE_DIR))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    # --- options ------------------------------------------------------------------------------

    def _save_settings(self):
        backup.save_settings(self.settings)

    def toggle_auto(self, on: bool):
        self.settings["auto_on_launch"] = on
        self._save_settings()

    def toggle_guard(self, on: bool):
        self.settings["guard"] = on
        self._save_settings()

    def toggle_uploads(self, on: bool):
        self.settings["keep_uploads"] = on
        self._save_settings()
        if self.camera is not None:
            self.camera.keep_uploads = on

    def toggle_startup(self, on: bool):
        try:
            startup.enable() if on else startup.disable()
            self.log("Your calibration will be put back at every sign-in." if on else "Removed the sign-in shortcut.")
        except Exception as exc:
            log.exception("startup shortcut")
            messagebox.showwarning("Monitor Calibrator", f"Couldn't change the sign-in shortcut: {exc}", parent=self.root)
            self.pages["history"].refresh()

    def _guard(self):
        """Put kept calibrations back when Windows resets them (sleep, sign-in, Night Light switching off)."""
        try:
            if self.settings.get("guard", True) and not (self.busy or self.applying or self.closing):
                self.worker.submit(engine.guard_kept, lambda messages: [self.log(m) for m in messages],
                                   lambda exc: log.warning("guard: %s", exc))
        finally:
            if not self.closing:
                self.root.after(5000, self._guard)

    # --- phone camera -------------------------------------------------------------------------

    def install_extras(self, specs: list[str], why: str, then=None):
        """Offer to pip-install missing extras, then carry on with `then` once they're in."""
        if not specs or self.busy:
            return
        if not messagebox.askyesno("Install extras", f"{why}\n\nInstall {', '.join(specs)} now? It uses pip and "
                                                     "takes about a minute.", parent=self.root):
            return
        self._set_busy(True, "Installing the extras…")

        def done(result):
            worked, output = result
            self._set_busy(False, "Installed." if worked else "Couldn't install the extras.")
            self.log(f"Installed {', '.join(specs)}." if worked else f"Installing {', '.join(specs)} failed.")
            if worked:
                if then:
                    then()
                else:
                    self.render()
            else:
                messagebox.showwarning("Install extras", f"pip couldn't install them:\n\n{output}\n\n"
                                                         "Try running: python install.py", parent=self.root)

        self.worker.submit(lambda: requirements.install(specs), done)

    def start_camera(self):
        if self.camera is not None:
            return
        try:
            from .camera.server import CameraSession
        except ImportError:
            self.install_extras(requirements.missing_packages() or ["pillow>=11.0"],
                                "The phone camera needs Pillow, which isn't installed yet.", then=self.start_camera)
            return
        self.camera = CameraSession(self.display, self.camera_events.put)
        self.camera.keep_uploads = self.settings.get("keep_uploads", False)
        try:
            self.camera.start()
        except OSError as exc:
            self.camera = None
            messagebox.showwarning("Phone camera", f"Couldn't start: {exc}", parent=self.root)
            return
        self.device_shown = _REDRAW
        self.log(f"Phone link ready for {self.display.name}. One phone at a time.")
        self.pages["camera"].refresh()

    def stop_camera(self):
        if self.camera is None:
            return
        self.show_chart(False)
        self.camera.stop()
        self.camera = None
        self.latest_photo = None
        self.pages["camera"].result = None
        self.log("Phone link closed.")
        self.pages["camera"].refresh()

    def disconnect_device(self):
        if self.camera is None:
            return
        self.camera.release_device(new_link=True)
        self.latest_photo = None
        self.device_shown = _REDRAW
        self.log("Disconnected the phone. The old link and QR code no longer work.")
        self.pages["camera"].refresh()

    def set_camera_mode(self, mode: str):
        """single: measure the screen this window is on. match: every screen in one photo."""
        if self.camera is None:
            self.start_camera()
        if self.camera is None:
            return
        self.show_chart(False)
        self.camera.set_mode(mode, self.displays if mode == "match" else None)
        self.latest_photo = None
        self.pages["camera"].result = None
        self.log("Phone camera: matching every screen in one photo." if mode == "match" else
                 f"Phone camera: measuring {self.display.name}.")
        self.pages["camera"].refresh()

    def start_camera_match(self):
        self.show_page("camera")
        self.set_camera_mode("match")

    def show_chart(self, show: bool):
        if show and self.busy:
            return  # never cover a keep/revert dialog with the full-screen chart
        if show and not self.chart_windows:
            if self.camera is None:
                self.start_camera()
            if self.camera is None:
                return
            screens = self.camera.match_displays if self.camera.mode == "match" else [self.camera.display]
            try:
                for display in screens:
                    # A fresh code each time and per screen: only photos of these very charts are accepted,
                    # and each chart in a photo is tied to the screen that showed it.
                    self.chart_windows[display.key] = ChartWindow(self.root, display, self._chart_closed,
                                                                  self.camera.new_chart_code(display.key))
            except ImportError:
                messagebox.showwarning("Phone camera", "Showing the chart needs Pillow: python -m pip install pillow",
                                       parent=self.root)
                return
            self.camera.set_chart_visible(True)
        elif not show and self.chart_windows:
            self._chart_closed()

    def _chart_closed(self):
        """Closing one chart (Esc or a click) closes them all."""
        windows, self.chart_windows = list(self.chart_windows.values()), {}
        for window in windows:
            window.close()
        if self.camera:
            self.camera.set_chart_visible(False)

    def _camera_poll(self):
        try:
            self.poll_count += 1
            if self.camera is not None and self.poll_count % 10 == 0:  # about once a second
                info = self.camera.device_info()
                shown = (info["kind"], info["address"], info["since"]) if info else None
                if shown != self.device_shown:
                    if info and self.device_shown is not _REDRAW:
                        self.log(f"{info['kind']} connected to the phone camera check.")
                    self.device_shown = shown
                    self.pages["camera"].refresh()
            while True:
                event = self.camera_events.get_nowait()
                if event[0] == "chart":
                    self.show_chart(event[1])
                elif event[0] == "photo":
                    check = event[2]
                    self.log("Photo received" + (f" (chart {check['code']} recognized)." if check["ok"] else
                                                 ", not confirmed yet."))
                elif event[0] == "analysis":
                    self._camera_result(event[1], event[2])
                elif event[0] == "apply":
                    self._camera_apply([(self.camera.display, event[1])])
                elif event[0] == "apply_many":
                    self._camera_apply(event[1])
        except queue.Empty:
            pass
        finally:
            if not self.closing:
                self.root.after(100, self._camera_poll)

    def _camera_result(self, photo_id, result):
        if result.get("mode") == "match":
            self.latest_photo = photo_id if result.get("ok") else None
            self.pages["camera"].result = result
            self.pages["camera"].refresh()
            self.log("Compared the screens." if result.get("ok") else
                     "Couldn't compare the screens: " + " ".join(result.get("warnings", [])[:2]))
            return
        check = result.get("verification") or {}
        self.latest_photo = photo_id if check.get("ok") and result.get("ok") else None
        self.pages["camera"].result = result
        self.pages["camera"].refresh()
        if check and not check.get("ok"):
            self.log("Photo rejected: " + " ".join(result.get("warnings", [])))
        elif result.get("ok"):
            self.log("Measured: " + result["summary"])

    def apply_camera(self, options):
        if self.camera is None or self.latest_photo is None:
            messagebox.showinfo("Phone camera", "Take and analyse a photo first.", parent=self.root)
            return
        try:
            self.camera.request_apply(self.latest_photo, options)
        except ValueError as exc:
            messagebox.showwarning("Phone camera", str(exc), parent=self.root)

    def _camera_apply(self, pairs):
        self.show_chart(False)
        session = self.camera
        if self.busy or session is None:
            if session:
                session.finished_apply("error", "The PC is busy with another change. Try again in a moment.")
            return
        self.apply_many(pairs, done=session.finished_apply)


def run(start_display, timeout: int = 15, auto_on_launch: bool | None = None) -> None:
    root = tk.Tk()
    App(root, start_display, timeout, auto_on_launch)
    root.mainloop()
