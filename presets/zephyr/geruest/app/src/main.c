#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "filter.h"

LOG_MODULE_REGISTER(app, LOG_LEVEL_INF);

int main(void)
{
	struct mavg f;

	mavg_init(&f);
	LOG_INF("app started on %s", CONFIG_BOARD);

	while (true) {
		int32_t sample = (int32_t)(k_uptime_get_32() % 100U);

		LOG_INF("sample=%d avg=%d", (int)sample, (int)mavg_update(&f, sample));
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
