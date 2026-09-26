#!/usr/bin/env python3
"""Spec 12.4, row 21's other half: the invariants against RANDOMISED sequences.

    python3 fuzz_sequences.py                 # 200 sequences, seed 0
    python3 fuzz_sequences.py --runs 2000 --seed 7
    python3 fuzz_sequences.py --replay artifacts/fuzz-fail-0042.scn

WHY THIS EXISTS SEPARATELY FROM invariants.py. That file runs the same
properties, but only over scenarios a person wrote -- and a person writes the
states they have thought of. Spec 12.4 asks for both halves for a reason: the
hand-written traces say "the rules hold in the situations we considered", and
this says "the rules hold in situations nobody considered". Every defect this
component has shipped was in a state nobody had considered.

WHAT IT DOES NOT DO. It does not check that behaviour is *sensible* -- there is
no golden and no expectation, because a random sequence has no intended
outcome. It checks only the properties that must hold for ANY input at all:
PASSIVE never transmits 0x051, OBSERVE never transmits anything, B0 is always
0x08, torque is always zero, tx_ok never exceeds what completed on the wire,
and so on. A property that needs a sane input is not an invariant and does not
belong in invariants.py either.

DETERMINISM IS THE WHOLE VALUE. Every sequence comes from a seed, the seed is
printed, and a failing sequence is written out so it can be replayed byte for
byte. A fuzzer whose failures cannot be reproduced is a rumour generator.
"""

import argparse
import os
import random
import sys

import invariants

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "artifacts")

S = 1000000
MS = 1000

# The IDs the inhibitor reads, plus traffic it must ignore. Junk IDs are in
# here on purpose: "a frame we do not care about must change nothing" is itself
# a property, and the accept-all filter means the device really does see them.
READ_IDS = [0x051, 0x592, 0x440, 0x411, 0x617, 0x639, 0x054, 0x471]
JUNK_IDS = [0x100, 0x2A0, 0x3FF, 0x420, 0x430, 0x5A5, 0x700, 0x7FF]


def rand_payload(rng, ident, sane=False):
    """Eight random bytes, with the fields that gate behaviour sometimes sane.

    Pure noise would spend almost every sequence blocked at the arm gate, which
    tests the gate and nothing past it. So the gating fields are drawn from a
    mix of plausible and implausible values: often valid, so the device goes
    live and the deeper rules get exercised, sometimes not, so the gate does
    too.
    """
    d = [rng.randrange(256) for _ in range(8)]

    if sane:
        # The gate-passing values, with a small chance of each deviation so the
        # interesting transitions still occur. Without this the conjunction of
        # six independent gates is almost never satisfied at the same instant
        # and the device never goes live.
        if ident == 0x051:
            # THREE CASES, and the third is the one that makes the B0 rule
            # testable at all. 0x08 is the ordinary value and 0x10 is the VCM
            # commanding generator shutdown -- but 0x10 triggers spec 6.3
            # suppression, so the device stops transmitting and a mirrored B0
            # can never be observed through it. With only those two values a
            # core rebuilt to mirror B0 from the VCM is EQUIVALENT and the fuzz
            # run reports success; that is exactly what happened here.
            #
            # So a third slice carries values that are neither: the device must
            # still transmit a held 0x08, whatever the bus presents. The
            # accept-all filter means it really can see anything.
            r = rng.random()
            if r < 0.85:
                d[0] = 0x08
            elif r < 0.92:
                d[0] = 0x10
            else:
                d[0] = rng.choice([0x00, 0x18, 0x28, 0x48, 0x88, 0xFF])
            d[1], d[2] = 0x00, 0x80
            d[5] = rng.randrange(16)
        elif ident == 0x592:
            d[0] = 0x10 if rng.random() < 0.95 else 0x00
        elif ident == 0x440:
            d[0] = (11 << 2) if rng.random() < 0.9 else (12 << 2)
        elif ident == 0x411:
            raw = 5000 if rng.random() < 0.9 else rng.choice([2099, 2100, 2101])
            d[0] = (raw >> 6) & 0xFF
            d[1] = (raw << 2) & 0xFF
        elif ident == 0x617:
            d[7] = 0xC8 if rng.random() < 0.95 else 0xCA
        elif ident == 0x639:
            d[6] = 2 << 4
        return d

    if ident == 0x051:
        # B0: mostly the engine-off value, sometimes the 0x10 shutdown value,
        # sometimes noise -- all three are things the VCM really sends.
        d[0] = rng.choice([0x08, 0x08, 0x08, 0x10, rng.randrange(256)])
        if rng.random() < 0.7:
            d[1], d[2] = 0x00, 0x80          # zero torque
        d[5] = rng.randrange(16)             # counter nibble
    elif ident == 0x592:
        d[0] = rng.choice([0x10, 0x10, 0x00, rng.randrange(256)])
    elif ident == 0x440:
        # bcm_mainc_stat in bits 2-5: 11 and 12 are the closed states.
        d[0] = rng.choice([11 << 2, 12 << 2, 13 << 2, rng.randrange(256)])
    elif ident == 0x411:
        # 14-bit SoC, big-endian at bit 7. Straddle the 21.00 % floor.
        raw = rng.choice([0, 2099, 2100, 2101, 5000,
                          rng.randrange(0, 1 << 14)])
        d[0] = (raw >> 6) & 0xFF
        d[1] = (raw << 2) & 0xFF
    elif ident == 0x617:
        d[7] = rng.choice([0xC8, 0xC8, 0xCA, rng.randrange(256)])
    elif ident == 0x639:
        d[6] = rng.choice([2 << 4, 4 << 4, rng.randrange(256)])
    elif ident == 0x054:
        rpm = rng.choice([0, 0, 299, 300, 301, 800, rng.randrange(4000)])
        d[0] = rpm & 0xFF
        d[1] = (rpm >> 8) & 0xFF

    return d


# The signals the spec 7 arm gate requires, with the cadence each must keep to
# stay fresh. A sequence that starves any of these never goes live, and a
# sequence that never goes live tests nothing past the gate.
GATE_IDS = (0x051, 0x592, 0x440, 0x411, 0x617, 0x639)


def gen(rng, seq, live=True):
    """One random sequence, as host_runner directive/frame lines.

    `live=True` biases generation hard toward a device that actually arms and
    transmits: the gate signals keep a fresh cadence and mostly-valid payloads,
    and the mode stays in INHIBIT or PASSIVE. That is not a weakening of the
    fuzzing -- it is the difference between fuzzing the decision logic and
    fuzzing the arm gate's refusal path over and over.

    The first version left everything uniformly random. It never went live, so
    it checked the emitted-frame properties against no emitted frames, and
    reported success against a core rebuilt to transmit the VCM's 0x10
    shutdown value.
    """
    lines = []
    t_end = rng.randrange(3 * S, 12 * S)

    if live:
        mode0 = rng.choice([3, 3, 3, 3, 4])
    else:
        mode0 = rng.choice([3, 4, 2, 1, 0])
    lines.append("mode 0 %d %d" % (mode0, rng.choice([500, 1000, 4000])))

    # The load model, occasionally. It re-times everything, so most sequences
    # leave it off and a minority turn it on -- both paths need covering.
    if rng.random() < 0.25:
        lines.append("load 0 %d 500" % rng.choice([1, 3, 5, 5, 10]))
        if rng.random() < 0.5:
            a = rng.randrange(1 * S, t_end - 100 * MS)
            lines.append("preempt %d %d" % (a, a + rng.randrange(500, 6000)))

    # Mode switches mid-run: the transitions are where latches and gates get
    # re-evaluated, and several past defects lived exactly there. A live
    # sequence only ever switches BETWEEN transmitting modes, because a switch
    # to OFF or OBSERVE ends the transmitting for the rest of the run and takes
    # the coverage with it.
    choices = [3, 4] if live else [0, 1, 2, 3, 4]
    for _ in range(rng.randrange(0, 3)):
        t = rng.randrange(100 * MS, t_end)
        lines.append("mode %d %d %d"
                     % (t, rng.choice(choices), rng.choice([500, 1000, 4000])))

    # Controller misbehaviour windows.
    for kind in ("txfail", "txstall", "txdone"):
        if rng.random() < 0.2:
            a = rng.randrange(0, t_end)
            lines.append("%s %d %d" % (kind, a, a + rng.randrange(1, 500 * MS)))

    if rng.random() < 0.3:
        lines.append("bus %d 1 1 1 %d" % (rng.randrange(0, t_end),
                                          rng.randrange(0, 50)))

    # The frame stream. Cadences are drawn per-ID per-sequence so that some
    # sequences starve a signal into staleness and others keep everything fresh.
    ids = list(READ_IDS)
    if rng.random() < 0.7:
        ids += rng.sample(JUNK_IDS, rng.randrange(1, 4))

    for ident in ids:
        gate = live and ident in GATE_IDS

        if not gate and rng.random() < 0.12:
            continue                      # this signal never appears

        if gate:
            # Comfortably inside every freshness window, including 0x617's
            # 1.0 s and everything else's 0.5 s.
            period = rng.choice([10 * MS, 20 * MS, 50 * MS, 100 * MS])
            t0 = rng.randrange(0, 200 * MS)
            t1 = t_end
        else:
            period = rng.choice([5 * MS, 10 * MS, 20 * MS, 50 * MS, 100 * MS,
                                 250 * MS, 600 * MS, 1200 * MS])
            t0 = rng.randrange(0, max(1, t_end // 3))
            t1 = rng.randrange(t0 + 100 * MS, t_end + 1)

        phase = rng.randrange(0, period)
        t = t0 + phase
        while t < t1:
            dlc = 8
            if ident == 0x051:
                # DLC < 6 is spec 7 trip 8 and must stay reachable -- but a
                # short frame aborts, so in a live sequence it is rare.
                dlc = rng.choice([6, 6, 6, 6, 6, 6, 6, 6, 8] if gate
                                 else [6, 6, 6, 6, 6, 8, 5, 3])
            payload = rand_payload(rng, ident, sane=gate)
            hexs = "".join("%02X" % b for b in payload[:dlc])
            lines.append("f %d %03X %d %s" % (t, ident, dlc, hexs))
            step = period if gate else int(period * rng.uniform(0.5, 1.5))
            t += max(1 * MS, step)

    lines.append("end %d" % t_end)

    # host_runner sorts directives by time itself, but frames must be in order.
    frames = sorted((l for l in lines if l.startswith("f ")),
                    key=lambda l: int(l.split()[1]))
    others = [l for l in lines if not l.startswith("f ")]
    return others + frames


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--replay", help="check one saved .scn and stop")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    os.makedirs(ART, exist_ok=True)

    if args.replay:
        _ok, _n, c = invariants.check(os.path.basename(args.replay),
                                      verbose=True, scn_path=args.replay)
        print()
        print("%d violation(s)" % len(c.problems))
        for p in c.problems:
            print("  " + p)
        return 1 if c.problems else 0

    rng = random.Random(args.seed)
    bad = 0
    checked = 0
    rx_frames = 0
    tx_frames = 0
    live_seqs = 0

    print("seed %d, %d sequences" % (args.seed, args.runs))
    for i in range(args.runs):
        # Three sequences in four are biased to go live, so the decision
        # logic past the arm gate is what gets fuzzed. The fourth is left
        # unbiased, because the gate's own refusal paths and the OFF/OBSERVE
        # modes need covering too -- just not at the cost of everything else.
        lines = gen(rng, i, live=(i % 4 != 3))
        path = os.path.join(ART, "fuzz-current.scn")
        with open(path, "w", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")

        try:
            _ok, _n, c = invariants.check("fuzz-%04d" % i, verbose=False,
                                          scn_path=path)
        except RuntimeError as e:
            # The runner refusing to run is a finding in itself: every sequence
            # here is well-formed by construction, so this means the generator
            # emitted something it should not have, or the runner has a limit
            # it does not report gracefully. Either way, keep the input.
            keep = os.path.join(ART, "fuzz-crash-%04d.scn" % i)
            os.replace(path, keep)
            print("[CRASH ] sequence %d: %s" % (i, e))
            print("         input kept at %s" % keep)
            bad += 1
            continue

        checked += 1
        rx_frames += len(c.vcm)
        tx_frames += c.saw_tx
        if c.saw_tx:
            live_seqs += 1

        if c.problems:
            keep = os.path.join(ART, "fuzz-fail-%04d.scn" % i)
            os.replace(path, keep)
            bad += 1
            print("[VIOLATE] sequence %d -- input kept at %s" % (i, keep))
            for p in c.problems[:5]:
                print("    " + p)
            print("    replay: python3 fuzz_sequences.py --replay %s"
                  % os.path.relpath(keep, HERE))
        elif args.verbose:
            print("[ok     ] sequence %d, %d received, %d transmitted"
                  % (i, len(c.vcm), c.saw_tx))

    stale = os.path.join(ART, "fuzz-current.scn")
    if os.path.exists(stale):
        os.remove(stale)

    print()
    print("%d sequences checked: %d 0x051 received, %d inhibit frames "
          "TRANSMITTED, %d sequences went live, %d with violations"
          % (checked, rx_frames, tx_frames, live_seqs, bad))

    """
    THE COVERAGE FLOOR, and it is here because its absence hid a dead fuzzer.

    The first version of this file reported the number of 0x051 frames the
    SCENARIO contained, which is a property of the generator and not of the
    device. It looked like coverage. It was not: the generated sequences never
    satisfied the six spec 7 arm-gate conditions simultaneously, so the device
    never transmitted, so every property about a transmitted frame was checked
    against nothing -- and the run reported success against a core deliberately
    rebuilt to transmit the VCM's 0x10 shutdown value. invariants.py catches
    that same break on the same tree in one scenario.

    So the number that matters is frames TRANSMITTED, and a run with too few is
    a failure rather than a pass. This is the same rule the hand-written
    scenarios learned one at a time -- "no probe was queued, so this case
    checked the offset zero times" -- applied once, here, where it cannot be
    forgotten per-property.
    """
    floor = max(1, checked // 4)
    if live_seqs < floor:
        print()
        print("FAIL: only %d of %d sequences ever went live (floor %d). The "
              "emitted-frame properties were checked against almost nothing, "
              "so this run is not evidence. Fix the generator, not the floor."
              % (live_seqs, checked, floor))
        return 1

    if not bad:
        print("Every property in invariants.py held on every randomised "
              "sequence, across %d transmitted inhibit frames. That is not "
              "proof -- %d sequences is a sample, and the properties are only "
              "as good as the list -- but it is the half of spec 12.4 row 21 "
              "that hand-written scenarios cannot provide."
              % (tx_frames, checked))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
