"""Detect Windows Night Light.

Night Light tints the picture by rewriting the same GPU gamma ramp this tool
uses, which is why it also turns an external monitor yellow. When it is on, it
will fight any gamma-ramp calibration; DDC/CI (monitor-side) changes stack with it.
"""
from __future__ import annotations

import winreg

STATE_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
    r"\default$windows.data.bluelightreduction.bluelightreductionstate"
    r"\windows.data.bluelightreduction.bluelightreductionstate"
)


def is_enabled() -> bool | None:
    """True/False, or None if the (undocumented) state blob is missing or unfamiliar."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STATE_KEY) as key:
            data, _ = winreg.QueryValueEx(key, "Data")
    except OSError:
        return None
    if len(data) < 19:
        return None
    # Byte 18 is 0x15 while Night Light is active and 0x13 when it is off.
    return {0x15: True, 0x13: False}.get(data[18])
