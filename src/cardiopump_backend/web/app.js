const state = {
  telemetry: null,
  connected: false,
  hadConnection: false,
  busy: false,
  refreshRequestId: 0,
  disableConfirmUntilMs: 0,
  activeTab: "flow",
  activeFlowMode: "constant",
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
  mainEnableButton: document.querySelector("#main-enable-button"),
  mainStartButton: document.querySelector("#main-start-button"),
  clearFaultButton: document.querySelector("#clear-fault-button"),
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
  const hardwareConnected = hardware.connected === true;
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
  const control = telemetry.control || {};
  const running = telemetry.running === true;
  const arduinoEnabled = hardware.arduino_enable === true;
  const system = telemetry.system || {};
  const faulted = system.state === "fault";
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
  elements.constantModeButton.disabled = running || state.busy || faulted;
  elements.waveformModeButton.disabled = running || state.busy || faulted;
  if (document.activeElement !== elements.flowInput && Number.isFinite(Number(control.flow_ml_s))) {
    elements.flowInput.value = Number(control.flow_ml_s).toFixed(2);
  }
  elements.flowInput.disabled = waveformMode || running || state.busy || hardware.connected !== true || faulted;
  elements.waveformFile.disabled = running || state.busy || faulted;
  elements.waveformMaxFlowInput.disabled = running || state.busy || !waveformAvailable || faulted;
  elements.waveformPeriodInput.disabled = running || state.busy || !waveformAvailable || faulted;
  elements.clipNegativeFlowInput.disabled = running || state.busy || !waveformAvailable || faulted;
  elements.resetWaveformDefaultsButton.disabled = running || state.busy || !waveformAvailable || faulted;
  if (document.activeElement !== elements.waveformRateSelect && Number.isFinite(Number(control.waveform_update_hz))) {
    elements.waveformRateSelect.value = String(control.waveform_update_hz);
  }
  elements.waveformRateSelect.disabled = running || state.busy || faulted;
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
  elements.triggerHighMsInput.disabled = running || state.busy || faulted;
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
  elements.calibrationStatus.textContent = waveform?.calibration === "linear_full_scale"
    ? "Linear full-scale fallback"
    : waveform?.calibration || "Not configured";
  drawWaveform(waveform);

  elements.mainEnableButton.textContent = faulted
    ? "Fault Latched"
    : disablePending
    ? "Confirm Disable"
    : arduinoEnabled ? "Disable" : "Enable";
  elements.mainEnableButton.className = faulted
    ? "big-button disabled"
    : arduinoEnabled ? "big-button danger" : "big-button enable";
  elements.clearFaultButton.disabled = !faulted || state.busy || hardware.connected !== true;

  elements.mainStartButton.textContent = running ? "Stop" : "Start";
  elements.mainStartButton.className = running
    ? "big-button danger"
    : canStart(telemetry)
      ? "big-button start"
      : "big-button disabled";
  elements.mainStartButton.disabled = !running && !canStart(telemetry);

  setControlsEnabled(hardware.connected === true && !state.busy && !faulted);

  if (faulted && !state.busy) {
    elements.controlMessage.textContent = `Fault: ${formatFaultReason(system.reason)}`;
    elements.controlMessage.className = "message error";
  }
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
  try {
    await postJson("/api/heartbeat");
  } catch {
    state.connected = false;
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

async function toggleMainEnable() {
  if (state.telemetry?.system?.state === "fault") {
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
elements.tabButtons.forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});

setActiveTab("flow");
setActiveFlowMode("constant");
refreshTelemetry();
setInterval(refreshTelemetry, 100);
setInterval(sendHeartbeat, 100);
