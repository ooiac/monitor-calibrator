"""Sample pictures for judging a change before and after. Drawn in code, so nothing is copied in.

* "shadows": a night scene whose hills, buildings and trees sit just above black (1 to 8%),
  plus a strip of near-black steps. Good for Enhanced blacks, Shadows and black level.
* "colors": the 24 standard color-checker patches (published sRGB values), a gray scale and
  color ramps. Good for white balance, gamma and matching screens.
"""
from __future__ import annotations

import colorsys
import math
import random

SCENES = (("shadows", "Night scene"), ("colors", "Colors"))
NEAR_BLACK = (0, 1, 2, 3, 4, 5, 6, 8, 10, 13, 16)  # percent of full signal
COLOR_CHECKER = (
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
    (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
)


def _hex(r: float, g: float, b: float) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v))) for v in (r, g, b))


def _level(percent: float, tint=(1.0, 1.0, 1.0)) -> str:
    value = 255 * percent / 100
    return _hex(value * tint[0], value * tint[1], value * tint[2])


def draw(canvas, scene: str, width: int, height: int) -> None:
    canvas.delete("all")
    canvas.configure(bg="#000000")
    if scene == "colors":
        _colors(canvas, width, height)
    else:
        _night(canvas, width, height)


def _night(c, w, h):
    navy = (0.75, 0.85, 1.25)
    bands = 80
    for i in range(bands):  # sky: dark blue at the top fading to black at the horizon
        t = i / (bands - 1)
        c.create_rectangle(0, i * h * 0.62 / bands, w, (i + 1) * h * 0.62 / bands + 1,
                           fill=_level(6.5 - 5 * t, navy), outline="")
    c.create_rectangle(0, h * 0.62, w, h, fill="#000000", outline="")
    stars = random.Random(7)
    for _ in range(70):
        x, y, bright = stars.random() * w, stars.random() * h * 0.5, stars.uniform(18, 60)
        c.create_rectangle(x, y, x + 2, y + 2, fill=_level(bright), outline="")
    mx, my, r = w * 0.78, h * 0.18, h * 0.045
    for k, glow in ((3.2, 7), (2.4, 9), (1.7, 12)):  # soft halo
        c.create_oval(mx - r * k, my - r * k, mx + r * k, my + r * k, fill=_level(glow, navy), outline="")
    c.create_oval(mx - r, my - r, mx + r, my + r, fill="#e6e3d8", outline="")
    c.create_oval(mx - r * 0.45, my - r * 0.3, mx - r * 0.05, my + r * 0.1, fill="#cfccc0", outline="")

    def ridge(base, amplitude, waves, level, seed):
        rnd = random.Random(seed)
        phases = [rnd.uniform(0, 6.28) for _ in range(3)]
        points = [0, h]
        for i in range(61):
            x = w * i / 60
            y = base + sum(amplitude / (n + 1) * math.sin(x / w * waves * (n + 1) + phases[n])
                           for n in range(3))
            points += [x, y]
        points += [w, h]
        c.create_polygon(points, fill=_level(level), outline="")

    ridge(h * 0.56, h * 0.05, 3.5, 8, 1)  # far hills
    ridge(h * 0.62, h * 0.04, 5.0, 5, 2)
    city = random.Random(3)
    x = w * 0.08
    while x < w * 0.62:  # buildings, with a few windows lit and some barely lit
        bw, bh = city.uniform(w * 0.025, w * 0.06), city.uniform(h * 0.07, h * 0.2)
        top = h * 0.69 - bh
        c.create_rectangle(x, top, x + bw, h * 0.7, fill=_level(3), outline="")
        for wy in range(int(top + 8), int(h * 0.68), 14):
            for wx in range(int(x + 5), int(x + bw - 8), 11):
                pick = city.random()
                if pick < 0.08:
                    c.create_rectangle(wx, wy, wx + 5, wy + 7, fill=_level(30, (1.0, 0.85, 0.6)), outline="")
                elif pick < 0.35:
                    c.create_rectangle(wx, wy, wx + 5, wy + 7, fill=_level(city.choice((4, 5, 6))), outline="")
        x += bw + city.uniform(2, 10)
    ridge(h * 0.74, h * 0.03, 4.0, 2, 4)  # foreground
    trees = random.Random(5)
    for _ in range(14):
        tx, th = trees.uniform(0, w), trees.uniform(h * 0.06, h * 0.14)
        base = h * 0.78
        c.create_polygon(tx - th * 0.25, base, tx, base - th, tx + th * 0.25, base, fill=_level(1), outline="")
    # near-black steps, top left (control panels sit at the bottom), on a pure black backing
    count = len(NEAR_BLACK)
    side = min(w * 0.4 / count, h * 0.05)
    x0, y0 = w * 0.05, h * 0.09
    c.create_rectangle(x0 - 14, y0 - 34, x0 + side * count + 14, y0 + side + 34, fill="#000000", outline="")
    for i, percent in enumerate(NEAR_BLACK):
        c.create_rectangle(x0 + i * side, y0, x0 + (i + 1) * side, y0 + side, fill=_level(percent), outline="")
        c.create_text(x0 + (i + 0.5) * side, y0 + side + 14, text=f"{percent}", fill="#3c3c3c")
    c.create_text(x0, y0 - 10, anchor="sw", text="% steps just above black", fill="#3c3c3c")


def _colors(c, w, h):
    c.create_rectangle(0, 0, w, h, fill="#383838", outline="")  # neutral surround
    margin_x, top = w * 0.08, h * 0.08
    usable = w - 2 * margin_x
    cell_w, cell_h = usable / 6, h * 0.11
    for index, rgb in enumerate(COLOR_CHECKER):
        col, row = index % 6, index // 6
        x, y = margin_x + col * cell_w, top + row * cell_h
        c.create_rectangle(x + 4, y + 4, x + cell_w - 4, y + cell_h - 4, fill=_hex(*rgb), outline="")
    y = top + 4 * cell_h + h * 0.04
    steps = 11
    for i in range(steps):  # gray scale 0..100%
        x = margin_x + i * usable / steps
        c.create_rectangle(x, y, x + usable / steps + 1, y + h * 0.07, fill=_level(i * 10), outline="")
    y += h * 0.07 + h * 0.03
    bands = 128
    rows = ((1, 0, 0), (0, 1, 0), (0, 0, 1), None)
    for row, mix in enumerate(rows):
        for i in range(bands):
            t = i / (bands - 1)
            if mix is None:  # hues at full strength
                rgb = [255 * v for v in colorsys.hsv_to_rgb(t * 0.83, 1, 1)]
            else:
                rgb = [255 * t * m for m in mix]
            x = margin_x + i * usable / bands
            c.create_rectangle(x, y, x + usable / bands + 1, y + h * 0.035, fill=_hex(*rgb), outline="")
        y += h * 0.045
