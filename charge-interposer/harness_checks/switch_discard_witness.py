"""Can the switch-discard case PASS? (tester, 2026-10-16; reviewer's request)

test_l2_bridge's `bridge-ok-switch-discard` is a known FAIL on src
36edf3e55552: bridged frames discarded at the switch to listen-only are not
counted, so bridge_ok stays set (spec 2.3 / 8.2). A case that cannot pass for
some unrelated reason would look exactly like that, so this builds a PASS
WITNESS: a copy of main.cpp, in the machine temp folder and never in src,
that books the bridged frames still queued at a switch to listen-only into
`veh_bridge_fail`, mirrors excluded as main.cpp's own send-failure booking
excludes them. The board's own 0x7F4-0x7F7 go out through sendDiag, not
dispatch, so they are never booked.

The witness cannot see WHICH queued frames are bridged (the port reports a
pending count only), so it keeps an upper bound: bridged sends since the
queue last held fewer, clamped to txPending() at every dispatch and at the
switch. That is a test device, not a proposed fix.

Expected:
  pristine src:  bridge-ok-switch-discard FAILS (on its spec check only),
                 bridge-ok-switch-discard-diag-only PASSES;
  witness:       both PASS.

Run on a machine with g++ (madhouse-debian), with INTERPOSER_CODE (the
clone's charge-interposer/) and L3_STIMULUS set:
  python3 -B projects/vtrux/notes/artifacts/interposer-firmware/switch_discard_witness.py
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

CODE = os.environ.get("INTERPOSER_CODE", "").strip()
if not CODE:
    raise SystemExit("INTERPOSER_CODE is not set")
HB = os.path.join(CODE, "firmware", "test", "host_bridge")
SRC = os.path.join(CODE, "firmware", "src")
WORK = os.path.join(tempfile.gettempdir(), "interposer-tester",
                    "switch_discard_witness")
CASES = ("bridge-ok-switch-discard", "bridge-ok-switch-discard-diag-only")
SPEC_CHECK = "bridged frames discarded at the switch to listen-only are counted"

EDITS = [
    ("static uint32_t veh_bridge_fail = 0;\n",
     "static uint32_t veh_bridge_fail = 0;\n"
     "static uint32_t veh_bridged_queued = 0;   // PASS WITNESS (tester)\n"),
    ("    const bool ok = (e.port == TO_CHARGER)\n",
     "    if (vehicle.txPending() < veh_bridged_queued)\n"
     "      veh_bridged_queued = vehicle.txPending();\n"
     "    const bool ok = (e.port == TO_CHARGER)\n"),
    ("    if (!ok) {\n      bridge_drops++;\n",
     "    if (ok && e.port == TO_VEHICLE &&\n"
     "        !(e.ext && (e.id & 0xFFu) == DIAG_SA))\n"
     "      veh_bridged_queued++;\n"
     "    if (!ok) {\n      bridge_drops++;\n"),
    ("  const uint32_t t0_us = micros();\n"
     "  const bool switched = vehicle.setMode(want);\n",
     "  if (vehicle.txPending() < veh_bridged_queued)\n"
     "    veh_bridged_queued = vehicle.txPending();\n"
     "  if (want) veh_bridge_fail += veh_bridged_queued;\n"
     "  veh_bridged_queued = 0;\n"
     "  const uint32_t t0_us = micros();\n"
     "  const bool switched = vehicle.setMode(want);\n"),
]


def build(src_dir, out):
    srcs = [os.path.join(HB, "test_l2_bridge.cpp")]
    srcs += [f for f in sorted(glob.glob(os.path.join(HB, "*.cpp")))
             if not os.path.basename(f).startswith("test_")]
    srcs += [os.path.join(src_dir, n) for n in
             ("main.cpp", "port_mcp2515.cpp", "port_twai.cpp", "machine.cpp")]
    if os.path.exists(out):
        os.remove(out)
    r = subprocess.run(["g++", "-std=c++11", "-O2", "-I", HB, "-I", src_dir,
                        "-DINTP_NO_SERIAL=1", "-o", out] + srcs,
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise SystemExit("build from %s failed:\n%s" % (src_dir,
                                                        r.stderr[-2000:]))


def run(binary, case):
    r = subprocess.run([binary, case], capture_output=True, text=True)
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
    info = [ln for ln in r.stdout.splitlines() if ln.startswith("  --  ")]
    return r.returncode, fails, info


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    wsrc = os.path.join(WORK, "src")
    shutil.copytree(SRC, wsrc)
    mp = os.path.join(wsrc, "main.cpp")
    with open(mp, encoding="utf-8") as f:
        s = f.read()
    for a, b in EDITS:
        if s.count(a) != 1:
            raise SystemExit("WITNESS EDIT DID NOT APPLY (pattern found %d "
                             "times): %r -- meaningless" % (s.count(a), a[:60]))
        s = s.replace(a, b)
    with open(mp, "w", encoding="utf-8", newline="") as f:
        f.write(s)

    pristine = os.path.join(WORK, "l2_pristine")
    witness = os.path.join(WORK, "l2_witness")
    build(SRC, pristine)
    build(wsrc, witness)

    bad = 0
    want = {
        ("pristine", CASES[0]): "fail-on-spec",
        ("pristine", CASES[1]): "pass",
        ("witness", CASES[0]): "pass",
        ("witness", CASES[1]): "pass",
    }
    for label, binary in (("pristine", pristine), ("witness", witness)):
        for case in CASES:
            rc, fails, info = run(binary, case)
            exp = want[(label, case)]
            if exp == "pass":
                ok = rc == 0 and not fails
            else:
                ok = (rc != 0 and len(fails) == 1
                      and SPEC_CHECK in fails[0])
            print("%s  %-8s %-36s rc=%d, %d FAIL line(s); expected %s" % (
                "ok  " if ok else "FAIL", label, case, rc, len(fails), exp))
            for ln in info:
                print("        " + ln.strip())
            for ln in fails:
                print("        " + ln[:150])
            bad += 0 if ok else 1
    if bad:
        print("\n%d FAILED" % bad)
        return 1
    print("\nswitch_discard_witness: all OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
