#!/usr/bin/env python3
"""Mutants of e4_device_calibration.py and wican_txabort_bench.py's device reads;
test_device_page_reads.py must fail on each BY A CHECK. A crash is not a kill, and an
edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_device_page_reads_mutants.py

Each mutant is a copy of the two tools in the worktree's scratch/, one edit applied to
one of them, run through DEVICE_READS_DIR. Gen-inhibit tester, 2026-10-25.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "test_device_page_reads.py")
FILES = ("e4_device_calibration.py", "wican_txabort_bench.py")
E4, TX = FILES
MUTANTS = [
    ("e4 names the image from /check_status", E4, '    st, txt = gi("/gen_inhibit")\n    if st != 200:\n        return None, "HTTP',
     '    st, txt = gi("/check_status")\n    if st != 200:\n        return None, "HTTP'),
    ("e4 main asks /check_status first, as before", E4, "    device_sha, why = device_identity()\n",
     '    gi("/check_status")\n    device_sha, why = device_identity()\n'),
    ("e4 main asks /check_status by a built path", E4, "    device_sha, why = device_identity()\n",
     '    gi("/check_" + "status")\n    device_sha, why = device_identity()\n'),
    ("e4 records no device", E4, '           "device": device_sha,', '           "device": None,'),
    ("e4 main goes on without the image", E4, "    if device_sha is None:\n", "    if False:\n"),
    ("e4 takes any elf_sha256", E4,
     '    if not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):',
     "    if not sha:"),
    ("e4 takes upper-case hex", E4, 'all(c in "0123456789abcdef" for c in sha)',
     'all(c in "0123456789abcdefABCDEF" for c in sha)'),
    ("e4 prints no image", E4, '    print("device elf_sha256 %s (from /gen_inhibit)" % device_sha)\n', ""),
    ("txabort reads /check_status at the start", TX, "    gi0 = gi_status()\n",
     '    http("/check_status")\n    gi0 = gi_status()\n'),
    ("txabort device_id copies the page", TX, '    return {"elf_sha256": page.get("elf_sha256")}', "    return dict(page)"),
    ("txabort prints no image", TX,
     '    print("device elf_sha256 %s, gen_inhibit mode %d" % (gi0.get("elf_sha256"), gi0["mode"]))',
     '    print("gen_inhibit mode %d" % gi0["mode"])'),
]


def main():
    base = gi_paths.scratch("mutants-device_page_reads")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in FILES}
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed
    for n, (name, target, old, new) in enumerate([("the control: the unmutated copy", None, None, None)] + MUTANTS):
        if old is not None and srcs[target].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[target].count(old), target))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for f, s in srcs.items():
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(s.replace(old, new) if f == target else s)
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, DEVICE_READS_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
