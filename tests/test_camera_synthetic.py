"""End-to-end check of the camera analysis with a simulated monitor and phone camera.

The fake monitor has known per-channel gammas and a bluish white; the fake camera
adds its own white balance, an S-shaped tone curve, perspective, blur, noise and
JPEG compression. The analysis must still recover the monitor's true numbers.

Run:  python -m unittest discover -s tests
"""
import io
import math
import random
import unittest

from PIL import Image, ImageFilter

from calibrator import colormath, gamma
from calibrator.camera import analysis, chart

SCREEN = (960, 540)
TRUE_GAMMA = (2.2, 2.35, 2.6)
TRUE_WHITE = (1.0, 0.97, 1.08)  # linear sRGB of the screen's white: bluish
CODE = 0x5A3C1  # chart code the fake PC "displayed"


def simulate_screen(ramp=None, block=3, code=CODE, gammas=TRUE_GAMMA, white=TRUE_WHITE):
    """What a camera sensor collects from the screen: linear light, averaged over blocks."""
    image = chart.render(*SCREEN, code=code)
    w, h = SCREEN
    channels = []
    for c, band in enumerate(image.split()):
        lut = []
        for value in range(256):
            signal = gamma.sample(ramp[c], value / 255) if ramp else value / 255
            lut.append(white[c] * signal ** gammas[c])
        data = band.tobytes()
        out_w, out_h = w // block, h // block
        out = []
        for oy in range(out_h):
            rows = [data[(oy * block + dy) * w:(oy * block + dy + 1) * w] for dy in range(block)]
            for ox in range(out_w):
                start = ox * block
                total = sum(lut[row[x]] for row in rows for x in range(start, start + block))
                out.append(total / (block * block))
        channels.append(_lens_blur(out, out_w, out_h))
    return channels, (w // block, h // block)


def _lens_blur(values, w, h):
    """Optical blur happens before the sensor, i.e. on linear light: a [1 2 1]/4 kernel each way."""
    def at(x, y):
        return values[min(h - 1, max(0, y)) * w + min(w - 1, max(0, x))]

    horizontal = [(at(x - 1, y) + 2 * at(x, y) + at(x + 1, y)) / 4 for y in range(h) for x in range(w)]
    values = horizontal
    return [(at(x, y - 1) + 2 * at(x, y) + at(x, y + 1)) / 4 for y in range(h) for x in range(w)]


def _moire(photo, strength):
    """Wavy brightness ripples, like a camera too close to a screen (a different phase per channel)."""
    w, h = photo.size
    out = []
    for c, band in enumerate(photo.split()):
        period = 7 + 2 * c
        base = bytes(round(127.5 + 127.5 * math.sin(2 * math.pi * i / period)) for i in range(period)) * (w // period + 3)
        mask = Image.frombytes("L", (w, h), b"".join(base[(y * (c + 1)) % period:][:w] for y in range(h)))
        dark = band.point(lambda v: v * (1 - strength))
        bright = band.point(lambda v: min(255, v * (1 + strength)))
        out.append(Image.composite(bright, dark, mask))
    return Image.merge("RGB", out)


def _encode(channels, size, wb, exposure, rnd):
    """The phone's processing: white balance, exposure, sRGB encoding plus extra contrast, noise."""
    def tone(x):
        x = colormath.linear_to_srgb(min(1.0, x))
        return 0.7 * x + 0.3 * (x * x * (3 - 2 * x))  # extra contrast like phone processing

    bands = []
    for c, values in enumerate(channels):
        data = bytes(max(0, min(255, round(255 * tone(v * wb[c] * exposure) + rnd.gauss(0, 1.2)))) for v in values)
        bands.append(Image.frombytes("L", size, data))
    return Image.merge("RGB", bands)


def camera(channels, size, wb=(1.0, 1.0, 1.0), exposure=0.8, seed=1, moire=0.0):
    """Phone pipeline: white balance, exposure, sRGB encode + S-curve, perspective, blur, noise, JPEG."""
    screen = _encode(channels, size, wb, exposure, random.Random(seed))

    photo_size = (1400, 1000)
    corners = [(260, 210), (1180, 260), (1150, 790), (230, 760)]  # where the screen lands in the photo
    coeffs = analysis._homography(corners, [(0, 0), (size[0], 0), size, (0, size[1])])
    photo = screen.transform(photo_size, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BILINEAR)
    photo = photo.filter(ImageFilter.GaussianBlur(0.7))
    if moire:
        photo = _moire(photo, moire)
    buffer = io.BytesIO()
    photo.save(buffer, "JPEG", quality=90)
    return buffer.getvalue(), [[x / photo_size[0], y / photo_size[1]] for x, y in corners]


def camera_pair(screens, wb=(1.0, 1.0, 1.0), exposure=0.6, seed=2):
    """Two screens side by side in one photo, through the same camera."""
    rnd = random.Random(seed)
    photo_size = (1800, 900)
    spots = [[(90, 220), (860, 250), (850, 690), (80, 660)], [(950, 240), (1720, 220), (1730, 680), (960, 700)]]
    photo = Image.new("RGB", photo_size)
    for (channels, size), corners in zip(screens, spots):
        coeffs = analysis._homography(corners, [(0, 0), (size[0], 0), size, (0, size[1])])
        layer = _encode(channels, size, wb, exposure, rnd).transform(
            photo_size, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BILINEAR)
        mask = Image.new("L", size, 255).transform(photo_size, Image.Transform.PERSPECTIVE, coeffs)
        photo.paste(layer, (0, 0), mask)
    photo = photo.filter(ImageFilter.GaussianBlur(0.7))
    buffer = io.BytesIO()
    photo.save(buffer, "JPEG", quality=90)
    return buffer.getvalue(), [[[x / photo_size[0], y / photo_size[1]] for x, y in corners] for corners in spots]


class CameraAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.channels, cls.size = simulate_screen()

    def run_analysis(self, wb, **kwargs):
        data, true_corners = camera(self.channels, self.size, wb=wb)
        image = analysis.load_photo(data)
        corners = analysis.detect_corners(image)
        for found, expected in zip(corners, true_corners):
            self.assertLess(math.dist(found, expected), 0.02, f"corner {found} vs {expected}")
        return analysis.analyse(image, corners, SCREEN, **kwargs)

    def test_gamma_is_camera_independent(self):
        for wb in ((1.0, 1.0, 1.0), (1.25, 1.0, 0.7), (0.8, 1.05, 1.3)):
            result = self.run_analysis(wb)
            self.assertTrue(result["ok"], result)
            for name, expected in zip(("red", "green", "blue"), TRUE_GAMMA):
                self.assertAlmostEqual(result["gammas"][name], expected, delta=0.08, msg=f"{name} with camera wb {wb}")
            self.assertIn("warm", result["tracking"])  # blue gamma is highest -> blue drops in mid-grays

    def test_white_point_with_locked_camera(self):
        result = self.run_analysis((1.0, 1.0, 1.0), wb_mode="locked", wb_kelvin=6500)
        white = result["white"]
        self.assertGreater(white["kelvin"], 6650)  # the fake screen is bluish
        # The phone's tone curve compresses colors near white, so the size of the error is
        # underestimated (that's why you re-photo and iterate), but the direction must be right.
        expected = [1 / t for t in TRUE_WHITE]
        expected = [e / max(expected) for e in expected]
        self.assertEqual(white["gains"].index(min(white["gains"])), 2)  # blue gets reduced the most
        for got, want in zip(white["gains"], expected):
            self.assertAlmostEqual(got, want, delta=0.06)

    def test_correction_converges(self):
        result = self.run_analysis((1.1, 1.0, 0.9), wb_mode="locked", wb_kelvin=6500)
        # Camera WB isn't really 6500 K here, so only fix gamma and check every channel lands on 2.2.
        ramp = analysis.correction_ramp(result, gamma.identity(), 2.2, True, (1.0, 1.0, 1.0))
        corrected, size = simulate_screen(ramp)
        data, _ = camera(corrected, size, wb=(1.1, 1.0, 0.9))
        image = analysis.load_photo(data)
        again = analysis.analyse(image, analysis.detect_corners(image), SCREEN)
        for name in ("red", "green", "blue", "gray"):
            self.assertAlmostEqual(again["gammas"][name], 2.2, delta=0.08, msg=name)


if __name__ == "__main__":
    unittest.main()
