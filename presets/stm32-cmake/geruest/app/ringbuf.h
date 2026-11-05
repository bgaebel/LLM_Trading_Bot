// Byte ring buffer with caller-provided storage.
// Pure logic: no HAL includes, so it builds for the target and the host tests.
#ifndef APP_RINGBUF_H
#define APP_RINGBUF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t *buf;
    size_t capacity;
    size_t head; // next write index
    size_t tail; // next read index
    size_t count;
} ringbuf_t;

void ringbuf_init(ringbuf_t *rb, uint8_t *storage, size_t capacity);
bool ringbuf_put(ringbuf_t *rb, uint8_t byte); // false if full
bool ringbuf_get(ringbuf_t *rb, uint8_t *out); // false if empty
size_t ringbuf_count(const ringbuf_t *rb);

#ifdef __cplusplus
}
#endif

#endif // APP_RINGBUF_H
