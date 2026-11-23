# MHFU Project Tools

- [PSPRecomp Workshop](outils/atelier-mhfu) — graphical interface, diagnostics, scenarios, and tests.
- [Development probes](work) — inspection, validation, and benchmarks.
- [Native runtime tools](native-runtime) — diagnostics, validation, image comparison, and reports.
- [Analysis](analyse) — ELF/MIPS, NIDs, overlays, and a C/Python test harness.
- [PPSSPP](remake-ppsspp) — launch and configuration scripts.
- [Migration tools](MHFU_REMAKE_CLAUDE_MIGRATION) — local file validation and audio extraction.
- [OpenGL test](https://github.com/LeWonkie/opengl-lighting-test) — animated-triangle lighting verification in a dedicated repository.

See the [Workshop README](outils/atelier-mhfu/README.md) for the local directory layout. Some tools require PSPRecomp or native runtime builds, their dependencies, a local copy of the game, and generated registries. The C test harness uses reference vectors from the local analysis archive.

Run these commands from the project root to generate the quest catalog and objective registry from your local copy.

```powershell
python outils/atelier-mhfu/quest_catalog.py --disc game-mhfu/disc.iso
python outils/atelier-mhfu/derive_objectives.py --disc game-mhfu/disc.iso
```
