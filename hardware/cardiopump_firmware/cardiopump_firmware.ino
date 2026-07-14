#include <DFRobot_GP8XXX.h>

constexpr uint8_t PressureLatchPin = 9;
constexpr uint8_t ServoReadyPin = 10;
constexpr uint8_t ArduinoEnablePin = 11;
constexpr uint8_t PressureLatchResetPin = 12;
constexpr uint8_t TriggerOutputPin = 7;

constexpr uint8_t DacAddress = 0x58;
constexpr unsigned long TelemetryIntervalMs = 100;
constexpr unsigned long InterlockCheckIntervalMs = 100;
constexpr unsigned long HostTimeoutMs = 500;
constexpr unsigned long ServoReleaseGraceMs = 100;
constexpr unsigned long RampDownDurationMs = 500;
constexpr unsigned long ConstantTriggerPeriodUs = 1000000UL;
constexpr unsigned long DefaultTriggerHighUs = 10000UL;
constexpr uint16_t DefaultWaveformRateHz = 250;
constexpr uint16_t MaxWaveformSamples = 4096;

enum FaultCode : uint8_t {
  FaultNone = 0,
  FaultUnexpectedServoReady = 1,
  FaultOverpressure = 2,
  FaultInterlock = 3,
};

enum RunMode : uint8_t {
  RunStopped = 0,
  RunConstant = 1,
  RunWaveform = 2,
};

int dacMillivolts = 0;
uint16_t dacData = 0;
int dacRange = 0;
bool dacPresent = false;

char commandBuffer[256];
uint8_t commandLength = 0;

int16_t waveform[MaxWaveformSamples];
uint16_t waveformSampleCount = 0;
uint16_t waveformLoadCount = 0;
uint16_t waveformRateHz = DefaultWaveformRateHz;
uint16_t waveformIndex = 0;
long expectedWaveformChecksum = 0;
long waveformChecksum = 0;
bool waveformLoaded = false;
bool waveformLoading = false;
bool waveformRunning = false;
unsigned long waveformPeriodUs = 1000000UL / DefaultWaveformRateHz;
unsigned long lastWaveformUpdateUs = 0;
unsigned long lastHostContactMs = 0;
unsigned long lastEnableOpenedMs = 0;
bool faultLatched = false;
FaultCode faultCode = FaultNone;
bool rampDownActive = false;
bool rampOpenEnableAfter = false;
unsigned long rampDownStartedMs = 0;
int rampStartMillivolts = 0;
RunMode runMode = RunStopped;
unsigned long triggerHighUs = DefaultTriggerHighUs;
bool triggerActive = false;
unsigned long triggerStartedUs = 0;
unsigned long lastConstantTriggerUs = 0;
unsigned long triggerCount = 0;

DFRobot_GP8630N_I2C GP8630N(&Wire1, DacAddress);

void setup() {
  Serial.begin(115200);

  pinMode(ArduinoEnablePin, OUTPUT);
  digitalWrite(ArduinoEnablePin, HIGH);
  lastEnableOpenedMs = millis();

  pinMode(TriggerOutputPin, OUTPUT);
  digitalWrite(TriggerOutputPin, LOW);

  pinMode(PressureLatchPin, INPUT_PULLUP);
  pinMode(ServoReadyPin, INPUT_PULLUP);

  digitalWrite(PressureLatchResetPin, LOW);
  pinMode(PressureLatchResetPin, OUTPUT);
  digitalWrite(PressureLatchResetPin, LOW);

  lastHostContactMs = millis();
  dacPresent = GP8630N.begin() == 0;
  setDacMillivolts(0);

  Serial.println("cardiopump firmware ready");
  printTelemetry();
}

void loop() {
  static unsigned long lastTelemetryMs = 0;
  static unsigned long lastInterlockCheckMs = 0;
  unsigned long nowMs = millis();

  readSerialCommands();
  updateRampDown(nowMs);
  updateTrigger(micros());
  if (!rampDownActive) {
    updateWaveform();
  }

  if (nowMs - lastInterlockCheckMs >= InterlockCheckIntervalMs) {
    lastInterlockCheckMs = nowMs;
    enforceSafety(nowMs);
  }

  if (nowMs - lastTelemetryMs >= TelemetryIntervalMs) {
    lastTelemetryMs = nowMs;
    enforceSafety(nowMs);
    printTelemetry();
  }
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      commandBuffer[commandLength] = '\0';
      handleCommand(commandBuffer);
      commandLength = 0;
      continue;
    }
    if (commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = c;
    }
  }
}

void handleCommand(char *command) {
  if (command[0] == '\0') {
    return;
  }

  recordHostContact();

  if (strncmp(command, "VOLT ", 5) == 0) {
    int requestedMillivolts = atoi(command + 5);
    if (faultLatched && requestedMillivolts != 0) {
      Serial.println("ERR fault_latched=1");
      return;
    }
    if (rampDownActive) {
      if (requestedMillivolts != 0) {
        Serial.println("ERR ramp_down=1");
      } else {
        printTelemetry();
      }
      return;
    }
    stopWaveform();
    runMode = RunStopped;
    setTriggerLow();
    setDacMillivolts(requestedMillivolts);
    printTelemetry();
    return;
  }

  if (strncmp(command, "ENABLE ", 7) == 0) {
    bool enabled = atoi(command + 7) != 0;
    if (enabled && faultLatched) {
      Serial.println("ERR fault_latched=1");
      return;
    }
    if (enabled && rampDownActive) {
      Serial.println("ERR ramp_down=1");
      return;
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
    printTelemetry();
    return;
  }

  if (strcmp(command, "RESET_LATCH") == 0) {
    pulsePressureLatchReset();
    printTelemetry();
    return;
  }

  if (strcmp(command, "CLEAR_FAULT") == 0) {
    clearFault();
    return;
  }

  if (strcmp(command, "GET") == 0) {
    dacPresent = GP8630N.begin() == 0;
    printTelemetry();
    return;
  }

  if (strcmp(command, "HEARTBEAT") == 0) {
    return;
  }

  if (strncmp(command, "TRIGGER_US ", 11) == 0) {
    setTriggerHighUs(atol(command + 11));
    printTelemetry();
    return;
  }

  if (strncmp(command, "WAVE_BEGIN ", 11) == 0) {
    beginWaveformLoad(command + 11);
    return;
  }

  if (strncmp(command, "WAVE_DATA ", 10) == 0) {
    appendWaveformData(command + 10);
    return;
  }

  if (strcmp(command, "WAVE_END") == 0) {
    finishWaveformLoad();
    return;
  }

  if (strcmp(command, "START_WAVE") == 0) {
    startWaveform();
    printTelemetry();
    return;
  }

  if (strncmp(command, "START_CONSTANT ", 15) == 0) {
    startConstant(command + 15);
    printTelemetry();
    return;
  }

  if (strcmp(command, "STOP") == 0) {
    if (faultLatched) {
      safeStop();
    } else {
      beginRampDown(false);
    }
    printTelemetry();
    return;
  }

  Serial.print("ERR unknown_command=");
  Serial.println(command);
}

void recordHostContact() {
  lastHostContactMs = millis();
}

void beginWaveformLoad(char *payload) {
  if (waveformRunning) {
    Serial.println("ERR waveform_running=1");
    return;
  }

  char *rateToken = strtok(payload, " ");
  char *countToken = strtok(nullptr, " ");
  char *checksumToken = strtok(nullptr, " ");
  if (rateToken == nullptr || countToken == nullptr || checksumToken == nullptr) {
    Serial.println("ERR wave_begin_args=1");
    return;
  }

  long requestedRate = atol(rateToken);
  long requestedCount = atol(countToken);
  if (requestedRate <= 0 || requestedCount <= 0 || requestedCount > MaxWaveformSamples) {
    Serial.println("ERR wave_begin_range=1");
    return;
  }

  waveformRateHz = static_cast<uint16_t>(requestedRate);
  waveformPeriodUs = 1000000UL / waveformRateHz;
  waveformSampleCount = static_cast<uint16_t>(requestedCount);
  expectedWaveformChecksum = atol(checksumToken);
  waveformLoadCount = 0;
  waveformChecksum = 0;
  waveformIndex = 0;
  waveformLoaded = false;
  waveformLoading = true;
  Serial.println("ACK wave_begin=1");
}

void appendWaveformData(char *payload) {
  if (!waveformLoading) {
    Serial.println("ERR wave_not_loading=1");
    return;
  }

  char *offsetToken = strtok(payload, " ");
  if (offsetToken == nullptr) {
    Serial.println("ERR wave_data_offset=1");
    return;
  }

  long offset = atol(offsetToken);
  if (offset < 0 || offset >= waveformSampleCount) {
    Serial.println("ERR wave_data_range=1");
    return;
  }

  uint16_t index = static_cast<uint16_t>(offset);
  char *sampleToken = strtok(nullptr, " ");
  while (sampleToken != nullptr) {
    if (index >= waveformSampleCount) {
      Serial.println("ERR wave_data_overflow=1");
      return;
    }
    int value = atoi(sampleToken);
    value = constrain(value, -32767, 32767);
    waveform[index] = static_cast<int16_t>(value);
    waveformChecksum += value;
    waveformLoadCount++;
    index++;
    sampleToken = strtok(nullptr, " ");
  }
}

void finishWaveformLoad() {
  if (!waveformLoading) {
    Serial.println("ERR wave_not_loading=1");
    return;
  }

  waveformLoading = false;
  if (waveformLoadCount != waveformSampleCount || waveformChecksum != expectedWaveformChecksum) {
    waveformLoaded = false;
    Serial.println("ERR wave_checksum=1");
    printTelemetry();
    return;
  }

  waveformLoaded = true;
  waveformIndex = 0;
  Serial.println("ACK wave_loaded=1");
  printTelemetry();
}

void startWaveform() {
  if (rampDownActive) {
    Serial.println("ERR ramp_down=1");
    return;
  }
  if (!waveformLoaded) {
    Serial.println("ERR wave_loaded=0");
    return;
  }
  if (!isSafeToRun()) {
    Serial.println("ERR interlock_not_ready=1");
    return;
  }

  waveformRunning = true;
  runMode = RunWaveform;
  waveformIndex = 0;
  lastWaveformUpdateUs = micros();
  fireTrigger();
  writeFlowQ15Fast(waveform[waveformIndex]);
  waveformIndex = nextWaveformIndex(waveformIndex);
}

void startConstant(char *payload) {
  if (rampDownActive) {
    Serial.println("ERR ramp_down=1");
    return;
  }

  long requestedMillivolts = atol(payload);
  if (requestedMillivolts < -10000 || requestedMillivolts > 10000) {
    Serial.println("ERR voltage_range=1");
    return;
  }
  if (!isSafeToRun()) {
    Serial.println("ERR interlock_not_ready=1");
    return;
  }

  stopWaveform();
  setDacMillivolts(static_cast<int>(requestedMillivolts));
  runMode = RunConstant;
  lastConstantTriggerUs = micros();
  fireTrigger();
}

void stopWaveform() {
  waveformRunning = false;
}

void updateWaveform() {
  if (!waveformRunning) {
    return;
  }

  unsigned long nowUs = micros();
  if (nowUs - lastWaveformUpdateUs < waveformPeriodUs) {
    return;
  }

  lastWaveformUpdateUs += waveformPeriodUs;
  if (waveformIndex == 0) {
    fireTrigger();
  }
  writeFlowQ15Fast(waveform[waveformIndex]);
  waveformIndex = nextWaveformIndex(waveformIndex);
}

uint16_t nextWaveformIndex(uint16_t index) {
  index++;
  if (index >= waveformSampleCount) {
    return 0;
  }
  return index;
}

bool isSafeToRun() {
  return !faultLatched
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
    return;
  }

  stopWaveform();
  runMode = RunStopped;
  setTriggerLow();
  rampStartMillivolts = dacMillivolts;
  rampDownStartedMs = millis();
  rampDownActive = true;
  rampOpenEnableAfter = openEnableAfter;
}

void updateRampDown(unsigned long nowMs) {
  if (!rampDownActive) {
    return;
  }

  unsigned long elapsedMs = nowMs - rampDownStartedMs;
  if (elapsedMs >= RampDownDurationMs) {
    writeFlowQ15Fast(0);
    rampDownActive = false;
    bool openEnable = rampOpenEnableAfter;
    rampOpenEnableAfter = false;
    if (openEnable) {
      setArduinoEnable(false);
    }
    return;
  }

  long remainingMs = RampDownDurationMs - elapsedMs;
  long scaledMillivolts = (static_cast<long>(rampStartMillivolts) * remainingMs)
    / RampDownDurationMs;
  int16_t rampQ15 = static_cast<int16_t>((scaledMillivolts * 32767L) / 10000L);
  writeFlowQ15Fast(rampQ15);
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

  if (!arduinoEnable && servoReady && nowMs - lastEnableOpenedMs >= ServoReleaseGraceMs) {
    enterFault(FaultUnexpectedServoReady);
    return;
  }

  if (motionCommanded() && !isSafeToRun()) {
    enterFault(FaultInterlock);
    return;
  }

  bool hostTimedOut = nowMs - lastHostContactMs > HostTimeoutMs;
  if (hostTimedOut) {
    beginRampDown(true);
  }
}

void setArduinoEnable(bool enabled) {
  digitalWrite(ArduinoEnablePin, enabled ? LOW : HIGH);
  if (!enabled) {
    lastEnableOpenedMs = millis();
  }
}

void safeStop() {
  stopWaveform();
  runMode = RunStopped;
  setTriggerLow();
  rampDownActive = false;
  rampOpenEnableAfter = false;
  if (dacMillivolts != 0) {
    writeFlowQ15Fast(0);
  }
  if (digitalRead(ArduinoEnablePin) == LOW) {
    setArduinoEnable(false);
  }
}

void setTriggerHighUs(long requestedHighUs) {
  if (requestedHighUs < 100 || requestedHighUs > 1000000L) {
    Serial.println("ERR trigger_range=1");
    return;
  }
  triggerHighUs = static_cast<unsigned long>(requestedHighUs);
}

void fireTrigger() {
  if (runMode == RunStopped || faultLatched || rampDownActive) {
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
      && !rampDownActive
      && nowUs - lastConstantTriggerUs >= ConstantTriggerPeriodUs) {
    lastConstantTriggerUs = nowUs;
    fireTrigger();
  }
}

void enterFault(FaultCode code) {
  faultLatched = true;
  faultCode = code;
  safeStop();
}

void clearFault() {
  if (digitalRead(PressureLatchPin) == LOW
      || digitalRead(ServoReadyPin) == LOW
      || digitalRead(ArduinoEnablePin) == LOW) {
    Serial.println("ERR fault_clear_conditions=1");
    printTelemetry();
    return;
  }

  faultLatched = false;
  faultCode = FaultNone;
  Serial.println("ACK fault_cleared=1");
  printTelemetry();
}

void pulsePressureLatchReset() {
  digitalWrite(PressureLatchResetPin, HIGH);
  delay(100);
  digitalWrite(PressureLatchResetPin, LOW);
}

void setDacMillivolts(int requestedMillivolts) {
  dacMillivolts = constrain(requestedMillivolts, -10000, 10000);
  if (!dacPresent) {
    return;
  }

  if (dacMillivolts >= 0) {
    setDacRange(1, true);
    dacData = static_cast<uint16_t>((static_cast<long>(dacMillivolts) * 65535L) / 10000L);
  } else {
    setDacRange(-1, true);
    dacData = static_cast<uint16_t>((static_cast<long>(-dacMillivolts) * 65535L) / 10000L);
  }

  GP8630N.setDACOutData(dacData);
  delay(5);
  GP8630N.setDACOutData(dacData);
}

void writeFlowQ15Fast(int16_t flowQ15) {
  long flow = flowQ15;
  dacMillivolts = static_cast<int>((flow * 10000L) / 32767L);
  if (!dacPresent) {
    return;
  }

  if (flow >= 0) {
    setDacRange(1, false);
    dacData = static_cast<uint16_t>((flow * 65535L) / 32767L);
  } else {
    setDacRange(-1, false);
    dacData = static_cast<uint16_t>((-flow * 65535L) / 32767L);
  }

  GP8630N.setDACOutData(dacData);
}

void setDacRange(int targetRange, bool settle) {
  if (targetRange == dacRange) {
    return;
  }

  if (targetRange > 0) {
    GP8630N.setDACOutRange(GP8630N.eOutputRange10V);
  } else {
    GP8630N.setDACOutRange(GP8630N.eOutputRange_10V);
  }

  dacRange = targetRange;
  if (settle) {
    delay(25);
  } else {
    delayMicroseconds(200);
  }
}

void printTelemetry() {
  bool pressureLatchSet = digitalRead(PressureLatchPin) == LOW;
  bool servoReady = digitalRead(ServoReadyPin) == LOW;
  bool arduinoEnable = digitalRead(ArduinoEnablePin) == LOW;

  Serial.print("TEL pin9=");
  Serial.print(digitalRead(PressureLatchPin));
  Serial.print(" pin10=");
  Serial.print(digitalRead(ServoReadyPin));
  Serial.print(" pin11=");
  Serial.print(digitalRead(ArduinoEnablePin));
  Serial.print(" pin12=");
  Serial.print(digitalRead(PressureLatchResetPin));
  Serial.print(" pressure_latch_set=");
  Serial.print(pressureLatchSet ? 1 : 0);
  Serial.print(" servo_ready=");
  Serial.print(servoReady ? 1 : 0);
  Serial.print(" arduino_enable=");
  Serial.print(arduinoEnable ? 1 : 0);
  Serial.print(" dac_mv=");
  Serial.print(dacMillivolts);
  Serial.print(" dac_data=");
  Serial.print(dacData);
  Serial.print(" dac_range=");
  Serial.print(dacRange > 0 ? "0to10" : "neg10to0");
  Serial.print(" dac_ok=");
  Serial.print(dacPresent ? 1 : 0);
  Serial.print(" wave_loaded=");
  Serial.print(waveformLoaded ? 1 : 0);
  Serial.print(" wave_running=");
  Serial.print(waveformRunning ? 1 : 0);
  Serial.print(" wave_samples=");
  Serial.print(waveformSampleCount);
  Serial.print(" wave_rate_hz=");
  Serial.print(waveformRateHz);
  Serial.print(" wave_index=");
  Serial.print(waveformIndex);
  Serial.print(" run_mode=");
  Serial.print(runModeName());
  Serial.print(" trigger_active=");
  Serial.print(triggerActive ? 1 : 0);
  Serial.print(" trigger_high_us=");
  Serial.print(triggerHighUs);
  Serial.print(" trigger_count=");
  Serial.print(triggerCount);
  Serial.print(" ramp_down_active=");
  Serial.print(rampDownActive ? 1 : 0);
  Serial.print(" fault_latched=");
  Serial.print(faultLatched ? 1 : 0);
  Serial.print(" fault_code=");
  Serial.print(faultCodeName());
  Serial.print(" uptime_ms=");
  Serial.println(millis());
}

const char* faultCodeName() {
  switch (faultCode) {
    case FaultUnexpectedServoReady:
      return "unexpected_servo_ready";
    case FaultOverpressure:
      return "overpressure";
    case FaultInterlock:
      return "interlock";
    default:
      return "none";
  }
}

const char* runModeName() {
  switch (runMode) {
    case RunConstant:
      return "constant";
    case RunWaveform:
      return "waveform";
    default:
      return "stopped";
  }
}
