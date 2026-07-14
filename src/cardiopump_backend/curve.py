from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .constants import MAX_VELOCITY_FRACTION, MIN_VELOCITY_FRACTION


@dataclass(frozen=True)
class CurvePoint:
    time_s: float
    velocity_fraction: float


class VelocityCurve:
    def __init__(self, points: list[CurvePoint]):
        if len(points) < 2:
            raise ValueError("velocity curve requires at least two points")

        if points[0].time_s != 0:
            raise ValueError("velocity curve must start at time_s=0")

        last_time = -1.0
        for point in points:
            if not isfinite(point.time_s) or not isfinite(point.velocity_fraction):
                raise ValueError("curve values must be finite")
            if point.time_s <= last_time:
                raise ValueError("curve point times must be strictly increasing")
            if not MIN_VELOCITY_FRACTION <= point.velocity_fraction <= MAX_VELOCITY_FRACTION:
                raise ValueError("velocity_fraction must be between -1.0 and 1.0")
            last_time = point.time_s

        if points[-1].time_s <= 0:
            raise ValueError("velocity curve duration must be positive")

        self._points = tuple(points)

    @property
    def points(self) -> tuple[CurvePoint, ...]:
        return self._points

    @property
    def duration_s(self) -> float:
        return self._points[-1].time_s

    def velocity_at(self, elapsed_s: float) -> float:
        phase_s = elapsed_s % self.duration_s

        for left, right in zip(self._points, self._points[1:]):
            if phase_s <= right.time_s:
                span_s = right.time_s - left.time_s
                fraction = (phase_s - left.time_s) / span_s
                return left.velocity_fraction + fraction * (
                    right.velocity_fraction - left.velocity_fraction
                )

        return self._points[-1].velocity_fraction

    def to_json(self) -> list[dict[str, float]]:
        return [
            {"time_s": point.time_s, "velocity_fraction": point.velocity_fraction}
            for point in self._points
        ]


def curve_from_json(raw_points: object) -> VelocityCurve:
    if not isinstance(raw_points, list):
        raise ValueError("points must be a list")

    points = []
    for raw_point in raw_points:
        if not isinstance(raw_point, dict):
            raise ValueError("each point must be an object")
        try:
            time_s = float(raw_point["time_s"])
            velocity_fraction = float(raw_point["velocity_fraction"])
        except KeyError as exc:
            raise ValueError(f"missing curve field: {exc.args[0]}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError("curve point values must be numeric") from exc

        points.append(CurvePoint(time_s=time_s, velocity_fraction=velocity_fraction))

    return VelocityCurve(points)
