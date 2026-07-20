import assert from "node:assert/strict";
import test from "node:test";

import {
  parseCalibrationFile,
  serializeCalibrationFile,
} from "../src/cardiopump_backend/web/calibration.js";

test("parses a four-point calibration file", () => {
  assert.deepEqual(
    parseCalibrationFile(JSON.stringify({
      format: "cardiopump-calibration",
      version: 1,
      voltages_v: [2.5, 5, 7.5, 10],
      flows_ml_s: [0.1, 0.2, 0.35, 0.5],
    })),
    [0.1, 0.2, 0.35, 0.5],
  );
});

test("rejects unsafe or ambiguous calibration values", () => {
  assert.throws(
    () => parseCalibrationFile('{"flows_ml_s":[0.1,0.2,0.2,0.5]}'),
    /strictly increasing/,
  );
  assert.throws(
    () => parseCalibrationFile('{"voltages_v":[1,2,3,4],"flows_ml_s":[0.1,0.2,0.3,0.4]}'),
    /voltages/,
  );
});

test("serializes a round-trippable calibration file", () => {
  const text = serializeCalibrationFile(
    [0.1, 0.2, 0.35, 0.5],
    new Date("2026-07-13T12:00:00Z"),
  );
  assert.deepEqual(parseCalibrationFile(text), [0.1, 0.2, 0.35, 0.5]);
  assert.equal(JSON.parse(text).created_at, "2026-07-13T12:00:00.000Z");
});
