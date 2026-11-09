"""Can the switch-discard case PASS? (tester, 2026-10-16; reviewer's request)

test_l2_bridge's `bridge-ok-switch-discard` is a known FAIL on src
36edf3e55552: bridged frames discarded at the switch to listen-only are not
counted, so bridge_ok stays set (spec 2.3 / 8.2). A case that cannot pass for
some unrelated reason would look exactly like that, so this builds a PASS
WITNESS: a copy of main.cpp, in a scratch_dir run folder (removed in a
finally, 2026-10-31) and never in src,
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
  python3 -B charge-interposer/harness_checks/switch_discard_witness.py
"""

import os
import shutil
import subprocess
import sys

# charge-interposer/, found from this file (port step 0, 2026-10-26):
# paths.code_dir() refuses an INTERPOSER_CODE naming another tree.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths                                           # noqa: E402
import scratch_dir                                     # noqa: E402
CODE = paths.code_dir()
HB = os.path.join(CODE, "fw_harness", "host_bridge")
SRC = os.path.join(CODE, "firmware", "main")
WORK = None            # this run's scratch_dir folder, made by main()
CASES = ("bridge-ok-switch-discard", "bridge-ok-switch-discard-diag-only")
SPEC_CHECK = "bridged frames discarded at the switch to listen-only are counted"

# Port step 2b: the vehicle port is the shared twai_port, read through its
# own status (twai_port T7: tx_pending, the frames to transmit, the one in
# flight included), and the switch is twai_port_set_mode(); the witness's
# anchors moved with it.
EDITS = [
    ("static uint32_t veh_bridge_fail = 0;\n",
     "static uint32_t veh_bridge_fail = 0;\n"
     "static uint32_t veh_bridged_queued = 0;   // PASS WITNESS (tester)\n"
     "static uint32_t witnessVehPending() {    // PASS WITNESS (tester)\n"
     "  can_port_status_t s = {};\n"
     "  return twai_port_status(&s) == ESP_OK ? s.tx_pending : 0;\n"
     "}\n"),
    ("    const bool ok = (e.port == TO_CHARGER)\n",
     "    if (witnessVehPending() < veh_bridged_queued)\n"
     "      veh_bridged_queued = witnessVehPending();\n"
     "    const bool ok = (e.port == TO_CHARGER)\n"),
    ("    if (!ok) {\n      bridge_drops++;\n",
     "    if (ok && e.port == TO_VEHICLE &&\n"
     "        !(e.ext && (e.id & 0xFFu) == DIAG_SA))\n"
     "      veh_bridged_queued++;\n"
     "    if (!ok) {\n      bridge_drops++;\n"),
    ("  const uint32_t t0_us = micros();\n",
     "  if (witnessVehPending() < veh_bridged_queued)\n"
     "    veh_bridged_queued = witnessVehPending();\n"
     "  if (want) veh_bridge_fail += veh_bridged_queued;\n"
     "  veh_bridged_queued = 0;\n"
     "  const uint32_t t0_us = micros();\n"),
]

# test_l2_bridge as host_bridge/run.sh builds it, by its l2_build.sh (port
# step 2b: on the shared vehicle port and TWAI driver fake, which it finds
# from TWAI_FAKE_DIR or the pinned checkout).
L2_BUILD = os.path.join(HB, "l2_build.sh")


def build(src_dir, out):
    if os.path.exists(out):
        os.remove(out)
    r = subprocess.run(["bash", L2_BUILD, out, HB, src_dir],
                       capture_output=True, text=True)
    if r.returncode == 2:
        print("refused, nothing built: %s" % (r.stdout + r.stderr).strip())
        raise SystemExit(2)
    if r.returncode != 0 or not os.path.exists(out):
        raise SystemExit("build from %s failed:\n%s" % (
            src_dir, (r.stdout + r.stderr)[-2000:]))


def run(binary, case):
    r = subprocess.run([binary, case], capture_output=True, text=True)
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
    info = [ln for ln in r.stdout.splitlines() if ln.startswith("  --  ")]
    return r.returncode, fails, info


def main():
    global WORK
    WORK = scratch_dir.make("switch-discard-witness")
    try:
        return _main()
    finally:
        scratch_dir.remove(WORK)


def _main():
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
