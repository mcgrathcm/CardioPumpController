import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cardiopump_backend.arduino import (
    POSITIONAL_TELEMETRY_FIELDS,
    ArduinoSerialBridge,
    ArduinoState,
)
from cardiopump_backend.server import CardiopumpRequestHandler
from cardiopump_backend.waveform import program_from_text


class ArduinoTelemetryTests(unittest.TestCase):
    def test_parses_controlled_stop_and_timing_diagnostics(self):
        bridge = object.__new__(ArduinoSerialBridge)
        bridge._lock = threading.Lock()
        bridge._state = ArduinoState()

        bridge._handle_line(
            "TEL controlled_stop_active=1 controlled_stop_code=heartbeat_timeout "
            "timing_lateness_us=125 timing_max_lateness_us=400 "
            "timing_write_us=360 timing_max_write_us=475 "
            "timing_warning_count=2 timing_overrun_count=1 "
            "ramp_up_active=1 ramp_update_count=125 ramp_missed_deadline_count=0 "
            "fault_clear_ready=1 calibration_valid_mask=3 "
            "cal0_flow_ul_s=100000 cal1_flow_ul_s=200000 "
            "watchdog_ok=1 reset_cause=watchdog retained_last_fault=protocol"
        )

        telemetry = bridge.telemetry()
        self.assertTrue(telemetry["controlled_stop_active"])
        self.assertEqual(telemetry["controlled_stop_code"], "heartbeat_timeout")
        self.assertEqual(telemetry["timing_lateness_us"], 125)
        self.assertEqual(telemetry["timing_max_lateness_us"], 400)
        self.assertEqual(telemetry["timing_write_us"], 360)
        self.assertEqual(telemetry["timing_max_write_us"], 475)
        self.assertEqual(telemetry["timing_warning_count"], 2)
        self.assertEqual(telemetry["timing_overrun_count"], 1)
        self.assertTrue(telemetry["ramp_up_active"])
        self.assertEqual(telemetry["ramp_update_count"], 125)
        self.assertTrue(telemetry["fault_clear_ready"])
        self.assertEqual(telemetry["calibration_flows_ml_s"][:2], [0.1, 0.2])
        self.assertTrue(telemetry["watchdog_ok"])
        self.assertEqual(telemetry["reset_cause"], "watchdog")

    def test_compact_heartbeat_refreshes_and_updates_runtime_state(self):
        bridge = object.__new__(ArduinoSerialBridge)
        bridge._lock = threading.Lock()
        bridge._response_condition = threading.Condition(bridge._lock)
        bridge._state = ArduinoState(connected=True)
        bridge._pending_sequences = set()
        bridge._responses = {}

        bridge._handle_line("HB4 w 0 0 1 0 1 1 1 1 1 0 250")

        telemetry = bridge.telemetry()
        self.assertTrue(telemetry["fresh"])
        self.assertTrue(telemetry["protocol_compatible"])
        self.assertTrue(telemetry["dac_ok"])
        self.assertTrue(telemetry["wave_running"])
        self.assertTrue(telemetry["arduino_enable"])
        self.assertTrue(telemetry["servo_ready"])
        self.assertFalse(telemetry["pressure_latch_set"])
        self.assertEqual(telemetry["dac_mv"], 250)
        self.assertEqual(telemetry["run_mode"], "waveform")
        self.assertTrue(telemetry["ramp_up_active"])
        self.assertEqual(telemetry["calibration_stage"], "ramp_up")

    def test_positional_v4_telemetry_decodes_numeric_enums(self):
        bridge = object.__new__(ArduinoSerialBridge)
        bridge._lock = threading.Lock()
        bridge._state = ArduinoState()
        values = {field: "0" for field in POSITIONAL_TELEMETRY_FIELDS}
        values.update(
            {
                "dac_range": "1",
                "dac_ok": "1",
                "run_mode": "3",
                "fault_latched": "1",
                "fault_code": "9",
                "calibration_stage": "2",
                "reset_cause": "1",
                "retained_last_fault": "5",
                "watchdog_ok": "1",
                "uptime_ms": "1234",
            }
        )
        line = "TEL4 " + " ".join(values[field] for field in POSITIONAL_TELEMETRY_FIELDS)

        bridge._handle_line(line)

        telemetry = bridge.telemetry()
        self.assertTrue(telemetry["protocol_compatible"])
        self.assertEqual(telemetry["run_mode"], "calibration")
        self.assertEqual(telemetry["fault_code"], "watchdog_reset")
        self.assertEqual(telemetry["calibration_stage"], "flat")
        self.assertEqual(telemetry["reset_cause"], "watchdog")
        self.assertEqual(telemetry["retained_last_fault"], "timing_overrun")
        self.assertEqual(telemetry["uptime_ms"], 1234)

    def test_open_serial_port_with_stale_response_is_not_fresh(self):
        bridge = object.__new__(ArduinoSerialBridge)
        bridge._lock = threading.Lock()
        bridge._state = ArduinoState(connected=True, last_seen_s=time.monotonic() - 1.0)

        telemetry = bridge.telemetry()

        self.assertTrue(telemetry["connected"])
        self.assertFalse(telemetry["fresh"])

    def test_raw_nonzero_voltage_is_rejected_before_serial_write(self):
        bridge = object.__new__(ArduinoSerialBridge)

        with self.assertRaisesRegex(ValueError, "raw nonzero"):
            bridge.set_voltage(1.0)

    def test_waveform_transfer_uses_crc32_and_sequential_offsets(self):
        bridge = object.__new__(ArduinoSerialBridge)
        bridge._write_lock = threading.Lock()
        bridge._request_unlocked = Mock(return_value={})

        bridge.load_waveform((1, -1), 250, 1.25)

        self.assertEqual(
            bridge._request_unlocked.call_args_list[0].args[0],
            "WAVE_BEGIN 250 2 668904070 1250000",
        )
        self.assertEqual(
            bridge._request_unlocked.call_args_list[1].args[0],
            "WAVE_DATA 0 1 -1",
        )
        self.assertEqual(bridge._request_unlocked.call_args_list[2].args[0], "WAVE_END")

    def test_physical_flow_commands_are_sent_in_integer_microliters_per_second(self):
        bridge = object.__new__(ArduinoSerialBridge)
        bridge._request = Mock(return_value={})

        bridge.start_constant(0.125)
        bridge.save_calibration_result(2500, 0.25)

        self.assertEqual(bridge._request.call_args_list[0].args[0], "START_CONSTANT 125000")
        self.assertEqual(bridge._request.call_args_list[1].args[0], "CAL_RESULT 2500 250000")

    def test_calibration_file_values_load_atomically(self):
        bridge = object.__new__(ArduinoSerialBridge)
        bridge._request = Mock(return_value={})

        bridge.load_calibration((0.1, 0.2, 0.35, 0.5))

        bridge._request.assert_called_once_with("CAL_LOAD 100000 200000 350000 500000")

    def test_correlated_ack_resolves_pending_request(self):
        bridge = object.__new__(ArduinoSerialBridge)
        bridge._lock = threading.Lock()
        bridge._response_condition = threading.Condition(bridge._lock)
        bridge._state = ArduinoState(connected=True)
        bridge._pending_sequences = {42}
        bridge._responses = {}

        bridge._handle_line("ACK seq=42 action=start_wave wave_running=1")

        ok, values, _ = bridge._responses[42]
        self.assertTrue(ok)
        self.assertEqual(values["action"], "start_wave")


class StartReadinessTests(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(CardiopumpRequestHandler)
        self.ready = {
            "connected": True,
            "fresh": True,
            "protocol_compatible": True,
            "dac_ok": True,
            "fault_latched": False,
            "controlled_stop_active": False,
            "run_mode": "stopped",
            "wave_running": False,
            "pressure_latch_set": False,
            "arduino_enable": True,
            "servo_ready": True,
        }

    def test_ready_state_is_accepted(self):
        self.handler._require_start_ready(self.ready)

    def test_stale_dac_or_existing_run_is_rejected(self):
        for update, message in (
            ({"fresh": False}, "stale"),
            ({"protocol_compatible": False}, "firmware protocol"),
            ({"dac_ok": False}, "DAC"),
            ({"run_mode": "waveform"}, "already running"),
        ):
            with self.subTest(update=update), self.assertRaisesRegex(RuntimeError, message):
                self.handler._require_start_ready(self.ready | update)

    def test_calibrated_max_flow_replaces_fallback_limit(self):
        self.assertEqual(
            self.handler._max_flow_ml_s(
                {
                    "calibration_valid": True,
                    "calibration_flows_ml_s": [0.2, 0.5, 0.9, 1.4],
                }
            ),
            1.4,
        )
        self.assertEqual(self.handler._max_flow_ml_s({}), 1.0)

    def test_loaded_calibration_is_available_without_serial_bridge(self):
        self.handler.arduino = None
        self.handler.server = SimpleNamespace(
            loaded_calibration_flows_ml_s=(0.2, 0.5, 0.9, 1.4),
            calibration_pending_transfer=True,
        )

        hardware = self.handler._hardware_telemetry()

        self.assertFalse(hardware["connected"])
        self.assertTrue(hardware["calibration_valid"])
        self.assertTrue(hardware["calibration_pending_transfer"])
        self.assertEqual(hardware["calibration_flows_ml_s"], [0.2, 0.5, 0.9, 1.4])
        self.assertEqual(self.handler._max_flow_ml_s(hardware), 1.4)

    def test_pending_loaded_calibration_syncs_when_hardware_is_ready(self):
        bridge = Mock()
        bridge.telemetry.return_value = self.ready | {
            "calibration_valid": True,
            "calibration_flows_ml_s": [0.2, 0.5, 0.9, 1.4],
        }
        self.handler.arduino = bridge
        self.handler.server = SimpleNamespace(
            loaded_calibration_flows_ml_s=(0.2, 0.5, 0.9, 1.4),
            calibration_pending_transfer=True,
        )

        hardware = self.handler._sync_loaded_calibration(self.ready)

        bridge.load_calibration.assert_called_once_with((0.2, 0.5, 0.9, 1.4))
        bridge.request_telemetry.assert_called_once_with()
        self.assertFalse(self.handler.server.calibration_pending_transfer)
        self.assertTrue(hardware["calibration_valid"])
        self.assertFalse(hardware["calibration_pending_transfer"])

    def test_pulsatile_and_curve_designer_programs_switch_independently(self):
        pulsatile = program_from_text("0 0\n1000 1\n")
        curve_designer = program_from_text("0 0\n500 0.5\n1000 0\n")
        self.handler.server = SimpleNamespace(
            waveform_program=curve_designer,
            waveform_source="curve_designer",
            pulsatile_waveform_program=pulsatile,
            curve_designer_waveform_program=curve_designer,
        )

        self.handler._activate_waveform_source("pulsatile")
        self.assertIs(self.handler.server.waveform_program, pulsatile)
        self.handler._activate_waveform_source("curve_designer")
        self.assertIs(self.handler.server.waveform_program, curve_designer)

        edited_curve = curve_designer.with_settings(0.75, 1000.0)
        self.handler.server.waveform_program = edited_curve
        self.handler._cache_active_waveform()
        self.assertIs(self.handler.server.curve_designer_waveform_program, edited_curve)
        self.assertIs(self.handler.server.pulsatile_waveform_program, pulsatile)

if __name__ == "__main__":
    unittest.main()
