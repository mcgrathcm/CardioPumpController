import {
  parseCalibrationFile,
  serializeCalibrationFile,
} from "./calibration.js";

const state = {
  telemetry: null,
  connected: false,
  hadConnection: false,
  busy: false,
  refreshRequestId: 0,
  disableConfirmUntilMs: 0,
  heartbeatInFlight: false,
  activeTab: "flow",
  activeFlowMode: "constant",
  calibrationVoltage: null,
  calibrationSelectedVoltage: 2.5,
  calibrationFileName: null,
  calibrationClearConfirmUntilMs: 0,
  calibrationRenderKey: null,
  waveformFileName: null,
  waveformRenderKey: null,
};

const DISABLE_CONFIRM_WINDOW_MS = 4000;

const elements = {
  tabButtons: document.querySelectorAll("[data-tab]"),
  tabPanels: document.querySelectorAll(".tab-panel"),
  flowTabButtons: document.querySelectorAll("[data-flow-mode]"),
  flowTabPanels: document.querySelectorAll(".flow-panel"),
  connectionStatus: document.querySelector("#connection-status"),
  systemStateStatus: document.querySelector("#system-state-status"),
  systemStateDetail: document.querySelector("#system-state-detail"),
  overpressureStatus: document.querySelector("#overpressure-status"),
  servoStatus: document.querySelector("#servo-status"),
  arduinoEnableStatus: document.querySelector("#arduino-enable-status"),
  dacStatus: document.querySelector("#dac-status"),
  constantModeButton: document.querySelector("#constant-mode-button"),
  waveformModeButton: document.querySelector("#waveform-mode-button"),
  flowInput: document.querySelector("#flow-input"),
  waveformFile: document.querySelector("#waveform-file"),
  waveformFileTitle: document.querySelector("#waveform-file-title"),
  waveformFileHint: document.querySelector("#waveform-file-hint"),
  waveformRasterNote: document.querySelector("#waveform-raster-note"),
  waveformMaxFlowInput: document.querySelector("#waveform-max-flow-input"),
  waveformFlowLimitNote: document.querySelector("#waveform-flow-limit-note"),
  waveformPeriodInput: document.querySelector("#waveform-period-input"),
  clipNegativeFlowInput: document.querySelector("#clip-negative-flow-input"),
  resetWaveformDefaultsButton: document.querySelector("#reset-waveform-defaults-button"),
  waveformCanvas: document.querySelector("#waveform-canvas"),
  waveformStatus: document.querySelector("#waveform-status"),
  waveformPositionLabel: document.querySelector("#waveform-position-label"),
  waveformPeakValue: document.querySelector("#waveform-peak-value"),
  waveformDurationValue: document.querySelector("#waveform-duration-value"),
  waveformSamplesValue: document.querySelector("#waveform-samples-value"),
  waveformVolumeValue: document.querySelector("#waveform-volume-value"),
  waveformClippingValue: document.querySelector("#waveform-clipping-value"),
  constantFlowGauge: document.querySelector("#constant-flow-gauge"),
  constantFlowValue: document.querySelector("#constant-flow-value"),
  constantFlowLimit: document.querySelector("#constant-flow-limit"),
  constantFlowMode: document.querySelector("#constant-flow-mode"),
  constantCalibrationState: document.querySelector("#constant-calibration-state"),
  constantOutputState: document.querySelector("#constant-output-state"),
  triggerHighMsInput: document.querySelector("#trigger-high-ms-input"),
  waveformRateToggle: document.querySelector("#waveform-rate-toggle"),
  waveformRateLabel: document.querySelector("#waveform-rate-label"),
  waveformRate250: document.querySelector("#waveform-rate-250"),
  waveformRate500: document.querySelector("#waveform-rate-500"),
  calibrationStatus: document.querySelector("#calibration-status"),
  calibrationFile: document.querySelector("#calibration-file"),
  calibrationFileTitle: document.querySelector("#calibration-file-title"),
  calibrationFileHint: document.querySelector("#calibration-file-hint"),
  calibrationPoints: document.querySelector("#calibration-points"),
  calibrationCanvas: document.querySelector("#calibration-canvas"),
  calibrationPlotNote: document.querySelector("#calibration-plot-note"),
  calibrationConfirmInput: document.querySelector("#calibration-confirm-input"),
  calibrationStartButton: document.querySelector("#calibration-start-button"),
  calibrationMeasurement: document.querySelector("#calibration-measurement"),
  calibrationMeasurementType: document.querySelector("#calibration-measurement-type"),
  calibrationMeasurementValue: document.querySelector("#calibration-measurement-value"),
  calibrationSaveButton: document.querySelector("#calibration-save-button"),
  calibrationExportButton: document.querySelector("#calibration-export-button"),
  calibrationClearButton: document.querySelector("#calibration-clear-button"),
  mainEnableButton: document.querySelector("#main-enable-button"),
  mainStartButton: document.querySelector("#main-start-button"),
  clearFaultButton: document.querySelector("#clear-fault-button"),
  timingStatus: document.querySelector("#timing-status"),
  timingDetails: document.querySelector("#timing-details"),
  hardwareDiagnostics: document.querySelector("#hardware-diagnostics"),
  runDiagnostics: document.querySelector("#run-diagnostics"),
  watchdogStatus: document.querySelector("#watchdog-status"),
  controllerDiagnostics: document.querySelector("#controller-diagnostics"),
  controlMessage: document.querySelector("#control-message .activity-copy"),
};

async function api(path, options = {}) {
  if (window.cardiopump?.request) {
    return window.cardiopump.request(path, options);
  }
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

async function postJson(path, body = {}) {
  return api(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function setPill(element, text, className) {
  element.textContent = text;
  element.className = `pill ${className}`;
}

function setActiveTab(tabName) {
  state.activeTab = tabName;
  elements.tabButtons.forEach((button) => {
    const active = button.dataset.tab === tabName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  elements.tabPanels.forEach((panel) => {
    panel.hidden = panel.id !== `${tabName}-tab`;
  });
}

function setActiveFlowMode(mode) {
  state.activeFlowMode = mode;
  elements.flowTabButtons.forEach((button) => {
    const active = button.dataset.flowMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  elements.flowTabPanels.forEach((panel) => {
    panel.hidden = panel.id !== `${mode === "constant" ? "constant" : "pulsatile"}-flow-panel`;
  });
}

function formatSystemState(system) {
  const stateName = system?.state || "disconnected";
  return stateName.charAt(0).toUpperCase() + stateName.slice(1);
}

function formatFaultReason(reason) {
  return String(reason || "latched_fault").replaceAll("_", " ");
}

function renderStatusBadges(telemetry) {
  const hardware = telemetry.hardware || {};
  const hardwareConnected =
    hardware.connected === true &&
    hardware.fresh === true &&
    hardware.protocol_compatible === true;
  const connectionLost = !hardwareConnected && state.hadConnection;
  const system = telemetry.system || {};
  const systemState = system.state || (hardwareConnected ? "disabled" : "disconnected");

  setPill(
    elements.connectionStatus,
    hardwareConnected ? "Connected" : connectionLost ? "Connection Lost" : "Disconnected",
    hardwareConnected ? "ok" : connectionLost ? "bad flash" : "bad",
  );

  const systemClass = {
    running: "ok",
    stopped: "neutral",
    disabled: "neutral",
    stopping: "warning",
    starting: "warning",
    calibrating: "warning",
    fault: "bad flash",
    disconnected: "bad",
  }[systemState] || "neutral";
  setPill(elements.systemStateStatus, formatSystemState({ state: systemState }), systemClass);
  elements.systemStateStatus.title = system.reason ? formatFaultReason(system.reason) : "";
  elements.systemStateDetail.textContent = hardwareConnected
    ? system.reason
      ? formatFaultReason(system.reason)
      : systemState === "running" ? "Run active" : "Controller online"
    : connectionLost ? "Controller connection lost" : "Waiting for controller";

  const overpressureSet = hardware.pressure_latch_set === true;
  setPill(
    elements.overpressureStatus,
    !hardwareConnected
      ? "Pressure unknown"
      : overpressureSet ? "Overpressure fault\nClick to reset" : "Pressure OK",
    hardwareConnected && overpressureSet ? "bad flash" : hardwareConnected ? "ok" : "neutral",
  );
  elements.overpressureStatus.disabled = !hardwareConnected || !overpressureSet;
  elements.overpressureStatus.title = overpressureSet ? "Pulse pressure latch reset" : "";

  setPill(
    elements.servoStatus,
    !hardwareConnected ? "Servo unknown" : hardware.servo_ready ? "Servo OK" : "Servo fault",
    !hardwareConnected ? "neutral" : hardware.servo_ready ? "ok" : "bad",
  );

  setPill(
    elements.arduinoEnableStatus,
    !hardwareConnected ? "Arduino unavailable" : hardware.arduino_enable ? "Arduino enabled" : "Arduino disabled",
    hardwareConnected && hardware.arduino_enable ? "ok" : "neutral",
  );

  setPill(
    elements.dacStatus,
    hardwareConnected ? (hardware.dac_ok ? "DAC OK" : "DAC Fault") : "DAC Unknown",
    hardwareConnected ? (hardware.dac_ok ? "ok" : "bad") : "neutral",
  );
}

function canStart(telemetry) {
  const hardware = telemetry.hardware || {};
  return (
    telemetry.system?.state === "stopped" &&
    telemetry.running !== true &&
    hardware.connected === true &&
    hardware.fresh === true &&
    hardware.protocol_compatible === true &&
    hardware.dac_ok === true &&
    hardware.arduino_enable === true &&
    hardware.servo_ready === true &&
    hardware.pressure_latch_set !== true
  );
}

function setControlsEnabled(enabled) {
  elements.mainEnableButton.disabled = !enabled;
}

function renderTelemetry(telemetry) {
  state.telemetry = telemetry;
  state.connected = true;
  if (telemetry.hardware?.connected === true) {
    state.hadConnection = true;
  }

  renderStatusBadges(telemetry);

  const hardware = telemetry.hardware || {};
  const hardwareConnected =
    hardware.connected === true &&
    hardware.fresh === true &&
    hardware.protocol_compatible === true;
  const control = telemetry.control || {};
  const running = telemetry.running === true;
  const arduinoEnabled = hardware.arduino_enable === true;
  const system = telemetry.system || {};
  const faulted = system.state === "fault";
  const stopping = system.state === "stopping";
  const starting = system.state === "starting";
  const calibrating = system.state === "calibrating";
  const controlsLocked = faulted || stopping || starting || calibrating;
  if (!running || !arduinoEnabled || Date.now() > state.disableConfirmUntilMs) {
    state.disableConfirmUntilMs = 0;
  }
  const disablePending = state.disableConfirmUntilMs > Date.now();
  const waveformAvailable = control.waveform_available === true;
  const waveform = control.waveform || null;
  const waveformMode = control.mode === "waveform";

  if (waveformAvailable || waveformMode || state.activeFlowMode !== "waveform") {
    setActiveFlowMode(waveformMode ? "waveform" : "constant");
  }
  elements.constantModeButton.disabled = running || state.busy || controlsLocked;
  elements.waveformModeButton.disabled = running || state.busy || controlsLocked;
  if (document.activeElement !== elements.flowInput && Number.isFinite(Number(control.flow_ml_s))) {
    elements.flowInput.value = Number(control.flow_ml_s).toFixed(2);
  }
  if (Number.isFinite(Number(control.max_flow_ml_s))) {
    elements.flowInput.max = String(control.max_flow_ml_s);
  }
  const constantFlow = Number(control.flow_ml_s) || 0;
  const constantFlowLimit = Number(control.max_flow_ml_s) || 0;
  const flowPercent = constantFlowLimit > 0
    ? Math.max(0, Math.min(100, constantFlow / constantFlowLimit * 100))
    : 0;
  elements.constantFlowGauge.style.setProperty("--flow-percent", String(flowPercent));
  elements.constantFlowValue.textContent = constantFlow.toFixed(2);
  elements.constantFlowLimit.textContent = `0–${constantFlowLimit.toFixed(2)} mL/s`;
  elements.constantFlowMode.textContent = waveformMode
    ? "Waveform selected"
    : running ? "Running" : "Stopped";
  elements.constantCalibrationState.textContent = hardware.calibration_valid
    ? "Four-point calibration"
    : "Conservative limit";
  elements.constantOutputState.textContent = waveformMode
    ? "Pulsatile mode"
    : running ? "Running" : arduinoEnabled ? "Ready" : "Disabled";
  elements.flowInput.disabled = waveformMode || running || state.busy || !hardwareConnected || controlsLocked;
  elements.waveformFile.disabled = running || state.busy || controlsLocked;
  elements.waveformFileTitle.textContent = state.waveformFileName
    || "Choose a two-column text or CSV file";
  elements.waveformFileHint.textContent = state.waveformFileName
    ? "Click to load new file"
    : "Loading prepares the preview. Transfer occurs on Start.";
  elements.waveformMaxFlowInput.disabled = running || state.busy || !waveformAvailable || controlsLocked;
  elements.waveformPeriodInput.disabled = running || state.busy || !waveformAvailable || controlsLocked;
  elements.clipNegativeFlowInput.disabled = running || state.busy || !waveformAvailable || controlsLocked;
  elements.resetWaveformDefaultsButton.disabled = running || state.busy || !waveformAvailable || controlsLocked;
  const waveformUpdateHz = Number(control.waveform_update_hz) || 250;
  if (document.activeElement !== elements.waveformRateToggle) {
    elements.waveformRateToggle.checked = waveformUpdateHz === 500;
  }
  elements.waveformRate250.classList.toggle("selected", waveformUpdateHz === 250);
  elements.waveformRate500.classList.toggle("selected", waveformUpdateHz === 500);
  elements.waveformRateLabel.textContent = `${waveformUpdateHz} Hz raster`;
  elements.waveformRateToggle.disabled = running || state.busy || controlsLocked;
  if (document.activeElement !== elements.waveformMaxFlowInput && Number.isFinite(Number(waveform?.max_flow_ml_s))) {
    elements.waveformMaxFlowInput.value = Number(waveform.max_flow_ml_s).toFixed(2);
  }
  const waveformFlowLimit = Number(control.max_flow_ml_s);
  if (Number.isFinite(waveformFlowLimit)) {
    elements.waveformMaxFlowInput.removeAttribute("max");
    const exceedsFlowLimit = Number(waveform?.max_flow_ml_s) > waveformFlowLimit;
    const limitName = hardware.calibration_valid
      ? "loaded calibration limit"
      : "conservative uncalibrated limit";
    elements.waveformFlowLimitNote.textContent = exceedsFlowLimit
      ? `Requested peak ${Number(waveform.max_flow_ml_s).toFixed(2)} mL/s exceeds the ${waveformFlowLimit.toFixed(2)} mL/s ${limitName}. The preview and output are clipped to the limit.`
      : `Active limit: ${waveformFlowLimit.toFixed(2)} mL/s (${limitName}). Values above it are clipped automatically.`;
    elements.waveformFlowLimitNote.className = exceedsFlowLimit
      ? "field-note error"
      : "field-note";
  }
  if (document.activeElement !== elements.waveformPeriodInput && Number.isFinite(Number(waveform?.period_ms))) {
    elements.waveformPeriodInput.value = Math.round(Number(waveform.period_ms));
  }
  const rasterPeriodMs = Number(waveform?.raster_period_ms)
    || 1000 / Number(control.waveform_update_hz || 250);
  elements.waveformPeriodInput.min = String(rasterPeriodMs);
  elements.waveformPeriodInput.step = String(rasterPeriodMs);
  if (waveform) {
    elements.clipNegativeFlowInput.checked = waveform.clip_negative_flow === true;
  } else {
    elements.clipNegativeFlowInput.checked = false;
  }
  const triggerHighMs = Number(control.trigger_high_ms);
  if (document.activeElement !== elements.triggerHighMsInput && Number.isFinite(triggerHighMs)) {
    elements.triggerHighMsInput.value = triggerHighMs.toFixed(1);
  }
  const triggerRasterMs = 1000 / waveformUpdateHz;
  elements.triggerHighMsInput.min = String(triggerRasterMs);
  elements.triggerHighMsInput.step = String(triggerRasterMs);
  elements.triggerHighMsInput.disabled = running || state.busy || controlsLocked;
  elements.waveformStatus.textContent = waveformAvailable && waveform
    ? `${waveform.sample_count} samples at ${waveform.update_hz} Hz, ${Number(waveform.period_ms).toFixed(0)} ms, `
      + `flow scale ${Number(waveform.flow_scale).toFixed(2)}, period scale ${Number(waveform.period_scale).toFixed(2)}, `
      + `${waveform.clipped_sample_count} clipped, `
      + `stroke volume ${Number(waveform.total_stroke_volume_ml).toFixed(2)} mL`
    : "No waveform loaded.";
  elements.waveformPeakValue.textContent = waveform
    ? `${Number(waveform.applied_max_flow_ml_s ?? waveform.max_flow_ml_s).toFixed(2)} mL/s`
    : "—";
  elements.waveformDurationValue.textContent = waveform
    ? `${Number(waveform.period_ms).toFixed(0)} ms`
    : "—";
  elements.waveformSamplesValue.textContent = waveform
    ? `${Number(waveform.sample_count)} @ ${Number(waveform.update_hz)} Hz`
    : "—";
  elements.waveformVolumeValue.textContent = waveform
    ? `${Number(waveform.total_stroke_volume_ml).toFixed(2)} mL`
    : "—";
  const clippedSamples = Number(waveform?.clipped_sample_count) || 0;
  const negativeClippedSamples = Number(waveform?.negative_clipped_sample_count) || 0;
  elements.waveformClippingValue.textContent = clippedSamples || negativeClippedSamples
    ? `${clippedSamples + negativeClippedSamples} clipped samples`
    : "No clipping";
  elements.waveformClippingValue.className = clippedSamples || negativeClippedSamples
    ? "clip-status warning"
    : "clip-status";
  const waveSamples = Number(hardware.wave_samples) || Number(waveform?.sample_count) || 0;
  const waveIndex = Number(hardware.wave_index) || 0;
  elements.waveformPositionLabel.textContent = running && waveformMode && waveSamples > 0
    ? `Sample ${Math.min(waveIndex + 1, waveSamples)} of ${waveSamples}`
    : waveform ? "Ready to transfer" : "Preview";
  if (waveform?.default_period_rounded) {
    elements.waveformRasterNote.hidden = false;
    elements.waveformRasterNote.textContent =
      `File duration ${Number(waveform.source_duration_ms).toFixed(2)} ms rounded to `
      + `${Number(waveform.default_period_ms).toFixed(0)} ms for the ${waveform.update_hz} Hz raster. `
      + `Period must be a multiple of ${Number(waveform.raster_period_ms).toFixed(2)} ms.`;
  } else if (waveform) {
    elements.waveformRasterNote.hidden = false;
    elements.waveformRasterNote.textContent =
      `Period must be a multiple of ${Number(waveform.raster_period_ms).toFixed(2)} ms.`;
  } else {
    elements.waveformRasterNote.hidden = true;
    elements.waveformRasterNote.textContent = "";
  }
  renderCalibration(hardware, telemetry);
  renderWaveformIfChanged(waveform, hardware, running && waveformMode);

  renderTimingDiagnostics(hardware, running && waveformMode);
  renderControllerDiagnostics(hardware);

  elements.mainEnableButton.textContent = stopping
    ? "Stopping"
    : faulted
    ? "Fault Latched"
    : disablePending
    ? "Confirm Disable"
    : arduinoEnabled ? "Disable" : "Enable";
  elements.mainEnableButton.className = controlsLocked
    ? "big-button disabled"
    : arduinoEnabled ? "big-button danger" : "big-button enable";
  elements.clearFaultButton.disabled =
    !faulted || state.busy || !hardwareConnected
    || (hardware.fault_clear_ready !== true && hardware.fault_code !== "dac_communication");
  elements.clearFaultButton.title = faulted && hardware.fault_clear_ready !== true
    ? "Interlocks must remain safe for 250 ms before clearing"
    : "";

  elements.mainStartButton.textContent = stopping
    ? "Stopping"
    : calibrating
    ? "Stop Calibration"
    : starting
    ? "Stop Ramp"
    : running ? "Stop" : "Start";
  elements.mainStartButton.className = stopping
    ? "big-button disabled"
    : running
    ? "big-button danger"
    : canStart(telemetry)
      ? "big-button start"
      : "big-button disabled";
  elements.mainStartButton.disabled = stopping || (!running && !canStart(telemetry));

  setControlsEnabled(hardwareConnected && !state.busy && !controlsLocked);

  if (stopping && !state.busy) {
    elements.controlMessage.textContent = `Controlled stop: ${formatFaultReason(system.reason)}`;
    elements.controlMessage.className = "message error";
  } else if (faulted && !state.busy) {
    elements.controlMessage.textContent = `Fault: ${formatFaultReason(system.reason)}`;
    elements.controlMessage.className = "message error";
  }
}

function drawCalibrationCurve(flows, validMask) {
  const canvas = elements.calibrationCanvas;
  const context = canvas.getContext("2d");
  const voltages = [2.5, 5, 7.5, 10];
  const measuredPoints = voltages
    .map((voltage, index) => ({ voltage, flow: Number(flows[index]) || 0, index }))
    .filter(({ index }) => (validMask & (1 << index)) !== 0);
  const width = canvas.width;
  const height = canvas.height;
  const plot = { left: 58, right: width - 22, top: 24, bottom: height - 44 };
  const maxMeasuredFlow = Math.max(0, ...measuredPoints.map(({ flow }) => flow));
  const yMax = Math.max(0.1, Math.ceil(maxMeasuredFlow * 12) / 10);
  const xPosition = (voltage) => plot.left + voltage / 10 * (plot.right - plot.left);
  const yPosition = (flow) => plot.bottom - flow / yMax * (plot.bottom - plot.top);

  context.clearRect(0, 0, width, height);
  context.font = "11px Inter, ui-sans-serif, sans-serif";
  context.lineWidth = 1;
  context.strokeStyle = "#e4e9ee";
  context.fillStyle = "#748196";
  context.textAlign = "right";
  for (let tick = 0; tick <= 4; tick += 1) {
    const flow = yMax * tick / 4;
    const y = yPosition(flow);
    context.beginPath();
    context.moveTo(plot.left, y);
    context.lineTo(plot.right, y);
    context.stroke();
    context.fillText(flow.toFixed(yMax < 1 ? 2 : 1), plot.left - 8, y + 4);
  }

  context.textAlign = "center";
  voltages.forEach((voltage) => {
    const x = xPosition(voltage);
    context.beginPath();
    context.moveTo(x, plot.top);
    context.lineTo(x, plot.bottom);
    context.stroke();
    context.fillText(`${voltage.toFixed(1)} V`, x, plot.bottom + 20);
  });

  context.strokeStyle = "#157b82";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.beginPath();
  context.moveTo(xPosition(0), yPosition(0));
  measuredPoints.forEach(({ voltage, flow }) => context.lineTo(xPosition(voltage), yPosition(flow)));
  context.stroke();

  measuredPoints.forEach(({ voltage, flow }) => {
    context.beginPath();
    context.arc(xPosition(voltage), yPosition(flow), 5, 0, Math.PI * 2);
    context.fillStyle = "#ffffff";
    context.fill();
    context.strokeStyle = "#157b82";
    context.lineWidth = 3;
    context.stroke();
  });

  const count = measuredPoints.length;
  elements.calibrationPlotNote.textContent = count === 0
    ? "No measured points yet."
    : count === 4 ? "Four-point calibration curve complete." : `${count} of 4 points measured.`;
}

function renderCalibration(hardware, telemetry) {
  const voltages = [2.5, 5, 7.5, 10];
  const flows = hardware.calibration_flows_ml_s || [0, 0, 0, 0];
  const validMask = Number(hardware.calibration_valid_mask) || 0;
  const active = hardware.run_mode === "calibration" || hardware.calibration_active === true;
  const complete = hardware.calibration_complete === true;
  const hardwareTargetVoltage = Number(hardware.calibration_target_mv) / 1000;
  if ((active || complete) && voltages.includes(hardwareTargetVoltage)) {
    state.calibrationSelectedVoltage = hardwareTargetVoltage;
  }
  const targetVoltage = state.calibrationSelectedVoltage;
  const renderKey = JSON.stringify({
    validMask,
    flows: voltages.map((_, index) => Number(flows[index]) || 0),
    targetVoltage,
    hardwareTargetVoltage: active ? hardwareTargetVoltage : 0,
    active,
    complete,
    busy: state.busy,
  });

  if (state.calibrationRenderKey !== renderKey) {
    elements.calibrationPoints.replaceChildren(...voltages.map((voltage, index) => {
      const button = document.createElement("button");
      const measured = (validMask & (1 << index)) !== 0;
      button.type = "button";
      button.dataset.calibrationVoltage = String(voltage);
      button.className = [
        "calibration-point",
        voltage === targetVoltage ? "selected" : "",
        active && voltage === hardwareTargetVoltage ? "current" : "",
        measured ? "complete" : "",
      ].filter(Boolean).join(" ");
      button.disabled = active || complete || state.busy;
      button.setAttribute("aria-pressed", String(voltage === targetVoltage));
      const voltageLabel = document.createElement("strong");
      voltageLabel.textContent = `${voltage.toFixed(1)} V`;
      const resultLabel = document.createElement("span");
      resultLabel.textContent = measured
        ? `${Number(flows[index]).toFixed(4)} mL/s`
        : "Not measured";
      button.append(voltageLabel, resultLabel);
      return button;
    }));
    drawCalibrationCurve(flows, validMask);
    state.calibrationRenderKey = renderKey;
  }

  if (active) {
    elements.calibrationStatus.textContent = `Running: ${formatFaultReason(hardware.calibration_stage)}`;
  } else if (complete) {
    elements.calibrationStatus.textContent = "Measurement required";
  } else if (hardware.calibration_valid === true) {
    elements.calibrationStatus.textContent = "4-point calibration active";
  } else {
    elements.calibrationStatus.textContent = `${validMask.toString(2).split("1").length - 1}/4 points`;
  }

  elements.calibrationStartButton.textContent = `Start ${targetVoltage.toFixed(1)} V point`;
  elements.calibrationStartButton.disabled =
    active || complete || state.busy
    || !elements.calibrationConfirmInput.checked || !canStart(telemetry);
  elements.calibrationConfirmInput.disabled = active || complete || state.busy;
  elements.calibrationFile.disabled = active || telemetry.running === true || state.busy
    || telemetry.hardware?.connected !== true || telemetry.hardware?.fresh !== true;
  elements.calibrationFileTitle.textContent = state.calibrationFileName
    || "Load four measured flow values";
  elements.calibrationFileHint.textContent = state.calibrationFileName
    ? "Click to load new file"
    : "Points must correspond to 2.5, 5, 7.5, and 10 V.";
  elements.calibrationMeasurement.hidden = !complete;
  elements.calibrationSaveButton.disabled = state.busy || !complete;
  elements.calibrationExportButton.disabled = state.busy || hardware.calibration_valid !== true;
  elements.calibrationClearButton.disabled = state.busy || active || validMask === 0;
  if (Date.now() > state.calibrationClearConfirmUntilMs) {
    state.calibrationClearConfirmUntilMs = 0;
    elements.calibrationClearButton.textContent = "Clear Calibration";
  }
  if (complete && hardwareTargetVoltage > 0) {
    state.calibrationVoltage = hardwareTargetVoltage;
  }
}

function renderDiagnosticRows(container, rows) {
  container.replaceChildren(...rows.map(([label, value]) => {
    const row = document.createElement("div");
    const labelElement = document.createElement("span");
    const valueElement = document.createElement("strong");
    labelElement.textContent = label;
    valueElement.textContent = String(value);
    valueElement.title = String(value);
    row.append(labelElement, valueElement);
    return row;
  }));
}

function renderControllerDiagnostics(hardware) {
  if (hardware.connected !== true || hardware.fresh !== true) {
    setPill(elements.watchdogStatus, "Watchdog Unknown", "neutral");
    renderDiagnosticRows(elements.hardwareDiagnostics, [["Status", "No current hardware data"]]);
    renderDiagnosticRows(elements.runDiagnostics, [["Status", "No current run data"]]);
    renderDiagnosticRows(elements.controllerDiagnostics, [["Status", "No current controller data"]]);
    return;
  }
  setPill(
    elements.watchdogStatus,
    hardware.watchdog_ok ? "Watchdog Active" : "Watchdog Fault",
    hardware.watchdog_ok ? "ok" : "bad",
  );
  renderDiagnosticRows(elements.hardwareDiagnostics, [
    ["Serial port", hardware.port || "Unknown"],
    ["Protocol", `v${Number(hardware.protocol_version) || 0}`],
    ["Telemetry age", `${Number(hardware.last_seen_ms) || 0} ms`],
    ["DAC output", `${Number(hardware.dac_voltage || 0).toFixed(3)} V`],
    ["DAC data / range", `${Number(hardware.dac_data) || 0} / ${hardware.dac_range || "unknown"}`],
    ["Pins 9 / 10 / 11 / 12", `${Number(hardware.pin9_pressure_latch_input)}/${Number(hardware.pin10_servo_ready_input)}/${Number(hardware.pin11_arduino_enable_output)}/${Number(hardware.pin12_latch_reset_output)}`],
  ]);
  renderDiagnosticRows(elements.runDiagnostics, [
    ["Run mode", formatFaultReason(hardware.run_mode)],
    ["Ramp up / down", `${hardware.ramp_up_active ? "Active" : "Idle"} / ${hardware.ramp_down_active ? "Active" : "Idle"}`],
    ["Waveform", `${hardware.wave_loaded ? "Loaded" : "Not loaded"} · ${hardware.wave_running ? "Running" : "Stopped"}`],
    ["Sample position", `${Number(hardware.wave_index) || 0} / ${Number(hardware.wave_samples) || 0} @ ${Number(hardware.wave_rate_hz) || 0} Hz`],
    ["Trigger output", `${hardware.trigger_active ? "High" : "Low"} · ${Number(hardware.trigger_high_ms || 0).toFixed(1)} ms`],
    ["Trigger count", Number(hardware.trigger_count) || 0],
  ]);
  renderDiagnosticRows(elements.controllerDiagnostics, [
    ["Uptime", formatDuration(Number(hardware.uptime_ms) || 0)],
    ["Watchdog timeout", `${Number(hardware.watchdog_timeout_ms) || 0} ms`],
    ["Heartbeat age / max", `${Number(hardware.heartbeat_age_ms) || 0} / ${Number(hardware.heartbeat_max_gap_ms) || 0} ms`],
    ["Fault / controlled stop", `${formatFaultReason(hardware.fault_code || "none")} / ${formatFaultReason(hardware.controlled_stop_code || "none")}`],
    ["Fault clear dwell", `${Number(hardware.fault_clear_safe_ms) || 0} ms · ${hardware.fault_clear_ready ? "Ready" : "Not ready"}`],
    ["Reset cause", formatFaultReason(hardware.reset_cause)],
    ["Retained fault", `${formatFaultReason(hardware.retained_last_fault)} · ${Number(hardware.retained_fault_count) || 0} total`],
    ["Ramp updates / missed", `${Number(hardware.ramp_update_count) || 0} / ${Number(hardware.ramp_missed_deadline_count) || 0}`],
    ["Ramp max lateness", `${Number(hardware.ramp_max_lateness_us) || 0} us`],
    ["Serial / protocol errors", `${Number(hardware.serial_overflow_count) || 0} / ${Number(hardware.protocol_error_count) || 0}`],
    ["DAC write failures", Number(hardware.dac_write_failure_count) || 0],
  ]);
}

function formatDuration(milliseconds) {
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${hours}h ${minutes}m ${seconds}s`;
}

function renderTimingDiagnostics(hardware, active) {
  if (
    hardware.connected !== true ||
    hardware.fresh !== true ||
    hardware.protocol_compatible !== true
  ) {
    setPill(elements.timingStatus, "Timing Unknown", "neutral");
    renderDiagnosticRows(elements.timingDetails, [["Status", "No current timing data"]]);
    return;
  }

  const faultCode = hardware.fault_code;
  const controlledStopCode = hardware.controlled_stop_code;
  const warnings = Number(hardware.timing_warning_count) || 0;
  const overruns = Number(hardware.timing_overrun_count) || 0;
  const timingFault = faultCode === "timing_overrun" || controlledStopCode === "timing_overrun";

  if (timingFault || overruns > 0) {
    setPill(elements.timingStatus, "Timing Overrun", "bad flash");
  } else if (warnings > 0) {
    setPill(elements.timingStatus, "Timing Warning", "warning");
  } else if (active) {
    setPill(elements.timingStatus, "Timing Active", "ok");
  } else {
    setPill(elements.timingStatus, "Timing Idle", "neutral");
  }

  const lateness = Number(hardware.timing_lateness_us) || 0;
  const maxLateness = Number(hardware.timing_max_lateness_us) || 0;
  const writeTime = Number(hardware.timing_write_us) || 0;
  const maxWriteTime = Number(hardware.timing_max_write_us) || 0;
  renderDiagnosticRows(elements.timingDetails, [
    ["Scheduler lateness", `${lateness} us`],
    ["Maximum lateness", `${maxLateness} us`],
    ["DAC write", `${writeTime} us`],
    ["Maximum DAC write", `${maxWriteTime} us`],
    ["Warnings", warnings],
    ["Overruns", overruns],
  ]);
}

function renderDisconnected(error) {
  state.connected = false;
  setPill(
    elements.connectionStatus,
    state.hadConnection ? "Connection Lost" : "Disconnected",
    state.hadConnection ? "bad flash" : "bad",
  );
  setPill(elements.systemStateStatus, "Disconnected", state.hadConnection ? "bad flash" : "bad");
  elements.systemStateStatus.title = error?.message || "connection_lost";
  elements.systemStateDetail.textContent = state.hadConnection
    ? "Controller connection lost"
    : "Waiting for controller";
  setPill(elements.overpressureStatus, "Pressure unknown", "neutral");
  setPill(elements.servoStatus, "Servo unknown", "neutral");
  setPill(elements.arduinoEnableStatus, "Arduino unavailable", "neutral");
  setPill(elements.dacStatus, "DAC unknown", "neutral");
  elements.mainStartButton.disabled = true;
  elements.clearFaultButton.disabled = true;
  elements.flowInput.disabled = true;
  elements.waveformFile.disabled = true;
  elements.waveformMaxFlowInput.disabled = true;
  elements.waveformPeriodInput.disabled = true;
  elements.clipNegativeFlowInput.disabled = true;
  elements.resetWaveformDefaultsButton.disabled = true;
  elements.triggerHighMsInput.disabled = true;
  elements.waveformRateToggle.disabled = true;
  elements.waveformRasterNote.hidden = true;
  elements.waveformFlowLimitNote.textContent = "Flow limit unavailable while disconnected.";
  elements.waveformFlowLimitNote.className = "field-note";
  elements.calibrationStatus.textContent = "Not configured";
  elements.calibrationFile.disabled = true;
  elements.calibrationStartButton.disabled = true;
  elements.calibrationSaveButton.disabled = true;
  elements.calibrationExportButton.disabled = true;
  elements.calibrationClearButton.disabled = true;
  elements.calibrationMeasurement.hidden = true;
  setPill(elements.timingStatus, "Timing Unknown", "neutral");
  renderDiagnosticRows(elements.timingDetails, [["Status", "No current timing data"]]);
  renderDiagnosticRows(elements.hardwareDiagnostics, [["Status", "No current hardware data"]]);
  renderDiagnosticRows(elements.runDiagnostics, [["Status", "No current run data"]]);
  setPill(elements.watchdogStatus, "Watchdog Unknown", "neutral");
  renderDiagnosticRows(elements.controllerDiagnostics, [["Status", "No current controller data"]]);
  elements.constantFlowGauge.style.setProperty("--flow-percent", "0");
  elements.constantFlowValue.textContent = "0.00";
  elements.constantFlowMode.textContent = "Disconnected";
  elements.constantOutputState.textContent = "Unavailable";
  elements.waveformPeakValue.textContent = "—";
  elements.waveformDurationValue.textContent = "—";
  elements.waveformSamplesValue.textContent = "—";
  elements.waveformVolumeValue.textContent = "—";
  elements.waveformPositionLabel.textContent = "Preview";
  elements.waveformClippingValue.textContent = "No clipping";
  elements.waveformClippingValue.className = "clip-status";
  state.waveformRenderKey = null;
  drawWaveform(null, {}, false);
  setControlsEnabled(false);
}

function renderWaveformIfChanged(waveform, hardware, active) {
  const renderKey = waveform
    ? [
        waveform.sample_count,
        waveform.max_flow_ml_s,
        waveform.applied_max_flow_ml_s,
        waveform.flow_limit_ml_s,
        waveform.period_ms,
        waveform.clip_negative_flow,
        waveform.clipped_sample_count,
        waveform.negative_clipped_sample_count,
        active ? Number(hardware.wave_index) || 0 : -1,
      ].join(":")
    : "empty";
  if (renderKey === state.waveformRenderKey) {
    return;
  }
  state.waveformRenderKey = renderKey;
  drawWaveform(waveform, hardware, active);
}

function drawWaveform(waveform, hardware = {}, active = false) {
  const canvas = elements.waveformCanvas;
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#fbfcfd";
  context.fillRect(0, 0, width, height);

  const paddingLeft = 62;
  const paddingRight = 22;
  const paddingTop = 24;
  const paddingBottom = 38;
  const plotWidth = width - paddingLeft - paddingRight;
  const plotHeight = height - paddingTop - paddingBottom;
  const preview = waveform?.preview || [];
  const previewFlows = preview.map((point) => Number(point.flow_ml_s ?? point.flow) || 0);
  const minimumFlow = previewFlows.length ? Math.min(...previewFlows) : 0;
  const maximumFlow = previewFlows.length ? Math.max(...previewFlows) : 0;
  const hasReverseFlow = minimumFlow < -1e-9;
  const plotLimit = Math.max(
    Math.abs(minimumFlow),
    Math.abs(maximumFlow),
    Math.abs(Number(waveform?.max_flow_ml_s) || 0),
    0.001,
  );
  const plotMinimum = hasReverseFlow ? -plotLimit : 0;
  const plotMaximum = plotLimit;
  const yFor = (flow) => paddingTop
    + (1 - (flow - plotMinimum) / (plotMaximum - plotMinimum)) * plotHeight;

  context.strokeStyle = "#e2e7ec";
  context.lineWidth = 1;
  context.font = "11px system-ui, sans-serif";
  context.textBaseline = "middle";
  for (let index = 0; index <= 4; index += 1) {
    const flow = plotMaximum - (plotMaximum - plotMinimum) * index / 4;
    const y = yFor(flow);
    context.beginPath();
    context.moveTo(paddingLeft, y);
    context.lineTo(width - paddingRight, y);
    context.stroke();
    context.fillStyle = "#758295";
    context.textAlign = "right";
    context.fillText(formatFlow(flow), paddingLeft - 10, y);
  }
  for (let index = 0; index <= 4; index += 1) {
    const x = paddingLeft + plotWidth * index / 4;
    context.beginPath();
    context.moveTo(x, paddingTop);
    context.lineTo(x, height - paddingBottom);
    context.stroke();
  }

  if (!preview.length) {
    context.fillStyle = "#6f7d8d";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.font = "600 14px system-ui, sans-serif";
    context.fillText("Load a waveform to preview the command", width / 2, height / 2 - 8);
    context.fillStyle = "#98a3af";
    context.font = "11px system-ui, sans-serif";
    context.fillText("Two-column text or CSV: time and normalized flow", width / 2, height / 2 + 14);
    return;
  }

  const duration = Math.max(Number(waveform.duration_s) || 1, preview[preview.length - 1].time_s || 1);
  const xFor = (timeS) => paddingLeft + (timeS / duration) * plotWidth;
  const zeroY = yFor(0);
  const waveformPath = new Path2D();
  preview.forEach((point, index) => {
    const x = xFor(point.time_s);
    const flow = Math.max(plotMinimum, Math.min(plotMaximum, Number(point.flow_ml_s ?? point.flow) || 0));
    const y = yFor(flow);
    if (index === 0) {
      waveformPath.moveTo(x, y);
    } else {
      waveformPath.lineTo(x, y);
    }
  });

  const fillPath = new Path2D(waveformPath);
  fillPath.lineTo(xFor(preview[preview.length - 1].time_s), zeroY);
  fillPath.lineTo(xFor(preview[0].time_s), zeroY);
  fillPath.closePath();
  const fillGradient = context.createLinearGradient(0, paddingTop, 0, height - paddingBottom);
  fillGradient.addColorStop(0, "rgba(40, 163, 166, 0.26)");
  fillGradient.addColorStop(1, "rgba(40, 163, 166, 0.02)");
  context.fillStyle = fillGradient;
  context.fill(fillPath);

  context.strokeStyle = "#16868b";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.stroke(waveformPath);

  context.fillStyle = "#b42f3b";
  preview.filter((point) => point.clipped).forEach((point) => {
    const x = xFor(point.time_s);
    const flow = Math.max(plotMinimum, Math.min(plotMaximum, Number(point.flow_ml_s ?? point.flow) || 0));
    context.beginPath();
    context.arc(x, yFor(flow), 4, 0, Math.PI * 2);
    context.fill();
  });

  context.strokeStyle = "#d29425";
  context.lineWidth = 2;
  context.setLineDash([5, 5]);
  context.beginPath();
  context.moveTo(paddingLeft, paddingTop);
  context.lineTo(paddingLeft, height - paddingBottom);
  context.stroke();
  context.setLineDash([]);

  const waveSamples = Number(hardware.wave_samples) || Number(waveform.sample_count) || 0;
  if (active && waveSamples > 0) {
    const waveIndex = Math.max(0, Math.min(Number(hardware.wave_index) || 0, waveSamples - 1));
    const playheadX = paddingLeft + waveIndex / Math.max(1, waveSamples - 1) * plotWidth;
    context.strokeStyle = "#183a4d";
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(playheadX, paddingTop);
    context.lineTo(playheadX, height - paddingBottom);
    context.stroke();
    context.fillStyle = "#183a4d";
    context.beginPath();
    context.arc(playheadX, paddingTop + 5, 5, 0, Math.PI * 2);
    context.fill();
  }

  context.fillStyle = "#758295";
  context.font = "11px system-ui, sans-serif";
  context.textAlign = "left";
  context.textBaseline = "alphabetic";
  context.fillText("0 s", paddingLeft, height - 12);
  context.textAlign = "center";
  context.fillText(`${(duration / 2).toFixed(2)} s`, paddingLeft + plotWidth / 2, height - 12);
  context.textAlign = "right";
  context.fillText(`${duration.toFixed(2)} s`, width - paddingRight, height - 12);
}

function formatFlow(flow) {
  const value = Number(flow);
  if (!Number.isFinite(value)) {
    return "0";
  }
  return Math.abs(value) >= 10 ? value.toFixed(0) : value.toFixed(2);
}

async function refreshTelemetry() {
  const requestId = ++state.refreshRequestId;
  try {
    const telemetry = await api("/api/telemetry");
    if (state.busy || requestId !== state.refreshRequestId) {
      return;
    }
    renderTelemetry(telemetry);
  } catch (error) {
    renderDisconnected(error);
  }
}

async function sendHeartbeat() {
  if (state.heartbeatInFlight) {
    return;
  }
  state.heartbeatInFlight = true;
  try {
    await postJson("/api/heartbeat");
  } catch {
    state.connected = false;
  } finally {
    state.heartbeatInFlight = false;
  }
}

async function updateControl() {
  const flowMlS = Number(elements.flowInput.value);
  state.busy = true;
  try {
    const payload = await postJson("/api/control", {
      mode: "constant",
      flow_ml_s: flowMlS,
    });
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Constant flow set.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function setMode(mode) {
  setActiveFlowMode(mode);
  state.busy = true;
  try {
    const payload = await postJson("/api/control", { mode });
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = mode === "waveform" ? "Waveform mode selected." : "Constant flow mode selected.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function loadWaveform() {
  const file = elements.waveformFile.files[0];
  if (!file) {
    return;
  }

  state.busy = true;
  try {
    const text = await file.text();
    const payload = await postJson("/api/waveform", { text });
    state.waveformFileName = file.name;
    state.waveformRenderKey = null;
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Waveform file loaded. It will send to Arduino on Start.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    elements.waveformFile.value = "";
    state.busy = false;
  }
}

async function updateWaveformSettings() {
  const maxFlow = Number(elements.waveformMaxFlowInput.value);
  const periodMs = Number(elements.waveformPeriodInput.value);
  state.busy = true;
  try {
    const payload = await postJson("/api/control", {
      waveform_max_flow_ml_s: maxFlow,
      waveform_period_ms: periodMs,
    });
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Waveform settings applied.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function updateNegativeFlowClipping() {
  state.busy = true;
  try {
    const payload = await postJson("/api/control", {
      clip_negative_flow: elements.clipNegativeFlowInput.checked,
    });
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Negative-flow clipping updated.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function updateWaveformRate() {
  const updateHz = elements.waveformRateToggle.checked ? 500 : 250;
  state.busy = true;
  try {
    const payload = await postJson("/api/control", { waveform_update_hz: updateHz });
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "System raster updated.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function resetWaveformDefaults() {
  state.busy = true;
  try {
    const payload = await postJson("/api/control", { reset_waveform_scaling: true });
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Waveform settings reset to default.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function updateTrigger() {
  const triggerHighMs = Number(elements.triggerHighMsInput.value);
  state.busy = true;
  try {
    const payload = await postJson("/api/control", { trigger_high_ms: triggerHighMs });
    renderTelemetry(payload.telemetry);
    const appliedMs = Number(payload.telemetry.control?.trigger_high_ms);
    elements.controlMessage.textContent = Number.isFinite(appliedMs)
      ? `Trigger timing aligned to the raster at ${appliedMs.toFixed(1)} ms.`
      : "Trigger timing applied.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function setArduinoEnable(enabled, sourceElement = elements.controlMessage) {
  state.busy = true;
  state.refreshRequestId += 1;
  state.disableConfirmUntilMs = 0;
  try {
    const response = await postJson("/api/hardware", { arduino_enable: enabled });
    renderTelemetry(response.telemetry);
    sourceElement.textContent = response.telemetry.hardware?.arduino_enable
      ? "Arduino enable set."
      : "Arduino enable cleared.";
    sourceElement.className = "message ok";
  } catch (error) {
    sourceElement.textContent = error.message;
    sourceElement.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function clearFault() {
  state.busy = true;
  try {
    const payload = await postJson("/api/clear-fault");
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Fault clear requested.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function loadCalibrationFile() {
  const file = elements.calibrationFile.files[0];
  if (!file) {
    return;
  }

  state.busy = true;
  try {
    const flowsMlS = parseCalibrationFile(await file.text());
    const payload = await postJson("/api/calibration/load", { flows_ml_s: flowsMlS });
    state.calibrationFileName = file.name;
    state.calibrationRenderKey = null;
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = `Calibration loaded from ${file.name}.`;
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    elements.calibrationFile.value = "";
    state.busy = false;
  }
}

function exportCalibrationFile() {
  const hardware = state.telemetry?.hardware || {};
  if (hardware.calibration_valid !== true) {
    return;
  }
  try {
    const contents = serializeCalibrationFile(hardware.calibration_flows_ml_s);
    const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `cardiopump-calibration-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
    elements.controlMessage.textContent = "Calibration file saved.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
  }
}

async function startCalibration() {
  const voltage = state.calibrationSelectedVoltage;
  if (![2.5, 5, 7.5, 10].includes(voltage) || !elements.calibrationConfirmInput.checked) {
    return;
  }

  state.busy = true;
  state.calibrationVoltage = voltage;
  try {
    const payload = await postJson("/api/calibration/start", { voltage });
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent =
      `Calibration at ${voltage.toFixed(1)} V started; the Arduino owns the timed run.`;
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function saveCalibrationMeasurement() {
  const value = Number(elements.calibrationMeasurementValue.value);
  const voltage = state.calibrationVoltage
    || Number(state.telemetry?.hardware?.calibration_target_mv) / 1000;
  if (!Number.isFinite(value) || value <= 0 || !Number.isFinite(voltage)) {
    elements.controlMessage.textContent = "Enter a positive calibration measurement.";
    elements.controlMessage.className = "message error";
    return;
  }

  state.busy = true;
  try {
    const measurementType = elements.calibrationMeasurementType.value;
    const payload = await postJson("/api/calibration/result", {
      voltage,
      [measurementType]: value,
    });
    elements.calibrationMeasurementValue.value = "";
    elements.calibrationConfirmInput.checked = false;
    state.calibrationVoltage = null;
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Calibration point saved in the Arduino.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function clearCalibration() {
  if (Date.now() > state.calibrationClearConfirmUntilMs) {
    state.calibrationClearConfirmUntilMs = Date.now() + DISABLE_CONFIRM_WINDOW_MS;
    elements.calibrationClearButton.textContent = "Confirm Clear Calibration";
    return;
  }
  state.calibrationClearConfirmUntilMs = 0;
  state.busy = true;
  try {
    const payload = await postJson("/api/calibration/clear");
    elements.calibrationClearButton.textContent = "Clear Calibration";
    elements.calibrationConfirmInput.checked = false;
    state.calibrationSelectedVoltage = 2.5;
    state.calibrationFileName = null;
    state.calibrationRenderKey = null;
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Arduino calibration cleared.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function toggleMainEnable() {
  if (["fault", "stopping"].includes(state.telemetry?.system?.state)) {
    return;
  }
  const running = state.telemetry?.running === true;
  const enabled = state.telemetry?.hardware?.arduino_enable === true;
  if (enabled && running) {
    if (Date.now() > state.disableConfirmUntilMs) {
      state.disableConfirmUntilMs = Date.now() + DISABLE_CONFIRM_WINDOW_MS;
      elements.controlMessage.textContent =
        "Disabling while running may damage the pump. Click Confirm Disable to proceed.";
      elements.controlMessage.className = "message error";
      renderTelemetry(state.telemetry);
      return;
    }
    state.disableConfirmUntilMs = 0;
  }
  await setArduinoEnable(!enabled, elements.controlMessage);
}

async function toggleStartStop() {
  state.busy = true;
  try {
    const path = state.telemetry?.running ? "/api/stop" : "/api/start";
    const payload = await postJson(path);
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = payload.telemetry.running ? "Pump started." : "Pump stopped.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

async function resetPressureLatch() {
  state.busy = true;
  try {
    const payload = await postJson("/api/reset-overpressure");
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Pressure latch reset pulse sent.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
    state.busy = false;
  }
}

function wireTabKeyboard(buttons) {
  const tabButtons = Array.from(buttons);
  tabButtons.forEach((button, index) => {
    button.addEventListener("keydown", (event) => {
      let targetIndex = null;
      if (["ArrowRight", "ArrowDown"].includes(event.key)) {
        targetIndex = (index + 1) % tabButtons.length;
      } else if (["ArrowLeft", "ArrowUp"].includes(event.key)) {
        targetIndex = (index - 1 + tabButtons.length) % tabButtons.length;
      } else if (event.key === "Home") {
        targetIndex = 0;
      } else if (event.key === "End") {
        targetIndex = tabButtons.length - 1;
      }
      if (targetIndex === null) {
        return;
      }
      event.preventDefault();
      tabButtons[targetIndex].focus();
      tabButtons[targetIndex].click();
    });
  });
}

elements.flowInput.addEventListener("change", updateControl);
elements.flowTabButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const mode = button.dataset.flowMode;
    if (mode === "waveform" && state.telemetry?.control?.waveform_available !== true) {
      setActiveFlowMode(mode);
      elements.controlMessage.textContent = "Select a waveform file.";
      elements.controlMessage.className = "message";
      return;
    }
    setMode(mode);
  });
});
elements.waveformFile.addEventListener("change", loadWaveform);
elements.waveformMaxFlowInput.addEventListener("change", updateWaveformSettings);
elements.waveformPeriodInput.addEventListener("change", updateWaveformSettings);
elements.clipNegativeFlowInput.addEventListener("change", updateNegativeFlowClipping);
elements.waveformRateToggle.addEventListener("change", updateWaveformRate);
elements.resetWaveformDefaultsButton.addEventListener("click", resetWaveformDefaults);
elements.triggerHighMsInput.addEventListener("change", updateTrigger);
elements.mainEnableButton.addEventListener("click", toggleMainEnable);
elements.mainStartButton.addEventListener("click", toggleStartStop);
elements.overpressureStatus.addEventListener("click", resetPressureLatch);
elements.clearFaultButton.addEventListener("click", clearFault);
elements.calibrationFile.addEventListener("change", loadCalibrationFile);
elements.calibrationPoints.addEventListener("click", (event) => {
  const button = event.target.closest("[data-calibration-voltage]");
  if (!button || button.disabled) {
    return;
  }
  state.calibrationSelectedVoltage = Number(button.dataset.calibrationVoltage);
  state.calibrationVoltage = null;
  state.calibrationRenderKey = null;
  elements.calibrationConfirmInput.checked = false;
  if (state.telemetry) {
    renderCalibration(state.telemetry.hardware || {}, state.telemetry);
  }
});
elements.calibrationConfirmInput.addEventListener("change", () => {
  if (state.telemetry) {
    renderCalibration(state.telemetry.hardware || {}, state.telemetry);
  }
});
elements.calibrationStartButton.addEventListener("click", startCalibration);
elements.calibrationSaveButton.addEventListener("click", saveCalibrationMeasurement);
elements.calibrationExportButton.addEventListener("click", exportCalibrationFile);
elements.calibrationClearButton.addEventListener("click", clearCalibration);
elements.tabButtons.forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});
wireTabKeyboard(elements.tabButtons);
wireTabKeyboard(elements.flowTabButtons);

setActiveTab("flow");
setActiveFlowMode("constant");
refreshTelemetry();
setInterval(refreshTelemetry, 100);
if (!window.cardiopump?.mainOwnsHeartbeat) {
  setInterval(sendHeartbeat, 100);
}
