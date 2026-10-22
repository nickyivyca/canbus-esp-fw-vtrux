"""Confirm autoarm_bit_offline_test.py actually bites, by breaking the harness on purpose.

WHY THIS EXISTS. A passing test is not evidence that it tests anything. The first version
of `autoarm_bit_offline_test.py` passed 7/7 while testing a COPY of the harness's logic --
deleting the population gate from `diag_build_page_check.py` would not have turned it red.
Importing the real function fixed that, but "it imports the right thing" is itself a claim
that wants checking, and the only check that settles it is to break the harness and watch
the test fail.

HOW IT WORKS. Each mutation is a literal source substitution applied to a COPY of
`diag_build_page_check.py`, written over the original, with the original restored in a
`finally`. The test is then run in a fresh interpreter and must EXIT NON-ZERO.

THREE OUTCOMES, NOT TWO, and the third was learned from the first run of this file.
Removing the population gate does not make `autoarm_verdict` return a wrong verdict --
it makes it RAISE, because with no pages `bits` is empty and `bits[0]` is an IndexError.
So that mutation is caught by a traceback and no case ever runs. The first version of
this script demanded red cases and therefore reported its own strongest result as
"3 expected cases stayed green", which on a second reading would have sent someone to
"fix" a gate that was working. Each mutation now declares whether it should be caught
`"red"` (named cases fail their assertions) or `"crash"`, and a GAP is reserved for what
it actually means: the test exited zero against a broken harness.

That the gate crashes rather than mis-answers is worth knowing in itself -- it cannot be
deleted and leave a function that quietly reports "shipping" for a dead device.

SAFETY. The original is read once at the start, restored in `finally`, and verified
byte-identical afterwards. If the restore ever fails, the backup path is printed and the
script exits non-zero rather than leaving a mutated harness on disk. Nothing here touches
hardware or the bus; the test it runs opens no adapters.

    py -3.14 autoarm_bit_mutation_check.py
"""

import hashlib
import io
import os
import subprocess
import tempfile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "diag_build_page_check.py")
TEST = os.path.join(HERE, "autoarm_bit_offline_test.py")
BACKUP = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "diag_build_page_check.py.mutation-backup")

#
# (name, old, new, expect, cases)
#
# `expect` is "red" when the mutation should make the named cases fail their
# assertions, or "crash" when it should make autoarm_verdict raise. The
# distinction matters: removing the population gate leaves `bits[0]` indexing
# an empty list, so the gate cannot be deleted and leave a function that
# quietly answers "shipping" -- it is load-bearing for the code path as well
# as for the verdict. A crash is therefore the STRONGEST outcome here, not a
# weaker one, and a mutation that stops crashing and starts answering wrong
# is a change this check should report rather than wave through.
#
MUTATIONS = [
    ("remove the population gate",
     "    if not flags:\n        return False, \"no_pages\", detail\n",
     "    if False:\n        return False, \"no_pages\", detail\n",
     "crash",
     ["SILENT device,  --expect-autoarm 0",
      "SILENT device,  --expect-autoarm 1",
      "undecodable pages only, expect 0"]),
    ("invert the mismatch comparison",
     "    if bits[0] != expect:\n",
     "    if bits[0] == expect:\n",
     "red",
     ["shipping image, --expect-autoarm 0",
      "shipping image, --expect-autoarm 1",
      "auto-arm image, --expect-autoarm 1",
      "auto-arm image, --expect-autoarm 0"]),
    ("drop the varied-across-pages check",
     "    if len(bits) > 1:\n        return False, \"varied\", detail\n",
     "    if False:\n        return False, \"varied\", detail\n",
     "red",
     ["bit3 varies across pages, expect 0"]),
    ("widen the bit mask to the whole byte",
     "    bits = sorted(set(1 if (f & AUTOARM_BIT) else 0 for f in flags))\n",
     "    bits = sorted(set(1 if (f & 0xFF) else 0 for f in flags))\n",
     "red",
     ["shipping image, --expect-autoarm 0",
      "shipping image, --expect-autoarm 1"]),
]


def run_test():
    """Run the offline test in a fresh interpreter. Returns (rc, stdout+stderr)."""
    p = subprocess.run([sys.executable, TEST], capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def red_cases(output):
    """The case labels the test marked BAD."""
    out = []
    for line in output.splitlines():
        if line.strip().startswith("[BAD]"):
            body = line.strip()[len("[BAD]"):].strip()
            out.append(body.split("->")[0].strip())
    return out


original = io.open(HARNESS, encoding="utf-8", newline="").read()
orig_md5 = hashlib.md5(original.encode("utf-8")).hexdigest()
if not os.path.isdir(os.path.dirname(BACKUP)):
    os.makedirs(os.path.dirname(BACKUP))
io.open(BACKUP, "w", encoding="utf-8", newline="").write(original)

print("harness  %s" % os.path.basename(HARNESS))
print("md5      %s" % orig_md5)
print("backup   %s" % os.path.relpath(BACKUP, HERE))
print()

failures = []
try:
    print("BASELINE: the unmutated harness must PASS")
    rc, out = run_test()
    print("  exit %d, %d case(s) red" % (rc, len(red_cases(out))))
    if rc != 0:
        print(out)
        failures.append("baseline does not pass -- fix that before reading anything "
                        "below")

    for name, old, new, expect_kind, must_redden in MUTATIONS:
        print("\nMUTATION: %s" % name)
        if original.count(old) != 1:
            print("  SKIPPED: the anchor matched %d times, not 1. The harness has "
                  "changed shape; update this mutation." % original.count(old))
            failures.append("mutation anchor stale: %s" % name)
            continue
        io.open(HARNESS, "w", encoding="utf-8", newline="").write(
            original.replace(old, new, 1))
        rc, out = run_test()
        red = red_cases(out)
        crashed = "Traceback (most recent call last)" in out
        print("  exit %d%s" % (rc, ", traceback" if crashed else ""))
        #
        # Each red case is labelled against this mutation's expectation on its own
        # line. An earlier version listed every red case and then repeated the
        # unexpected ones in a trailing "(also red)" line, so a case could appear
        # twice -- which in a diagnostic report reads as two findings.
        #
        for c in red:
            print("    red: %-36s %s"
                  % (c, "(expected)" if c in must_redden else "(also, which is fine)"))
        if rc == 0:
            print("  GAP: the test PASSED a broken harness. That is a hole in the "
                  "test, not in the mutation.")
            failures.append("test survives mutation: %s" % name)
            continue

        if expect_kind == "crash":
            #
            # The gate is load-bearing for the code path, not only for the verdict:
            # without it `bits[0]` indexes an empty list. A traceback is therefore
            # the expected and strongest outcome. What would be a real finding is
            # the OPPOSITE -- a mutation that stops crashing and starts returning a
            # wrong answer quietly.
            #
            if crashed:
                print("  BITES BY CRASH -- autoarm_verdict raised rather than "
                      "answering. Stronger than a red assertion: it does not "
                      "depend on the test asserting anything.")
                print("    (these cases could not run: %s)"
                      % ", ".join(must_redden))
            else:
                print("  CHANGED: this mutation used to crash and now fails "
                      "cleanly. Not a gap, but check which cases caught it and "
                      "move this row to \"red\" if that is now the real behaviour.")
                missing = [c for c in must_redden if c not in red]
                if missing:
                    print("  GAP: and these stayed green: %s" % ", ".join(missing))
                    failures.append("mutation %s: no crash and %d case(s) green"
                                    % (name, len(missing)))
            continue

        if crashed:
            print("  NOTE: caught, but by a traceback rather than by an assertion. "
                  "That still detects the mutation; it means the cases below never "
                  "ran, so they are not evidence.")
        #
        # A mutation reddening MORE than the cases aimed at it is not a failure --
        # the mapping from mutation to case is not exact and pretending otherwise
        # would be the error. Only a case that should have reddened and did not is
        # a gap. Both are already labelled per line above.
        #
        missing = [c for c in must_redden if c not in red]
        if missing:
            print("  GAP: expected these to go red and they did not: %s"
                  % ", ".join(missing))
            failures.append("mutation %s: %d expected case(s) stayed green"
                            % (name, len(missing)))
        if not missing:
            print("  BITES -- every expected case went red.")
finally:
    io.open(HARNESS, "w", encoding="utf-8", newline="").write(original)
    back = io.open(HARNESS, encoding="utf-8", newline="").read()
    if hashlib.md5(back.encode("utf-8")).hexdigest() != orig_md5:
        print("\nRESTORE FAILED. The harness on disk is NOT the original.")
        print("  Recover it from %s" % BACKUP)
        sys.exit(2)
    print("\nrestored: harness md5 back to %s" % orig_md5)

print()
if failures:
    print("%d problem(s):" % len(failures))
    for f in failures:
        print("  - %s" % f)
    sys.exit(1)
print("Every mutation was caught. The offline test exercises the harness, not a copy.")
os.remove(BACKUP)
