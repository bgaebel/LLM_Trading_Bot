#include <gtest/gtest.h>

#include "ringbuf.h"

TEST(RingBuf, StartsEmpty) {
    uint8_t storage[4];
    ringbuf_t rb;
    ringbuf_init(&rb, storage, sizeof storage);
    uint8_t out = 0;
    EXPECT_EQ(ringbuf_count(&rb), 0u);
    EXPECT_FALSE(ringbuf_get(&rb, &out));
}

TEST(RingBuf, RejectsPutWhenFull) {
    uint8_t storage[2];
    ringbuf_t rb;
    ringbuf_init(&rb, storage, sizeof storage);
    EXPECT_TRUE(ringbuf_put(&rb, 1));
    EXPECT_TRUE(ringbuf_put(&rb, 2));
    EXPECT_FALSE(ringbuf_put(&rb, 3));
}

TEST(RingBuf, PreservesOrderAcrossWrap) {
    uint8_t storage[3];
    ringbuf_t rb;
    ringbuf_init(&rb, storage, sizeof storage);
    uint8_t out = 0;
    for (uint8_t i = 0; i < 10; i++) {
        ASSERT_TRUE(ringbuf_put(&rb, i));
        ASSERT_TRUE(ringbuf_get(&rb, &out));
        EXPECT_EQ(out, i);
    }
}
