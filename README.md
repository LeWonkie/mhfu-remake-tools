# Outils du projet MHFU

- [Atelier PSPRecomp](outils/atelier-mhfu) — interface, diagnostic, scénarios et tests.
- [Sondes de développement](work) — inspection, validation et benchmarks.
- [Runtime natif](native-runtime) — diagnostic, vérifications, comparaison d'images et synthèse.
- [Analyse](analyse) — ELF/MIPS, NID, overlays et banc C/Python.
- [PPSSPP](remake-ppsspp) — scripts de lancement et de configuration.
- [Migration](MHFU_REMAKE_CLAUDE_MIGRATION) — validation des fichiers locaux et extraction audio.
- [Test OpenGL](https://github.com/LeWonkie/opengl-lighting-test) — éclairage sur un triangle animé, dans son dépôt dédié.

Suivre le [README de l'Atelier](outils/atelier-mhfu/README.md) pour l'arborescence locale. Certains outils attendent les builds PSPRecomp ou du runtime natif, leurs dépendances, une copie locale du jeu et des registres de travail générés. Le banc C utilise les vecteurs de référence de l'archive d'analyse locale.

Pour générer le catalogue et le registre d’objectifs à partir de ta copie locale, exécute ces commandes depuis la racine du projet.

```powershell
python outils/atelier-mhfu/quest_catalog.py --disc game-mhfu/disc.iso
python outils/atelier-mhfu/derive_objectives.py --disc game-mhfu/disc.iso
```
