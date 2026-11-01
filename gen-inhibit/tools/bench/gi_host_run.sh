#!/usr/bin/env bash
#
# Push the gen_inhibit core + host suite to madhouse-debian and run it there.
#
# WHY THIS EXISTS. The host suite's runs that count for VERIFICATION.md are on
# madhouse-debian (gcc 14.2). NICKY-XPS has a host C compiler only in WSL
# (Ubuntu 20.04, gcc 9.4.0, ASan included), and a pass there is a self-check
# that does not count for VERIFICATION.md (user, 2026-10-24, through the
# reviewer). `~/gi_host/` on madhouse-debian is the working copy that gets
# built; this repo, the project folder this script is in ($FW below), is the
# source of truth. ~/gi_host has the project folder's layout (port step 0d,
# 2026-10-26): firmware/main/, test/ and tools/bench/. The copy is one-way and
# this script is the only thing that should write it.
#
# It is also the right machine for a second reason: `scenarios/` is generated,
# gitignored and EMPTY in the repo -- the replay fixtures are cut from the log
# corpus, which is on local disk there and would hydrate gigabytes over
# SeaDrive here.
#
# Usage, from the repo root:
#   bash gen-inhibit/tools/bench/gi_host_run.sh            # sync + full suite
#   bash .../gi_host_run.sh --only keyon                                      # sync + subset
#   bash .../gi_host_run.sh --asan                                            # sanitiser build
#   RROOT=<remote reverse-it root> bash .../gi_host_run.sh --signals          # cantools cross-check
#   bash .../gi_host_run.sh --checks                                          # the four checkers run_tests.py does not invoke
#   bash .../gi_host_run.sh --margin                                          # spec 5.1 item 5 gate on its own
#   RROOT=<remote reverse-it root> bash .../gi_host_run.sh --shim             # E1, the shim suite
#   bash .../gi_host_run.sh --sched                                           # the transmit-scheduler cases
#   bash .../gi_host_run.sh --sync-only
#   bash .../gi_host_run.sh --pull-goldens                                    # after a --bless
#   bash .../gi_host_run.sh --scenarios                                       # regenerate synthetic scenarios
#   bash .../gi_host_run.sh --canre                                           # install/check the pinned canre there
#
set -euo pipefail

FW="${FW:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"   # default: this project folder
REMOTE="${REMOTE:-madhouse-debian}"
DEST="${DEST:-gi_host}"
PY="${PY:-python3.13}"

[ -d "$FW/firmware/main" ] || { echo "no WiCAN tree at $FW/firmware" >&2; exit 1; }

mode="suite"
case "${1:-}" in
  --sync-only)     mode=sync;      shift ;;
  --asan)          mode=asan;      shift ;;
  --signals)       mode=signals;   shift ;;
  --scenarios)     mode=scenarios; shift ;;
  --checks)        mode=checks;    shift ;;
  --margin)        mode=margin;    shift ;;
  --shim)          mode=shim;      shift ;;
  --sched)         mode=sched;     shift ;;
  --pull-goldens)  mode=pull;      shift ;;
  --canre)         mode=canre;     shift ;;
esac

# THE PROJECT FOLDER ON THE REMOTE (2026-10-26, port step 0d finding 5). The shim's
# Makefile builds the long-arm stimulus from a capture in the reverse-it folder
# (build_longarm.py), and test_signals.py finds its logs through project_dir.py, both
# from $REVERSE_IT_DIR with no default. The shell an ssh command runs in on the remote
# does not have it, so --shim and --signals stopped there unless it was set by hand.
# RROOT is the reverse-it root on the remote, as baseline_host_run.sh takes it (the
# suite's gi_host_root), with no default, checked before the sync. It goes into the
# remote command as one quoted word, a leading ~/ kept as the remote's $HOME, and is
# exported there. test_gi_host_run_env.py runs both modes' remote commands.
RX=""
case "$mode" in
  shim|signals)
    RROOT="${RROOT:?set RROOT to the reverse-it root on the remote (--shim and --signals need it)}"
    case "$RROOT" in
      "~/"*) RQ='"$HOME"/'$(printf %q "${RROOT#"~/"}") ;;
      *)     RQ=$(printf %q "$RROOT") ;;
    esac
    RX="export REVERSE_IT_DIR=$RQ && "
    ;;
esac

# BLESSING HAPPENS ON THE REMOTE, SO THE GOLDENS HAVE TO COME BACK.
#
# The sync below is one-way and includes golden/, so the next invocation after a
# `run_tests.py --bless` would overwrite the blessed goldens with the stale ones
# from this repo -- silently, and the symptom is a suite that "fails" on a build
# you just made pass. That happened once; hence this mode and this comment.
#
#   bash .../gi_host_run.sh --pull-goldens
#
# Run it immediately after blessing, before anything else.
if [ "$mode" = pull ]; then
  echo "--- pull $REMOTE:~/$DEST goldens -> $FW"
  ssh "$REMOTE" "tar -C ~/$DEST -czf - test/gen_inhibit_host/golden" \
    | tar -C "$FW" -xzf -
  git -C "$FW" status --short test/gen_inhibit_host/golden | head -50
  exit 0
fi

# The files the host suite AND the shim suite compile.
#
# THIS COMMENT USED TO SAY the opposite -- "gen_inhibit.c and the rest of the
# shim are ESP-IDF-only and would not build here; sending them would invite
# someone to try" -- and it was wrong by the time E1 existed. The shim
# (test/gen_inhibit_shim) compiles gen_inhibit.c and gen_inhibit_core.c on the
# host against the mock platform headers in its own mock/ directory, which is the
# whole point of it: the CAN code is built AS SHIPPED rather than reimplemented.
# Since port step 1b that CAN code is the shared components can_port and
# twai_port, which replaced main/can.c, with twai_port's host fake as the driver;
# they and vtrux/wican's headers come from the pinned shared checkout (below).
#
# Because the shim was outside this sync, ~/gi_host held whatever a previous
# session had copied by hand. On 2026-09-26 all five of its inputs -- can.c,
# can.h, gen_inhibit.c, fake_twai.c, shim_test.c -- differed from the repo, so an
# E1 run there was reporting on files nobody had chosen. Same failure as the four
# checkers further down: a stale pass is indistinguishable from a pass.
#
# AND IT HAPPENED AGAIN, with gi_sched.c (2026-10-03). The shim's Makefile
# compiles $(COREDIR)/gi_sched.c -- spec 5.2's scheduler, as shipped -- and
# neither it nor gi_sched.h was in this list, so ~/gi_host had no copy at all
# and `--shim` here could not build the shim the repo has. The scheduler suite
# (test/gen_inhibit_sched) was outside the sync too and had no mode, so its
# cases were only ever run from hand-made copies on the remote -- six of them
# existed, three carrying a different gi_sched.c from the repo's. The case 3
# verdict's emulation half rests on that suite. All three paths are in the
# list below now; check a hash before believing a pass from anywhere else.
# EVERY main/ HEADER, globbed inside $FW rather than named here. Naming them
# individually was wrong three times (see above): can.h and friends in
# 2026-09, gi_sched.h and gen_inhibit.h in 2026-10. The last one carried
# GI_STATUS_PAGE_CAP, which had just moved 2048 -> 3072, so the build failed
# loudly -- but only because the value changed. A stale header whose values
# still compile gives a clean pass on the wrong source, which is the failure
# this file keeps having. 28 headers, 268K: the glob costs nothing.
#
# The .c list stays explicit. Which sources the host suites compile IS a
# deliberate choice, and a glob there would pull in ESP-IDF-only files.
#
# FOURTH INSTANCE, 2026-10-03, and it was live in a GATE: test/build_checks was
# never in this list, but a hand-copied one sat in ~/gi_host with a DIFFERENT
# config_vs_spec.py from the repo's (3baf912a against 125595a2). That script is
# the check comparing the firmware's defaults against the numbers the spec
# states, so a stale copy of it passes exactly like a current one. Added below.
#
# E3 IS SPLIT ACROSS TWO MACHINES BY NECESSITY, which is why this was easy to
# miss: build_output.py and elf_checks.py need the built image and ELF and so
# run where ESP-IDF is (NICKY-XPS), while config_vs_spec.py compiles
# config_probe.c and needs a host C compiler, which NICKY-XPS does not have.
MAIN_HDRS="$(cd "$FW" && ls firmware/main/*.h)"

# THE DIAG DBCS (2026-10-24, port plan toolkit item 10). run_tests.py compares the
# goldens' diag lines decoded by name, and the probes decode too, all through
# tools/bench/gi_diag.py, which reads the DBCs from git. ~/gi_host has
# no git, so gi_diag exports them here first -- one DBC per schema from the DBC's
# history, the pinned vtrux_bench decode.py, the interposer's DBC at its pin and
# this tree's run identity -- and they go to ~/$DEST/diag_schemas/, replacing the
# last export, with gi_diag.py and pins.json beside the rest. The export refuses
# a working-tree DBC that is not HEAD's, which stops the run here.
LOCAL_PY="${LOCAL_PY:-py -3.14}"
EXPORT_PARENT="$(git -C "$FW" rev-parse --show-toplevel)/scratch/gi-host-export"
rm -rf "$EXPORT_PARENT"
mkdir -p "$EXPORT_PARENT"
$LOCAL_PY -B "$FW/tools/bench/gi_diag.py" export "$EXPORT_PARENT/diag_schemas" >/dev/null

# THE SHARED COMPONENTS THE SHIM COMPILES (port steps 1a and 1b, 2026-10-31).
# vtrux/wican's headers (can.h, comm_server.h) since 1a, and since 1b can_port
# (can_port.c, can_stock.c, which replaced main/can.c) and twai_port with its one
# host fake (twai_port/test/twai_fake.c). They are the version gen-inhibit pins:
# the read-only checkout ../shared-<tag>/ the shim's wican_ref.py finds. ~/gi_host
# has no checkout beside it, so the three folders go to ~/$DEST/shared/,
# replacing the last export, and --shim gives the shim's make WICANDIR there.
# Before this, --shim had no wican headers on the remote at all (since 1a), and
# the sync named main/can.c, which 1b deleted, so tar stopped the sync.
WICAN_LOCAL="$(cd "$FW/test/gen_inhibit_shim" && $LOCAL_PY -B wican_ref.py)"
[ -n "$WICAN_LOCAL" ] || { echo "no pinned vtrux/wican checkout (wican_ref.py above)" >&2; exit 1; }
SHARED_ROOT="$(cd "$WICAN_LOCAL/../.." && pwd)"

echo "--- sync $FW -> $REMOTE:~/$DEST"
tar -C "$FW" -czf - \
    $MAIN_HDRS \
    tools/bench/gi_diag.py \
    tools/bench/pins.json \
    tools/bench/canre_pin.py \
    tools/bench/gi_paths.py \
    firmware/main/gen_inhibit.c \
    test/gen_inhibit_shim \
    firmware/main/gen_inhibit_core.c \
    firmware/main/gi_sched.c \
    test/gen_inhibit_sched \
    test/build_checks \
    test/gen_inhibit_host/Makefile \
    test/gen_inhibit_host/README.md \
    test/gen_inhibit_host/host_runner.c \
    test/gen_inhibit_host/extract_probe.c \
    test/gen_inhibit_host/make_scenarios.py \
    test/gen_inhibit_host/from_capture.py \
    test/gen_inhibit_host/project_dir.py \
    test/gen_inhibit_host/run_tests.py \
    test/gen_inhibit_host/test_signals.py \
    test/gen_inhibit_host/invariants.py \
    test/gen_inhibit_host/passive_diff.py \
    test/gen_inhibit_host/fuzz_sequences.py \
    test/gen_inhibit_host/load_margin.py \
    test/gen_inhibit_host/spec_expect_check.py \
    test/gen_inhibit_host/bus_on_check.py \
    test/gen_inhibit_host/drained_check.py \
    test/gen_inhibit_host/test_cfg_build_id.py \
    test/gen_inhibit_host/replay_stimulus_delta.py \
    test/gen_inhibit_host/golden \
    -C "$SHARED_ROOT" shared/wican shared/can_port shared/twai_port \
    -C "$EXPORT_PARENT" diag_schemas \
  | ssh "$REMOTE" "mkdir -p ~/$DEST && rm -rf ~/$DEST/diag_schemas ~/$DEST/shared && tar xzf - -C ~/$DEST"

# project_dir.py WAS MISSING UNTIL 2026-10-26: from_capture.py,
# replay_stimulus_delta.py and test_signals.py import it, so none of them could run
# in ~/gi_host, and its replay files went stale. test_gi_host_run_list.py now checks that every module a
# shipped host .py imports from its own folder is shipped too.
#
# THE FOUR ABOVE run_tests.py WERE MISSING UNTIL 2026-09-26, and the way that
# fails is the reason the golden warning further up exists.
#
# run_tests.py does not invoke invariants.py, passive_diff.py,
# fuzz_sequences.py or load_margin.py -- they are run by hand on the remote.
# They were not in this list, so they were whatever a previous session had left
# in ~/gi_host, while being edited here. Editing one locally and running it
# remotely then reports on the OLD file, and a pass is indistinguishable from a
# pass. Any new checker added beside run_tests.py belongs in this list on the
# same commit.
#
# AND AGAIN, 2026-10-12: spec_expect_check.py, bus_on_check.py and
# replay_stimulus_delta.py had been added beside run_tests.py without joining
# this list; drained_check.py was added the same day. All four are in it now,
# and the three spec judges run under --checks.

# The host and compiler every mode below builds with, first in the run's output
# (2026-10-24); the run stops here if either is missing.
ssh "$REMOTE" "bash -s" < "$(dirname "${BASH_SOURCE[0]}")/host_identity.sh"

case "$mode" in
  sync)
    ssh "$REMOTE" "cd ~/$DEST && git status >/dev/null 2>&1 || true; ls -l firmware/main test/gen_inhibit_host | head -20"
    ;;
  scenarios)
    ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_host && $PY make_scenarios.py"
    ;;
  # THE FOUR CHECKERS run_tests.py DOES NOT INVOKE, run in one place with their
  # exit codes propagated.
  #
  # They used to be run from memory, one ssh command at a time, and that is how
  # spec 5.1 item 5 ended up with no enforcement at all: load_margin.py always
  # exited 0 and nothing called it, so its margin was a table a person compared
  # by eye. Mutations that moved the boundary survived every suite. `set -e`
  # above means the first non-zero here stops the run.
  checks)
    ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_host \
      && echo '--- invariants' && $PY invariants.py \
      && echo '--- passive_diff' && $PY passive_diff.py \
      && echo '--- fuzz_sequences' && $PY fuzz_sequences.py \
      && echo '--- load_margin (spec 5.1 item 5 gate)' && $PY load_margin.py \
      && echo '--- spec_expect_check (trip 6)' && $PY spec_expect_check.py \
      && echo '--- bus_on_check' && $PY bus_on_check.py \
      && echo '--- drained_check (gate condition 9)' && $PY drained_check.py \
      && echo '--- test_cfg_build_id (0x7F3 build id, port step 0c)' && $PY test_cfg_build_id.py"
    ;;
  margin)
    ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_host && $PY load_margin.py $*"
    ;;
  # E1, the shim suite. Builds gen_inhibit.c, gen_inhibit_core.c and gi_sched.c on
  # the host against test/gen_inhibit_shim/mock, with can_port and twai_port (on its
  # fake) from ~/$DEST/shared/, so it needs the sync above to have carried main/ --
  # which it did not until 2026-09-26 -- and the shared folders (2026-10-31).
  #
  # CANRE AT THE PINNED COMMIT (the canre-pin step, 2026-10-26). from_capture.py takes
  # canre only from canre_pin.py's install of the commit pins.json pins, which on the
  # remote is ~/$DEST/scratch/canre/<commit> (no git there): installed once, from the
  # public repo, and checked every time. --canre does that on its own (for from_capture.py
  # or replay_stimulus_delta.py --regen by hand); --shim does it before the build, since
  # the shim's long-arm stimulus is cut with from_capture.py. The sync never touches
  # scratch/. test_gi_host_run_env.py runs both remote commands.
  canre)
    ssh "$REMOTE" "$PY -B ~/$DEST/tools/bench/canre_pin.py install"
    ;;
  shim)
    ssh "$REMOTE" "${RX}$PY -B ~/$DEST/tools/bench/canre_pin.py install && cd ~/$DEST/test/gen_inhibit_shim && make -s clean && make -s WICANDIR=\$HOME/$DEST/shared/wican && ./shim_test"
    ;;
  # The transmit-scheduler cases (spec 5.2 item 10's emulation half), against
  # fake_buf's calibrated buffer model. Needs main/gi_sched.c from the sync.
  sched)
    ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_sched && make -s clean && make -s && ./sched_test"
    ;;
  asan)
    ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_host && make -s clean && make -s asan && $PY run_tests.py $*"
    ;;
  signals)
    ssh "$REMOTE" "${RX}cd ~/$DEST/test/gen_inhibit_host && make -s clean && make -s && $PY test_signals.py"
    ;;
  *)
    ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_host && $PY run_tests.py $*"
    ;;
esac
