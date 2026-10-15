"""Broken files on the user's PC must never stop the app from scanning a screen."""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from calibrator import engine, gamma, icc, profiles
from calibrator.displays import Display


def _icc_with_bad_vcgt() -> bytes:
    """A valid-looking header whose vcgt tag claims far more data than it has."""
    vcgt = b"vcgt" + b"\0" * 4 + struct.pack(">IHHH", 0, 3, 65535, 2) + b"\0" * 10
    header = bytearray(128)
    header[36:40] = b"acsp"
    table = struct.pack(">I", 1) + struct.pack(">4sII", b"vcgt", 144, len(vcgt))
    return bytes(header) + table + vcgt


class RobustnessTest(unittest.TestCase):
    def test_damaged_icc_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.icm"
            path.write_bytes(_icc_with_bad_vcgt())
            with self.assertRaises(ValueError):
                icc.read(path)

    def test_typo_in_a_profile_is_skipped(self):
        display = Display(gdi_name="\\\\.\\DISPLAY1", hmonitor=1, rect=(0, 0, 1920, 1080), primary=True,
                          device_path="\\\\?\\DISPLAY#DEL4123#5&1&0&UID1#{x}")
        state = engine.DisplayState(display=display, ramp=gamma.identity())
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(profiles, "PROFILE_DIR", Path(folder)):
            (Path(folder) / "DEL4123.json").write_text(json.dumps({"match": {"pnp_id": "DEL4123"}, "gpu": {"gamma": "abc"}}))
            (Path(folder) / "list.json").write_text("[1, 2, 3]")
            with mock.patch.object(profiles.icc, "COLOR_DIR", Path(folder)):
                self.assertEqual([c for c in profiles.find(display, state) if c.source.startswith("profiles/")], [])


if __name__ == "__main__":
    unittest.main()
