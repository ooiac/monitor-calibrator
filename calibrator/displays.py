"""Find the connected displays and work out which one we are on."""
from __future__ import annotations

import ctypes
import hashlib
import re
from ctypes import wintypes
from dataclasses import dataclass

from . import edid as edid_mod
from . import winapi as w

OUTPUT_TECH = {
    0: "VGA",
    4: "DVI",
    5: "HDMI",
    6: "LVDS (built-in)",
    10: "DisplayPort",
    11: "eDP (built-in)",
    13: "UDI (built-in)",
    15: "Miracast",
    16: "Wired dock / adapter",
    17: "Virtual display",
    18: "USB-C DisplayPort",
    0x80000000: "Built-in",
}
INTERNAL_TECH = {6, 11, 13, 0x80000000}


@dataclass
class Display:
    gdi_name: str  # \\.\DISPLAY1, the name GDI (gamma ramps) uses
    hmonitor: int  # handle for this session; DDC/CI hangs off it
    rect: tuple[int, int, int, int]
    primary: bool
    friendly_name: str = ""
    device_path: str = ""
    output_technology: int | None = None
    adapter_luid: tuple[int, int] | None = None
    source_id: int | None = None
    hdr_enabled: bool | None = None
    edid: edid_mod.Edid | None = None
    index: int = 0

    @property
    def internal(self) -> bool:
        return self.output_technology in INTERNAL_TECH

    @property
    def connection(self) -> str:
        if self.output_technology is None:
            return "unknown connection"
        return OUTPUT_TECH.get(self.output_technology, f"connection type {self.output_technology}")

    @property
    def pnp_id(self) -> str:
        if self.edid:
            return self.edid.pnp_id
        parts = self.device_path.split("#")
        return parts[1] if len(parts) > 2 else ""

    @property
    def name(self) -> str:
        if self.friendly_name:
            return self.friendly_name
        if self.edid and self.edid.name:
            return self.edid.name
        if self.internal:
            return "Built-in display"
        return self.pnp_id or self.gdi_name

    @property
    def size(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return right - left, bottom - top

    @property
    def key(self) -> str:
        """Stable id for this monitor's backups.

        With a real serial number the folder follows the monitor to any port. Without one
        (common), it's tied to the connection instead, so two identical monitors never share
        backups.
        """
        serial = self.edid.serial if self.edid else ""
        if not serial:
            parts = self.device_path.split("#")
            source = "#".join(parts[1:3]) if len(parts) > 2 else self.gdi_name
            serial = hashlib.sha1(source.encode()).hexdigest()[:8]
        return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{self.pnp_id or 'display'}_{serial}")

    @property
    def legacy_key(self) -> str:
        """How the first version named the folder (placeholder serials counted as serials)."""
        serial = ""
        if self.edid:
            serial = self.edid.serial_text or (str(self.edid.serial_number) if self.edid.serial_number else "")
        if not serial:
            serial = hashlib.sha1((self.device_path or self.gdi_name).encode()).hexdigest()[:8]
        return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{self.pnp_id or 'display'}_{serial}")

    def describe(self) -> str:
        width, height = self.size
        kind = "built-in" if self.internal else "external"
        bits = [kind] if self.connection.lower() == kind else [kind, self.connection]
        bits += [f"{width}×{height}", self.gdi_name.replace("\\\\.\\", "")]
        if self.pnp_id:
            bits.append(self.pnp_id)
        if self.primary:
            bits.append("primary")
        return " · ".join(bits)


def _enum_hmonitors() -> dict[str, tuple[int, tuple[int, int, int, int], bool]]:
    found = {}

    def callback(hmonitor, _hdc, _rect, _lparam):
        info = w.MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if w.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            found[info.szDevice] = (
                hmonitor,
                (r.left, r.top, r.right, r.bottom),
                bool(info.dwFlags & w.MONITORINFOF_PRIMARY),
            )
        return True

    w.user32.EnumDisplayMonitors(None, None, w.MonitorEnumProc(callback), 0)
    return found


def _active_paths() -> list:
    num_paths, num_modes = ctypes.c_uint32(), ctypes.c_uint32()
    for _ in range(3):  # the layout can change between the two calls
        if w.user32.GetDisplayConfigBufferSizes(
            w.QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), ctypes.byref(num_modes)
        ):
            return []
        paths = (w.DISPLAYCONFIG_PATH_INFO * num_paths.value)()
        modes = (w.DISPLAYCONFIG_MODE_INFO * num_modes.value)()
        rc = w.user32.QueryDisplayConfig(
            w.QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), paths, ctypes.byref(num_modes), modes, None
        )
        if rc == 0:
            return list(paths[: num_paths.value])
        if rc != w.ERROR_INSUFFICIENT_BUFFER:
            return []
    return []


def _device_info(struct_type, info_type: int, adapter: w.LUID, item_id: int):
    request = struct_type()
    request.header.type = info_type
    request.header.size = ctypes.sizeof(request)
    request.header.adapterId = adapter
    request.header.id = item_id
    if w.user32.DisplayConfigGetDeviceInfo(ctypes.byref(request)) == 0:
        return request
    return None


def list_displays() -> list[Display]:
    monitors = _enum_hmonitors()
    found: dict[str, Display] = {}

    for path in _active_paths():
        src, tgt = path.sourceInfo, path.targetInfo
        source = _device_info(
            w.DISPLAYCONFIG_SOURCE_DEVICE_NAME, w.DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME, src.adapterId, src.id
        )
        if source is None or source.viewGdiDeviceName not in monitors:
            continue
        gdi_name = source.viewGdiDeviceName
        if gdi_name in found:  # clone/duplicate mode: describe the first screen only
            continue
        hmonitor, rect, primary = monitors[gdi_name]
        display = Display(
            gdi_name=gdi_name,
            hmonitor=hmonitor,
            rect=rect,
            primary=primary,
            output_technology=tgt.outputTechnology,
            adapter_luid=(tgt.adapterId.LowPart, tgt.adapterId.HighPart),
            source_id=src.id,
        )
        target = _device_info(
            w.DISPLAYCONFIG_TARGET_DEVICE_NAME, w.DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME, tgt.adapterId, tgt.id
        )
        if target is not None:
            display.friendly_name = target.monitorFriendlyDeviceName
            display.device_path = target.monitorDevicePath
        color = _device_info(
            w.DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO,
            w.DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO,
            tgt.adapterId,
            tgt.id,
        )
        if color is not None:
            display.hdr_enabled = bool(color.value & 0x2)
        raw = edid_mod.read_from_registry(display.device_path) if display.device_path else None
        if raw:
            try:
                display.edid = edid_mod.parse(raw)
            except ValueError:
                pass
        found[gdi_name] = display

    # Anything Windows lists that the display-config API didn't describe.
    for gdi_name, (hmonitor, rect, primary) in monitors.items():
        found.setdefault(gdi_name, Display(gdi_name=gdi_name, hmonitor=hmonitor, rect=rect, primary=primary))

    ordered = sorted(found.values(), key=lambda d: (d.rect[0], d.rect[1]))
    for number, display in enumerate(ordered, 1):
        display.index = number
    return ordered


def by_hmonitor(displays: list[Display], hmonitor: int) -> Display | None:
    return next((d for d in displays if d.hmonitor == hmonitor), None)


def detect_current(displays: list[Display], hwnd: int | None = None) -> Display | None:
    """The display holding `hwnd`, else the foreground window, else the mouse, else the primary."""
    if not displays:
        return None
    hwnd = hwnd or w.user32.GetForegroundWindow()
    if hwnd:
        match = by_hmonitor(displays, w.user32.MonitorFromWindow(hwnd, w.MONITOR_DEFAULTTONEAREST))
        if match:
            return match
    point = wintypes.POINT()
    if w.user32.GetCursorPos(ctypes.byref(point)):
        match = by_hmonitor(displays, w.user32.MonitorFromPoint(point, w.MONITOR_DEFAULTTONEAREST))
        if match:
            return match
    return next((d for d in displays if d.primary), displays[0])
