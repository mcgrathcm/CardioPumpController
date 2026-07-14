from __future__ import annotations

from dataclasses import dataclass

from .constants import DAC_I2C_ADDRESS, MAX_DAC_VOLTAGE, SAFETY_TIMEOUT_S
from .curve import CurvePoint, VelocityCurve


@dataclass(frozen=True)
class InterlockState:
    overpressure_latched: bool = False
    parker_relay_closed: bool = True
    arduino_relay_closed: bool = False


class PumpControllerSimulator:
    def __init__(self, now_s: float = 0.0):
        self._curve = VelocityCurve(
            [
                CurvePoint(time_s=0.0, velocity_fraction=0.0),
                CurvePoint(time_s=1.0, velocity_fraction=0.0),
            ]
        )
        self._running = False
        self._start_time_s = now_s
        self._last_host_contact_s = now_s
        self._velocity_fraction = 0.0
        self._communication_ok = True
        self._overpressure_latched = False
        self._parker_relay_closed = True
        self._arduino_relay_closed = False
        self._reset_pulse_count = 0

    def heartbeat(self, now_s: float) -> None:
        self._record_host_contact(now_s)

    def load_curve(self, curve: VelocityCurve, now_s: float) -> None:
        self._record_host_contact(now_s)
        if self._running:
            raise RuntimeError("stop pump before loading a new velocity curve")
        self._curve = curve

    def set_constant_velocity(self, velocity_fraction: float, now_s: float) -> None:
        self._record_host_contact(now_s)
        self._curve = VelocityCurve(
            [
                CurvePoint(time_s=0.0, velocity_fraction=velocity_fraction),
                CurvePoint(time_s=1.0, velocity_fraction=velocity_fraction),
            ]
        )
        if self._running:
            self._start_time_s = now_s
            self.tick(now_s)

    def start(self, now_s: float) -> None:
        self._record_host_contact(now_s)
        if self._overpressure_latched:
            raise RuntimeError("cannot start while overpressure latch is active")
        if not self._parker_relay_closed:
            raise RuntimeError("cannot start while Parker relay is open")

        self._start_time_s = now_s
        self._running = True
        self._arduino_relay_closed = True
        self.tick(now_s)

    def stop(self, now_s: float) -> None:
        self._record_host_contact(now_s)
        self._safe_state()

    def reset_overpressure_latch(self, now_s: float) -> None:
        self._record_host_contact(now_s)
        self._reset_pulse_count += 1
        self._overpressure_latched = False

    def set_simulated_interlocks(
        self,
        *,
        overpressure_latched: bool | None = None,
        parker_relay_closed: bool | None = None,
        now_s: float,
    ) -> None:
        self._record_host_contact(now_s)
        if overpressure_latched is not None:
            self._overpressure_latched = overpressure_latched
        if parker_relay_closed is not None:
            self._parker_relay_closed = parker_relay_closed
        if self._overpressure_latched or not self._parker_relay_closed:
            self._safe_state()

    def tick(self, now_s: float) -> None:
        if now_s - self._last_host_contact_s > SAFETY_TIMEOUT_S:
            self._communication_ok = False
            self._safe_state()
            return

        self._communication_ok = True
        if self._running:
            self._velocity_fraction = self._curve.velocity_at(now_s - self._start_time_s)
        else:
            self._velocity_fraction = 0.0

    def telemetry(self, now_s: float) -> dict[str, object]:
        self.tick(now_s)
        return {
            "running": self._running,
            "communication_ok": self._communication_ok,
            "safety_tripped": self._overpressure_latched
            or not self._parker_relay_closed
            or not self._communication_ok,
            "velocity_fraction": self._velocity_fraction,
            "dac_voltage": self._velocity_fraction * MAX_DAC_VOLTAGE,
            "dac_i2c_address": DAC_I2C_ADDRESS,
            "ms_since_last_host_command": round(
                1000 * max(0.0, now_s - self._last_host_contact_s)
            ),
            "curve": {
                "duration_s": self._curve.duration_s,
                "point_count": len(self._curve.points),
            },
            "interlocks": {
                "overpressure_latched": self._overpressure_latched,
                "parker_relay_closed": self._parker_relay_closed,
                "arduino_relay_closed": self._arduino_relay_closed,
                "reset_pulse_count": self._reset_pulse_count,
            },
        }

    def _record_host_contact(self, now_s: float) -> None:
        self._last_host_contact_s = now_s
        self._communication_ok = True

    def _safe_state(self) -> None:
        self._running = False
        self._velocity_fraction = 0.0
        self._arduino_relay_closed = False
