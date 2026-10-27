"""Test for script_can_fail.py (tester, 2026-10-24).

Four fake bench scripts, each with a --self-test and a MUTANTS list, run
through the runner's run():

  - a mutant that breaks what the self-test checks: caught, exit 0;
  - a mutant the self-test does not check: STILL PASSES, exit 1;
  - a mutant whose text is not in the script: NOT APPLIED, exit 1;
  - a self-test that passes only beside its own source (the shape of
    bench_mcp_reset's cwd-relative DBC path, 2026-10-24), with a harmless
    mutant: the baseline fails where the mutants run, exit 1, and no
    mutant is reported caught. Without the baseline run, that mutant read
    as caught.

The fakes are written to the machine temp folder, outside SeaDrive, and
removed.

Run:  py -3.14 -B script_can_fail_test.py
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import script_can_fail as S  # noqa: E402

TEMPLATE = '''import os
import sys


def add(a, b):
    return a + b


X = 1


def self_test():
    ok = add(2, 2) == 4
    %s
    print("fake self-test: %%s" %% ("all checks OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__" and "--self-test" in sys.argv:
    sys.exit(self_test())

MUTANTS = [%s]
'''

PLAIN = "pass"
# passes only when run from the folder that holds the marker
PLACED = ('ok = ok and os.path.exists(os.path.join(os.path.dirname('
          'os.path.abspath(__file__)), "marker.txt"))')

CASES = [
    # name, self-test extra line, mutants, want exit, want in output,
    # never in output
    ("caught", PLAIN, '("subtracts", "return a + b", "return a - b")',
     0, ["baseline: the unmutated copy passes", "1 of 1 mutants caught"],
     ["STILL PASSES"]),
    ("survivor", PLAIN, '("X unchecked", "X = 1", "X = 2")',
     1, ["STILL PASSES", "0 of 1 mutants caught"], []),
    ("not-applied", PLAIN, '("no such text", "return a * b", "return 0")',
     1, ["NOT APPLIED"], []),
    ("placed", PLACED, '("X unchecked", "X = 1", "X = 2")',
     1, ["FAIL  baseline", "no mutant was run"], ["caught"]),
]

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def main():
    root = os.path.join(tempfile.gettempdir(), "interposer-tester",
                        "script-can-fail-test")
    shutil.rmtree(root, ignore_errors=True)
    try:
        for name, extra, muts, want_rc, want, never in CASES:
            d = os.path.join(root, name)
            os.makedirs(d)
            src = os.path.join(d, "fake_%s.py" % name.replace("-", "_"))
            with open(src, "w", encoding="utf-8", newline="\n") as f:
                f.write(TEMPLATE % (extra, muts))
            open(os.path.join(d, "marker.txt"), "w").close()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = S.run(src, eval("[%s]" % muts))
            text = out.getvalue()
            missing = [w for w in want if w not in text]
            present = [w for w in never if w in text]
            check(rc == want_rc and not missing and not present,
                  "%s: exit %d (want %d)%s%s" % (
                      name, rc, want_rc,
                      "; missing %r" % missing if missing else "",
                      "; has %r" % present if present else ""))
            if name == "placed":
                import subprocess
                r = subprocess.run([sys.executable, "-B", src, "--self-test"],
                                   capture_output=True, text=True)
                check(r.returncode == 0,
                      "placed: its self-test passes beside its own source, "
                      "so only the runner's place fails it")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\nscript_can_fail_test: %s"
          % ("all checks OK" if not failures else "%d FAILED" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
