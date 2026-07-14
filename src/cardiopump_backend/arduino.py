from __future__ import annotations

import glob
import os
import termios
import threading
import time
import tty
from dataclasses import dataclass, field


@dataclass
class ArduinoState:
    connected: bool = False
    port: str | None = None
    last_seen_s: float | None = None
    pin9: bool | None = None
    pin10: bool | None = None
    pin11: bool | None = None
    pin12: bool | None = None
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
    ramp_down_active: bool = False
    run_mode: str = "stopped"
    trigger_active: bool = False
    trigger_high_us: int = 10000
    trigger_count: int = 0
    fault_latched: bool = False
    fault_code: str | None = None
    uptime_ms: int = 0
    last_error: str | None = None
    raw_line: str | None = None


class ArduinoSerialBridge:
    def __init__(self, port: str | None = None, baudrate: int = 115200):
        self._configured_port = port
        self._baudrate = baudrate
        self._fd: int | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._state = ArduinoState(port=port)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def telemetry(self) -> dict[str, object]:
        with self._lock:
            state = ArduinoState(**self._state.__dict__)

        last_seen_ms = None
        if state.last_seen_s is not None:
            last_seen_ms = round((time.monotonic() - state.last_seen_s) * 1000)

        return {
            "connected": state.connected,
            "port": state.port,
            "last_seen_ms": last_seen_ms,
            "pin9_pressure_latch_input": state.pin9,
            "pin10_servo_ready_input": state.pin10,
            "pin11_arduino_enable_output": state.pin11,
            "pin12_latch_reset_output": state.pin12,
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
            "ramp_down_active": state.ramp_down_active,
            "run_mode": state.run_mode,
            "trigger_active": state.trigger_active,
            "trigger_high_us": state.trigger_high_us,
            "trigger_high_ms": state.trigger_high_us / 1000,
            "trigger_count": state.trigger_count,
            "fault_latched": state.fault_latched,
            "fault_code": state.fault_code,
            "uptime_ms": state.uptime_ms,
            "last_error": state.last_error,
            "raw_line": state.raw_line,
        }

    def set_voltage(self, voltage: float) -> int:
        if not -10.0 <= voltage <= 10.0:
            raise ValueError("dac voltage must be between -10.0 and 10.0")
        millivolts = round(voltage * 1000)
        self._write_command(f"VOLT {millivolts}")
        return millivolts

    def set_trigger_high_us(self, high_us: int) -> None:
        if not 100 <= high_us <= 1_000_000:
            raise ValueError("trigger high time must be between 0.1 ms and 1000 ms")
        self._write_command(f"TRIGGER_US {high_us}")

    def start_constant(self, voltage: float) -> None:
        if not -10.0 <= voltage <= 10.0:
            raise ValueError("constant voltage must be between -10.0 and 10.0")
        self._write_command(f"START_CONSTANT {round(voltage * 1000)}")

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
        self._write_command(f"ENABLE {1 if enabled else 0}")

    def heartbeat(self) -> None:
        self._write_command("HEARTBEAT")

    def reset_pressure_latch(self) -> None:
        self._write_command("RESET_LATCH")

    def clear_fault(self) -> None:
        self._write_command("CLEAR_FAULT")

    def request_telemetry(self) -> None:
        self._write_command("GET")

    def load_waveform(self, samples: tuple[int, ...], update_hz: int) -> None:
        if not samples:
            raise ValueError("waveform must contain at least one sample")
        checksum = sum(samples)
        with self._write_lock:
            self._write_command_unlocked(f"WAVE_BEGIN {update_hz} {len(samples)} {checksum}")
            chunk_size = 12
            for offset in range(0, len(samples), chunk_size):
                chunk = samples[offset : offset + chunk_size]
                values = " ".join(str(sample) for sample in chunk)
                self._write_command_unlocked(f"WAVE_DATA {offset} {values}")
            self._write_command_unlocked("WAVE_END")

    def start_waveform(self) -> None:
        self._write_command("START_WAVE")

    def stop_waveform(self) -> None:
        self._write_command("STOP")

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
            self._set_error("no /dev/cu.usbmodem* port found")
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

    def _write_command(self, command: str) -> None:
        with self._write_lock:
            self._write_command_unlocked(command)

    def _write_command_unlocked(self, command: str) -> None:
        if self._fd is None:
            raise RuntimeError("arduino serial port is not connected")
        os.write(self._fd, f"{command}\n".encode("ascii"))

    def _handle_line(self, line: str) -> None:
        if not line.startswith("TEL "):
            with self._lock:
                self._state.raw_line = line
            return

        values: dict[str, str] = {}
        for item in line[4:].split():
            if "=" in item:
                key, value = item.split("=", 1)
                values[key] = value

        with self._lock:
            self._state.connected = True
            self._state.last_seen_s = time.monotonic()
            self._state.pin9 = self._parse_bool(values.get("pin9"))
            self._state.pin10 = self._parse_bool(values.get("pin10"))
            self._state.pin11 = self._parse_bool(values.get("pin11"))
            self._state.pin12 = self._parse_bool(values.get("pin12"))
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
            self._state.ramp_down_active = values.get("ramp_down_active") == "1"
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
            self._state.uptime_ms = self._parse_int(values.get("uptime_ms"), 0)
            self._state.last_error = None
            self._state.raw_line = line

    def _mark_disconnected(self, error: str) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._set_error(error)
        with self._lock:
            self._state.connected = False

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
        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
        return ports[-1] if ports else None

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
