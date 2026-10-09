import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const html = readFileSync(new URL("../src/cardiopump_backend/web/index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/cardiopump_backend/web/app.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/cardiopump_backend/web/app.css", import.meta.url), "utf8");

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

test("fault reset is available above every navigation tab", () => {
  const topbar = html.slice(0, html.indexOf('<div class="app-shell">'));
  assert.match(topbar, /id="clear-fault-button"/);
});

test("setup exposes the raster toggle and calibration curve workspace", () => {
  assert.match(html, /id="waveform-rate-toggle"[^>]+type="checkbox"[^>]+role="switch"/);
  assert.match(html, /id="calibration-canvas"/);
  assert.match(app, /dataset\.calibrationVoltage/);
});

test("calibration files can load while the Arduino is disconnected", () => {
  const disabledExpression = app.match(
    /elements\.calibrationFile\.disabled = ([\s\S]*?);/,
  )?.[1] || "";
  assert.doesNotMatch(disabledExpression, /connected|fresh/);
  assert.match(app, /hardware\.calibration_pending_transfer/);
});

test("waveform loading reports its filename and does not cap requested scaling", () => {
  assert.match(html, /id="waveform-file-title"/);
  assert.match(html, /id="waveform-file-hint"/);
  assert.match(html, /id="waveform-file"[^>]+accept="[^"]*\.csv[^"]*text\/csv/);
  assert.doesNotMatch(html, /id="waveform-max-flow-input"[^>]+max=/);
  assert.match(app, /state\.waveformFileName = file\.name/);
  assert.equal((html.match(/class="file-drop-control(?: compact-drop)?"/g) || []).length, 3);
  assert.match(css, /\.file-drop-control > strong[^}]+text-overflow: ellipsis/);
  assert.match(css, /\.file-drop-control > strong[^}]+white-space: nowrap/);
});

test("curve designer chooses point count before loading and plots fitted points", () => {
  const pointCountIndex = html.indexOf('id="curve-control-point-count"');
  const fileInputIndex = html.indexOf('id="curve-designer-file"');

  assert.ok(pointCountIndex >= 0);
  assert.ok(fileInputIndex > pointCountIndex);
  assert.match(html, /id="curve-control-point-count"[^>]+type="number"[^>]+min="2"[^>]+max="20"/);
  assert.match(html, /id="curve-point-count-decrease"/);
  assert.match(html, /id="curve-point-count-increase"/);
  assert.match(html, /id="curve-designer-canvas"/);
  assert.match(html, /id="curve-designer-file"[^>]+accept="[^"]*\.json[^"]*application\/json/);
  assert.match(html, /id="save-curve-csv-button"/);
  assert.match(html, /id="save-curve-checkpoint-button"/);
  assert.match(html, /Periodic Curve Designer/);
  assert.doesNotMatch(html, /Control-point curve/);
  assert.match(html, /id="curve-designer-canvas"[^>]+height="540"/);
  assert.match(css, /\.curve-metrics > div[^}]+display: flex/);
  assert.match(css, /\.curve-metrics strong[^}]+display: inline/);
  assert.match(html, /id="curve-max-flow-input"/);
  assert.match(html, /id="curve-period-input"/);
  assert.match(html, /id="curve-interpolation-select"/);
  assert.match(html, /option value="pchip"/);
  assert.match(html, /option value="linear"/);
  assert.match(html, /option value="cubic"/);
  assert.match(html, /id="curve-stroke-volume-value"/);
  assert.doesNotMatch(html, /id="curve-(?:peak|duration)-value"/);
  assert.match(html, /id="curve-reset-button"/);
  assert.match(html, /id="curve-original-legend"[^>]+hidden/);
  assert.match(html, /id="curve-point-menu"[^>]+role="menu"[^>]+hidden/);
  assert.match(html, /id="curve-point-add-button"/);
  assert.match(html, /id="curve-point-lock-button"/);
  assert.match(html, /id="curve-point-remove-button"/);
  assert.match(app, /postJson\("\/api\/curve-designer"/);
  assert.match(app, /postJson\("\/api\/curve-designer\/export"/);
  assert.match(app, /saveCurveDesigner\("csv"\)/);
  assert.match(app, /saveCurveDesigner\("checkpoint"\)/);
  assert.match(app, /postJson\("\/api\/curve-designer\/refit"/);
  assert.match(app, /control_point_count: controlPointCount/);
  assert.match(app, /curveMaxFlowInput\.addEventListener\("change", updateCurveDesignerSettings\)/);
  assert.match(app, /curvePeriodInput\.addEventListener\("change", updateCurveDesignerSettings\)/);
  assert.match(app, /waveform\.control_points/);
  assert.match(app, /fit\?\.original_preview \|\| \[\]/);
  assert.match(app, /fit\.original_stroke_volume_ml/);
  assert.match(app, /curveDesignerFit\?\.modified === true/);
  assert.match(app, /addEventListener\("contextmenu", handleCurveDesignerContextMenu\)/);
  assert.match(app, /addEventListener\("pointerdown", handleCurveZoomPointerDown\)/);
  assert.match(app, /addEventListener\("pointermove", handleCurveZoomPointerMove\)/);
  assert.match(app, /addEventListener\("pointerup", handleCurveZoomPointerUp\)/);
  assert.match(app, /addEventListener\("dblclick", resetCurveZoom\)/);
  assert.match(app, /const calibratedMaximumFlow = Number\(waveform\?\.flow_limit_ml_s\)/);
  assert.match(app, /Math\.max\(maximumFlow, calibratedMaximumFlow\)/);
  assert.match(app, /const flowRange = axisMaximumFlow - minimumFlow/);
  assert.match(app, /flowRange \/ 20/);
  assert.match(app, /const fullPlotMinimum = minimumFlow - flowPadding/);
  assert.match(app, /const fullPlotMaximum = axisMaximumFlow \+ flowPadding/);
  assert.match(app, /boxWidth >= 3 \* boxHeight/);
  assert.match(app, /boxHeight >= 3 \* boxWidth/);
  assert.match(app, /context\.moveTo\(left, currentY - 7\)/);
  assert.match(app, /context\.moveTo\(currentX - 7, top\)/);
  assert.match(app, /distanceToCurve\(canvasPoint, plot\.curvePoints\)/);
  assert.match(app, /state\.curveSelectedPointTimeMs/);
  assert.match(app, /state\.curvePointDrag/);
  assert.match(app, /action: "move"/);
  assert.match(app, /new_time_ms: newTimeMs/);
  assert.match(app, /new_flow_ml_s: newFlowMlS/);
  assert.match(app, /closestPoint\.point\.locked === true/);
  assert.match(app, /postJson\("\/api\/curve-designer\/interpolation"/);
  assert.match(app, /postJson\("\/api\/curve-designer\/control-point"/);
  assert.match(app, /request\.waveform_source = waveformSource/);
  assert.match(app, /control\.pulsatile_waveform_available/);
  assert.match(app, /control\.curve_designer_waveform_available/);
});
