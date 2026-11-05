#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* CRC-8, polynomial 0x07, init 0x00, no reflection, no final XOR. */
uint8_t logic_crc8(const uint8_t *data, size_t len);

#ifdef __cplusplus
}
#endif
