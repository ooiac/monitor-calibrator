"""The shadows curve, enhanced blacks and live-session calibrations."""
import unittest
from types import SimpleNamespace

from calibrator import blacks, ddc, engine, gamma


class ShadowsTest(unittest.TestCase):
    def test_black_and_white_never_move(self):
        for shadows in (-1, -0.3, 0, 0.5, 1):
            self.assertEqual(gamma.toe(0.0, shadows), 0.0)
            self.assertAlmostEqual(gamma.toe(1.0, shadows), 1.0)

    def test_curve_always_rises(self):
        for shadows in (-1, -0.3, 0.4, 1):
            values = [gamma.toe(i / 1000, shadows) for i in range(1001)]
            self.assertTrue(all(b > a for a, b in zip(values, values[1:])), shadows)

    def test_negative_darkens_near_black_only(self):
        self.assertLess(gamma.toe(0.05, -0.3), 0.05)
        self.assertAlmostEqual(gamma.toe(0.9, -0.3), 0.9, delta=0.001)

    def test_default_settings_are_unchanged(self):
        # Adding shadows and white level must not change any existing calibration.
        self.assertEqual(gamma.from_settings({}), gamma.build())
        self.assertTrue(gamma.is_neutral_settings({"shadows": 0, "white_level": 100}))
        self.assertFalse(gamma.is_neutral_settings({"shadows": blacks.SHADOWS}))

    def test_black_level_target_never_raises(self):
        self.assertEqual(blacks.black_level_target(80, 100), 50)
        self.assertEqual(blacks.black_level_target(30, 100), 30)


class TunedCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.state = engine.DisplayState(display=SimpleNamespace(name="x"), ramp=gamma.identity(),
                                         ddc_values={ddc.BRIGHTNESS: (80, 100), ddc.RED_BLACK: (80, 100)})
        self.start = {"temperature": 6500, "tint": 0, "gamma": 2.2, "shadows": 0, "white_level": 100}

    def test_nothing_changed_means_nothing_saved(self):
        self.assertIsNone(engine.tuned_calibration(self.state, dict(self.start), self.start,
                                                   {ddc.BRIGHTNESS: 80}, None, "t"))

    def test_only_what_changed_is_kept(self):
        tuned = dict(self.start, shadows=-3)
        cal = engine.tuned_calibration(self.state, tuned, self.start, {ddc.BRIGHTNESS: 60, ddc.RED_BLACK: 80}, None, "t")
        self.assertIsNotNone(cal.ramp)
        self.assertEqual(cal.ddc, {ddc.BRIGHTNESS: 60})


if __name__ == "__main__":
    unittest.main()
