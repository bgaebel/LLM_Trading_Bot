#include "filter.h"

#include <string.h>

void mavg_init(struct mavg *f)
{
	memset(f, 0, sizeof(*f));
}

int32_t mavg_update(struct mavg *f, int32_t sample)
{
	if (f->count == MAVG_WINDOW) {
		f->sum -= f->buf[f->idx];
	} else {
		f->count++;
	}
	f->buf[f->idx] = sample;
	f->sum += sample;
	f->idx = (f->idx + 1U) % MAVG_WINDOW;

	return (int32_t)(f->sum / (int64_t)f->count);
}
