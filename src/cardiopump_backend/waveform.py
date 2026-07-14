from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from math import floor, isclose, isfinite

from .constants import MAX_WAVEFORM_SAMPLES, WAVEFORM_UPDATE_HZ


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
        return _round_period_to_raster(self.source_duration_ms, self.update_hz)

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
        flows = self._commanded_flows()
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
        flows = self._commanded_flows()
        if not flows:
            return 0.0

        # Total displaced volume counts reverse flow by magnitude.
        dt_s = 1.0 / self.update_hz
        return sum(
            (abs(flows[index]) + abs(flows[(index + 1) % len(flows)])) * 0.5 * dt_s
            for index in range(len(flows))
        )

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
        period_ms = _round_period_to_raster(self.period_ms, update_hz)
        program = WaveformProgram(
            points=self.points,
            update_hz=update_hz,
            max_samples=self.max_samples,
            flow_scale=self.flow_scale,
            period_scale=period_ms / self.source_duration_ms,
            clip_negative_flow=self.clip_negative_flow,
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
        )

    def reset_scaling(self) -> WaveformProgram:
        return self.with_scaling(1.0, self.default_period_scale)

    def to_json(self) -> dict[str, object]:
        scaled_flows = self._commanded_flows()
        preview = self._preview_points(scaled_flows)
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
            "period_ms": self.period_ms,
            "raster_period_ms": self.raster_period_ms,
            "period_is_raster_aligned": _is_raster_multiple(self.period_ms, self.update_hz),
            "sample_count": self.sample_count,
            "update_hz": self.update_hz,
            "duration_s": self.duration_s,
            "min_flow_ml_s": min(point["flow_ml_s"] for point in preview),
            "max_flow_ml_s_preview": max(point["flow_ml_s"] for point in preview),
            "clipped_sample_count": self.clipped_sample_count,
            "clip_negative_flow": self.clip_negative_flow,
            "negative_clipped_sample_count": self.negative_clipped_sample_count,
            "total_stroke_volume_ml": self.total_stroke_volume_ml,
            "calibration": "linear_full_scale",
            "preview": preview,
        }

    def _resampled_flows(self) -> tuple[float, ...]:
        count = self.sample_count
        return tuple(
            _interpolate(
                self.points,
                (index * 1000.0 / self.update_hz) / self.period_scale,
            )
            * self.flow_scale
            for index in range(count)
        )

    def _commanded_flows(self) -> tuple[float, ...]:
        flows = self._resampled_flows()
        if not self.clip_negative_flow:
            return flows
        return tuple(max(0.0, flow) for flow in flows)

    def _flow_to_drive_fraction(self, flow_ml_s: float, *, clip: bool = True) -> float:
        # This is the linear full-scale fallback until the user calibration curve exists.
        max_flow = self.max_flow_ml_s
        drive_fraction = 0.0 if max_flow == 0.0 else flow_ml_s / max_flow
        return max(-1.0, min(1.0, drive_fraction)) if clip else drive_fraction

    def _preview_points(
        self,
        scaled_flows: tuple[float, ...],
        max_points: int = 500,
    ) -> list[dict[str, float | bool]]:
        step = max(1, len(scaled_flows) // max_points)
        points: list[dict[str, float | bool]] = []
        for index in range(0, len(scaled_flows), step):
            flow = scaled_flows[index]
            drive_fraction = self._flow_to_drive_fraction(flow, clip=False)
            clipped_drive_fraction = max(-1.0, min(1.0, drive_fraction))
            clipped_flow = clipped_drive_fraction * self.max_flow_ml_s
            points.append(
                {
                    "time_ms": index * 1000.0 / self.update_hz,
                    "time_s": index / self.update_hz,
                    "flow_ml_s": clipped_flow,
                    "flow": clipped_flow,
                    "drive_fraction": clipped_drive_fraction,
                    "clipped": abs(drive_fraction) > 1.0,
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
    default_period_ms = _round_period_to_raster(source_duration_ms, update_hz)
    program = WaveformProgram(
        points=points,
        update_hz=update_hz,
        max_samples=max_samples,
        period_scale=default_period_ms / source_duration_ms,
    )
    program.sample_count
    return program


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


def _sample_count(period_ms: float, update_hz: int, max_samples: int) -> int:
    count = round(period_ms / 1000.0 * update_hz)
    if count < 1:
        raise ValueError("waveform period is too short for the configured update rate")
    if count > max_samples:
        raise ValueError(
            f"waveform has {count} samples at {update_hz} Hz; limit is {max_samples}"
        )
    return count


def _interpolate(points: tuple[WaveformPoint, ...], time_ms: float) -> float:
    times = [point.time_ms for point in points]
    source_index = bisect_right(times, time_ms) - 1
    if source_index >= len(points) - 1:
        return points[-1].flow_ml_s
    if source_index < 0:
        return points[0].flow_ml_s
    left = points[source_index]
    right = points[source_index + 1]
    fraction = (time_ms - left.time_ms) / (right.time_ms - left.time_ms)
    return left.flow_ml_s + fraction * (right.flow_ml_s - left.flow_ml_s)


def _flow_to_q15(flow_fraction: float) -> int:
    return max(-32767, min(32767, round(flow_fraction * 32767)))


def _validate_update_hz(update_hz: int) -> None:
    if update_hz <= 0:
        raise ValueError("update_hz must be positive")


def _round_period_to_raster(period_ms: float, update_hz: int) -> float:
    raster_ms = 1000.0 / update_hz
    return max(raster_ms, floor(period_ms / raster_ms + 0.5) * raster_ms)


def _is_raster_multiple(period_ms: float, update_hz: int) -> bool:
    raster_ms = 1000.0 / update_hz
    return isclose(period_ms / raster_ms, round(period_ms / raster_ms), abs_tol=1e-6)
