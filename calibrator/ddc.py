"""DDC/CI: change the monitor's own settings (the ones in its on-screen menu).

Codes come from the VESA MCCS standard. Laptop panels almost never answer;
external monitors usually do if "DDC/CI" is enabled in their menu.
"""
from __future__ import annotations

import ctypes
import re
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field

from . import winapi as w

BRIGHTNESS = 0x10
CONTRAST = 0x12
COLOR_PRESET = 0x14
RED_GAIN, GREEN_GAIN, BLUE_GAIN = 0x16, 0x18, 0x1A
RED_BLACK, GREEN_BLACK, BLUE_BLACK = 0x6C, 0x6E, 0x70

NAMES = {
    BRIGHTNESS: "brightness",
    CONTRAST: "contrast",
    COLOR_PRESET: "color_preset",
    RED_GAIN: "red_gain",
    GREEN_GAIN: "green_gain",
    BLUE_GAIN: "blue_gain",
    RED_BLACK: "red_black_level",
    GREEN_BLACK: "green_black_level",
    BLUE_BLACK: "blue_black_level",
}
CODES = {name: code for code, name in NAMES.items()}
LABELS = {  # for people
    BRIGHTNESS: "brightness", CONTRAST: "contrast", COLOR_PRESET: "color mode",
    RED_GAIN: "red", GREEN_GAIN: "green", BLUE_GAIN: "blue",
    RED_BLACK: "red black level", GREEN_BLACK: "green black level", BLUE_BLACK: "blue black level",
}
GAINS = (RED_GAIN, GREEN_GAIN, BLUE_GAIN)
BLACK_LEVELS = (RED_BLACK, GREEN_BLACK, BLUE_BLACK)
COLOR_CODES = (COLOR_PRESET, *GAINS, *BLACK_LEVELS)
MANAGED = (BRIGHTNESS, CONTRAST, COLOR_PRESET, *GAINS, *BLACK_LEVELS)

# Preset first so gains land in the right mode; brightness/contrast last.
APPLY_ORDER = (COLOR_PRESET, *GAINS, *BLACK_LEVELS, CONTRAST, BRIGHTNESS)
# Undo in reverse: put the user-mode gains back while still in that mode, then the preset.
RESTORE_ORDER = (*GAINS, *BLACK_LEVELS, COLOR_PRESET, CONTRAST, BRIGHTNESS)

SRGB, NATIVE, K6500, USER1 = 0x01, 0x02, 0x05, 0x0B
PRESETS = {
    0x01: "sRGB",
    0x02: "Native",
    0x03: "4000 K",
    0x04: "5000 K",
    0x05: "6500 K",
    0x06: "7500 K",
    0x07: "8200 K",
    0x08: "9300 K",
    0x09: "10000 K",
    0x0A: "11500 K",
    0x0B: "User 1",
    0x0C: "User 2",
    0x0D: "User 3",
}
USER_PRESETS = (0x0B, 0x0C, 0x0D)

# One DDC conversation at a time: the I2C bus does not like interleaved requests.
_bus_lock = threading.RLock()


class DdcError(Exception):
    pass


@dataclass
class Capabilities:
    raw: str
    model: str = ""
    vcp: dict[int, list[int] | None] = field(default_factory=dict)

    def supports(self, code: int) -> bool:
        return code in self.vcp

    def presets(self) -> list[int]:
        return [v for v in (self.vcp.get(COLOR_PRESET) or []) if v in PRESETS]


def _section(text: str, name: str) -> str:
    match = re.search(rf"\b{name}\(", text, re.IGNORECASE)
    if not match:
        return ""
    depth, start = 1, match.end()
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return text[start:]


def parse_capabilities(text: str) -> Capabilities:
    """Parse '(prot(monitor)type(lcd)model(X)vcp(10 12 14(01 05 0B) 16 ...))'."""
    vcp: dict[int, list[int] | None] = {}
    last, depth = None, 0
    for token in re.findall(r"[0-9A-Fa-f]{2}|\(|\)", _section(text, "vcp")):
        if token == "(":
            depth += 1
            if depth == 1 and last is not None:
                vcp[last] = []
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            last = int(token, 16)
            vcp.setdefault(last, None)
        elif depth == 1 and last is not None:
            vcp[last].append(int(token, 16))
    return Capabilities(raw=text, model=_section(text, "model").strip(), vcp=vcp)


class Monitor:
    """Physical-monitor handle behind one HMONITOR. Use as a context manager."""

    def __init__(self, hmonitor: int):
        count = wintypes.DWORD()
        if not w.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)) or not count.value:
            raise DdcError("Windows has no DDC/CI handle for this display")
        self._array = (w.PHYSICAL_MONITOR * count.value)()
        if not w.dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count.value, self._array):
            raise DdcError("could not open the DDC/CI handle")
        self._count = count.value
        self.handle = self._array[0].hPhysicalMonitor
        self.description = self._array[0].szPhysicalMonitorDescription

    def __enter__(self):
        _bus_lock.acquire()
        return self

    def __exit__(self, *_exc):
        try:
            w.dxva2.DestroyPhysicalMonitors(self._count, self._array)
        finally:
            _bus_lock.release()

    def get(self, code: int, retries: int = 3) -> tuple[int, int]:
        """Return (current, maximum) for a VCP code."""
        current, maximum = wintypes.DWORD(), wintypes.DWORD()
        for attempt in range(retries):
            if w.dxva2.GetVCPFeatureAndVCPFeatureReply(
                self.handle, code, None, ctypes.byref(current), ctypes.byref(maximum)
            ):
                return current.value, maximum.value
            time.sleep(0.05 * (attempt + 1))
        raise DdcError(f"monitor did not answer when reading {NAMES.get(code, hex(code))}")

    def set(self, code: int, value: int, retries: int = 3) -> None:
        for attempt in range(retries):
            if w.dxva2.SetVCPFeature(self.handle, code, int(value)):
                time.sleep(0.06)  # MCCS asks for a pause after each write
                return
            time.sleep(0.05 * (attempt + 1))
        raise DdcError(f"monitor refused {NAMES.get(code, hex(code))} = {value}")

    def capabilities(self) -> Capabilities:
        length = wintypes.DWORD()
        if not w.dxva2.GetCapabilitiesStringLength(self.handle, ctypes.byref(length)) or not length.value:
            raise DdcError("monitor did not report its capabilities")
        buffer = ctypes.create_string_buffer(length.value)
        if not w.dxva2.CapabilitiesRequestAndCapabilitiesReply(self.handle, buffer, length.value):
            raise DdcError("monitor did not report its capabilities")
        return parse_capabilities(buffer.value.decode("ascii", "replace"))


def open_monitor(hmonitor: int) -> Monitor:
    return Monitor(hmonitor)


def preset_value(value) -> int:
    """Accept 0x0B, 11, 'user1', 'User 1', 'srgb', '6500k' …"""
    if isinstance(value, int):
        return value
    text = str(value).strip().lower().replace(" ", "")
    if text.startswith("0x") or text.isdigit():
        return int(text, 0)
    for number, label in PRESETS.items():
        if label.lower().replace(" ", "") == text:
            return number
    raise ValueError(f"unknown color preset {value!r}")


def code_for(name: str) -> int:
    text = str(name).strip().lower()
    if text in CODES:
        return CODES[text]
    if text.startswith("0x"):
        return int(text, 16)
    raise ValueError(f"unknown monitor setting {name!r}")
