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
    WAVEFORM_UPDATE_HZ,
    WAVEFORM_UPDATE_RATES,
)
from .curve import curve_from_json
from .simulator import PumpControllerSimulator
from .system_state import derive_system_state
from .waveform import (
    WaveformProgram,
    WaveformPoint,
    constant_control_point_program,
    control_point_fit_from_points,
    control_point_program_from_points,
    curve_designer_checkpoint_from_text,
    curve_designer_checkpoint_text,
    curve_designer_csv_text,
    insert_control_point,
    move_control_point,
    program_from_text,
    program_from_control_points,
    remove_control_point,
    round_to_raster_ms,
)


class CardiopumpServer(ThreadingHTTPServer):
    arduino: ArduinoSerialBridge | None = None
    control_mode: str = "constant"
    flow_ml_s: float = 0.0
    waveform_program: WaveformProgram | None = None
    waveform_source: str | None = None
    pulsatile_waveform_program: WaveformProgram | None = None
    curve_designer_waveform_program: WaveformProgram | None = None
    curve_designer_fit: dict[str, object] | None = None
    curve_designer_source_program: WaveformProgram | None = None
    curve_designer_reference_program: WaveformProgram | None = None
    curve_designer_baseline_program: WaveformProgram | None = None
    curve_designer_baseline_locked_times_ms: tuple[float, ...] = ()
    curve_designer_locked_times_ms: tuple[float, ...] = ()
    curve_designer_modified: bool = False
    waveform_update_hz: int = WAVEFORM_UPDATE_HZ
    trigger_high_ms: float = round_to_raster_ms(10.0, WAVEFORM_UPDATE_HZ)
    loaded_calibration_flows_ml_s: tuple[float, float, float, float] | None = None
    calibration_pending_transfer: bool = False

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

        if self.path in ("/app.css", "/app.js", "/calibration.js"):
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
                hardware = self._sync_loaded_calibration(hardware)
                max_flow_ml_s = self._max_flow_ml_s(hardware)
                self.controller.set_simulated_interlocks(
                    overpressure_latched=bool(hardware.get("pressure_latch_set")),
                    parker_relay_closed=bool(hardware.get("servo_ready")),
                    now_s=now_s,
                )
                self.controller.set_constant_velocity(
                    (
                        self.server.flow_ml_s / max_flow_ml_s
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
                        samples = self.server.waveform_program.samples_with_flow_limit(
                            max_flow_ml_s
                        )
                        self.arduino.load_waveform(
                            samples,
                            self.server.waveform_program.update_hz,
                            self.server.waveform_program.applied_max_flow_ml_s(max_flow_ml_s),
                        )
                        loaded = self.arduino.wait_for_wave_loaded(
                            len(samples)
                        )
                        self.arduino.request_telemetry()
                        if not loaded:
                            raise RuntimeError("Arduino did not confirm waveform load")
                        self.arduino.start_waveform()
                    else:
                        self.arduino.start_constant(
                            self.server.flow_ml_s
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

            if self.path == "/api/calibration/start":
                if self.arduino is None:
                    raise RuntimeError("arduino serial bridge is disabled")
                payload = self._read_json()
                voltage_mv = round(float(payload.get("voltage", 0)) * 1000)
                self._require_start_ready(self._hardware_telemetry())
                self.arduino.start_calibration(voltage_mv)
                self.server.loaded_calibration_flows_ml_s = None
                self.server.calibration_pending_transfer = False
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/calibration/result":
                if self.arduino is None:
                    raise RuntimeError("arduino serial bridge is disabled")
                payload = self._read_json()
                voltage_mv = round(float(payload.get("voltage", 0)) * 1000)
                if "mean_flow_ml_s" in payload:
                    flow_ml_s = float(payload["mean_flow_ml_s"])
                elif "volume_ml" in payload:
                    flow_ml_s = float(payload["volume_ml"]) / 60.0
                else:
                    raise ValueError("mean flow or volume is required")
                if not isfinite(flow_ml_s) or flow_ml_s <= 0.0:
                    raise ValueError("calibration measurement must be positive")
                self.arduino.save_calibration_result(voltage_mv, flow_ml_s)
                self.arduino.request_telemetry()
                time.sleep(0.05)
                self.server.flow_ml_s = min(
                    self.server.flow_ml_s,
                    self._max_flow_ml_s(self._hardware_telemetry()),
                )
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/calibration/clear":
                hardware = self._hardware_telemetry()
                if (
                    self.arduino is not None
                    and hardware.get("connected")
                    and hardware.get("fresh")
                ):
                    self.arduino.clear_calibration()
                    self.arduino.request_telemetry()
                    time.sleep(0.05)
                elif not self.server.calibration_pending_transfer:
                    raise RuntimeError("cannot clear Arduino calibration while disconnected")
                self.server.loaded_calibration_flows_ml_s = None
                self.server.calibration_pending_transfer = False
                self.server.flow_ml_s = min(
                    self.server.flow_ml_s,
                    CONSTANT_FLOW_FULL_SCALE_ML_S,
                )
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/calibration/load":
                payload = self._read_json()
                flows = payload.get("flows_ml_s")
                if not isinstance(flows, list) or len(flows) != 4:
                    raise ValueError("flows_ml_s must contain four calibration values")
                calibration = tuple(float(value) for value in flows)
                if any(not isfinite(flow) or flow <= 0.0 for flow in calibration):
                    raise ValueError("calibration requires four positive flow values")
                if any(
                    right <= left
                    for left, right in zip(calibration, calibration[1:])
                ):
                    raise ValueError("calibration flow values must be strictly increasing")
                self.server.loaded_calibration_flows_ml_s = calibration
                self.server.calibration_pending_transfer = True
                self.server.flow_ml_s = min(self.server.flow_ml_s, calibration[-1])
                self._sync_loaded_calibration(self._hardware_telemetry())
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
                if "waveform_source" in payload:
                    self._activate_waveform_source(str(payload["waveform_source"]))
                if "mode" in payload:
                    mode = str(payload["mode"])
                    if mode not in ("constant", "waveform"):
                        raise ValueError("control mode must be constant or waveform")
                    if mode == "waveform" and self.server.waveform_program is None:
                        raise RuntimeError("load a waveform before selecting waveform mode")
                    self.server.control_mode = mode
                if "flow_ml_s" in payload:
                    flow_ml_s = float(payload["flow_ml_s"])
                    max_flow_ml_s = self._max_flow_ml_s(self._hardware_telemetry())
                    if not isfinite(flow_ml_s) or not 0.0 <= flow_ml_s <= max_flow_ml_s:
                        raise ValueError(
                            "flow_ml_s must be between 0.0 and "
                            f"{max_flow_ml_s:g}"
                        )
                    self.server.flow_ml_s = flow_ml_s
                if "flow_fraction" in payload:
                    flow_fraction = float(payload["flow_fraction"])
                    if not -1.0 <= flow_fraction <= 1.0:
                        raise ValueError("flow_fraction must be between -1.0 and 1.0")
                    self.server.flow_ml_s = max(0.0, flow_fraction) * self._max_flow_ml_s(
                        self._hardware_telemetry()
                    )
                if "waveform_update_hz" in payload:
                    update_hz = int(payload["waveform_update_hz"])
                    if update_hz not in WAVEFORM_UPDATE_RATES:
                        raise ValueError(
                            "waveform_update_hz must be one of "
                            + ", ".join(str(rate) for rate in WAVEFORM_UPDATE_RATES)
                        )
                    self.server.waveform_update_hz = update_hz
                    self.server.trigger_high_ms = round_to_raster_ms(
                        self.server.trigger_high_ms,
                        update_hz,
                    )
                    if self.arduino is not None:
                        self.arduino.set_trigger_high_us(
                            round(self.server.trigger_high_ms * 1000)
                        )
                    if self.server.pulsatile_waveform_program is not None:
                        self.server.pulsatile_waveform_program = (
                            self.server.pulsatile_waveform_program.with_update_hz(update_hz)
                        )
                    if self.server.curve_designer_waveform_program is not None:
                        self.server.curve_designer_waveform_program = (
                            self.server.curve_designer_waveform_program.with_update_hz(update_hz)
                        )
                    if self.server.waveform_source == "pulsatile":
                        self.server.waveform_program = self.server.pulsatile_waveform_program
                    elif self.server.waveform_source == "curve_designer":
                        self.server.waveform_program = self.server.curve_designer_waveform_program
                    if self.server.curve_designer_source_program is not None:
                        self.server.curve_designer_source_program = (
                            self.server.curve_designer_source_program.with_update_hz(update_hz)
                        )
                    if self.server.curve_designer_reference_program is not None:
                        self.server.curve_designer_reference_program = (
                            self.server.curve_designer_reference_program.with_update_hz(update_hz)
                        )
                    if self.server.curve_designer_baseline_program is not None:
                        self.server.curve_designer_baseline_program = (
                            self.server.curve_designer_baseline_program.with_update_hz(update_hz)
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
                    if (
                        self.server.waveform_source == "curve_designer"
                        and self.server.curve_designer_fit is not None
                        and self.server.curve_designer_fit["source"] == "constant"
                        and self.server.waveform_program.source_peak_flow_ml_s == 0.0
                        and max_flow_ml_s != 0.0
                    ):
                        self.server.waveform_program.with_settings(
                            self.server.waveform_program.max_flow_ml_s,
                            period_ms,
                        )
                        waveform_program = program_from_control_points(
                            tuple(
                                WaveformPoint(point.time_ms, max_flow_ml_s)
                                for point in self.server.waveform_program.points
                            ),
                            interpolation=self.server.waveform_program.interpolation,
                            update_hz=self.server.waveform_update_hz,
                        ).with_settings(max_flow_ml_s, period_ms)
                    else:
                        waveform_program = self.server.waveform_program.with_settings(
                            max_flow_ml_s,
                            period_ms,
                        )
                    self.server.waveform_program = waveform_program
                    if self.server.waveform_source == "curve_designer":
                        self._update_curve_fit_metrics()
                        self.server.curve_designer_modified = True
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
                    self.server.trigger_high_ms = round_to_raster_ms(
                        trigger_high_ms,
                        self.server.waveform_update_hz,
                    )
                    if self.arduino is not None:
                        self.arduino.set_trigger_high_us(
                            round(self.server.trigger_high_ms * 1000)
                        )
                self._cache_active_waveform()
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
                self.server.waveform_source = "pulsatile"
                self.server.pulsatile_waveform_program = program
                self.server.control_mode = "waveform"
                self._send_json({"ok": True, "waveform": program.to_json(), "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/curve-designer":
                if self.controller.telemetry(now_s)["running"]:
                    raise RuntimeError("stop pump before initializing the curve designer")
                payload = self._read_json()
                text = payload.get("text")
                if text is None:
                    flow_ml_s = float(
                        payload.get("constant_flow_ml_s", self.server.flow_ml_s)
                    )
                    program = constant_control_point_program(
                        flow_ml_s,
                        update_hz=self.server.waveform_update_hz,
                    )
                    fit_metadata: dict[str, object] = {
                        "source": "constant",
                        "source_point_count": 2,
                        "max_error_ml_s": 0.0,
                        "rmse_ml_s": 0.0,
                    }
                    source_program = None
                    reference_program = None
                    locked_times_ms: tuple[float, ...] = ()
                else:
                    if not isinstance(text, str):
                        raise ValueError("curve text is required")
                    checkpoint = curve_designer_checkpoint_from_text(
                        text,
                        update_hz=self.server.waveform_update_hz,
                    )
                    if checkpoint is not None:
                        program, locked_times_ms = checkpoint
                        reference_program = program
                        source_program = program_from_text(
                            curve_designer_csv_text(program),
                            update_hz=self.server.waveform_update_hz,
                        )
                        fit_metadata = {
                            "source": "checkpoint",
                            "source_point_count": len(program.points),
                            "max_error_ml_s": 0.0,
                            "rmse_ml_s": 0.0,
                            "initial_control_point_count": len(program.points),
                        }
                    else:
                        control_point_count = payload.get("control_point_count")
                        source_program = program_from_text(
                            text,
                            update_hz=self.server.waveform_update_hz,
                        )
                        reference_program = source_program
                        fit = control_point_program_from_points(
                            source_program.points,
                            control_point_count,
                            update_hz=self.server.waveform_update_hz,
                        )
                        program = fit.program.with_scaling(
                            source_program.flow_scale,
                            source_program.period_scale,
                        )
                        fit_metadata = {
                            "source": "file",
                            "source_point_count": fit.source_point_count,
                            "max_error_ml_s": fit.max_error_ml_s * source_program.flow_scale,
                            "rmse_ml_s": fit.rmse_ml_s * source_program.flow_scale,
                            "initial_control_point_count": control_point_count,
                        }
                        locked_times_ms = ()
                self.server.waveform_program = program
                self.server.waveform_source = "curve_designer"
                self.server.curve_designer_waveform_program = program
                self.server.curve_designer_fit = fit_metadata
                self.server.curve_designer_source_program = source_program
                self.server.curve_designer_reference_program = reference_program
                self.server.curve_designer_baseline_program = program
                self.server.curve_designer_baseline_locked_times_ms = locked_times_ms
                self.server.curve_designer_locked_times_ms = locked_times_ms
                self.server.curve_designer_modified = False
                self.server.control_mode = "waveform"
                self._send_json(
                    {
                        "ok": True,
                        "waveform": program.to_json(),
                        "telemetry": self._telemetry(now_s),
                    }
                )
                return

            if self.path == "/api/curve-designer/export":
                if (
                    self.server.waveform_source != "curve_designer"
                    or self.server.waveform_program is None
                ):
                    raise RuntimeError("initialize the curve designer before saving")
                payload = self._read_json()
                export_format = payload.get("format")
                if export_format == "csv":
                    text = curve_designer_csv_text(self.server.waveform_program)
                elif export_format == "checkpoint":
                    text = curve_designer_checkpoint_text(
                        self.server.waveform_program,
                        self.server.curve_designer_locked_times_ms,
                    )
                else:
                    raise ValueError("Curve Designer export format must be csv or checkpoint")
                self._send_json({"ok": True, "text": text})
                return

            if self.path == "/api/curve-designer/refit":
                if self.controller.telemetry(now_s)["running"]:
                    raise RuntimeError("stop pump before refitting the curve")
                if (
                    self.server.waveform_source != "curve_designer"
                    or self.server.curve_designer_fit is None
                    or self.server.curve_designer_source_program is None
                ):
                    raise RuntimeError("load a curve file before refitting")
                payload = self._read_json()
                reset = payload.get("reset") is True
                if self.server.curve_designer_modified and not reset:
                    raise RuntimeError("reset the curve before changing the point count")
                if reset:
                    program = self.server.curve_designer_baseline_program
                    if program is None:
                        raise RuntimeError("Curve Designer reset baseline is unavailable")
                    self.server.waveform_program = program
                    self.server.curve_designer_locked_times_ms = (
                        self.server.curve_designer_baseline_locked_times_ms
                    )
                    self._update_curve_fit_metrics()
                else:
                    control_point_count = payload.get("control_point_count")
                    source_program = self.server.curve_designer_source_program
                    selected_fit = control_point_program_from_points(
                        source_program.points,
                        control_point_count,
                        update_hz=self.server.waveform_update_hz,
                    )
                    baseline = self.server.curve_designer_baseline_program
                    interpolation = baseline.interpolation if baseline is not None else "pchip"
                    fit = control_point_fit_from_points(
                        source_program.points,
                        selected_fit.program.points,
                        interpolation=interpolation,
                        update_hz=self.server.waveform_update_hz,
                    )
                    program = fit.program.with_scaling(
                        source_program.flow_scale,
                        source_program.period_scale,
                    )
                    source = self.server.curve_designer_fit["source"]
                    self.server.waveform_program = program
                    self.server.curve_designer_fit = {
                        "source": source,
                        "source_point_count": fit.source_point_count,
                        "max_error_ml_s": fit.max_error_ml_s * source_program.flow_scale,
                        "rmse_ml_s": fit.rmse_ml_s * source_program.flow_scale,
                        "initial_control_point_count": control_point_count,
                    }
                    self.server.curve_designer_baseline_program = program
                    self.server.curve_designer_baseline_locked_times_ms = ()
                    self.server.curve_designer_locked_times_ms = ()
                self.server.curve_designer_modified = False
                self.server.curve_designer_waveform_program = self.server.waveform_program
                self.server.control_mode = "waveform"
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/curve-designer/interpolation":
                if self.controller.telemetry(now_s)["running"]:
                    raise RuntimeError("stop pump before changing curve interpolation")
                if (
                    self.server.waveform_source != "curve_designer"
                    or self.server.waveform_program is None
                ):
                    raise RuntimeError("initialize the curve designer before changing interpolation")
                payload = self._read_json()
                interpolation = payload.get("interpolation")
                if not isinstance(interpolation, str):
                    raise ValueError("interpolation must be a string")
                self.server.waveform_program = (
                    self.server.waveform_program.with_interpolation(interpolation)
                )
                self.server.curve_designer_waveform_program = self.server.waveform_program
                self._update_curve_fit_metrics()
                self.server.curve_designer_modified = True
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
                return

            if self.path == "/api/curve-designer/control-point":
                if self.controller.telemetry(now_s)["running"]:
                    raise RuntimeError("stop pump before editing control points")
                if (
                    self.server.waveform_source != "curve_designer"
                    or self.server.waveform_program is None
                    or self.server.curve_designer_fit is None
                ):
                    raise RuntimeError("initialize the curve designer before editing points")
                payload = self._read_json()
                action = payload.get("action")
                time_value = payload.get("time_ms")
                if isinstance(time_value, bool) or not isinstance(time_value, (int, float)):
                    raise ValueError("control point time must be numeric")
                time_ms = float(time_value)
                program = self.server.waveform_program
                old_period_scale = program.period_scale
                locked_times = set(self.server.curve_designer_locked_times_ms)
                if action == "add":
                    program = insert_control_point(program, time_ms)
                    self.server.curve_designer_locked_times_ms = tuple(
                        sorted(point_time * old_period_scale for point_time in locked_times)
                    )
                else:
                    point_index = self._curve_control_point_index(time_ms)
                    point_time_ms = program.points[point_index].time_ms
                    if action == "toggle_lock":
                        linked_times = {point_time_ms}
                        if point_index in (0, len(program.points) - 1):
                            linked_times = {
                                program.points[0].time_ms,
                                program.points[-1].time_ms,
                            }
                        if linked_times <= locked_times:
                            locked_times.difference_update(linked_times)
                        else:
                            locked_times.update(linked_times)
                        self.server.curve_designer_locked_times_ms = tuple(
                            sorted(locked_times)
                        )
                    elif action == "remove":
                        program = remove_control_point(program, point_index)
                        locked_times.discard(point_time_ms)
                        self.server.curve_designer_locked_times_ms = tuple(
                            sorted(
                                locked_time * old_period_scale
                                for locked_time in locked_times
                            )
                        )
                    elif action == "move":
                        if point_time_ms in locked_times:
                            raise ValueError("unlock the control point before moving it")
                        moved_time = payload.get("new_time_ms")
                        moved_flow = payload.get("new_flow_ml_s")
                        if (
                            isinstance(moved_time, bool)
                            or not isinstance(moved_time, (int, float))
                            or isinstance(moved_flow, bool)
                            or not isinstance(moved_flow, (int, float))
                        ):
                            raise ValueError("moved control point time and flow must be numeric")
                        program = move_control_point(
                            program,
                            point_index,
                            float(moved_time),
                            float(moved_flow),
                        )
                        self.server.curve_designer_locked_times_ms = tuple(
                            sorted(
                                locked_time * old_period_scale
                                for locked_time in locked_times
                            )
                        )
                    else:
                        raise ValueError(
                            "control point action must be add, move, toggle_lock, or remove"
                        )
                self.server.waveform_program = program
                self.server.curve_designer_waveform_program = program
                if action in ("add", "move", "remove"):
                    self._update_curve_fit_metrics()
                self.server.curve_designer_modified = True
                self._send_json({"ok": True, "telemetry": self._telemetry(now_s)})
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

    def _curve_control_point_index(self, time_ms: float) -> int:
        program = self.server.waveform_program
        if program is None or not isfinite(time_ms):
            raise ValueError("control point time must be finite")
        source_time_ms = time_ms / program.period_scale
        point_index = min(
            range(len(program.points)),
            key=lambda index: abs(program.points[index].time_ms - source_time_ms),
        )
        tolerance_ms = max(1e-6, program.source_duration_ms * 1e-6)
        if abs(program.points[point_index].time_ms - source_time_ms) > tolerance_ms:
            raise ValueError("control point not found")
        return point_index

    def _activate_waveform_source(self, source: str) -> None:
        programs = {
            "pulsatile": self.server.pulsatile_waveform_program,
            "curve_designer": self.server.curve_designer_waveform_program,
        }
        if source not in programs:
            raise ValueError("waveform source must be pulsatile or curve_designer")
        program = programs[source]
        if program is None:
            label = "pulsatile waveform" if source == "pulsatile" else "Curve Designer curve"
            raise RuntimeError(f"load a {label} before selecting that mode")
        self.server.waveform_source = source
        self.server.waveform_program = program

    def _cache_active_waveform(self) -> None:
        if self.server.waveform_source == "pulsatile":
            self.server.pulsatile_waveform_program = self.server.waveform_program
        elif self.server.waveform_source == "curve_designer":
            self.server.curve_designer_waveform_program = self.server.waveform_program

    def _update_curve_fit_metrics(self) -> None:
        source_program = self.server.curve_designer_source_program
        program = self.server.waveform_program
        fit_metadata = self.server.curve_designer_fit
        if source_program is None or program is None or fit_metadata is None:
            return
        source_flow_scale = (
            program.max_flow_ml_s / source_program.source_peak_flow_ml_s
            if source_program.source_peak_flow_ml_s > 0.0
            else 1.0
        )
        source_period_scale = program.period_ms / source_program.source_duration_ms
        displayed_source_points = tuple(
            WaveformPoint(
                point.time_ms * source_period_scale,
                point.flow_ml_s * source_flow_scale,
            )
            for point in source_program.points
        )
        displayed_control_points = tuple(
            WaveformPoint(
                point.time_ms * program.period_scale,
                point.flow_ml_s * program.flow_scale,
            )
            for point in program.points
        )
        fit = control_point_fit_from_points(
            displayed_source_points,
            displayed_control_points,
            interpolation=program.interpolation,
            update_hz=self.server.waveform_update_hz,
        )
        fit_metadata.update(
            source_point_count=fit.source_point_count,
            max_error_ml_s=fit.max_error_ml_s,
            rmse_ml_s=fit.rmse_ml_s,
        )

    def _telemetry(self, now_s: float) -> dict[str, object]:
        hardware = self._hardware_telemetry()
        max_flow_ml_s = self._max_flow_ml_s(hardware)
        curve_designer = None
        if (
            self.server.waveform_source == "curve_designer"
            and self.server.curve_designer_fit is not None
        ):
            curve_designer = dict(self.server.curve_designer_fit)
            curve_designer["modified"] = self.server.curve_designer_modified
            reference_program = self.server.curve_designer_reference_program
            if reference_program is not None:
                source_flow_scale = (
                    self.server.waveform_program.max_flow_ml_s
                    / reference_program.source_peak_flow_ml_s
                    if reference_program.source_peak_flow_ml_s > 0.0
                    else 1.0
                )
                source_period_scale = (
                    self.server.waveform_program.period_ms
                    / reference_program.source_duration_ms
                )
                original_program = reference_program.with_scaling(
                    source_flow_scale,
                    source_period_scale,
                )
                original_json = original_program.to_json()
                curve_designer["original_preview"] = original_json["preview"]
                curve_designer["original_stroke_volume_ml"] = original_json[
                    "total_stroke_volume_ml"
                ]
        if hardware.get("connected") and hardware.get("fresh") and (
            hardware.get("fault_latched") or hardware.get("controlled_stop_active")
        ):
            self.controller.stop(now_s)
        telemetry = self.controller.telemetry(now_s)
        hardware_running = bool(
            hardware.get("fresh")
            and hardware.get("run_mode") in ("constant", "waveform", "calibration")
        )
        telemetry["running"] = bool(telemetry.get("running")) or hardware_running
        telemetry["hardware"] = hardware
        waveform_json = (
            self.server.waveform_program.to_json(max_flow_ml_s)
            if self.server.waveform_program is not None
            else None
        )
        if (
            waveform_json is not None
            and self.server.waveform_source == "curve_designer"
        ):
            locked_times = set(self.server.curve_designer_locked_times_ms)
            control_points = waveform_json["control_points"]
            for index, point in enumerate(control_points):
                point["locked"] = (
                    self.server.waveform_program.points[index].time_ms in locked_times
                )
                point["endpoint"] = index in (0, len(control_points) - 1)
        telemetry["control"] = {
            "mode": self.server.control_mode,
            "flow_ml_s": self.server.flow_ml_s,
            "flow_fraction": self.server.flow_ml_s / max_flow_ml_s,
            "max_flow_ml_s": max_flow_ml_s,
            "trigger_high_ms": self.server.trigger_high_ms,
            "waveform_update_hz": self.server.waveform_update_hz,
            "waveform_update_rates": list(WAVEFORM_UPDATE_RATES),
            "waveform_available": self.server.waveform_program is not None,
            "pulsatile_waveform_available": self.server.pulsatile_waveform_program is not None,
            "curve_designer_waveform_available": (
                self.server.curve_designer_waveform_program is not None
            ),
            "waveform_source": self.server.waveform_source,
            "waveform": waveform_json,
            "curve_designer": curve_designer,
        }
        telemetry["system"] = derive_system_state(
            hardware,
            running=bool(telemetry.get("running")),
            communication_ok=bool(telemetry.get("communication_ok")),
        )
        if hardware.get("connected") and hardware.get("fresh"):
            telemetry["interlocks"]["overpressure_latched"] = bool(hardware.get("pressure_latch_set"))
            telemetry["interlocks"]["parker_relay_closed"] = bool(hardware.get("servo_ready"))
            telemetry["interlocks"]["arduino_relay_closed"] = bool(hardware.get("arduino_enable"))
        return telemetry

    @staticmethod
    def _max_flow_ml_s(hardware: dict[str, object]) -> float:
        flows = hardware.get("calibration_flows_ml_s")
        if hardware.get("calibration_valid") and isinstance(flows, list) and len(flows) == 4:
            maximum = float(flows[-1])
            if maximum > 0.0:
                return maximum
        return CONSTANT_FLOW_FULL_SCALE_ML_S

    def _hardware_telemetry(self) -> dict[str, object]:
        hardware = (
            self.arduino.telemetry()
            if self.arduino is not None
            else {"connected": False, "last_error": "arduino serial bridge is disabled"}
        )
        calibration = self.server.loaded_calibration_flows_ml_s
        if calibration is not None:
            hardware = dict(hardware)
            hardware.update(
                calibration_valid=True,
                calibration_valid_mask=0b1111,
                calibration_flows_ml_s=list(calibration),
                calibration_loaded_locally=True,
                calibration_pending_transfer=self.server.calibration_pending_transfer,
            )
        return hardware

    def _sync_loaded_calibration(
        self, hardware: dict[str, object]
    ) -> dict[str, object]:
        calibration = self.server.loaded_calibration_flows_ml_s
        if (
            calibration is None
            or not self.server.calibration_pending_transfer
            or self.arduino is None
            or not hardware.get("connected")
            or not hardware.get("fresh")
        ):
            return hardware
        self.arduino.load_calibration(calibration)
        self.arduino.request_telemetry()
        time.sleep(0.05)
        self.server.calibration_pending_transfer = False
        return self._hardware_telemetry()

    def _require_start_ready(self, hardware: dict[str, object]) -> None:
        if not hardware.get("connected"):
            raise RuntimeError("cannot start while Arduino is disconnected")
        if not hardware.get("fresh"):
            raise RuntimeError("cannot start with stale Arduino telemetry")
        if not hardware.get("protocol_compatible"):
            raise RuntimeError("cannot start with incompatible Arduino firmware protocol")
        if not hardware.get("dac_ok"):
            raise RuntimeError("cannot start while DAC communication is unavailable")
        if hardware.get("fault_latched") or hardware.get("controlled_stop_active"):
            raise RuntimeError("cannot start while an Arduino fault is active")
        if hardware.get("run_mode") != "stopped" or hardware.get("wave_running"):
            raise RuntimeError("cannot start while Arduino is already running")
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
    server.pulsatile_waveform_program = None
    server.curve_designer_waveform_program = None
    server.curve_designer_reference_program = None
    server.curve_designer_baseline_program = None
    server.curve_designer_baseline_locked_times_ms = ()
    server.loaded_calibration_flows_ml_s = None
    server.calibration_pending_transfer = False
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
