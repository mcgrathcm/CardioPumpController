from __future__ import annotations

import glob
import math
import os
import struct
import termios
import threading
import time
import tty
import zlib
from dataclasses import dataclass, field

from .constants import ARDUINO_PROTOCOL_VERSION, HARDWARE_FRESHNESS_TIMEOUT_MS


POSITIONAL_TELEMETRY_FIELDS = (
    "pin9", "pin10", "pin11", "pin12",
    "pressure_latch_set", "servo_ready", "arduino_enable",
    "dac_mv", "dac_data", "dac_range", "dac_ok",
    "wave_loaded", "wave_running", "wave_samples", "wave_rate_hz", "wave_index",
    "run_mode", "trigger_active", "trigger_high_us", "trigger_count",
    "ramp_up_active", "ramp_down_active",
    "controlled_stop_active", "controlled_stop_code", "fault_latched", "fault_code",
    "fault_clear_safe_ms", "fault_clear_ready",
    "calibration_active", "calibration_stage", "calibration_complete",
    "calibration_target_mv", "calibration_valid_mask", "calibration_valid",
    "cal0_flow_ul_s", "cal1_flow_ul_s", "cal2_flow_ul_s", "cal3_flow_ul_s",
    "timing_lateness_us", "timing_max_lateness_us",
    "timing_write_us", "timing_max_write_us",
    "timing_warning_count", "timing_overrun_count",
    "ramp_update_count", "ramp_missed_deadline_count", "ramp_max_lateness_us",
    "serial_overflow_count", "protocol_error_count", "dac_write_failure_count",
    "heartbeat_age_ms", "heartbeat_max_gap_ms",
    "watchdog_ok", "watchdog_timeout_ms", "reset_cause", "reset_status1",
    "retained_last_fault", "retained_fault_count", "uptime_ms",
)
COMPACT_HEARTBEAT_FIELDS = ("m", "f", "s", "u", "n", "g", "d", "w", "e", "r", "p", "a")

FAULT_CODES = {
    "0": "none",
    "1": "unexpected_servo_ready",
    "2": "overpressure",
    "3": "interlock",
    "4": "heartbeat_timeout",
    "5": "timing_overrun",
    "6": "dac_communication",
    "7": "protocol",
    "8": "watchdog_init",
    "9": "watchdog_reset",
}
RUN_MODES = {"0": "stopped", "1": "constant", "2": "waveform", "3": "calibration"}
CALIBRATION_STAGES = {
    "0": "idle", "1": "ramp_up", "2": "flat", "3": "ramp_down", "4": "complete"
}
RESET_CAUSES = {
    "0": "power_on_or_external",
    "1": "watchdog",
    "2": "independent_watchdog",
    "3": "software",
    "4": "error",
}


@dataclass
class ArduinoState:
    connected: bool = False
    port: str | None = None
    last_seen_s: float | None = None
    pin9: bool | None = None
    pin10: bool | None = None
    pin11: bool | None = None
    pin12: bool | None = None
    protocol_version: int = 0
    pressure_latch_set: bool | None = None
    servo_ready: bool | None = None
    arduino_enable: bool | None = None
    dac_ok: bool = False
    dac_mv: int = 0
    dac_data: int = 0
    dac_range: str | None = None
    wave_loaded: bool = False
    wave_running: bool = False
    wave_samples: int = 0
    wave_rate_hz: int = 0
    wave_index: int = 0
    ramp_up_active: bool = False
    ramp_down_active: bool = False
    controlled_stop_active: bool = False
    controlled_stop_code: str | None = None
    run_mode: str = "stopped"
    trigger_active: bool = False
    trigger_high_us: int = 10000
    trigger_count: int = 0
    fault_latched: bool = False
    fault_code: str | None = None
    timing_lateness_us: int = 0
    timing_max_lateness_us: int = 0
    timing_write_us: int = 0
    timing_max_write_us: int = 0
    timing_warning_count: int = 0
    timing_overrun_count: int = 0
    ramp_update_count: int = 0
    ramp_missed_deadline_count: int = 0
    ramp_max_lateness_us: int = 0
    serial_overflow_count: int = 0
    protocol_error_count: int = 0
    dac_write_failure_count: int = 0
    heartbeat_age_ms: int = 0
    heartbeat_max_gap_ms: int = 0
    fault_clear_safe_ms: int = 0
    fault_clear_ready: bool = False
    calibration_active: bool = False
    calibration_stage: str = "idle"
    calibration_complete: bool = False
    calibration_target_mv: int = 0
    calibration_valid_mask: int = 0
    calibration_valid: bool = False
    calibration_flows_ul_s: tuple[int, int, int, int] = (0, 0, 0, 0)
    watchdog_ok: bool = False
    watchdog_timeout_ms: int = 0
    reset_cause: str | None = None
    reset_status1: int = 0
    retained_last_fault: str | None = None
    retained_fault_count: int = 0
    uptime_ms: int = 0
    last_error: str | None = None
    raw_line: str | None = None


class ArduinoSerialBridge:
    def __init__(self, port: str | None = None, baudrate: int = 115200):
        self._configured_port = port
        self._baudrate = baudrate
        self._fd: int | None = None
        self._lock = threading.Lock()
        self._response_condition = threading.Condition(self._lock)
        self._write_lock = threading.Lock()
        self._state = ArduinoState(port=port)
        self._next_sequence = 1
        self._pending_sequences: set[int] = set()
        self._responses: dict[int, tuple[bool, dict[str, str], str]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def telemetry(self) -> dict[str, object]:
        with self._lock:
            state = ArduinoState(**self._state.__dict__)

        last_seen_ms = None
        if state.last_seen_s is not None:
            last_seen_ms = round((time.monotonic() - state.last_seen_s) * 1000)
        fresh = bool(
            state.connected
            and last_seen_ms is not None
            and last_seen_ms <= HARDWARE_FRESHNESS_TIMEOUT_MS
        )

        return {
            "connected": state.connected,
            "fresh": fresh,
            "port": state.port,
            "last_seen_ms": last_seen_ms,
            "pin9_pressure_latch_input": state.pin9,
            "pin10_servo_ready_input": state.pin10,
            "pin11_arduino_enable_output": state.pin11,
            "pin12_latch_reset_output": state.pin12,
            "protocol_version": state.protocol_version,
            "protocol_compatible": state.protocol_version == ARDUINO_PROTOCOL_VERSION,
            "pressure_latch_set": state.pressure_latch_set,
            "servo_ready": state.servo_ready,
            "arduino_enable": state.arduino_enable,
            "dac_ok": state.dac_ok,
            "dac_mv": state.dac_mv,
            "dac_data": state.dac_data,
            "dac_range": state.dac_range,
            "dac_voltage": state.dac_mv / 1000,
            "wave_loaded": state.wave_loaded,
            "wave_running": state.wave_running,
            "wave_samples": state.wave_samples,
            "wave_rate_hz": state.wave_rate_hz,
            "wave_index": state.wave_index,
            "ramp_up_active": state.ramp_up_active,
            "ramp_down_active": state.ramp_down_active,
            "controlled_stop_active": state.controlled_stop_active,
            "controlled_stop_code": state.controlled_stop_code,
            "run_mode": state.run_mode,
            "trigger_active": state.trigger_active,
            "trigger_high_us": state.trigger_high_us,
            "trigger_high_ms": state.trigger_high_us / 1000,
            "trigger_count": state.trigger_count,
            "fault_latched": state.fault_latched,
            "fault_code": state.fault_code,
            "timing_lateness_us": state.timing_lateness_us,
            "timing_max_lateness_us": state.timing_max_lateness_us,
            "timing_write_us": state.timing_write_us,
            "timing_max_write_us": state.timing_max_write_us,
            "timing_warning_count": state.timing_warning_count,
            "timing_overrun_count": state.timing_overrun_count,
            "ramp_update_count": state.ramp_update_count,
            "ramp_missed_deadline_count": state.ramp_missed_deadline_count,
            "ramp_max_lateness_us": state.ramp_max_lateness_us,
            "serial_overflow_count": state.serial_overflow_count,
            "protocol_error_count": state.protocol_error_count,
            "dac_write_failure_count": state.dac_write_failure_count,
            "heartbeat_age_ms": state.heartbeat_age_ms,
            "heartbeat_max_gap_ms": state.heartbeat_max_gap_ms,
            "fault_clear_safe_ms": state.fault_clear_safe_ms,
            "fault_clear_ready": state.fault_clear_ready,
            "calibration_active": state.calibration_active,
            "calibration_stage": state.calibration_stage,
            "calibration_complete": state.calibration_complete,
            "calibration_target_mv": state.calibration_target_mv,
            "calibration_valid_mask": state.calibration_valid_mask,
            "calibration_valid": state.calibration_valid,
            "calibration_flows_ml_s": [value / 1_000_000 for value in state.calibration_flows_ul_s],
            "watchdog_ok": state.watchdog_ok,
            "watchdog_timeout_ms": state.watchdog_timeout_ms,
            "reset_cause": state.reset_cause,
            "reset_status1": state.reset_status1,
            "retained_last_fault": state.retained_last_fault,
            "retained_fault_count": state.retained_fault_count,
            "uptime_ms": state.uptime_ms,
            "last_error": state.last_error,
            "raw_line": state.raw_line,
        }

    def set_voltage(self, voltage: float) -> int:
        if not -10.0 <= voltage <= 10.0:
            raise ValueError("dac voltage must be between -10.0 and 10.0")
        millivolts = round(voltage * 1000)
        if millivolts != 0:
            raise ValueError("raw nonzero DAC voltage is disabled; use a guarded run mode")
        self._request(f"VOLT {millivolts}")
        return millivolts

    def set_trigger_high_us(self, high_us: int) -> None:
        if not 100 <= high_us <= 1_000_000:
            raise ValueError("trigger high time must be between 0.1 ms and 1000 ms")
        self._request(f"TRIGGER_US {high_us}")

    def start_constant(self, flow_ml_s: float) -> None:
        if flow_ml_s < 0.0:
            raise ValueError("constant flow must be non-negative")
        self._request(f"START_CONSTANT {round(flow_ml_s * 1_000_000)}")

    def start_calibration(self, millivolts: int) -> None:
        if millivolts not in (2500, 5000, 7500, 10000):
            raise ValueError("calibration voltage must be 2.5, 5, 7.5, or 10 V")
        self._request(f"CAL_START {millivolts}")

    def save_calibration_result(self, millivolts: int, flow_ml_s: float) -> None:
        if flow_ml_s <= 0.0:
            raise ValueError("calibration flow must be positive")
        self._request(f"CAL_RESULT {millivolts} {round(flow_ml_s * 1_000_000)}")

    def clear_calibration(self) -> None:
        self._request("CAL_CLEAR")

    def load_calibration(self, flows_ml_s: tuple[float, float, float, float]) -> None:
        if len(flows_ml_s) != 4 or any(
            not math.isfinite(flow) or flow <= 0.0 for flow in flows_ml_s
        ):
            raise ValueError("calibration requires four positive flow values")
        if any(right <= left for left, right in zip(flows_ml_s, flows_ml_s[1:])):
            raise ValueError("calibration flow values must be strictly increasing")
        values = " ".join(str(round(flow * 1_000_000)) for flow in flows_ml_s)
        self._request(f"CAL_LOAD {values}")

    def wait_for_dac_mv(self, millivolts: int, timeout_s: float = 0.5) -> bool:
        deadline_s = time.monotonic() + timeout_s
        while time.monotonic() < deadline_s:
            with self._lock:
                if self._state.dac_mv == millivolts:
                    return True
            time.sleep(0.01)
        return False

    def wait_for_wave_loaded(self, sample_count: int, timeout_s: float = 1.0) -> bool:
        deadline_s = time.monotonic() + timeout_s
        while time.monotonic() < deadline_s:
            with self._lock:
                if self._state.wave_loaded and self._state.wave_samples == sample_count:
                    return True
            time.sleep(0.01)
        return False

    def wait_for_ramp_down(self, timeout_s: float = 1.0) -> bool:
        deadline_s = time.monotonic() + timeout_s
        earliest_ready_s = time.monotonic() + 0.05
        ramp_seen = False
        while time.monotonic() < deadline_s:
            with self._lock:
                ramp_seen = ramp_seen or self._state.ramp_down_active
                ready = not self._state.ramp_down_active and self._state.dac_mv == 0
            ready_window_open = time.monotonic() >= earliest_ready_s
            if ready_window_open and ready and (
                ramp_seen or time.monotonic() >= earliest_ready_s + 0.1
            ):
                return True
            time.sleep(0.01)
        return False

    def set_arduino_enable(self, enabled: bool) -> None:
        self._request(f"ENABLE {1 if enabled else 0}")

    def heartbeat(self) -> None:
        if not self._write_lock.acquire(blocking=False):
            return
        try:
            self._send_command_unlocked("HEARTBEAT")
        finally:
            self._write_lock.release()

    def reset_pressure_latch(self) -> None:
        self._request("RESET_LATCH")

    def clear_fault(self) -> None:
        self._request("CLEAR_FAULT")

    def request_telemetry(self) -> None:
        self._send_command("GET")

    def load_waveform(
        self, samples: tuple[int, ...], update_hz: int, peak_flow_ml_s: float
    ) -> None:
        if not samples:
            raise ValueError("waveform must contain at least one sample")
        crc = zlib.crc32(b"".join(struct.pack("<h", sample) for sample in samples))
        with self._write_lock:
            peak_flow_ul_s = round(peak_flow_ml_s * 1_000_000)
            self._request_unlocked(
                f"WAVE_BEGIN {update_hz} {len(samples)} {crc} {peak_flow_ul_s}"
            )
            chunk_size = 12
            for offset in range(0, len(samples), chunk_size):
                chunk = samples[offset : offset + chunk_size]
                values = " ".join(str(sample) for sample in chunk)
                self._request_unlocked(f"WAVE_DATA {offset} {values}")
            self._request_unlocked("WAVE_END", timeout_s=5.0)

    def start_waveform(self) -> None:
        self._request("START_WAVE")

    def stop_waveform(self) -> None:
        self._request("STOP")

    def close(self) -> None:
        self._stop.set()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _run(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            if self._fd is None:
                self._connect()
                time.sleep(0.2)
                continue

            try:
                chunk = os.read(self._fd, 256)
                if not chunk:
                    time.sleep(0.02)
                    continue
                buffer += chunk
            except BlockingIOError:
                time.sleep(0.02)
                continue
            except OSError as exc:
                self._mark_disconnected(str(exc))
                buffer = b""
                continue

            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    self._handle_line(line)

    def _connect(self) -> None:
        port = self._configured_port or self._autodetect_port()
        if port is None:
            self._set_error("no Arduino port found (/dev/cu.usbmodem*, /dev/ttyACM*, /dev/ttyUSB*)")
            return

        try:
            fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            attrs[4] = self._baud_constant()
            attrs[5] = self._baud_constant()
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            self._fd = fd
            with self._lock:
                self._state.connected = True
                self._state.port = port
                self._state.last_error = None
            self.request_telemetry()
        except OSError as exc:
            self._set_error(str(exc), port=port)

    def _request(self, command: str, timeout_s: float = 0.75) -> dict[str, str]:
        with self._write_lock:
            return self._request_unlocked(command, timeout_s)

    def _request_unlocked(self, command: str, timeout_s: float = 0.75) -> dict[str, str]:
        sequence = self._allocate_sequence()
        with self._response_condition:
            self._pending_sequences.add(sequence)
        try:
            self._write_framed_command(sequence, command)
        except Exception:
            with self._response_condition:
                self._pending_sequences.discard(sequence)
            raise

        with self._response_condition:
            received = self._response_condition.wait_for(
                lambda: sequence in self._responses,
                timeout=timeout_s,
            )
            self._pending_sequences.discard(sequence)
            if not received:
                raise RuntimeError(f"Arduino did not acknowledge {command.split()[0]}")
            ok, values, line = self._responses.pop(sequence)
        if not ok:
            raise RuntimeError(values.get("code") or line)
        return values

    def _send_command(self, command: str) -> None:
        with self._write_lock:
            self._send_command_unlocked(command)

    def _send_command_unlocked(self, command: str) -> None:
        self._write_framed_command(self._allocate_sequence(), command)

    def _allocate_sequence(self) -> int:
        with self._lock:
            sequence = self._next_sequence
            self._next_sequence = 1 if sequence >= 0xFFFFFFFF else sequence + 1
        return sequence

    def _write_framed_command(self, sequence: int, command: str) -> None:
        if self._fd is None:
            raise RuntimeError("arduino serial port is not connected")
        data = memoryview(f"CMD {sequence} {command}\n".encode("ascii"))
        deadline_s = time.monotonic() + 0.75
        while data:
            try:
                written = os.write(self._fd, data)
            except BlockingIOError:
                if time.monotonic() >= deadline_s:
                    raise RuntimeError("Arduino serial write timed out")
                time.sleep(0.002)
                continue
            if written <= 0:
                raise RuntimeError("Arduino serial write failed")
            data = data[written:]

    def _handle_line(self, line: str) -> None:
        if line.startswith("ACK") or line.startswith("ERR"):
            self._handle_response(line)
            return
        if line.startswith("HB4 "):
            raw_values = line[4:].split()
            if len(raw_values) != len(COMPACT_HEARTBEAT_FIELDS):
                with self._lock:
                    self._state.last_error = "invalid HB4 field count"
                    self._state.raw_line = line
                return
            values = dict(zip(COMPACT_HEARTBEAT_FIELDS, raw_values, strict=True))
            values["v"] = "4"
            with self._response_condition:
                self._state.connected = True
                self._state.last_seen_s = time.monotonic()
                self._state.raw_line = line
                self._apply_compact_status(values)
                self._response_condition.notify_all()
            return
        if line.startswith("TEL4 "):
            raw_values = line[5:].split()
            if len(raw_values) != len(POSITIONAL_TELEMETRY_FIELDS):
                with self._lock:
                    self._state.last_error = "invalid TEL4 field count"
                    self._state.raw_line = line
                return
            values = dict(zip(POSITIONAL_TELEMETRY_FIELDS, raw_values, strict=True))
            values["protocol"] = "4"
            values["dac_range"] = "0to10" if values["dac_range"] == "1" else "neg10to0"
            values["run_mode"] = RUN_MODES.get(values["run_mode"], "stopped")
            values["controlled_stop_code"] = FAULT_CODES.get(
                values["controlled_stop_code"], "unknown"
            )
            values["fault_code"] = FAULT_CODES.get(values["fault_code"], "unknown")
            values["calibration_stage"] = CALIBRATION_STAGES.get(
                values["calibration_stage"], "unknown"
            )
            values["reset_cause"] = RESET_CAUSES.get(values["reset_cause"], "unknown")
            values["retained_last_fault"] = FAULT_CODES.get(
                values["retained_last_fault"], "unknown"
            )
        elif line.startswith("TEL "):
            values = {}
            for item in line[4:].split():
                if "=" in item:
                    key, value = item.split("=", 1)
                    values[key] = value
        else:
            with self._lock:
                self._state.raw_line = line
            return

        with self._lock:
            self._state.connected = True
            self._state.last_seen_s = time.monotonic()
            self._state.pin9 = self._parse_bool(values.get("pin9"))
            self._state.pin10 = self._parse_bool(values.get("pin10"))
            self._state.pin11 = self._parse_bool(values.get("pin11"))
            self._state.pin12 = self._parse_bool(values.get("pin12"))
            self._state.protocol_version = self._parse_int(values.get("protocol"), 0)
            self._state.pressure_latch_set = self._parse_bool(values.get("pressure_latch_set"))
            self._state.servo_ready = self._parse_bool(values.get("servo_ready"))
            self._state.arduino_enable = self._parse_bool(values.get("arduino_enable"))
            self._state.dac_ok = values.get("dac_ok") == "1"
            self._state.dac_mv = self._parse_int(values.get("dac_mv"), self._state.dac_mv)
            self._state.dac_data = self._parse_int(values.get("dac_data"), self._state.dac_data)
            self._state.dac_range = values.get("dac_range", self._state.dac_range)
            self._state.wave_loaded = values.get("wave_loaded") == "1"
            self._state.wave_running = values.get("wave_running") == "1"
            self._state.wave_samples = self._parse_int(
                values.get("wave_samples"), self._state.wave_samples
            )
            self._state.wave_rate_hz = self._parse_int(
                values.get("wave_rate_hz"), self._state.wave_rate_hz
            )
            self._state.wave_index = self._parse_int(
                values.get("wave_index"), self._state.wave_index
            )
            self._state.ramp_up_active = values.get("ramp_up_active") == "1"
            self._state.ramp_down_active = values.get("ramp_down_active") == "1"
            self._state.controlled_stop_active = values.get("controlled_stop_active") == "1"
            self._state.controlled_stop_code = values.get(
                "controlled_stop_code", self._state.controlled_stop_code
            )
            self._state.run_mode = values.get("run_mode", self._state.run_mode)
            self._state.trigger_active = values.get("trigger_active") == "1"
            self._state.trigger_high_us = self._parse_int(
                values.get("trigger_high_us"), self._state.trigger_high_us
            )
            self._state.trigger_count = self._parse_int(
                values.get("trigger_count"), self._state.trigger_count
            )
            self._state.fault_latched = values.get("fault_latched") == "1"
            self._state.fault_code = values.get("fault_code")
            self._state.timing_lateness_us = self._parse_int(
                values.get("timing_lateness_us"), self._state.timing_lateness_us
            )
            self._state.timing_max_lateness_us = self._parse_int(
                values.get("timing_max_lateness_us"), self._state.timing_max_lateness_us
            )
            self._state.timing_write_us = self._parse_int(
                values.get("timing_write_us"), self._state.timing_write_us
            )
            self._state.timing_max_write_us = self._parse_int(
                values.get("timing_max_write_us"), self._state.timing_max_write_us
            )
            self._state.timing_warning_count = self._parse_int(
                values.get("timing_warning_count"), self._state.timing_warning_count
            )
            self._state.timing_overrun_count = self._parse_int(
                values.get("timing_overrun_count"), self._state.timing_overrun_count
            )
            for name in (
                "ramp_update_count",
                "ramp_missed_deadline_count",
                "ramp_max_lateness_us",
                "serial_overflow_count",
                "protocol_error_count",
                "dac_write_failure_count",
                "heartbeat_age_ms",
                "heartbeat_max_gap_ms",
                "fault_clear_safe_ms",
                "calibration_target_mv",
                "calibration_valid_mask",
                "watchdog_timeout_ms",
                "reset_status1",
                "retained_fault_count",
            ):
                setattr(
                    self._state,
                    name,
                    self._parse_int(values.get(name), getattr(self._state, name)),
                )
            self._state.fault_clear_ready = values.get("fault_clear_ready") == "1"
            self._state.calibration_active = values.get("calibration_active") == "1"
            self._state.calibration_stage = values.get(
                "calibration_stage", self._state.calibration_stage
            )
            self._state.calibration_complete = values.get("calibration_complete") == "1"
            self._state.calibration_valid = values.get("calibration_valid") == "1"
            self._state.calibration_flows_ul_s = tuple(
                self._parse_int(
                    values.get(f"cal{index}_flow_ul_s"),
                    self._state.calibration_flows_ul_s[index],
                )
                for index in range(4)
            )
            self._state.watchdog_ok = values.get("watchdog_ok") == "1"
            self._state.reset_cause = values.get("reset_cause", self._state.reset_cause)
            self._state.retained_last_fault = values.get(
                "retained_last_fault", self._state.retained_last_fault
            )
            self._state.uptime_ms = self._parse_int(values.get("uptime_ms"), 0)
            self._state.last_error = None
            self._state.raw_line = line

    def _handle_response(self, line: str) -> None:
        values: dict[str, str] = {}
        for item in line.split()[1:]:
            if "=" in item:
                key, value = item.split("=", 1)
                values[key] = value

        sequence = self._parse_int(values.get("seq"), 0)
        with self._response_condition:
            self._state.connected = True
            self._state.last_seen_s = time.monotonic()
            self._state.raw_line = line
            self._apply_compact_status(values)
            if sequence in self._pending_sequences:
                self._responses[sequence] = (line.startswith("ACK"), values, line)
            self._response_condition.notify_all()

    def _apply_compact_status(self, values: dict[str, str]) -> None:
        if "v" in values:
            self._state.protocol_version = self._parse_int(values["v"], 0)
        mode = values.get("m") or values.get("run_mode")
        if mode in {"s", "stopped"}:
            self._state.run_mode = "stopped"
        elif mode in {"c", "constant"}:
            self._state.run_mode = "constant"
        elif mode in {"w", "waveform"}:
            self._state.run_mode = "waveform"
        elif mode in {"k", "calibration"}:
            self._state.run_mode = "calibration"

        if "f" in values:
            self._state.fault_latched = values["f"] == "1"
        if "s" in values:
            self._state.controlled_stop_active = values["s"] == "1"
        if "u" in values:
            self._state.ramp_up_active = values["u"] == "1"
        if "n" in values:
            self._state.ramp_down_active = values["n"] == "1"
        if "g" in values:
            self._state.calibration_stage = {
                "0": "idle",
                "1": "ramp_up",
                "2": "flat",
                "3": "ramp_down",
                "4": "complete",
            }.get(values["g"], self._state.calibration_stage)
        if "d" in values:
            self._state.dac_ok = values["d"] == "1"
        if "w" in values:
            self._state.wave_running = values["w"] == "1"
        if "e" in values:
            self._state.arduino_enable = values["e"] == "1"
        if "r" in values:
            self._state.servo_ready = values["r"] == "1"
        if "p" in values:
            self._state.pressure_latch_set = values["p"] == "1"
        if "a" in values:
            self._state.dac_mv = self._parse_int(values["a"], self._state.dac_mv)
        if "wave_running" in values:
            self._state.wave_running = values["wave_running"] == "1"
        if "wave_loaded" in values:
            self._state.wave_loaded = values["wave_loaded"] == "1"
        if "ramp_up_active" in values:
            self._state.ramp_up_active = values["ramp_up_active"] == "1"

    def _mark_disconnected(self, error: str) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._set_error(error)
        with self._response_condition:
            self._state.connected = False
            for sequence in self._pending_sequences:
                self._responses[sequence] = (
                    False,
                    {"code": "arduino_disconnected"},
                    "Arduino disconnected",
                )
            self._response_condition.notify_all()

    def _set_error(self, error: str, port: str | None = None) -> None:
        with self._lock:
            self._state.last_error = error
            if port is not None:
                self._state.port = port

    def _baud_constant(self) -> int:
        if self._baudrate != 115200:
            raise ValueError("only 115200 baud is currently supported")
        return termios.B115200

    @staticmethod
    def _autodetect_port() -> str | None:
        for pattern in ("/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*"):
            ports = sorted(glob.glob(pattern))
            if ports:
                return ports[-1]
        return None

    @staticmethod
    def _parse_bool(value: str | None) -> bool | None:
        if value == "0":
            return False
        if value == "1":
            return True
        return None

    @staticmethod
    def _parse_int(value: str | None, default: int) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default
