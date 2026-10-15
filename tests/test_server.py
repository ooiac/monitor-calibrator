"""The phone-facing server: token required, one device at a time, inputs validated,
only verified photos can change anything."""
import http.cookiejar
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

from calibrator import backup, engine, gamma
from calibrator.camera import server
sys.path.insert(0, str(Path(__file__).parent))  # shared fakes, however the tests are started
from test_camera_synthetic import CODE, SCREEN, camera, simulate_screen


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        channels, size = simulate_screen()
        cls.photo, cls.corners = camera(channels, size)
        cls.events = []
        display = SimpleNamespace(name='Test <script>alert(1)</script>', size=SCREEN, hdr_enabled=False)
        cls.fake_state = engine.DisplayState(display=display, ramp=gamma.identity())
        cls.probe = mock.patch.object(engine, "probe", return_value=cls.fake_state)
        cls.probe.start()
        cls.home = mock.patch.object(backup, "DATA_DIR", Path(tempfile.mkdtemp()))  # uploads land in a temp dir
        cls.home.start()
        cls.session = server.CameraSession(display, cls.events.append)
        cls.session.chart_codes = {CODE: time.time()}
        cls.base = cls.session.start(host="127.0.0.1", port=18765)
        cls.phone = cls.device()
        cls.request_with(cls.phone, cls.base)  # opening the page claims the session for this "phone"

    @classmethod
    def tearDownClass(cls):
        cls.session.stop()
        cls.probe.stop()
        cls.home.stop()

    @staticmethod
    def device():
        """A browser with its own cookie jar, like a separate phone."""
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @staticmethod
    def request_with(opener, url, body=None, content_type="application/json"):
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with opener.open(req, timeout=60) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as err:
            return err.code, err.read(), err.headers

    def request(self, path, body=None, content_type="application/json", base=None):
        return self.request_with(self.phone, (base or self.base) + path, body, content_type)

    def test_wrong_token_is_rejected(self):
        wrong = self.base.replace(self.session.token, "not-the-token")
        self.assertEqual(self.request("", base=wrong)[0], 404)
        self.assertEqual(self.request("upload", self.photo, "image/jpeg", base=wrong)[0], 404)

    def test_page_escapes_display_name_and_sets_csp(self):
        status, body, headers = self.request("")
        self.assertEqual(status, 200)
        self.assertNotIn(b"<script>alert(1)", body)
        self.assertIn(b"&lt;script&gt;", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_bad_inputs(self):
        self.assertEqual(self.request("upload", b"not an image", "image/jpeg")[0], 400)
        self.assertEqual(self.request("analyse", b"{nope")[0], 400)
        self.assertEqual(self.request("analyse", {"id": "../../etc", "corners": self.corners})[0], 400)
        self.assertEqual(self.request("analyse", {"id": "abc", "corners": "x"})[0], 400)
        with mock.patch.object(server, "MAX_UPLOAD", 1000):
            self.assertEqual(self.request("upload", self.photo, "image/jpeg")[0], 413)

    def test_verified_photo_flow(self):
        status, body, _ = self.request("upload", self.photo, "image/jpeg")
        self.assertEqual(status, 200, body)
        upload = json.loads(body)
        self.assertTrue(upload["verification"]["ok"], upload)
        status, body, _ = self.request(
            "analyse", {"id": upload["id"], "corners": upload["corners"], "wb": {"mode": "auto"}}
        )
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"] and result["verification"]["ok"], result)
        status, body, _ = self.request("apply", {"id": upload["id"], "options": {"target_gamma": 2.2}})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.events[-1][0], "apply")
        self.assertIsNotNone(self.events[-1][1].ramp)

    def test_second_device_is_turned_away(self):
        other = self.device()
        status, body, _ = self.request_with(other, self.base)
        self.assertEqual(status, 409)
        self.assertIn(b"Another device is connected", body)
        self.assertEqual(self.request_with(other, self.base + "upload", self.photo, "image/jpeg")[0], 409)
        self.assertEqual(self.request_with(other, self.base + "status")[0], 409)
        self.assertEqual(self.request("status")[0], 200)  # the connected phone is unaffected
        self.assertIsNotNone(self.session.device_info())

    def test_disconnect_lets_a_new_device_in_and_kills_the_old_link(self):
        old_base = new_base = self.base
        try:
            new_base = self.session.release_device(new_link=True)
            self.assertNotEqual(new_base, old_base)
            self.assertEqual(self.request_with(self.phone, old_base)[0], 404)  # old QR/link is dead
            newcomer = self.device()
            self.assertEqual(self.request_with(newcomer, new_base)[0], 200)
            self.assertEqual(self.request_with(self.phone, new_base + "status")[0], 409)  # old phone is out
        finally:
            type(self).base = self.session.release_device(new_link=False) or new_base
            self.request_with(self.phone, self.base)  # hand the session back to the main "phone"

    def test_idle_device_loses_the_session(self):
        try:
            with mock.patch.object(server, "IDLE_RELEASE", -1):
                self.assertIsNone(self.session.device_info())
                newcomer = self.device()
                self.assertEqual(self.request_with(newcomer, self.base)[0], 200)
        finally:
            self.session.release_device(new_link=False)
            self.request_with(self.phone, self.base)

    def test_unverified_photo_cannot_apply(self):
        self.session.chart_codes = {0x11111: time.time()}  # the photo shows a code we "never displayed"
        try:
            status, body, _ = self.request("upload", self.photo, "image/jpeg")
            upload = json.loads(body)
            self.assertFalse(upload["verification"]["ok"])
            status, body, _ = self.request("analyse", {"id": upload["id"], "corners": upload["corners"], "wb": {}})
            self.assertFalse(json.loads(body)["ok"])
            status, body, _ = self.request("apply", {"id": upload["id"], "options": {}})
            self.assertEqual(status, 400)
            self.assertIn("chart check", json.loads(body)["error"])
        finally:
            self.session.chart_codes = {CODE: time.time()}


if __name__ == "__main__":
    unittest.main()
