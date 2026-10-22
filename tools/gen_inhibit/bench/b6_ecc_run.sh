#!/usr/bin/env bash
# B6 investigation, one session of 20 arms on the _ecc measurement build (user: investigate
# first; reviewer's plan 2026-10-20, option 1 with longer arms). B6's threshold arms with
# the stimulus unchanged -- the PCAN replaying the VCM stream, the Kvaser witnessing --
# but 29 pulses at 1000 ms in one PULSES command (the jammer's 30 s limit), trip 6 judged
# on the first 10 as B6 does (--judge-first 10), every pulse recorded, and /gi_ecc read
# after each arm (--ecc). The WiCAN is reset before each arm (--reset-wican), which also
# clears /gi_ecc's rings. Leads as b6_rate_run.sh: 7 at 1.5 s, 7 at 5 s, 6 at 8.5 s.
# No WiCAN console capture (--no-wican-console; reviewer 2026-10-20, until the WiCAN's
# factory reset): the resets go by POST /system_reboot.
# Gen-inhibit tester.
#
#   GI_ECC_SHA=<_ecc elf_sha256> GI_COMMIT=8abf16b INTERPOSER_CODE=... bash b6_ecc_run.sh
#
# The WiCAN must already run the _ecc image: every x2_jam_cases.py call refuses unless
# /gen_inhibit's elf_sha256 is GI_ECC_SHA (never /check_status). Custody and restore are
# baseline_b_jam.sh's steps (the dump holds NVS and is SENSITIVE: only path, size and
# sha256 are recorded). Run from the reverse-it root, holding the bench lock. The stop
# rule across sessions (the 3rd over-count, and at least 10 single-count pulses in the
# 0x051 landing class; cap 80 arms) is applied by the tester between sessions, from
# b6_multicount_landing.py and each arm's ecc summary.
set -u
A=$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")   # this folder, tools/gen_inhibit/bench
cd "${REVERSE_IT_DIR:?set REVERSE_IT_DIR to the reverse-it root}" || exit 2
DATA=projects/vtrux/notes/artifacts/gen-inhibit   # the SeaDrive half, relative to REVERSE_IT_DIR: runs/ and fixtures
[ -z "${GI_FW_TOOLS:-}" ] || { echo "GI_FW_TOOLS is retired (2026-10-22): unset it"; exit 2; }
: "${GI_ECC_SHA:?set GI_ECC_SHA (the _ecc image elf_sha256)}"
: "${INTERPOSER_CODE:?set INTERPOSER_CODE}"
# baseline_b_jam.sh requires it (2026-10-20: missing here at first; its fake in the test
# did not require it). The _ecc build's commit; with GI_OUT set it names nothing else.
export GI_COMMIT=${GI_COMMIT:?set GI_COMMIT (the _ecc build commit, e.g. 8abf16b)}
case "$GI_ECC_SHA" in *[!0-9a-fA-F]*|"") echo "GI_ECC_SHA is not hex: $GI_ECC_SHA"; exit 2;; esac
[ ${#GI_ECC_SHA} = 64 ] || { echo "GI_ECC_SHA is not 64 hex digits"; exit 2; }
export GI_WICAN_SSID=${GI_WICAN_SSID:-$(grep -o 'SoftAP `WiCAN_[0-9a-fA-F]*`' firmware/bench-holder.md | head -1 | grep -o 'WiCAN_[0-9a-fA-F]*')}
[ -n "$GI_WICAN_SSID" ] || { echo "no WiCAN SSID in firmware/bench-holder.md"; exit 2; }
stamp=$(date +%Y%m%dT%H%M%S)
export GI_OUT=${GI_OUT:-$DATA/runs/b6_ecc_$stamp}
# Relative, no spaces (baseline_b_jam.sh expands GI_RUNS_DIR unquoted; b6_rate_run.sh).
export GI_RUNS_DIR=$GI_OUT
case "$GI_RUNS_DIR" in *" "*) echo "GI_RUNS_DIR has a space: $GI_RUNS_DIR"; exit 2;; esac
mkdir -p "$GI_OUT"
PW="12 V on OBD pin 16, USB unplugged (no COM4); WiCAN _ecc ${GI_ECC_SHA:0:8}; X2 jammer a89ece8c (custody)"
echo "== $(date +%H:%M:%S) custody -> $GI_OUT"
bash $A/baseline_b_jam.sh custody || { echo "custody failed: stop (X2 state: see $GI_OUT/x2)"; exit 1; }
rec=$(ls -t "$GI_RUNS_DIR"/x2_flash_custody_dump_*.json | head -1)
echo "dump record: $rec"
idrec=$(ls -t "$GI_RUNS_DIR"/x2_jammer_id_*.json 2>/dev/null | head -1)
if [ -z "$idrec" ] || ! grep -q '"banner_ok": true' "$idrec"; then
  echo "jammer banner not read (${idrec:-no record}): no arms; restoring"
  bash $A/baseline_b_jam.sh restore "$rec"
  exit 1
fi
# The liveness gate (reviewer, 2026-10-20) is on the session's first arm: the first call
# passes --ecc-gate, and its exit 3 (that arm's /gi_ecc page failed gi_ecc.liveness)
# stops the session before any other arm. The X2 is restored either way.
gate=--ecc-gate
stopped=
for spec in 1.5:7 5:7 8.5:6; do
  l=${spec%%:*}; n=${spec##*:}
  echo "$(date +%H:%M:%S) B6_ecc_n29_lead$l ($n arms)"
  py -3.14 -B $A/x2_jam_cases.py threshold --arms $n --n 29 --judge-first 10 --interval-ms 1000 \
    --lead-s $l --reset-wican --ecc $gate --want-sha "$GI_ECC_SHA" --no-wican-console \
    --power "$PW" > "$GI_OUT/B6_ecc_n29_lead$l.txt" 2>&1
  rc=$?
  echo "  exit $rc"
  grep -E "^arm |^  /gi_ecc|^REFUSED|^STOPPED|^verdict" "$GI_OUT/B6_ecc_n29_lead$l.txt" | cut -c1-220
  if [ -n "$gate" ] && [ $rc = 3 ]; then
    stopped=1
    echo "STOPPED: the first arm's /gi_ecc page failed the liveness gate (B6_ecc_n29_lead$l.txt): no more arms"
    break
  fi
  gate=
done
echo "== $(date +%H:%M:%S) restore"
bash $A/baseline_b_jam.sh restore "$rec"
sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$GI_OUT"/B6_ecc_*.txt "$GI_OUT"/x2/*.txt 2>/dev/null
echo "== $(date +%H:%M:%S) done"
[ -z "$stopped" ] || exit 3
