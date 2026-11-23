#include "mhfu_funcs.h"
#include <string.h>

/* Acces little-endian non aligne, sans UB d'aliasing (PSP = little-endian). */
static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static void     wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }

int32_t psp_0885973c_ceil_log2_cap11(uint32_t n)
{
    /* MIPS : v1=1; v0=0; boucle { if (!(v1 <u n)) sortir; v0++; if (!(v0 < 11)) sortir; v1 <<= 1; } */
    uint32_t p = 1;
    int32_t  k = 0;
    while (p < n) {
        k++;
        if (!(k < 11)) break;
        p <<= 1;
    }
    return k;
}

void psp_0884fa40_clear_flag_bit(uint8_t *obj, int32_t bit)
{
    /* MIPS : mask = ~(1 << (bit & 15)) & 0xFFFF ;
     *        idx  = bit >= 0 ? bit >> 4 : (bit + 15) >> 4   (sra = decalage arithmetique)
     *        *(u16*)(obj + 0x508 + 2*idx) &= mask
     * NB : ">>" sur entier signe negatif est "defini par l'implementation" en C, arithmetique
     * sur GCC/Clang/MSVC ; on s'appuie dessus ici car c'est ce que fait le code d'origine. */
    uint16_t mask = (uint16_t)(~(1u << (bit & 15)) & 0xFFFFu);
    int32_t  idx  = (bit >= 0) ? (bit >> 4) : ((bit + 15) >> 4);
    uint8_t *p    = obj + 0x508 + (int64_t)idx * 2;
    wr16(p, (uint16_t)(rd16(p) & mask));
}

int32_t psp_088e12fc_find_slot_by_id(const uint8_t *obj, uint32_t id)
{
    const uint8_t *rec = obj + 0xFC;
    uint16_t key = (uint16_t)(id & 0xFFFFu);
    for (int32_t i = 0x15; i < 0x2B; i++, rec += 12) {
        if ((rd16(rec) & 2u) && rd16(rec + 2) == key)
            return i;
    }
    return -1;
}

float psp_0888decc_dot3(const float *a, const float *b)
{
    volatile float t0 = a[0] * b[0];   /* volatile : interdit toute contraction/reassociation */
    volatile float t1 = a[1] * b[1];
    volatile float t2 = a[2] * b[2];
    volatile float xy = t0 + t1;
    return t2 + xy;
}

float psp_0888defc_dot_xz(const float *a, const float *b)
{
    volatile float t0 = a[0] * b[0];
    volatile float t2 = a[2] * b[2];
    return t0 + t2;
}
