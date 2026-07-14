import unittest

from cardiopump_backend.system_state import derive_system_state


def hardware(**values):
    defaults = {
        "connected": True,
        "fresh": True,
        "protocol_compatible": True,
        "last_error": None,
        "fault_latched": False,
        "fault_code": None,
        "controlled_stop_active": False,
        "controlled_stop_code": None,
        "ramp_up_active": False,
        "ramp_down_active": False,
        "run_mode": "stopped",
        "calibration_stage": "idle",
        "pressure_latch_set": False,
        "arduino_enable": False,
        "servo_ready": False,
    }
    defaults.update(values)
    return defaults


class SystemStateTests(unittest.TestCase):
    def test_disconnected_has_priority(self):
        self.assertEqual(
            derive_system_state(hardware(connected=False), running=True, communication_ok=True),
            {"state": "disconnected", "reason": "connection_lost"},
        )

    def test_disabled_is_safe_with_relay_open(self):
        self.assertEqual(
            derive_system_state(hardware(), running=False, communication_ok=True),
            {"state": "disabled", "reason": None},
        )

    def test_stale_telemetry_is_disconnected(self):
        self.assertEqual(
            derive_system_state(hardware(fresh=False), running=False, communication_ok=True),
            {"state": "disconnected", "reason": "telemetry_stale"},
        )

    def test_protocol_mismatch_is_disconnected(self):
        self.assertEqual(
            derive_system_state(
                hardware(protocol_compatible=False), running=False, communication_ok=True
            ),
            {"state": "disconnected", "reason": "protocol_mismatch"},
        )

    def test_stopped_requires_enable_and_servo_ready(self):
        self.assertEqual(
            derive_system_state(
                hardware(arduino_enable=True, servo_ready=True),
                running=False,
                communication_ok=True,
            ),
            {"state": "stopped", "reason": None},
        )

    def test_running_has_priority_over_stopped(self):
        self.assertEqual(
            derive_system_state(
                hardware(arduino_enable=True, servo_ready=True),
                running=True,
                communication_ok=True,
            ),
            {"state": "running", "reason": None},
        )

    def test_fault_latch_has_priority_over_running(self):
        self.assertEqual(
            derive_system_state(
                hardware(
                    arduino_enable=True,
                    servo_ready=True,
                    fault_latched=True,
                    fault_code="unexpected_servo_ready",
                ),
                running=True,
                communication_ok=True,
            ),
            {"state": "fault", "reason": "unexpected_servo_ready"},
        )

    def test_controlled_stop_has_priority_over_running(self):
        self.assertEqual(
            derive_system_state(
                hardware(
                    arduino_enable=True,
                    servo_ready=True,
                    controlled_stop_active=True,
                    controlled_stop_code="timing_overrun",
                ),
                running=True,
                communication_ok=True,
            ),
            {"state": "stopping", "reason": "timing_overrun"},
        )

    def test_ramp_up_is_starting_and_calibration_flat_is_calibrating(self):
        self.assertEqual(
            derive_system_state(
                hardware(ramp_up_active=True), running=True, communication_ok=True
            ),
            {"state": "starting", "reason": "ramp_up"},
        )
        self.assertEqual(
            derive_system_state(
                hardware(run_mode="calibration", calibration_stage="flat"),
                running=True,
                communication_ok=True,
            ),
            {"state": "calibrating", "reason": "flat"},
        )

    def test_normal_ramp_down_is_stopping(self):
        self.assertEqual(
            derive_system_state(
                hardware(ramp_down_active=True), running=False, communication_ok=True
            ),
            {"state": "stopping", "reason": "ramp_down"},
        )

    def test_overpressure_is_a_fault(self):
        self.assertEqual(
            derive_system_state(
                hardware(pressure_latch_set=True), running=False, communication_ok=True
            ),
            {"state": "fault", "reason": "overpressure_latched"},
        )


if __name__ == "__main__":
    unittest.main()
