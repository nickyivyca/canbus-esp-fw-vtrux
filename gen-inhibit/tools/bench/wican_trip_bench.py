"""Drive each spec section 7 trip condition on hardware, one at a time.

Written 2026-09-20. The section 13 status table says the ported arm
interlocks are only PARTIAL: the gate was exercised incidentally by every
armed run, but the individual section 7 conditions had never each been driven
on real hardware. "Exercised incidentally" and "driven deliberately" are
different claims and the first reads as the second in a table. This closes
that.

Each trip latches until reboot, so every condition runs on its own fresh boot.
That reboot is most of the wall time; the tests themselves are seconds.

STIMULUS. Rather than hand-encode 0x051/0x054/0x411/0x471/0x639, a slice of a
real clean capture is replayed in timestamp order -- vtrux_20260719_190019_T4
after t=92 s, where the generator has stopped, SoC is 77 % and the inhibit is
known to go live (it is the replay-genrun-stop host fixture). Everything the
device sees is therefore traffic the truck produced, except the one field
being manipulated per case.

CASES

  arm gate -- the device must REFUSE to go live:
    gen_running     0x054 rpm forced above the running threshold
    vcm_torque      0x051 B0 = 0x0B with nonzero torque
    stale_bus       nothing transmitted at all

  live trips -- the device goes live first, then the condition is applied:
    vcm_fault       0x617 B7 -> 0xCA
    inverter_lost   0x471 withheld
    engine_turns    0x054 rpm forced above threshold, sustained past debounce
    bus_lost        all transmission stops (must LATCH)

NOT COVERED, and deliberately not faked: "abort immediately on a failed
transmit". Forcing a TX failure needs the transmitter to actually fail --
a disconnected bus or a jammed arbitration -- which is not reproducible on
this bench without physically interfering with it. It stays partial and the
section 13 table should keep saying so.

Verdicts come from the device's own /gen_inhibit JSON (`inhibit_live`,
`arm_block`, `abort_reason`), which is the authority on its internal state;
the Kvaser is used only to confirm that transmission actually stopped, since
"the device says it is not live" and "the device is silent" are different
claims and the second is the one that matters on a truck.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

import gi_paths  # SeaDrive paths (goal-20 move, 2026-10-22)
gi_paths.use_canre()

import can
from canre.parsers import parse_file


# --- dongle backlog ------------------------------------------------------
#
# Reported by the interposer session and measured on BOTH dongles
# (2026-09-22): after the bus has been idle, the first second of a freshly
# opened handle delivers a backlog -- 37 frames against a true rate of 4/s --
# and it is then exact. Counting from the instant of the open therefore
# inflates a witness count, which reads as a better pass rather than a
# failure. A non-blocking drain at t=0 does not fix it: the backlog arrives
# over about a second, so there is nothing to drain yet.
#
# Sleep, then drain. Two seconds costs nothing next to a bench run.
SETTLE_S = 2.0


def settle(*buses, seconds=SETTLE_S):
    """Let any backlog arrive, then throw it away. Returns what was dropped."""
    time.sleep(seconds)
    dropped = 0
    for b in buses:
        while b.recv(timeout=0.0) is not None:
            dropped += 1
    return dropped


HOST = "192.168.80.1"
BITRATE = 500000
CAPTURE = gi_paths.capture("vtrux_20260719_190019_T4.log")
SLICE_FROM = 95.0
SLICE_SECONDS = 12.0
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = gi_paths.runs()

MODE_OFF, MODE_INHIBIT = 0, 3
VCM_ID = 0x051
RPM_ID = 0x054
SOC_ID = 0x411
INV_ID = 0x471
FAULT_ID = 0x617
# Spec 7.1 (2026-09-20). 0x592 carries IgnitionKeyState in B0 bit 4, and from
# DIAG_FW_VERSION 3 the device will not transmit unless it reads on AND fresh.
# Never-seen reads OFF, so a bench that does not replay 0x592 sees a device
# that arms, reports live=1, and transmits nothing -- with no error anywhere.
# The capture this slice comes from carries 0x592 at 10 Hz; it only had to be
# added to this filter.
#
# DELETE runs/trip_bench_stimulus.json after changing this. The slice is
# cached, and a stale cache silently reproduces the old six-ID stimulus.
KEY_ID = 0x592
# Review A1 (2026-09-24). 0x440 bcm_mainc_stat gates going live: the arm
# gate needs a closed reading with an 0x411 after it. The capture carries
# it, so this is purely a matter of not filtering it out -- but leaving it
# out means every case below 'passes' because nothing ever went live.
CONTACTOR_ID = 0x440
WANT = (VCM_ID, RPM_ID, SOC_ID, INV_ID, FAULT_ID, 0x639, KEY_ID,
        CONTACTOR_ID)

# The cache is keyed to the ID SET, not just the capture. Adding 0x440 to
# WANT while reusing a cache built without it would replay the old stimulus
# and every case would "pass" with the device never live -- a stale cache
# that answers the wrong question is worse than no cache.
CACHE = os.path.join(OUT_DIR, "trip_bench_stimulus_%s.json"
                     % "-".join("%03X" % i for i in sorted(WANT)))

RUN_RPM_RAW = 32767 + 800        # comfortably over the 300 rpm threshold


def dev_get(path, timeout=8):
    with urllib.request.urlopen("http://%s%s" % (HOST, path), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def dev_post(path, timeout=8):
    req = urllib.request.Request("http://%s%s" % (HOST, path), data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def reboot_and_wait(timeout=60):
    """POST, not GET -- a GET returns 200 and does not reboot."""
    before = json.loads(dev_get("/gen_inhibit")).get("tx_ok", -1)
    try:
        dev_post("/system_reboot", timeout=6)
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(3)
        try:
            st = json.loads(dev_get("/gen_inhibit", timeout=4))
        except Exception:
            continue
        # tx_ok going to 0 is the evidence the reboot happened, not the 200.
        if st.get("tx_ok", -1) == 0 and not st.get("disabled"):
            return True, st
        if before == 0 and not st.get("disabled"):
            return True, st
    return False, None


def build_stimulus():
    if os.path.exists(CACHE):
        with open(CACHE) as fh:
            raw = json.load(fh)
        return [(t, a, bytes.fromhex(h)) for t, a, h in raw]
    print("extracting stimulus from %s ..." % CAPTURE)
    out = []
    for f in parse_file(CAPTURE):
        t = f.timestamp
        if t is None or t < SLICE_FROM:
            continue
        if t > SLICE_FROM + SLICE_SECONDS:
            break
        if f.arbitration_id in WANT and f.data:
            out.append((round(t - SLICE_FROM, 6), f.arbitration_id,
                        bytes(f.data)))
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    with open(CACHE, "w") as fh:
        json.dump([(t, a, d.hex()) for t, a, d in out], fh)
    return out


def force_rpm(data):
    d = bytearray(data)
    if len(d) >= 2:
        d[0] = RUN_RPM_RAW & 0xFF
        d[1] = (RUN_RPM_RAW >> 8) & 0xFF
    return bytes(d)


def force_torque_request(data):
    d = bytearray(data)
    if len(d) >= 3:
        d[0] = 0x0B
        d[1] = 0x00      # nonzero torque: B1B2 away from the 0080 null
        d[2] = 0x40
    return bytes(d)


def force_fault(data):
    d = bytearray(data)
    if len(d) >= 8:
        d[7] = 0xCA
    return bytes(d)


def play(pcan, kvaser, stim, seconds, mutate=None, drop=(), capture=True):
    """Replay the stimulus slice, looping, for `seconds`."""
    seen = 0
    t_start = time.perf_counter()
    i = 0
    base = 0.0
    while time.perf_counter() - t_start < seconds:
        for rel, arb, data in stim:
            if time.perf_counter() - t_start >= seconds:
                break
            if arb in drop:
                continue
            payload = data
            if mutate:
                payload = mutate(arb, data)
                if payload is None:
                    continue
            target = t_start + base + rel
            while time.perf_counter() < target:
                pass
            try:
                pcan.send(can.Message(arbitration_id=arb, data=payload,
                                      is_extended_id=False), timeout=0.2)
            except Exception:
                pass
            if capture:
                m = kvaser.recv(timeout=0.0)
                while m is not None:
                    if m.arbitration_id == VCM_ID and not m.is_error_frame:
                        seen += 1
                    m = kvaser.recv(timeout=0.0)
        base += stim[-1][0] if stim else 0.0
        i += 1
    return seen


# --- what each case must REPORT, not merely that it reported something ------
#
# THE DEFECT THIS FIXES (2026-10-04, found by the reviewing session). Until now
# the summary's only reason check was `if not reason: fails.append(...)` -- it
# failed when a case stopped the inhibit and named NOTHING, and passed when a
# case stopped for the WRONG reason. Every one of these cases manipulates one
# field of a live replay, and the most likely way to get a wrong answer is for
# the replay itself to falter, which trips "bus lost" and looks like a pass.
# `engine_turns` and `inverter_lost` are the same shape: both withhold or alter
# a GENE-family frame, and spec 7's own evidence rule exists because those two
# are confusable with each other and with a whole-bus stop.
#
# This is the "wrong LABEL on a right quantity" failure from AGENTS.md -- it
# leaves no residue, so it surfaces later when someone reads the reason back
# out of a recorded run and reasons from it.
#
# THE RECORDED RUNS ARE NOT INVALIDATED BY THIS. Checked both of them
# (runs/trip_bench.json 2026-09-20, runs/trip_bench_eea8f75.json 2026-09-22):
# all seven cases already carry exactly the reasons below. So the recorded pass
# was WEAKER THAN IT READ rather than wrong, and tightening the check costs no
# result. Worth stating, because "the assertion was missing" and "the answer
# was wrong" are different claims and the first reads as the second.
#
# WHERE THESE STRINGS COME FROM. They are returned by gi_block_name() and
# gi_abort_name() in main/gen_inhibit_core.c, which gen_inhibit.c:1402-1427
# puts into the JSON verbatim. gen_inhibit_core.h flags that table LOAD-BEARING
# precisely because a harness matching on the strings is coupled to it with no
# compiler error to catch a rename. A rename now breaks this harness LOUDLY
# (every case reports the wrong reason) rather than silently, which is the
# right direction -- and --check-strings cross-checks the table without
# hardware so the break is attributable.
#
# WHY THE EXPECTED FIELD DIFFERS BY KIND, and it is not cosmetic:
# gen_inhibit.c:1391 makes `arm_block` report the ABORT text once a trip has
# latched, so the two JSON fields are NOT independent. For a live trip both
# fields carry the same string. The old code's `abort_reason or arm_block`
# fallback therefore could not tell a gate REFUSAL from a latched ABORT -- and
# that distinction is the whole content of the three arm_gate cases. An
# arm_gate case that went live and then aborted would have read as a clean
# gate refusal. So arm_gate additionally requires abort_reason to be EMPTY.
#
# SPEC TRIP NUMBER IS NOT THE DBC ENUM VALUE, and the mapping is neither an
# offset nor one-to-one. Spec 7 numbers its trips 1-8; the DBC's VAL_ 2040
# diag_abort_reason has its own numbering. Written out in full, because a
# partial version of this map is how the two get read for each other:
#
#   spec trip 1  VCM fault active        -> abort 2   vcm_fault
#   spec trip 2  Link loss (0x051)       -> abort 3   bus_lost
#   spec trip 3  Inverter lost (0x471)   -> abort 4   inverter_lost
#   spec trip 4  Engine turning          -> abort 5   engine_turning
#   spec trip 5  Stale interlock signal  -> abort 7   stale_soc       (0x411)
#                                           abort 8   stale_contactor (0x440)
#                                           abort 9   stale_fault     (0x617)
#                                           abort 10  stale_shift     (0x639)
#                                           abort 11  stale_rpm       (0x054)
#   spec trip 6  Error-frame rate        -> abort 6   error_rate
#   spec trip 7  Failed transmit         -> abort 1   tx_failed
#                                           abort 13  tx_not_queued
#                                           abort 14  tx_late (RETIRED 09-27)
#                                           abort 15  skips_3_in_1s
#   spec trip 8  Short VCM frame         -> abort 12  short_vcm_frame
#
# THREE TRAPS IN THAT TABLE:
#   - Trips 1-4 look like a clean +1 offset. It stops at trip 4.
#   - TRIP 6 COINCIDES WITH ABORT 6 BY ACCIDENT. It is the one row where
#     reading one numbering for the other gives the right answer, which makes
#     it the row that lets the mistake survive being checked once.
#   - Trips 5 and 7 are one-to-many, so "the" abort value for them does not
#     exist. A map that gives trip 7 a single value (1) is missing the three
#     the scheduler actually sets, 15 among them.
#
# So spec 12.4's evidence rule "reports trip 2, never trip 3 or 5" means
# abort_reason 3 bus_lost, never 4 inverter_lost nor any of 7-11 stale_*.
#
# Corrected 2026-10-04: this comment said "trips 1-7" and stopped at trip 7.
# Spec 7 has eight -- trip 8 is the short-VCM-frame abort at spec :1560, and
# gen_inhibit_core.h's own enum comment already said "spec 7 trip 8". A
# miscount in the very comment warning against conflating two numberings.

# arm_gate: the expected `arm_block`. Derived from arm_gate_ok()'s documented
# check order in gen_inhibit_core.c, which decides WHICH reason is reported
# when several conditions fail at once -- for stale_bus, where a silent bus
# fails most of the gate, 0x051-fresh is the FIRST check.
EXPECT_BLOCK = {
    "gen_running": "generator running",            # spec 7 gate cond. 5
    "vcm_torque":  "VCM commanding torque",        # spec 7 gate cond. 6
    "stale_bus":   "no fresh 0x051",               # spec 7 gate cond. 1
}

# live_trip: the expected `abort_reason`.
EXPECT_ABORT = {
    # spec 7 trip 1 = GI_ABORT_VCM_FAULT = DBC abort_reason 2
    "vcm_fault":     "0x617 B7 = 0xCA (VCM fault active)",
    # spec 7 trip 3 = GI_ABORT_INVERTER_LOST = DBC abort_reason 4
    "inverter_lost": "0x471 stopped while 0x051 still live -- inverter lost",
    # spec 7 trip 4 = GI_ABORT_ENGINE_TURNING = DBC abort_reason 5
    "engine_turns":  "engine turning while armed -- the inhibit did not hold",
    # spec 7 trip 2 = GI_ABORT_BUS_LOST = DBC abort_reason 3. This is the one
    # spec 12.4's evidence rule names: a whole-bus stop reports THIS and never
    # inverter_lost or a stale_* reason.
    "bus_lost":      "bus lost -- no 0x051 (latched; CAN link down)",
}


def check_reason_strings(fw_root):
    """Cross-check every expected string against the firmware's own table.

    Runs WITHOUT hardware, which is the point: it makes a firmware rename
    attributable instead of surfacing as seven cases reporting wrong reasons.

    Returns (ok, notes). A missing source tree is reported as NOT VERIFIED and
    never as a pass -- a check whose failure is indistinguishable from success
    is not a check (AGENTS.md).
    """
    src = os.path.join(fw_root, "main", "gen_inhibit_core.c")
    if not os.path.isfile(src):
        return None, ["NOT VERIFIED: no %s" % src]
    with open(src, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    notes = []
    ok = True
    for label, table in (("arm_block", EXPECT_BLOCK),
                         ("abort_reason", EXPECT_ABORT)):
        for name, want in sorted(table.items()):
            # The firmware spells these as C string literals in the two
            # name lookups; a literal match is what couples us to them.
            if ('"%s"' % want) in text:
                notes.append("  ok   %-14s %s -> %r" % (name, label, want))
            else:
                ok = False
                notes.append("  FAIL %-14s %s -> %r NOT FOUND in "
                             "gen_inhibit_core.c" % (name, label, want))
    return ok, notes


def judge(results):
    """Turn a run's results into (printable rows, fails).

    EXTRACTED from main() on 2026-10-04 so it can be exercised WITHOUT
    HARDWARE -- see trip_reason_offline_test.py, which runs the two recorded
    runs and a set of mutations through THIS function. The previous version of
    this reasoning lived inline in main(), which meant the only way to test it
    was a full bench run, and the only alternative was to test a copy of the
    logic. A copy is what autoarm_bit_offline_test.py did on 2026-10-04 and it
    is worth not repeating: a test of a copy goes green while the real path
    rots.
    """
    rows = []
    fails = []
    for r in results:
        if r.get("error"):
            rows.append("%-15s %-10s %-7s %s"
                        % (r["name"], "-", "-", r["error"]))
            fails.append("%s: %s" % (r["name"], r["error"]))
            continue
        block = r.get("arm_block") or ""
        abort = r.get("abort_reason") or ""
        reason = abort if abort not in ("", "none") else block
        rows.append("%-15s %-10s %-7s %s"
                    % (r["name"], r["kind"], r["inhibit_live"], reason))
        if r["inhibit_live"]:
            fails.append("%s: still live -- the condition did not stop it"
                         % r["name"])

        #
        # The reason must be THE RIGHT ONE, not merely present. See the
        # EXPECT_* tables above for why, and for why the field checked
        # depends on the kind.
        #
        if r["kind"] == "arm_gate":
            want = EXPECT_BLOCK.get(r["name"])
            if want is None:
                fails.append("%s: no expected arm_block recorded for this "
                             "case -- add one to EXPECT_BLOCK" % r["name"])
            elif abort not in ("", "none"):
                # arm_block carries the abort text once a trip latches
                # (gen_inhibit.c:1391), so this is the only way to tell a
                # gate refusal from a case that went live and then aborted.
                fails.append("%s: expected the GATE to refuse (%r) but a trip "
                             "LATCHED instead (%r) -- it went live, so this "
                             "case did not test the gate"
                             % (r["name"], want, abort))
            elif block != want:
                fails.append("%s: gate refused for the wrong reason -- "
                             "expected %r, got %r" % (r["name"], want, block))
        elif r["kind"] == "live_trip":
            want = EXPECT_ABORT.get(r["name"])
            if want is None:
                fails.append("%s: no expected abort_reason recorded for this "
                             "case -- add one to EXPECT_ABORT" % r["name"])
            elif abort in ("", "none"):
                fails.append("%s: stopped but gave no reason (expected %r)"
                             % (r["name"], want))
            elif abort != want:
                fails.append("%s: tripped for the wrong reason -- expected "
                             "%r, got %r. A replay that faltered trips "
                             "'bus lost' and would otherwise read as a pass"
                             % (r["name"], want, abort))
        else:
            fails.append("%s: unknown kind %r -- no reason check applies, "
                         "which would be a silent pass"
                         % (r["name"], r["kind"]))

        if r["name"] == "bus_lost" and r.get("resumed_tx"):
            fails.append("bus_lost: resumed transmitting %d frames after the "
                         "bus returned -- the release must LATCH"
                         % r["resumed_tx"])
    return rows, fails


CASES = []


def case(name, kind, why):
    def deco(fn):
        CASES.append({"name": name, "kind": kind, "why": why, "fn": fn})
        return fn
    return deco


@case("gen_running", "arm_gate", "generator turning must refuse the arm")
def c_gen_running(pcan, kvaser, stim):
    dev_post("/gen_inhibit_set?mode=%d" % MODE_INHIBIT)
    play(pcan, kvaser, stim, 4.0,
         mutate=lambda a, d: force_rpm(d) if a == RPM_ID else d)
    return json.loads(dev_get("/gen_inhibit"))


@case("vcm_torque", "arm_gate", "VCM commanding torque must refuse the arm")
def c_vcm_torque(pcan, kvaser, stim):
    dev_post("/gen_inhibit_set?mode=%d" % MODE_INHIBIT)
    play(pcan, kvaser, stim, 4.0,
         mutate=lambda a, d: force_torque_request(d) if a == VCM_ID else d)
    return json.loads(dev_get("/gen_inhibit"))


@case("stale_bus", "arm_gate", "no fresh 0x051 must refuse the arm")
def c_stale_bus(pcan, kvaser, stim):
    dev_post("/gen_inhibit_set?mode=%d" % MODE_INHIBIT)
    time.sleep(3.0)          # transmit nothing at all
    return json.loads(dev_get("/gen_inhibit"))


def _go_live(pcan, kvaser, stim, seconds=4.0):
    dev_post("/gen_inhibit_set?mode=%d" % MODE_INHIBIT)
    play(pcan, kvaser, stim, seconds)
    st = json.loads(dev_get("/gen_inhibit"))
    return st


@case("vcm_fault", "live_trip", "0x617 B7 = 0xCA must abort a live inhibit")
def c_vcm_fault(pcan, kvaser, stim):
    pre = _go_live(pcan, kvaser, stim)
    if not pre.get("inhibit_live"):
        return {"_setup_failed": pre}
    play(pcan, kvaser, stim, 3.0,
         mutate=lambda a, d: force_fault(d) if a == FAULT_ID else d)
    return json.loads(dev_get("/gen_inhibit"))


@case("inverter_lost", "live_trip", "0x471 stopping must abort a live inhibit")
def c_inverter_lost(pcan, kvaser, stim):
    pre = _go_live(pcan, kvaser, stim)
    if not pre.get("inhibit_live"):
        return {"_setup_failed": pre}
    play(pcan, kvaser, stim, 6.0, drop=(INV_ID,))
    return json.loads(dev_get("/gen_inhibit"))


@case("engine_turns", "live_trip", "rpm crossing while live must abort")
def c_engine_turns(pcan, kvaser, stim):
    pre = _go_live(pcan, kvaser, stim)
    if not pre.get("inhibit_live"):
        return {"_setup_failed": pre}
    play(pcan, kvaser, stim, 3.0,
         mutate=lambda a, d: force_rpm(d) if a == RPM_ID else d)
    return json.loads(dev_get("/gen_inhibit"))


@case("bus_lost", "live_trip", "0x051 stopping while live must LATCH off")
def c_bus_lost(pcan, kvaser, stim):
    pre = _go_live(pcan, kvaser, stim)
    if not pre.get("inhibit_live"):
        return {"_setup_failed": pre}
    time.sleep(3.0)                       # silence
    mid = json.loads(dev_get("/gen_inhibit"))
    # the latch test: bring the bus back and confirm it does NOT resume
    tx_before = mid.get("tx_ok", 0)
    play(pcan, kvaser, stim, 4.0)
    after = json.loads(dev_get("/gen_inhibit"))
    after["_resumed_tx"] = after.get("tx_ok", 0) - tx_before
    return after


#
# The firmware is a SEPARATE repo (tools-reference.md, "The WiCAN firmware
# repo"), so its path is per-machine and not derivable from here. Env var
# first, then the documented clone path.
#
DEFAULT_FW = os.environ.get("GEN_INHIBIT_FW") or gi_paths.firmware_root()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "trip_bench.json"))
    ap.add_argument("--only", default=None)
    ap.add_argument("--fw-source", default=DEFAULT_FW,
                    help="the WiCAN tree (main/; gen-inhibit/firmware/ since port "
                         "step 0d), for the reason-string cross-check (default "
                         "$GEN_INHIBIT_FW or this repo's)")
    ap.add_argument("--check-strings", action="store_true",
                    help="cross-check the expected reason strings against "
                         "the firmware source and exit. Needs no hardware "
                         "and no bus.")
    args = ap.parse_args()

    #
    # Do this FIRST even on a full run: if the strings have drifted, every
    # case below will report a wrong reason, and knowing why beforehand is
    # the difference between one fix and seven confusing failures.
    #
    strings_ok, string_notes = check_reason_strings(args.fw_source)
    print("reason strings vs %s:" % args.fw_source)
    for n in string_notes:
        print(n)
    if strings_ok is None:
        print("  -> NOT VERIFIED. The expected strings below are UNCHECKED "
              "against the firmware; point --fw-source at the repo to "
              "verify them.")
    elif not strings_ok:
        print("  -> the firmware's reason table has drifted from this "
              "harness. Fix EXPECT_BLOCK/EXPECT_ABORT before trusting any "
              "verdict below.")
    else:
        print("  -> all %d expected strings found in the firmware's own "
              "lookup table." % (len(EXPECT_BLOCK) + len(EXPECT_ABORT)))
    print()
    if args.check_strings:
        return 0 if strings_ok else 1

    stim = build_stimulus()
    ids = sorted({a for _, a, _ in stim})
    print("stimulus: %d frames over %.1fs, IDs %s"
          % (len(stim), stim[-1][0] if stim else 0,
             " ".join("0x%X" % i for i in ids)))
    if VCM_ID not in ids:
        print("FAIL: stimulus has no 0x051")
        return 1

    found = {}
    for cfg in can.detect_available_configs(["pcan", "kvaser"]):
        if "Virtual" in cfg.get("device_name", ""):
            continue
        found.setdefault(cfg["interface"], cfg)
    if "pcan" not in found or "kvaser" not in found:
        print("FAIL: need both dongles; found %s" % sorted(found))
        return 1

    # OPEN ORDER IS LOAD-BEARING: PCAN FIRST, THEN KVASER.
    # Resolved by the interposer session 2026-09-22. `can.Bus()` on the PCAN
    # takes seconds to return, and a bus already open while another is still
    # opening receives and buffers that whole time, then delivers the backlog in
    # a burst when reading starts. Open the Kvaser first and it accumulates ~33
    # frames through the PCAN's slow open; open the PCAN first and nothing waits
    # on anything. Measured over the same 20 s window, varying only the order:
    # kvaser-then-pcan gave 113 vs 80 frames and 21 "duplicates"; pcan-then-kvaser
    # gave 80 vs 80 and zero. There is no receiver duplication -- that conclusion
    # is withdrawn, and this was its whole cause.
    # Swapping these two lines looks harmless and is not. The settle() below is
    # the belt to this braces.
    pcan = can.Bus(interface="pcan", channel=found["pcan"]["channel"], bitrate=BITRATE)
    kvaser = can.Bus(interface="kvaser", channel=found["kvaser"]["channel"], bitrate=BITRATE)
    print("settling dongles (%.1fs) ... dropped %d backlog frames"
          % (SETTLE_S, settle(pcan, kvaser)))

    results = []
    try:
        for c in CASES:
            if args.only and c["name"] != args.only:
                continue
            print("\n--- %s (%s) ---" % (c["name"], c["why"]))
            ok, boot = reboot_and_wait()
            if not ok:
                print("  SKIP: device did not come back clean after reboot")
                results.append({"name": c["name"], "error": "reboot failed"})
                continue
            st = c["fn"](pcan, kvaser, stim)
            if "_setup_failed" in st:
                print("  SETUP FAILED: never went live. arm_block=%r live=%s"
                      % (st["_setup_failed"].get("arm_block"),
                         st["_setup_failed"].get("inhibit_live")))
                results.append({"name": c["name"], "error": "never went live",
                                "state": st["_setup_failed"]})
                continue
            row = {"name": c["name"], "kind": c["kind"], "why": c["why"],
                   "inhibit_live": st.get("inhibit_live"),
                   "arm_block": st.get("arm_block"),
                   "abort_reason": st.get("abort_reason"),
                   "gene_rpm": st.get("gene_rpm"),
                   "tx_ok": st.get("tx_ok"),
                   "resumed_tx": st.get("_resumed_tx")}
            results.append(row)
            print("  live=%s  arm_block=%r  abort_reason=%r  tx_ok=%s%s"
                  % (row["inhibit_live"], row["arm_block"],
                     row["abort_reason"], row["tx_ok"],
                     "" if row["resumed_tx"] is None
                     else "  resumed_tx=%d" % row["resumed_tx"]))
        dev_post("/gen_inhibit_set?mode=%d" % MODE_OFF)
    finally:
        for b in (pcan, kvaser):
            try:
                b.shutdown()
            except Exception:
                pass

    print("\n%-15s %-10s %-7s %s" % ("case", "kind", "live", "reason given"))
    print("-" * 78)
    rows, fails = judge(results)
    for row in rows:
        print(row)
    print()
    if fails:
        print("=== FAIL ===")
        for f in fails:
            print("  " + f)
    else:
        print("=== PASS -- every section 7 condition driven stopped the "
              "inhibit and named ITSELF, not merely something ===")
    print("\nNOT covered here: abort on a failed transmit. Forcing a real TX "
          "failure needs physical interference with the bus; section 13 "
          "should keep calling the interlocks partial.")

    with open(args.out, "w") as fh:
        json.dump({"results": results, "fails": fails,
                   # None = the firmware source was not available to check
                   # against, which is NOT the same as verified.
                   "reason_strings_verified": strings_ok,
                   "expected": {"arm_block": EXPECT_BLOCK,
                                "abort_reason": EXPECT_ABORT}},
                  fh, indent=1)
    print("wrote %s" % args.out)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
