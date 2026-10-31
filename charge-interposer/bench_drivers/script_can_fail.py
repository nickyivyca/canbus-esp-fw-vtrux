"""Can-fail runner for the bench scripts' self-tests (tester, 2026-10-21).

A bench script declares MUTANTS = [(label, old text, new text), ...] and
calls run(__file__, MUTANTS) from its --can-fail. Each mutant is a copy of
the script with one text edit, written to a scratch_dir run folder
(scratch/charge-interposer-harness/script-can-fail/, removed in a
finally, 2026-10-31), and run with --self-test from this folder (so its
sibling imports resolve). Every mutant must make
the self-test exit non-zero. An edit applies to the code above the
script's MUTANTS line only; one whose text is not found there exactly once
is reported NOT APPLIED and counts as a failure, never as caught.

First the unmutated script is run the same way, from the same temp path,
and must pass (2026-10-24). If it fails there, every mutant would fail too
and read as caught whatever its edit did, so no mutant is run and the
can-fail fails. Found when bench_mcp_reset's DBC path, relative to the
reverse-it root, did not resolve from this folder: its 0x7F7 decode check
failed under every mutant, and an equivalent mutant read as caught.
Test: script_can_fail_test.py.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# charge-interposer/, for scratch_dir; last, so it shadows nothing
sys.path.append(os.path.dirname(HERE))
import scratch_dir                  # noqa: E402


def run(script, mutants, timeout_s=300):
    script = os.path.abspath(script)
    name = os.path.basename(script)
    with open(script, encoding="utf-8") as f:
        src = f.read()
    # the edits apply to the code above the MUTANTS list, never to the list
    # itself, which holds every edit's text
    mark = "\nMUTANTS = ["
    if src.count(mark) != 1:
        print("FAIL  %s: no single 'MUTANTS = [' line" % name)
        return 1
    body, tail = src.split(mark)
    tail = mark + tail
    d = scratch_dir.make("script-can-fail")
    try:
        return _run_in(d, name, src, body, tail, mutants, timeout_s)
    finally:
        scratch_dir.remove(d)


def _run_in(d, name, src, body, tail, mutants, timeout_s):
    # the copy runs from the temp folder, so it finds its siblings
    # (bench_drivers/) and the code (charge-interposer/) through PYTHONPATH
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               PYTHONPATH=os.pathsep.join([HERE, os.path.dirname(HERE)] + [
                   p for p in [os.environ.get("PYTHONPATH", "")] if p]))
    path = os.path.join(d, name)

    def self_test(text):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return subprocess.run([sys.executable, "-B", path, "--self-test"],
                              capture_output=True, text=True,
                              timeout=timeout_s, cwd=HERE, env=env)

    r = self_test(src)
    if r.returncode != 0:
        last = (r.stdout.strip().splitlines() or [""])[-1][:70]
        print("FAIL  baseline: the unmutated copy fails its self-test where "
              "the mutants run (rc %d: %s), so a catch there would mean "
              "nothing; no mutant was run" % (r.returncode, last))
        return 1
    print("ok    baseline: the unmutated copy passes where the mutants run")
    bad = 0
    for label, old, new in mutants:
        n = body.count(old)
        if n != 1:
            print("FAIL  %-44s NOT APPLIED (text found %d times)" % (label, n))
            bad += 1
            continue
        r = self_test(body.replace(old, new) + tail)
        caught = r.returncode != 0
        bad += not caught
        last = (r.stdout.strip().splitlines() or [""])[-1][:70]
        print("%s  %-44s %s" % ("ok  " if caught else "FAIL", label,
                                ("caught (rc %d: %s)" % (r.returncode, last))
                                if caught else
                                "STILL PASSES -- the self-test checks nothing"))
    print("\n%d of %d mutants caught" % (len(mutants) - bad, len(mutants)))
    return 1 if bad else 0
