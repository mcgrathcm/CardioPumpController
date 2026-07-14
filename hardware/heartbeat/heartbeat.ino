void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}

void loop() {
  static unsigned long count = 0;

  digitalWrite(LED_BUILTIN, HIGH);
  Serial.print("heartbeat ");
  Serial.println(count++);
  delay(250);

  digitalWrite(LED_BUILTIN, LOW);
  delay(750);
}
