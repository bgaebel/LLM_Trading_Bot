#include <stdint.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "logic.h"

static const char *TAG = "app";

void app_main(void)
{
    static const uint8_t frame[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};

    while (1) {
        ESP_LOGI(TAG, "crc8 = 0x%02X", logic_crc8(frame, sizeof(frame)));
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
