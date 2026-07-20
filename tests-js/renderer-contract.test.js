import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const html = readFileSync(new URL("../src/cardiopump_backend/web/index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/cardiopump_backend/web/app.js", import.meta.url), "utf8");

test("renderer IDs are unique and every JavaScript ID selector exists", () => {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length, "renderer contains duplicate IDs");

  const selectedIds = [...app.matchAll(/querySelector\("#([A-Za-z0-9_-]+)/g)]
    .map((match) => match[1]);
  const missingIds = [...new Set(selectedIds)].filter((id) => !ids.includes(id));
  assert.deepEqual(missingIds, []);
});

test("safety-critical confirmations remain nonblocking", () => {
  assert.doesNotMatch(app, /\b(?:alert|confirm|prompt)\s*\(/);
});

test("setup exposes the raster toggle and calibration curve workspace", () => {
  assert.match(html, /id="waveform-rate-toggle"[^>]+type="checkbox"[^>]+role="switch"/);
  assert.match(html, /id="calibration-canvas"/);
  assert.match(app, /dataset\.calibrationVoltage/);
});

test("waveform loading reports its filename and does not cap requested scaling", () => {
  assert.match(html, /id="waveform-file-title"/);
  assert.match(html, /id="waveform-file-hint"/);
  assert.match(html, /id="waveform-file"[^>]+accept="[^"]*\.csv[^"]*text\/csv/);
  assert.doesNotMatch(html, /id="waveform-max-flow-input"[^>]+max=/);
  assert.match(app, /state\.waveformFileName = file\.name/);
});
