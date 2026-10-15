"""Is an upload really a photo of the chart this session put on the screen?

Required checks (any failure rejects the photo):
  * chart code: every time the chart is shown it carries a fresh random code
    (the row of blocks under the top edge). The photo must show a code this
    session issued, so random pictures and old charts are refused.
  * chart structure: white brighter than black, each color patch led by its
    own channel, gray/color steps getting brighter overall (noise and moiré
    are tolerated here; they're reported by the distance check instead).
  * real photo: a screenshot or the chart file itself shows the picture before
    the GPU curve and the monitor touch it, so it would measure nothing.
Soft check (informational): the photo's EXIF camera model and capture time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from PIL import ImageStat

from . import analysis, chart


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    required: bool = True


@dataclass
class Verification:
    ok: bool
    code: int | None
    corners: list[list[float]]
    checks: list[Check] = field(default_factory=list)

    @property
    def reason(self) -> str:
        failed = [c for c in self.checks if c.required and not c.passed]
        return failed[0].detail if failed else ""

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "code": chart.format_code(self.code) if self.code is not None else None,
            "corners": self.corners,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail, "required": c.required} for c in self.checks],
        }


def _lum(mean) -> float:
    return 0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]


def _read(image, corners, screen_size):
    flat, scale = analysis.rectify(image, corners, screen_size)
    patches = chart.layout(*screen_size)
    samples = {p.key: analysis._sample(flat, p.rect) for p in patches}
    threshold = (_lum(samples["ref:white"][0]) + _lum(samples["ref:black"][0])) / 2
    cells = [
        1 if _lum(ImageStat.Stat(flat.crop(analysis._inner_box(rect, flat.size, 0.6))).mean) > threshold else 0
        for rect in chart.code_cells(*screen_size)
    ]
    return chart.decode_code(cells), samples, patches, scale


def _rank_correlation(values) -> float:
    """Spearman correlation between position (0, 1, 2…) and value: 1 = steadily rising."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    for rank, i in enumerate(order):
        ranks[i] = rank
    return 1 - 6 * sum((r - i) ** 2 for i, r in enumerate(ranks)) / (n * (n * n - 1))


def _structure(samples, patches, code_found: bool) -> Check:
    """Is this the chart at all? Checks the overall pattern only; measurement noise is the analysis' job."""
    problems = []

    def mean(key):
        return samples[key][0]

    white, black = mean("ref:white"), mean("ref:black")
    if _lum(white) < _lum(black) + 40:
        problems.append("white isn't clearly brighter than black")
    dominant = {"red": (0,), "green": (1,), "blue": (2,), "cyan": (1, 2), "magenta": (0, 2), "yellow": (0, 1)}
    for name, channels in dominant.items():
        color = mean(f"ref:{name}")
        others = [color[c] for c in range(3) if c not in channels]
        if min(color[c] for c in channels) < max(others) + 15:
            problems.append(f"the {name} patch isn't {name}")
    for group in chart.CHANNELS:
        scalar = analysis.SCALAR[group]
        steps = [scalar(mean(p.key)) for p in sorted(
            (p for p in patches if p.group == group and p.kind == "solid"), key=lambda p: p.level)]
        third = len(steps) // 3
        rising = sum(steps[-third:]) / third > sum(steps[:third]) / third + 4
        if not rising or _rank_correlation(steps) < 0.5:
            problems.append(f"the {group} steps don't get brighter")
    if not problems:
        return Check("Looks like the chart", True, "Patches, colors and gray steps are where and what they should be.")
    if code_found:
        return Check(
            "Looks like the chart", False,
            "The chart was recognized, but the patches are too inconsistent to measure (" + "; ".join(problems) + "). "
            "Usually glare or a reflection on the screen, or moiré from standing too close.",
        )
    return Check("Looks like the chart", False, "This doesn't look like the calibration chart: " + "; ".join(problems) + ".")


def _distance(samples, patches, scale) -> Check:
    """Soft check shown right after upload, so a too-close photo can be retaken before analysing."""
    ripple, advice = analysis.photo_quality(samples, patches, scale)
    if advice:
        return Check("Distance & sharpness", False, advice, required=False)
    return Check("Distance & sharpness", True, "Good: the screen's pixels blend together as they should.", required=False)


def _real_photo(image, corners, screen_size, samples, patches) -> Check:
    solids = [analysis.SCALAR["gray"](samples[p.key][1]) for p in patches if p.group == "gray" and p.kind == "solid"]
    dithers = [analysis.SCALAR["gray"](samples[p.key][1]) for p in patches if p.group == "gray" and p.kind == "dither"]
    noise = sum(solids) / len(solids)
    perfect_pattern = noise < 0.25 and min(dithers) > 40  # flawless dither, zero noise: never a camera
    exact_copy = image.size == tuple(screen_size) and all(
        abs(x - ex) < 0.01 and abs(y - ey) < 0.01 for (x, y), (ex, ey) in zip(corners, ((0, 0), (1, 0), (1, 1), (0, 1)))
    )
    if perfect_pattern or exact_copy:
        return Check(
            "Real camera photo", False,
            "This looks like a screenshot or the chart image itself, not a photo of the screen. "
            "A screenshot shows the picture before the GPU curve and the monitor change it, so it can't measure them.",
        )
    return Check("Real camera photo", True, "Sensor noise and optical blur look like a real camera.")


def _exif(image, shown_at: float | None) -> Check:
    info = image.info.get("camera", {})
    model = " ".join(x for x in (info.get("make"), info.get("model")) if x).strip()
    taken = info.get("taken")
    if not model and not taken:
        return Check("Photo info", True, "No camera info in the file. Some apps remove it, and it isn't required.", required=False)
    detail = f"Taken with {model}" if model else "Camera unknown"
    passed = True
    if taken:
        try:
            when = datetime.strptime(taken, "%Y:%m:%d %H:%M:%S").timestamp()
        except ValueError:
            when = None
        if when is not None and shown_at is not None:
            delta = when - shown_at
            if delta < -300:
                passed = False
                detail += f", but the photo time is {abs(delta) / 60:.0f} min before this chart was shown. Check the phone's clock."
            else:
                detail += f", {max(0, delta):.0f} s after this chart was shown."
        elif when is not None:
            detail += f", at {taken}."
    return Check("Photo info", passed, detail + ("" if detail.endswith(".") else "."), required=False)


def verify(image, corners, screen_size, issued: dict[int, float]) -> Verification:
    """issued: chart code -> time it was shown, for this session."""
    code, samples, patches, scale, used, turns = None, None, None, 1.0, corners, 0
    for k in range(4):  # as given, then as if the photo were turned 90/180/270°
        attempt = corners[k:] + corners[:k]
        try:
            found, found_samples, found_patches, found_scale = _read(image, attempt, screen_size)
        except analysis.PhotoError:
            continue  # corners don't form a shape at all
        if samples is None:
            samples, patches, scale = found_samples, found_patches, found_scale
        if found is not None:
            code, samples, patches, scale, used, turns = found, found_samples, found_patches, found_scale, attempt, k
            break
    if samples is None:
        return Verification(False, None, corners, [Check(
            "Chart code", False,
            "Couldn't find the chart in this photo. Drag the dots onto the white frame's corners, or retake it.",
        )])

    checks = []
    if code is None:
        checks.append(Check(
            "Chart code", False,
            "Couldn't read the chart code (the row of blocks under the top edge). Drag the corners onto the white "
            "frame's corners, or retake the photo a bit closer / steadier.",
        ))
    elif code not in issued:
        checks.append(Check(
            "Chart code", False,
            f"The photo shows chart code {chart.format_code(code)}, which this session never displayed. "
            "It's an old or different chart. Show the chart again and retake.",
        ))
    else:
        latest = max(issued, key=issued.get)
        note = "" if code == latest else " (from an earlier showing of the chart)"
        turned = " The photo was rotated; corners fixed automatically." if turns else ""
        checks.append(Check("Chart code", True, f"Code {chart.format_code(code)} matches the chart shown on the PC{note}.{turned}"))

    checks.append(_structure(samples, patches, code is not None))
    checks.append(_real_photo(image, used, screen_size, samples, patches))
    checks.append(_distance(samples, patches, scale))
    checks.append(_exif(image, issued.get(code) if code is not None else None))
    ok = all(c.passed for c in checks if c.required)
    return Verification(ok=ok, code=code, corners=[list(map(float, p)) for p in used], checks=checks)
