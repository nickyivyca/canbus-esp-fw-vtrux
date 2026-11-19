#!/usr/bin/env python3
"""Harness test for baseline_b22.sh's reboot gate (gen-inhibit reviewer, 2026-11-19: run cb08 ran
both B22 arms after reboots it could not confirm). An arm whose reboot interlock_bench.reboot does
not confirm is not run, and its file carries the void line the suite's B22 judge reads; a confirmed
reboot runs the arm.

    python3.13 -B gen-inhibit/tools/bench/test_baseline_b22.py [--mutants]

The script runs on a copy, beside fakes of interlock_bench (its reboot from FAKE_REBOOT_OK) and
e4_device_calibration (it records its call), in this run's scratch folder; nothing touches the bench.
--mutants applies each edit in MUTANTS to the copied script and requires a FAIL line on each.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders

SCRIPT = os.environ.get("B22_SCRIPT") or os.path.join(HERE, "baseline_b22.sh")
VOID_LINE = "       - the WiCAN's reboot before the arm was not confirmed in mode 0: no page after the reboot"

FAKE_IB = '''import os
def reboot(LF=None, timeout=60.0):
    ok = os.environ.get("FAKE_REBOOT_OK") == "1"
    return {"ok": ok, "confirmed": ok, "why": "uptime_ms 900 < 5000 before" if ok else "no page after the reboot",
            "s": 1.0, "uptime_ms": 900 if ok else None, "reset_reason": "SW" if ok else None,
            "mode": 0 if ok else None, "rejoin": None, "rejoins": []}
'''
FAKE_E4 = '''import sys
out = sys.argv[sys.argv.index("--out") + 1]
with open(out[:-5] + ".called", "w") as f:
    f.write(" ".join(sys.argv[1:]))
print("  [idle] provenance (live): as specified -- test")
'''

fails, oks = [], 0


def check(name, ok, detail=""):
    global oks
    if ok:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def run(work, ok, arms=("a", "b")):
    out = os.path.join(work, "out")
    gi_paths.remove_tree(out)
    os.makedirs(out)
    env = dict(os.environ, GI_COMMIT="c0ffee12", REVERSE_IT_DIR=work, GI_OUT=out, GI_WICAN_SSID="WiCAN_testssid",
               FAKE_REBOOT_OK="1" if ok else "0", PYTHONDONTWRITEBYTECODE="1")
    env.pop("GI_FW_TOOLS", None)
    r = subprocess.run(["bash", os.path.join(work, "tools", "baseline_b22.sh")] + list(arms), env=env,
                       capture_output=True, text=True, timeout=120)
    files = {f: open(os.path.join(out, f), encoding="utf-8").read() for f in sorted(os.listdir(out))}
    return r, files


def main():
    work = gi_paths.scratch("test-baseline-b22")
    gi_paths.remove_tree(work)
    tools = os.path.join(work, "tools")
    os.makedirs(tools)
    try:
        shutil.copy2(SCRIPT, os.path.join(tools, "baseline_b22.sh"))
        shutil.copy2(os.path.join(HERE, "bench_host.sh"), os.path.join(tools, "bench_host.sh"))
        open(os.path.join(tools, "interlock_bench.py"), "w").write(FAKE_IB)
        open(os.path.join(tools, "e4_device_calibration.py"), "w").write(FAKE_E4)

        r, files = run(work, ok=False)
        txt = files.get("B22_case3_c0ffee12_man_a.txt", "")
        check("unconfirmed reboot: neither arm is run (no e4 call)",
              not [f for f in files if f.endswith(".called")], sorted(files))
        check("unconfirmed reboot: each arm's file carries the void line the B22 judge reads",
              VOID_LINE in txt.splitlines() and VOID_LINE in files.get("B22_case3_c0ffee12_man_b.txt", "").splitlines(),
              txt[-300:])
        check("unconfirmed reboot: the arm's file has the ARM VOID header",
              "---- ARM VOID: the figures above do not support a verdict ----" in txt, txt[-300:])
        check("unconfirmed reboot: the log says each arm was not run, and why the reboot was not confirmed",
              r.stdout.count("not run: its reboot was not confirmed") == 2 and "no page after the reboot" in r.stdout,
              r.stdout[-600:])

        r, files = run(work, ok=True)
        check("confirmed reboot: both arms run (e4 called with the arm's --out)",
              sorted(f for f in files if f.endswith(".called")) == ["B22_case3_c0ffee12_man_a.called",
                                                                     "B22_case3_c0ffee12_man_b.called"], sorted(files))
        check("confirmed reboot: no void line", not any(VOID_LINE in v for v in files.values()))
        check("confirmed reboot: the reboot's summary is in the log",
              r.stdout.count('reboot {"ok": true') == 2, r.stdout[-600:])
    finally:
        gi_paths.remove_tree(work)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


MUTANTS = [
    ("an unconfirmed reboot still runs the arm, as in run cb08",
     '  if [ $? -ne 0 ]; then echo "  arm $a not run: its reboot was not confirmed"; continue; fi\n', ""),
    ("the void line not written", "    sys.exit(1)\nPY", "    pass\nPY"),
    ("the reboot's result not checked", "if not r.get(\"ok\"):", "if False:"),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read()
    base = gi_paths.scratch("mutants-baseline-b22")
    killed = errors = 0
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print("ERROR    control: the unmutated script fails this test (exit %d)" % r.returncode)
        return 1
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, "baseline_b22.sh")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, B22_SCRIPT=path), timeout=300)
        fl = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        dead = bool(fl) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fl),
                                                   ("; " + fl[0][:110]) if fl else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
