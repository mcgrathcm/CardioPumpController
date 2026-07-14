#include <DFRobot_GP8XXX.h>

constexpr uint8_t ModuleI2cAddress = 0x58;

DFRobot_GP8630N_I2C GP8630N(&Wire1, ModuleI2cAddress);

void setup() {
  Serial.begin(9600);

  while (GP8630N.begin() != 0) {
    Serial.println("Communication with the device failed.");
    delay(1000);
  }

  Serial.println("Successful connection!");
  GP8630N.setDACOutRange(GP8630N.eOutputRange10V);
  GP8630N.setDACOutData(65535);
  Serial.println("Set GP8630N output to 10 V.");
}

void loop() {
}
