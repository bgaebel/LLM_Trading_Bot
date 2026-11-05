/* Pure logic, no Zephyr APIs: unit-testable on native_sim (tests/filter). */
#ifndef APP_FILTER_H_
#define APP_FILTER_H_

#include <stdint.h>

#define MAVG_WINDOW 4

struct mavg {
	int32_t buf[MAVG_WINDOW];
	int64_t sum;
	uint32_t idx;
	uint32_t count;
};

void mavg_init(struct mavg *f);

/* Adds a sample and returns the mean of the last min(count, MAVG_WINDOW) samples. */
int32_t mavg_update(struct mavg *f, int32_t sample);

#endif /* APP_FILTER_H_ */
