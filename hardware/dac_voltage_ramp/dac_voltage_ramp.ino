#include <DFRobot_GP8XXX.h>

constexpr uint8_t RelayControlPin = 10;
constexpr uint8_t OpenRelayPin = 11;

constexpr uint8_t DacAddress = 0x58;
constexpr unsigned long StepIntervalMs = 1000;

constexpr int Sequence[] = {
  1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
  -10, -9, -8, -7, -6, -5, -4, -3, -2, -1, 0
};
constexpr uint8_t SequenceLength = sizeof(Sequence) / sizeof(Sequence[0]);

uint8_t sequenceIndex = 0;
unsigned long lastStepMs = 0;
int currentRange = 0;
DFRobot_GP8630N_I2C GP8630N(&Wire1, DacAddress);

void setup() {
  Serial.begin(115200);

  digitalWrite(OpenRelayPin, HIGH);
  pinMode(OpenRelayPin, OUTPUT);
  digitalWrite(OpenRelayPin, HIGH);

  digitalWrite(RelayControlPin, LOW);
  pinMode(RelayControlPin, OUTPUT);
  digitalWrite(RelayControlPin, LOW);

  while (GP8630N.begin() != 0) {
    Serial.println("GP8630N communication failed");
    delay(1000);
  }

  Serial.println("GP8630N DAC voltage ramp");
  Serial.println("Sequence: 1..10, -10..0 V, one step per second");
  setOutputVolts(Sequence[sequenceIndex]);
}

void loop() {
  unsigned long nowMs = millis();
  if (nowMs - lastStepMs < StepIntervalMs) {
    return;
  }

  lastStepMs = nowMs;
  sequenceIndex = (sequenceIndex + 1) % SequenceLength;
  setOutputVolts(Sequence[sequenceIndex]);
}

void setOutputVolts(int volts) {
  int targetRange = 1;
  uint16_t data = 0;

  if (volts >= 0) {
    targetRange = 1;
    data = static_cast<uint16_t>((static_cast<long>(volts) * 65535L) / 10L);
  } else {
    targetRange = -1;
    data = static_cast<uint16_t>((static_cast<long>(-volts) * 65535L) / 10L);
  }

  setOutputRange(targetRange);
  GP8630N.setDACOutData(data);
  delay(5);
  GP8630N.setDACOutData(data);

  Serial.print("volts=");
  Serial.print(volts);
  Serial.print(" range=");
  Serial.print(targetRange > 0 ? "0..10" : "-10..0");
  Serial.print(" data=");
  Serial.println(data);
}

void setOutputRange(int targetRange) {
  if (targetRange == currentRange) {
    return;
  }

  if (targetRange > 0) {
    GP8630N.setDACOutRange(GP8630N.eOutputRange10V);
  } else {
    GP8630N.setDACOutRange(GP8630N.eOutputRange_10V);
  }

  currentRange = targetRange;
  delay(25);
}
