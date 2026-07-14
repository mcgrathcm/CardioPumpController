#include <Wire.h>

constexpr uint8_t ExpectedAddress = 0x58;

void setup() {
  Serial.begin(115200);
  unsigned long start = millis();
  while (!Serial && millis() - start < 3000) {
  }

  Wire1.begin();
  Serial.println("Qwiic Wire1 I2C scan");
}

void loop() {
  bool foundExpected = false;
  int count = 0;

  Serial.println("scan begin");
  for (uint8_t address = 1; address < 127; address++) {
    Wire1.beginTransmission(address);
    uint8_t error = Wire1.endTransmission();

    if (error == 0) {
      count++;
      Serial.print("found 0x");
      if (address < 16) {
        Serial.print("0");
      }
      Serial.println(address, HEX);
      if (address == ExpectedAddress) {
        foundExpected = true;
      }
    }
  }

  Serial.print("device count: ");
  Serial.println(count);
  Serial.print("expected 0x58: ");
  Serial.println(foundExpected ? "present" : "missing");
  delay(2000);
}
