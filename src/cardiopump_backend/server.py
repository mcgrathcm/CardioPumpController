from __future__ import annotations

import argparse
import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from math import isfinite
from typing import Any

from .arduino import ArduinoSerialBridge
from .constants import (
    CONSTANT_FLOW_FULL_SCALE_ML_S,
    MAX_DAC_VOLTAGE,
    WAVEFORM_UPDATE_HZ,
    WAVEFORM_UPDATE_RATES,
)
from .curve import curve_from_json
from .simulator import PumpControllerSimulator
from .system_state import derive_system_state
from .waveform import WaveformProgram, program_from_text


class CardiopumpServer(ThreadingHTTPServer):
    arduino: ArduinoSerialBridge | None = None
    control_mode: str = "constant"
    flow_ml_s: float = 0.0
    waveform_program: WaveformProgram | None = None
    waveform_update_hz: int = WAVEFORM_UPDATE_HZ
    trigger_high_ms: float = 10.0

    def server_close(self) -> None:
        if self.arduino is not None:
            self.arduino.close()
        super().server_close()


class CardiopumpRequestHandler(BaseHTTPRequestHandler):
    controller: PumpControllerSimulator
    arduino: ArduinoSerialBridge | None

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_static("index.html")
            return

        if self.path in ("/app.css", "/app.js"):
            self._send_static(self.path.lstrip("/"))
            return

        if self.path == "/health":
            self._send_json({"ok": True})
            return

        if self.path == "/api/telemetry":
            self._send_json(self._telemetry(time.monotonic()))
            return

        self._send_error(404, "not found")

    def do_POST(self) -> None:
        now_s = time.monotonic()
        try:
            if self.path == "/api/heartbeat":
                self.controller.heartbeat(now_s)
                if self.arduino is not None:
                    self.arduino.heartbeat()
                self._send_json({"ok": True})
                return

            if self.path == "/api/curve":
                payload = self._read_json()
                curve = curve_from_json(payload.get("points"))
                self.controller.load_curve(curve, now_s)
                self._send_json({"ok": True, "curve": curve.to_json()})
                return

            if self.path == "/api/start":
                hardware = self._hardware_telemetry()
                self._require_start_ready(hardware)
                self.controller.set_simulated_interlocks(
                    overpressure_latched=bool(hardware.get("pressure_latch_set")),
                    parker_relay_closed=bool(hardware.get("servo_ready")),
                    now_s=now_s,
                )
                self.controller.set_constant_velocity(
                    (
                        self.server.flow_ml_s / CONSTANT_FLOW_FULL_SCALE_ML_S
                        if self.server.control_mode == "constant"
                        else 0.0
                    ),
                    now_s,
                )
                if self.arduino is not None:
                    self.arduino.set_trigger_high_us(round(self.server.trigger_high_ms * 1000))
                    if self.server.control_mode == "waveform":
                        if self.server.waveform_program is None:
                            raise RuntimeError("load a waveform before starting waveform mode")
                        self.arduino.load_waveform(
                            self.server.waveform_program.samples,
                            self.server.waveform_program.update_hz,
                        )
                        loaded = self.arduino.wait_for_wave_loaded(
                            len(self.server.waveform_program.samples)
                        )
                        self.arduino.request_telemetry()
                        if not loaded:
                            raise RuntimeError("Arduino did not confirm waveform load")
                        self.arduino.start_waveform()
                    else:
                        self.arduino.start_constant(
                            self.server.flow_ml_s
                            / CONSTANT_FLOW_FULL_SCALE_ML_S
                            * MAX_DAC_VOLTAGE
                        )
                self.controller.start(now_s)
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/stop":
                self.controller.stop(now_s)
                if self.arduino is not None:
                    self.arduino.stop_waveform()
                    if not self.arduino.wait_for_ramp_down():
                        raise RuntimeError("Arduino did not complete the stop ramp")
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/reset-overpressure":
                self.controller.reset_overpressure_latch(now_s)
                if self.arduino is not None:
                    self.arduino.reset_pressure_latch()
                    time.sleep(0.15)
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/clear-fault":
                if self.arduino is None:
                    raise RuntimeError("arduino serial bridge is disabled")
                self.arduino.clear_fault()
                self.arduino.request_telemetry()
                time.sleep(0.05)
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/hardware":
                payload = self._read_json()
                if self.arduino is None:
                    raise RuntimeError("arduino serial bridge is disabled")
                target_dac_mv = None
                if "dac_voltage" in payload:
                    target_dac_mv = self.arduino.set_voltage(float(payload["dac_voltage"]))
                if "arduino_enable" in payload:
                    arduino_enable = bool(payload["arduino_enable"])
                    if not arduino_enable:
                        self.controller.stop(now_s)
                        self.arduino.stop_waveform()
                        if not self.arduino.wait_for_ramp_down():
                            raise RuntimeError("Arduino did not complete the stop ramp")
                    self.arduino.set_arduino_enable(arduino_enable)
                if payload.get("reset_pressure_latch"):
                    self.arduino.reset_pressure_latch()
                self.arduino.request_telemetry()
                if target_dac_mv is not None:
                    self.arduino.wait_for_dac_mv(target_dac_mv)
                else:
                    time.sleep(0.05)
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/control":
                payload = self._read_json()
                if self.controller.telemetry(now_s)["running"]:
                    raise RuntimeError("stop pump before changing control settings")
                if "mode" in payload:
                    mode = str(payload["mode"])
                    if mode not in ("constant", "waveform"):
                        raise ValueError("control mode must be constant or waveform")
                    if mode == "waveform" and self.server.waveform_program is None:
                        raise RuntimeError("load a waveform before selecting waveform mode")
                    self.server.control_mode = mode
                if "flow_ml_s" in payload:
                    flow_ml_s = float(payload["flow_ml_s"])
                    if not isfinite(flow_ml_s) or not 0.0 <= flow_ml_s <= CONSTANT_FLOW_FULL_SCALE_ML_S:
                        raise ValueError(
                            "flow_ml_s must be between 0.0 and "
                            f"{CONSTANT_FLOW_FULL_SCALE_ML_S:g}"
                        )
                    self.server.flow_ml_s = flow_ml_s
                if "flow_fraction" in payload:
                    flow_fraction = float(payload["flow_fraction"])
                    if not -1.0 <= flow_fraction <= 1.0:
                        raise ValueError("flow_fraction must be between -1.0 and 1.0")
                    self.server.flow_ml_s = max(0.0, flow_fraction) * CONSTANT_FLOW_FULL_SCALE_ML_S
                if "waveform_update_hz" in payload:
                    update_hz = int(payload["waveform_update_hz"])
                    if update_hz not in WAVEFORM_UPDATE_RATES:
                        raise ValueError(
                            "waveform_update_hz must be one of "
                            + ", ".join(str(rate) for rate in WAVEFORM_UPDATE_RATES)
                        )
                    self.server.waveform_update_hz = update_hz
                    if self.server.waveform_program is not None:
                        self.server.waveform_program = self.server.waveform_program.with_update_hz(
                            update_hz
                        )
                if "waveform_scale" in payload:
                    if self.server.waveform_program is None:
                        raise RuntimeError("load a waveform before changing waveform scale")
                    self.server.waveform_program = self.server.waveform_program.with_scaling(
                        float(payload["waveform_scale"]),
                        self.server.waveform_program.period_scale,
                    )
                if payload.get("reset_waveform_scale"):
                    if self.server.waveform_program is None:
                        raise RuntimeError("load a waveform before resetting waveform scale")
                    self.server.waveform_program = self.server.waveform_program.reset_scaling()
                if "waveform_max_flow_ml_s" in payload or "waveform_period_ms" in payload:
                    if self.server.waveform_program is None:
                        raise RuntimeError("load a waveform before changing waveform settings")
                    max_flow_ml_s = float(
                        payload.get(
                            "waveform_max_flow_ml_s",
                            self.server.waveform_program.max_flow_ml_s,
                        )
                    )
                    period_ms = float(
                        payload.get("waveform_period_ms", self.server.waveform_program.period_ms)
                    )
                    self.server.waveform_program = self.server.waveform_program.with_settings(
                        max_flow_ml_s,
                        period_ms,
                    )
                if payload.get("reset_waveform_scaling"):
                    if self.server.waveform_program is None:
                        raise RuntimeError("load a waveform before resetting waveform settings")
                    self.server.waveform_program = self.server.waveform_program.reset_scaling()
                if "clip_negative_flow" in payload:
                    if self.server.waveform_program is None:
                        raise RuntimeError("load a waveform before changing negative-flow clipping")
                    clip_negative_flow = payload["clip_negative_flow"]
                    if not isinstance(clip_negative_flow, bool):
                        raise ValueError("clip_negative_flow must be boolean")
                    self.server.waveform_program = self.server.waveform_program.with_clip_negative_flow(
                        clip_negative_flow
                    )
                if "trigger_high_ms" in payload:
                    trigger_high_ms = float(payload["trigger_high_ms"])
                    if not isfinite(trigger_high_ms) or not 0.1 <= trigger_high_ms <= 1000.0:
                        raise ValueError("trigger high time must be between 0.1 and 1000 ms")
                    self.server.trigger_high_ms = trigger_high_ms
                    if self.arduino is not None:
                        self.arduino.set_trigger_high_us(round(trigger_high_ms * 1000))
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/waveform":
                if self.controller.telemetry(now_s)["running"]:
                    raise RuntimeError("stop pump before loading a waveform")
                payload = self._read_json()
                text = payload.get("text")
                if not isinstance(text, str):
                    raise ValueError("waveform text is required")
                program = program_from_text(text, update_hz=self.server.waveform_update_hz)
                self.server.waveform_program = program
                self.server.control_mode = "waveform"
                self._send_json({"ok": True, "waveform": program.to_json(), "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/simulator/interlocks":
                payload = self._read_json()
                self.controller.set_simulated_interlocks(
                    overpressure_latched=payload.get("overpressure_latched"),
                    parker_relay_closed=payload.get("parker_relay_closed"),
                    now_s=now_s,
                )
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

        except (RuntimeError, ValueError) as exc:
            self._send_error(400, str(exc))
            return

        self._send_error(404, "not found")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if not body:
            return {}
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status=status)

    def _telemetry(self, now_s: float) -> dict[str, object]:
        telemetry = self.controller.telemetry(now_s)
        hardware = self._hardware_telemetry()
        telemetry["hardware"] = hardware
        telemetry["control"] = {
            "mode": self.server.control_mode,
            "flow_ml_s": self.server.flow_ml_s,
            "flow_fraction": self.server.flow_ml_s / CONSTANT_FLOW_FULL_SCALE_ML_S,
            "trigger_high_ms": self.server.trigger_high_ms,
            "waveform_update_hz": self.server.waveform_update_hz,
            "waveform_update_rates": list(WAVEFORM_UPDATE_RATES),
            "waveform_available": self.server.waveform_program is not None,
            "waveform": self.server.waveform_program.to_json()
            if self.server.waveform_program is not None
            else None,
        }
        telemetry["system"] = derive_system_state(
            hardware,
            running=bool(telemetry.get("running")),
            communication_ok=bool(telemetry.get("communication_ok")),
        )
        if hardware.get("connected"):
            telemetry["interlocks"]["overpressure_latched"] = bool(hardware.get("pressure_latch_set"))
            telemetry["interlocks"]["parker_relay_closed"] = bool(hardware.get("servo_ready"))
            telemetry["interlocks"]["arduino_relay_closed"] = bool(hardware.get("arduino_enable"))
        return telemetry

    def _hardware_telemetry(self) -> dict[str, object]:
        hardware = (
            self.arduino.telemetry()
            if self.arduino is not None
            else {"connected": False, "last_error": "arduino serial bridge is disabled"}
        )
        return hardware

    def _require_start_ready(self, hardware: dict[str, object]) -> None:
        if not hardware.get("connected"):
            raise RuntimeError("cannot start while Arduino is disconnected")
        if hardware.get("pressure_latch_set"):
            raise RuntimeError("cannot start while overpressure latch is active")
        if not hardware.get("arduino_enable"):
            raise RuntimeError("cannot start until Arduino Enable is on")
        if not hardware.get("servo_ready"):
            raise RuntimeError("cannot start until Servo OK is true")

    def _send_static(self, name: str) -> None:
        path = files("cardiopump_backend").joinpath("web", name)
        body = path.read_bytes()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def build_server(
    host: str, port: int, serial_port: str | None = None, hardware: bool = True
) -> ThreadingHTTPServer:
    handler = type(
        "BoundCardiopumpRequestHandler",
        (CardiopumpRequestHandler,),
        {
            "controller": PumpControllerSimulator(time.monotonic()),
            "arduino": ArduinoSerialBridge(serial_port) if hardware else None,
        },
    )
    server = CardiopumpServer((host, port), handler)
    server.arduino = handler.arduino
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--serial-port")
    parser.add_argument("--no-hardware", action="store_true")
    args = parser.parse_args()

    server = build_server(args.host, args.port, args.serial_port, not args.no_hardware)
    print(f"cardiopump backend listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
