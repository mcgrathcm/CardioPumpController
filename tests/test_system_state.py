import unittest

from cardiopump_backend.system_state import derive_system_state


def hardware(**values):
    defaults = {
        "connected": True,
        "last_error": None,
        "fault_latched": False,
        "fault_code": None,
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

    def test_overpressure_is_a_fault(self):
        self.assertEqual(
            derive_system_state(
                hardware(pressure_latch_set=True), running=False, communication_ok=True
            ),
            {"state": "fault", "reason": "overpressure_latched"},
        )


if __name__ == "__main__":
    unittest.main()
