#pragma once

#include <stdint.h>

// Pure logic, no Arduino/HAL dependency: testable on the host (env:native).
class Debounce {
 public:
  Debounce(uint32_t settle_ms, bool initial_state);

  // Feed a raw sample with a monotonic timestamp. Returns true if the
  // debounced state changed with this sample.
  bool update(bool raw, uint32_t now_ms);

  bool state() const { return stable_; }

 private:
  uint32_t settle_ms_;
  bool stable_;
  bool candidate_;
  uint32_t candidate_since_ms_;
};
