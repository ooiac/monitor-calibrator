"""Look and feel.

The interface is deliberately colorless: every gray is neutral (R = G = B), like the
workspace of grading and photo tools, because tinted chrome next to the picture skews
how you judge color. The only colors come from the 75% broadcast color bars, used as
the logo and for status. Headings and readouts use Bahnschrift, Windows' DIN face.
"""
from __future__ import annotations

import ctypes
import tkinter as tk
import winreg
from tkinter import font as tkfont
from tkinter import ttk

from .. import winapi

BARS = ("#bfbfbf", "#bfbf00", "#00bfbf", "#00bf00", "#bf00bf", "#bf0000", "#0000bf")  # SMPTE 75% bars

DARK = {
    "bg": "#202020", "side": "#191919", "panel": "#282828", "raised": "#303030", "line": "#3a3a3a",
    "line_strong": "#505050", "text": "#e6e6e6", "dim": "#a0a0a0", "faint": "#6c6c6c",
    "action": "#e6e6e6", "action_text": "#161616", "action_hover": "#ffffff",
    "ok": "#5fb865", "warn": "#d4bf3a", "error": "#dc5e52", "info": "#43b9c3",
}
LIGHT = {
    "bg": "#e9e9e9", "side": "#dddddd", "panel": "#f2f2f2", "raised": "#f8f8f8", "line": "#cbcbcb",
    "line_strong": "#a9a9a9", "text": "#181818", "dim": "#545454", "faint": "#8a8a8a",
    "action": "#181818", "action_text": "#f4f4f4", "action_hover": "#000000",
    "ok": "#2e8a38", "warn": "#866f00", "error": "#b13a2f", "info": "#1d7c86",
}

C: dict = dict(DARK)  # current palette, filled by apply()
F: dict = {}  # current fonts


def windows_prefers_dark() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except OSError:
        return True


def dark_title_bar(window: tk.Misc, dark: bool) -> None:
    if winapi.dwmapi is None:
        return
    window.update_idletasks()
    hwnd = winapi.user32.GetParent(window.winfo_id()) or window.winfo_id()
    flag = ctypes.c_int(1 if dark else 0)
    for attribute in (20, 19):  # 20 on current Windows, 19 on early Windows 10 builds
        if winapi.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(flag), ctypes.sizeof(flag)) == 0:
            break


def _pick(available, *names, fallback="Segoe UI"):
    return next((n for n in names if n in available), fallback)


def apply(root: tk.Tk) -> dict:
    dark = windows_prefers_dark()
    C.clear()
    C.update(DARK if dark else LIGHT)
    C["dark"] = dark
    available = set(tkfont.families(root))
    text = _pick(available, "Segoe UI Variable Text", "Segoe UI")
    din = _pick(available, "Bahnschrift", fallback=text)
    din_bold = _pick(available, "Bahnschrift SemiBold", fallback=din)
    din_narrow = _pick(available, "Bahnschrift SemiBold SemiConden", "Bahnschrift SemiCondensed", fallback=din_bold)
    din_light = _pick(available, "Bahnschrift Light", fallback=din)
    F.update({
        "body": (text, 10),
        "small": (text, 9),
        "strong": (_pick(available, "Segoe UI Variable Text Semibold", "Segoe UI Semibold", fallback=text), 10),
        "caps": (din_narrow, 9),  # section labels, always UPPERCASE
        "h1": (din_bold, 21),
        "h2": (din_bold, 13),
        "state": (din_bold, 17),
        "readout": (din_light, 22),
        "value": (din, 11),
        "brand": (din_narrow, 11),
        "mono": (_pick(available, "Cascadia Mono", "Consolas"), 9),
    })
    root.configure(background=C["bg"])
    # Classic Tk widgets only: a bare "*Font" would also override the ttk styles' fonts.
    for widget_class in ("Label", "Button", "Entry", "Text", "Listbox", "Menu", "Checkbutton"):
        root.option_add(f"*{widget_class}.font", F["body"])
    for key, value in (("background", C["raised"]), ("foreground", C["text"]),
                       ("selectBackground", C["line_strong"]), ("selectForeground", C["text"])):
        root.option_add(f"*TCombobox*Listbox.{key}", value)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=C["bg"], foreground=C["text"], font=F["body"], bordercolor=C["line"],
                    lightcolor=C["bg"], darkcolor=C["bg"], troughcolor=C["line"], focuscolor=C["line_strong"],
                    selectbackground=C["line_strong"], selectforeground=C["text"], insertcolor=C["text"])
    for prefix, back in (("", C["bg"]), ("Panel.", C["panel"])):
        style.configure(f"{prefix}TFrame", background=back)
        style.configure(f"{prefix}TLabel", background=back, foreground=C["text"])
        style.configure(f"{prefix}Dim.TLabel", background=back, foreground=C["dim"])
        style.configure(f"{prefix}Small.TLabel", background=back, foreground=C["dim"], font=F["small"])
        style.configure(f"{prefix}Strong.TLabel", background=back, foreground=C["text"], font=F["strong"])
        style.configure(f"{prefix}Caps.TLabel", background=back, foreground=C["dim"], font=F["caps"])
        style.configure(f"{prefix}H1.TLabel", background=back, foreground=C["text"], font=F["h1"])
        style.configure(f"{prefix}H2.TLabel", background=back, foreground=C["text"], font=F["h2"])
        style.configure(f"{prefix}State.TLabel", background=back, foreground=C["text"], font=F["state"])
        style.configure(f"{prefix}Readout.TLabel", background=back, foreground=C["text"], font=F["readout"])
        style.configure(f"{prefix}Value.TLabel", background=back, foreground=C["text"], font=F["value"])
        style.configure(f"{prefix}TCheckbutton", background=back, foreground=C["text"], indicatorbackground=C["raised"],
                        indicatorforeground=C["text"], indicatormargin=(0, 0, 8, 0))
        style.map(f"{prefix}TCheckbutton", background=[("active", back)],
                  indicatorbackground=[("selected", C["action"]), ("active", C["line"])],
                  indicatorforeground=[("selected", C["action_text"])])
        # Secondary buttons are outlined in the container's own color.
        style.configure(f"{prefix}TButton", background=back, foreground=C["text"], bordercolor=C["line_strong"],
                        lightcolor=back, darkcolor=back, relief="solid", borderwidth=1, padding=(12, 5), focusthickness=0)
        style.map(f"{prefix}TButton", background=[("pressed", C["line"]), ("active", C["raised"])],
                  foreground=[("disabled", C["faint"])], bordercolor=[("disabled", C["line"]), ("active", C["text"])])

    style.configure("Primary.TButton", background=C["action"], foreground=C["action_text"], bordercolor=C["action"],
                    lightcolor=C["action"], darkcolor=C["action"], font=F["strong"], padding=(16, 6))
    style.map("Primary.TButton", background=[("disabled", C["line"]), ("pressed", C["action_hover"]),
                                             ("active", C["action_hover"])],
              foreground=[("disabled", C["faint"])], bordercolor=[("disabled", C["line"])],
              lightcolor=[("disabled", C["line"])], darkcolor=[("disabled", C["line"])])

    style.configure("Horizontal.TScale", background=C["text"], troughcolor=C["line"], bordercolor=C["panel"],
                    lightcolor=C["text"], darkcolor=C["text"], sliderthickness=12, gripcount=0)
    style.map("Horizontal.TScale", background=[("active", C["action_hover"])])
    style.configure("TCombobox", fieldbackground=C["raised"], background=C["raised"], foreground=C["text"],
                    arrowcolor=C["dim"], bordercolor=C["line_strong"], lightcolor=C["raised"], darkcolor=C["raised"], padding=3)
    style.map("TCombobox", fieldbackground=[("readonly", C["raised"])], foreground=[("readonly", C["text"])],
              selectbackground=[("readonly", C["raised"])], selectforeground=[("readonly", C["text"])])
    style.configure("TEntry", fieldbackground=C["raised"], foreground=C["text"], bordercolor=C["line_strong"],
                    lightcolor=C["raised"], darkcolor=C["raised"], padding=4)
    style.configure("TProgressbar", background=C["text"], troughcolor=C["line"], bordercolor=C["bg"],
                    lightcolor=C["text"], darkcolor=C["text"], thickness=3)
    style.configure("Vertical.TScrollbar", background=C["line"], troughcolor=C["bg"], bordercolor=C["bg"],
                    lightcolor=C["line"], darkcolor=C["line"], arrowcolor=C["dim"], gripcount=0, arrowsize=11)
    style.map("Vertical.TScrollbar", background=[("active", C["line_strong"])])
    return C
