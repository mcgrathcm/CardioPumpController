from __future__ import annotations


def derive_system_state(
    hardware: dict[str, object], *, running: bool, communication_ok: bool
) -> dict[str, str | None]:
    if not hardware.get("connected") or not communication_ok:
        return {"state": "disconnected", "reason": hardware.get("last_error") or "connection_lost"}

    if hardware.get("fresh") is False:
        return {"state": "disconnected", "reason": "telemetry_stale"}

    if hardware.get("protocol_compatible") is False:
        return {"state": "disconnected", "reason": "protocol_mismatch"}

    if hardware.get("fault_latched"):
        return {"state": "fault", "reason": hardware.get("fault_code") or "latched_fault"}

    if hardware.get("pressure_latch_set"):
        return {"state": "fault", "reason": "overpressure_latched"}

    if hardware.get("controlled_stop_active"):
        return {
            "state": "stopping",
            "reason": hardware.get("controlled_stop_code") or "controlled_stop",
        }

    if hardware.get("ramp_down_active"):
        return {"state": "stopping", "reason": "ramp_down"}

    if hardware.get("ramp_up_active"):
        return {"state": "starting", "reason": "ramp_up"}

    if hardware.get("run_mode") == "calibration":
        return {"state": "calibrating", "reason": hardware.get("calibration_stage")}

    if running:
        return {"state": "running", "reason": None}

    if hardware.get("arduino_enable") and hardware.get("servo_ready"):
        return {"state": "stopped", "reason": None}

    if hardware.get("arduino_enable") and not hardware.get("servo_ready"):
        return {"state": "disabled", "reason": "servo_not_ready"}

    return {"state": "disabled", "reason": None}
