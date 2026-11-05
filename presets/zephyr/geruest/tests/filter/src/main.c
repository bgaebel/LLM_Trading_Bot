#include <zephyr/ztest.h>

#include "filter.h"

static struct mavg f;

static void before(void *fixture)
{
	ARG_UNUSED(fixture);
	mavg_init(&f);
}

ZTEST_SUITE(filter, NULL, NULL, before, NULL, NULL);

ZTEST(filter, test_first_sample_is_returned)
{
	zassert_equal(mavg_update(&f, 42), 42);
}

ZTEST(filter, test_partial_window_averages_seen_samples)
{
	mavg_update(&f, 10);
	zassert_equal(mavg_update(&f, 20), 15);
}

ZTEST(filter, test_window_drops_oldest_sample)
{
	for (int i = 0; i < MAVG_WINDOW; i++) {
		mavg_update(&f, 0);
	}
	zassert_equal(mavg_update(&f, 400), 400 / MAVG_WINDOW);
}

ZTEST(filter, test_negative_samples)
{
	mavg_update(&f, -10);
	zassert_equal(mavg_update(&f, -30), -20);
}
