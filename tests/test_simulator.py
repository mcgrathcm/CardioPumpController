import unittest

from cardiopump_backend.constants import DAC_I2C_ADDRESS
from cardiopump_backend.curve import CurvePoint, VelocityCurve
from cardiopump_backend.simulator import PumpControllerSimulator


class PumpControllerSimulatorTests(unittest.TestCase):
    def test_start_tracks_curve_and_scales_voltage(self):
        controller = PumpControllerSimulator(now_s=0.0)
        controller.load_curve(
            VelocityCurve([CurvePoint(0.0, 0.0), CurvePoint(1.0, 1.0)]),
            now_s=0.0,
        )

        controller.start(now_s=1.0)
        telemetry = controller.telemetry(now_s=1.5)

        self.assertTrue(telemetry["running"])
        self.assertAlmostEqual(telemetry["velocity_fraction"], 0.5)
        self.assertAlmostEqual(telemetry["dac_voltage"], 5.0)
        self.assertEqual(telemetry["dac_i2c_address"], DAC_I2C_ADDRESS)
        self.assertTrue(telemetry["interlocks"]["arduino_relay_closed"])

    def test_watchdog_forces_safe_state_after_half_second_without_host_contact(self):
        controller = PumpControllerSimulator(now_s=0.0)
        controller.load_curve(
            VelocityCurve([CurvePoint(0.0, 1.0), CurvePoint(1.0, 1.0)]),
            now_s=0.0,
        )

        controller.start(now_s=0.0)
        telemetry = controller.telemetry(now_s=0.501)

        self.assertFalse(telemetry["running"])
        self.assertFalse(telemetry["communication_ok"])
        self.assertTrue(telemetry["safety_tripped"])
        self.assertEqual(telemetry["velocity_fraction"], 0.0)
        self.assertFalse(telemetry["interlocks"]["arduino_relay_closed"])

    def test_interlock_change_stops_pump(self):
        controller = PumpControllerSimulator(now_s=0.0)
        controller.start(now_s=0.0)

        controller.set_simulated_interlocks(overpressure_latched=True, now_s=0.1)
        telemetry = controller.telemetry(now_s=0.1)

        self.assertFalse(telemetry["running"])
        self.assertTrue(telemetry["safety_tripped"])
        self.assertFalse(telemetry["interlocks"]["arduino_relay_closed"])

    def test_cannot_load_curve_while_running(self):
        controller = PumpControllerSimulator(now_s=0.0)
        controller.start(now_s=0.0)

        with self.assertRaisesRegex(RuntimeError, "stop pump"):
            controller.load_curve(
                VelocityCurve([CurvePoint(0.0, 0.0), CurvePoint(1.0, 0.5)]),
                now_s=0.1,
            )


if __name__ == "__main__":
    unittest.main()
