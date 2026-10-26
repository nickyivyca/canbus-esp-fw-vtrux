#!/usr/bin/env bash
# Baseline part B, the X2 jammer rows B5-B8 (baseline_run_list.md), the same runs
# as on c65c45e, with the X2 under the gen-inhibit tester's custody. Gen-inhibit
# tester, 2026-10-16 (on c65c45e these were run by hand; this records them).
#
#   GI_COMMIT=e3d9437 REVERSE_IT_DIR=<reverse-it root> GI_WICAN_SSID=... INTERPOSER_CODE=... \
#     bash baseline_b_jam.sh custody|jam|restore
#
#   custody  dump the X2 (two reads must match; the dump holds NVS and is
#            SENSITIVE: only its path, size and sha256 are recorded), flash the
#            jammer a89ece8c, read its banner (x2_jammer.py id)
#   jam      B5 livetrain 200/s (--p 5000) once and 500/s (--p 2000) three times;
#            B6 threshold n10 at 1.0 s with leads 1.5/5/8.5, controls n9 at 1.1 s
#            with leads 1.5/5, 2 arms each, --reset-wican; B7 busoff-rearm
#            --expect reinstalls; B8 busoff --control; 3 arms where not stated
#   restore  restore from the dump (verify-flash digest), hold it in ROM, then
#            release it with the PCAN ACKing and read 0x7F7 off the wire
# Run from the reverse-it root. Each step's stdout is kept in
# runs/baseline_<commit>/bench/ (x2/ for custody).
set -u
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, gen-inhibit/tools/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
C=${GI_COMMIT:?set GI_COMMIT}
D=${GI_OUT:-$DATA/runs/baseline_$C/bench}   # GI_OUT: the chain runner's check folder (2026-10-18)
X=$D/x2
JAMMER_MD5=a89ece8c467f90723a3ac26e72924aa8
mkdir -p "$X"
PW="12 V on OBD pin 16, USB unplugged (no COM4); WiCAN $C _man; X2 jammer a89ece8c (custody)"
case ${1:?custody, jam or restore} in
  custody)
    py -3.14 -B $A/x2_flash_custody.py dump --expect-elf 756d6714 > "$X/custody_dump.txt" 2>&1; echo "dump exit $?"
    rec=$(ls -t "${GI_RUNS_DIR:-$DATA/runs}"/x2_flash_custody_dump_*.json | head -1)
    [ -n "$rec" ] || { echo "no dump record in ${GI_RUNS_DIR:-$DATA/runs}: stop"; exit 1; }
    grep -q '"verdict": "PASS"\|-> PASS' "$X/custody_dump.txt" || { echo "dump did not PASS: stop"; exit 1; }
    py -3.14 -B $A/x2_flash_custody.py flash-jammer --dump-record "$rec" --build $JAMMER_MD5 > "$X/custody_flash_jammer.txt" 2>&1
    echo "flash-jammer exit $?"
    py -3.14 -B $A/x2_jammer.py id > "$X/jammer_id.txt" 2>&1; echo "jammer id exit $?"
    echo "dump record: $rec"
    ;;
  jam)
    j() { local n=$1; shift; echo "$(date +%H:%M:%S) $n"; py -3.14 -B $A/x2_jam_cases.py "$@" --power "$PW" > "$D/$n.txt" 2>&1; echo "  exit $?"; }
    j B5_livetrain_p5000 livetrain --arms 3 --p 5000 --ms 5000
    for r in 1 2 3; do j B5_livetrain_p2000_run$r livetrain --arms 3 --p 2000 --ms 5000; done
    for l in 1.5 5 8.5; do j B6_threshold_n10_lead$l threshold --arms 2 --n 10 --interval-ms 1000 --lead-s $l --reset-wican; done
    for l in 1.5 5; do j B6_threshold_n9_lead$l threshold --arms 2 --n 9 --interval-ms 1100 --lead-s $l --reset-wican; done
    j B7_busoff_rearm busoff-rearm --arms 3 --expect reinstalls
    j B8_busoff busoff --arms 3 --control
    ;;
  restore)
    rec=${2:?restore needs the dump record}
    py -3.14 -B $A/x2_flash_custody.py restore --dump-record "$rec" > "$X/custody_restore.txt" 2>&1; echo "restore exit $?"
    grep -q "Verification successful (digest matched)" "$X/custody_restore.txt" && echo "digest matched" || echo "DIGEST NOT CONFIRMED"
    py -3.14 -B $A/x2_hold.py hold > "$X/restore_hold.txt" 2>&1; echo "hold exit $?"
    mv "${GI_RUNS_DIR:-$DATA/runs}"/x2_hold_hold_*.json "$X/" 2>/dev/null
    bash $A/x2_release_identify.sh "$X" pcan
    ;;
esac
