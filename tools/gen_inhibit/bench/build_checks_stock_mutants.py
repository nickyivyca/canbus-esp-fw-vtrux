#!/usr/bin/env python3
"""Mutants for the 2026-10-16 build-check changes (spec 3.2, 12.3 item 3: the stock
features off in this product). Gen-inhibit tester.

Each mutant edits one of the firmware repo's test/build_checks files in a copy of
the whole folder under the machine temp folder (outside SeaDrive), runs
test_switch_rows.py and test_build_switches.py there, and must make one of them
report a FAIL. test_refusal_pairs.py is tested against real images instead (its
negative runs, recorded in the evidence doc).

With --pairs, everything after it is passed to test_refusal_pairs.py (real images,
each with its .elf), which then runs in every mutant's copy too: a FAIL line that
the unmutated copy does not print also kills. That is how the rows that need a real
image (check_no_stock_features writing them at all) are covered.

    GI_FW=<firmware repo> py -3.14 tools/gen_inhibit/bench/build_checks_stock_mutants.py [--pairs --off <bin> --on SW=<bin> ...]
"""
import os
import shutil
import subprocess
import sys
import tempfile
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

FW = os.environ.get("GI_FW") or gi_paths.repo_root()
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
    out = []
    for t in TESTS:
        r = subprocess.run([sys.executable, "-B", t], capture_output=True, text=True, cwd=d, timeout=300)
        out += ["%s: %s" % (t, l.strip()) for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
    if pairs is not None:
        r = subprocess.run([sys.executable, "-B", "test_refusal_pairs.py"] + pairs, capture_output=True,
                           text=True, cwd=d, timeout=900)
        out += ["test_refusal_pairs.py: %s" % l.strip() for l in (r.stdout + r.stderr).splitlines()
                if l.strip().startswith(("FAIL", "Traceback"))]
    return out


def main():
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "build_checks_stock")
    killed = errors = 0
    pairs = sys.argv[sys.argv.index("--pairs") + 1:] if "--pairs" in sys.argv else None
    known = set()
    if pairs is not None:
        d = os.path.join(base, "m0")
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(SRC, d, ignore=shutil.ignore_patterns("__pycache__", "_test_*_tmp", "reference"))
        known = set(fails_in(d, pairs))
        print("unmutated: %d FAIL line(s)" % len(known))
        for k in sorted(known):
            print("    " + k[:140])
    for n, (fname, name, old, new) in enumerate(MUTANTS, 1):
        src = open(os.path.join(SRC, fname), encoding="utf-8").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(SRC, d, ignore=shutil.ignore_patterns("__pycache__", "_test_*_tmp", "reference"))
        open(os.path.join(d, fname), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        fails = [f for f in fails_in(d, pairs) if f not in known]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name, ("; " + fails[0][:100]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
