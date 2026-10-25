#!/usr/bin/env bash
#
# Baseline host + emulation run (baseline_run_list.md, rows H1-H10 and C7's
# config_vs_spec) of ONE commit on madhouse-debian, from `git archive` of that
# commit -- never from a hand-synced tree (gi_host_run.sh's comments record what
# a stale copy did, several times). Tester, 2026-10-15.
#
#   bash tools/gen_inhibit/bench/baseline_host_run.sh c65c45e
#
# Remote: ~/gi_baseline_<commit> (made fresh; nothing else touches it).
# Local:  runs/baseline_<commit>/host/host_run.log, and traces_scenarios_golden.tgz
#         holding every scenario, every FULL trace (TX lines kept, including the
#         scenarios with no golden) and the goldens, so a later run can be diffed
#         line by line against this one.
# Each step prints "=== <name>" and "=== exit <code>"; a failing step does not stop
# the run, so the log shows every result. Nothing is blessed and nothing is pinned.
set -uo pipefail

C="${1:?usage: baseline_host_run.sh <commit>}"
FW="${FW:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"   # default: this repo
REMOTE="${REMOTE:-madhouse-debian}"
DEST="gi_baseline_$C"
PY="${PY:-python3.13}"
RROOT="${RROOT:?set RROOT to the reverse-it root on the remote}"
# RROOT as one quoted word of the remote script (the heredoc below is unquoted, so it is
# pasted in as text): a leading ~/ stays the remote's $HOME, and the rest is quoted, so a
# root with spaces or other shell characters is one path (host-run commit, 2026-10-22).
case "$RROOT" in
  "~/"*) RQ='"$HOME"/'$(printf %q "${RROOT#"~/"}") ;;
  *)     RQ=$(printf %q "$RROOT") ;;
esac
OUT="${GI_OUT:-${REVERSE_IT_DIR:?set REVERSE_IT_DIR}/projects/vtrux/notes/artifacts/gen-inhibit/runs/baseline_$C/host}"   # GI_OUT: the chain runner's check folder (2026-10-18)

git -C "$FW" cat-file -e "$C^{commit}" || { echo "no commit $C in $FW" >&2; exit 2; }
mkdir -p "$OUT"
[ -e "$OUT/host_run.log" ] && { echo "$OUT/host_run.log exists; not overwriting" >&2; exit 2; }

# THE DIAG DBCS (2026-10-25). Since port plan toolkit item 10 (2026-10-24) run_tests.py and the
# host checkers decode the diag lines through tools/gen_inhibit/bench/gi_diag.py, which a main+test
# archive lacks, and ~/$DEST has no git. So a commit that has gi_diag.py also ships it and pins.json
# from its own archive, and gi_diag's diag_schemas/ export at the copy's root, made here by this
# tree's gi_diag (the DBCs from its git history), as gi_host_run.sh does. A commit before item 10
# ships neither, as before. The copy is staged in this tree's scratch/ first.
STAGE="$FW/scratch/baseline-host-stage/$C"
rm -rf "$STAGE" && mkdir -p "$STAGE"
git -C "$FW" archive "$C" main test | tar -x -C "$STAGE" || exit 2
if git -C "$FW" cat-file -e "$C:tools/gen_inhibit/bench/gi_diag.py" 2>/dev/null; then
  git -C "$FW" archive "$C" tools/gen_inhibit/bench/gi_diag.py tools/gen_inhibit/bench/pins.json | tar -x -C "$STAGE" || exit 2
  ${LOCAL_PY:-py -3.14} -B "$FW/tools/gen_inhibit/bench/gi_diag.py" export "$STAGE/diag_schemas" >/dev/null || exit 2
fi
tar -C "$STAGE" -cf - . | ssh "$REMOTE" "rm -rf ~/$DEST && mkdir -p ~/$DEST && tar xf - -C ~/$DEST"

ssh "$REMOTE" "bash -s" > "$OUT/host_run.log" 2>&1 <<EOF
cd ~/$DEST
echo "# baseline host/emulation run: commit $C (git archive), \$(date -Is), \$(hostname)"
echo "# \$(gcc --version | head -1); $PY \$($PY --version 2>&1)"
sha256sum main/gen_inhibit_core.c main/gen_inhibit.c main/gi_sched.c main/can.c \
  test/gen_inhibit_host/host_runner.c test/gen_inhibit_host/golden/stimulus.sha256
step() { n="\$1"; shift; echo; echo "=== \$n"; "\$@"; echo "=== exit \$? (\$n)"; }
# The project folder (methodology, 2026-10-20): \$REVERSE_IT_DIR, and --project-dir on
# from_capture.py, from the commit whose test/gen_inhibit_host/project_dir.py exists.
# Earlier commits read \$GEN_INHIBIT_REPO and take --repo, so both are set and the flag
# is chosen by the archive itself.
RR=$RQ
export REVERSE_IT_DIR="\$RR" GEN_INHIBIT_REPO="\$RR"
if [ -e test/gen_inhibit_host/project_dir.py ]; then PDFLAG=--project-dir; else PDFLAG=--repo; fi
echo "# project folder: \$RR (from_capture \$PDFLAG)"
cd test/gen_inhibit_host
step make make -s
step make_scenarios $PY make_scenarios.py
step replays_regen $PY replay_stimulus_delta.py --regen
step run_tests $PY run_tests.py
mkdir -p traces
for f in scenarios/*.scn; do ./host_runner < "\$f" > "traces/\$(basename "\$f" .scn).trace" 2>&1; done
echo; echo "=== traces: \$(ls traces | wc -l) full traces kept"
step test_signals $PY test_signals.py
step invariants $PY invariants.py
step passive_diff $PY passive_diff.py
step fuzz_sequences $PY fuzz_sequences.py --seed 0
step load_margin $PY load_margin.py
step spec_expect_selftest $PY spec_expect_check.py --selftest
step spec_expect_check $PY spec_expect_check.py
step bus_on_check $PY bus_on_check.py
step drained_selftest $PY drained_check.py --selftest
step drained_check $PY drained_check.py
# 0x7F3's build id through the runner's cfg key (port step 0c, 2026-10-25), where the archive has it.
if [ -e test_cfg_build_id.py ]; then step test_cfg_build_id $PY test_cfg_build_id.py; fi
# Shim case 33's long-arm stimulus is generated, not in git (test/gen_inhibit_shim/
# README.md). The 2026-10-15 baseline's first shim run lacked it and failed case 33
# on the missing file alone (runs/baseline_c65c45e/host/shim_rerun.log).
# scenarios/ is not in git either, so a git archive has no such folder and the fixture
# could not be written: every run before 2026-10-16 failed this step on that alone
# (found on 65e9446). Make it, and check the size, so a missing or truncated fixture
# fails here by name rather than in shim case 33/53.
mkdir -p ../gen_inhibit_shim/scenarios
step longarm_fixture $PY from_capture.py "\$RR/projects/vtrux/notes/artifacts/gen-inhibit/runs/scottsvalley_armable_300s.log" \$PDFLAG "\$RR" --channel 0 --at 0 --for 300 --all-ids --out ../gen_inhibit_shim/scenarios/replay-longarm-300s-all.scn
echo "fixture: \$(wc -c < ../gen_inhibit_shim/scenarios/replay-longarm-300s-all.scn) bytes (README: 23241533)"
step fixture_size test "\$(wc -c < ../gen_inhibit_shim/scenarios/replay-longarm-300s-all.scn)" = 23241533
# The fixture is not in git, so its hash is the only proof a later run used the same one
# (c65c45e baseline: runs/baseline_c65c45e/host/shim_fixture.sha256).
echo "fixture sha256: \$(sha256sum < ../gen_inhibit_shim/scenarios/replay-longarm-300s-all.scn | cut -d' ' -f1)"
echo "fixture source sha256: \$(sha256sum < "\$RR/projects/vtrux/notes/artifacts/gen-inhibit/runs/scottsvalley_armable_300s.log" | cut -d' ' -f1)"
cd ../gen_inhibit_shim
step shim_E1 bash -c 'make -s clean && make -s && ./shim_test'
step shim_asan bash -c 'make -s clean && make -s asan && ./shim_test'
# The auto-arm build (e3d9437 on, spec 3.2 "arming comes after it"): a second binary,
# which "make run" also runs (no backticks here: this heredoc is unquoted, so they would run locally). The asan target builds only shim_test, so the sanitiser
# goes in through CC, which compiles and links, leaving the Makefile's CFLAGS as they are.
step shim_autoarm bash -c 'make -s clean && make -s shim_test_autoarm && ./shim_test_autoarm'
step shim_autoarm_asan bash -c 'make -s clean && make -s CC="gcc -fsanitize=address,undefined -fno-omit-frame-pointer" shim_test_autoarm && ./shim_test_autoarm'
cd ../gen_inhibit_sched
step sched bash -c 'make -s clean && make -s && ./sched_test'
step sched_asan bash -c 'make -s clean && make -s asan && ./sched_test'
cd ../build_checks
step config_vs_spec bash -c 'make -s -C . config_probe && $PY config_vs_spec.py'
EOF
echo "remote run finished, log $OUT/host_run.log"
ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_host && tar czf - traces scenarios golden" \
  > "$OUT/traces_scenarios_golden.tgz"
grep -E "^=== exit|^=== traces" "$OUT/host_run.log"
