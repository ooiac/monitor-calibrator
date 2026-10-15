"""Small color-science helpers: color temperature, white-point correction."""
from __future__ import annotations

D65 = (0.3127, 0.3290)
NEUTRAL_K = 6500

XYZ_TO_SRGB = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)


SRGB_TO_XYZ = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)


def srgb_to_linear(value: float) -> float:
    """0..1 sRGB-encoded -> 0..1 linear light."""
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(value: float) -> float:
    value = max(0.0, value)
    return value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def _mat_vec(m, v):
    return tuple(sum(m[r][c] * v[c] for c in range(3)) for r in range(3))


def _inverse(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError("singular matrix")
    return (
        ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det),
        ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det),
        ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det),
    )


def xy_to_xyz(x: float, y: float) -> tuple[float, float, float]:
    return x / y, 1.0, (1 - x - y) / y


def kelvin_to_xy(kelvin: float) -> tuple[float, float]:
    """CIE daylight locus above 4000 K, Planckian (Kim et al.) below."""
    k = min(max(float(kelvin), 1667.0), 25000.0)
    if k >= 4000:
        if k <= 7000:
            x = -4.6070e9 / k**3 + 2.9678e6 / k**2 + 0.09911e3 / k + 0.244063
        else:
            x = -2.0064e9 / k**3 + 1.9018e6 / k**2 + 0.24748e3 / k + 0.237040
        return x, -3.0 * x * x + 2.870 * x - 0.275
    x = -0.2661239e9 / k**3 - 0.2343589e6 / k**2 + 0.8776956e3 / k + 0.179910
    if k <= 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    else:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    return x, y


def _linear_rgb(xy) -> tuple[float, float, float]:
    return _mat_vec(XYZ_TO_SRGB, xy_to_xyz(*xy))


_NEUTRAL_RGB = _linear_rgb(kelvin_to_xy(NEUTRAL_K))


def kelvin_gains(kelvin: float) -> tuple[float, float, float]:
    """Linear-light R,G,B multipliers that shift a 6500 K white to `kelvin` (max channel = 1)."""
    if abs(kelvin - NEUTRAL_K) < 1:
        return 1.0, 1.0, 1.0
    rgb = [max(0.0, c / n) for c, n in zip(_linear_rgb(kelvin_to_xy(kelvin)), _NEUTRAL_RGB)]
    top = max(rgb)
    return tuple(c / top for c in rgb)


def white_gains(kelvin: float = NEUTRAL_K, tint: float = 0.0) -> tuple[float, float, float]:
    """Temperature + tint, like a camera's white balance. tint > 0 = magenta, < 0 = green (±100 scale)."""
    r, g, b = kelvin_gains(kelvin)
    t = max(-100.0, min(100.0, tint)) / 100
    if t > 0:
        g *= 1 - t
    else:
        r *= 1 + t
        b *= 1 + t
    top = max(r, g, b)
    return r / top, g / top, b / top


def linear_rgb_of_kelvin(kelvin: float) -> tuple[float, float, float]:
    """Linear sRGB of a white at `kelvin` (Y = 1, not normalized)."""
    return _linear_rgb(kelvin_to_xy(kelvin))


def linear_rgb_to_xy(rgb) -> tuple[float, float]:
    x, y, z = _mat_vec(SRGB_TO_XYZ, rgb)
    total = x + y + z
    if total <= 0:
        raise ValueError("black has no chromaticity")
    return x / total, y / total


def estimate_kelvin(gains, tolerance: float = 0.004) -> int | None:
    """Which color temperature tint best explains these channel gains (None if it isn't one)."""
    top = max(gains) or 1.0
    target = [g / top for g in gains]
    best, best_error = None, tolerance
    for kelvin in range(1700, 12001, 50):
        error = sum((a - b) ** 2 for a, b in zip(kelvin_gains(kelvin), target)) / 3
        if error < best_error:
            best, best_error = kelvin, error
    return best


def xy_to_cct(x: float, y: float) -> float:
    """McCamy's approximation of correlated color temperature."""
    n = (x - 0.3320) / (0.1858 - y)
    return 449 * n**3 + 3525 * n**2 + 6823.3 * n + 5520.33


def rgb_to_xyz_matrix(red, green, blue, white):
    primaries = tuple(xy_to_xyz(*p) for p in (red, green, blue))
    p = tuple(tuple(primaries[col][row] for col in range(3)) for row in range(3))
    scale = _mat_vec(_inverse(p), xy_to_xyz(*white))
    return tuple(tuple(p[row][col] * scale[col] for col in range(3)) for row in range(3))


def whitepoint_gains(red, green, blue, white, target=D65) -> tuple[float, float, float]:
    """Linear R,G,B multipliers that move a panel's native white to `target`."""
    matrix = rgb_to_xyz_matrix(red, green, blue, white)
    rgb = [max(0.0, c) for c in _mat_vec(_inverse(matrix), xy_to_xyz(*target))]
    top = max(rgb)
    return tuple(c / top for c in rgb)
