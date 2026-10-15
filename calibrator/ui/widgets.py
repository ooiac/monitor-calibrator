"""Building blocks: sections, readouts, indicators, sliders, curve plot, screen map."""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from .. import colormath, gamma
from .theme import BARS, C, F


def clear(widget) -> None:
    for child in widget.winfo_children():
        child.destroy()


def section(parent, label: str, note: str = "", top: int = 26, panel: bool = False) -> ttk.Frame:
    """An uppercase label over a hairline. Returns the frame to put the section's content in."""
    prefix = "Panel." if panel else ""
    frame = ttk.Frame(parent, style=f"{prefix}TFrame")
    frame.pack(fill="x", pady=(top, 0))
    ttk.Label(frame, text=label.upper(), style=f"{prefix}Caps.TLabel").pack(anchor="w")
    tk.Frame(frame, bg=C["line"], height=1).pack(fill="x", pady=(5, 12))
    if note:
        ttk.Label(frame, text=note, style=f"{prefix}Dim.TLabel", wraplength=720, justify="left").pack(anchor="w", pady=(0, 12))
    return frame


class Panel(tk.Frame):
    """A slightly raised area with an optional colored edge on the left. Content goes in .body."""

    def __init__(self, parent, stripe: str | None = None, padding=(18, 16)):
        super().__init__(parent, bg=C["panel"])
        if stripe:
            tk.Frame(self, bg=stripe, width=3).pack(side="left", fill="y")
        self.body = ttk.Frame(self, style="Panel.TFrame", padding=padding)
        self.body.pack(side="left", fill="both", expand=True)


def indicator(parent, text: str, state: str = "on", panel: bool = False) -> tk.Frame:
    """A small square in a status color, then the text. state: on / off / warn."""
    back = C["panel"] if panel else C["bg"]
    frame = tk.Frame(parent, bg=back)
    color = {"on": C["ok"], "off": C["faint"], "warn": C["warn"]}[state]
    tk.Label(frame, text="■", bg=back, fg=color, font=F["small"]).pack(side="left")
    tk.Label(frame, text=text, bg=back, fg=C["text"] if state != "off" else C["faint"], font=F["body"]).pack(
        side="left", padx=(5, 0))
    return frame


class Note(tk.Frame):
    """A quiet remark with a colored edge: tips, warnings, errors."""

    def __init__(self, parent, text: str, kind: str = "warn", panel: bool = False, wrap: int = 680):
        back = C["panel"] if panel else C["bg"]
        super().__init__(parent, bg=back)
        tk.Frame(self, bg={"warn": C["warn"], "error": C["error"], "ok": C["ok"], "info": C["info"]}[kind],
                 width=2).pack(side="left", fill="y")
        tk.Label(self, text=text, bg=back, fg=C["text"], font=F["body"], wraplength=wrap, justify="left",
                 anchor="w", padx=12, pady=2).pack(side="left", fill="x", expand=True)


class InfoButton(tk.Canvas):
    """A small circled "i". Clicking it opens a short explanation beside it.

    The explanation closes on a click, Esc, clicking anywhere else, switching pages, or when its
    page is redrawn, so it never lingers over something unrelated.
    """

    _open: set = set()

    def __init__(self, parent, title: str, text: str, panel: bool = False):
        back = C["panel"] if panel else C["bg"]
        super().__init__(parent, width=18, height=18, bg=back, highlightthickness=0, bd=0, cursor="hand2")
        self.circle = self.create_oval(2, 2, 16, 16, outline=C["dim"])
        self.letter = self.create_text(9, 9, text="i", fill=C["dim"], font=(F["value"][0], 8, "bold"))
        self.title, self.text = title, text
        self.popup: tk.Toplevel | None = None
        self.closed_at = 0.0
        self.bind("<Button-1>", lambda _e: self.toggle())
        self.bind("<Enter>", lambda _e: self._tone(C["text"]))
        self.bind("<Leave>", lambda _e: self._tone(C["dim"]))
        self.bind("<Destroy>", lambda _e: self.close(), add="+")

    @classmethod
    def close_all(cls):
        for button in list(cls._open):
            button.close()

    def _tone(self, color):
        self.itemconfigure(self.circle, outline=color)
        self.itemconfigure(self.letter, fill=color)

    def toggle(self):
        if self.popup is not None:
            self.close()
            return
        if time.monotonic() - self.closed_at < 0.3:
            return  # this click is what just closed it (focus left the popup); don't reopen
        InfoButton.close_all()
        self.popup = tk.Toplevel(self)
        self.popup.overrideredirect(True)
        self.popup.attributes("-topmost", True)
        frame = tk.Frame(self.popup, bg=C["raised"], highlightthickness=1, highlightbackground=C["line_strong"])
        frame.pack()
        tk.Label(frame, text=self.title, bg=C["raised"], fg=C["text"], font=F["strong"], anchor="w").pack(
            fill="x", padx=16, pady=(14, 6))
        tk.Label(frame, text=self.text, bg=C["raised"], fg=C["text"], font=F["body"], wraplength=400,
                 justify="left", anchor="w").pack(fill="x", padx=16)
        tk.Label(frame, text="Click to close", bg=C["raised"], fg=C["faint"], font=F["small"], anchor="w").pack(
            fill="x", padx=16, pady=(10, 12))
        self.popup.update_idletasks()
        x, y = self.winfo_rootx() + 26, self.winfo_rooty() - 12
        bottom = self.winfo_toplevel().winfo_rooty() + self.winfo_toplevel().winfo_height()
        y = max(self.winfo_toplevel().winfo_rooty(), min(y, bottom - self.popup.winfo_reqheight()))
        self.popup.geometry(f"+{x}+{y}")
        for widget in (self.popup, frame, *frame.winfo_children()):
            widget.bind("<Button-1>", lambda _e: self.close())
        self.popup.bind("<Escape>", lambda _e: self.close())
        self.popup.bind("<FocusOut>", lambda _e: self.close())
        InfoButton._open.add(self)
        self.popup.focus_force()

    def close(self):
        InfoButton._open.discard(self)
        if self.popup is not None:
            popup, self.popup = self.popup, None
            self.closed_at = time.monotonic()
            try:
                popup.destroy()
            except tk.TclError:
                pass


class ScrollFrame(ttk.Frame):
    """A page that scrolls with the mouse wheel when its content is taller than the window."""

    def __init__(self, parent, padding=(34, 26, 34, 30)):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, padding=padding)
        self.item = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.item, width=e.width))
        self.bind("<Enter>", lambda _e: self.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))

    def _wheel(self, event):
        if self.body.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(255 * c))) for c in rgb)


def white_color(kelvin: float, tint: float = 0.0) -> str:
    """How a white at this temperature/tint looks, encoded for the screen."""
    return _hex(colormath.linear_to_srgb(g) for g in colormath.white_gains(kelvin, tint))


def temperature_color(t: float) -> str:
    return white_color(3000 + t * (9300 - 3000))


def tint_color(t: float) -> str:
    return white_color(6500, -30 + 60 * t)


class Slider:
    """Name on the left, value on the right, a thin color strip (optional) and the slider."""

    def __init__(self, parent, label, low, high, step, value, fmt, gradient=None, on_change=None, panel=False):
        prefix = "Panel." if panel else ""
        back = C["panel"] if panel else C["bg"]
        self.low, self.high, self.step, self.fmt, self.on_change = low, high, step, fmt, on_change
        self.initial = self._snap(value)
        self.frame = ttk.Frame(parent, style=f"{prefix}TFrame")
        top = ttk.Frame(self.frame, style=f"{prefix}TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=label, style=f"{prefix}TLabel").pack(side="left")
        self.text = ttk.Label(top, style=f"{prefix}Value.TLabel")
        self.text.pack(side="right")
        self.var = tk.DoubleVar(value=self.initial)
        ttk.Scale(self.frame, from_=low, to=high, variable=self.var, command=self._changed).pack(fill="x", pady=(3, 0))
        if gradient:
            strip = tk.Canvas(self.frame, height=3, highlightthickness=0, bd=0, bg=back)
            strip.pack(fill="x", padx=6)
            strip.bind("<Configure>", lambda e: self._paint(strip, gradient, e.width))
        self._show()

    def _snap(self, value):
        value = min(self.high, max(self.low, float(value)))
        return round(round(value / self.step) * self.step, 4)

    @staticmethod
    def _paint(strip, gradient, width):
        strip.delete("all")
        steps = 60
        for i in range(steps):
            strip.create_rectangle(i * width / steps, 0, (i + 1) * width / steps + 1, 4,
                                   fill=gradient(i / (steps - 1)), outline="")

    def _show(self):
        mark = "" if abs(self.var.get() - self.initial) < 1e-6 else "  •"
        self.text.configure(text=self.fmt.format(self.var.get()) + mark)

    def _changed(self, _value=None):
        self.var.set(self._snap(self.var.get()))
        self._show()
        if self.on_change:
            self.on_change()

    def get(self) -> float:
        return round(self.var.get(), 4)

    def set(self, value) -> None:
        self.var.set(self._snap(value))
        self._show()
        if self.on_change:
            self.on_change()

    @property
    def changed(self) -> bool:
        return abs(self.get() - self.initial) > 1e-6


class Tile(tk.Frame):
    """A preset: name and one line about it. Selected = marked edge."""

    def __init__(self, parent, title: str, subtitle: str, on_click, own=False):
        super().__init__(parent, bg=C["panel"], cursor="hand2")
        self.edge = tk.Frame(self, bg=C["panel"], width=3)
        self.edge.pack(side="left", fill="y")
        text = tk.Frame(self, bg=C["panel"])
        text.pack(side="left", fill="both", expand=True, padx=12, pady=9)
        self.parts = [self, text,
                      tk.Label(text, text=title + ("  (yours)" if own else ""), bg=C["panel"], fg=C["text"],
                               font=F["strong"], anchor="w", justify="left", wraplength=200),
                      tk.Label(text, text=subtitle, bg=C["panel"], fg=C["dim"], font=F["small"], anchor="w",
                               justify="left", wraplength=200)]
        self.parts[2].pack(fill="x")
        self.parts[3].pack(fill="x", pady=(1, 0))
        for part in self.parts:
            part.bind("<Button-1>", lambda _e: on_click())
            part.bind("<Enter>", lambda _e: self._paint(True))
            part.bind("<Leave>", lambda _e: self._paint(False))
        self.selected = False

    def _paint(self, hover):
        back = C["raised"] if hover or self.selected else C["panel"]
        for part in self.parts:
            part.configure(bg=back)
        self.edge.configure(bg=C["text"] if self.selected else back)

    def select(self, on: bool):
        self.selected = on
        self._paint(False)


class NavItem(tk.Frame):
    """A sidebar entry: text only, a bar on the left when it's the current page, an optional small note below."""

    def __init__(self, parent, text: str, on_click, note: str = ""):
        super().__init__(parent, bg=C["side"], cursor="hand2")
        self.bar = tk.Frame(self, bg=C["side"], width=2)
        self.bar.pack(side="left", fill="y")
        column = tk.Frame(self, bg=C["side"])
        column.pack(side="left", fill="x", expand=True, padx=(16, 10), pady=8)
        self.label = tk.Label(column, text=text, bg=C["side"], fg=C["dim"], font=F["body"], anchor="w")
        self.label.pack(fill="x")
        parts = [self, column, self.label]
        if note:
            hint = tk.Label(column, text=note.upper(), bg=C["side"], fg=C["warn"], font=F["caps"], anchor="w")
            hint.pack(fill="x")
            parts.append(hint)
        for part in parts:
            part.bind("<Button-1>", lambda _e: on_click())
            part.bind("<Enter>", lambda _e: self.label.configure(fg=C["text"]))
            part.bind("<Leave>", lambda _e: self._paint())
        self.active = False

    def _paint(self):
        self.label.configure(fg=C["text"] if self.active else C["dim"], font=F["strong"] if self.active else F["body"])
        self.bar.configure(bg=C["text"] if self.active else C["side"])

    def set_active(self, on: bool):
        self.active = on
        self._paint()


class ColorBars(tk.Canvas):
    """The logo: a strip of broadcast color bars."""

    def __init__(self, parent, width=84, height=10):
        super().__init__(parent, width=width, height=height, bg=C["side"], highlightthickness=0, bd=0)
        step = width / len(BARS)
        for i, color in enumerate(BARS):
            self.create_rectangle(i * step, 0, (i + 1) * step, height, fill=color, outline="")


class ScreenMap(tk.Canvas):
    """Your screens as Windows arranges them. The one this window is on is filled; click another to move there."""

    def __init__(self, parent, on_pick, width=180, height=96):
        super().__init__(parent, width=width, height=height, bg=C["side"], highlightthickness=0, bd=0)
        self.on_pick = on_pick
        self.size = (width, height)

    def draw(self, displays, current_key):
        self.delete("all")
        if not displays:
            return
        left = min(d.rect[0] for d in displays)
        top = min(d.rect[1] for d in displays)
        right = max(d.rect[2] for d in displays)
        bottom = max(d.rect[3] for d in displays)
        width, height = self.size
        scale = min((width - 4) / (right - left), (height - 4) / (bottom - top))
        oy = (height - (bottom - top) * scale) / 2
        for display in displays:
            x0, y0 = 2 + (display.rect[0] - left) * scale + 2, oy + (display.rect[1] - top) * scale + 2
            x1, y1 = 2 + (display.rect[2] - left) * scale - 2, oy + (display.rect[3] - top) * scale - 2
            here = display.key == current_key
            tag = f"screen{display.index}"
            self.create_rectangle(x0, y0, x1, y1, fill=C["text"] if here else C["side"],
                                  outline=C["text"] if here else C["line_strong"], tags=tag)
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=str(display.index), tags=tag, font=F["value"],
                             fill=C["side"] if here else C["dim"])
            self.tag_bind(tag, "<Button-1>", lambda _e, d=display: self.on_pick(d))
            self.tag_bind(tag, "<Enter>", lambda _e: self.configure(cursor="hand2"))
            self.tag_bind(tag, "<Leave>", lambda _e: self.configure(cursor=""))


CHANNEL_COLORS = {"gray": None, "red": BARS[5], "green": BARS[3], "blue": "#3f6fff"}


class ToneCurve(tk.Canvas):
    """Input → output plot. Curves are lists of y values for evenly spaced x from 0 to 1."""

    def __init__(self, parent, width=250, height=210, panel=False):
        super().__init__(parent, width=width, height=height, bg=C["panel"] if panel else C["bg"],
                         highlightthickness=0, bd=0)
        self.box = (30, 8, width - 8, height - 26)

    def draw(self, curves, reference=None, x_label="input", y_label="output"):
        self.delete("all")
        x0, y0, x1, y1 = self.box

        def point(x, y):
            return x0 + x * (x1 - x0), y1 - min(1.0, max(0.0, y)) * (y1 - y0)

        for t in (0.25, 0.5, 0.75):
            self.create_line(*point(t, 0), *point(t, 1), fill=C["line"])
            self.create_line(*point(0, t), *point(1, t), fill=C["line"])
        self.create_rectangle(x0, y0, x1, y1, outline=C["line_strong"])
        self.create_text(x1, y1 + 14, text=x_label, anchor="e", fill=C["faint"], font=F["small"])
        self.create_text(x0 - 8, y0, text=y_label, anchor="ne", fill=C["faint"], font=F["small"], angle=90)
        if reference:
            ys = reference
            self.create_line(*[c for i, y in enumerate(ys) for c in point(i / (len(ys) - 1), y)],
                             fill=C["faint"], dash=(4, 3), width=1)
        for color, ys in curves:
            self.create_line(*[c for i, y in enumerate(ys) for c in point(i / (len(ys) - 1), y)],
                             fill=color or C["text"], width=2, smooth=True)


def ramp_curves(ramp, points: int = 33):
    """The three GPU ramp channels as plottable curves."""
    return [(CHANNEL_COLORS[name], [gamma.sample(ramp[c], i / (points - 1)) for i in range(points)])
            for c, name in enumerate(("red", "green", "blue"))]


def power_curve(exponent: float, points: int = 33):
    return [(i / (points - 1)) ** exponent for i in range(points)]
