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
  calibrationClearConfirmUntilMs: 0,
};

const DISABLE_CONFIRM_WINDOW_MS = 4000;

const elements = {
  tabButtons: document.querySelectorAll("[data-tab]"),
  tabPanels: document.querySelectorAll(".tab-panel"),
  flowTabButtons: document.querySelectorAll("[data-flow-mode]"),
  flowTabPanels: document.querySelectorAll(".flow-panel"),
  connectionStatus: document.querySelector("#connection-status"),
  systemStateStatus: document.querySelector("#system-state-status"),
  overpressureStatus: document.querySelector("#overpressure-status"),
  servoStatus: document.querySelector("#servo-status"),
  arduinoEnableStatus: document.querySelector("#arduino-enable-status"),
  dacStatus: document.querySelector("#dac-status"),
  constantModeButton: document.querySelector("#constant-mode-button"),
  waveformModeButton: document.querySelector("#waveform-mode-button"),
  flowInput: document.querySelector("#flow-input"),
  waveformFile: document.querySelector("#waveform-file"),
  waveformRasterNote: document.querySelector("#waveform-raster-note"),
  waveformMaxFlowInput: document.querySelector("#waveform-max-flow-input"),
  waveformPeriodInput: document.querySelector("#waveform-period-input"),
  clipNegativeFlowInput: document.querySelector("#clip-negative-flow-input"),
  resetWaveformDefaultsButton: document.querySelector("#reset-waveform-defaults-button"),
  waveformCanvas: document.querySelector("#waveform-canvas"),
  waveformStatus: document.querySelector("#waveform-status"),
  triggerHighMsInput: document.querySelector("#trigger-high-ms-input"),
  waveformRateSelect: document.querySelector("#waveform-rate-select"),
  calibrationStatus: document.querySelector("#calibration-status"),
  calibrationPoints: document.querySelector("#calibration-points"),
  calibrationConfirmInput: document.querySelector("#calibration-confirm-input"),
  calibrationStartButton: document.querySelector("#calibration-start-button"),
  calibrationMeasurement: document.querySelector("#calibration-measurement"),
  calibrationMeasurementType: document.querySelector("#calibration-measurement-type"),
  calibrationMeasurementValue: document.querySelector("#calibration-measurement-value"),
  calibrationSaveButton: document.querySelector("#calibration-save-button"),
  calibrationClearButton: document.querySelector("#calibration-clear-button"),
  mainEnableButton: document.querySelector("#main-enable-button"),
  mainStartButton: document.querySelector("#main-start-button"),
  clearFaultButton: document.querySelector("#clear-fault-button"),
  timingStatus: document.querySelector("#timing-status"),
  timingDetails: document.querySelector("#timing-details"),
  watchdogStatus: document.querySelector("#watchdog-status"),
  controllerDiagnostics: document.querySelector("#controller-diagnostics"),
  controlMessage: document.querySelector("#control-message"),
};

async function api(path, options = {}) {
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

  const overpressureSet = hardware.pressure_latch_set === true;
  setPill(
    elements.overpressureStatus,
    overpressureSet ? "Overpressure Fault\nClick to Reset" : "Pressure OK",
    overpressureSet ? "bad flash" : "neutral",
  );
  elements.overpressureStatus.disabled = !overpressureSet;
  elements.overpressureStatus.title = overpressureSet ? "Pulse pressure latch reset" : "";

  setPill(
    elements.servoStatus,
    hardware.servo_ready ? "Servo OK" : "Servo Fault",
    hardware.servo_ready ? "ok" : "bad",
  );

  setPill(
    elements.arduinoEnableStatus,
    hardware.arduino_enable ? "Arduino Enable" : "Arduino Disabled",
    hardware.arduino_enable ? "ok" : "neutral",
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
  elements.flowInput.disabled = waveformMode || running || state.busy || !hardwareConnected || controlsLocked;
  elements.waveformFile.disabled = running || state.busy || controlsLocked;
  elements.waveformMaxFlowInput.disabled = running || state.busy || !waveformAvailable || controlsLocked;
  elements.waveformPeriodInput.disabled = running || state.busy || !waveformAvailable || controlsLocked;
  elements.clipNegativeFlowInput.disabled = running || state.busy || !waveformAvailable || controlsLocked;
  elements.resetWaveformDefaultsButton.disabled = running || state.busy || !waveformAvailable || controlsLocked;
  if (document.activeElement !== elements.waveformRateSelect && Number.isFinite(Number(control.waveform_update_hz))) {
    elements.waveformRateSelect.value = String(control.waveform_update_hz);
  }
  elements.waveformRateSelect.disabled = running || state.busy || controlsLocked;
  if (document.activeElement !== elements.waveformMaxFlowInput && Number.isFinite(Number(waveform?.max_flow_ml_s))) {
    elements.waveformMaxFlowInput.value = Number(waveform.max_flow_ml_s).toFixed(2);
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
  elements.triggerHighMsInput.disabled = running || state.busy || controlsLocked;
  elements.waveformStatus.textContent = waveformAvailable && waveform
    ? `${waveform.sample_count} samples at ${waveform.update_hz} Hz, ${Number(waveform.period_ms).toFixed(0)} ms, `
      + `flow scale ${Number(waveform.flow_scale).toFixed(2)}, period scale ${Number(waveform.period_scale).toFixed(2)}, `
      + `${waveform.clipped_sample_count} clipped, `
      + `stroke volume ${Number(waveform.total_stroke_volume_ml).toFixed(2)} mL`
    : "No waveform loaded.";
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
  drawWaveform(waveform);

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

function renderCalibration(hardware, telemetry) {
  const voltages = [2.5, 5, 7.5, 10];
  const flows = hardware.calibration_flows_ml_s || [0, 0, 0, 0];
  const validMask = Number(hardware.calibration_valid_mask) || 0;
  const active = hardware.run_mode === "calibration" || hardware.calibration_active === true;
  const complete = hardware.calibration_complete === true;
  const nextIndex = voltages.findIndex((_, index) => (validMask & (1 << index)) === 0);
  const targetIndex = nextIndex < 0 ? 0 : nextIndex;
  const targetVoltage = voltages[targetIndex];

  elements.calibrationPoints.replaceChildren(...voltages.map((voltage, index) => {
    const row = document.createElement("div");
    row.className = "calibration-point";
    const measured = (validMask & (1 << index)) !== 0;
    row.innerHTML = `<span>${voltage.toFixed(1)} V</span><span>${measured
      ? `${Number(flows[index]).toFixed(4)} mL/s`
      : "Not measured"}</span>`;
    return row;
  }));

  if (active) {
    elements.calibrationStatus.textContent = `Running: ${formatFaultReason(hardware.calibration_stage)}`;
  } else if (complete) {
    elements.calibrationStatus.textContent = "Measurement required";
  } else if (hardware.calibration_valid === true) {
    elements.calibrationStatus.textContent = "4-point calibration active";
  } else {
    elements.calibrationStatus.textContent = `${validMask.toString(2).split("1").length - 1}/4 points`;
  }

  elements.calibrationStartButton.textContent = `Start ${targetVoltage.toFixed(1)} V Point`;
  elements.calibrationStartButton.disabled =
    nextIndex < 0 || active || complete || state.busy
    || !elements.calibrationConfirmInput.checked || !canStart(telemetry);
  elements.calibrationConfirmInput.disabled = active || complete || state.busy || nextIndex < 0;
  elements.calibrationMeasurement.hidden = !complete;
  elements.calibrationSaveButton.disabled = state.busy || !complete;
  elements.calibrationClearButton.disabled = state.busy || active || validMask === 0;
  if (Date.now() > state.calibrationClearConfirmUntilMs) {
    state.calibrationClearConfirmUntilMs = 0;
    elements.calibrationClearButton.textContent = "Clear Calibration";
  }
  if (complete && Number(hardware.calibration_target_mv) > 0) {
    state.calibrationVoltage = Number(hardware.calibration_target_mv) / 1000;
  }
}

function renderControllerDiagnostics(hardware) {
  if (hardware.connected !== true || hardware.fresh !== true) {
    setPill(elements.watchdogStatus, "Watchdog Unknown", "neutral");
    elements.controllerDiagnostics.textContent = "No current controller diagnostics.";
    return;
  }
  setPill(
    elements.watchdogStatus,
    hardware.watchdog_ok ? "Watchdog Active" : "Watchdog Fault",
    hardware.watchdog_ok ? "ok" : "bad",
  );
  elements.controllerDiagnostics.textContent =
    `Reset: ${formatFaultReason(hardware.reset_cause)}; retained fault: `
    + `${formatFaultReason(hardware.retained_last_fault)} (${Number(hardware.retained_fault_count) || 0} total). `
    + `Heartbeat age/max gap: ${Number(hardware.heartbeat_age_ms) || 0}/`
    + `${Number(hardware.heartbeat_max_gap_ms) || 0} ms. `
    + `Ramp updates/missed/max late: ${Number(hardware.ramp_update_count) || 0}/`
    + `${Number(hardware.ramp_missed_deadline_count) || 0}/`
    + `${Number(hardware.ramp_max_lateness_us) || 0} us. `
    + `Serial overflows: ${Number(hardware.serial_overflow_count) || 0}; `
    + `protocol errors: ${Number(hardware.protocol_error_count) || 0}; `
    + `DAC write failures: ${Number(hardware.dac_write_failure_count) || 0}.`;
}

function renderTimingDiagnostics(hardware, active) {
  if (
    hardware.connected !== true ||
    hardware.fresh !== true ||
    hardware.protocol_compatible !== true
  ) {
    setPill(elements.timingStatus, "Timing Unknown", "neutral");
    elements.timingDetails.textContent = "No current waveform timing data.";
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
  elements.timingDetails.textContent =
    `Scheduler lateness ${lateness} us (max ${maxLateness} us); `
    + `DAC write ${writeTime} us (max ${maxWriteTime} us); `
    + `${warnings} warnings; ${overruns} overruns.`;
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
  setPill(elements.overpressureStatus, "Pressure OK", "neutral");
  setPill(elements.servoStatus, "Servo Fault", "bad");
  setPill(elements.arduinoEnableStatus, "Arduino Disabled", "neutral");
  setPill(elements.dacStatus, "DAC Unknown", "neutral");
  elements.mainStartButton.disabled = true;
  elements.clearFaultButton.disabled = true;
  elements.flowInput.disabled = true;
  elements.waveformFile.disabled = true;
  elements.waveformMaxFlowInput.disabled = true;
  elements.waveformPeriodInput.disabled = true;
  elements.clipNegativeFlowInput.disabled = true;
  elements.resetWaveformDefaultsButton.disabled = true;
  elements.triggerHighMsInput.disabled = true;
  elements.waveformRateSelect.disabled = true;
  elements.waveformRasterNote.hidden = true;
  elements.calibrationStatus.textContent = "Not configured";
  elements.calibrationStartButton.disabled = true;
  elements.calibrationSaveButton.disabled = true;
  elements.calibrationClearButton.disabled = true;
  elements.calibrationMeasurement.hidden = true;
  setPill(elements.timingStatus, "Timing Unknown", "neutral");
  elements.timingDetails.textContent = "No waveform timing data.";
  setPill(elements.watchdogStatus, "Watchdog Unknown", "neutral");
  elements.controllerDiagnostics.textContent = "No controller diagnostics.";
  drawWaveform(null);
  setControlsEnabled(false);
}

function drawWaveform(waveform) {
  const canvas = elements.waveformCanvas;
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, width, height);

  const paddingLeft = 42;
  const paddingRight = 12;
  const paddingTop = 14;
  const paddingBottom = 28;
  const plotWidth = width - paddingLeft - paddingRight;
  const plotHeight = height - paddingTop - paddingBottom;
  const plotLimit = Math.max(Math.abs(Number(waveform?.max_flow_ml_s) || 0), 0.001);
  const yFor = (flow) => paddingTop + (1 - (flow + plotLimit) / (2 * plotLimit)) * plotHeight;

  context.strokeStyle = "#d6dde6";
  context.lineWidth = 1;
  context.beginPath();
  for (const flow of [plotLimit, 0, -plotLimit]) {
    const y = yFor(flow);
    context.moveTo(paddingLeft, y);
    context.lineTo(width - paddingRight, y);
  }
  context.stroke();

  context.fillStyle = "#657386";
  context.font = "12px system-ui, sans-serif";
  context.textAlign = "right";
  context.textBaseline = "middle";
  for (const flow of [plotLimit, 0, -plotLimit]) {
    context.fillText(formatFlow(flow), paddingLeft - 8, yFor(flow));
  }

  const preview = waveform?.preview || [];
  if (!preview.length) {
    context.fillStyle = "#7a8696";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText("No waveform loaded", width / 2, height / 2);
    return;
  }

  const duration = Math.max(Number(waveform.duration_s) || 1, preview[preview.length - 1].time_s || 1);
  context.strokeStyle = "#1f7a8c";
  context.lineWidth = 2;
  context.beginPath();
  preview.forEach((point, index) => {
    const x = paddingLeft + (point.time_s / duration) * plotWidth;
    const flow = Math.max(-plotLimit, Math.min(plotLimit, Number(point.flow_ml_s ?? point.flow) || 0));
    const y = yFor(flow);
    if (index === 0) {
      context.moveTo(x, y);
    } else {
      context.lineTo(x, y);
    }
  });
  context.stroke();

  context.fillStyle = "#657386";
  context.textAlign = "left";
  context.textBaseline = "alphabetic";
  context.fillText("0 s", paddingLeft, height - 8);
  context.textAlign = "right";
  context.fillText(`${Number(waveform.duration_s).toFixed(2)} s`, width - paddingRight, height - 8);
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
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent = "Waveform file loaded. It will send to Arduino on Start.";
    elements.controlMessage.className = "message ok";
  } catch (error) {
    elements.controlMessage.textContent = error.message;
    elements.controlMessage.className = "message error";
    await refreshTelemetry();
  } finally {
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
  const updateHz = Number(elements.waveformRateSelect.value);
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
    elements.controlMessage.textContent = "Trigger timing applied.";
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

async function startCalibration() {
  const validMask = Number(state.telemetry?.hardware?.calibration_valid_mask) || 0;
  const voltages = [2.5, 5, 7.5, 10];
  const index = voltages.findIndex((_, pointIndex) => (validMask & (1 << pointIndex)) === 0);
  if (index < 0 || !elements.calibrationConfirmInput.checked) {
    return;
  }

  state.busy = true;
  state.calibrationVoltage = voltages[index];
  try {
    const payload = await postJson("/api/calibration/start", { voltage: voltages[index] });
    renderTelemetry(payload.telemetry);
    elements.controlMessage.textContent =
      `Calibration at ${voltages[index].toFixed(1)} V started; the Arduino owns the timed run.`;
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
elements.waveformRateSelect.addEventListener("change", updateWaveformRate);
elements.resetWaveformDefaultsButton.addEventListener("click", resetWaveformDefaults);
elements.triggerHighMsInput.addEventListener("change", updateTrigger);
elements.mainEnableButton.addEventListener("click", toggleMainEnable);
elements.mainStartButton.addEventListener("click", toggleStartStop);
elements.overpressureStatus.addEventListener("click", resetPressureLatch);
elements.clearFaultButton.addEventListener("click", clearFault);
elements.calibrationConfirmInput.addEventListener("change", () => {
  if (state.telemetry) {
    renderCalibration(state.telemetry.hardware || {}, state.telemetry);
  }
});
elements.calibrationStartButton.addEventListener("click", startCalibration);
elements.calibrationSaveButton.addEventListener("click", saveCalibrationMeasurement);
elements.calibrationClearButton.addEventListener("click", clearCalibration);
elements.tabButtons.forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});

setActiveTab("flow");
setActiveFlowMode("constant");
refreshTelemetry();
setInterval(refreshTelemetry, 100);
setInterval(sendHeartbeat, 100);
