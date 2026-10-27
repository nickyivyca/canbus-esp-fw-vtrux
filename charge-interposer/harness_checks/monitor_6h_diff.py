"""MONITOR held past 6 h, on machine.cpp (spec 6; tester, 2026-10-16,
coverage gap 5).

Spec 6's cap is "override longer than 6 hours (OVERRIDE only ...)", so
MONITOR held past 6 h does not trip (user, 2026-10-07). test_trips.py shows
that on machine.py (monitor_cap_problems). No differential trace was long
enough to show it on machine.cpp (evidence 6, Not covered). This script
generates two 6 h 5 min unit traces with test_trips' Bench, on the default
Config:

  monitor_6h05   armed into MONITOR and held there with fresh inputs every
                 250 ms; machine.py must stay MONITOR with no trip.
  override_6h05  the control: armed, Low Power, OVERRIDE, held the same
                 span; machine.py must trip on the session cap. It shows the
                 trace length reaches the cap.

Each trace is replayed through machine.py (regen_golden.py --stdout) and
through machine.cpp (host_runner, built here from the clone's src), and
the two outputs must be byte-identical.

The traces are about 23 MB each. They are regenerable, so they go in the
machine temp folder (AGENTS.md, 2026-10-16), not the fixtures folder. Their
sha256, and their golden's, are pinned in PINS below, so a rebuild is
checked against the pin as the capture traces are. A pin mismatch FAILS:
the generator or machine.py moved, and the pin may only change with a
stated reason, like any other.

--can-fail builds a copy of machine.cpp, in the temp folder and never in
src, whose MONITOR branch applies the 6 h cap. monitor_6h05 must then read
DIFFERS, and override_6h05 must still be IDENTICAL. --corrupt-pin flips
one hex digit of each expected trace pin, and both pin checks must FAIL
(the run exits 1).

Run from the project root, on a machine with bash and g++ (madhouse-debian),
with INTERPOSER_CODE (the clone's charge-interposer/) set:
  python3 -B projects/vtrux/notes/artifacts/interposer-firmware/monitor_6h_diff.py [--can-fail] [--print-pins] [--corrupt-pin]
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

CODE = os.environ.get("INTERPOSER_CODE", "").strip()
if not CODE:
    raise SystemExit("INTERPOSER_CODE is not set: the charge-interposer code "
                     "root, a clone's charge-interposer/")
HOST_DIFF = os.path.join(CODE, "firmware", "test", "host_diff")
SRC = os.path.join(CODE, "firmware", "src")
sys.path.insert(0, CODE)
sys.path.insert(0, HOST_DIFF)
import machine as M                 # noqa: E402
import test_trips as T              # noqa: E402
import make_unit_traces as U        # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "interposer-tester", "monitor_6h")
STEP_MS = 250
SPAN_MS = T.CAP_MS + 5 * 60 * 1000          # 6 h 5 min

# name -> (trace sha256, golden sha256). Filled from the first run's
# --print-pins on 2026-10-16; see the run file for the values it printed.
PINS = {
    "unit_monitor_6h05": (
        "4fe96bedf0b429cb35d23bef09ca1421a1e3500cab253bab2a197850da5dd2a1",
        "bb864abba84b96dbb10399006510a8a51df4346493fb75ca8dd57d78749ed4e4"),
    "unit_override_6h05": (
        "19ef6ba5e3ecd01e6f2de08a669ca93c0a6e82e0d2b2241249b35883743da954",
        "8cc438ce072cda206a916a54d3abf168cbd7f1507273a8a9e3396ad3a47fb8cf"),
}

# The can-fail's mutation: the cap applied in MONITOR as well.
MUT_FROM = ("  if (state_ == S_MONITOR) {\n"
            "    if (vcu_mode_ == MODE_LOW_POWER && vcu_flow_) {\n")
MUT_TO = ("  if (state_ == S_MONITOR) {\n"
          "    if ((uint32_t)(t_ms - t_enter_) > cfg_.override_max_ms) {\n"
          "      trip(t_ms, TRIP_OVERRIDE_CAP, out);\n"
          "      return;\n"
          "    }\n"
          "    if (vcu_mode_ == MODE_LOW_POWER && vcu_flow_) {\n")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def hold(b, steps):
    for _ in range(steps):
        b.t += STEP_MS
        b.charger_status(12)
        b.bms(vmax=3.340, chg_max=300.0)
        b.tick()


def gen_monitor():
    b = T._armed()
    if b.state() != "MONITOR":
        raise SystemExit("monitor_6h05: armed into %s, not MONITOR"
                         % b.state())
    t0 = b.t
    hold(b, SPAN_MS // STEP_MS)
    if b.state() != "MONITOR" or b.core.trip_reason is not None:
        raise SystemExit("monitor_6h05: machine.py ended in %s (%r) -- not "
                         "recording it" % (b.state(), b.core.trip_reason))
    return b, (b.t - t0)


def gen_override():
    b = T._armed()
    b.low_power()
    if b.state() != "OVERRIDE":
        raise SystemExit("override_6h05: Low Power left the core in %s"
                         % b.state())
    t0 = b.t
    hold(b, SPAN_MS // STEP_MS)
    if "session cap" not in (b.core.trip_reason or ""):
        raise SystemExit("override_6h05: no cap trip on machine.py (%r, %s) "
                         "-- the control would show nothing"
                         % (b.core.trip_reason, b.state()))
    return b, (b.t - t0)


def build_runner(src_dir, out_bin):
    r = subprocess.run(["g++", "-std=c++11", "-O2", "-I", src_dir, "-o",
                        out_bin, os.path.join(HOST_DIFF, "host_runner.cpp"),
                        os.path.join(src_dir, "machine.cpp")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("host_runner did not build from %s:\n%s"
                         % (src_dir, r.stderr[-2000:]))


def run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit("%s exited %d: %s" % (" ".join(cmd), r.returncode,
                                               r.stderr[-800:]))
    return r.stdout


def first_diff(a, b):
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else b"<end>"
        y = lb[i] if i < len(lb) else b"<end>"
        if x != y:
            return "line %d: cpp %r / py %r" % (i + 1, x[:80], y[:80])
    return "none"


def main():
    can_fail = "--can-fail" in sys.argv[1:]
    print_pins = "--print-pins" in sys.argv[1:]
    corrupt = "--corrupt-pin" in sys.argv[1:]
    os.makedirs(OUT, exist_ok=True)
    bad = 0

    traces = {}
    for name, gen in (("unit_monitor_6h05", gen_monitor),
                      ("unit_override_6h05", gen_override)):
        b, span = gen()
        path, n = U.write(OUT, name, b.cfg_overrides, b.trace)
        with open(path, "rb") as f:
            tsha = sha256(f.read())
        golden = run([sys.executable, "-B",
                      os.path.join(HOST_DIFF, "regen_golden.py"), "--stdout",
                      path])
        gsha = sha256(golden)
        states = [ln for ln in golden.splitlines() if ln.startswith(b"S ")]
        print("%s: %d steps, %.0f s held, %d bytes; machine.py states %s"
              % (name, n, span / 1000.0, os.path.getsize(path),
                 [s.decode() for s in states]))
        print("  trace sha256 %s, golden sha256 %s" % (tsha, gsha))
        traces[name] = (path, golden)
        pin_t, pin_g = PINS[name]
        if corrupt:
            pin_t = ("0" if pin_t[0] != "0" else "1") + pin_t[1:]
        if print_pins:
            continue
        ok = pin_t == tsha and pin_g == gsha
        print("  %s  trace and golden %s their pins" % (
            "ok  " if ok else "FAIL", "match" if ok else "do NOT match"))
        bad += 0 if ok else 1

    runner = os.path.join(OUT, "host_runner")
    build_runner(SRC, runner)
    want = {"unit_monitor_6h05": True, "unit_override_6h05": True}
    src_label = "machine.cpp"
    if can_fail:
        mut_dir = os.path.join(OUT, "mutant_src")
        shutil.rmtree(mut_dir, ignore_errors=True)
        shutil.copytree(SRC, mut_dir)
        mp = os.path.join(mut_dir, "machine.cpp")
        with open(mp, encoding="utf-8") as f:
            s = f.read()
        if s.count(MUT_FROM) != 1:
            raise SystemExit("MUTATION DID NOT APPLY: the MONITOR branch "
                             "pattern is not in machine.cpp once -- "
                             "meaningless")
        with open(mp, "w", encoding="utf-8", newline="") as f:
            f.write(s.replace(MUT_FROM, MUT_TO))
        runner = os.path.join(OUT, "host_runner_mutant")
        build_runner(mut_dir, runner)
        want["unit_monitor_6h05"] = False
        src_label = "machine.cpp with the cap in MONITOR"

    for name, (path, golden) in traces.items():
        cpp = run([runner, path])
        same = cpp == golden
        ok = same == want[name]
        print("  %s  %s on %s: %s%s" % (
            "ok  " if ok else "FAIL", name, src_label,
            "IDENTICAL" if same else "DIFFERS",
            "" if same else " (first difference %s)" % first_diff(cpp, golden)))
        bad += 0 if ok else 1

    if print_pins:
        print("\n(--print-pins: pins not checked)")
    if bad:
        print("\n%d FAILED" % bad)
        return 1
    print("\nmonitor_6h_diff%s: all OK" % (" --can-fail" if can_fail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
