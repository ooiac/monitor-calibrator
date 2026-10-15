"""EDID: the identity block every monitor sends over the cable.

It tells us the maker, model and serial (used to match calibrations) and the
panel's nominal primaries, white point and gamma (used for EDID-based fixes).
"""
from __future__ import annotations

import winreg
from dataclasses import dataclass

HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
INTERFACES = {1: "DVI", 2: "HDMI", 3: "HDMI", 4: "MDDI", 5: "DisplayPort"}
PLACEHOLDER_SERIALS = {0, 1, 0x01010101, 0xFFFFFFFF}


@dataclass
class Edid:
    raw: bytes
    manufacturer: str
    product_code: int
    serial_number: int
    serial_text: str
    name: str
    week: int
    year: int
    version: str
    interface: str | None
    size_cm: tuple[int, int]
    gamma: float | None
    red: tuple[float, float]
    green: tuple[float, float]
    blue: tuple[float, float]
    white: tuple[float, float]

    @property
    def pnp_id(self) -> str:
        """Plug-and-play id such as 'DEL4123', the one Windows shows in Device Manager."""
        return f"{self.manufacturer}{self.product_code:04X}"

    @property
    def serial(self) -> str:
        """The unit's serial number, or "" when the monitor only reports a placeholder.

        Many monitors send 0x01010101 or 0 for every unit, so two of the same model
        would look identical. Those don't count as serials.
        """
        text = self.serial_text.strip()
        if text and len(set(text)) > 1:
            return text
        if self.serial_number not in PLACEHOLDER_SERIALS:
            return str(self.serial_number)
        return ""


def parse(raw: bytes) -> Edid:
    if len(raw) < 128 or raw[:8] != HEADER:
        raise ValueError("not an EDID block")

    word = (raw[8] << 8) | raw[9]
    manufacturer = "".join(chr(((word >> shift) & 0x1F) + 64) for shift in (10, 5, 0))
    digital = bool(raw[20] & 0x80)

    def coord(high: int, low_bits: int, shift: int) -> float:
        return ((high << 2) | ((low_bits >> shift) & 0x03)) / 1024

    rg, bw = raw[25], raw[26]
    name = serial_text = ""
    for offset in range(54, 126, 18):
        block = raw[offset:offset + 18]
        if block[0] == 0 and block[1] == 0:
            text = block[5:18].split(b"\x0a")[0].decode("ascii", "replace").strip()
            if block[3] == 0xFC:
                name = text
            elif block[3] == 0xFF:
                serial_text = text

    return Edid(
        raw=bytes(raw[:128]),
        manufacturer=manufacturer,
        product_code=raw[10] | (raw[11] << 8),
        serial_number=int.from_bytes(raw[12:16], "little"),
        serial_text=serial_text,
        name=name,
        week=raw[16],
        year=raw[17] + 1990,
        version=f"{raw[18]}.{raw[19]}",
        interface=INTERFACES.get(raw[20] & 0x0F) if digital else "Analog",
        size_cm=(raw[21], raw[22]),
        gamma=None if raw[23] == 0xFF else (raw[23] + 100) / 100,
        red=(coord(raw[27], rg, 6), coord(raw[28], rg, 4)),
        green=(coord(raw[29], rg, 2), coord(raw[30], rg, 0)),
        blue=(coord(raw[31], bw, 6), coord(raw[32], bw, 4)),
        white=(coord(raw[33], bw, 2), coord(raw[34], bw, 0)),
    )


def read_from_registry(device_path: str) -> bytes | None:
    r"""Read the EDID Windows cached for a monitor device path.

    device_path looks like \\?\DISPLAY#DEL4123#5&1a2b3c4d&0&UID256#{guid}
    and maps to HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\DEL4123\5&...\Device Parameters.
    """
    parts = device_path.split("#")
    if len(parts) < 3:
        return None
    key_path = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{parts[1]}\{parts[2]}\Device Parameters"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "EDID")
            return bytes(value)
    except OSError:
        return None
