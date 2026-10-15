"""Turn a photo of the chart into measurements, and measurements into a correction.

What is measured and how much to trust it:

* Gamma per channel (R, G, B, gray): dither matching, so the camera's tone
  curve and white balance cancel out. Reliable if the screen's pixels are not
  resolved in the photo (stand back, or let it be slightly out of focus).
* Gray balance (do mid-grays have the same color as white?): follows from
  the per-channel gammas, so it is equally camera-independent.
* White point: the one thing a photo of the screen alone cannot know: a
  bluish screen and a bluish camera look identical. It needs an outside
  reference, like a gray card on a film set: either the camera's white balance
  locked to a known Kelvin value, or a trusted white (another calibrated
  screen, a tablet showing white) in the same photo.
"""
from __future__ import annotations

import hashlib
import io
import json
import math

from PIL import Image, ImageDraw, ImageOps, ImageStat

from .. import colormath, ddc, gamma
from ..profiles import Calibration
from . import chart

Image.MAX_IMAGE_PIXELS = 80_000_000
SAMPLE_FRACTION = 0.6  # middle part of each patch that is averaged (bigger = moiré averages out more)
RIPPLE_LIMIT = 6.0  # average in-patch variation (0-255) above which a photo shows moiré
SCALAR = {
    "gray": lambda m: (m[0] + m[1] + m[2]) / 3,
    "red": lambda m: m[0],
    "green": lambda m: m[1],
    "blue": lambda m: m[2],
}


class PhotoError(ValueError):
    pass


def load_photo(data: bytes) -> Image.Image:
    """Decode an upload, upright, as RGB. Camera EXIF (make/model/time) goes to image.info['camera']."""
    try:
        image = Image.open(io.BytesIO(data))
        camera = _camera_info(image)
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise PhotoError(
            f"could not read the photo ({exc}). If it's an iPhone HEIC, set Camera → Formats → Most Compatible."
        ) from exc
    image.info["camera"] = camera
    return image


def _camera_info(image) -> dict:
    try:
        exif = image.getexif()
        details = exif.get_ifd(0x8769)  # Exif sub-IFD: DateTimeOriginal lives here
    except Exception:  # broken EXIF never blocks a photo
        return {}
    info = {
        "make": exif.get(271),
        "model": exif.get(272),
        "taken": details.get(36867) or exif.get(306),
    }
    return {k: str(v).strip("\x00 ") for k, v in info.items() if v}


def jpeg(image: Image.Image, longest: int = 1400, quality: int = 85) -> bytes:
    copy = image.copy()
    copy.thumbnail((longest, longest))
    buffer = io.BytesIO()
    copy.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


# --- finding the screen in the photo ----------------------------------------


def detect_corners(image: Image.Image) -> list[list[float]]:
    """Corners (TL, TR, BR, BL) of the chart's white frame, normalized to 0..1."""
    found = detect_charts(image, 1)
    return found[0] if found else [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]


def detect_charts(image: Image.Image, count: int) -> list[list[list[float]]]:
    """Corners of up to `count` charts (one per screen in the photo), ordered left to right."""
    small = image.copy()
    small.thumbnail((800, 800) if count > 1 else (640, 640))
    width, height = small.size
    rings = sorted(_bright_shapes(small), key=lambda shape: -shape[0])[:count]
    charts = [
        [[round((ext[k][1] % width + 0.5) / width, 4), round((ext[k][1] // width + 0.5) / height, 4)]
         for k in ("tl", "tr", "br", "bl")]
        for _score, ext in rings
    ]
    return sorted(charts, key=lambda corners: corners[0][0])


def _bright_shapes(small: Image.Image) -> list[tuple[float, dict]]:
    """Every bright connected shape, scored so the hollow white frame of a chart ranks highest."""
    width, height = small.size
    lum = small.convert("L").tobytes()
    ranked = sorted(lum[:: max(1, len(lum) // 20000)])
    threshold = max(40, int(ranked[int(len(ranked) * 0.998)] * 0.72))
    seen = bytearray(width * height)
    shapes = []
    for start in range(width * height):
        if seen[start] or lum[start] < threshold:
            continue
        seen[start] = 1
        stack = [start]
        count = 0
        min_x, min_y, max_x, max_y = width, height, 0, 0
        extremes = {"tl": (math.inf, 0), "br": (-math.inf, 0), "tr": (-math.inf, 0), "bl": (math.inf, 0)}
        while stack:
            i = stack.pop()
            x, y = i % width, i // width
            count += 1
            min_x, max_x, min_y, max_y = min(min_x, x), max(max_x, x), min(min_y, y), max(max_y, y)
            s, d = x + y, x - y
            if s < extremes["tl"][0]:
                extremes["tl"] = (s, i)
            if s > extremes["br"][0]:
                extremes["br"] = (s, i)
            if d > extremes["tr"][0]:
                extremes["tr"] = (d, i)
            if d < extremes["bl"][0]:
                extremes["bl"] = (d, i)
            for j in (i - 1 if x else -1, i + 1 if x < width - 1 else -1, i - width, i + width):
                if 0 <= j < width * height and not seen[j] and lum[j] >= threshold:
                    seen[j] = 1
                    stack.append(j)
        box = (max_x - min_x + 1) * (max_y - min_y + 1)
        # The frame is a hollow ring: big bounding box, little fill. Lamps and windows are solid blobs.
        if count >= 30:
            shapes.append((box * (2 if count / box < 0.6 else 1), extremes))
    return shapes


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise PhotoError("the four corners don't form a usable shape")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                factor = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def _homography(src, dst):
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    return _solve(a, b)


def rectify(image: Image.Image, corners, screen_size) -> tuple[Image.Image, float]:
    """Undo the perspective. Returns the flattened chart and photo-pixels per screen-pixel."""
    w, h = image.size
    points = [(x * w, y * h) for x, y in corners]
    top = math.dist(points[0], points[1])
    bottom = math.dist(points[3], points[2])
    span = (top + bottom) / 2
    out_w = int(min(2000, max(300, span)))
    out_h = max(100, int(out_w * screen_size[1] / screen_size[0]))
    coeffs = _homography([(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)], points)
    flat = image.transform((out_w, out_h), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BILINEAR)
    return flat, span / screen_size[0]


def _inner_box(rect, size, fraction=0.5):
    w, h = size
    x0, y0, x1, y1 = rect
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * fraction / 2, (y1 - y0) * fraction / 2
    left, top = int((cx - hw) * w), int((cy - hh) * h)
    return (left, top, max(left + 1, int((cx + hw) * w)), max(top + 1, int((cy + hh) * h)))


def _sample(image, rect):
    stat = ImageStat.Stat(image.crop(_inner_box(rect, image.size, SAMPLE_FRACTION)))
    return stat.mean, stat.stddev


def photo_quality(samples, patches, scale) -> tuple[float, str | None]:
    """Detect a photo taken too close: solid patches ripple inside (moiré) instead of being smooth.

    Then the camera resolved the screen's pixels, the dither patches stop averaging to their true
    light level, and the measured gamma is wrong. Returns (ripple, advice or None).
    """
    spreads = [SCALAR[p.group](samples[p.key][1]) for p in patches if p.group in SCALAR and p.kind == "solid"]
    ripple = sum(spreads) / len(spreads)
    if ripple <= RIPPLE_LIMIT:
        return ripple, None
    return ripple, (
        f"Too close: the camera saw the screen's individual pixels and recorded moiré (ripple {ripple:.0f}; "
        f"{scale:.1f} photo pixels per screen pixel). That makes gamma come out wrong. Step back to about 2 m "
        "so the screen fills roughly a quarter of the photo width (don't zoom), or blur the photo on purpose."
    )


# --- measurements -------------------------------------------------------------


def _isotonic(values):
    """Pool-adjacent-violators: the closest non-decreasing sequence (camera noise can wiggle)."""
    blocks = [[v, 1] for v in values]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            total = blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]
            count = blocks[i][1] + blocks[i + 1][1]
            blocks[i : i + 2] = [[total / count, count]]
            i = max(0, i - 1)
        else:
            i += 1
    return [v for v, n in blocks for _ in range(n)]


def _inverse(ladder, target):
    """Which displayed level produces camera value `target`? (ladder: sorted (level, value))."""
    for (v0, y0), (v1, y1) in zip(ladder, ladder[1:]):
        if y0 <= target <= y1:
            return v0 if y1 == y0 else v0 + (target - y0) / (y1 - y0) * (v1 - v0)
    return None


def _fit_gamma(points):
    """Least-squares power law through the origin in log space: p = v ** gamma."""
    num = sum(math.log(p) * math.log(v) for p, v in points)
    den = sum(math.log(v) ** 2 for _, v in points)
    return num / den if den else None


def _tint_words(r: float, g: float, b: float) -> str:
    """Describe a relative color deviation (fractions, e.g. +0.04) in plain words."""
    words = []
    if b - (r + g) / 2 > 0.02:
        words.append("bluish")
    elif (r + g) / 2 - b > 0.02:
        words.append("warm")
    if g - (r + b) / 2 > 0.02:
        words.append("greenish")
    elif (r + b) / 2 - g > 0.02:
        words.append("magenta-ish")
    return " and ".join(words) or "neutral"


def analyse(image, corners, screen_size, wb_mode="auto", wb_kelvin=6500.0, reference=None) -> dict:
    warnings = []
    flat, scale = rectify(image, corners, screen_size)
    patches = chart.layout(*screen_size)
    samples = {p.key: _sample(flat, p.rect) for p in patches}

    white, _ = samples["ref:white"]
    black, _ = samples["ref:black"]
    if sum(white) / 3 - sum(black) / 3 < 40:
        return {"ok": False, "warnings": ["Couldn't find the chart there. Drag the dots onto the corners of the white frame."]}
    if max(white) >= 252:
        warnings.append("The white is blown out, so the photo is too bright. Tap the white frame or slide the exposure down.")
    if sum(white) / 3 < 70:
        warnings.append("The photo is quite dark; raise the exposure a little for cleaner numbers.")
    ripple, too_close = photo_quality(samples, patches, scale)
    if too_close:
        warnings.insert(0, too_close)

    gammas, points = {}, {}
    for group, scalar in SCALAR.items():
        ladder = sorted(
            (round(p.level * 255) / 255, scalar(samples[p.key][0]))
            for p in patches
            if p.group == group and p.kind == "solid"
        )
        smoothed = _isotonic([y for _, y in ladder])
        ladder = [(v, y) for (v, _), y in zip(ladder, smoothed)]
        if group == "gray":
            gray_ladder = ladder
        found = []
        for p in patches:
            if p.group != group or p.kind != "dither":
                continue
            level = _inverse(ladder, scalar(samples[p.key][0]))
            if level is None or not 0 < level < 1:
                warnings.append(f"{group} {p.level:.0%} patch fell outside the comparison steps. Exposure or framing may be off.")
                continue
            found.append((p.level, level))
        fitted = _fit_gamma(found) if found else None
        if fitted is not None and not 1.2 < fitted < 4.0:
            warnings.append(f"{group} gamma came out implausible ({fitted:.2f}), so it was left out.")
            fitted = None
        gammas[group] = round(fitted, 3) if fitted else None
        points[group] = [[round(p, 3), round(v, 4)] for p, v in found]

    # Gray balance: each channel is measured relative to its own full level (= white),
    # so different channel gammas mean mid-grays drift in color compared with white.
    tracking = "Gray balance couldn't be measured because some channels are missing."
    r, g, b = gammas.get("red"), gammas.get("green"), gammas.get("blue")
    if r and g and b:
        mid = [0.5 ** x for x in (r, g, b)]
        avg = sum(mid) / 3
        dev = [m / avg - 1 for m in mid]
        tracking = (
            f"Compared with white, mid-grays have red {dev[0]:+.0%}, green {dev[1]:+.0%} and blue {dev[2]:+.0%}, "
            f"so they look {_tint_words(*dev)}."
        )

    white_info = _white_point(image, white, wb_mode, wb_kelvin, reference, warnings)
    measured = gammas.get("gray")
    summary = (
        f"Gamma {measured:.2f} (target 2.2). " if measured else "Gamma not measured. "
    ) + white_info["text"]
    if too_close:
        summary = "Unreliable: the photo was taken too close (see the note above). " + summary
    return {
        "ok": any(gammas.values()),
        "reliable": too_close is None,
        "warnings": list(dict.fromkeys(warnings)),
        "quality": {
            "photo_pixels_per_screen_pixel": round(scale, 2),
            "ripple": round(ripple, 1),
            "white": [round(c) for c in white],
        },
        "gammas": gammas,
        "points": points,
        "tracking": tracking,
        "white": white_info,
        "summary": summary,
        # Raw camera readings, for comparing screens photographed together.
        "camera": {"white": [round(c, 2) for c in white], "ladder": [[v, round(y, 2)] for v, y in gray_ladder]},
    }


def _white_point(image, white_mean, mode, kelvin, reference, warnings) -> dict:
    seen = [colormath.srgb_to_linear(c / 255) for c in white_mean]
    if mode == "locked":
        # With the camera's white balance fixed at `kelvin`, a stimulus of that color comes out
        # neutral; undo that to estimate the screen's real white in linear sRGB.
        stimulus = [s * k for s, k in zip(seen, colormath.linear_rgb_of_kelvin(kelvin))]
        target = colormath.linear_rgb_of_kelvin(colormath.NEUTRAL_K)
        cct = colormath.xy_to_cct(*colormath.linear_rgb_to_xy(stimulus))
        text = f"White ≈ {cct:.0f} K (target 6500 K), judged with the camera locked at {kelvin:.0f} K."
    elif mode == "reference" and reference:
        w, h = image.size
        x0, y0, x1, y1 = reference
        box = (int(min(x0, x1) * w), int(min(y0, y1) * h), int(max(x0, x1) * w) + 1, int(max(y0, y1) * h) + 1)
        ref_mean = ImageStat.Stat(image.crop(box)).mean
        if max(ref_mean) >= 252:
            warnings.append("The reference white is blown out. Lower the exposure until it isn't pure white in the photo.")
        stimulus = seen
        target = [colormath.srgb_to_linear(c / 255) for c in ref_mean]
        cct = None
        dev = _relative(stimulus, target)
        text = f"White compared with your reference: red {dev[0]:+.0%}, green {dev[1]:+.0%}, blue {dev[2]:+.0%} ({_tint_words(*dev)})."
    else:
        dev = _relative(seen, (1, 1, 1))
        return {
            "mode": "auto",
            "gains": [1.0, 1.0, 1.0],
            "kelvin": None,
            "text": "The white point wasn't judged, because the camera's auto white balance hides it. "
            f"The camera saw white as {_tint_words(*dev)}.",
        }
    gains = [t / s if s > 0 else 1.0 for t, s in zip(target, stimulus)]
    top = max(gains)
    gains = [g / top for g in gains]
    if min(gains) < 0.5:
        warnings.append("The white correction is very large. Double-check the camera's white balance or the reference.")
        gains = [max(0.5, g) for g in gains]
    return {"mode": mode, "gains": [round(g, 4) for g in gains], "kelvin": round(cct) if cct else None, "text": text}


def _relative(values, reference):
    ratios = [v / r if r > 0 else 1.0 for v, r in zip(values, reference)]
    avg = sum(ratios) / 3
    return [x / avg - 1 for x in ratios]


# --- matching screens photographed together ------------------------------------------------------


def _light_curve(result) -> list[tuple[float, float]] | None:
    """(camera value, share of this screen's white light) points, from its own gray steps and gamma."""
    measured = result["gammas"].get("gray")
    if not measured:
        return None
    points = [(y, v ** measured) for v, y in result["camera"]["ladder"]]
    points.append((sum(result["camera"]["white"]) / 3, 1.0))
    points.sort()
    return points


def _light_at(curve, value) -> float | None:
    for (y0, l0), (y1, l1) in zip(curve, curve[1:]):
        if y0 <= value <= y1:
            return l0 if y1 == y0 else l0 + (value - y0) / (y1 - y0) * (l1 - l0)
    return None


def compare(reference: dict, other: dict) -> dict:
    """How `other` differs from `reference` when both are in the same photo.

    The camera handled both screens the same way, so its white balance and contrast cancel out:
    the color difference is measured directly, and brightness is read off one screen's own gray
    steps (whose light levels the gamma measurement already established).
    """
    seen_ref = [colormath.srgb_to_linear(c / 255) for c in reference["camera"]["white"]]
    seen_other = [colormath.srgb_to_linear(c / 255) for c in other["camera"]["white"]]
    deviation = _relative(seen_other, seen_ref)
    gains = [r / o if o > 0 else 1.0 for r, o in zip(seen_ref, seen_other)]
    gains = [round(g / max(gains), 4) for g in gains]

    notes = []
    ratio = None
    if max(reference["camera"]["white"]) >= 252 or max(other["camera"]["white"]) >= 252:
        notes.append("A white is blown out, so brightness can't be compared. Lower the exposure and retake.")
    else:
        ref_curve, other_curve = _light_curve(reference), _light_curve(other)
        other_white = sum(other["camera"]["white"]) / 3
        ref_white = sum(reference["camera"]["white"]) / 3
        if ref_curve and other_curve:
            if other_white <= ref_white:
                share = _light_at(ref_curve, other_white)
                ratio = share if share else None
            else:
                share = _light_at(other_curve, ref_white)
                ratio = 1 / share if share else None
        if ratio is None:
            notes.append("The two screens are too far apart in brightness to compare from one photo.")

    if ratio is None:
        brightness = "Brightness couldn't be compared."
    elif abs(ratio - 1) < 0.05:
        brightness = "About the same brightness."
    else:
        brightness = f"{abs(ratio - 1):.0%} {'brighter' if ratio > 1 else 'dimmer'}."
    color = (f"White has red {deviation[0]:+.0%}, green {deviation[1]:+.0%} and blue {deviation[2]:+.0%}, "
             f"so it looks {_tint_words(*deviation)} next to the other screen.")
    return {
        "white_gains": gains,
        "white_deviation": [round(d, 4) for d in deviation],
        "brightness_ratio": round(ratio, 4) if ratio else None,
        "text": f"{color} {brightness}",
        "notes": notes,
    }


def match_corrections(results: dict, comparisons: dict, states: dict, reference: str, *, target_gamma=2.2,
                      fix_gamma=True, match_white=True, match_brightness=True, names=None) -> dict:
    """Calibrations (by screen key) that bring every screen in line with the reference one.

    White: each other screen takes on the reference's white. Brightness: the brighter of each pair
    is turned down (also darkening its blacks). Gamma: every screen goes to the target.
    """
    names = names or {}
    dim = {key: 1.0 for key in results}
    for key, comparison in comparisons.items():
        ratio = comparison.get("brightness_ratio")
        if not match_brightness or not ratio or abs(ratio - 1) < 0.05:
            continue
        if ratio > 1:
            dim[key] = min(dim[key], 1 / ratio)
        else:
            dim[reference] = min(dim[reference], ratio)

    out = {}
    for key, result in results.items():
        state = states[key]
        comparison = comparisons.get(key)
        white = list(comparison["white_gains"]) if (comparison and match_white) else [1.0, 1.0, 1.0]
        ddc_values, panel = {}, None
        if dim[key] < 1:
            if ddc.BRIGHTNESS in state.ddc_values:
                ddc_values[ddc.BRIGHTNESS] = max(5, round(state.ddc_current(ddc.BRIGHTNESS) * dim[key]))
            elif state.brightness is not None:
                panel = max(5, round(state.brightness * dim[key]))
            else:  # no brightness control: dim through the graphics card instead
                white = [g * dim[key] for g in white]
        ramp = None
        if state.ramp is not None and result.get("gammas", {}).get("gray") and (
                fix_gamma or any(abs(g - 1) > 1e-3 for g in white)):
            ramp = correction_ramp(result, state.ramp, target_gamma, fix_gamma, white)
        if ramp is None and not ddc_values and panel is None:
            continue
        if key == reference:
            title = "Matched with the phone (reference screen)"
        else:
            title = f"Matched to {names.get(reference, 'the reference screen')} with the phone"
        fingerprint = hashlib.sha1(json.dumps([key, white, ddc_values, panel, target_gamma, fix_gamma]).encode()).hexdigest()[:10]
        out[key] = Calibration(
            id=f"camera-match:{fingerprint}", title=title, source="camera photo", confidence="medium",
            detail=(comparison or {}).get("text", ""), auto=False, ramp=ramp, ddc=ddc_values, brightness=panel,
            settings={"camera": {"gammas": result.get("gammas"), "white_gains": white}},
        )
    return out


def overlay(image, corners, screen_size) -> bytes:
    """The flattened chart with the sampled areas outlined, so you can check the alignment."""
    flat, _ = rectify(image, corners, screen_size)
    draw = ImageDraw.Draw(flat)
    for patch in chart.layout(*screen_size):
        draw.rectangle(_inner_box(patch.rect, flat.size), outline=(255, 0, 255), width=2)
    return jpeg(flat, 1200)


# --- correction --------------------------------------------------------------


def correction_ramp(result, ramp_at_photo, target_gamma=2.2, fix_gamma=True, white_gains=(1.0, 1.0, 1.0)):
    """New GPU ramp, composed with the ramp that was active when the photo was taken.

    The photo measured  screen(ramp(x)) ≈ x ** measured_c  (relative to each channel's white).
    We want            screen(new(x))  =  gain_c * x ** target,
    so new(x) = ramp(u) with u = (gain_c * x ** target) ** (1 / measured_c).
    """
    gammas = result["gammas"]
    fallback = gammas.get("gray") or gamma.DISPLAY_GAMMA
    out = []
    for c, name in enumerate(("red", "green", "blue")):
        measured = gammas.get(name) or fallback
        wanted = target_gamma if fix_gamma else measured
        out.append([
            round(gamma.MAX * gamma.sample(ramp_at_photo[c], (white_gains[c] * (i / 255) ** wanted) ** (1 / measured)))
            for i in range(gamma.SIZE)
        ])
    return out


def correction(result, state, target_gamma=2.2, fix_gamma=True, fix_white=True, via="gpu") -> Calibration:
    """Build the Calibration to apply. `state` is the DisplayState captured with the photo."""
    if state.ramp is None:
        raise ValueError("the GPU ramp couldn't be read when the photo was taken, so a correction can't be composed")
    white = list(result["white"]["gains"]) if fix_white else [1.0, 1.0, 1.0]
    ddc_values = {}
    if via == "monitor" and fix_white and any(abs(g - 1) > 1e-3 for g in white):
        preset = state.ddc_current(ddc.COLOR_PRESET)
        if preset not in ddc.USER_PRESETS or not all(c in state.ddc_values for c in ddc.GAINS):
            raise ValueError(
                "Monitor-side white balance needs the monitor in a User color mode with RGB gains. "
                "Pick a User color mode on the Adjust page, take a new photo, then try again. Or use the graphics card."
            )
        for code, gain in zip(ddc.GAINS, white):
            current, maximum = state.ddc_values[code]
            ddc_values[code] = max(0, min(maximum or 100, round(current * gain)))
        white = [1.0, 1.0, 1.0]
    ramp = None
    if fix_gamma or any(abs(g - 1) > 1e-3 for g in white):
        ramp = correction_ramp(result, state.ramp, target_gamma, fix_gamma, white)
    parts = []
    if fix_gamma:
        parts.append(f"gamma → {target_gamma}")
    if fix_white and result["white"]["mode"] != "auto":
        parts.append("white balance" + (" via monitor gains" if ddc_values else ""))
    fingerprint = hashlib.sha1(json.dumps([result["gammas"], white, ddc_values, target_gamma]).encode()).hexdigest()[:10]
    return Calibration(
        id=f"camera:{fingerprint}",
        title="Camera-measured correction (" + (", ".join(parts) or "no change") + ")",
        source="camera photo",
        confidence="medium",
        detail=result["summary"] + " " + result["tracking"],
        auto=False,
        ramp=ramp,
        ddc=ddc_values,
        settings={"camera": {"gammas": result["gammas"], "white": result["white"]}},
    )
