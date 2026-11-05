#include <stdint.h>
#include <stdlib.h>

#include "logic.h"
#include "unity.h"

static void test_crc8_check_value(void)
{
    static const uint8_t data[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};

    TEST_ASSERT_EQUAL_HEX8(0xF4, logic_crc8(data, sizeof(data)));
}

static void test_crc8_empty_is_zero(void)
{
    TEST_ASSERT_EQUAL_HEX8(0x00, logic_crc8(NULL, 0));
}

void app_main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_crc8_check_value);
    RUN_TEST(test_crc8_empty_is_zero);
    // On the linux target app_main runs inside the FreeRTOS POSIX port; exit() ends the process with the
    // failure count as exit code so CI fails on red tests.
    exit(UNITY_END());
}
