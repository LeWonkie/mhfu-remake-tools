# usage: run.sh <fixture-run> <new-out-name> <rounds> ; prints a compact summary only
set -e
R="/c/Users/Hugo/Documents/MHFU REMAKE/native-runtime"; CX="/c/Users/Hugo/.codex/.chatgpt-projects/g-p-6ad4298d549c8191a7869ece6a415f33"
PY=C:/Users/Hugo/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe
SRC="$R/builds/$1"; OUT="$R/builds/$2"
[ -e "$OUT" ] && { echo "exists"; exit 1; }
mkdir "$OUT"
for n in build.json variants.json observed-methods.json capture.ram.bin interpreted-roots.txt; do [ -f "$SRC/$n" ] && cp "$SRC/$n" "$OUT/"; done
for n in overlays flash0 modules; do cp -r "$SRC/$n" "$OUT/"; done
cp -r "$R/builds/checkpoint14-saved-character/${MS0:-initial-ms0}" "$OUT/ms0"
export MHFU_LIBC_EPOCH_US=${EPOCH:-1792000000000000}
export MHFU_INPUT="$(cat "${INPUT:-$R/src/input_character_creation_yes.txt}")"
start=$(date +%s)
"$PY" "$R/src/advance_native_bootstrap.py" --elf "$CX/work/extracted/PSP_GAME/SYSDIR/EBOOT.BIN" --data "$CX/work/extracted/PSP_GAME/USRDIR/DATA.BIN" \
  --zig "$CX/work/reprise-20261018/dependencies/ziglang/zig.exe" --out "$OUT" --rounds $3 --limit 800000 --optimization ${OPT:-2} \
  --seed-roots "$R/src/character_creation_roots.txt" --dispatch-fuel 200000000 --dispatches ${DISPATCHES:-250000} --frame-dump 60 > "$OUT/advance.log" 2>&1 || echo "ADVANCE EXIT $?"
echo "wall=$(( $(date +%s)-start ))s"
"$PY" "$R/tools/summary.py" "$OUT"
