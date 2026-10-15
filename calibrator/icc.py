"""Minimal ICC profile reader: description text and 'vcgt' calibration curves.

A display ICC profile from a review site or a colorimeter usually carries a
'vcgt' tag (the gamma-ramp part of a calibration), which we can load directly.
"""
from __future__ import annotations

import ctypes
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from . import gamma
from . import winapi as w

COLOR_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "spool" / "drivers" / "color"


@dataclass
class IccProfile:
    path: Path
    description: str
    device_class: str
    vcgt: list[list[int]] | None

    @property
    def searchable_text(self) -> str:
        return f"{self.path.stem} {self.description}".lower()


def _text(tag: bytes) -> str:
    kind = tag[:4]
    try:
        if kind == b"desc":
            (length,) = struct.unpack_from(">I", tag, 8)
            return tag[12:12 + length].split(b"\0")[0].decode("latin-1").strip()
        if kind == b"mluc":
            (records,) = struct.unpack_from(">I", tag, 8)
            if records:
                length, offset = struct.unpack_from(">II", tag, 20)
                return tag[offset:offset + length].decode("utf-16-be", "replace").rstrip("\0").strip()
        if kind == b"text":
            return tag[8:].split(b"\0")[0].decode("latin-1").strip()
    except struct.error:
        pass
    return ""


def _vcgt(tag: bytes) -> list[list[int]] | None:
    if tag[:4] != b"vcgt" or len(tag) < 18:
        return None
    (kind,) = struct.unpack_from(">I", tag, 8)
    if kind == 0:  # tables
        channels, entries, size = struct.unpack_from(">HHH", tag, 12)
        if size not in (1, 2) or channels not in (1, 3) or entries < 2:
            return None
        top = 255 if size == 1 else 65535
        values = struct.unpack_from(f">{channels * entries}{'B' if size == 1 else 'H'}", tag, 18)
        curves = [[v / top for v in values[c * entries:(c + 1) * entries]] for c in range(channels)]
        if channels == 1:
            curves *= 3
        return [gamma.resample(curve) for curve in curves]
    if kind == 1:  # formula: gamma, min, max per channel as s15Fixed16
        numbers = [v / 65536 for v in struct.unpack_from(">9i", tag, 12)]
        curves = []
        for c in range(3):
            power, low, high = numbers[c * 3:c * 3 + 3]
            curves.append([low + (high - low) * (i / 255) ** power for i in range(256)])
        return [gamma.resample(curve) for curve in curves]
    return None


def read(path: Path) -> IccProfile:
    """Parse an ICC file. Anything malformed raises ValueError, never a crash further up."""
    data = Path(path).read_bytes()
    if len(data) < 132 or data[36:40] != b"acsp":
        raise ValueError(f"{Path(path).name} is not an ICC profile")
    try:
        (count,) = struct.unpack_from(">I", data, 128)
        tags = {}
        for i in range(min(count, 256)):
            signature, offset, size = struct.unpack_from(">4sII", data, 132 + 12 * i)
            tags[signature] = data[offset:offset + size]
        description = _text(tags.get(b"desc", b"")) or Path(path).stem
        vcgt = _vcgt(tags.get(b"vcgt", b""))
    except (struct.error, IndexError, KeyError, ZeroDivisionError) as exc:
        raise ValueError(f"{Path(path).name} is damaged ({exc})") from exc
    return IccProfile(path=Path(path), description=description, device_class=data[12:16].decode("latin-1"), vcgt=vcgt)


def display_default_profile(adapter_luid: tuple[int, int] | None, source_id: int | None) -> str | None:
    """File name of the ICC profile Windows has set as default for this display, if any."""
    if w.ColorProfileGetDisplayDefault is None or adapter_luid is None or source_id is None:
        return None
    luid = w.LUID(adapter_luid[0], adapter_luid[1])
    for scope in (1, 0):  # current user, then system wide
        name = ctypes.c_void_p()
        hr = w.ColorProfileGetDisplayDefault(scope, luid, source_id, 0, 7, ctypes.byref(name))  # ICC, SDR mode
        if hr == 0 and name.value:
            try:
                return ctypes.wstring_at(name.value)
            finally:
                w.kernel32.LocalFree(name)
    return None
