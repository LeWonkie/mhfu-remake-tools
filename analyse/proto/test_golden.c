/* test_golden.c - rejoue golden.bin (sorties de l'ORIGINAL MIPS, generees par diff_test.py)
 * contre les reimplementations C. Aucune dependance externe. Code retour 0 = tout OK. */
#include "mhfu_funcs.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t u32(const uint8_t *p) { return p[0] | (p[1]<<8) | (p[2]<<16) | ((uint32_t)p[3]<<24); }
static uint32_t fbits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static int bad = 0;
#define CHECK(c, ...) do { if (!(c)) { if (bad++ < 5) { printf("  DIVERGENCE: " __VA_ARGS__); printf("\n"); } } } while (0)

int main(int argc, char **argv)
{
    const char *path = argc > 1 ? argv[1] : "golden.bin";
    FILE *f = fopen(path, "rb"); if (!f) { perror(path); return 2; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc(sz); if (fread(buf, 1, sz, f) != (size_t)sz) return 2; fclose(f);
    if (memcmp(buf, "MHFUGOLD1", 9)) { puts("mauvais fichier"); return 2; }
    const uint8_t *p = buf + 12;
    static uint8_t obj[0x1000];
    long total = 0;
    for (int fn = 0; fn < 5; fn++) {
        uint32_t tag = (uint32_t)p[0]<<24 | p[1]<<16 | p[2]<<8 | p[3]; uint32_t n = u32(p+4); p += 8;
        int before = bad;
        for (uint32_t i = 0; i < n; i++) {
            switch (fn) {
            case 0: { uint32_t in = u32(p); int32_t w = (int32_t)u32(p+4); p += 8;
                      CHECK(psp_0885973c_ceil_log2_cap11(in) == w, "n=%u", in); break; }
            case 1: { int32_t bit = (int32_t)u32(p); memset(obj, 0, sizeof obj); memcpy(obj+0x500, p+4, 32);
                      psp_0884fa40_clear_flag_bit(obj, bit);
                      CHECK(memcmp(obj+0x500, p+36, 32) == 0, "bit=%d", bit); p += 68; break; }
            case 2: { uint32_t id = u32(p); memset(obj, 0, sizeof obj); memcpy(obj+0xFC, p+4, 264);
                      CHECK(psp_088e12fc_find_slot_by_id(obj, id) == (int32_t)u32(p+268), "id=%#x", id); p += 272; break; }
            default: { float a[3], b[3]; memcpy(a, p, 12); memcpy(b, p+12, 12); uint32_t w = u32(p+24); p += 28;
                      float r = (fn == 3) ? psp_0888decc_dot3(a, b) : psp_0888defc_dot_xz(a, b);
                      CHECK(fbits(r) == w, "attendu %08x obtenu %08x", w, fbits(r)); break; }
            }
        }
        printf("  fonction sub_%08x : %6u vecteurs, %s\n", tag, n, bad == before ? "OK" : "ECHEC");
        total += n;
    }
    printf("TOTAL %ld vecteurs, %d divergence(s)\n", total, bad);
    free(buf); return bad != 0;
}
