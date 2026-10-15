"""GPU gamma ramps: the mechanism behind Night Light, f.lux and ICC 'vcgt' curves.

A ramp is three 256-entry lookup tables (R, G, B) the graphics card applies to
every pixel before it leaves the cable, so it works on any screen, including
laptop panels. It does not touch the monitor's own settings, and Windows
resets it on reboot, sleep or a display change.
"""
from __future__ import annotations

import ctypes
import math
from contextlib import contextmanager

from . import colormath
from . import winapi as w

SIZE = 256
MAX = 65535
DISPLAY_GAMMA = 2.2


class GammaError(Exception):
    pass


@contextmanager
def _device_context(gdi_name: str):
    hdc = w.gdi32.CreateDCW(None, gdi_name, None, None)
    if not hdc:
        raise GammaError(f"could not open {gdi_name}")
    try:
        yield hdc
    finally:
        w.gdi32.DeleteDC(hdc)


def read(gdi_name: str) -> list[list[int]]:
    ramp = w.GammaRamp()
    with _device_context(gdi_name) as hdc:
        if not w.gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
            raise GammaError("the graphics driver does not expose a gamma ramp for this display")
    return [list(channel) for channel in ramp]


def write(gdi_name: str, ramp: list[list[int]]) -> None:
    table = w.GammaRamp()
    for c in range(3):
        for i in range(SIZE):
            table[c][i] = max(0, min(MAX, int(ramp[c][i])))
    with _device_context(gdi_name) as hdc:
        if not w.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(table)):
            raise GammaError(
                "Windows rejected the gamma ramp. Strong corrections are blocked unless the "
                "GdiIcmGammaRange registry value is 256 (run: calibrate.py --unlock-gamma-range, then reboot)."
            )


def identity() -> list[list[int]]:
    return [[i * 257 for i in range(SIZE)] for _ in range(3)]


SHADOW_STRENGTH = 0.9  # keeps the curve strictly rising at both ends of the shadows range


def toe(x: float, shadows: float) -> float:
    """Reshape the tones just above black. shadows runs from -1 to 1.

    Below 0 they get darker, so blacks look deeper but the darkest detail sinks into black.
    Above 0 they get lighter, showing more shadow detail on screens whose black is grayish.
    Pure black and pure white never move, and the result always keeps rising.
    """
    return x + SHADOW_STRENGTH * max(-1.0, min(1.0, shadows)) * x * (1 - x) ** 4


def build(gains=(1.0, 1.0, 1.0), exponent=(1.0, 1.0, 1.0), shadows: float = 0.0) -> list[list[int]]:
    """gains: linear-light multipliers for white. exponent: applied to the signal (1 = unchanged)."""
    ramp = []
    for gain, exp in zip(gains, exponent):
        scale = max(0.0, gain) ** (1 / DISPLAY_GAMMA)  # linear gain -> signal gain
        ramp.append([round(MAX * min(1.0, scale * toe(i / (SIZE - 1), shadows) ** exp)) for i in range(SIZE)])
    return ramp


def from_settings(settings: dict) -> list[list[int]]:
    """Ramp from human knobs.

    {"temperature": K, "tint": ±100, "gamma": 2.2, "shadows": -10..10, "white_level": 30..100 (%),
     "gains": [r, g, b]}
    """
    gains = colormath.white_gains(
        float(settings.get("temperature", colormath.NEUTRAL_K)), float(settings.get("tint", 0))
    )
    extra = settings.get("gains") or (1, 1, 1)
    level = max(0.05, min(1.0, float(settings.get("white_level", 100)) / 100))
    gains = [a * float(b) * level for a, b in zip(gains, extra)]
    exponent = float(settings.get("gamma", DISPLAY_GAMMA)) / DISPLAY_GAMMA
    return build(gains, (exponent,) * 3, float(settings.get("shadows", 0)) / 10)


def is_neutral_settings(settings: dict) -> bool:
    return (
        abs(float(settings.get("temperature", colormath.NEUTRAL_K)) - colormath.NEUTRAL_K) < 1
        and abs(float(settings.get("tint", 0))) < 0.01
        and abs(float(settings.get("gamma", DISPLAY_GAMMA)) - DISPLAY_GAMMA) < 0.005
        and abs(float(settings.get("shadows", 0))) < 0.01
        and abs(float(settings.get("white_level", 100)) - 100) < 0.01
        and all(abs(float(g) - 1) < 1e-3 for g in settings.get("gains") or (1, 1, 1))
    )


def sample(ramp_channel, position: float) -> float:
    """Evaluate one ramp channel (0..65535 table) at 0..1, linearly interpolated, as 0..1."""
    x = min(1.0, max(0.0, position)) * (SIZE - 1)
    lo = int(x)
    hi = min(lo + 1, SIZE - 1)
    return (ramp_channel[lo] + (ramp_channel[hi] - ramp_channel[lo]) * (x - lo)) / MAX


def resample(curve: list[float]) -> list[int]:
    """Values in 0..1 of any length -> 256 entries in 0..65535."""
    n = len(curve)
    if n == 1:
        return [round(MAX * curve[0])] * SIZE
    out = []
    for i in range(SIZE):
        pos = i * (n - 1) / (SIZE - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        value = curve[lo] + (curve[hi] - curve[lo]) * (pos - lo)
        out.append(round(MAX * min(1.0, max(0.0, value))))
    return out


def close(a, b, tolerance: int = 400) -> bool:
    return all(abs(x - y) <= tolerance for ca, cb in zip(a, b) for x, y in zip(ca, cb))


def is_identity(ramp) -> bool:
    # Drivers report the neutral ramp as i*256 or i*257; both are "no change".
    return close(ramp, identity())


def _exponent(channel) -> float | None:
    top, mid = channel[-1], channel[SIZE // 2]
    if top <= 0 or mid <= 0:
        return None
    return math.log(mid / top) / math.log((SIZE // 2) / (SIZE - 1))


def analyse(ramp) -> dict:
    """Reverse-engineer a ramp: per-channel gain, implied color temperature and gamma."""
    tops = [channel[-1] / MAX for channel in ramp]
    gains = [t**DISPLAY_GAMMA for t in tops]
    top = max(gains) or 1.0
    relative = [g / top for g in gains]
    exponents = [e for e in (_exponent(ch) for ch in ramp) if e]
    average = sum(exponents) / len(exponents) if exponents else 1.0
    return {
        "relative_gains": relative,
        "kelvin": colormath.estimate_kelvin(relative) if max(relative) - min(relative) > 0.01 else 6500,
        "gamma": DISPLAY_GAMMA * average,
        "dimmed": max(tops) < 0.97,
    }


def describe(ramp) -> str:
    if is_identity(ramp):
        return "neutral (no GPU tint)"
    info = analyse(ramp)
    parts = []
    r, g, b = info["relative_gains"]
    if max(r, g, b) - min(r, g, b) > 0.01:
        tint = f"≈{info['kelvin']} K" if info["kelvin"] else "custom color"
        parts.append(f"tinted {tint} (R {r:.0%}, G {g:.0%}, B {b:.0%})")
    if abs(info["gamma"] - DISPLAY_GAMMA) > 0.05:
        parts.append(f"gamma ≈{info['gamma']:.2f}")
    if info["dimmed"]:
        parts.append("dimmed")
    return ", ".join(parts) or "slightly modified"
