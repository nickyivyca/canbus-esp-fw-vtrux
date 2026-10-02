#!/usr/bin/env python3
"""Build the host runner, replay every scenario, diff against the goldens.

  ./run_tests.py            build, run, diff, report
  ./run_tests.py --bless    overwrite the goldens with current output
  ./run_tests.py --only X   just the scenarios whose name contains X

WHAT A GREEN RUN MEANS, AND WHAT IT DOES NOT

This compiles the same gen_inhibit_core.c the ESP32 runs, so green means
"behaviour has not changed since the goldens were blessed". It is a
REGRESSION test. It cannot tell you a rule is right -- only that it is the
same. A wrong rule blessed into a golden stays green forever.

Two consequences worth keeping in mind:

  - --bless is not a way to make a failing test pass. Read the diff first and
    decide whether the CHANGE is intended. The per-scenario header says what
    the scenario is meant to demonstrate, which is the thing to check the new
    output against.
  - the goldens were blessed against code that has never run on the truck.
    Section 13 of the spec clears on BENCH proof, not on a green run here.
"""

import argparse
import difflib
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCN = os.path.join(HERE, "scenarios")
GOLD = os.path.join(HERE, "golden")
RUNNER = os.path.join(HERE, "host_runner")

#
# SPEC 12.4 (user, 2026-10-02): "every golden is pinned to the stimulus it was
# blessed from". One sha256 per scenario, written at bless time, checked on every
# run. The scenarios themselves stay out of git -- the replays are rebuilt from
# captures through the project's parser -- so this is what makes a stale or
# regenerated stimulus fail instead of passing quietly.
#
# IT EXISTS BECAUSE THAT HAPPENED. canre's BUSMASTER parser was fixed on
# 2026-09-27 (a negative timestamp component could not match, so pre-origin lines
# were dropped silently) and six replay scenarios changed. The goldens stopped
# reproducing from a clean checkout while a stale local scenarios/ kept the suite
# green at 77/77, and nothing complained for three days.
#
STIMULUS_PIN = os.path.join(GOLD, "stimulus.sha256")


def scn_hashes(path):
    """(stimulus_sha, header_sha) for a scenario.

    TWO HASHES, BECAUSE THEY MEAN DIFFERENT THINGS AND DESERVE DIFFERENT OUTCOMES.

    stimulus  every non-comment line: the frames and the `mode` directive. This is
              the pin. A mismatch means the device under test would see something
              different, so the golden proves nothing and the suite FAILS.
    header    the `#` lines, including from_capture.py's `# COVERAGE:` block. A
              mismatch is reported as a NOTICE, not a failure: the stimulus is
              identical, only the documentation moved.

    Whole-file hashing was the first implementation and it failed a reworded note
    exactly as it failed a moved frame, which teaches re-pinning without reading --
    the habit spec 12.4 exists to stop (reviewing session, 2026-10-02). Hashing the
    stimulus alone would have been worse in the other direction: it would have
    missed a regeneration that silently DROPPED the `# COVERAGE:` warning from
    replay-shutdown-at-keyon, which is the defect that whole-file hashing caught the
    day it landed. Keeping both facts, with the right severity each, is the only
    version that catches both.
    """
    stim = hashlib.sha256()
    head = hashlib.sha256()
    with open(path, "rb") as fh:
        for line in fh:
            if line.lstrip().startswith(b"#"):
                head.update(line)
            else:
                stim.update(line)
    return stim.hexdigest(), head.hexdigest()


def load_pins():
    """name -> (stimulus_sha, header_sha). An absent file is an empty dict, which
    the caller must treat as "nothing is pinned" rather than "all matched"."""
    pins = {}
    if not os.path.exists(STIMULUS_PIN):
        return pins
    with open(STIMULUS_PIN, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split()
            if len(parts) == 3:
                pins[parts[2]] = (parts[0], parts[1])
    return pins


def save_pins(pins):
    with open(STIMULUS_PIN, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# <stimulus sha256>  <header sha256>  <scenario>   "
                 "(spec 12.4)\n")
        fh.write("# stimulus = every non-comment line, the frames and `mode`. A "
                 "mismatch FAILS the\n")
        fh.write("#            suite: the device would see something else, so the "
                 "golden proves\n")
        fh.write("#            nothing. replay_stimulus_delta.py shows what "
                 "changed.\n")
        fh.write("# header   = the `#` lines, including from_capture.py's "
                 "`# COVERAGE:` block. A\n")
        fh.write("#            mismatch is a NOTICE, not a failure -- the "
                 "stimulus is identical.\n")
        for name in sorted(pins):
            fh.write("%s  %s  %s\n" % (pins[name][0], pins[name][1], name))


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), cwd=HERE,
                          capture_output=True, text=True, **kw)


def build():
    r = sh(["make", "-s", "host_runner", "extract_probe"])
    if r.returncode != 0:
        print("BUILD FAILED")
        print(r.stdout)
        print(r.stderr)
        sys.exit(2)
    if r.stderr.strip():
        print("build warnings:")
        print(r.stderr)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bless", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--context", type=int, default=3)
    ap.add_argument("--pin-stimulus", action="store_true",
                    help="record the current scenarios' checksums against the "
                         "existing goldens WITHOUT re-blessing any trace. Use this "
                         "once when the pin file is first introduced, or after "
                         "confirming by hand that a regenerated stimulus produces "
                         "the goldens that are already there.")
    args = ap.parse_args()

    build()
    if not os.path.isdir(SCN):
        print("no scenarios; run make_scenarios.py first")
        sys.exit(2)
    os.makedirs(GOLD, exist_ok=True)

    names = sorted(n[:-4] for n in os.listdir(SCN) if n.endswith(".scn"))
    if args.only:
        names = [n for n in names if args.only in n]
    if not names:
        print("no scenarios matched")
        sys.exit(2)

    #
    # A GOLDEN WITH NO SCENARIO RUNS NOTHING, AND THE SUITE USED TO PASS ANYWAY.
    # On 2026-09-29 this directory held 77 goldens against 70 .scn files --
    # make_scenarios.py had not been run -- so seven cases including BOTH skip
    # cases were never executed, and the summary said "70 passed, 0 without
    # goldens" because that line only reports the other direction. A suite that
    # cannot tell you it did not run something is the silent filter this project's
    # notes open with, so this is an error rather than a note.
    #
    orphans = sorted(n[:-6] for n in os.listdir(GOLD) if n.endswith(".trace")
                     and not os.path.exists(os.path.join(SCN, n[:-6] + ".scn")))
    if orphans and not args.only:
        print("ERROR: %d golden(s) have no scenario, so nothing runs them:"
              % len(orphans))
        for n in orphans:
            print("    %s" % n)
        print("Run make_scenarios.py first, or delete the stale goldens.")
        sys.exit(2)

    pins = load_pins()

    if args.pin_stimulus:
        # Only pin scenarios that HAVE a golden: a scenario with no golden has not
        # blessed anything yet, so there is nothing to pin it to.
        newpins = dict(pins)
        n_pinned = 0
        for name in names:
            if os.path.exists(os.path.join(GOLD, name + ".trace")):
                newpins[name + ".scn"] = scn_hashes(
                    os.path.join(SCN, name + ".scn"))
                n_pinned += 1
        save_pins(newpins)
        print("pinned %d scenario checksum(s) to %s" % (n_pinned, STIMULUS_PIN))
        print("No trace was re-blessed. Run the suite to confirm it still passes.")
        return

    if not pins and not args.bless:
        #
        # NOT A WARNING. An empty pin file would make every comparison below pass
        # vacuously, which is the whole failure this check exists to prevent --
        # the suite was green for three days against a stimulus that no longer
        # reproduced.
        #
        print("ERROR: no stimulus pin file at %s, so the goldens are not tied to "
              "any stimulus." % STIMULUS_PIN)
        print("Run with --pin-stimulus once (records checksums, re-blesses "
              "nothing), or --bless.")
        sys.exit(2)

    npass = nfail = nnew = 0
    failed = []
    stale = []
    headnote = []
    for name in names:
        spath = os.path.join(SCN, name + ".scn")
        gpath_pre = os.path.join(GOLD, name + ".trace")
        if not args.bless and os.path.exists(gpath_pre):
            #
            # THE STIMULUS FIRST. Diffing a trace against a golden blessed from a
            # DIFFERENT stimulus tells you nothing about the firmware, and reading
            # it as a firmware change is exactly the wrong conclusion -- so the
            # comparison is skipped rather than reported alongside.
            #
            have_stim, have_head = scn_hashes(spath)
            want = pins.get(name + ".scn")
            if want is None:
                print("[UNPIN ] %-30s golden is not pinned to any stimulus"
                      % name)
                nfail += 1
                failed.append(name)
                stale.append(name)
                continue
            if have_stim != want[0]:
                print("[STIM  ] %-30s the FRAMES differ from the ones this golden "
                      "was blessed from" % name)
                print("           blessed from %s" % want[0][:16])
                print("           on disk      %s" % have_stim[:16])
                nfail += 1
                failed.append(name)
                stale.append(name)
                continue
            if have_head != want[1]:
                #
                # A NOTICE, NOT A FAILURE, and the distinction is the point: the
                # frames are identical, so the golden is still evidence. Only the
                # header text moved -- which can still matter, because
                # from_capture.py's `# COVERAGE:` block is the only place a --note
                # survives into a generated, gitignored scenario.
                #
                print("[note  ] %-30s same frames, header text differs (coverage "
                      "note reworded or lost)" % name)
                headnote.append(name)

        with open(spath) as fh:
            r = subprocess.run([RUNNER], stdin=fh, capture_output=True,
                               text=True, cwd=HERE)
        if r.returncode != 0:
            print("[ERROR ] %-30s runner exit %d" % (name, r.returncode))
            print(r.stderr[:2000])
            nfail += 1
            failed.append(name)
            continue

        out = r.stdout
        gpath = os.path.join(GOLD, name + ".trace")

        if name.startswith("replay-"):
            # A real-capture replay emits one TX line per received 0x051 --
            # tens of thousands of them, all identical in form. Diffing those
            # would put a 30,000-line golden in git to catch nothing the
            # summary does not: FINAL already carries tx_ok, tx_fail, ctr_ok,
            # ctr_bad and the histogram counts, so a change in transmit
            # behaviour still shows up. Keep the decision sequence, drop the
            # volume. TX payload CONTENT is pinned by the synthetic
            # b0-held-not-mirrored scenario instead, where it is readable.
            out = "".join(ln + "\n" for ln in out.splitlines()
                          if " TX " not in ln)

        if args.bless:
            with open(gpath, "w", newline="\n") as fh:
                fh.write(out)
            pins[name + ".scn"] = scn_hashes(os.path.join(SCN, name + ".scn"))
            print("[BLESS ] %-30s %d lines" % (name, out.count("\n")))
            continue

        if not os.path.exists(gpath):
            print("[NEW   ] %-30s no golden -- run with --bless" % name)
            nnew += 1
            continue

        with open(gpath) as fh:
            want = fh.read()
        if want == out:
            print("[ok    ] %-30s %d lines" % (name, out.count("\n")))
            npass += 1
        else:
            print("[DIFF  ] %-30s" % name)
            d = difflib.unified_diff(want.splitlines(), out.splitlines(),
                                     "golden", "current", n=args.context,
                                     lineterm="")
            shown = 0
            for ln in d:
                print("    " + ln)
                shown += 1
                if shown > 60:
                    print("    ... (truncated)")
                    break
            nfail += 1
            failed.append(name)

    if args.bless:
        save_pins(pins)
        print("stimulus checksums written to %s" % STIMULUS_PIN)
        return
    print()
    print("%d passed, %d failed, %d without goldens" % (npass, nfail, nnew))
    if failed:
        print("failed: %s" % ", ".join(failed))
    if stale:
        print()
        print("%d of those are STIMULUS failures, not firmware failures: the "
              "scenario on disk is not the one the golden was blessed from."
              % len(stale))
        print("  python3 replay_stimulus_delta.py     shows what changed and "
              "whether any decision moved")
        print("Re-bless only after reading that, and never to make the suite "
              "green.")
    if headnote:
        print()
        print("%d scenario(s) have identical frames and a changed header: %s"
              % (len(headnote), ", ".join(headnote)))
        print("  Not a failure. But if a `# COVERAGE:` note went missing, the "
              "README's --note and")
        print("  replay_stimulus_delta.py's REPLAYS table have to agree -- that is "
              "how one was")
        print("  silently dropped before 2026-10-02. Re-pin with --pin-stimulus "
              "once checked.")
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
