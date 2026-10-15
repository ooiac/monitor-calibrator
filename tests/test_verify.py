"""The photo check must accept real photos of the shown chart and refuse everything else."""
import io
import sys
import time
import unittest
from pathlib import Path

from PIL import Image

from calibrator.camera import analysis, chart, verify
sys.path.insert(0, str(Path(__file__).parent))  # shared fakes, however the tests are started
from test_camera_synthetic import CODE, SCREEN, camera, simulate_screen


def _jpeg(image, exif=None):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92, **({"exif": exif} if exif else {}))
    return buffer.getvalue()


class VerifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        channels, size = simulate_screen()
        cls.photo_bytes, cls.true_corners = camera(channels, size)
        cls.issued = {CODE: time.time() - 20}

    def check(self, data, issued=None, corners=None):
        image = analysis.load_photo(data)
        return verify.verify(image, corners or analysis.detect_corners(image), SCREEN, issued or self.issued)

    def test_code_roundtrip(self):
        for code in (1, 0x5A3C1, 0xFFFFF, 0x80000):
            self.assertEqual(chart.decode_code(chart.encode_code(code)), code)
        broken = chart.encode_code(0x5A3C1)
        broken[5] ^= 1
        self.assertIsNone(chart.decode_code(broken))  # a misread bit is caught by the checksum

    def test_real_photo_of_shown_chart_passes(self):
        result = self.check(self.photo_bytes)
        self.assertTrue(result.ok, result.to_json())
        self.assertEqual(result.code, CODE)

    def test_chart_this_session_never_showed_is_refused(self):
        result = self.check(self.photo_bytes, issued={0x12345: time.time()})
        self.assertFalse(result.ok)
        self.assertIn("never displayed", result.reason)

    def test_random_picture_is_refused(self):
        noise = Image.effect_noise((1200, 900), 60).convert("RGB")
        result = self.check(_jpeg(noise))
        self.assertFalse(result.ok)

    def test_screenshot_is_refused(self):
        buffer = io.BytesIO()
        chart.render(*SCREEN, code=CODE).save(buffer, "PNG")
        result = self.check(buffer.getvalue(), corners=[[0, 0], [1, 0], [1, 1], [0, 1]])
        self.assertFalse(result.ok)
        self.assertIn("screenshot", result.reason)

    def test_upside_down_photo_is_fixed(self):
        rotated = Image.open(io.BytesIO(self.photo_bytes)).rotate(180)
        result = self.check(_jpeg(rotated))
        self.assertTrue(result.ok, result.to_json())
        self.assertIn("rotated", result.checks[0].detail)

    def test_close_up_moire_is_flagged_not_rejected(self):
        # Like the first real-phone photo: clearly the chart, but taken too close (moiré ripples).
        channels, size = simulate_screen()
        data, _ = camera(channels, size, moire=0.3)
        result = self.check(data)
        self.assertTrue(result.ok, result.to_json())  # it IS the chart, so don't call it something else
        distance = next(c for c in result.checks if c.name == "Distance & sharpness")
        self.assertFalse(distance.passed)
        self.assertIn("2 m", distance.detail)
        self.assertTrue(next(c for c in self.check(self.photo_bytes).checks if c.name == "Distance & sharpness").passed)

    def test_exif_is_reported(self):
        image = Image.open(io.BytesIO(self.photo_bytes))
        exif = Image.Exif()
        exif[271], exif[272] = "TestMaker", "Phone 9"
        exif.get_ifd(0x8769)[36867] = time.strftime("%Y:%m:%d %H:%M:%S")
        result = self.check(_jpeg(image, exif.tobytes()))
        info = next(c for c in result.checks if c.name == "Photo info")
        self.assertIn("TestMaker Phone 9", info.detail)
        self.assertTrue(info.passed)


if __name__ == "__main__":
    unittest.main()
