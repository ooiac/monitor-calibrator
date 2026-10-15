"""Monitors must get their own backup folder, even two of the same model with placeholder serials."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from calibrator import backup, displays, edid


def _edid(serial_number=0x01010101, serial_text=""):
    return edid.Edid(
        raw=b"", manufacturer="DEL", product_code=0x4123, serial_number=serial_number, serial_text=serial_text,
        name="DELL TEST", week=1, year=2024, version="1.4", interface="DisplayPort", size_cm=(60, 34),
        gamma=2.2, red=(0.64, 0.33), green=(0.30, 0.60), blue=(0.15, 0.06), white=(0.3127, 0.3290),
    )


def _display(port: int, serial_number=0x01010101, serial_text=""):
    return displays.Display(
        gdi_name=f"\\\\.\\DISPLAY{port}", hmonitor=port, rect=(0, 0, 1920, 1080), primary=port == 1,
        device_path=f"\\\\?\\DISPLAY#DEL4123#5&1a2b3c4d&0&UID{port}#{{e6f07b5f}}",
        edid=_edid(serial_number, serial_text),
    )


class IdentityTest(unittest.TestCase):
    def test_placeholder_serials_are_ignored(self):
        for number in (0, 1, 0x01010101, 0xFFFFFFFF):
            self.assertEqual(_edid(number).serial, "")
        self.assertEqual(_edid(0x01010101, "000000000").serial, "")
        self.assertEqual(_edid(12345678).serial, "12345678")
        self.assertEqual(_edid(0x01010101, "CN0ABC123").serial, "CN0ABC123")

    def test_identical_monitors_get_separate_folders(self):
        left, right = _display(1), _display(2)
        self.assertNotEqual(left.key, right.key)
        self.assertEqual(left.legacy_key, right.legacy_key)  # the bug the new key fixes

    def test_real_serial_follows_the_monitor_to_any_port(self):
        self.assertEqual(_display(1, 12345678).key, _display(7, 12345678).key)

    def test_old_folders_move_once(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(backup, "DATA_DIR", Path(home)):
            one = _display(1)
            old = Path(home) / "displays" / one.legacy_key
            old.mkdir(parents=True)
            (old / "original.json").write_text("{}")
            self.assertEqual(len(backup.migrate_folders([one])), 1)
            self.assertTrue((Path(home) / "displays" / one.key / "original.json").exists())
            self.assertEqual(backup.migrate_folders([one]), [])  # nothing left to move
            # Two identical monitors both claim the old folder: too ambiguous, leave it alone.
            old.mkdir(parents=True)
            self.assertEqual(backup.migrate_folders([_display(1), _display(2)]), [])
            self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
