import unittest

from cardiopump_backend.curve import CurvePoint, VelocityCurve, curve_from_json


class VelocityCurveTests(unittest.TestCase):
    def test_interpolates_and_loops_curve(self):
        curve = VelocityCurve(
            [
                CurvePoint(0.0, 0.0),
                CurvePoint(1.0, 1.0),
                CurvePoint(2.0, 0.0),
            ]
        )

        self.assertAlmostEqual(curve.velocity_at(0.5), 0.5)
        self.assertAlmostEqual(curve.velocity_at(1.5), 0.5)
        self.assertAlmostEqual(curve.velocity_at(2.5), 0.5)

    def test_rejects_out_of_range_velocity(self):
        with self.assertRaisesRegex(ValueError, "between -1.0 and 1.0"):
            VelocityCurve([CurvePoint(0.0, 0.0), CurvePoint(1.0, 1.1)])

    def test_parses_json_points(self):
        curve = curve_from_json(
            [
                {"time_s": 0, "velocity_fraction": 0},
                {"time_s": 1, "velocity_fraction": -0.25},
            ]
        )

        self.assertAlmostEqual(curve.velocity_at(1.0), 0.0)


if __name__ == "__main__":
    unittest.main()
