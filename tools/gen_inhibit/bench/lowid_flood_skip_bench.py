#!/usr/bin/env python3
"""Does the skip accounting tell the truth when `0x050` crowds the inhibit frame?

A3, the skip half. Spec 5.2 item 5 and section 7 trip 7 count SKIPPED inhibit
frames -- withdrawn and late -- and nothing has ever driven that path hard
enough to check whether the counts are right. This does, by flooding the one
standard identifier on the powertrain bus that outranks ours.

WHY `0x050` AND NOTHING ELSE. `lowid_priority_census.py` swept 1,484
powertrain captures and found six things outrank a standard `0x051`: `0x050`
(4,243,511 frames), `0x004`, `0x000`, `0x00C`, and two extended frames at base
id `0x000`. `0x050` is the only one of the six that is common rather than
incidental, so it is both the realistic crowder and the effective one. It is
also absent from the firmware's consumed set -- `gen_inhibit_core.h` declares
`0x051`, `0x411`, `0x471`, `0x054`, `0x440`, `0x592`, `0x639`, `0x617`, and
`grep` finds `0x050` nowhere in `main/` -- so flooding it changes ARBITRATION
and RECEIVE LOAD and cannot change a single decision the device makes. That
separation is the whole reason this is a clean instrument: anything that moves
in the skip counters moved because the frame could not get out, not because
the device was told something different.

WHAT "NOT A TRUCK THRESHOLD" MEANS, agreed with the reviewing session. The
flood here is far denser than anything the truck produces -- on the truck
`0x050` is ~2-12 frames per 100,000. This is a stress test of the ACCOUNTING,
asking whether the device's own skip counts match what an independent witness
saw on the wire. A pass does not license a claim about truck behaviour and a
failure is a bug in the counting, not a prediction about the vehicle.

THE FLOOD SHARES THE REPLAY'S ADAPTER, DELIBERATELY AND WITH A COST.

Frames queued in one adapter do not arbitrate against each other; they leave
in queue order. So filler queued ahead of a `0x051` delays that `0x051`
regardless of priority, which is NOT what the truck does and which corrupts
the trigger timing. `e4_device_calibration.py` hit this and solved it with a
second adapter over slcan. That board is no longer on this bench -- COM3 is
now the interposer session's board -- and the user's direction (2026-10-06) is
to keep the hardware configuration stable, so the single-adapter path is what
is available.

The mitigation is to pace the flood by WIRE TIME rather than by `send()`
returning, exactly as `e4_device_calibration`'s inline `--flood` does, and the
known residual is that at very high carried load some offered frames never
reach the wire (384 of 8,998 at 97.6 %, measured 2026-09-29). Two things make
that tolerable here where it was not there: the stimulus is the INTERLOCK IDS
ONLY, not a full 2,350 fps bus replay, so the flood has far more headroom; and
the quantity being measured is the device's own transmissions against a
witness, not the replay's fidelity. The run still reports replay delivery and
refuses a verdict if the `0x051` stream itself was damaged -- a crowded
trigger and a crowded answer produce the same skip count and must be told
apart.

THE WITNESS DRAINS ON ITS OWN THREAD. A loop that interleaves `send()` and
`recv()` paces itself to ~970 fps against a 4,505 fps wire with zero
exceptions to announce the cap (measured 2026-10-05; see `tools-reference.md`,
"Throughput traps common to both dongles"). Every sparse stimulus in this
investigation traced to that shape, including two that were reported as
results before anyone noticed. Here the main thread replays and floods; the
Kvaser is drained continuously by `Witness`, which keeps arrival ORDER.

OURS AGAINST THE VCM'S: NOTHING ABOUT THE FRAME SEPARATES THEM, and that is
by design rather than by accident.

Measured on this bench, 2026-10-06. The replayed VCM `0x051` is DLC 6 and
carries `08 00 80 ff 7f 0n`, where `n` is a 4-bit rolling counter -- 16
distinct payloads across 1,200 frames. `build_inhibit()`
(`gen_inhibit_core.c:1749`) emits DLC 6 with `data[0]=GI_B0_ENGINE_OFF`,
`data[1..2]` zero torque, `data[3..4]` COPIED from the trigger, and
`data[5] = (rx[5] & 0x0F) + 1`. So the device's frame is **byte-identical to
the VCM's own next frame**. It has to be: the inverter obeys it precisely
because it is indistinguishable from a real command.

Two discriminators were tried and both are unusable here:

  * PAYLOAD. Tested directly -- it classed 0 of 1,186 device transmits as
    ours, because every device payload is also a VCM payload. DLC is no help
    either; both are 6.
  * TRAIL TIMING, which is what `e4_device_calibration.py` uses. It cannot be
    used by THIS bench: the whole purpose here is to delay the device's
    answer, so an answer pushed past the trail window is scored as a VCM
    frame -- which simultaneously invents a VCM frame and books the real one
    as withdrawn. It would fail in the direction of the hypothesis, under
    exactly the condition being tested, and the failure would read as a
    stronger result. Measured at the arm with no flood at all it already
    undercounted: 1,025 against the device's own 1,186.

WHAT IS USED INSTEAD: RUNS OF EQUAL COUNTER IN WIRE ORDER. On a healthy wire
the sequence is

    VCM(n), ours(n+1), VCM(n+1), ours(n+2), VCM(n+2), ...

so every counter value appears EXACTLY TWICE IN A ROW -- the device's echo
first, then the VCM's own frame carrying the value the device already sent. A
run of two is an answered cycle; a run of ONE is a cycle where one of the pair
never reached the wire, and since this script sends the VCM half and the
adapter reports its delivery, the missing member is the device's answer. A
value that reappears after an intervening different value is an answer that
arrived after its trigger's successor, which is LATE.

This depends on order and on a counter the firmware itself derives, and on no
timestamp at all -- which matters because `pcan_backtoback_capability.py`
measured the Kvaser's resolution floor at ~170 us against 190 us of air time,
with 318 of 468 inter-frame gaps coming out NEGATIVE. Order survives a broken
clock; a 0.5 ms trail test does not.

AND IT IS CHECKED RATHER THAN TRUSTED. `tx_ok` is the device's own count of
inhibit frames transmitted -- ground truth from the other side of the wire,
independent of the witness and of this classifier. The run compares the
counter-run prediction against it at the arm, BEFORE any flood, and refuses to
flood at all if they disagree. The trail-timing figure is computed alongside
purely so the disagreement is on the record.

WHAT IS ATTRIBUTED BEFORE ANYTHING IS COUNTED. A skip during the burst means
nothing if the device had stopped inhibiting for an unrelated reason, so the
run refuses to report skip figures unless, across the whole burst:

    mode stayed 3, inhibit_live stayed true, abort_latched stayed false,
    arm_block stayed empty, and the controller state stayed `running`

and it reports WHICH of those failed if one did. The state check is new and is
there because of 2026-10-06: the device can sit in bus-off, silent, with
`bus_on` reading true, and from the wire that is indistinguishable from an
unbroken run of withdrawn frames.

    py -3.14 tools/gen_inhibit/bench/lowid_flood_skip_bench.py
    py -3.14 ... --flood-occupancy 0.85 --burst-seconds 30
    py -3.14 ... --dry-run        # preconditions and arming only, no flood
"""

import argparse
import collections
import json
import os
import sys
import threading
import time
import urllib.request

import gi_paths  # SeaDrive paths (goal-20 move, 2026-10-22)
gi_paths.use_canre()

import can                                                     # noqa: E402
from canre.parsers import parse_file                           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()

HOST = "192.168.80.1"
BITRATE = 500000
MODE_OFF = 0
MODE_INHIBIT = 3

VCM_ID = 0x051
FLOOD_ID = 0x050
RPM_ID = 0x054
SOC_ID = 0x411
INV_ID = 0x471
FAULT_ID = 0x617
MMODE_ID = 0x639
KEY_ID = 0x592
CONTACTOR_ID = 0x440
WANT = (VCM_ID, RPM_ID, SOC_ID, INV_ID, FAULT_ID, MMODE_ID, KEY_ID,
        CONTACTOR_ID)

# The same armable slice the trip bench uses, so the arming behaviour is one
# already characterised on this bench rather than a new unknown.
CAPTURE = gi_paths.capture("vtrux_20260719_190019_T4.log")
SLICE_FROM = 95.0
SLICE_SECONDS = 12.0
CACHE = os.path.join(RUNS, "lowid_flood_stimulus.json")

# A 0x051 closer than this to its predecessor is ours, not the VCM's. Matches
# e4_device_calibration.py; see the docstring.
TRAIL_MAX_US = 3000.0

# 111 bits including IFS at 500 kbit for an 8-byte standard frame.
AIR_US = 222.0
SPIN_THRESHOLD_S = 0.002
SETTLE_S = 2.0

# Below this many witnessed VCM frames the burst did not happen and no skip
# figure from it means anything.
MIN_VCM_IN_BURST = 50


def dev_get(path, timeout=8.0):
    with urllib.request.urlopen("http://%s%s" % (HOST, path),
                                timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def dev_post(path, timeout=8.0):
    req = urllib.request.Request("http://%s%s" % (HOST, path), data=b"",
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status == 200


def page():
    try:
        d, _ = json.JSONDecoder().raw_decode(dev_get("/gen_inhibit").lstrip())
        return d
    except Exception:
        return None


def build_id():
    """The running image, {"elf_sha256": ...}, from one GET of /gen_inhibit, whose
    elf_sha256 names the image file (spec 11, "The page names the binary it runs"); None
    when the page does not answer, does not parse, or names no 64 lower-case hex digits.

    NEVER /check_status (reviewer, 2026-10-20): it answers with the device's config,
    credentials included. Until 2026-10-25 this read git_version and fw_version from it,
    and the suite's B1, B4, B9, B10, B12 and B20 called it (gen-inhibit reviewer,
    2026-10-25).
    """
    p = page()
    sha = p.get("elf_sha256") if isinstance(p, dict) else None
    if isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
        return {"elf_sha256": sha}
    return None


def snap(p):
    """The fields this question turns on."""
    if p is None:
        return None
    drv = p.get("drv") or {}
    sch = p.get("sched") or {}
    return {
        "mode": p.get("mode"),
        "inhibit_live": p.get("inhibit_live"),
        "abort_latched": p.get("abort_latched"),
        "abort_reason": p.get("abort_reason"),
        "arm_block": p.get("arm_block"),
        "disabled": p.get("disabled"),
        "disable_reason": p.get("disable_reason"),
        "tx_ok": p.get("tx_ok"),
        "tx_fail": p.get("tx_fail"),
        "ctr_bad": p.get("ctr_bad"),
        "tx_queued_behind": p.get("tx_queued_behind"),
        "skips_withdrawn": p.get("skips_withdrawn"),
        "skips_late": p.get("skips_late"),
        "would_tx": p.get("would_tx"),
        "sched_skipped": sch.get("skipped"),
        "sched_tx_failed": sch.get("tx_failed"),
        "sched_tx_refused": sch.get("tx_refused"),
        "sched_aborts": sch.get("aborts"),
        # Added 2026-10-06 (spec 11). A silent bus-off and a long run of
        # withdrawn frames look identical from the wire.
        "drv_state": drv.get("state"),
        "drv_tec": drv.get("tec"),
        "drv_rec": drv.get("rec"),
        "drv_bus_error": drv.get("bus_error"),
        "drv_msgs_to_tx": drv.get("msgs_to_tx"),
        "bus_on": p.get("bus_on"),
    }


def build_stimulus():
    """The interlock IDs only, at recorded timing, from the armable slice."""
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
    os.makedirs(RUNS, exist_ok=True)
    with open(CACHE, "w") as fh:
        json.dump([(t, a, d.hex()) for t, a, d in out], fh)
    return out


class Witness(threading.Thread):
    """Drain the Kvaser continuously, preserving ARRIVAL ORDER.

    On its own thread for the reason in the module docstring: draining inside
    the send loop paces the sender and the cap is silent. Order is what the
    classifier uses; the timestamps are recorded but are NOT trusted at frame
    scale (resolution floor ~170 us against 190 us of air).
    """

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.stop_flag = threading.Event()
        self.frames = []          # (perf, arb, bytes)
        self.errors = 0
        self.lock = threading.Lock()

    def run(self):
        while not self.stop_flag.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                now = time.perf_counter()
                with self.lock:
                    if m.is_error_frame:
                        self.errors += 1
                    else:
                        self.frames.append((now, m.arbitration_id,
                                            bytes(m.data)))
                m = self.bus.recv(timeout=0.0)

    def mark(self):
        with self.lock:
            return len(self.frames)

    def slice(self, a, b):
        with self.lock:
            return list(self.frames[a:b])


def play(pcan, stim, seconds, flood_period=None, stats=None,
         wall_frames=0):
    """Replay the interlock slice, looping, optionally flooding 0x050.

    The flood is paced by WIRE TIME. It shares this adapter with the replay, so
    pacing by send-return would dump frames ahead of a 0x051 and push the
    trigger stream back -- see the module docstring.
    """
    t0 = time.perf_counter()
    base = 0.0
    sent = fail = flood_sent = flood_fail = vcm_offered = 0
    next_flood = 0.0
    flood_msg = can.Message(arbitration_id=FLOOD_ID,
                            data=bytes([0xA5] * 8), is_extended_id=False)
    while time.perf_counter() - t0 < seconds:
        for rel, arb, data in stim:
            now = time.perf_counter()
            if now - t0 >= seconds:
                break
            target = t0 + base + rel
            #
            # FILL THE WAIT WITH FLOOD, INCLUDING THE PART THAT WOULD BE SLEPT.
            #
            # The first version only emitted flood inside the final
            # sub-SPIN_THRESHOLD_S stretch and slept through everything above
            # it, so the flood was capped by how much spin time the replay
            # happened to leave: it reached 44 % of the wire against a 75 %
            # target and produced no skips at all. The target was not
            # unachievable, it was never attempted.
            #
            # Flood frames are never queued ahead of a trigger that is already
            # due, because the loop exits the moment `target` passes. That is
            # what keeps a shared adapter honest -- see the module docstring.
            #
            while True:
                now = time.perf_counter()
                left = target - now
                if left <= 0:
                    break
                if flood_period is not None:
                    el = now - t0
                    if el >= next_flood:
                        try:
                            pcan.send(flood_msg)
                            flood_sent += 1
                        except Exception:
                            flood_fail += 1
                        next_flood = el + flood_period
                        continue
                    nap = min(left, (t0 + next_flood) - now)
                    if nap > SPIN_THRESHOLD_S:
                        time.sleep(nap - SPIN_THRESHOLD_S)
                elif left > SPIN_THRESHOLD_S:
                    time.sleep(left - SPIN_THRESHOLD_S)
            try:
                pcan.send(can.Message(arbitration_id=arb, data=data,
                                      is_extended_id=False))
                sent += 1
                if arb == VCM_ID:
                    vcm_offered += 1
            except Exception:
                fail += 1
            #
            # THE WALL. A PACED flood cannot withdraw an inhibit frame and the
            # reason is structural: the device's answer only has to win ONE
            # gap, and at high paced occupancy gaps are plentiful. (Paced runs
            # at several flood levels on 2026-10-06 reportedly answered every
            # trigger, but their records were overwritten -- see
            # run_out_path() -- so that is NOT evidence; the one surviving
            # record, 21:44, is a wall run.) Withdrawing it means denying it the
            # wire for a whole VCM period, which needs CONTINUOUS 0x050, and
            # raising the paced rate to get there starves the trigger as well
            # -- the flood outranks the trigger too, from the same queue.
            #
            # Placement solves what density cannot. These frames are queued
            # IMMEDIATELY AFTER the trigger, so they cannot delay it: one
            # adapter's frames leave in queue order, and the trigger is
            # already in front of them. They then hold the wire for exactly
            # the window in which the device wants to answer. That turns the
            # shared adapter from a limitation into the mechanism -- the same
            # queue-order property that makes a shared adapter wrong for
            # paced filler makes it right for this.
            #
            if wall_frames and arb == VCM_ID:
                #
                # PACED TO WIRE TIME, NOT BLASTED. Queueing the wall as fast
                # as send() accepts it makes the adapter's transmit queue a
                # reservoir: a 50-frame-wall run on 2026-10-06 reportedly
                # displaced hundreds of triggers out of the window and left
                # flood frames draining into the recovery window. That run's
                # record was overwritten, so the figures once quoted here are
                # withdrawn (2026-10-07); the mechanism is the reason for the
                # pacing, not a measurement. send() returns at queueing; the
                # queue then decides when things happen, not this loop.
                #
                wall_t = time.perf_counter()
                for _ in range(wall_frames):
                    try:
                        pcan.send(flood_msg)
                        flood_sent += 1
                    except Exception:
                        flood_fail += 1
                    wall_t += AIR_US / 1e6
                    while time.perf_counter() < wall_t:
                        pass
        base += stim[-1][0] if stim else 0.0
    if stats is not None:
        stats.update({"replay_sent": sent, "replay_fail": fail,
                      "vcm_offered": vcm_offered,
                      "flood_sent": flood_sent, "flood_fail": flood_fail,
                      "seconds": round(time.perf_counter() - t0, 3)})


def classify(frames, trail_us, replay_payloads):
    """Wire-order classification of the device's answers. See the docstring.

    OURS IS DECIDED BY PAYLOAD, NOT BY TIMING, and that change is the whole
    reason this run is trustworthy under flood.

    The obvious discriminator is trail timing -- the VCM runs at ~10 ms and
    our answer trails its trigger by 500 us -- and it is what
    `e4_device_calibration.py` uses. It CANNOT be used here. This bench exists
    to delay the device's answer; an answer delayed past the trail window gets
    classed as a VCM frame, which simultaneously invents a VCM frame and books
    the real one as withdrawn. The classifier would therefore fail in exactly
    the direction of the hypothesis, under exactly the condition being tested,
    and the failure would read as a stronger result.

    What is used instead is the set of `0x051` payloads THIS SCRIPT SENDS,
    which is known exactly because the replay is deterministic. A witnessed
    `0x051` whose payload is not in that set was generated by the firmware.
    Payload does not change with delay, so the discriminator is independent of
    the quantity under test.

    IT IS STILL SCORED RATHER THAN ASSUMED. Two checks travel with it: the
    trail-timing classification is computed alongside and the disagreement is
    reported, and the caller compares the payload count against the device's
    own `tx_ok` delta, which is ground truth from the other side of the wire.
    If the device ever emits a payload the VCM also sent, this undercounts --
    so a collision is looked for rather than assumed away.
    """
    seq = [(t, a, d) for t, a, d in frames if a == VCM_ID and len(d) > 5]
    vals = [d[5] & 0x0F for _, _, d in seq]

    # The timing classifier, computed only to be disagreed with.
    t_ours = 0
    prev_t = None
    for t, _, _ in seq:
        if prev_t is not None and (t - prev_t) * 1e6 <= trail_us:
            t_ours += 1
        prev_t = t

    #
    # RUNS OF EQUAL COUNTER. On a healthy wire the sequence is
    #   VCM(n), ours(n+1), VCM(n+1), ours(n+2), VCM(n+2), ...
    # so every counter value appears EXACTLY TWICE IN A ROW -- the device's
    # answer first, then the VCM's own frame carrying the value the device
    # already echoed. A run of two is an answered cycle. A run of ONE is a
    # cycle where one of the pair never reached the wire, and since this
    # script sends the VCM half and the adapter reports its delivery, the
    # missing member is the device's answer: a WITHDRAWN inhibit frame.
    #
    runs = []
    for v in vals:
        if runs and runs[-1][0] == v:
            runs[-1][1] += 1
        else:
            runs.append([v, 1])

    #
    # DROP THE FIRST AND LAST RUN. A window is a slice of a continuous
    # stream, so its first run may be missing the member that fell in the
    # previous window and its last may be missing the one that falls in the
    # next. Both read as a run of one, i.e. as a withdrawn inhibit frame.
    #
    # This was not theoretical. The first version of this classifier reported
    # exactly 2 withdrawn in EVERY window -- baseline with no flood at all,
    # burst, and recovery -- and called the device's 0 a disagreement. A count
    # that does not move when the stimulus moves is not measuring the
    # stimulus; it was measuring its own edges.
    #
    boundary_runs = runs[:1] + runs[-1:] if len(runs) > 1 else runs[:]
    runs = runs[1:-1] if len(runs) > 2 else []

    answered = sum(1 for _, n in runs if n == 2)
    withdrawn = sum(1 for _, n in runs if n == 1)
    overlong = sum(1 for _, n in runs if n > 2)
    ours_predicted = sum(max(n - 1, 0) for _, n in runs)

    #
    # LATE, in this scheme, is a run whose two members are NOT adjacent: the
    # device's answer arrived after the VCM's next frame, so the sequence
    # reads n, n+1, n+1(ours, late) instead of n, n+1(ours), n+1. That shows
    # up as a run of 1 for value n+1 followed later by the same value
    # reappearing out of step, so it is counted by looking for a value that
    # repeats after an intervening different value within one counter period.
    #
    late = 0
    events = []
    for i, (v, n) in enumerate(runs):
        if n == 1:
            # Does this same value come back within the next two runs? That is
            # an answer that arrived after the trigger's successor.
            if any(runs[j][0] == v for j in range(i + 1, min(i + 3, len(runs)))):
                late += 1
                events.append({"run_index": i, "counter": v,
                               "verdict": "late"})
            else:
                events.append({"run_index": i, "counter": v,
                               "verdict": "withdrawn"})
    withdrawn -= late
    return {
        "frames_0x051": len(seq),
        "runs": len(runs),
        "answered": answered,
        "withdrawn": withdrawn,
        "late": late,
        "runs_over_two": overlong,
        "boundary_runs_excluded": len(boundary_runs),
        "our_frames": ours_predicted,
        "vcm_frames": len(seq) - ours_predicted,
        "discriminator": "runs of equal B5 counter in wire order -- the "
                         "device's frame is BYTE-IDENTICAL to the VCM's next "
                         "frame (both 08 00 80 ff 7f 0n at DLC 6), so neither "
                         "payload nor DLC can separate them and timing fails "
                         "under the flood this bench applies",
        "ours_by_trail_timing": t_ours,
        "trail_us": trail_us,
        "timing_vs_counter_disagreement": t_ours - ours_predicted,
        "events": events[:200],
        "events_truncated": len(events) > 200,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-seconds", type=float, default=12.0)
    ap.add_argument("--baseline-seconds", type=float, default=20.0)
    ap.add_argument("--burst-seconds", type=float, default=30.0)
    ap.add_argument("--recover-seconds", type=float, default=15.0)
    ap.add_argument("--wall-frames", type=int, default=0,
                    help="0x050 frames queued back-to-back immediately after "
                         "each replayed 0x051, to hold the wire across the "
                         "window the device answers in. 0 disables. ~45 "
                         "frames is one VCM period at 222 us each, so values "
                         "near that start eating the next trigger.")
    ap.add_argument("--flood-occupancy", type=float, default=0.75,
                    help="target 0x050 share of the wire (default 0.75); the "
                         "interlock replay takes a few per cent on top")
    ap.add_argument("--dry-run", action="store_true",
                    help="arm and check preconditions, then stop; no flood")
    ap.add_argument("--out", default=None,
                    help="run record path (default: a new timestamped file "
                         "under runs/). An existing file is never "
                         "overwritten; see run_out_path()")
    args = ap.parse_args()
    args.out = run_out_path(args.out, time.strftime("%Y%m%dT%H%M%S"))
    print("record will be written to %s" % args.out)

    flood_period = (AIR_US / 1e6) / max(args.flood_occupancy, 1e-9)

    rec = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wall_frames": args.wall_frames,
        "question": "does the skip accounting match the wire when 0x050 "
                    "crowds the inhibit frame",
        "flood_id": "0x%03X" % FLOOD_ID,
        "flood_occupancy_target": args.flood_occupancy,
        "flood_period_s": flood_period,
        "bitrate": BITRATE,
        "capture": CAPTURE,
        "note": "stress test of skip ACCOUNTING, not a truck threshold -- the "
                "flood is far denser than the truck's 0x050",
    }

    print("=" * 74)
    print("A3: 0x050 flood against the inhibit frame. Skip-accounting stress.")
    print("Shared vehicle segment; the interposer board is a third node and")
    print("stays powered (user, 2026-10-06). Burst edges are recorded.")
    print("=" * 74)

    stim = build_stimulus()
    ids = sorted({a for _, a, _ in stim})
    span = stim[-1][0] if stim else 0.0
    print("\nstimulus: %d frames over %.1f s, ids %s"
          % (len(stim), span, " ".join("0x%03X" % i for i in ids)))
    if VCM_ID not in ids:
        print("FAIL: stimulus carries no 0x051")
        return 1
    # Known exactly, because the replay is deterministic. This is the
    # ours-versus-VCM discriminator; see classify().
    replay_payloads = {d for _, a, d in stim if a == VCM_ID}
    print("replay 0x051 distinct payloads: %d" % len(replay_payloads))
    stim_fps = len(stim) / span if span else 0.0
    stim_occ = stim_fps * AIR_US / 1e6
    print("replay offers %.0f fps = %.1f %% of the wire; flood targets %.0f %%"
          % (stim_fps, stim_occ * 100, args.flood_occupancy * 100))
    rec["replay_fps"] = round(stim_fps, 1)
    rec["replay_occupancy"] = round(stim_occ, 4)
    if stim_occ + args.flood_occupancy > 1.0:
        print("FAIL: replay %.2f + flood %.2f exceeds the wire. Lower "
              "--flood-occupancy." % (stim_occ, args.flood_occupancy))
        return 1

    pre = page()
    if pre is None:
        print("FAIL: device did not answer /gen_inhibit")
        return 1
    rec["build"] = build_id()
    rec["pre"] = snap(pre)
    print("pre: mode %s  build %s" % (pre.get("mode"),
                                      (rec["build"] or {}).get("elf_sha256")))
    if (pre.get("drv") or {}).get("tec") is None and pre.get("drv"):
        print("NOTE: this image predates the spec 11 state/tec/rec fields; "
              "the bus-off attribution below will be blind.")
    if pre.get("mode") != MODE_OFF:
        print("FAIL: device is in mode %s, not OFF. Put it in a known state "
              "deliberately." % pre.get("mode"))
        return 1

    found = {}
    for cfg in can.detect_available_configs(["pcan", "kvaser"]):
        if "Virtual" in cfg.get("device_name", ""):
            continue
        found.setdefault(cfg["interface"], cfg)
    if "pcan" not in found or "kvaser" not in found:
        print("FAIL: need both dongles; found %s" % sorted(found))
        return 1

    # OPEN ORDER IS LOAD-BEARING: PCAN FIRST, THEN KVASER. A bus already open
    # while the other is still opening buffers the whole time and delivers a
    # burst when reading starts (interposer session, 2026-09-22).
    pcan = can.Bus(interface="pcan", channel=found["pcan"]["channel"],
                   bitrate=BITRATE, auto_reset=True)
    kv = can.Bus(interface="kvaser", channel=found["kvaser"]["channel"],
                 bitrate=BITRATE)
    time.sleep(SETTLE_S)
    while kv.recv(timeout=0.0) is not None:
        pass
    wit = Witness(kv)
    wit.start()
    rec["acknowledger_up"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    try:
        # ---------------------------------------------------------- arming
        print("\n[arm] mode %d, replaying interlocks %.0f s"
              % (MODE_INHIBIT, args.arm_seconds))
        dev_post("/gen_inhibit_set?mode=%d" % MODE_INHIBIT)
        st = {}
        play(pcan, stim, args.arm_seconds, stats=st)
        armed = snap(page())
        rec["after_arm"] = armed
        rec["arm_replay"] = st
        print("    inhibit_live %s  arm_block %r  abort %r  tx_ok %s"
              % (armed.get("inhibit_live"), armed.get("arm_block"),
                 armed.get("abort_reason"), armed.get("tx_ok")))
        if not armed.get("inhibit_live"):
            rec["verdict"] = "inconclusive_never_went_live"
            print("\nINCONCLUSIVE: the device never reached inhibit_live, so "
                  "there were no inhibit frames for the flood to crowd. "
                  "arm_block=%r disable_reason=%r"
                  % (armed.get("arm_block"), armed.get("disable_reason")))
            return 1

        # The device's own trail offset bounds the ours-vs-VCM window.
        trail_us = TRAIL_MAX_US
        off = (pre.get("offset_us") or 0)
        if 0 < off * 2 < trail_us:
            trail_us = float(off * 2)
        rec["trail_us"] = trail_us
        print("    trail window %.0f us (device offset %s us)" % (trail_us, off))

        #
        # PROVE THE DISCRIMINATOR SEPARATES, BEFORE THE FLOOD. The replay
        # carries only 16 distinct 0x051 payloads -- a 4-bit rolling counter
        # in B5 with every other byte constant -- so the firmware emitting a
        # byte pattern the VCM also sends is a live possibility, not a
        # theoretical one. If it does, every "ours" is undercounted and every
        # undercount reads as a withdrawn frame, which is the result this
        # bench is looking for. Check it on the ARM window, where there is no
        # flood and the device's transmit count is known to be healthy.
        #
        arm_frames = wit.slice(0, wit.mark())
        arm_cls = classify(arm_frames, trail_us, replay_payloads)
        arm_tx = armed.get("tx_ok") or 0
        sep = {
            "witnessed_0x051": arm_cls["vcm_frames"] + arm_cls["our_frames"],
            "classed_ours": arm_cls["our_frames"],
            "device_tx_ok": arm_tx,
            "delta": arm_cls["our_frames"] - arm_tx,
            "distinct_replay_payloads": len(replay_payloads),
            "ours_by_trail_timing": arm_cls["ours_by_trail_timing"],
        }
        rec["discriminator_separation"] = sep
        print("    discriminator check: counter-runs say ours %d vs device "
              "tx_ok %d (delta %+d); trail timing would say %d"
              % (sep["classed_ours"], arm_tx, sep["delta"],
                 sep["ours_by_trail_timing"]))
        tol = max(5, int(0.02 * max(arm_tx, 1)))
        if abs(sep["delta"]) > tol:
            rec["verdict"] = "inconclusive_discriminator_not_separating_at_arm"
            print("\nFAIL BEFORE FLOODING: the payload discriminator does not "
                  "separate even with no flood -- counted %d ours against "
                  "the device's own %d transmits, tolerance %d. Flooding now "
                  "would produce skip counts that are wrong in an unknown "
                  "direction and look like a result."
                  % (sep["classed_ours"], arm_tx, tol))
            return 1

        if args.dry_run:
            rec["verdict"] = "dry_run_armed_only"
            print("\nDRY RUN: armed, live, and the discriminator separates. "
                  "No flood sent.")
            return 0

        windows = {}
        for name, secs, fp in (("baseline", args.baseline_seconds, None),
                               ("burst", args.burst_seconds, flood_period),
                               ("recover", args.recover_seconds, None)):
            print("\n[%s] %.0f s%s"
                  % (name, secs, "" if fp is None else
                     "  FLOOD 0x050 period %.0f us" % (fp * 1e6)))
            a = wit.mark()
            t_begin = time.strftime("%Y-%m-%dT%H:%M:%S")
            samples = []
            st = {}
            stop = threading.Event()

            def poll():
                while not stop.is_set():
                    samples.append({"t": round(time.perf_counter(), 3),
                                    "snap": snap(page())})
                    time.sleep(0.5)

            pt = threading.Thread(target=poll, daemon=True)
            pt.start()
            play(pcan, stim, secs, flood_period=fp, stats=st,
                 wall_frames=(args.wall_frames if fp is not None else 0))
            stop.set()
            pt.join(timeout=3.0)
            t_end = time.strftime("%Y-%m-%dT%H:%M:%S")
            b = wit.mark()
            frames = wit.slice(a, b)
            cls = classify(frames, trail_us, replay_payloads)
            #
            # GROUND TRUTH FROM THE OTHER SIDE OF THE WIRE. `tx_ok` is the
            # device's own count of inhibit frames it transmitted, so it says
            # how many of the witnessed 0x051 should have been classed as
            # ours. If the payload discriminator and this disagree, the
            # discriminator is not separating and no skip figure from this
            # window means anything.
            #
            sl = [s["snap"] for s in samples if s.get("snap")]
            txd = None
            if len(sl) >= 2 and sl[0].get("tx_ok") is not None \
                    and sl[-1].get("tx_ok") is not None:
                txd = sl[-1]["tx_ok"] - sl[0]["tx_ok"]
            cls["device_tx_ok_delta"] = txd
            #
            # TRIGGER INTEGRITY. A wall long enough to withdraw an answer is
            # also long enough to delay the NEXT trigger, which is queued
            # behind it. A stretched VCM stream produces withdrawals that are
            # the harness's doing and are indistinguishable from the device's.
            #
            cls["vcm_offered"] = st.get("vcm_offered")
            cls["vcm_shortfall"] = (
                None if st.get("vcm_offered") is None
                else st["vcm_offered"] - cls["vcm_frames"])
            cls["vcm_rate_offered"] = (
                None if not st.get("seconds")
                else round(st["vcm_offered"] / st["seconds"], 1))
            cls["ours_vs_tx_ok"] = (None if txd is None
                                    else cls["our_frames"] - txd)
            byid = collections.Counter(x[1] for x in frames)
            windows[name] = {
                "begin": t_begin, "end": t_end, "seconds_requested": secs,
                "replay": st,
                "witnessed_total": len(frames),
                "witnessed_by_id": {"0x%03X" % i: n
                                    for i, n in sorted(byid.items())},
                "classify": cls,
                "samples": samples,
            }
            fl = byid.get(FLOOD_ID, 0)
            occ = fl * AIR_US / 1e6 / max(st.get("seconds", secs), 1e-9)
            windows[name]["flood_witnessed"] = fl
            windows[name]["flood_occupancy_witnessed"] = round(occ, 4)
            print("    witnessed %d frames; 0x050 %d (%.0f %% of wire); "
                  "0x051 vcm %d ours %d"
                  % (len(frames), fl, occ * 100, cls["vcm_frames"],
                     cls["our_frames"]))
            print("    answered %d  withdrawn %d  late %d"
                  % (cls["answered"], cls["withdrawn"], cls["late"]))
            print("    replay offered %d (fail %d), flood offered %d (fail %d)"
                  % (st.get("replay_sent", 0), st.get("replay_fail", 0),
                     st.get("flood_sent", 0), st.get("flood_fail", 0)))
        rec["windows"] = windows
    finally:
        wit.stop_flag.set()
        wit.join(timeout=3.0)
        for b in (pcan, kv):
            try:
                b.shutdown()
            except Exception:
                pass
        dev_post("/gen_inhibit_set?mode=%d" % MODE_OFF)
        time.sleep(1.0)
        rec["post_after_mode_off"] = snap(page())
        rec["witness_error_frames"] = wit.errors
        print("\nrestored mode 0")

    judge(rec)
    print("wrote %s" % write_record(args.out, rec))
    return 0


def run_out_path(out, stamp):
    """Where this run's record goes. Never a path that already exists.

    WHY. Until 2026-10-07 --out defaulted to the one fixed path
    runs/lowid_flood_skip.json, so every run overwrote the last. Five runs on
    2026-10-06 (21:38-21:45) left one record, and figures quoted from the four
    lost runs then circulated with nothing behind them. The default is now a
    timestamped name, and an explicit --out that already exists is refused
    before any hardware is touched rather than discovered at the end.
    """
    if out is None:
        out = os.path.join(RUNS, "lowid_flood_skip_%s.json" % stamp)
    if os.path.exists(out):
        sys.exit("refusing to overwrite existing run record %s; pass a new "
                 "--out" % out)
    return out


def write_record(out, rec):
    """Write the record with exclusive create, so it can never replace one.

    run_out_path() checked at start-up, but a run lasts minutes and a second
    run could have taken the name meanwhile. Rather than lose either record,
    a clash writes beside it with a numeric suffix. Returns the path written.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    base, ext = os.path.splitext(out)
    path, n = out, 1
    while True:
        try:
            fh = open(path, "x", encoding="ascii", errors="replace")
        except FileExistsError:
            path = "%s_%d%s" % (base, n, ext)
            n += 1
            continue
        with fh:
            json.dump(rec, fh, indent=2, sort_keys=True)
        return path


def judge(rec):
    """Attribute first, then compare. See the docstring."""
    w = rec.get("windows") or {}
    burst = w.get("burst")
    if not burst:
        rec["verdict"] = "inconclusive_no_burst"
        print("\nINCONCLUSIVE: no burst window was recorded.")
        return

    #
    # ATTRIBUTION BEFORE COUNTING. A withdrawn frame during the burst is only
    # a crowding result if the device was still trying to inhibit throughout.
    # Each of these failing produces withdrawals that have nothing to do with
    # 0x050, and the last one is invisible from the wire.
    #
    live = [s["snap"] for s in burst["samples"] if s.get("snap")]
    problems = []
    if any(s.get("mode") != MODE_INHIBIT for s in live):
        problems.append("mode left INHIBIT")
    if any(not s.get("inhibit_live") for s in live):
        problems.append("inhibit_live went false")
    if any(s.get("abort_latched") for s in live):
        reasons = sorted({s.get("abort_reason") for s in live
                          if s.get("abort_latched")})
        problems.append("an abort latched (%s)" % ", ".join(r for r in reasons
                                                            if r))
    if any(s.get("arm_block") for s in live):
        problems.append("arm_block became non-empty")
    states = sorted({s.get("drv_state") for s in live if s.get("drv_state")})
    if states and states != ["running"]:
        problems.append("controller state was %s" % ",".join(states))
    rec["burst_attribution"] = {
        "clean": not problems, "problems": problems,
        "states_seen": states,
        "peak_tec": max((s.get("drv_tec") or 0) for s in live) if live else None,
    }

    cls = burst["classify"]
    base = (w.get("baseline") or {}).get("classify", {})
    dev = {}
    if live:
        first, last = live[0], live[-1]
        for k in ("skips_withdrawn", "skips_late", "sched_skipped", "tx_ok",
                  "tx_fail", "tx_queued_behind", "sched_tx_failed",
                  "sched_tx_refused"):
            a, b = first.get(k), last.get(k)
            dev[k] = None if (a is None or b is None) else (b - a)
    rec["device_deltas_in_burst"] = dev
    rec["summary"] = {
        "baseline_withdrawn": base.get("withdrawn"),
        "burst_withdrawn": cls["withdrawn"],
        "burst_late": cls["late"],
        "burst_answered": cls["answered"],
        "flood_occupancy_witnessed": burst.get("flood_occupancy_witnessed"),
    }

    print("\n" + "=" * 74)
    print("burst: vcm %d, ours %d, answered %d, withdrawn %d, late %d"
          % (cls["vcm_frames"], cls["our_frames"], cls["answered"],
             cls["withdrawn"], cls["late"]))
    print("device deltas in burst: %s"
          % ", ".join("%s=%s" % (k, v) for k, v in sorted(dev.items())))

    if problems:
        rec["verdict"] = "inconclusive_burst_not_attributable"
        print("INCONCLUSIVE: the burst cannot be attributed to crowding -- %s. "
              "Withdrawals from these causes are indistinguishable on the wire "
              "from crowding, so no skip figure is reported."
              % "; ".join(problems))
        return
    short = cls.get("vcm_shortfall")
    off = cls.get("vcm_offered") or 0
    if short is not None and off and abs(short) > max(10, 0.05 * off):
        rec["verdict"] = "inconclusive_trigger_stream_displaced"
        print("INCONCLUSIVE: %d of %d triggers offered in the burst did not "
              "reach the wire inside it (%.1f %%). The harness displaced the "
              "VCM stream -- most likely the adapter queue became a reservoir "
              "-- and a displaced trigger produces withdrawals indistinguishable "
              "from the device's. No skip figure is reported."
              % (short, off, 100.0 * short / off))
        return
    if cls["vcm_frames"] < MIN_VCM_IN_BURST:
        rec["verdict"] = "inconclusive_trigger_stream_damaged"
        print("INCONCLUSIVE: only %d VCM 0x051 frames reached the wire during "
              "the burst (need %d). The flood crowded the TRIGGER, not just "
              "the answer, and a starved trigger produces the same skip count "
              "as a crowded answer." % (cls["vcm_frames"], MIN_VCM_IN_BURST))
        return
    #
    # THE DISCRIMINATOR MUST BE SHOWN TO SEPARATE before any count built on it
    # is reported (AGENTS.md, "A discriminator that cannot fail"). `tx_ok` is
    # the device's own transmit count and is independent of anything the
    # witness or this classifier did.
    #
    gap = cls.get("ours_vs_tx_ok")
    if gap is None:
        rec["verdict"] = "inconclusive_no_ground_truth_for_discriminator"
        print("INCONCLUSIVE: tx_ok could not be read across the burst, so the "
              "payload discriminator has nothing to be checked against.")
        return
    if abs(gap) > max(2, 0.02 * max(cls["our_frames"], 1)):
        rec["verdict"] = "inconclusive_discriminator_not_separating"
        print("INCONCLUSIVE: the payload discriminator classed %d frames as "
              "ours but the device reports transmitting %d (delta %+d). It is "
              "not separating -- most likely the firmware emitted a payload "
              "the replay also sends. Every skip figure below it would be "
              "wrong in an unknown direction, so none is reported."
              % (cls["our_frames"], cls.get("device_tx_ok_delta"), gap))
        return
    if (burst.get("flood_occupancy_witnessed") or 0) < 0.30:
        rec["verdict"] = "inconclusive_flood_too_sparse"
        print("INCONCLUSIVE: the flood reached only %.0f %% of the wire "
              "witnessed. It was not there."
              % ((burst.get("flood_occupancy_witnessed") or 0) * 100))
        return

    wire = cls["withdrawn"]
    devw = dev.get("skips_withdrawn")
    devl = dev.get("skips_late")
    if devw is None:
        rec["verdict"] = "inconclusive_device_counters_unreadable"
        print("INCONCLUSIVE: the device's skip counters could not be read.")
    elif wire == 0 and cls["late"] == 0:
        rec["verdict"] = "no_skips_produced"
        print("NO SKIPS PRODUCED at %.0f %% flood. The scheduler placed every "
              "inhibit frame inside its window. The accounting is untested by "
              "this run -- raise --flood-occupancy."
              % ((burst.get("flood_occupancy_witnessed") or 0) * 100))
    elif devw == wire and (devl is None or devl == cls["late"]):
        rec["verdict"] = "accounting_matches_wire"
        print("ACCOUNTING MATCHES THE WIRE: witness %d withdrawn / %d late, "
              "device %s / %s." % (wire, cls["late"], devw, devl))
    else:
        rec["verdict"] = "accounting_disagrees_with_wire"
        print("ACCOUNTING DISAGREES: witness saw %d withdrawn and %d late; "
              "the device counted %s and %s. One of them is wrong and the "
              "run record has the per-event list."
              % (wire, cls["late"], devw, devl))


if __name__ == "__main__":
    sys.exit(main())
