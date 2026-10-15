"""The "Keep this change?" dialog that goes back on its own."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def ask_keep(display, seconds: int = 15, master: tk.Misc | None = None, summary: str = "") -> bool:
    """Show the dialog centred on `display`. True only if the user clicks Keep in time.

    Closing the window, pressing Esc, or waiting it out all mean "go back".
    The focused button is "Go back", so a stray Enter is safe too.
    """
    own_root = master is None
    root = tk.Tk() if own_root else master
    if own_root:
        root.withdraw()

    win = tk.Toplevel(root)
    win.title("Keep this change?")
    win.resizable(False, False)
    win.attributes("-topmost", True)
    background = ttk.Style(win).lookup("TFrame", "background")
    if background:
        win.configure(background=background)

    kept = False
    remaining = seconds
    timer = None

    def finish(keep: bool):
        nonlocal kept
        kept = keep
        if timer is not None:
            win.after_cancel(timer)
        win.grab_release()
        win.destroy()

    body = ttk.Frame(win, padding=(22, 18))
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Keep this change?", style="H2.TLabel").pack(anchor="w")
    ttk.Label(body, text=display.name, style="Dim.TLabel").pack(anchor="w")
    if summary:
        ttk.Label(body, text=summary, wraplength=380, justify="left").pack(anchor="w", pady=(10, 0))
    countdown = ttk.Label(body, style="Dim.TLabel")
    countdown.pack(anchor="w", pady=(12, 16))

    buttons = ttk.Frame(body)
    buttons.pack(fill="x")
    back = ttk.Button(buttons, text="Go back", command=lambda: finish(False))
    back.pack(side="right")
    ttk.Button(buttons, text="Keep", style="Primary.TButton", command=lambda: finish(True)).pack(side="right", padx=(0, 8))

    def tick():
        nonlocal remaining, timer
        if remaining <= 0:
            timer = None
            finish(False)
            return
        countdown.config(text=f"Going back in {remaining} s unless you keep it.")
        remaining -= 1
        timer = win.after(1000, tick)

    def press_focused(_event):
        widget = win.focus_get()
        if hasattr(widget, "invoke"):
            widget.invoke()

    win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    win.bind("<Escape>", lambda _e: finish(False))
    win.bind("<Return>", press_focused)

    win.update_idletasks()
    left, top, right, bottom = display.rect
    x = left + (right - left - win.winfo_reqwidth()) // 2
    y = top + (bottom - top - win.winfo_reqheight()) // 2
    win.geometry(f"+{x}+{y}")
    win.deiconify()
    win.lift()
    win.focus_force()
    back.focus_set()
    win.grab_set()
    tick()
    root.wait_window(win)
    if own_root:
        root.destroy()
    return kept
