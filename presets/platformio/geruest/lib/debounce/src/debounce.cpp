#include "debounce.h"

Debounce::Debounce(uint32_t settle_ms, bool initial_state)
    : settle_ms_(settle_ms),
      stable_(initial_state),
      candidate_(initial_state),
      candidate_since_ms_(0) {}

bool Debounce::update(bool raw, uint32_t now_ms) {
  if (raw != candidate_) {
    candidate_ = raw;
    candidate_since_ms_ = now_ms;
    return false;
  }
  // Unsigned subtraction stays correct across the millis() wrap at 2^32.
  if (candidate_ != stable_ && now_ms - candidate_since_ms_ >= settle_ms_) {
    stable_ = candidate_;
    return true;
  }
  return false;
}
