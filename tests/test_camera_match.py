"""Two screens in one photo: the comparison must see which is bluer and how much brighter."""
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from calibrator import ddc, engine, gamma
from calibrator.camera import analysis

sys.path.insert(0, str(Path(__file__).parent))  # shared fakes, however the tests are started
from test_camera_synthetic import CODE, SCREEN, camera_pair, simulate_screen

NEUTRAL = (1.0, 1.0, 1.0)
BLUISH_BRIGHTER = (1.3, 1.3 * 0.97, 1.3 * 1.08)  # 30% more light and a bluer white


class MatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reference = simulate_screen(code=CODE, gammas=(2.2, 2.2, 2.2), white=NEUTRAL)
        other = simulate_screen(code=CODE + 1, gammas=(2.3, 2.4, 2.5), white=BLUISH_BRIGHTER)
        data, cls.spots = camera_pair([reference, other], wb=(1.15, 1.0, 0.8))  # the camera's own cast
        cls.image = analysis.load_photo(data)

    def test_finds_both_charts(self):
        charts = analysis.detect_charts(self.image, 2)
        self.assertEqual(len(charts), 2)
        for found, expected in zip(charts, self.spots):
            for a, b in zip(found, expected):
                self.assertLess(math.dist(a, b), 0.02)

    def test_comparison_and_corrections(self):
        left, right = analysis.detect_charts(self.image, 2)
        reference = analysis.analyse(self.image, left, SCREEN)
        other = analysis.analyse(self.image, right, SCREEN)
        result = analysis.compare(reference, other)
        self.assertAlmostEqual(result["brightness_ratio"], 1.28, delta=0.1)
        red, _green, blue = result["white_deviation"]
        self.assertGreater(blue, red)  # the other screen is bluer, whatever the camera's own cast
        self.assertEqual(result["white_gains"].index(min(result["white_gains"])), 2)

        def state(brightness):
            return engine.DisplayState(display=SimpleNamespace(name="x"), ramp=gamma.identity(),
                                       ddc_values={ddc.BRIGHTNESS: (brightness, 100)})

        fixes = analysis.match_corrections({"ref": reference, "other": other}, {"other": result},
                                           {"ref": state(80), "other": state(90)}, "ref")
        self.assertAlmostEqual(fixes["other"].ddc[ddc.BRIGHTNESS], 90 / result["brightness_ratio"], delta=1)
        self.assertNotIn(ddc.BRIGHTNESS, fixes["ref"].ddc)  # the dimmer screen keeps its brightness
        self.assertIsNotNone(fixes["other"].ramp)


if __name__ == "__main__":
    unittest.main()


class MatchServerTest(unittest.TestCase):
    """The phone flow for matching, through the real web server."""

    @classmethod
    def setUpClass(cls):
        import http.cookiejar
        import tempfile
        import urllib.request
        from unittest import mock

        from calibrator import backup
        from calibrator.camera import server

        cls.events = []
        cls.left = SimpleNamespace(key="L", name="Left screen", index=1, size=SCREEN, hdr_enabled=False)
        cls.right = SimpleNamespace(key="R", name="Right screen", index=2, size=SCREEN, hdr_enabled=False)
        states = {d.key: engine.DisplayState(display=d, ramp=gamma.identity(), ddc_values={ddc.BRIGHTNESS: (80, 100)})
                  for d in (cls.left, cls.right)}
        cls.patches = [mock.patch.object(engine, "probe", side_effect=lambda d: states[d.key]),
                       mock.patch.object(backup, "DATA_DIR", Path(tempfile.mkdtemp()))]
        for patch in cls.patches:
            patch.start()
        cls.session = server.CameraSession(cls.left, cls.events.append)
        cls.session.set_mode("match", [cls.left, cls.right])
        codes = (cls.session.new_chart_code("L"), cls.session.new_chart_code("R"))
        both = [simulate_screen(code=codes[0], gammas=(2.2, 2.2, 2.2), white=NEUTRAL),
                simulate_screen(code=codes[1], gammas=(2.3, 2.4, 2.5), white=BLUISH_BRIGHTER)]
        cls.photo, _ = camera_pair(both, wb=(1.15, 1.0, 0.8))
        cls.base = cls.session.start(host="127.0.0.1", port=18865)
        cls.phone = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        cls.phone.open(cls.base, timeout=30).read()  # opening the page claims the session

    @classmethod
    def tearDownClass(cls):
        cls.session.stop()
        for patch in cls.patches:
            patch.stop()

    def post(self, path, body, content_type="application/json"):
        import json
        import urllib.error
        import urllib.request

        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method="POST",
                                         headers={"Content-Type": content_type})
        try:
            with self.phone.open(request, timeout=120) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_match_flow(self):
        status, upload = self.post("upload", self.photo, "image/jpeg")
        self.assertEqual(status, 200, upload)
        self.assertEqual([c["key"] for c in upload["charts"]], ["L", "R"])
        self.assertTrue(all(c["verification"]["ok"] for c in upload["charts"]), upload)
        charts = [{"key": c["key"], "corners": c["corners"]} for c in upload["charts"]]
        status, result = self.post("analyse", {"id": upload["id"], "charts": charts, "reference": "L"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"], result)
        right = next(s for s in result["screens"] if s["key"] == "R")
        self.assertGreater(right["comparison"]["brightness_ratio"], 1.15)
        status, applied = self.post("apply", {"id": upload["id"], "options": {"target_gamma": 2.2}})
        self.assertEqual(status, 200, applied)
        kind, pairs = self.events[-1]
        self.assertEqual(kind, "apply_many")
        fixes = {display.key: cal for display, cal in pairs}
        self.assertLess(fixes["R"].ddc[ddc.BRIGHTNESS], 80)  # the brighter screen is turned down

    def test_photo_missing_a_screen_is_refused(self):
        from PIL import Image
        import io

        full = Image.open(io.BytesIO(self.photo))
        half = full.crop((0, 0, full.width // 2, full.height))  # only the left screen
        buffer = io.BytesIO()
        half.save(buffer, "JPEG", quality=90)
        status, upload = self.post("upload", buffer.getvalue(), "image/jpeg")
        self.assertEqual(status, 200, upload)
        charts = [{"key": c["key"], "corners": c["corners"]} for c in upload["charts"] if c["key"]]
        status, result = self.post("analyse", {"id": upload["id"], "charts": charts, "reference": "L"})
        self.assertFalse(result["ok"])
        self.assertTrue(any("Not in the photo" in w for w in result["warnings"]), result["warnings"])
        status, applied = self.post("apply", {"id": upload["id"], "options": {}})
        self.assertEqual(status, 400)
