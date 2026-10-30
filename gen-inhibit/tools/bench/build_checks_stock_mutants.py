#!/usr/bin/env python3
"""Mutants for the 2026-10-16 build-check changes (spec 3.2, 12.3 item 3: the stock
features off in this product). Gen-inhibit tester.

Each mutant edits one of the firmware repo's test/build_checks files in a copy of
the whole folder in the worktree's scratch/, runs
test_switch_rows.py and test_build_switches.py there, and must make one of them
report a FAIL. test_refusal_pairs.py is tested against real images instead (its
negative runs, recorded in the evidence doc).

With --pairs, everything after it is passed to test_refusal_pairs.py (real images,
each with its .elf), which then runs in every mutant's copy too: a FAIL line from
it also kills. That is how the rows that need a real
image (check_no_stock_features writing them at all) are covered.

    GI_FW=<project folder> py -3.14 gen-inhibit/tools/bench/build_checks_stock_mutants.py --tree <WiCAN tree>
    GI_FW=<project folder> py -3.14 gen-inhibit/tools/bench/build_checks_stock_mutants.py --pairs --off <bin> --on SW=<bin> ...

--tree takes the images from a WiCAN tree's build-tester-<name>/ folders (the names
baseline_builds.ps1 gives them), forming the same arguments refusal_pairs_runs.py
passes for its positive run. One of the pairs is required: "the image rows never
written" is killed only by them, so a run without real images refuses to start
rather than report it SURVIVED (harness-defects row, 2026-10-24). The arguments
used are printed first.

The unmutated copy runs first, as the control (2026-10-30): if it prints a FAIL line or
crashes, no mutant is judged and the run exits 1. Until then its FAIL lines were only
subtracted from each mutant's, and a traceback from test_refusal_pairs.py counted as a
kill; a crash is not a kill.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

FW = os.environ.get("GI_FW") or gi_paths.project_root()   # a project folder (gen-inhibit/ since port step 0d)
SRC = os.path.join(FW, "test", "build_checks")
TESTS = ("test_switch_rows.py", "test_build_switches.py")

MUTANTS = [
    ("build_output.py", "MQTT library prefixes ignored",
     "        hits += sorted(x for x in defined if any(x.startswith(p) for p in prefixes))", "        hits += []"),
    ("build_output.py", "feature symbols ignored",
     "        hits = [x for x in syms if x in defined]", "        hits = []"),
    ("build_output.py", "the boot-enable row never fails",
     "    out.append((REALDASH_ROW, n == 0,", "    out.append((REALDASH_ROW, True,"),
    ("build_output.py", "bus-state callers not checked",
     "            if caller not in allowed:", "            if False:"),
    ("build_output.py", "can_flush_rx / can_send presence not checked",
     "    present = [x for x in BUS_STATE_ABSENT if x in defined]", "    present = []"),
    ("build_output.py", "a function's jump to itself counted as a call site",
     "        if tgt in out and fn is not None and fn != tgt:", "        if tgt in out and fn is not None:"),
    ("build_output.py", "any instruction naming the target counted as a call",
     "        if mnem not in _CALL_MNEMONICS:", "        if False:"),
    ("build_output.py", "the raw-instruction line shape misread",
     "        mnem = parts[-2].strip() if len(parts) >= 4 else parts[1].strip()", "        mnem = parts[1].strip()"),
    ("build_output.py", "stock features' refusal rows not required to have run",
     '    for cls, label in ((BS.TEST, "test-only switch"), (BS.STOCK, "stock feature off in this product")):',
     '    for cls, label in ((BS.TEST, "test-only switch"),):'),
    ("build_output.py", "the OTA handler allowed to bring the bus up",
     '    "can_enable": ("apply_mode",),', '    "can_enable": ("apply_mode", "upload_post_handler"),'),
    ("build_output.py", "image markers ignored",
     '        hits = [m.decode("latin-1") for m in markers if m in img]', "        hits = []"),
    ("build_output.py", "an image row's detail does not name the switch",
     '                    % (", ".join(hits), flag) if hits', '                    % (", ".join(hits), "a switch") if hits'),
    ("build_output.py", "the image rows never written",
     "        for n, ok, detail in stock_image_rows(img):\n            row(n, ok, detail)", "        pass"),
    ("build_switches.py", "stock feature not a class",
     "CLASSES = (SHIP, TEST, STOCK, NOT)", "CLASSES = (SHIP, TEST, NOT)"),
    ("build_switches.py", "a stock feature needs no refusal row",
     "REFUSED = (TEST, STOCK)", "REFUSED = (TEST,)"),
    ("build_switches.py", "RealDash classified as not a switch",
     '    "WICAN_FEATURE_REALDASH": (STOCK, None,', '    "WICAN_FEATURE_REALDASH": (NOT, "default value",'),
]


def fails_in(d, pairs):
    """The FAIL lines of the tests run in d, and whether any of them crashed."""
    out, crashed = [], False
    for t in TESTS:
        r = subprocess.run([sys.executable, "-B", t], capture_output=True, text=True, cwd=d, timeout=300)
        out += ["%s: %s" % (t, l.strip()) for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        crashed = crashed or "Traceback" in r.stderr
    r = subprocess.run([sys.executable, "-B", "test_refusal_pairs.py"] + pairs, capture_output=True,
                       text=True, cwd=d, timeout=900)
    out += ["test_refusal_pairs.py: %s" % l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
    crashed = crashed or "Traceback" in r.stdout + r.stderr
    return out, crashed


def main():
    base = gi_paths.scratch("mutants-build_checks_stock")
    killed = errors = 0
    if "--pairs" in sys.argv:
        pairs = sys.argv[sys.argv.index("--pairs") + 1:]
    elif "--tree" in sys.argv[:-1]:
        import refusal_pairs_runs as RP
        pairs = RP.argv(sys.argv[sys.argv.index("--tree") + 1])
    else:
        pairs = []
    if not pairs:
        print("REFUSED: give --tree <worktree with build-tester-*/> or --pairs <test_refusal_pairs args>: "
              "the real-image pairs are the only kill for 'the image rows never written'")
        return 2
    print("pairs: %s" % " ".join(pairs))
    # the unmutated copy runs first as the control (2026-10-30): where the tests cannot pass,
    # every mutant would read killed; and a crash is not a kill
    for n, (fname, name, old, new) in enumerate([(None, "the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(SRC, d, ignore=shutil.ignore_patterns("__pycache__", "_test_*_tmp", "reference"))
        if old is not None:
            src = open(os.path.join(SRC, fname), encoding="utf-8").read()
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            open(os.path.join(d, fname), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        fails, crashed = fails_in(d, pairs)
        if old is None:
            ok = not fails and not crashed
            print("%s m0 %s  (%d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, len(fails),
                                              ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s%s%s" % ("KILLED  " if dead else "SURVIVED", n, name, ", CRASHED" if crashed else "",
                                ("; " + fails[0][:100]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
