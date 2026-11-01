#!/usr/bin/env bash
#
# Baseline host + emulation run (baseline_run_list.md, rows H1-H10 and C7's
# config_vs_spec) of ONE commit on madhouse-debian, from `git archive` of that
# commit -- never from a hand-synced tree (gi_host_run.sh's comments record what
# a stale copy did, several times). Tester, 2026-10-15.
#
#   bash gen-inhibit/tools/bench/baseline_host_run.sh c65c45e   (from the repo root)
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
FW="${FW:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"   # default: this project folder; any folder of a worktree will do
TOP="$(git -C "$FW" rev-parse --show-toplevel)" || { echo "$FW is in no git worktree" >&2; exit 2; }
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

git -C "$TOP" cat-file -e "$C^{commit}" || { echo "no commit $C in $TOP" >&2; exit 2; }
mkdir -p "$OUT"
[ -e "$OUT/host_run.log" ] && { echo "$OUT/host_run.log exists; not overwriting" >&2; exit 2; }

# THE DIAG DBCS (2026-10-25). Since port plan toolkit item 10 (2026-10-24) run_tests.py and the
# host checkers decode the diag lines through the bench's gi_diag.py, which a main+test
# archive lacks, and ~/$DEST has no git. So a commit that has gi_diag.py also ships it and pins.json
# from its own archive, and gi_diag's diag_schemas/ export at the copy's root, made here by this
# tree's gi_diag (the DBCs from its git history), as gi_host_run.sh does. A commit before item 10
# ships neither, as before. The copy is staged in the worktree's scratch/ first.
#
# EACH COMMIT AT ITS OWN LAYOUT (port step 0d, 2026-10-26). A commit from the move on has the
# WiCAN tree in gen-inhibit/firmware/ and the harness in gen-inhibit/test/ and gen-inhibit/tools/;
# an earlier one has main/ and test/ at the repo root and the bench in tools/gen_inhibit/bench/.
# The copy keeps the commit's own layout, which its Makefiles and run_tests.py expect: firmware/main/,
# test/ and tools/bench/ for the first, main/, test/ and tools/gen_inhibit/bench/ for the second.
# Every git call runs from the worktree's top level, with each tree named in full as <commit>:<path>
# (no pathspec, which git would read against -C's folder: the first 0d move's defect, 2026-10-26).
# A commit with only part of the move (one topic of it) has neither layout, and is refused.
STAGE="$TOP/scratch/baseline-host-stage/$C"
rm -rf "$STAGE" && mkdir -p "$STAGE"
tree() { git -C "$TOP" cat-file -e "$C:$1" 2>/dev/null; }
put() { mkdir -p "$STAGE/$2" && git -C "$TOP" archive "$C:$1" | tar -x -C "$STAGE/$2"; }   # tree $1 of C at $2 in the copy
if tree gen-inhibit/firmware/main && tree gen-inhibit/test; then
  LAYOUT=project; put gen-inhibit/firmware/main firmware/main || exit 2; put gen-inhibit/test test || exit 2
  BENCH=gen-inhibit/tools/bench; BENCH_AT=tools/bench
elif tree main && tree test && ! tree gen-inhibit/test; then
  LAYOUT=root; put main main || exit 2; put test test || exit 2
  BENCH=tools/gen_inhibit/bench; BENCH_AT=tools/gen_inhibit/bench
else
  echo "$C has neither layout (main/ and test/ at the root, or gen-inhibit/firmware/main/ and gen-inhibit/test/): refused" >&2
  exit 2
fi
echo "commit $C: $LAYOUT layout"
if tree "$BENCH/gi_diag.py"; then
  mkdir -p "$STAGE/$BENCH_AT"
  for f in gi_diag.py pins.json; do git -C "$TOP" show "$C:$BENCH/$f" > "$STAGE/$BENCH_AT/$f" || exit 2; done
  ${LOCAL_PY:-py -3.14} -B "$TOP/gen-inhibit/tools/bench/gi_diag.py" export "$STAGE/diag_schemas" >/dev/null || exit 2
fi
# CANRE AT THE PINNED COMMIT (the canre-pin step, 2026-10-26). From that step from_capture.py
# (replays_regen, the long-arm fixture) takes canre only from canre_pin.py's install of the
# commit tools/bench/pins.json pins, in a copy with no git <copy>/scratch/canre/<commit>: the
# remote script installs it from the public repo (the canre_install step) before anything
# imports it, and every import checks it. A commit with canre_pin.py ships it beside pins.json;
# an earlier one imported canre from the project folder. Until 2026-10-29 this script shipped
# neither, so every replay regen and the long-arm fixture failed on the import (found on the
# step 1a acceptance's first emulated run).
# GI_PATHS BESIDE IT (2026-10-31). From the scratch-removal merge (488ef9f) canre_pin.py imports
# gi_paths (remove_tree), so a commit with both ships gi_paths.py beside canre_pin.py. Until this,
# the copy had canre_pin.py alone, and at 488ef9f canre_install, every replay regen, the long-arm
# fixture and the shims' longarm build failed on the import (found on the step 1b acceptance's
# emulated run).
CANRE=0
if tree "$BENCH/canre_pin.py"; then
  mkdir -p "$STAGE/$BENCH_AT"
  git -C "$TOP" show "$C:$BENCH/canre_pin.py" > "$STAGE/$BENCH_AT/canre_pin.py" || exit 2
  if tree "$BENCH/gi_paths.py"; then
    git -C "$TOP" show "$C:$BENCH/gi_paths.py" > "$STAGE/$BENCH_AT/gi_paths.py" || exit 2
  fi
  CANRE=1
fi
# THE STOCK HEADERS (port step 1a, 2026-10-29). From the stock code's move into vtrux/wican the
# shims compile can.c and gen_inhibit.c against vtrux/wican's headers, which the shim Makefiles
# find through wican_ref.py (the pinned checkout ../shared-<tag>/ beside the worktree root), or
# WICANDIR. The copy has no such checkout, so a commit with the shim's wican_ref.py ships
# shared/wican from git at the tag the commit pins (its own wican_ref.py reads the tag from its
# idf_component.yml) at shared/wican/ in the copy, and the remote script sets WICANDIR to it.
# THE CAN PORT (port step 1b, 2026-10-31). From 1b main/can.c is gone: the shims compile the
# shared can_port (can.h is its own now) and twai_port on twai_port's host fake, from beside
# vtrux/wican (the shim Makefile's $(WICANDIR)/..). So a commit with no firmware/main/can.c
# also ships shared/can_port and shared/twai_port at the same tag, beside shared/wican, and
# the source hashes name them in place of can.c. Until this, the copy had shared/wican only
# (at shared-wican/), and a 1b commit was refused here: its tag has no shared/wican/can.h.
WICAN=0
WTAG=
CANSRC='$M/can.c'
if [ -e "$STAGE/test/gen_inhibit_shim/wican_ref.py" ]; then
  WTAG=$(git -C "$TOP" show "$C:gen-inhibit/firmware/main/idf_component.yml" |
    ${LOCAL_PY:-py -3.14} -B -c "import sys; sys.path.insert(0, sys.argv[1]); import wican_ref; print(wican_ref.pinned_tag(sys.stdin.read()) or '')" \
      "$STAGE/test/gen_inhibit_shim")
  [ -n "$WTAG" ] || { echo "$C pins no vtrux/wican version" >&2; exit 2; }
  SHIP="shared/wican"
  if tree gen-inhibit/firmware/main/can.c; then
    NEED="shared/wican/can.h shared/wican/comm_server.h"
  else
    NEED="shared/wican/comm_server.h shared/can_port/include/can.h shared/can_port/can_port.c shared/can_port/can_stock.c
          shared/twai_port/twai_port.c shared/twai_port/twai_port_ll_esp32c3.c shared/twai_port/test/twai_fake.c"
    SHIP="shared/wican shared/can_port shared/twai_port"
    CANSRC="shared/can_port/can_port.c shared/can_port/can_stock.c shared/twai_port/twai_port.c shared/twai_port/twai_port_ll_esp32c3.c shared/twai_port/test/twai_fake.c"
  fi
  for f in $NEED; do
    git -C "$TOP" cat-file -e "$WTAG:$f" 2>/dev/null || { echo "no $f at $WTAG" >&2; exit 2; }
  done
  for d in $SHIP; do
    mkdir -p "$STAGE/$d" && git -C "$TOP" archive "$WTAG:$d" | tar -x -C "$STAGE/$d" || exit 2
  done
  WICAN=1
  echo "shared components at $WTAG: $SHIP"
fi
tar -C "$STAGE" -cf - . | ssh "$REMOTE" "rm -rf ~/$DEST && mkdir -p ~/$DEST && tar xf - -C ~/$DEST"

ssh "$REMOTE" "bash -s" > "$OUT/host_run.log" 2>&1 <<EOF
cd ~/$DEST
echo "# baseline host/emulation run: commit $C (git archive), \$(date -Is), \$(hostname)"
echo "# \$(gcc --version | head -1); $PY \$($PY --version 2>&1)"
M=main; [ -d firmware/main ] && M=firmware/main     # the commit's own layout (port step 0d)
echo "# layout: $LAYOUT (WiCAN sources in \$M)"
sha256sum \$M/gen_inhibit_core.c \$M/gen_inhibit.c \$M/gi_sched.c $CANSRC \
  test/gen_inhibit_host/host_runner.c test/gen_inhibit_host/golden/stimulus.sha256
step() { n="\$1"; shift; echo; echo "=== \$n"; "\$@"; echo "=== exit \$? (\$n)"; }
# The project folder (methodology, 2026-10-20): \$REVERSE_IT_DIR, and --project-dir on
# from_capture.py, from the commit whose test/gen_inhibit_host/project_dir.py exists.
# Earlier commits read \$GEN_INHIBIT_REPO and take --repo, and the flag is chosen by the
# archive itself. GEN_INHIBIT_REPO is set for those commits only, and unset for the others:
# gi_paths refuses it as retired when imported (2026-10-31, the step 1b acceptance).
RR=$RQ
export REVERSE_IT_DIR="\$RR"
if [ -e test/gen_inhibit_host/project_dir.py ]; then PDFLAG=--project-dir; unset GEN_INHIBIT_REPO; else PDFLAG=--repo; export GEN_INHIBIT_REPO="\$RR"; fi
echo "# project folder: \$RR (from_capture \$PDFLAG)"
# vtrux/wican's headers for the shims, shipped at the tag the commit pins (port step 1a), and
# from 1b can_port and twai_port beside them (the source hashes above)
if [ $WICAN = 1 ]; then export WICANDIR="\$HOME/$DEST/shared/wican"; echo "# shared components: $WTAG, comm_server.h sha256 \$(sha256sum < shared/wican/comm_server.h | cut -c1-16)"; fi
# canre at the pinned commit, before replays_regen and the long-arm fixture import it
if [ $CANRE = 1 ]; then step canre_install $PY -B $BENCH_AT/canre_pin.py install; fi
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
