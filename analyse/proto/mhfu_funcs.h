/*
 * mhfu_funcs.h - Reimplementations C99 de fonctions MIPS extraites de EBOOT.BIN
 * (module interne "MonsterHunterPortable2ndG", ELF PSP non chiffre).
 *
 * IMPORTANT
 *  - Les noms ci-dessous sont des noms DESCRIPTIFS choisis par l'auteur : l'ELF est
 *    "stripped", aucun symbole d'origine n'est disponible. L'adresse d'origine figure
 *    dans chaque nom pour la tracabilite.
 *  - Chaque fonction a ete validee contre l'execution des octets MIPS ORIGINAUX
 *    (emulateur Unicorn) : voir diff_test.py. Ce que la fonction signifie pour le
 *    gameplay n'est PAS etabli ; seul son comportement l'est.
 *  - Les pointeurs sont des pointeurs hote. Dans un portage reel, les adresses
 *    "guest" PSP (0x08800000-0x0A000000) devront etre traduites (base + offset).
 */
#ifndef MHFU_FUNCS_H
#define MHFU_FUNCS_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* sub_0885973c : plus petit k tel que (1<<k) >= n, plafonne a 11. n=0 ou 1 -> 0.
 * Arguments MIPS : $a0 (inutilise), $a1 = n.  Retour : $v0. */
int32_t psp_0885973c_ceil_log2_cap11(uint32_t n);

/* sub_0884fa40 : met a 0 le bit `bit` d'un tableau de uint16 situe a obj+0x508.
 * Arguments MIPS : $a0 = obj, $a1 = bit (signe). Ecrit : u16 a obj + 0x508 + 2*idx. */
void    psp_0884fa40_clear_flag_bit(uint8_t *obj, int32_t bit);

/* sub_088e12fc : cherche, parmi 22 enregistrements de 12 octets a obj+0xFC, le premier
 * dont (u16@+0 & 2) != 0 et u16@+2 == (id & 0xFFFF). Retourne l'indice 21..42, ou -1.
 * Arguments MIPS : $a0 = obj, $a1 = id. */
int32_t psp_088e12fc_find_slot_by_id(const uint8_t *obj, uint32_t id);

/* sub_0888decc : produit scalaire 3D. Ordre d'evaluation EXACT du code MIPS :
 *   r = a[2]*b[2] + ( a[0]*b[0] + a[1]*b[1] )
 * Arguments MIPS : $a0 (inutilise), $a1 = a, $a2 = b. Retour : $f0. */
float   psp_0888decc_dot3(const float *a, const float *b);

/* sub_0888defc : produit scalaire dans le plan (composantes 0 et 2) :
 *   r = a[0]*b[0] + a[2]*b[2]                                        */
float   psp_0888defc_dot_xz(const float *a, const float *b);

#ifdef __cplusplus
}
#endif
#endif
