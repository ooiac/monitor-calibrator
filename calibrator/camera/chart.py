"""The test chart shown full-screen for the camera check.

Why it can see through the camera's own color and tone handling:

* Dither patches turn exactly 25 / 50 / 75 % of their pixels fully on. Seen
  from far enough (or slightly out of focus) they emit exactly that fraction of
  the channel's full light, whatever the monitor's gamma is.
* Next to them is a ladder of solid levels. In the photo we find which solid
  level looks the same as each dither patch. Equal look = equal light, because
  both went through the *same* camera, so the camera's tone curve and white
  balance cancel out, and we get the monitor's real response curve per channel.
* A white frame around the edge makes the screen easy to find in the photo.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

COLS, ROWS = 9, 9
FRAME = 0.035  # white frame thickness, as a fraction of screen height
GUTTER = 0.10  # gap around each patch, as a fraction of the cell
BACKGROUND = (24, 24, 24)
DITHERS = (0.25, 0.5, 0.75)
LADDER = tuple(round(0.40 + i * 0.54 / 14, 4) for i in range(15))  # 0.40 … 0.94 encoded
CHANNELS = {"gray": (1, 1, 1), "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1)}
REFERENCES = (
    ("white", (255, 255, 255)),
    ("gray50", (128, 128, 128)),
    ("black", (0, 0, 0)),
    ("red", (255, 0, 0)),
    ("green", (0, 255, 0)),
    ("blue", (0, 0, 255)),
    ("cyan", (0, 255, 255)),
    ("magenta", (255, 0, 255)),
    ("yellow", (255, 255, 0)),
)
# Two rows per channel; each dither sits beside the ladder steps it should match
# at gamma ≈ 2.2, so screen non-uniformity hits both sides of a comparison alike.
GROUP_LAYOUT = (
    ("L0", "L1", "L2", "L3", "D0.25", "L4", "L5", "L6", "L7"),
    ("L8", "D0.5", "L9", "L10", "L11", "L12", "D0.75", "L13", "L14"),
)


CODE_BITS = 20  # random per showing of the chart; proves a photo shows *this* chart
CODE_CELLS = CODE_BITS + 4 + 2  # + 4-bit checksum + a start and a stop cell that are always on


def encode_code(code: int) -> list[int]:
    """Cells (1 = lit) for a chart code: start, 20 code bits, 4 checksum bits, stop."""
    bits = [(code >> (CODE_BITS - 1 - i)) & 1 for i in range(CODE_BITS)]
    nibbles = sum((code >> shift) & 0xF for shift in range(0, CODE_BITS, 4))
    check = (nibbles % 16) ^ 0b1010
    return [1, *bits, *[(check >> (3 - i)) & 1 for i in range(4)], 1]


def decode_code(cells: list[int]) -> int | None:
    """Inverse of encode_code; None if the cells don't form a valid code."""
    if len(cells) != CODE_CELLS or cells[0] != 1 or cells[-1] != 1:
        return None
    code = 0
    for bit in cells[1:1 + CODE_BITS]:
        code = (code << 1) | bit
    return code if encode_code(code) == cells else None


def format_code(code: int) -> str:
    return f"{code:05X}"


def code_cells(width: int, height: int) -> list[tuple[float, float, float, float]]:
    """Normalized rects of the code cells, centered in the strip between the top frame and the grid."""
    frame = FRAME * height
    size, pitch = 0.6 * frame, 1.0 * frame
    left = (width - (CODE_CELLS - 1) * pitch - size) / 2
    top = 1.5 * frame - size / 2
    return [
        ((left + i * pitch) / width, top / height, (left + i * pitch + size) / width, (top + size) / height)
        for i in range(CODE_CELLS)
    ]


@dataclass(frozen=True)
class Patch:
    key: str  # "red:D0.5", "gray:L3", "ref:white"
    group: str  # gray / red / green / blue / ref
    kind: str  # dither / solid
    level: float  # dither fraction, or encoded level 0..1
    color: tuple[int, int, int]  # solid color, or the "on" color of a dither
    rect: tuple[float, float, float, float]  # normalized x0, y0, x1, y1 on the screen


def _level_value(level: float) -> int:
    return round(255 * level)


def layout(width: int, height: int) -> list[Patch]:
    frame = FRAME * height
    x0, y0 = 2 * frame / width, 2 * frame / height
    cell_w, cell_h = (1 - 2 * x0) / COLS, (1 - 2 * y0) / ROWS

    def rect(col, row):
        gx, gy = cell_w * GUTTER, cell_h * GUTTER
        left, top = x0 + col * cell_w, y0 + row * cell_h
        return (left + gx, top + gy, left + cell_w - gx, top + cell_h - gy)

    patches = []
    for index, (group, mix) in enumerate(CHANNELS.items()):
        for sub_row, names in enumerate(GROUP_LAYOUT):
            row = index * 2 + sub_row
            for col, name in enumerate(names):
                if name.startswith("D"):
                    level = float(name[1:])
                    color = tuple(255 * m for m in mix)
                    patches.append(Patch(f"{group}:{name}", group, "dither", level, color, rect(col, row)))
                else:
                    level = LADDER[int(name[1:])]
                    color = tuple(_level_value(level) * m for m in mix)
                    patches.append(Patch(f"{group}:{name}", group, "solid", level, color, rect(col, row)))
    for col, (name, color) in enumerate(REFERENCES):
        patches.append(Patch(f"ref:{name}", "ref", "solid", 1.0, color, rect(col, ROWS - 1)))
    return patches


def _dither_mask(width: int, height: int, fraction: float) -> Image.Image:
    """Full-screen on/off mask aligned to absolute pixels (so the pattern is exact)."""
    on, off = b"\xff", b"\x00"
    alternate = (on + off) * (width // 2 + 1)
    shifted = (off + on) * (width // 2 + 1)
    if fraction == 0.5:  # checkerboard
        rows = (alternate[:width], shifted[:width])
    elif fraction == 0.25:  # one pixel of each 2×2
        rows = (alternate[:width], off * width)
    else:  # 0.75: three of each 2×2
        rows = (on * width, alternate[:width])
    data = b"".join(rows[y % 2] for y in range(height))
    return Image.frombytes("L", (width, height), data)


def render(width: int, height: int, caption: str = "", code: int | None = None) -> Image.Image:
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    frame = max(4, round(FRAME * height))
    draw.rectangle((0, 0, width - 1, height - 1), outline=(255, 255, 255), width=frame)

    if code is not None:
        for lit, rect in zip(encode_code(code), code_cells(width, height)):
            if lit:
                box = (round(rect[0] * width), round(rect[1] * height), round(rect[2] * width) - 1, round(rect[3] * height) - 1)
                draw.rectangle(box, fill=(255, 255, 255))
        caption = f"{caption} · chart code {format_code(code)}" if caption else f"chart code {format_code(code)}"

    masks = {f: _dither_mask(width, height, f) for f in DITHERS}
    for patch in layout(width, height):
        box = (
            round(patch.rect[0] * width),
            round(patch.rect[1] * height),
            round(patch.rect[2] * width),
            round(patch.rect[3] * height),
        )
        if patch.kind == "dither":
            draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(0, 0, 0))
            image.paste(patch.color, box, masks[patch.level].crop(box))
        else:
            draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=patch.color)

    if caption:
        try:
            font = ImageFont.load_default(size=max(12, frame // 2))
        except TypeError:  # Pillow < 10.1
            font = ImageFont.load_default()
        draw.text((frame + 6, height - frame - frame // 4), caption, fill=(150, 150, 150), font=font, anchor="ls")
    return image
