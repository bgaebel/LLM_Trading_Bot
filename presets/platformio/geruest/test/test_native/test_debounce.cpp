#include <unity.h>

#include "debounce.h"

void setUp() {}
void tearDown() {}

void test_bounce_shorter_than_settle_time_is_ignored() {
  Debounce d(30, true);
  TEST_ASSERT_FALSE(d.update(false, 0));
  TEST_ASSERT_FALSE(d.update(true, 10));
  TEST_ASSERT_FALSE(d.update(true, 100));
  TEST_ASSERT_TRUE(d.state());
}

void test_stable_change_is_reported_once() {
  Debounce d(30, true);
  TEST_ASSERT_FALSE(d.update(false, 0));
  TEST_ASSERT_FALSE(d.update(false, 29));
  TEST_ASSERT_TRUE(d.update(false, 30));
  TEST_ASSERT_FALSE(d.state());
  TEST_ASSERT_FALSE(d.update(false, 31));
}

void test_timer_wraparound() {
  Debounce d(30, true);
  TEST_ASSERT_FALSE(d.update(false, 0xFFFFFFF0u));
  TEST_ASSERT_TRUE(d.update(false, 0x0000000Eu));  // 30 ms later, after wrap
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_bounce_shorter_than_settle_time_is_ignored);
  RUN_TEST(test_stable_change_is_reported_once);
  RUN_TEST(test_timer_wraparound);
  return UNITY_END();
}
