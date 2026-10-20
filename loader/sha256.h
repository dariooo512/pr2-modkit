#ifndef PR2N_SHA256_H
#define PR2N_SHA256_H
#include <stddef.h>
#include <stdint.h>

typedef struct { uint32_t h[8]; uint64_t len; uint8_t buf[64]; size_t fill; } Sha256;

void sha256_init(Sha256 *s);
void sha256_update(Sha256 *s, const void *data, size_t n);
void sha256_final(Sha256 *s, uint8_t out[32]);
void sha256_tohex(const uint8_t d[32], char hex[65]);
void sha256_hex(const void *data, size_t n, char hex[65]);   /* lower-case hex */

#endif
