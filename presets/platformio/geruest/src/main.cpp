#include <Arduino.h>

#include "debounce.h"

namespace {
constexpr uint8_t kButtonPin = 0;  // BOOT button on most ESP32 dev boards
constexpr uint32_t kDebounceMs = 30;

Debounce button(kDebounceMs, true);  // input is pulled up: idle = high
}  // namespace

void setup() {
  Serial.begin(115200);
  pinMode(kButtonPin, INPUT_PULLUP);
}

void loop() {
  if (button.update(digitalRead(kButtonPin) == HIGH, millis())) {
    Serial.println(button.state() ? "released" : "pressed");
  }
  delay(1);
}
