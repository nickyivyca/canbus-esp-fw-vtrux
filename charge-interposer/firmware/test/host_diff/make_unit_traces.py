"""Turn the unit tests into differential traces, so they reach `machine.cpp`.

`test_trips.py` and `test_invariants.py` drive `machine.py` and nothing else.
That leaves the same hole the differential has, from the other side: the C++
port's per-message staleness walk and its removed `cell_undervolt` trip have
never executed in any test, because no capture-derived trace reaches those
paths and the unit tests never reach the port. Raised by the review session
on 2026-10-03.

So the harnesses record every step they drive (`Bench.trace`,
`Checker.trace`), and this script replays those sequences into
`<name>.trace` files in the artifacts folder. `regen_golden.py` then writes
the goldens from `machine.py` and `diff_all.sh` picks them up with the rest
-- they are deterministic, so unlike the recorded scenario traces of spec
9.1 L1 they can live in the maintained set without churn.

Config overrides travel with the trace as `K <field> <value>` lines: the
benches run with `arm_delay_ms` 0 and, for the 6 h cap, `override_max_ms`
2000, and replaying those against `configDefaults()` would diverge for a
reason that is not a port bug.

Usage (from the repo root):
    py -3.11 projects/vtrux/tools/interposer/firmware/test/host_diff/make_unit_traces.py
    ... --outdir <dir>   default: notes/artifacts/interposer-firmware/
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_INTERPOSER = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _INTERPOSER)
sys.path.insert(0, ".")

import machine as M            # noqa: E402
import test_trips as T         # noqa: E402
import test_invariants as I    # noqa: E402

PREFIX = "unit_"

# Short enough to keep each trace a few hundred kB rather than tens of MB,
# long enough that the calm seeds still reach OVERRIDE -- which is checked
# below rather than assumed, because a random sequence that never arms
# exercises none of the rules that matter and says nothing about it.
RANDOM_STEPS = 9000
RANDOM_SEEDS = ((1, 0.00005, 0.0), (2, 0.0001, 0.0), (4, 0.02, 0.03))


def _slug(label):
    out = []
    for ch in label.lower():
        out.append(ch if ch.isalnum() else "_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def write(outdir, name, cfg_overrides, lines):
    path = os.path.join(outdir, name + ".trace")
    with open(path, "w", newline="\n") as f:
        for k in sorted(cfg_overrides):
            f.write("K %s %d\n" % (k, cfg_overrides[k]))
        f.write("\n".join(lines) + "\n")
    return path, len(lines)


def trip_traces(outdir):
    """One trace per fault source per entry state, plus the control."""
    made = []
    for label, reason, fire, hold, steps, override_only in T.CASES:
        for entry in ("monitor", "override"):
            if entry == "monitor" and override_only:
                continue
            cap = 2000 if override_only else None
            b = T._armed(cap_ms=cap)
            if entry == "override":
                b.low_power()
            sent = T._originated(fire(b))
            T._drain(b, hold, steps)
            if entry == "override":
                T._drain(b, hold, 25)      # let the repeat finish
            # The assertion the trace is worth keeping for: this case
            # really did trip, and on its own source. A trace recorded from
            # a case that silently stopped tripping would still diff clean
            # against a golden regenerated from the same silence.
            got = b.core.trip_reason or ""
            if reason not in got:
                raise SystemExit(
                    "%s/%s did not trip on its own source (reason %r) -- "
                    "recording it would bake that in" % (label, entry, got))
            _ = sent
            made.append(write(outdir, "%s%s_%s" % (PREFIX, _slug(label), entry),
                              b.cfg_overrides, b.trace))
    return made


def control_trace(outdir):
    """The healthy bus. Nothing trips; the port must agree about that too."""
    b = T._armed()
    b.low_power()
    T._drain(b, T._hold_all, 80)
    if b.core.trip_reason is not None:
        raise SystemExit("the control tripped (%r)" % b.core.trip_reason)
    return [write(outdir, PREFIX + "no_trip_control", b.cfg_overrides, b.trace)]


def definition_traces(outdir):
    """Spec 6's two definitions (user, 2026-10-07), one input per trace:
    every bcm_mainc_stat value and every 0x18FFD4C0 mux-3 flag alone, from
    MONITOR and from OVERRIDE (test_trips.definition_cases). Added by the
    tester 2026-10-08 so the port is held to them, not only machine.py."""
    made = []
    for state, kind, value, frag, trips in T.definition_cases():
        b = T.run_definition_case(state, kind, value)
        got = b.core.trip_reason or ""
        if trips != (frag in got) or (not trips and got):
            raise SystemExit("definition case %s %s %s: reason %r, spec "
                             "says trips=%s -- recording it would bake "
                             "that in" % (state, kind, value, got, trips))
        tag = ("mainc_%02d" % value) if kind == "mainc" else _slug(value)
        made.append(write(outdir, "%sdef_%s_%s" % (PREFIX, tag, state.lower()),
                          b.cfg_overrides, b.trace))
    return made


# Synthetic charges replayed densely (test_invariants.densified): ticks 2-10
# ms apart and page 03 injected, so machine.cpp is held to the spec 4 hold of
# 03.02 / 03.07 and to the 4.1 repeat spacing through the differential, not
# only machine.py. The recorded traces tick every 100 ms and carry neither
# page in an override. Reviewer request, 2026-10-08.
DENSE_SOURCES = ("syn_override_release", "syn_above_ceiling_start",
                 "syn_repeat_replaced", "syn_trip_during_override",
                 "syn_hard_ceiling_trip")


def dense_traces(outdir, src_dir):
    made = []
    for name in DENSE_SOURCES:
        src = os.path.join(src_dir, name + ".trace")
        overrides = {}
        with open(src, "r") as f:
            for line in f:
                if not line.startswith("K"):
                    break
                k, v = line[1:].split()
                overrides[k] = int(v)
        c = I.densified(src)
        problems = c.finish()
        if problems:
            raise SystemExit("dense %s breaks an invariant before it is even "
                             "recorded: %s" % (name, problems[0]))
        if c.n_held_differed == 0 or c.n_gaps_judged == 0:
            raise SystemExit("dense %s judged %d differing held frames and %d "
                             "gaps -- recording it would test nothing it was "
                             "added for" % (name, c.n_held_differed,
                                            c.n_gaps_judged))
        made.append(write(outdir, "%sdense_%s" % (PREFIX, name[len("syn_"):]),
                          overrides, c.trace)
                    + (c.n_held_differed, c.n_gaps_judged))
    return made


def silence_traces(outdir):
    """Spec 6.1 (user, 2026-10-08): the charger-silence boundary forgets the
    last pilot reading (test_trips.run_silence_case), one trace per entry
    state and per way SAFE is then cleared. Added by the tester after the
    fix landed, so the golden records the spec's behaviour."""
    made = []
    for state, clear_by in T.SILENCE_CASES:
        b, setup, after, final = T.run_silence_case(state, clear_by)
        if setup or after != "SAFE" or final != "PASSTHROUGH":
            raise SystemExit("silence case %s/%s: %s, %s after the silence, "
                             "%s after %s -- not the spec 6.1 behaviour, so "
                             "recording it would bake that in"
                             % (state, clear_by, setup, after, final,
                                clear_by))
        made.append(write(outdir, "%ssilence_forgets_pilot_%s_%s"
                          % (PREFIX, state.lower(), _slug(clear_by)),
                          b.cfg_overrides, b.trace))
    return made


def random_traces(outdir):
    made = []
    for seed, rate, junk in RANDOM_SEEDS:
        c = I.random_sequence(seed, steps=RANDOM_STEPS, fault_rate=rate,
                              garbage=junk)
        problems = c.finish()
        if problems:
            raise SystemExit("seed %d breaks an invariant before it is even "
                             "recorded: %s" % (seed, problems[0]))
        tag = "calm" if junk == 0.0 else "chaotic"
        made.append(write(outdir, "%srandom_%s_seed%d" % (PREFIX, tag, seed),
                          {}, c.trace))
        made[-1] = made[-1] + (c.n_overrode, c.n_repeat_frames)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    import paths
    ap.add_argument("--outdir", default=None,
                    help="default: notes/artifacts/interposer-firmware/ "
                         "under VTRUX_DATA")
    a = ap.parse_args()
    if a.outdir is None:
        a.outdir = paths.fixtures()
    if not os.path.isdir(a.outdir):
        os.makedirs(a.outdir)

    made = (trip_traces(a.outdir) + control_trace(a.outdir)
            + definition_traces(a.outdir) + silence_traces(a.outdir))
    for path, n in made:
        print("  %-52s %6d steps" % (os.path.basename(path), n))

    src_dir = paths.fixtures()
    dense = dense_traces(a.outdir, src_dir)
    for path, n, n_held, n_gaps in dense:
        print("  %-52s %6d steps  (%d held frames differing, %d gaps judged)"
              % (os.path.basename(path), n, n_held, n_gaps))

    rnd = random_traces(a.outdir)
    overrode = 0
    for path, n, n_over, n_rep in rnd:
        overrode += n_over
        print("  %-52s %6d steps  (%d in OVERRIDE, %d repeat frames)"
              % (os.path.basename(path), n, n_over, n_rep))
    if overrode == 0:
        raise SystemExit(
            "none of the random traces reached OVERRIDE, so none of them "
            "will exercise the port's rewrite or repeat paths. Raise "
            "RANDOM_STEPS or lower the fault rates.")

    print("\n%d unit traces written to %s"
          % (len(made) + len(dense) + len(rnd), a.outdir))
    print("Now: regen_golden.py <those traces>, then diff_all.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
