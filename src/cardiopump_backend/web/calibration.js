export const CALIBRATION_VOLTAGES_V = Object.freeze([2.5, 5, 7.5, 10]);

export function parseCalibrationFile(text) {
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("Calibration file must contain valid JSON.");
  }
  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    throw new Error("Calibration file must contain a JSON object.");
  }
  if (payload.format !== undefined && payload.format !== "cardiopump-calibration") {
    throw new Error("Calibration file format is not supported.");
  }
  if (payload.version !== undefined && payload.version !== 1) {
    throw new Error("Calibration file version is not supported.");
  }
  if (payload.voltages_v !== undefined) {
    if (
      !Array.isArray(payload.voltages_v)
      || payload.voltages_v.length !== CALIBRATION_VOLTAGES_V.length
      || payload.voltages_v.some((value, index) => Number(value) !== CALIBRATION_VOLTAGES_V[index])
    ) {
      throw new Error("Calibration voltages must be 2.5, 5, 7.5, and 10 V.");
    }
  }

  const flows = payload.flows_ml_s;
  if (!Array.isArray(flows) || flows.length !== CALIBRATION_VOLTAGES_V.length) {
    throw new Error("Calibration file must contain four flows_ml_s values.");
  }
  const parsedFlows = flows.map(Number);
  if (parsedFlows.some((flow) => !Number.isFinite(flow) || flow <= 0)) {
    throw new Error("Calibration flow values must be positive finite numbers.");
  }
  if (parsedFlows.some((flow, index) => index > 0 && flow <= parsedFlows[index - 1])) {
    throw new Error("Calibration flow values must be strictly increasing.");
  }
  return parsedFlows;
}

export function serializeCalibrationFile(flows, createdAt = new Date()) {
  const parsedFlows = parseCalibrationFile(JSON.stringify({ flows_ml_s: flows }));
  return JSON.stringify({
    format: "cardiopump-calibration",
    version: 1,
    voltages_v: CALIBRATION_VOLTAGES_V,
    flows_ml_s: parsedFlows,
    created_at: createdAt.toISOString(),
  }, null, 2);
}
