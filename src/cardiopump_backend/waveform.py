from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from math import copysign, floor, isclose, isfinite, sqrt

from .constants import MAX_WAVEFORM_SAMPLES, WAVEFORM_UPDATE_HZ

CONTROL_POINT_INTERPOLATIONS = ("pchip", "linear", "cubic")
CURVE_DESIGNER_CHECKPOINT_FORMAT = "cardiopump-curve-designer"


@dataclass(frozen=True)
class WaveformPoint:
    time_ms: float
    flow_ml_s: float


@dataclass(frozen=True)
class WaveformProgram:
    points: tuple[WaveformPoint, ...]
    update_hz: int
    max_samples: int = MAX_WAVEFORM_SAMPLES
    flow_scale: float = 1.0
    period_scale: float = 1.0
    clip_negative_flow: bool = False
    interpolation: str = "linear"
    control_point_curve: bool = False

    @property
    def raster_period_ms(self) -> float:
        return 1000.0 / self.update_hz

    @property
    def source_duration_ms(self) -> float:
        return self.points[-1].time_ms

    @property
    def source_peak_flow_ml_s(self) -> float:
        return max(abs(point.flow_ml_s) for point in self.points)

    @property
    def default_period_ms(self) -> float:
        return round_to_raster_ms(self.source_duration_ms, self.update_hz)

    @property
    def default_period_scale(self) -> float:
        return self.default_period_ms / self.source_duration_ms

    @property
    def max_flow_ml_s(self) -> float:
        return self.source_peak_flow_ml_s * self.flow_scale

    @property
    def period_ms(self) -> float:
        return self.source_duration_ms * self.period_scale

    @property
    def duration_s(self) -> float:
        return self.period_ms / 1000.0

    @property
    def sample_count(self) -> int:
        return _sample_count(self.period_ms, self.update_hz, self.max_samples)

    @property
    def normalized_samples(self) -> tuple[float, ...]:
        flows = self._flows_with_limit(self.max_flow_ml_s)
        max_flow = self.max_flow_ml_s
        if max_flow == 0.0:
            return tuple(0.0 for _ in flows)
        return tuple(flow / max_flow for flow in flows)

    @property
    def samples(self) -> tuple[int, ...]:
        return tuple(
            _flow_to_q15(self._flow_to_drive_fraction(flow))
            for flow in self._commanded_flows()
        )

    def samples_with_flow_limit(self, max_flow_ml_s: float) -> tuple[int, ...]:
        applied_max_flow_ml_s = self.applied_max_flow_ml_s(max_flow_ml_s)
        flows = self._flows_with_limit(max_flow_ml_s)
        if applied_max_flow_ml_s == 0.0:
            return tuple(0 for _ in flows)
        return tuple(
            _flow_to_q15(flow / applied_max_flow_ml_s)
            for flow in flows
        )

    def applied_max_flow_ml_s(self, max_flow_ml_s: float) -> float:
        if not isfinite(max_flow_ml_s) or max_flow_ml_s < 0.0:
            raise ValueError("maximum flow limit must be non-negative")
        return min(self.max_flow_ml_s, max_flow_ml_s)

    @property
    def clipped_sample_count(self) -> int:
        return sum(
            1
            for flow in self._commanded_flows()
            if abs(self._flow_to_drive_fraction(flow, clip=False)) > 1.0
        )

    @property
    def negative_clipped_sample_count(self) -> int:
        if not self.clip_negative_flow:
            return 0
        return sum(1 for flow in self._resampled_flows() if flow < 0.0)

    @property
    def total_stroke_volume_ml(self) -> float:
        return _stroke_volume_ml(self._flows_with_limit(self.max_flow_ml_s), self.update_hz)

    def with_settings(self, max_flow_ml_s: float, period_ms: float) -> WaveformProgram:
        if not isfinite(max_flow_ml_s) or max_flow_ml_s < 0.0:
            raise ValueError("maximum flow must be non-negative")
        if not isfinite(period_ms) or period_ms <= 0.0:
            raise ValueError("period must be positive")
        if not _is_raster_multiple(period_ms, self.update_hz):
            raise ValueError(
                f"period must be a multiple of {self.raster_period_ms:g} ms "
                f"at {self.update_hz} Hz"
            )
        if self.source_peak_flow_ml_s == 0.0:
            flow_scale = 1.0
            if max_flow_ml_s != 0.0:
                raise ValueError("maximum flow must be zero for an all-zero waveform")
        else:
            flow_scale = max_flow_ml_s / self.source_peak_flow_ml_s
        period_scale = period_ms / self.source_duration_ms
        return self.with_scaling(flow_scale, period_scale)

    def with_scaling(self, flow_scale: float, period_scale: float) -> WaveformProgram:
        if not isfinite(flow_scale) or flow_scale < 0.0:
            raise ValueError("flow scaling must be non-negative")
        if not isfinite(period_scale) or period_scale <= 0.0:
            raise ValueError("period scaling must be positive")
        program = WaveformProgram(
            points=self.points,
            update_hz=self.update_hz,
            max_samples=self.max_samples,
            flow_scale=flow_scale,
            period_scale=period_scale,
            clip_negative_flow=self.clip_negative_flow,
            interpolation=self.interpolation,
            control_point_curve=self.control_point_curve,
        )
        program.sample_count
        if not _is_raster_multiple(program.period_ms, program.update_hz):
            raise ValueError(
                f"period must be a multiple of {program.raster_period_ms:g} ms "
                f"at {program.update_hz} Hz"
            )
        return program

    def with_update_hz(self, update_hz: int) -> WaveformProgram:
        _validate_update_hz(update_hz)
        period_ms = round_to_raster_ms(self.period_ms, update_hz)
        program = WaveformProgram(
            points=self.points,
            update_hz=update_hz,
            max_samples=self.max_samples,
            flow_scale=self.flow_scale,
            period_scale=period_ms / self.source_duration_ms,
            clip_negative_flow=self.clip_negative_flow,
            interpolation=self.interpolation,
            control_point_curve=self.control_point_curve,
        )
        program.sample_count
        return program

    def with_clip_negative_flow(self, enabled: bool) -> WaveformProgram:
        return WaveformProgram(
            points=self.points,
            update_hz=self.update_hz,
            max_samples=self.max_samples,
            flow_scale=self.flow_scale,
            period_scale=self.period_scale,
            clip_negative_flow=enabled,
            interpolation=self.interpolation,
            control_point_curve=self.control_point_curve,
        )

    def with_interpolation(self, interpolation: str) -> WaveformProgram:
        if not self.control_point_curve:
            raise ValueError("interpolation can only be changed for a control-point curve")
        _validate_control_point_interpolation(interpolation)
        return WaveformProgram(
            points=self.points,
            update_hz=self.update_hz,
            max_samples=self.max_samples,
            flow_scale=self.flow_scale,
            period_scale=self.period_scale,
            clip_negative_flow=self.clip_negative_flow,
            interpolation=interpolation,
            control_point_curve=True,
        )

    def reset_scaling(self) -> WaveformProgram:
        return self.with_scaling(1.0, self.default_period_scale)

    def to_json(self, max_flow_limit_ml_s: float | None = None) -> dict[str, object]:
        requested_flows = self._commanded_flows()
        if max_flow_limit_ml_s is None:
            applied_max_flow_ml_s = self.max_flow_ml_s
            scaled_flows = self._flows_with_limit(self.max_flow_ml_s)
        else:
            applied_max_flow_ml_s = self.applied_max_flow_ml_s(max_flow_limit_ml_s)
            scaled_flows = self._flows_with_limit(max_flow_limit_ml_s)
        clipped_sample_count = sum(
            requested != applied
            for requested, applied in zip(requested_flows, scaled_flows, strict=True)
        )
        preview = self._preview_points(
            scaled_flows,
            requested_flows,
            applied_max_flow_ml_s,
        )
        return {
            "source_point_count": len(self.points),
            "source_duration_ms": self.source_duration_ms,
            "source_peak_flow_ml_s": self.source_peak_flow_ml_s,
            "default_flow_scale": 1.0,
            "default_period_scale": self.default_period_scale,
            "default_period_ms": self.default_period_ms,
            "default_period_rounded": not isclose(
                self.default_period_ms, self.source_duration_ms, abs_tol=1e-6
            ),
            "flow_scale": self.flow_scale,
            "period_scale": self.period_scale,
            "max_flow_ml_s": self.max_flow_ml_s,
            "applied_max_flow_ml_s": applied_max_flow_ml_s,
            "flow_limit_ml_s": max_flow_limit_ml_s,
            "period_ms": self.period_ms,
            "raster_period_ms": self.raster_period_ms,
            "period_is_raster_aligned": _is_raster_multiple(self.period_ms, self.update_hz),
            "sample_count": self.sample_count,
            "update_hz": self.update_hz,
            "duration_s": self.duration_s,
            "min_flow_ml_s": min(point["flow_ml_s"] for point in preview),
            "max_flow_ml_s_preview": max(point["flow_ml_s"] for point in preview),
            "clipped_sample_count": clipped_sample_count,
            "clip_negative_flow": self.clip_negative_flow,
            "negative_clipped_sample_count": self.negative_clipped_sample_count,
            "total_stroke_volume_ml": _stroke_volume_ml(scaled_flows, self.update_hz),
            "calibration": "firmware_managed",
            "interpolation": self.interpolation,
            "control_points": self._control_points_json(max_flow_limit_ml_s),
            "preview": preview,
        }

    def _resampled_flows(self) -> tuple[float, ...]:
        count = self.sample_count
        slopes = _control_point_slopes(self.points, self.interpolation)
        return tuple(
            _interpolate(
                self.points,
                (index * 1000.0 / self.update_hz) / self.period_scale,
                slopes,
            )
            * self.flow_scale
            for index in range(count)
        )

    def _commanded_flows(self) -> tuple[float, ...]:
        flows = self._resampled_flows()
        if not self.clip_negative_flow:
            return flows
        return tuple(max(0.0, flow) for flow in flows)

    def _flows_with_limit(self, max_flow_ml_s: float) -> tuple[float, ...]:
        if not isfinite(max_flow_ml_s) or max_flow_ml_s < 0.0:
            raise ValueError("maximum flow limit must be non-negative")
        # Match Q15 saturation, including interpolation overshoot above the requested peak.
        max_flow_ml_s = self.applied_max_flow_ml_s(max_flow_ml_s)
        return tuple(
            max(-max_flow_ml_s, min(max_flow_ml_s, flow))
            for flow in self._commanded_flows()
        )

    def _flow_to_drive_fraction(self, flow_ml_s: float, *, clip: bool = True) -> float:
        # Q15 preserves waveform shape; firmware applies calibration or its conservative fallback.
        max_flow = self.max_flow_ml_s
        drive_fraction = 0.0 if max_flow == 0.0 else flow_ml_s / max_flow
        return max(-1.0, min(1.0, drive_fraction)) if clip else drive_fraction

    def _control_points_json(
        self, max_flow_limit_ml_s: float | None
    ) -> list[dict[str, float | bool]]:
        if not self.control_point_curve:
            return []
        points = []
        for point in self.points:
            requested_flow = point.flow_ml_s * self.flow_scale
            commanded_flow = (
                max(0.0, requested_flow) if self.clip_negative_flow else requested_flow
            )
            flow = commanded_flow
            if max_flow_limit_ml_s is not None:
                flow = max(-max_flow_limit_ml_s, min(max_flow_limit_ml_s, flow))
            time_ms = point.time_ms * self.period_scale
            points.append(
                {
                    "time_ms": time_ms,
                    "time_s": time_ms / 1000.0,
                    "flow_ml_s": flow,
                    "flow": flow,
                    "clipped": flow != requested_flow,
                }
            )
        return points

    def _preview_points(
        self,
        scaled_flows: tuple[float, ...],
        requested_flows: tuple[float, ...],
        applied_max_flow_ml_s: float,
        max_points: int = 500,
    ) -> list[dict[str, float | bool]]:
        step = max(1, len(scaled_flows) // max_points)
        points: list[dict[str, float | bool]] = []
        for index in range(0, len(scaled_flows), step):
            flow = scaled_flows[index]
            drive_fraction = (
                0.0 if applied_max_flow_ml_s == 0.0
                else flow / applied_max_flow_ml_s
            )
            points.append(
                {
                    "time_ms": index * 1000.0 / self.update_hz,
                    "time_s": index / self.update_hz,
                    "flow_ml_s": flow,
                    "flow": flow,
                    "drive_fraction": drive_fraction,
                    "clipped": flow != requested_flows[index],
                }
            )
        return points


def program_from_text(
    text: str,
    *,
    update_hz: int = WAVEFORM_UPDATE_HZ,
    max_samples: int = MAX_WAVEFORM_SAMPLES,
) -> WaveformProgram:
    _validate_update_hz(update_hz)
    if max_samples <= 0:
        raise ValueError("max_samples must be positive")
    points = tuple(_parse_points(text))
    source_duration_ms = points[-1].time_ms
    default_period_ms = round_to_raster_ms(source_duration_ms, update_hz)
    program = WaveformProgram(
        points=points,
        update_hz=update_hz,
        max_samples=max_samples,
        flow_scale=_flow_scale_from_metadata(text),
        period_scale=default_period_ms / source_duration_ms,
    )
    program.sample_count
    return program


@dataclass(frozen=True)
class ControlPointFit:
    program: WaveformProgram
    source_point_count: int
    max_error_ml_s: float
    rmse_ml_s: float


def control_point_program_from_text(
    text: str,
    control_point_count: int,
    *,
    update_hz: int = WAVEFORM_UPDATE_HZ,
    max_samples: int = MAX_WAVEFORM_SAMPLES,
) -> ControlPointFit:
    source_program = program_from_text(
        text,
        update_hz=update_hz,
        max_samples=max_samples,
    )
    fit = control_point_program_from_points(
        source_program.points,
        control_point_count,
        update_hz=update_hz,
        max_samples=max_samples,
    )
    return ControlPointFit(
        program=fit.program.with_scaling(
            source_program.flow_scale,
            source_program.period_scale,
        ),
        source_point_count=fit.source_point_count,
        max_error_ml_s=fit.max_error_ml_s * source_program.flow_scale,
        rmse_ml_s=fit.rmse_ml_s * source_program.flow_scale,
    )


def control_point_program_from_points(
    source_points: tuple[WaveformPoint, ...],
    control_point_count: int,
    *,
    update_hz: int = WAVEFORM_UPDATE_HZ,
    max_samples: int = MAX_WAVEFORM_SAMPLES,
) -> ControlPointFit:
    _validate_control_point_count(control_point_count)
    if control_point_count > len(source_points):
        raise ValueError(
            f"curve file contains {len(source_points)} points; "
            f"choose {len(source_points)} or fewer control points"
        )

    selected = {0, len(source_points) - 1}
    while len(selected) < control_point_count:
        control_points = _periodic_control_points(
            tuple(source_points[index] for index in sorted(selected))
        )
        slopes = _pchip_slopes(control_points)
        candidate = max(
            (index for index in range(1, len(source_points) - 1) if index not in selected),
            key=lambda index: (
                abs(
                    source_points[index].flow_ml_s
                    - _interpolate(control_points, source_points[index].time_ms, slopes)
                ),
                -index,
            ),
        )
        selected.add(candidate)

    control_points = _periodic_control_points(
        tuple(source_points[index] for index in sorted(selected))
    )
    return control_point_fit_from_points(
        source_points,
        control_points,
        update_hz=update_hz,
        max_samples=max_samples,
    )


def control_point_fit_from_points(
    source_points: tuple[WaveformPoint, ...],
    control_points: tuple[WaveformPoint, ...],
    *,
    interpolation: str = "pchip",
    update_hz: int = WAVEFORM_UPDATE_HZ,
    max_samples: int = MAX_WAVEFORM_SAMPLES,
) -> ControlPointFit:
    program = program_from_control_points(
        control_points,
        interpolation=interpolation,
        update_hz=update_hz,
        max_samples=max_samples,
    )
    control_points = program.points
    slopes = _control_point_slopes(control_points, interpolation)
    errors = tuple(
        point.flow_ml_s - _interpolate(control_points, point.time_ms, slopes)
        for point in source_points
    )
    return ControlPointFit(
        program=program,
        source_point_count=len(source_points),
        max_error_ml_s=max(abs(error) for error in errors),
        rmse_ml_s=sqrt(sum(error * error for error in errors) / len(errors)),
    )


def curve_designer_csv_text(program: WaveformProgram) -> str:
    if not program.control_point_curve:
        raise ValueError("only a Curve Designer program can be exported")
    flows = program._flows_with_limit(program.max_flow_ml_s)
    lines = [
        "# Cardiopump Curve Designer export",
        f"# Interpolation = {program.interpolation}",
        "# Columns: time_ms,flow_ml_s",
    ]
    lines.extend(
        f"{index * program.raster_period_ms:.12g},{flow:.12g}"
        for index, flow in enumerate(flows)
    )
    lines.append(f"{program.period_ms:.12g},{flows[0]:.12g}")
    return "\n".join(lines) + "\n"


def curve_designer_checkpoint_text(
    program: WaveformProgram,
    locked_times_ms: tuple[float, ...] = (),
) -> str:
    if not program.control_point_curve:
        raise ValueError("only a Curve Designer program can be checkpointed")
    locked_times = set(locked_times_ms)
    control_points = [
        {
            "time_ms": point.time_ms * program.period_scale,
            "flow_ml_s": point.flow_ml_s * program.flow_scale,
            "locked": point.time_ms in locked_times,
        }
        for point in program.points
    ]
    checkpoint = {
        "format": CURVE_DESIGNER_CHECKPOINT_FORMAT,
        "version": 1,
        "interpolation": program.interpolation,
        "period_ms": program.period_ms,
        "maximum_flow_ml_s": program.max_flow_ml_s,
        "control_points": control_points,
    }
    return json.dumps(checkpoint, indent=2) + "\n"


def curve_designer_checkpoint_from_text(
    text: str,
    *,
    update_hz: int = WAVEFORM_UPDATE_HZ,
    max_samples: int = MAX_WAVEFORM_SAMPLES,
) -> tuple[WaveformProgram, tuple[float, ...]] | None:
    if not text.lstrip().startswith("{"):
        return None
    try:
        checkpoint = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Curve Designer checkpoint is not valid JSON") from exc
    if not isinstance(checkpoint, dict) or checkpoint.get("format") != CURVE_DESIGNER_CHECKPOINT_FORMAT:
        raise ValueError("JSON file is not a Curve Designer checkpoint")
    if checkpoint.get("version") != 1:
        raise ValueError("unsupported Curve Designer checkpoint version")
    interpolation = checkpoint.get("interpolation")
    if not isinstance(interpolation, str):
        raise ValueError("Curve Designer checkpoint interpolation is required")
    raw_points = checkpoint.get("control_points")
    if not isinstance(raw_points, list):
        raise ValueError("Curve Designer checkpoint control points are required")
    points: list[WaveformPoint] = []
    locked_indices: list[int] = []
    for index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, dict):
            raise ValueError("Curve Designer checkpoint control points must be objects")
        try:
            time_ms = float(raw_point["time_ms"])
            flow_ml_s = float(raw_point["flow_ml_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Curve Designer checkpoint points require numeric time_ms and flow_ml_s"
            ) from exc
        points.append(WaveformPoint(time_ms, flow_ml_s))
        if raw_point.get("locked") is True:
            locked_indices.append(index)
    program = program_from_control_points(
        tuple(points),
        interpolation=interpolation,
        update_hz=update_hz,
        max_samples=max_samples,
    )
    locked_times_ms = tuple(program.points[index].time_ms for index in locked_indices)
    return program, locked_times_ms


def program_from_control_points(
    points: tuple[WaveformPoint, ...],
    *,
    interpolation: str = "pchip",
    update_hz: int = WAVEFORM_UPDATE_HZ,
    max_samples: int = MAX_WAVEFORM_SAMPLES,
) -> WaveformProgram:
    _validate_control_point_count(len(points))
    _validate_control_point_interpolation(interpolation)
    if (
        points[0].time_ms != 0.0
        or not all(
            isfinite(point.time_ms) and isfinite(point.flow_ml_s)
            for point in points
        )
        or any(
            right.time_ms <= left.time_ms
            for left, right in zip(points, points[1:])
        )
    ):
        raise ValueError("control points must be finite, start at 0 ms, and increase in time")
    return _control_point_program(
        _periodic_control_points(points),
        update_hz,
        max_samples,
        interpolation,
    )


def insert_control_point(program: WaveformProgram, time_ms: float) -> WaveformProgram:
    if not program.control_point_curve:
        raise ValueError("control points can only be added to a control-point curve")
    if len(program.points) >= 20:
        raise ValueError("curve already has the maximum of 20 control points")
    if not isfinite(time_ms) or not 0.0 < time_ms < program.period_ms:
        raise ValueError("new control point must be inside the curve period")

    source_time_ms = time_ms / program.period_scale
    tolerance_ms = max(1e-9, program.source_duration_ms * 1e-9)
    if any(
        isclose(point.time_ms, source_time_ms, abs_tol=tolerance_ms)
        for point in program.points
    ):
        raise ValueError("a control point already exists at that time")
    flow_ml_s = _interpolate(
        program.points,
        source_time_ms,
        _control_point_slopes(program.points, program.interpolation),
    ) * program.flow_scale
    points = tuple(sorted(
        (
            *(WaveformPoint(
                point.time_ms * program.period_scale,
                point.flow_ml_s * program.flow_scale,
            ) for point in program.points),
            WaveformPoint(time_ms, flow_ml_s),
        ),
        key=lambda point: point.time_ms,
    ))
    updated = program_from_control_points(
        points,
        interpolation=program.interpolation,
        update_hz=program.update_hz,
        max_samples=program.max_samples,
    )
    return updated.with_clip_negative_flow(program.clip_negative_flow)


def remove_control_point(program: WaveformProgram, index: int) -> WaveformProgram:
    if not program.control_point_curve:
        raise ValueError("control points can only be removed from a control-point curve")
    if index <= 0 or index >= len(program.points) - 1:
        raise ValueError("the first and last control points define the curve period")
    points = tuple(
        WaveformPoint(
            point.time_ms * program.period_scale,
            point.flow_ml_s * program.flow_scale,
        )
        for point_index, point in enumerate(program.points)
        if point_index != index
    )
    updated = program_from_control_points(
        points,
        interpolation=program.interpolation,
        update_hz=program.update_hz,
        max_samples=program.max_samples,
    )
    return updated.with_clip_negative_flow(program.clip_negative_flow)


def move_control_point(
    program: WaveformProgram,
    index: int,
    time_ms: float,
    flow_ml_s: float,
) -> WaveformProgram:
    if not program.control_point_curve:
        raise ValueError("control points can only be moved on a control-point curve")
    if not 0 <= index < len(program.points):
        raise ValueError("control point index is out of range")
    if not isfinite(time_ms) or not isfinite(flow_ml_s):
        raise ValueError("control point time and flow must be finite")

    displayed_points = tuple(
        WaveformPoint(
            point.time_ms * program.period_scale,
            point.flow_ml_s * program.flow_scale,
        )
        for point in program.points
    )
    if index in (0, len(displayed_points) - 1):
        time_ms = displayed_points[index].time_ms
        points = (
            WaveformPoint(displayed_points[0].time_ms, flow_ml_s),
            *displayed_points[1:-1],
            WaveformPoint(displayed_points[-1].time_ms, flow_ml_s),
        )
    elif not displayed_points[index - 1].time_ms < time_ms < displayed_points[index + 1].time_ms:
        raise ValueError("a moved control point cannot cross its neighbors")
    else:
        points = (
            displayed_points[:index]
            + (WaveformPoint(time_ms, flow_ml_s),)
            + displayed_points[index + 1 :]
        )
    updated = program_from_control_points(
        points,
        interpolation=program.interpolation,
        update_hz=program.update_hz,
        max_samples=program.max_samples,
    )
    return updated.with_clip_negative_flow(program.clip_negative_flow)


def constant_control_point_program(
    flow_ml_s: float,
    *,
    period_ms: float = 1000.0,
    update_hz: int = WAVEFORM_UPDATE_HZ,
    max_samples: int = MAX_WAVEFORM_SAMPLES,
) -> WaveformProgram:
    if not isfinite(flow_ml_s) or flow_ml_s < 0.0:
        raise ValueError("constant curve flow must be non-negative")
    if not isfinite(period_ms) or period_ms <= 0.0:
        raise ValueError("constant curve period must be positive")
    points = (
        WaveformPoint(time_ms=0.0, flow_ml_s=flow_ml_s),
        WaveformPoint(time_ms=period_ms, flow_ml_s=flow_ml_s),
    )
    return _control_point_program(points, update_hz, max_samples)


def _control_point_program(
    points: tuple[WaveformPoint, ...],
    update_hz: int,
    max_samples: int,
    interpolation: str = "pchip",
) -> WaveformProgram:
    _validate_update_hz(update_hz)
    if max_samples <= 0:
        raise ValueError("max_samples must be positive")
    source_duration_ms = points[-1].time_ms
    default_period_ms = round_to_raster_ms(source_duration_ms, update_hz)
    program = WaveformProgram(
        points=points,
        update_hz=update_hz,
        max_samples=max_samples,
        period_scale=default_period_ms / source_duration_ms,
        interpolation=interpolation,
        control_point_curve=True,
    )
    program.sample_count
    return program


def _periodic_control_points(
    points: tuple[WaveformPoint, ...],
) -> tuple[WaveformPoint, ...]:
    return points[:-1] + (
        WaveformPoint(points[-1].time_ms, points[0].flow_ml_s),
    )


def _parse_points(text: str) -> list[WaveformPoint]:
    points: list[WaveformPoint] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(",", " ").split()
        if len(parts) != 2:
            raise ValueError(f"line {line_number} must have exactly two columns")
        try:
            time_ms = float(parts[0])
            flow_ml_s = float(parts[1])
        except ValueError as exc:
            raise ValueError(f"line {line_number} contains a non-numeric value") from exc
        if not isfinite(time_ms) or not isfinite(flow_ml_s):
            raise ValueError(f"line {line_number} values must be finite")
        if time_ms < 0.0:
            raise ValueError(f"line {line_number} time must be non-negative")
        points.append(WaveformPoint(time_ms=time_ms, flow_ml_s=flow_ml_s))

    if len(points) < 2:
        raise ValueError("waveform must contain at least two points")
    if points[0].time_ms != 0.0:
        raise ValueError("first waveform time must be 0 ms")

    previous_time = points[0].time_ms
    for point in points[1:]:
        if point.time_ms <= previous_time:
            raise ValueError("waveform times must be strictly increasing")
        previous_time = point.time_ms
    return points


def _flow_scale_from_metadata(text: str) -> float:
    normalized_flow = False
    scale_text = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("#"):
            continue
        metadata = line[1:].strip()
        if metadata.casefold().startswith("columns:"):
            normalized_flow = "flow_normalized" in metadata.casefold()
        key, separator, value = metadata.partition("=")
        if separator and key.strip().casefold() == "scale":
            scale_text = value.rstrip(";").strip()
    if not normalized_flow or scale_text is None:
        return 1.0
    try:
        scale = float(scale_text)
    except ValueError as exc:
        raise ValueError("normalized waveform Scale metadata must be numeric") from exc
    if not isfinite(scale) or scale <= 0.0:
        raise ValueError("normalized waveform Scale metadata must be positive")
    return scale


def _sample_count(period_ms: float, update_hz: int, max_samples: int) -> int:
    count = round(period_ms / 1000.0 * update_hz)
    if count < 1:
        raise ValueError("waveform period is too short for the configured update rate")
    if count > max_samples:
        raise ValueError(
            f"waveform has {count} samples at {update_hz} Hz; limit is {max_samples}"
        )
    return count


def _interpolate(
    points: tuple[WaveformPoint, ...],
    time_ms: float,
    slopes: tuple[float, ...] | None = None,
) -> float:
    times = [point.time_ms for point in points]
    source_index = bisect_right(times, time_ms) - 1
    if source_index >= len(points) - 1:
        return points[-1].flow_ml_s
    if source_index < 0:
        return points[0].flow_ml_s
    left = points[source_index]
    right = points[source_index + 1]
    fraction = (time_ms - left.time_ms) / (right.time_ms - left.time_ms)
    if slopes is not None:
        span = right.time_ms - left.time_ms
        fraction_squared = fraction * fraction
        fraction_cubed = fraction_squared * fraction
        return (
            (2 * fraction_cubed - 3 * fraction_squared + 1) * left.flow_ml_s
            + (fraction_cubed - 2 * fraction_squared + fraction) * span * slopes[source_index]
            + (-2 * fraction_cubed + 3 * fraction_squared) * right.flow_ml_s
            + (fraction_cubed - fraction_squared) * span * slopes[source_index + 1]
        )
    return left.flow_ml_s + fraction * (right.flow_ml_s - left.flow_ml_s)


def _control_point_slopes(
    points: tuple[WaveformPoint, ...], interpolation: str
) -> tuple[float, ...] | None:
    if interpolation == "linear":
        return None
    if interpolation == "pchip":
        return _pchip_slopes(points)
    if interpolation == "cubic":
        return _periodic_cubic_slopes(points)
    raise ValueError(f"unsupported interpolation strategy: {interpolation}")


def _periodic_cubic_slopes(points: tuple[WaveformPoint, ...]) -> tuple[float, ...]:
    spans = tuple(
        right.time_ms - left.time_ms for left, right in zip(points, points[1:])
    )
    secants = tuple(
        (right.flow_ml_s - left.flow_ml_s) / span
        for left, right, span in zip(points, points[1:], spans)
    )
    unique_count = len(points) - 1
    if unique_count == 1:
        return (0.0, 0.0)

    matrix = [[0.0] * unique_count for _ in range(unique_count)]
    right_hand_side = [0.0] * unique_count
    for index in range(unique_count):
        previous = (index - 1) % unique_count
        following = (index + 1) % unique_count
        previous_span = spans[previous]
        following_span = spans[index]
        matrix[index][previous] += previous_span
        matrix[index][index] += 2.0 * (previous_span + following_span)
        matrix[index][following] += following_span
        right_hand_side[index] = 6.0 * (
            secants[index] - secants[previous]
        )

    for column in range(unique_count):
        pivot = max(
            range(column, unique_count),
            key=lambda row: abs(matrix[row][column]),
        )
        if pivot != column:
            matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
            right_hand_side[column], right_hand_side[pivot] = (
                right_hand_side[pivot],
                right_hand_side[column],
            )
        for row in range(column + 1, unique_count):
            factor = matrix[row][column] / matrix[column][column]
            for entry in range(column, unique_count):
                matrix[row][entry] -= factor * matrix[column][entry]
            right_hand_side[row] -= factor * right_hand_side[column]

    second_derivatives = [0.0] * unique_count
    for row in range(unique_count - 1, -1, -1):
        second_derivatives[row] = (
            right_hand_side[row]
            - sum(
                matrix[row][column] * second_derivatives[column]
                for column in range(row + 1, unique_count)
            )
        ) / matrix[row][row]

    slopes = []
    for index in range(unique_count):
        following = (index + 1) % unique_count
        slopes.append(
            secants[index]
            - spans[index]
            * (2.0 * second_derivatives[index] + second_derivatives[following])
            / 6.0
        )
    return (*slopes, slopes[0])


def _pchip_slopes(points: tuple[WaveformPoint, ...]) -> tuple[float, ...]:
    spans = tuple(
        right.time_ms - left.time_ms for left, right in zip(points, points[1:])
    )
    secants = tuple(
        (right.flow_ml_s - left.flow_ml_s) / span
        for left, right, span in zip(points, points[1:], spans)
    )
    if len(points) == 2:
        return (0.0, 0.0)

    endpoint_slope = _periodic_pchip_slope(
        spans[-1], spans[0], secants[-1], secants[0]
    )
    slopes = [endpoint_slope]
    for index in range(1, len(points) - 1):
        slopes.append(
            _periodic_pchip_slope(
                spans[index - 1],
                spans[index],
                secants[index - 1],
                secants[index],
            )
        )
    slopes.append(endpoint_slope)
    return tuple(slopes)


def _periodic_pchip_slope(
    left_span: float,
    right_span: float,
    left_secant: float,
    right_secant: float,
) -> float:
    if (
        left_secant == 0.0
        or right_secant == 0.0
        or copysign(1, left_secant) != copysign(1, right_secant)
    ):
        return 0.0
    left_weight = 2.0 * right_span + left_span
    right_weight = right_span + 2.0 * left_span
    return (left_weight + right_weight) / (
        left_weight / left_secant + right_weight / right_secant
    )


def _flow_to_q15(flow_fraction: float) -> int:
    return max(-32767, min(32767, round(flow_fraction * 32767)))


def _stroke_volume_ml(flows: tuple[float, ...], update_hz: int) -> float:
    # One full cycle of uniformly held samples; reverse flow subtracts volume.
    return sum(flows) / update_hz


def _validate_update_hz(update_hz: int) -> None:
    if update_hz <= 0:
        raise ValueError("update_hz must be positive")


def _validate_control_point_count(control_point_count: int) -> None:
    if isinstance(control_point_count, bool) or not isinstance(control_point_count, int):
        raise ValueError("control point count must be an integer")
    if not 2 <= control_point_count <= 20:
        raise ValueError("control point count must be between 2 and 20")


def _validate_control_point_interpolation(interpolation: str) -> None:
    if interpolation not in CONTROL_POINT_INTERPOLATIONS:
        choices = ", ".join(CONTROL_POINT_INTERPOLATIONS)
        raise ValueError(f"interpolation must be one of: {choices}")


def round_to_raster_ms(period_ms: float, update_hz: int) -> float:
    raster_ms = 1000.0 / update_hz
    return max(raster_ms, floor(period_ms / raster_ms + 0.5) * raster_ms)


def _is_raster_multiple(period_ms: float, update_hz: int) -> bool:
    raster_ms = 1000.0 / update_hz
    return isclose(period_ms / raster_ms, round(period_ms / raster_ms), abs_tol=1e-6)
