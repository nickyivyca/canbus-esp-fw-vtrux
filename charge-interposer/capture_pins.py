"""Spec 9.1 "pinned stimulus": every capture-derived input is pinned by a hash.

The pins are HELD HERE, in the repo, and never read from the input they
cover. A hash stored inside its own file (l3_stimulus.txt's header, which
l3_replay.cpp checks) catches a hand edit but not a regeneration: a changed
parser regenerates the file AND its header hash, and the self-check passes.
A pin held apart from the data does not move when the data does.

What is capture-derived, and how each is pinned:

  - The three real-capture differential traces (make_golden.py output):
    sha256 of the .trace file. Every other .trace must belong to a
    synthetic family (SYNTHETIC_PREFIXES); a trace that is neither fails, so
    a new capture-derived trace cannot join the suite unpinned.
  - The two host-bridge L3 stimulus files: sha256 of the whole file.
  - regress.py's captures: the sha256 of each raw source file AND of the
    frame stream the core was fed (replay.replay_offline's
    stimulus_sha256), with its frame count. The raw pins say the capture is
    the same; the stream pin says the parser, the pre-filter and the bus
    choice still turn it into the same input. Both are reported, so a
    mismatch says which side moved.

Pinned 2026-10-08 (tester) from the files as tested: the trace and L3 files
are dated 2026-09-19 / 2026-09-30, before every run in the evidence doc,
so every result there was produced on exactly these bytes. Changing a pin
is a harness change: say why in the commit, and re-run what used the input.

    python capture_pins.py            # check traces + L3 in VTRUX_DATA
    python capture_pins.py --file F   # check one input (host_bridge)
    python capture_pins.py --print    # print current hashes (to re-pin)
"""

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                                                # noqa: E402

TRACE_PINS = {
    "above83.trace":
        "5edc0848c06143d327ad64645d28a05aa6ed8fc77f69e71cb49ec3427de66fd2",
    "charging45.trace":
        "ebb69e9f04a12b0aea6682070591f0f951efe92fa979d172fa0e595403c8695d",
    "evap80.trace":
        "cc72bd624d3490d48064973b1ca1cd830908303ab6209639c21c3116586396d7",
}

# make_synthetic.py (syn_) and make_unit_traces.py (unit_): built from
# code, not from captures
SYNTHETIC_PREFIXES = ("syn_", "unit_")

L3_PINS = {
    "l3_stimulus.txt":
        "2951083e56692cb3f220dca25f0d7ab1816844c14a1bb00ed0d3465126856ee0",
    "l3_stimulus_real.txt":
        "249b4c4a0ac79821eb99706b132df9f31f44a00194d6c8044b24e4034a671abe",
}

# regress.py case -> pins. sources: path under logs/ -> sha256 of the raw
# file; stimulus: sha256 of the frame stream fed to the core; frames_in.
# Measured on madhouse-debian 2026-10-08 (regress.py --all, three runs, the
# same digests and counts each time; runs/pins_20261008T225906/ in the
# fixtures folder).
REGRESS_PINS = {
    "charge_M1": dict(
        sources={
            "vtrux_M1_T20.log":
                "165a6f2a6170f162e93435a478310d762a034f63a0dc93473a3090449294b607",
            "vtrux_charge_M1_T0.log":
                "888494e176646bef5f1bfcfe1deb89f91fe178c7263fa5c49e7543c677772898",
            "vtrux_charge_M1_T1.log":
                "f91048c2c5c8742c6f1cb4087668f9ade2f37390aef9cac4390bd5ae9a841189",
            "vtrux_charge_M1_T10.log":
                "4f9f380c4ff09e5b8c03c189a5e76ec158b3801bc50463515cb1d0bcdae50ced",
            "vtrux_charge_M1_T11.log":
                "3692f8372315b41be302053e53e337ac564e58e6f0b7efb93cfafb15117cb9a1",
            "vtrux_charge_M1_T12.log":
                "3aaccc2259031290df1e2a32946fb6d11834b5abd9bf70945a7b4da5ab3ccc5a",
            "vtrux_charge_M1_T13.log":
                "ee9b6d9cf96134f8984ab434e2a96493ec6309b5dfcf7d6af1696fb267e06d25",
            "vtrux_charge_M1_T14.log":
                "fc17d7b643f374aa726707bba83f20611faa5a3e02fb0c0baf9eefa8572c1621",
            "vtrux_charge_M1_T15.log":
                "899625bfb14b437d36dea9bfa30c5badc2230ea174222bddfb2ee2f06c1c2de3",
            "vtrux_charge_M1_T16.log":
                "29aa529fad28300a981be4adb76df1a6b5cb8cb07f1c31733595eb8d76a55ab7",
            "vtrux_charge_M1_T17.log":
                "369d599118d7ad4a6b8b7e10cdbebe12a52cb6042ce11b3213946ef5c6218389",
            "vtrux_charge_M1_T18.log":
                "cd77fceae123a4f5e617014498006f38fdfcb5f024ee40b344b8616e086d9489",
            "vtrux_charge_M1_T19.log":
                "55fbb5ef336af2f18abce091103589e8c6c681a38b4134e349fdbd6d1b2cd0d3",
            "vtrux_charge_M1_T2.log":
                "7f8831c904d61d6cb4143c1094631c515fc95bd1ef156f45073d6ec167212dee",
            "vtrux_charge_M1_T21.log":
                "1a7d706d5258c70b2998c7ac18cae77bfd289da20d8451d4df159284f53bad8c",
            "vtrux_charge_M1_T22.log":
                "b7a56c1b31dbe05b6dea0f92be2aa1acd84397f253c7a07bfc34d29f2903ca8b",
            "vtrux_charge_M1_T23.log":
                "b1604a0c72b743393cde0abc892d02ba3de92873e01b51bf3777e44deef73067",
            "vtrux_charge_M1_T24.log":
                "5564fe9792de0596a4c8ecae0f10a99ec045fa9ebf6ae96f16fbd7833a749c9a",
            "vtrux_charge_M1_T25.log":
                "d4f9287d67adaef3359454dfa07ee2c91ff2d772a09ea53746a6d449a4b95338",
            "vtrux_charge_M1_T26.log":
                "1694aae2a2d960e665d8a1039d454c438908dad0014d29ecb5fddfa856593194",
            "vtrux_charge_M1_T27.log":
                "a8dc4b23b65d6c589c64c2f16e41246f64b3041ed65b2865d4caf6303fc64112",
            "vtrux_charge_M1_T28.log":
                "8bcf7247453e621dd344527c770e308fcf2a01526bd13fbba5c4c5de4f091793",
            "vtrux_charge_M1_T29.log":
                "7274fafb6019ffbf81843355b3f65ae25ad51d0d620983e4d13100de86c2fcc5",
            "vtrux_charge_M1_T3.log":
                "3a71a82c69e9374e882977743680f6e98b49e1dad23371b1517d2a519f3f76ae",
            "vtrux_charge_M1_T4.log":
                "936c87eac2f23d489b8f79087fd32209503d2954c3afda95200d0b029e41f9ac",
            "vtrux_charge_M1_T5.log":
                "50126bd36262a08b2ffebd28f4a161054ba737733e0447a3389f98695ac13c6f",
            "vtrux_charge_M1_T6.log":
                "0ab139dc076adf353122f83657e2dc616526282d25ac15e4c0130bd6ed65758d",
            "vtrux_charge_M1_T7.log":
                "43d0998d425138835ee0b47e3638094ea694781cd3a4ef9c581107b2a016a659",
            "vtrux_charge_M1_T8.log":
                "b424d44a9b5ff7b660ca1ac4dd8c9eb7365ff059a8a6e4b4e8f07a617841c817",
            "vtrux_charge_M1_T9.log":
                "8ec7bb30af74fcc93238802323a96d549de21c5e29e336b19d20d98267b02e6d",
        },
        stimulus="f5bd64817fa87c788712416f37224cec34e1fa36c48aa53327855b1643dbbe4b",
        frames_in=4066355),
    "evap_80pct": dict(
        sources={
            "vtruxchargeafterexportchargetest-80pctcv.log":
                "22190d6c0b98d37912f8d4da9f69942844bfc385bd2ec0ccbeee7ca04adc131d",
        },
        stimulus="337669d017e30887ddd5c3f9a6c05ba39c10b7598130a91aee09f89bb0139b2e",
        frames_in=4399187),
    "partstruck": dict(
        sources={
            "big-logs/vtrux_partstruck_charge.log":
                "30ac4800224278cda0c591c6d2d46ea6db4dc77dbedc65ca0cc59d17d9d97af9",
        },
        stimulus="7f88038edb3e1922cdac5f78ad490bfceb04ac3665471673f97d0dffaf8f40f2",
        frames_in=16368900),
    "above_ceiling_start": dict(
        sources={
            "unidentified-bus/vtrux_partstruck_chargeagain3.log":
                "0f9ea6e8f1265051db9a3c00275bb81351ba23deb94338ea0e0f5bbdac011199",
        },
        stimulus="b0a049e8669580141f06a7931d1b54ff4153bfc3e662e328ae4384552853357e",
        frames_in=28668),
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_files(folder, pins, what):
    """Every pinned file exists in `folder` with its pinned hash ->
    (problems, n_checked)."""
    problems = []
    n = 0
    for name, want in sorted(pins.items()):
        p = os.path.join(folder, name)
        if not os.path.isfile(p):
            problems.append("%s %s is missing from %s" % (what, name, folder))
            continue
        got = sha256_file(p)
        n += 1
        if got != want:
            problems.append(
                "%s %s: sha256 %s, pinned %s -- a capture-derived input "
                "changed (spec 9.1); if deliberate, re-pin in "
                "capture_pins.py and re-run what used it"
                % (what, name, got[:16], want[:16]))
    return problems, n


def unpinned_traces(names):
    """Trace names that are neither pinned nor synthetic."""
    return sorted(n for n in names if n.endswith(".trace")
                  and n not in TRACE_PINS
                  and not n.startswith(SYNTHETIC_PREFIXES))


def check_traces(folder):
    problems, n = check_files(folder, TRACE_PINS, "trace")
    for name in unpinned_traces(os.listdir(folder)):
        problems.append("trace %s is neither pinned in capture_pins.py nor "
                        "synthetic (%s) -- a capture-derived input without "
                        "a pin (spec 9.1)"
                        % (name, ", ".join(SYNTHETIC_PREFIXES)))
    return problems, n


def check_l3(folder):
    return check_files(folder, L3_PINS, "L3 stimulus")


def check_one(path):
    """One file, pinned by its basename in TRACE_PINS or L3_PINS ->
    problems. A basename in neither is unpinned, and fails."""
    name = os.path.basename(path)
    pins = TRACE_PINS if name in TRACE_PINS else L3_PINS
    if name not in pins:
        return ["%s has no pin in capture_pins.py -- a capture-derived "
                "input without a pin (spec 9.1)" % name]
    return check_files(os.path.dirname(os.path.abspath(path)),
                       {name: pins[name]}, "input")[0]


def check_regress(case, source_hashes, stimulus, frames_in):
    """`source_hashes`: {path under logs/: sha256}. -> problems. A case
    with no pins is itself a problem: every regress capture is
    capture-derived."""
    pin = REGRESS_PINS.get(case)
    if pin is None:
        # full hashes: this is the line someone pins from
        return ["regress case %s has no pin in capture_pins.py (spec 9.1); "
                "measured: sources %s, stimulus %s, %d frames"
                % (case, dict(sorted(source_hashes.items())),
                   stimulus or "-", frames_in)]
    problems = []
    raw_same = source_hashes == pin["sources"]
    if not raw_same:
        problems.append("regress %s: the raw capture(s) changed: %s, pinned "
                        "%s" % (case, {k: v[:16] for k, v in
                                       sorted(source_hashes.items())},
                                {k: v[:16] for k, v in
                                 sorted(pin["sources"].items())}))
    if stimulus != pin["stimulus"] or frames_in != pin["frames_in"]:
        problems.append(
            "regress %s: the frame stream fed to the core changed (sha256 %s, "
            "%d frames; pinned %s, %d)%s"
            % (case, (stimulus or "-")[:16], frames_in,
               pin["stimulus"][:16], pin["frames_in"],
               " with the raw capture unchanged -- the parser, the "
               "pre-filter or the bus choice moved" if raw_same else ""))
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default=None,
                    help="fixture folder (default: paths.fixtures())")
    ap.add_argument("--file", default=None,
                    help="check this one input against its pin")
    ap.add_argument("--print", action="store_true",
                    help="print the current hashes instead of checking")
    a = ap.parse_args()
    if a.file:
        probs = check_one(a.file)
        for p in probs:
            print("FAIL -- " + p)
        if not probs:
            print("capture pin: %s matches its pin" % os.path.basename(a.file))
        return 1 if probs else 0
    folder = a.dir or paths.fixtures()
    if a.print:
        for name in sorted(TRACE_PINS) + sorted(L3_PINS):
            p = os.path.join(folder, name)
            print("%s %s" % (sha256_file(p) if os.path.isfile(p)
                             else "MISSING", name))
        return 0
    pt, nt = check_traces(folder)
    pl, nl = check_l3(folder)
    probs = pt + pl
    if probs:
        print("FAIL -- %d problem(s):" % len(probs))
        for p in probs:
            print("  - " + p)
        return 1
    print("capture pins: %d trace(s) and %d L3 file(s) match their pins; no "
          "unpinned capture-derived trace" % (nt, nl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
