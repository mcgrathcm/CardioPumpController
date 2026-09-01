#include <DFRobot_GP8XXX.h>
#include <EEPROM.h>
#include <WDT.h>

constexpr uint8_t PressureLatchPin = 9;
constexpr uint8_t ServoReadyPin = 10;
constexpr uint8_t ArduinoEnablePin = 11;
constexpr uint8_t PressureLatchResetPin = 12;
constexpr uint8_t TriggerOutputPin = 7;

constexpr uint8_t DacAddress = 0x58;
constexpr unsigned long TelemetryIntervalMs = 1000;
constexpr unsigned long HostTimeoutMs = 500;
constexpr unsigned long ServoReleaseGraceMs = 100;
constexpr unsigned long UnexpectedServoReadyDwellMs = 10;
constexpr unsigned long ServoEngageGraceMs = 250;
constexpr unsigned long RampDurationUs = 500000UL;
constexpr unsigned long RampUpdatePeriodUs = 4000UL;
constexpr unsigned long CalibrationFlatTopUs = 59500000UL;
constexpr unsigned long FaultClearDwellMs = 250;
constexpr unsigned long WatchdogTimeoutMs = 2684;
constexpr unsigned long I2cTimeoutUs = 1000;
constexpr unsigned long ConstantTriggerPeriodUs = 1000000UL;
constexpr unsigned long DefaultTriggerHighUs = 10000UL;
constexpr uint16_t DefaultWaveformRateHz = 250;
constexpr uint16_t MaxWaveformSamples = 4096;
constexpr uint8_t MaxSerialBytesPerLoop = 64;
constexpr uint8_t MaxHeartbeatBytesPerLoop = 4;
constexpr uint8_t TimingWarningPeriodDivisor = 4;
constexpr uint8_t DacDataRegister = 0x02;
constexpr uint8_t DacModeRegister = 0x01;
constexpr uint8_t DacPositive10VMode = 0x1C;
constexpr uint8_t DacNegative10VMode = 0x14;
constexpr uint8_t ProtocolVersion = 4;
constexpr uint8_t CalibrationPointCount = 4;
constexpr uint16_t CalibrationVoltagesMv[CalibrationPointCount] = {2500, 5000, 7500, 10000};
constexpr uint16_t UncalibratedMaxMillivolts = 2500;
constexpr uint32_t UncalibratedMaxFlowUlS = 1000000UL;
constexpr uint32_t RetainedMagic = 0x43415244UL;
constexpr uint8_t RetainedVersion = 2;

enum FaultCode : uint8_t {
  FaultNone = 0,
  FaultUnexpectedServoReady = 1,
  FaultOverpressure = 2,
  FaultInterlock = 3,
  FaultHeartbeatTimeout = 4,
  FaultTimingOverrun = 5,
  FaultDacCommunication = 6,
  FaultProtocol = 7,
  FaultWatchdogInit = 8,
  FaultWatchdogReset = 9,
};

enum RunMode : uint8_t {
  RunStopped = 0,
  RunConstant = 1,
  RunWaveform = 2,
  RunCalibration = 3,
};

enum CalibrationStage : uint8_t {
  CalibrationIdle = 0,
  CalibrationRampUp = 1,
  CalibrationFlat = 2,
  CalibrationRampDown = 3,
  CalibrationComplete = 4,
};

struct RetainedState {
  uint32_t magic;
  uint8_t version;
  uint8_t lastFault;
  uint16_t faultCount;
  uint32_t checksum;
};

int dacMillivolts = 0;
uint16_t dacData = 0;
int dacRange = 0;
bool dacPresent = false;

char commandBuffer[256];
uint8_t commandLength = 0;
bool commandOverflow = false;
bool commandSequenced = false;
unsigned long commandSequence = 0;

int16_t waveform[MaxWaveformSamples];
uint16_t waveformSampleCount = 0;
uint16_t waveformLoadCount = 0;
uint16_t waveformRateHz = DefaultWaveformRateHz;
uint16_t waveformIndex = 0;
uint32_t expectedWaveformCrc = 0;
uint32_t waveformPeakFlowUlS = 0;
uint32_t waveformCrc = 0xFFFFFFFFUL;
bool waveformLoaded = false;
bool waveformLoading = false;
bool waveformRunning = false;
unsigned long waveformPeriodUs = 1000000UL / DefaultWaveformRateHz;
unsigned long nextWaveformUpdateUs = 0;
unsigned long lastHostContactMs = 0;
unsigned long lastEnableOpenedMs = 0;
unsigned long lastEnableClosedMs = 0;
bool unexpectedServoReadyTracking = false;
unsigned long unexpectedServoReadySinceMs = 0;
bool hostSessionEstablished = false;
bool faultLatched = false;
FaultCode faultCode = FaultNone;
bool controlledStopActive = false;
FaultCode controlledStopCode = FaultNone;
bool rampUpActive = false;
bool rampDownActive = false;
bool rampOpenEnableAfter = false;
bool rampDownCompletesCalibration = false;
unsigned long rampUpStartedUs = 0;
unsigned long rampDownStartedUs = 0;
unsigned long nextRampUpdateUs = 0;
int rampTargetMillivolts = 0;
int rampStartMillivolts = 0;
RunMode runMode = RunStopped;
CalibrationStage calibrationStage = CalibrationIdle;
uint16_t calibrationTargetMv = 0;
unsigned long calibrationFlatStartedUs = 0;
bool calibrationComplete = false;
uint8_t calibrationValidMask = 0;
uint32_t calibrationFlowUlS[CalibrationPointCount] = {};
unsigned long triggerHighUs = DefaultTriggerHighUs;
bool triggerActive = false;
unsigned long triggerStartedUs = 0;
unsigned long lastConstantTriggerUs = 0;
unsigned long triggerCount = 0;
unsigned long timingLatenessUs = 0;
unsigned long timingMaxLatenessUs = 0;
unsigned long timingWriteUs = 0;
unsigned long timingMaxWriteUs = 0;
unsigned long timingWarningCount = 0;
unsigned long timingOverrunCount = 0;
unsigned long rampUpdateCount = 0;
unsigned long rampMissedDeadlineCount = 0;
unsigned long rampMaxLatenessUs = 0;
unsigned long serialOverflowCount = 0;
unsigned long protocolErrorCount = 0;
unsigned long dacWriteFailureCount = 0;
unsigned long heartbeatMaxGapMs = 0;
char heartbeatAckBuffer[48] = {};
uint8_t heartbeatAckLength = 0;
uint8_t heartbeatAckOffset = 0;
bool faultClearDwellTracking = false;
unsigned long faultClearSafeSinceMs = 0;
bool watchdogOk = false;
uint16_t resetStatus1 = 0;
RetainedState retainedState = {};

DFRobot_GP8630N_I2C GP8630N(&Wire1, DacAddress);

void setup() {
  Serial.begin(115200);

  pinMode(ArduinoEnablePin, OUTPUT);
  digitalWrite(ArduinoEnablePin, HIGH);
  lastEnableOpenedMs = millis();

  resetStatus1 = R_SYSTEM->RSTSR1;
  R_SYSTEM->RSTSR1 = 0;
  loadRetainedState();

  pinMode(TriggerOutputPin, OUTPUT);
  digitalWrite(TriggerOutputPin, LOW);

  pinMode(PressureLatchPin, INPUT_PULLUP);
  pinMode(ServoReadyPin, INPUT_PULLUP);

  digitalWrite(PressureLatchResetPin, LOW);
  pinMode(PressureLatchResetPin, OUTPUT);
  digitalWrite(PressureLatchResetPin, LOW);

  lastHostContactMs = millis();
  Wire1.begin();
  Wire1.setWireTimeout(I2cTimeoutUs, true);
  dacPresent = probeDac();
  if (!dacPresent || !setDacMillivolts(0)) {
    enterDacCommunicationFault();
  }

  watchdogOk = WDT.begin(WatchdogTimeoutMs);
  if (!watchdogOk) {
    enterFault(FaultWatchdogInit);
  } else if (!faultLatched && watchdogResetDetected()) {
    enterFault(FaultWatchdogReset);
  }

  Serial.println("cardiopump firmware ready");
  printTelemetry();
}

void loop() {
  static unsigned long lastTelemetryMs = 0;

  readSerialCommands();
  flushHeartbeatAck();
  unsigned long nowMs = millis();
  enforceSafety(nowMs);
  updateFaultClearDwell(nowMs);
  updateRampUp(micros());
  updateCalibration(micros());
  updateRampDown(micros());
  updateTrigger(micros());
  if (!rampUpActive && !rampDownActive) {
    updateWaveform();
  }

  if (runMode == RunStopped && !rampUpActive && !rampDownActive
      && nowMs - lastTelemetryMs >= TelemetryIntervalMs) {
    lastTelemetryMs = nowMs;
    printTelemetry();
  }

  if (watchdogOk) {
    WDT.refresh();
  }
}

void readSerialCommands() {
  uint8_t bytesRead = 0;
  while (Serial.available() > 0 && bytesRead < MaxSerialBytesPerLoop) {
    char c = Serial.read();
    bytesRead++;
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      if (commandOverflow) {
        serialOverflowCount++;
        commandLength = 0;
        commandOverflow = false;
        commandSequenced = false;
        protocolViolation("command_too_long");
        continue;
      }
      commandBuffer[commandLength] = '\0';
      handleCommand(commandBuffer);
      commandLength = 0;
      continue;
    }
    if (commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = c;
    } else {
      commandOverflow = true;
    }
  }
}

void handleCommand(char *rawCommand) {
  commandSequenced = false;
  commandSequence = 0;

  char *command = rawCommand;
  if (strncmp(command, "CMD ", 4) == 0) {
    char *sequenceToken = command + 4;
    char *commandStart = strchr(sequenceToken, ' ');
    if (commandStart == nullptr) {
      protocolViolation("command_envelope");
      return;
    }
    *commandStart = '\0';
    commandStart++;
    if (!parseUnsignedLongStrict(sequenceToken, commandSequence) || commandStart[0] == '\0') {
      protocolViolation("command_envelope");
      return;
    }
    commandSequenced = true;
    command = commandStart;
  }

  if (command[0] == '\0') {
    return;
  }

  if (strncmp(command, "VOLT ", 5) == 0) {
    long requestedMillivolts = 0;
    if (!parseLongStrict(command + 5, -10000, 10000, requestedMillivolts)) {
      protocolViolation("voltage_value");
      return;
    }
    if (requestedMillivolts != 0) {
      sendCommandError("raw_voltage_disabled");
      return;
    }
    if (rampUpActive || rampDownActive) {
      sendCommandError("ramp_active");
      return;
    }
    stopWaveform();
    runMode = RunStopped;
    setTriggerLow();
    if (!setDacMillivolts(0)) {
      sendCommandError("dac_communication");
      return;
    }
    acceptHostCommand();
    sendCommandAck("action=voltage_zero");
    printTelemetry();
    return;
  }

  if (strncmp(command, "ENABLE ", 7) == 0) {
    long enabledValue = 0;
    if (!parseLongStrict(command + 7, 0, 1, enabledValue)) {
      protocolViolation("enable_value");
      return;
    }
    bool enabled = enabledValue == 1;
    if (enabled && faultLatched) {
      sendCommandError("fault_latched");
      return;
    }
    if (enabled && (rampUpActive || rampDownActive)) {
      sendCommandError("ramp_active");
      return;
    }
    bool alreadyEnabled = digitalRead(ArduinoEnablePin) == LOW;
    if (enabled && !alreadyEnabled) {
      bool safeEnableState = dacPresent
        && !motionCommanded()
        && dacMillivolts == 0
        && digitalRead(PressureLatchPin) == HIGH
        && digitalRead(ServoReadyPin) == HIGH;
      if (!safeEnableState) {
        sendCommandError("enable_not_safe");
        return;
      }
    }
    if (!enabled) {
      if (faultLatched) {
        safeStop();
      } else if (rampDownActive) {
        rampOpenEnableAfter = true;
      } else if (motionCommanded()) {
        beginRampDown(true);
      } else {
        setArduinoEnable(false);
      }
    } else {
      setArduinoEnable(true);
    }
    acceptHostCommand();
    sendCommandAck(enabled ? "action=enable_on" : "action=enable_off");
    if (!rampUpActive && !rampDownActive && runMode == RunStopped) {
      printTelemetry();
    }
    return;
  }

  if (strcmp(command, "RESET_LATCH") == 0) {
    if (motionCommanded() || rampUpActive || rampDownActive) {
      sendCommandError("pump_active");
      return;
    }
    acceptHostCommand();
    pulsePressureLatchReset();
    sendCommandAck("action=latch_reset");
    printTelemetry();
    return;
  }

  if (strcmp(command, "CLEAR_FAULT") == 0) {
    if (clearFault()) {
      acceptHostCommand();
    }
    return;
  }

  if (strcmp(command, "GET") == 0) {
    if (motionCommanded() || rampUpActive || rampDownActive) {
      sendCommandError("telemetry_busy");
      return;
    }
    dacPresent = probeDac();
    if (!dacPresent) {
      enterDacCommunicationFault();
    }
    printTelemetry();
    sendCommandAck("action=telemetry");
    return;
  }

  if (strcmp(command, "HEARTBEAT") == 0) {
    acceptHostCommand();
    sendHeartbeatAck();
    return;
  }

  if (strncmp(command, "TRIGGER_US ", 11) == 0) {
    if (motionCommanded() || rampUpActive || rampDownActive) {
      sendCommandError("configuration_busy");
      return;
    }
    long requestedHighUs = 0;
    if (!parseLongStrict(command + 11, 100, 1000000L, requestedHighUs)) {
      protocolViolation("trigger_value");
      return;
    }
    setTriggerHighUs(requestedHighUs);
    acceptHostCommand();
    sendCommandAck("action=trigger_set");
    printTelemetry();
    return;
  }

  if (strncmp(command, "WAVE_BEGIN ", 11) == 0) {
    if (beginWaveformLoad(command + 11)) {
      acceptHostCommand();
      sendCommandAck("action=wave_begin");
    }
    return;
  }

  if (strncmp(command, "WAVE_DATA ", 10) == 0) {
    if (appendWaveformData(command + 10)) {
      acceptHostCommand();
      sendCommandAck("action=wave_data");
    }
    return;
  }

  if (strcmp(command, "WAVE_END") == 0) {
    if (finishWaveformLoad()) {
      acceptHostCommand();
      sendCommandAck("action=wave_loaded wave_loaded=1");
    }
    return;
  }

  if (strcmp(command, "START_WAVE") == 0) {
    startWaveform();
    return;
  }

  if (strncmp(command, "START_CONSTANT ", 15) == 0) {
    startConstant(command + 15);
    return;
  }

  if (strncmp(command, "CAL_START ", 10) == 0) {
    startCalibration(command + 10);
    return;
  }

  if (strncmp(command, "CAL_RESULT ", 11) == 0) {
    storeCalibrationResult(command + 11);
    return;
  }

  if (strncmp(command, "CAL_LOAD ", 9) == 0) {
    loadCalibration(command + 9);
    return;
  }

  if (strcmp(command, "CAL_CLEAR") == 0) {
    clearCalibration();
    return;
  }

  if (strcmp(command, "STOP") == 0) {
    rampDownCompletesCalibration = false;
    calibrationComplete = false;
    if (runMode == RunCalibration) {
      calibrationStage = CalibrationIdle;
    }
    if (faultLatched) {
      safeStop();
    } else {
      beginRampDown(false);
    }
    acceptHostCommand();
    sendCommandAck("action=stop");
    if (!rampUpActive && !rampDownActive) {
      printTelemetry();
    }
    return;
  }

  protocolViolation("unknown_command");
}

void acceptHostCommand() {
  unsigned long nowMs = millis();
  if (hostSessionEstablished) {
    heartbeatMaxGapMs = max(heartbeatMaxGapMs, nowMs - lastHostContactMs);
  }
  lastHostContactMs = nowMs;
  hostSessionEstablished = true;
}

bool beginWaveformLoad(char *payload) {
  if (faultLatched || controlledStopActive || rampUpActive || rampDownActive
      || runMode == RunCalibration) {
    sendCommandError("fault_or_ramp");
    return false;
  }
  if (waveformRunning) {
    sendCommandError("waveform_running");
    return false;
  }

  char *rateToken = strtok(payload, " ");
  char *countToken = strtok(nullptr, " ");
  char *crcToken = strtok(nullptr, " ");
  char *peakFlowToken = strtok(nullptr, " ");
  char *extraToken = strtok(nullptr, " ");
  if (rateToken == nullptr || countToken == nullptr || crcToken == nullptr
      || peakFlowToken == nullptr
      || extraToken != nullptr) {
    protocolViolation("wave_begin_args");
    return false;
  }

  long requestedRate = 0;
  long requestedCount = 0;
  unsigned long requestedCrc = 0;
  unsigned long requestedPeakFlow = 0;
  if (!parseLongStrict(rateToken, 1, 1000, requestedRate)
      || !parseLongStrict(countToken, 1, MaxWaveformSamples, requestedCount)
      || !parseUnsignedLongStrict(crcToken, requestedCrc)
      || !parseUnsignedLongStrict(peakFlowToken, requestedPeakFlow)) {
    protocolViolation("wave_begin_value");
    return false;
  }
  bool supportedRate = requestedRate == 250 || requestedRate == 500;
  if (!supportedRate) {
    sendCommandError("wave_begin_range");
    return false;
  }
  uint32_t maxFlowUlS = calibrationValid()
    ? calibrationFlowUlS[CalibrationPointCount - 1]
    : UncalibratedMaxFlowUlS;
  if (requestedPeakFlow > maxFlowUlS) {
    sendCommandError("flow_out_of_calibration");
    return false;
  }

  waveformRateHz = static_cast<uint16_t>(requestedRate);
  waveformPeriodUs = 1000000UL / waveformRateHz;
  waveformSampleCount = static_cast<uint16_t>(requestedCount);
  waveformPeakFlowUlS = static_cast<uint32_t>(requestedPeakFlow);
  expectedWaveformCrc = static_cast<uint32_t>(requestedCrc);
  waveformLoadCount = 0;
  waveformCrc = 0xFFFFFFFFUL;
  waveformIndex = 0;
  waveformLoaded = false;
  waveformLoading = true;
  return true;
}

bool appendWaveformData(char *payload) {
  if (!waveformLoading) {
    sendCommandError("wave_not_loading");
    return false;
  }

  char *offsetToken = strtok(payload, " ");
  if (offsetToken == nullptr) {
    abortWaveformLoad();
    protocolViolation("wave_data_offset");
    return false;
  }

  long offset = 0;
  if (!parseLongStrict(offsetToken, 0, waveformSampleCount - 1, offset)) {
    abortWaveformLoad();
    protocolViolation("wave_data_offset");
    return false;
  }
  if (static_cast<uint16_t>(offset) != waveformLoadCount) {
    abortWaveformLoad();
    sendCommandError("wave_data_sequence");
    return false;
  }

  bool receivedSample = false;
  char *sampleToken = strtok(nullptr, " ");
  while (sampleToken != nullptr) {
    if (waveformLoadCount >= waveformSampleCount) {
      abortWaveformLoad();
      sendCommandError("wave_data_overflow");
      return false;
    }
    long value = 0;
    if (!parseLongStrict(sampleToken, -32767, 32767, value)) {
      abortWaveformLoad();
      protocolViolation("wave_data_value");
      return false;
    }
    int16_t sample = static_cast<int16_t>(value);
    waveform[waveformLoadCount] = sample;
    uint16_t rawSample = static_cast<uint16_t>(sample);
    waveformCrc = updateCrc32(waveformCrc, rawSample & 0xFF);
    waveformCrc = updateCrc32(waveformCrc, rawSample >> 8);
    waveformLoadCount++;
    receivedSample = true;
    sampleToken = strtok(nullptr, " ");
  }
  if (!receivedSample) {
    abortWaveformLoad();
    protocolViolation("wave_data_empty");
    return false;
  }
  return true;
}

bool finishWaveformLoad() {
  if (!waveformLoading) {
    sendCommandError("wave_not_loading");
    return false;
  }

  waveformLoading = false;
  uint32_t completedCrc = waveformCrc ^ 0xFFFFFFFFUL;
  if (waveformLoadCount != waveformSampleCount || completedCrc != expectedWaveformCrc) {
    waveformLoaded = false;
    sendCommandError("wave_crc");
    printTelemetry();
    return false;
  }

  waveformLoaded = true;
  waveformIndex = 0;
  printTelemetry();
  return true;
}

void abortWaveformLoad() {
  waveformLoading = false;
  waveformLoaded = false;
  waveformLoadCount = 0;
  waveformCrc = 0xFFFFFFFFUL;
}

uint32_t updateCrc32(uint32_t crc, uint8_t value) {
  crc ^= value;
  for (uint8_t bit = 0; bit < 8; bit++) {
    crc = (crc >> 1) ^ (0xEDB88320UL & (0UL - (crc & 1UL)));
  }
  return crc;
}

void startWaveform() {
  if (rampUpActive || rampDownActive || runMode != RunStopped) {
    sendCommandError("pump_active");
    return;
  }
  if (!waveformLoaded) {
    sendCommandError("wave_not_loaded");
    return;
  }
  if (!isSafeToRun()) {
    sendCommandError("interlock_not_ready");
    return;
  }

  waveformRunning = false;
  runMode = RunWaveform;
  waveformIndex = 0;
  resetTimingDiagnostics();
  calibrationComplete = false;
  acceptHostCommand();
  sendCommandAck("action=start_wave wave_running=0 ramp_up_active=1 run_mode=waveform");
  beginRampUp(waveformSampleMillivolts(waveform[0]));
}

void startConstant(char *payload) {
  if (rampUpActive || rampDownActive || runMode != RunStopped) {
    sendCommandError("pump_active");
    return;
  }

  unsigned long requestedFlowUlS = 0;
  if (!parseUnsignedLongStrict(payload, requestedFlowUlS)) {
    protocolViolation("constant_flow");
    return;
  }
  if (!isSafeToRun()) {
    sendCommandError("interlock_not_ready");
    return;
  }

  int requestedMillivolts = 0;
  if (!flowToMillivolts(static_cast<uint32_t>(requestedFlowUlS), requestedMillivolts)) {
    sendCommandError("flow_out_of_calibration");
    return;
  }
  stopWaveform();
  runMode = RunConstant;
  calibrationComplete = false;
  acceptHostCommand();
  sendCommandAck("action=start_constant ramp_up_active=1 run_mode=constant wave_running=0");
  beginRampUp(requestedMillivolts);
}

void beginRampUp(int targetMillivolts) {
  rampTargetMillivolts = constrain(targetMillivolts, -10000, 10000);
  rampUpStartedUs = micros();
  nextRampUpdateUs = rampUpStartedUs + RampUpdatePeriodUs;
  rampUpActive = true;
}

void updateRampUp(unsigned long nowUs) {
  if (!rampUpActive) {
    return;
  }

  unsigned long elapsedUs = nowUs - rampUpStartedUs;
  if (elapsedUs < RampDurationUs
      && static_cast<long>(nowUs - nextRampUpdateUs) < 0) {
    return;
  }
  if (recordRampDeadline(nowUs)) {
    timingOverrunCount++;
    beginControlledFault(FaultTimingOverrun);
    return;
  }

  int targetMv = rampTargetMillivolts;
  if (elapsedUs < RampDurationUs) {
    targetMv = static_cast<int>(
      (static_cast<long long>(rampTargetMillivolts) * elapsedUs) / RampDurationUs
    );
  }
  if (!writeMillivoltsFast(targetMv)) {
    return;
  }
  rampUpdateCount++;

  if (elapsedUs < RampDurationUs) {
    nextRampUpdateUs += RampUpdatePeriodUs;
    return;
  }

  unsigned long completedUs = micros();
  rampUpActive = false;
  if (runMode == RunWaveform) {
    waveformRunning = true;
    waveformIndex = nextWaveformIndex(0);
    resetTimingDiagnostics();
    nextWaveformUpdateUs = completedUs + waveformPeriodUs;
    fireTrigger();
  } else if (runMode == RunConstant) {
    lastConstantTriggerUs = completedUs;
    fireTrigger();
  } else if (runMode == RunCalibration) {
    calibrationStage = CalibrationFlat;
    calibrationFlatStartedUs = completedUs;
  }
}

bool recordRampDeadline(unsigned long nowUs) {
  long latenessUs = static_cast<long>(nowUs - nextRampUpdateUs);
  if (latenessUs <= 0) {
    return false;
  }
  rampMaxLatenessUs = max(rampMaxLatenessUs, static_cast<unsigned long>(latenessUs));
  if (static_cast<unsigned long>(latenessUs) >= RampUpdatePeriodUs) {
    rampMissedDeadlineCount++;
    nextRampUpdateUs = nowUs;
    return true;
  }
  return false;
}

void stopWaveform() {
  waveformRunning = false;
}

void updateWaveform() {
  if (!waveformRunning) {
    return;
  }

  unsigned long nowUs = micros();
  long latenessUs = static_cast<long>(nowUs - nextWaveformUpdateUs);
  if (latenessUs < 0) {
    return;
  }

  timingLatenessUs = static_cast<unsigned long>(latenessUs);
  timingMaxLatenessUs = max(timingMaxLatenessUs, timingLatenessUs);
  if (timingLatenessUs >= waveformPeriodUs) {
    timingOverrunCount++;
    beginControlledFault(FaultTimingOverrun);
    return;
  }
  if (timingLatenessUs > waveformPeriodUs / TimingWarningPeriodDivisor) {
    timingWarningCount++;
  }

  unsigned long scheduledUpdateUs = nextWaveformUpdateUs;
  if (waveformIndex == 0) {
    fireTrigger();
  }
  unsigned long writeStartedUs = micros();
  if (writeTimedWaveformSample(waveform[waveformIndex], writeStartedUs)) {
    waveformIndex = nextWaveformIndex(waveformIndex);
    nextWaveformUpdateUs = scheduledUpdateUs + waveformPeriodUs;
  }
}

bool writeTimedWaveformSample(int16_t sample, unsigned long writeStartedUs) {
  if (!writeMillivoltsFast(waveformSampleMillivolts(sample))) {
    return false;
  }
  timingWriteUs = micros() - writeStartedUs;
  timingMaxWriteUs = max(timingMaxWriteUs, timingWriteUs);
  if (timingWriteUs >= waveformPeriodUs) {
    timingOverrunCount++;
    beginControlledFault(FaultTimingOverrun);
    return false;
  }
  return true;
}

int waveformSampleMillivolts(int16_t sample) {
  uint32_t magnitude = sample < 0
    ? static_cast<uint32_t>(-static_cast<long>(sample))
    : static_cast<uint32_t>(sample);
  uint32_t flowUlS = static_cast<uint32_t>(
    (static_cast<unsigned long long>(magnitude) * waveformPeakFlowUlS) / 32767UL
  );
  int millivolts = 0;
  if (!flowToMillivolts(flowUlS, millivolts)) {
    return 0;
  }
  return sample < 0 ? -millivolts : millivolts;
}

void resetTimingDiagnostics() {
  timingLatenessUs = 0;
  timingMaxLatenessUs = 0;
  timingWriteUs = 0;
  timingMaxWriteUs = 0;
  timingWarningCount = 0;
  timingOverrunCount = 0;
}

uint16_t nextWaveformIndex(uint16_t index) {
  index++;
  if (index >= waveformSampleCount) {
    return 0;
  }
  return index;
}

void startCalibration(char *payload) {
  if (rampUpActive || rampDownActive || runMode != RunStopped) {
    sendCommandError("pump_active");
    return;
  }

  long requestedMillivolts = 0;
  if (!parseLongStrict(payload, 1, 10000, requestedMillivolts)
      || calibrationIndexForVoltage(requestedMillivolts) < 0) {
    protocolViolation("calibration_voltage");
    return;
  }
  if (!isSafeToRun()) {
    sendCommandError("interlock_not_ready");
    return;
  }

  stopWaveform();
  runMode = RunCalibration;
  calibrationTargetMv = static_cast<uint16_t>(requestedMillivolts);
  calibrationStage = CalibrationRampUp;
  calibrationComplete = false;
  rampDownCompletesCalibration = false;
  acceptHostCommand();
  sendCommandAck("action=calibration_start ramp_up_active=1 run_mode=calibration");
  beginRampUp(requestedMillivolts);
}

void updateCalibration(unsigned long nowUs) {
  if (runMode != RunCalibration || calibrationStage != CalibrationFlat) {
    return;
  }
  if (nowUs - calibrationFlatStartedUs < CalibrationFlatTopUs) {
    return;
  }
  if (nowUs - calibrationFlatStartedUs - CalibrationFlatTopUs >= RampUpdatePeriodUs) {
    timingOverrunCount++;
    beginControlledFault(FaultTimingOverrun);
    return;
  }

  calibrationStage = CalibrationRampDown;
  rampDownCompletesCalibration = true;
  beginRampDown(false);
}

void storeCalibrationResult(char *payload) {
  if (runMode != RunStopped || rampUpActive || rampDownActive || !calibrationComplete) {
    sendCommandError("calibration_not_complete");
    return;
  }

  char *voltageToken = strtok(payload, " ");
  char *flowToken = strtok(nullptr, " ");
  char *extraToken = strtok(nullptr, " ");
  long voltageMv = 0;
  unsigned long flowUlS = 0;
  int index = voltageToken == nullptr ? -1 : calibrationIndexForVoltage(atol(voltageToken));
  if (voltageToken == nullptr || flowToken == nullptr || extraToken != nullptr
      || !parseLongStrict(voltageToken, 1, 10000, voltageMv)
      || index < 0 || voltageMv != calibrationTargetMv
      || !parseUnsignedLongStrict(flowToken, flowUlS)
      || flowUlS == 0 || !calibrationFlowOrderValid(index, flowUlS)) {
    protocolViolation("calibration_result");
    return;
  }

  calibrationFlowUlS[index] = static_cast<uint32_t>(flowUlS);
  calibrationValidMask |= 1U << index;
  calibrationComplete = false;
  acceptHostCommand();
  sendCommandAck("action=calibration_saved");
  printTelemetry();
}

void clearCalibration() {
  if (motionCommanded() || rampUpActive || rampDownActive) {
    sendCommandError("pump_active");
    return;
  }
  calibrationValidMask = 0;
  for (uint8_t index = 0; index < CalibrationPointCount; index++) {
    calibrationFlowUlS[index] = 0;
  }
  calibrationComplete = false;
  acceptHostCommand();
  sendCommandAck("action=calibration_cleared");
  printTelemetry();
}

void loadCalibration(char *payload) {
  if (motionCommanded() || rampUpActive || rampDownActive) {
    sendCommandError("pump_active");
    return;
  }

  uint32_t loadedFlows[CalibrationPointCount] = {};
  char *token = strtok(payload, " ");
  for (uint8_t index = 0; index < CalibrationPointCount; index++) {
    unsigned long flowUlS = 0;
    if (token == nullptr || !parseUnsignedLongStrict(token, flowUlS) || flowUlS == 0
        || (index > 0 && flowUlS <= loadedFlows[index - 1])) {
      protocolViolation("calibration_load");
      return;
    }
    loadedFlows[index] = static_cast<uint32_t>(flowUlS);
    token = strtok(nullptr, " ");
  }
  if (token != nullptr) {
    protocolViolation("calibration_load");
    return;
  }

  for (uint8_t index = 0; index < CalibrationPointCount; index++) {
    calibrationFlowUlS[index] = loadedFlows[index];
  }
  calibrationValidMask = (1U << CalibrationPointCount) - 1U;
  calibrationComplete = false;
  acceptHostCommand();
  sendCommandAck("action=calibration_loaded");
  printTelemetry();
}

int calibrationIndexForVoltage(long voltageMv) {
  for (uint8_t index = 0; index < CalibrationPointCount; index++) {
    if (CalibrationVoltagesMv[index] == voltageMv) {
      return index;
    }
  }
  return -1;
}

bool calibrationFlowOrderValid(uint8_t index, uint32_t flowUlS) {
  if (index > 0 && (calibrationValidMask & (1U << (index - 1)))
      && flowUlS <= calibrationFlowUlS[index - 1]) {
    return false;
  }
  if (index + 1 < CalibrationPointCount
      && (calibrationValidMask & (1U << (index + 1)))
      && flowUlS >= calibrationFlowUlS[index + 1]) {
    return false;
  }
  return true;
}

bool calibrationValid() {
  return calibrationValidMask == ((1U << CalibrationPointCount) - 1U);
}

bool flowToMillivolts(uint32_t flowUlS, int &millivolts) {
  if (!calibrationValid()) {
    if (flowUlS > UncalibratedMaxFlowUlS) {
      return false;
    }
    millivolts = static_cast<int>(
      (static_cast<unsigned long long>(flowUlS) * UncalibratedMaxMillivolts)
      / UncalibratedMaxFlowUlS
    );
    return true;
  }
  if (flowUlS > calibrationFlowUlS[CalibrationPointCount - 1]) {
    return false;
  }

  uint32_t lowerFlow = 0;
  uint16_t lowerMv = 0;
  for (uint8_t index = 0; index < CalibrationPointCount; index++) {
    uint32_t upperFlow = calibrationFlowUlS[index];
    uint16_t upperMv = CalibrationVoltagesMv[index];
    if (flowUlS <= upperFlow) {
      millivolts = lowerMv + static_cast<int>(
        (static_cast<unsigned long long>(flowUlS - lowerFlow) * (upperMv - lowerMv))
        / (upperFlow - lowerFlow)
      );
      return true;
    }
    lowerFlow = upperFlow;
    lowerMv = upperMv;
  }
  return false;
}

bool isSafeToRun() {
  return !faultLatched
    && dacPresent
    && digitalRead(PressureLatchPin) == HIGH
    && digitalRead(ServoReadyPin) == LOW
    && digitalRead(ArduinoEnablePin) == LOW;
}

bool motionCommanded() {
  return waveformRunning || runMode != RunStopped || dacMillivolts != 0;
}

void beginRampDown(bool openEnableAfter) {
  if (faultLatched) {
    safeStop();
    return;
  }

  if (rampDownActive) {
    if (openEnableAfter) {
      rampOpenEnableAfter = true;
    }
    return;
  }

  if (!motionCommanded()) {
    if (openEnableAfter) {
      setArduinoEnable(false);
    }
    if (controlledStopActive) {
      completeControlledStop();
    }
    return;
  }

  rampUpActive = false;
  stopWaveform();
  runMode = RunStopped;
  setTriggerLow();
  rampStartMillivolts = dacMillivolts;
  rampDownStartedUs = micros();
  nextRampUpdateUs = rampDownStartedUs + RampUpdatePeriodUs;
  rampDownActive = true;
  rampOpenEnableAfter = openEnableAfter;
}

void updateRampDown(unsigned long nowUs) {
  if (!rampDownActive) {
    return;
  }

  unsigned long elapsedUs = nowUs - rampDownStartedUs;
  if (elapsedUs < RampDurationUs
      && static_cast<long>(nowUs - nextRampUpdateUs) < 0) {
    return;
  }
  recordRampDeadline(nowUs);

  if (elapsedUs >= RampDurationUs) {
    if (!writeMillivoltsFast(0)) {
      return;
    }
    rampUpdateCount++;
    rampDownActive = false;
    bool openEnable = rampOpenEnableAfter;
    rampOpenEnableAfter = false;
    if (openEnable) {
      setArduinoEnable(false);
    }
    if (controlledStopActive) {
      completeControlledStop();
    } else if (rampDownCompletesCalibration) {
      rampDownCompletesCalibration = false;
      calibrationStage = CalibrationComplete;
      calibrationComplete = true;
    }
    return;
  }

  unsigned long remainingUs = RampDurationUs - elapsedUs;
  int scaledMillivolts = static_cast<int>(
    (static_cast<long long>(rampStartMillivolts) * remainingUs) / RampDurationUs
  );
  if (writeMillivoltsFast(scaledMillivolts)) {
    rampUpdateCount++;
    nextRampUpdateUs += RampUpdatePeriodUs;
  }
}

void enforceSafety(unsigned long nowMs) {
  if (faultLatched) {
    safeStop();
    return;
  }

  bool pressureLatchSet = digitalRead(PressureLatchPin) == LOW;
  bool servoReady = digitalRead(ServoReadyPin) == LOW;
  bool arduinoEnable = digitalRead(ArduinoEnablePin) == LOW;

  if (pressureLatchSet) {
    enterFault(FaultOverpressure);
    return;
  }

  bool unexpectedServoReady = !arduinoEnable
    && servoReady
    && nowMs - lastEnableOpenedMs >= ServoReleaseGraceMs;
  if (!unexpectedServoReady) {
    unexpectedServoReadyTracking = false;
  } else if (!unexpectedServoReadyTracking) {
    unexpectedServoReadyTracking = true;
    unexpectedServoReadySinceMs = nowMs;
  } else if (nowMs - unexpectedServoReadySinceMs >= UnexpectedServoReadyDwellMs) {
    enterFault(FaultUnexpectedServoReady);
    return;
  }

  if (arduinoEnable && !servoReady && !motionCommanded()
      && nowMs - lastEnableClosedMs >= ServoEngageGraceMs) {
    enterFault(FaultInterlock);
    return;
  }

  if (motionCommanded() && !isSafeToRun()) {
    enterFault(FaultInterlock);
    return;
  }

  bool hostTimedOut = hostSessionEstablished && nowMs - lastHostContactMs > HostTimeoutMs;
  if (hostTimedOut && !controlledStopActive) {
    beginControlledFault(FaultHeartbeatTimeout);
  }
}

void beginControlledFault(FaultCode code) {
  if (faultLatched || controlledStopActive) {
    return;
  }
  controlledStopActive = true;
  controlledStopCode = code;
  rampDownCompletesCalibration = false;
  calibrationComplete = false;
  calibrationStage = CalibrationIdle;
  beginRampDown(true);
}

void completeControlledStop() {
  faultLatched = true;
  faultCode = controlledStopCode;
  retainFault(faultCode);
  controlledStopActive = false;
  controlledStopCode = FaultNone;
}

void setArduinoEnable(bool enabled) {
  bool wasEnabled = digitalRead(ArduinoEnablePin) == LOW;
  digitalWrite(ArduinoEnablePin, enabled ? LOW : HIGH);
  if (enabled && !wasEnabled) {
    lastEnableClosedMs = millis();
  } else if (!enabled) {
    lastEnableOpenedMs = millis();
  }
}

void safeStop() {
  stopWaveform();
  runMode = RunStopped;
  setTriggerLow();
  rampUpActive = false;
  rampDownActive = false;
  rampOpenEnableAfter = false;
  rampDownCompletesCalibration = false;
  calibrationStage = CalibrationIdle;
  calibrationComplete = false;
  if (dacMillivolts != 0) {
    bestEffortDacZero();
  }
  if (digitalRead(ArduinoEnablePin) == LOW) {
    setArduinoEnable(false);
  }
}

void setTriggerHighUs(long requestedHighUs) {
  triggerHighUs = static_cast<unsigned long>(requestedHighUs);
}

void fireTrigger() {
  if (runMode == RunStopped || faultLatched || rampUpActive || rampDownActive) {
    return;
  }
  digitalWrite(TriggerOutputPin, HIGH);
  triggerActive = true;
  triggerStartedUs = micros();
  triggerCount++;
}

void setTriggerLow() {
  digitalWrite(TriggerOutputPin, LOW);
  triggerActive = false;
}

void updateTrigger(unsigned long nowUs) {
  if (triggerActive && nowUs - triggerStartedUs >= triggerHighUs) {
    setTriggerLow();
  }

  if (runMode == RunConstant
      && !faultLatched
      && !rampUpActive
      && !rampDownActive
      && nowUs - lastConstantTriggerUs >= ConstantTriggerPeriodUs) {
    lastConstantTriggerUs = nowUs;
    fireTrigger();
  }
}

void enterFault(FaultCode code) {
  controlledStopActive = false;
  controlledStopCode = FaultNone;
  faultLatched = true;
  faultCode = code;
  safeStop();
  retainFault(code);
}

bool clearFault() {
  if (faultCode == FaultDacCommunication) {
    dacPresent = probeDac();
    if (!dacPresent || !setDacMillivolts(0)) {
      sendCommandError("dac_communication");
      printTelemetry();
      return false;
    }
    updateFaultClearDwell(millis());
  }
  if (!faultClearConditionsSafe()) {
    sendCommandError("fault_clear_conditions");
    printTelemetry();
    return false;
  }
  if (!faultClearReady(millis())) {
    sendCommandError("fault_clear_dwell");
    printTelemetry();
    return false;
  }

  faultLatched = false;
  faultCode = FaultNone;
  controlledStopActive = false;
  controlledStopCode = FaultNone;
  sendCommandAck("action=fault_cleared");
  printTelemetry();
  return true;
}

bool faultClearConditionsSafe() {
  return digitalRead(PressureLatchPin) == HIGH
    && digitalRead(ServoReadyPin) == HIGH
    && digitalRead(ArduinoEnablePin) == HIGH
    && dacPresent
    && watchdogOk;
}

void updateFaultClearDwell(unsigned long nowMs) {
  if (!faultClearConditionsSafe()) {
    faultClearDwellTracking = false;
    faultClearSafeSinceMs = 0;
    return;
  }
  if (!faultClearDwellTracking) {
    faultClearDwellTracking = true;
    faultClearSafeSinceMs = nowMs;
  }
}

bool faultClearReady(unsigned long nowMs) {
  return faultClearDwellTracking
    && nowMs - faultClearSafeSinceMs >= FaultClearDwellMs;
}

void pulsePressureLatchReset() {
  digitalWrite(PressureLatchResetPin, HIGH);
  delay(100);
  digitalWrite(PressureLatchResetPin, LOW);
}

bool setDacMillivolts(int requestedMillivolts) {
  int targetMillivolts = constrain(requestedMillivolts, -10000, 10000);
  if (!dacPresent) {
    enterDacCommunicationFault();
    return false;
  }

  uint16_t targetData = 0;
  if (targetMillivolts >= 0) {
    if (!setDacRange(1, true)) {
      enterDacCommunicationFault();
      return false;
    }
    targetData = static_cast<uint16_t>(
      (static_cast<long>(targetMillivolts) * 65535L) / 10000L
    );
  } else {
    if (!setDacRange(-1, true)) {
      enterDacCommunicationFault();
      return false;
    }
    targetData = static_cast<uint16_t>(
      (static_cast<long>(-targetMillivolts) * 65535L) / 10000L
    );
  }

  if (!writeDacData(targetData)) {
    enterDacCommunicationFault();
    return false;
  }
  delay(5);
  if (!writeDacData(targetData)) {
    enterDacCommunicationFault();
    return false;
  }
  dacMillivolts = targetMillivolts;
  dacData = targetData;
  return true;
}

bool writeMillivoltsFast(int millivolts) {
  int constrainedMv = constrain(millivolts, -10000, 10000);
  int targetRange = constrainedMv < 0 ? -1 : 1;
  if (!dacPresent || !setDacRange(targetRange, false)) {
    enterDacCommunicationFault();
    return false;
  }
  uint16_t targetData = static_cast<uint16_t>(
    (static_cast<long>(abs(constrainedMv)) * 65535L) / 10000L
  );
  if (!writeDacData(targetData)) {
    enterDacCommunicationFault();
    return false;
  }
  dacMillivolts = constrainedMv;
  dacData = targetData;
  return true;
}

bool setDacRange(int targetRange, bool settle) {
  if (targetRange == dacRange) {
    return true;
  }

  uint8_t mode = targetRange > 0 ? DacPositive10VMode : DacNegative10VMode;
  if (!writeDacRegister(DacModeRegister, &mode, 1)) {
    return false;
  }

  dacRange = targetRange;
  if (settle) {
    delay(25);
  } else {
    delayMicroseconds(200);
  }
  return true;
}

bool probeDac() {
  Wire1.beginTransmission(DacAddress);
  return Wire1.endTransmission() == 0;
}

bool writeDacData(uint16_t data) {
  uint8_t bytes[] = {
    static_cast<uint8_t>(data & 0xFF),
    static_cast<uint8_t>(data >> 8),
  };
  return writeDacRegister(DacDataRegister, bytes, sizeof(bytes));
}

bool writeDacRegister(uint8_t reg, const uint8_t *data, size_t size) {
  Wire1.beginTransmission(DacAddress);
  Wire1.write(reg);
  Wire1.write(data, size);
  bool success = Wire1.endTransmission() == 0;
  if (!success) {
    dacWriteFailureCount++;
  }
  return success;
}

void bestEffortDacZero() {
  if (!dacPresent) {
    return;
  }

  if (dacRange != 1) {
    uint8_t mode = DacPositive10VMode;
    if (!writeDacRegister(DacModeRegister, &mode, 1)) {
      dacPresent = false;
      return;
    }
    dacRange = 1;
    delayMicroseconds(200);
  }

  if (!writeDacData(0)) {
    dacPresent = false;
    return;
  }
  dacMillivolts = 0;
  dacData = 0;
}

void enterDacCommunicationFault() {
  bool newlyLatched = !faultLatched || faultCode != FaultDacCommunication;
  dacPresent = false;
  controlledStopActive = false;
  controlledStopCode = FaultNone;
  faultLatched = true;
  faultCode = FaultDacCommunication;
  rampUpActive = false;
  stopWaveform();
  runMode = RunStopped;
  setTriggerLow();
  rampDownActive = false;
  rampOpenEnableAfter = false;
  setArduinoEnable(false);
  if (newlyLatched) {
    retainFault(FaultDacCommunication);
  }
}

bool parseLongStrict(const char *text, long minimum, long maximum, long &value) {
  if (text == nullptr || text[0] == '\0') {
    return false;
  }
  char *end = nullptr;
  long parsed = strtol(text, &end, 10);
  if (end == text || *end != '\0' || parsed < minimum || parsed > maximum) {
    return false;
  }
  value = parsed;
  return true;
}

bool parseUnsignedLongStrict(const char *text, unsigned long &value) {
  if (text == nullptr || text[0] == '\0' || text[0] == '-') {
    return false;
  }
  char *end = nullptr;
  unsigned long parsed = strtoul(text, &end, 10);
  if (end == text || *end != '\0') {
    return false;
  }
  value = parsed;
  return true;
}

uint32_t retainedChecksum(const RetainedState &state) {
  const uint8_t *bytes = reinterpret_cast<const uint8_t *>(&state);
  uint32_t checksum = 2166136261UL;
  for (size_t index = 0; index < offsetof(RetainedState, checksum); index++) {
    checksum = (checksum ^ bytes[index]) * 16777619UL;
  }
  return checksum;
}

void initializeRetainedState() {
  memset(&retainedState, 0, sizeof(retainedState));
  retainedState.magic = RetainedMagic;
  retainedState.version = RetainedVersion;
  retainedState.checksum = retainedChecksum(retainedState);
}

void loadRetainedState() {
  EEPROM.get(0, retainedState);
  if (retainedState.magic != RetainedMagic
      || retainedState.version != RetainedVersion
      || retainedState.checksum != retainedChecksum(retainedState)) {
    initializeRetainedState();
  }
}

void saveRetainedState() {
  retainedState.checksum = retainedChecksum(retainedState);
  if (watchdogOk) {
    WDT.refresh();
  }
  EEPROM.put(0, retainedState);
  if (watchdogOk) {
    WDT.refresh();
  }
}

void retainFault(FaultCode code) {
  retainedState.lastFault = static_cast<uint8_t>(code);
  retainedState.faultCount++;
  saveRetainedState();
}

void sendCommandAck(const char *details) {
  Serial.print("ACK");
  if (commandSequenced) {
    Serial.print(" seq=");
    Serial.print(commandSequence);
  }
  Serial.print(" v=");
  Serial.print(ProtocolVersion);
  if (details != nullptr && details[0] != '\0') {
    Serial.print(" ");
    Serial.print(details);
  }
  Serial.println();
}

void sendCommandError(const char *code) {
  Serial.print("ERR");
  if (commandSequenced) {
    Serial.print(" seq=");
    Serial.print(commandSequence);
  }
  Serial.print(" v=");
  Serial.print(ProtocolVersion);
  Serial.print(" code=");
  Serial.println(code);
}

void sendHeartbeatAck() {
  if (heartbeatAckLength != 0) {
    return;
  }
  char mode = runMode == RunWaveform ? 'w'
    : (runMode == RunConstant ? 'c' : (runMode == RunCalibration ? 'k' : 's'));
  int length = snprintf(
    heartbeatAckBuffer,
    sizeof(heartbeatAckBuffer),
    "HB4 %c %u %u %u %u %u %u %u %u %u %u %d\n",
    mode,
    faultLatched ? 1 : 0,
    controlledStopActive ? 1 : 0,
    rampUpActive ? 1 : 0,
    rampDownActive ? 1 : 0,
    static_cast<unsigned int>(calibrationStage),
    dacPresent ? 1 : 0,
    waveformRunning ? 1 : 0,
    digitalRead(ArduinoEnablePin) == LOW ? 1 : 0,
    digitalRead(ServoReadyPin) == LOW ? 1 : 0,
    digitalRead(PressureLatchPin) == LOW ? 1 : 0,
    dacMillivolts
  );
  heartbeatAckLength = length > 0 && length < static_cast<int>(sizeof(heartbeatAckBuffer))
    ? static_cast<uint8_t>(length)
    : 0;
  heartbeatAckOffset = 0;
}

void flushHeartbeatAck() {
  if (heartbeatAckLength == 0 || !Serial) {
    return;
  }
  uint8_t remaining = heartbeatAckLength - heartbeatAckOffset;
  uint8_t chunkSize = min(remaining, MaxHeartbeatBytesPerLoop);
  size_t written = Serial.write(
    reinterpret_cast<const uint8_t *>(heartbeatAckBuffer + heartbeatAckOffset),
    chunkSize
  );
  heartbeatAckOffset += static_cast<uint8_t>(written);
  if (heartbeatAckOffset == heartbeatAckLength) {
    heartbeatAckLength = 0;
    heartbeatAckOffset = 0;
  }
}

void protocolViolation(const char *code) {
  protocolErrorCount++;
  if (motionCommanded() && !faultLatched && !controlledStopActive) {
    beginControlledFault(FaultProtocol);
  }
  sendCommandError(code);
}

#define PRINT_TELEMETRY_VALUE(value) do { Serial.write(' '); Serial.print(value); } while (false)

void printTelemetry() {
  unsigned long nowMs = millis();
  Serial.print("TEL4");
  PRINT_TELEMETRY_VALUE(digitalRead(PressureLatchPin));
  PRINT_TELEMETRY_VALUE(digitalRead(ServoReadyPin));
  PRINT_TELEMETRY_VALUE(digitalRead(ArduinoEnablePin));
  PRINT_TELEMETRY_VALUE(digitalRead(PressureLatchResetPin));
  PRINT_TELEMETRY_VALUE(digitalRead(PressureLatchPin) == LOW ? 1 : 0);
  PRINT_TELEMETRY_VALUE(digitalRead(ServoReadyPin) == LOW ? 1 : 0);
  PRINT_TELEMETRY_VALUE(digitalRead(ArduinoEnablePin) == LOW ? 1 : 0);
  PRINT_TELEMETRY_VALUE(dacMillivolts);
  PRINT_TELEMETRY_VALUE(dacData);
  PRINT_TELEMETRY_VALUE(dacRange);
  PRINT_TELEMETRY_VALUE(dacPresent ? 1 : 0);
  PRINT_TELEMETRY_VALUE(waveformLoaded ? 1 : 0);
  PRINT_TELEMETRY_VALUE(waveformRunning ? 1 : 0);
  PRINT_TELEMETRY_VALUE(waveformSampleCount);
  PRINT_TELEMETRY_VALUE(waveformRateHz);
  PRINT_TELEMETRY_VALUE(waveformIndex);
  PRINT_TELEMETRY_VALUE(static_cast<unsigned int>(runMode));
  PRINT_TELEMETRY_VALUE(triggerActive ? 1 : 0);
  PRINT_TELEMETRY_VALUE(triggerHighUs);
  PRINT_TELEMETRY_VALUE(triggerCount);
  PRINT_TELEMETRY_VALUE(rampUpActive ? 1 : 0);
  PRINT_TELEMETRY_VALUE(rampDownActive ? 1 : 0);
  PRINT_TELEMETRY_VALUE(controlledStopActive ? 1 : 0);
  PRINT_TELEMETRY_VALUE(static_cast<unsigned int>(controlledStopCode));
  PRINT_TELEMETRY_VALUE(faultLatched ? 1 : 0);
  PRINT_TELEMETRY_VALUE(static_cast<unsigned int>(faultCode));
  PRINT_TELEMETRY_VALUE(faultClearDwellTracking ? nowMs - faultClearSafeSinceMs : 0);
  PRINT_TELEMETRY_VALUE(faultClearReady(nowMs) ? 1 : 0);
  PRINT_TELEMETRY_VALUE(runMode == RunCalibration ? 1 : 0);
  PRINT_TELEMETRY_VALUE(static_cast<unsigned int>(calibrationStage));
  PRINT_TELEMETRY_VALUE(calibrationComplete ? 1 : 0);
  PRINT_TELEMETRY_VALUE(calibrationTargetMv);
  PRINT_TELEMETRY_VALUE(calibrationValidMask);
  PRINT_TELEMETRY_VALUE(calibrationValid() ? 1 : 0);
  for (uint8_t index = 0; index < CalibrationPointCount; index++) {
    PRINT_TELEMETRY_VALUE(calibrationFlowUlS[index]);
  }
  PRINT_TELEMETRY_VALUE(timingLatenessUs);
  PRINT_TELEMETRY_VALUE(timingMaxLatenessUs);
  PRINT_TELEMETRY_VALUE(timingWriteUs);
  PRINT_TELEMETRY_VALUE(timingMaxWriteUs);
  PRINT_TELEMETRY_VALUE(timingWarningCount);
  PRINT_TELEMETRY_VALUE(timingOverrunCount);
  PRINT_TELEMETRY_VALUE(rampUpdateCount);
  PRINT_TELEMETRY_VALUE(rampMissedDeadlineCount);
  PRINT_TELEMETRY_VALUE(rampMaxLatenessUs);
  PRINT_TELEMETRY_VALUE(serialOverflowCount);
  PRINT_TELEMETRY_VALUE(protocolErrorCount);
  PRINT_TELEMETRY_VALUE(dacWriteFailureCount);
  PRINT_TELEMETRY_VALUE(hostSessionEstablished ? nowMs - lastHostContactMs : 0);
  PRINT_TELEMETRY_VALUE(heartbeatMaxGapMs);
  PRINT_TELEMETRY_VALUE(watchdogOk ? 1 : 0);
  PRINT_TELEMETRY_VALUE(watchdogOk ? WDT.getTimeout() : 0);
  PRINT_TELEMETRY_VALUE(resetCauseCode());
  PRINT_TELEMETRY_VALUE(resetStatus1);
  PRINT_TELEMETRY_VALUE(retainedState.lastFault);
  PRINT_TELEMETRY_VALUE(retainedState.faultCount);
  PRINT_TELEMETRY_VALUE(nowMs);
  Serial.println();
}

#undef PRINT_TELEMETRY_VALUE

uint8_t resetCauseCode() {
  if (resetStatus1 & R_SYSTEM_RSTSR1_WDTRF_Msk) {
    return 1;
  }
  if (resetStatus1 & R_SYSTEM_RSTSR1_IWDTRF_Msk) {
    return 2;
  }
  if (resetStatus1 & R_SYSTEM_RSTSR1_SWRF_Msk) {
    return 3;
  }
  return resetStatus1 == 0 ? 0 : 4;
}

bool watchdogResetDetected() {
  uint8_t code = resetCauseCode();
  return code == 1 || code == 2;
}
