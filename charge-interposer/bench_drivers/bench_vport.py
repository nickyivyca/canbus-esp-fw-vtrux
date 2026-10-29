"""What the X2's vehicle port can put on the vehicle segment by itself:
spec 9's bench measurements, host side (step 4 prep).

The board runs the vport_bench image (charge-interposer/firmware/test/
vport_bench, a standalone ESP-IDF image that drives the TWAI controller and
nothing else; never on the truck; reviewer, 2026-10-13). This script
commands it over its USB console and judges the wire with its own adapters:

  Kvaser  "the node": a periodic frame (NODE_ID every NODE_PERIOD_S, a
          sequence number in its data), and it ACKs
  PCAN    the witness, LISTEN-ONLY (state=PASSIVE: no ACK, no error
          flags), hardware timestamps; every frame and error frame on the
          segment

WHY THIS WAY ROUND (2026-10-13). The first version had the Kvaser as the
silent witness. The Kvaser Leaf Light v2 cannot be silent: its driver
reports no silent-mode capability, and opened with driver_mode=False it
still ACKs (bench-holder.md, bench fact of 2026-10-09: the WiCAN's TEC
stayed 0 with it open, 128 with nothing open). The PCAN opened PASSIVE did
not ACK (TEC 128). A witness that ACKs makes the board's frames succeed in
step 1 and step 4's no-ACKer variant, which is the very thing those steps
judge. So the PCAN witnesses and the Kvaser is the node, and step 0 proves
the witness silent on the day rather than trusting either fact.

Order (reviewer, 2026-10-13; the listen-only baseline first, since it also
checks 2.3 on hardware):

  0 silence the witness alone on the segment (the node NOT open): the
            port in normal mode sends ONE frame, and STATUS must show
            tec >= 128 -- nobody ACKed it. tec 0 means something on the
            segment acknowledges (the witness, the WiCAN, anything), and
            the whole run is REFUSED, since steps 1 and 4 would then judge
            an ACK the board did not need
  1 listen  the port in listen-only under each of 2-4 below: the witness
            must see no board frame and no error frame, and every node
            frame must get through
  2 bitrate the port at 250k and 1M against the 500k bus, normal mode,
            idle and then flooding: error frames per burst, bursts per
            minute (bus-off recovery on, as the product, B-7c), and the
            share of the node's frames the witness saw
  3 flood   the port flooding 0x7F4 (diagnostic) and 0x649 (a vehicle
            id): the delay added to the node's higher-priority frame,
            from witness timestamps, against a no-flood baseline
  4 stall   STALL n with the node ACKing: exactly n frames, nothing
            beyond; and with nothing ACKing (--no-acker-ready: the WiCAN
            off or passive; the node is CLOSED, since the Kvaser cannot
            stop ACKing): how often each queued frame is retried, and
            still nothing beyond the n
  5 dominant  the MCP2562's permanent-dominant timeout, timed by the board
            itself from TXD held low to RXD going recessive (spec 9 at
            731e9c15, user 2026-10-13; no scope): 20 pulses of up to 5 ms,
            min / median / max; first the can-fail, a 0.5 ms pulse that
            must show RXD following TXD back high; the witness's error
            events a coarse cross-check (one short event per pulse)

A board frame is recognised by content: the image puts its own id in
bytes 4-7 (vport_bench.cpp, fillFrame). The node's frame carries
NODE_MAGIC and a 32-bit sequence number.

TIMING. Steps 3 and 4 judge time from the witness's (the PCAN's) own
timestamps, never the host's. The run REFUSES a step whose timestamps are
coarser than it needs (stamp_tick_ms): 0.01 ms for step 3, 1 ms for the
burst grouping of step 2. MCP2515 path: NO (the vehicle port only).

    py -3.14 bench_vport.py --self-test                 # no hardware
    py -3.14 bench_vport.py --port COM7 --elf <vport_bench.elf>

The record is written verbatim to --out: image identity (the ELF sha256
off the board, checked against --elf, and --src-sha), adapters and their
modes, pins, and each step's numbers and verdict. Tester, 2026-10-13. Not
yet run on hardware.
"""

import argparse
import collections
import datetime
import hashlib
import os
import re
import sys
import struct
import tempfile
import shutil
import threading
import time

# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never from
# the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BUS_BITRATE = 500000
NODE_ID = 0x100                  # above 0x649 and 0x7F4 in priority
NODE_MAGIC = 0x5A
NODE_PERIOD_S = 0.010
FLOOD_IDS = (0x7F4, 0x649)       # diagnostic, vehicle (jam sweep)
STALL_ID = 0x7F0                 # vport_bench's STALL id
STALL_N = 16
SILENCE_ID = 0x7EF               # step 0's one probe frame
BURST_GAP_S = 0.010              # error frames closer than this: one burst
NEAR_GAP_S = 0.002               # step 2: gaps this close to it are counted
BACK_TOL_MS = 2.0                # step 2 only: backward stamp steps tolerated
SETTLE_MAX_S = 20.0              # step 3: the node settles before the baseline
SETTLE_CLEAN_S = 2.0             # ...2 s running with no failed send
FLOOD_SETTLE_S = 1.0             # step 3: flood on this long before scoring
NODE_GAP_MIN_S = 0.015           # step 3: a node gap longer than this...
DRAIN_MAX_S = 0.005              # ...then node frames this close: node-side
CANDIDATE_TICKS_MS = [1.0, 0.1, 0.05, 0.025, 0.01, 0.001]
TICK_MATCH = 0.98
PINS = "TWAI TX GPIO7, RX GPIO6; MCP2562 (STBY has no GPIO)"


# --- classifying the witness's rows ----------------------------------------
# A row is (timestamp_s, arbitration_id, is_error_frame, data bytes).

def board_frame(row):
    """A frame the image sent: its own id in bytes 4-7 (fillFrame)."""
    _t, ident, err, data = row
    return (not err and len(data) == 8
            and int.from_bytes(bytes(data[4:8]), "little") == ident)


def board_counter(row):
    return int.from_bytes(bytes(row[3][0:4]), "little")


def node_payload(seq):
    return bytearray([NODE_MAGIC]) + bytearray(seq.to_bytes(4, "little")) \
        + bytearray(3)


def node_seq(row):
    _t, ident, err, data = row
    if err or ident != NODE_ID or len(data) < 5 or data[0] != NODE_MAGIC:
        return None
    return int.from_bytes(bytes(data[1:5]), "little")


def stamp_tick_ms(ts):
    """The coarsest candidate tick that >= TICK_MATCH of the timestamps are
    integer multiples of, or None if none is (a fine clock)."""
    if not ts:
        return None
    for tick in CANDIDATE_TICKS_MS:
        hits = 0
        for t in ts:
            f = t * 1000.0 / tick
            if abs(f - round(f)) <= 0.02:
                hits += 1
        if hits >= TICK_MATCH * len(ts):
            return tick
    return None


def back_steps(ts):
    """-> (count, largest in ms) of the timestamps running backwards."""
    b = [(a - c) * 1000.0 for a, c in zip(ts, ts[1:]) if c < a]
    return len(b), (max(b) if b else 0.0)


def timing_ok(ts, need_ms, back_tol_ms=0.0):
    """-> (ok, why). Refuse timestamps that cannot resolve need_ms: none,
    running backwards (by more than back_tol_ms per step; 0 = any), or
    quantised coarser than need_ms. back_tol_ms is step 2's only (reviewer,
    2026-10-16): the 00:01 run had 5 and 2 backward steps of ~1 ms in
    windows of 1.8M and 381k rows, a suspected PCAN stamp glitch."""
    if len(ts) < 10:
        return False, "fewer than 10 witness timestamps"
    n, big = back_steps(ts)
    if n and big > back_tol_ms:
        return False, ("%d witness timestamp(s) run backwards, the largest "
                       "%.3f ms (tolerated: %s)" % (
                           n, big, "none" if not back_tol_ms
                           else "%.1f ms each" % back_tol_ms))
    tick = stamp_tick_ms(ts)
    if tick is not None and tick > need_ms:
        return False, ("witness timestamps are quantised to %.3f ms, coarser "
                       "than the %.3f ms this step needs" % (tick, need_ms))
    return True, "witness tick %s ms%s" % (
        "< 0.001" if tick is None else "%.3f" % tick,
        "; %d backward step(s), the largest %.3f ms, tolerated (suspected "
        "PCAN stamp glitch; stamps sorted before grouping)" % (n, big)
        if n else "")


# --- scorers (pure) --------------------------------------------------------

def score_listen(rows, node_sent):
    """Step 1: in listen-only the port puts NOTHING on the bus. -> (ok,
    lines). node_sent: the sequence numbers the node sent in the window."""
    lines, ok = [], True
    board = [r for r in rows if board_frame(r)]
    errs = [r for r in rows if r[2]]
    seen = set(s for s in (node_seq(r) for r in rows) if s is not None)
    missing = sorted(set(node_sent) - seen)
    if board:
        ok = False
        lines.append("FAIL: %d board frame(s) on the bus in listen-only, "
                     "first id 0x%X at %.6f s" % (len(board), board[0][1],
                                                  board[0][0]))
    if errs:
        ok = False
        lines.append("FAIL: %d error frame(s) on the bus in listen-only, "
                     "first at %.6f s" % (len(errs), errs[0][0]))
    if not node_sent:
        ok = False
        lines.append("FAIL: the node sent nothing, so 'every frame got "
                     "through' shows nothing")
    elif missing:
        ok = False
        lines.append("FAIL: %d of %d node frame(s) never reached the "
                     "witness, first seq %d" % (len(missing), len(node_sent),
                                                missing[0]))
    if ok:
        lines.append("no board frame, no error frame; all %d node frames "
                     "seen" % len(node_sent))
    return ok, lines


NOACK_MIN_ROWS = 10   # node frames seen retrying, for the check to mean anything


def score_noack(rows):
    """Step 1's silence claim (reviewer, 2026-10-15): with the witness
    PASSIVE and the node open, nothing but the board could acknowledge the
    node's frames. -> (verdict, lines), verdict one of "unacked", "acked",
    "fault". Unacked: the node's head frame is retried -- many node rows,
    ONE sequence number. Acked: the node got past its head frame -- more
    than one sequence number. Fault: too few node rows to say (the
    population must be non-empty before a negative means anything)."""
    seqs = [node_seq(r) for r in rows]
    seqs = [x for x in seqs if x is not None]
    distinct = sorted(set(seqs))
    board = [r for r in rows if board_frame(r)]
    if len(seqs) < NOACK_MIN_ROWS:
        return "fault", ["TEST FAULT: only %d node frame(s) on the witness -- "
                         "the node's retries were not seen, so an ACK could "
                         "not be ruled in or out" % len(seqs)]
    where = "; %d board frame(s)" % len(board) if board else ""
    if len(distinct) > 1:
        return "acked", ["the node's frames COMPLETED: %d node rows, %d "
                         "sequence numbers (%d..%d)%s"
                         % (len(seqs), len(distinct), distinct[0],
                            distinct[-1], where)]
    return "unacked", ["the node's frames were NOT acknowledged: %d node rows, "
                       "all sequence %d (its head frame, retried)%s"
                       % (len(seqs), distinct[0], where)]


# An arm's verdict is True (PASS), falsy (FAIL), or FAULT: it reported a
# TEST FAULT, so it measured nothing. Reviewer, 2026-10-23 (c40b's
# vport-bitrate): a TEST FAULT arm makes the run VOID unless another arm
# FAILs on a valid measurement, which keeps it FAIL; a fault arm never
# PASSes. combine() is the rule.
FAULT = object()


def verdict_word(ok):
    return ("VOID (TEST FAULT)" if ok is FAULT else
            "PASS" if ok else "FAIL")


def combine(verdicts):
    """[(name, ok)] -> (rc, final line or None). A valid FAIL anywhere ->
    rc 1 and a FAIL line; else any FAULT -> rc 1 and a VOID line (the
    suite's script_judge reads a VOID line with no FAIL line as VOID);
    else rc 0. The lines start in column 0 so the suite's judge finds
    them."""
    faults = [n for n, ok in verdicts if ok is FAULT]
    fails = [n for n, ok in verdicts if ok is not FAULT and not ok]
    if fails:
        return 1, "FAIL: %s%s" % (", ".join(fails), (
            "; TEST FAULT, not measured: %s" % ", ".join(faults))
            if faults else "")
    if faults:
        return 1, ("VOID: TEST FAULT on %s, and no arm FAILs on a valid "
                   "measurement" % ", ".join(faults))
    return 0, None


def refused_arm(why):
    """A timing_ok refusal -> (FAULT, its line). All three refusals (fewer
    than 10 witness stamps, stamps running backwards, quantisation coarser
    than needed) are about the witness's record, never a board measurement
    (reviewer, 2026-10-23), so the arm is a fault for combine()."""
    return FAULT, "REFUSED, not judged (the witness's timing record): %s" % why


def start_refused(why, lines=()):
    """A refusal before any step runs (the board's ELF, its pins, the
    witness not shown silent) -> the lines to print: the refused check's
    own lines marked as not judged, then a VOID line in column 0. The rig
    did not meet a premise, so nothing about the board was measured, and
    no line of it may read as a FAIL to the suite's judge (reviewer,
    2026-10-23)."""
    return (["not judged (the run is refused): " + ln for ln in lines] +
            ["VOID: REFUSED, no step run: %s" % why])


def unjudged(lines):
    """A faulted arm's scorer lines, kept for the record but marked, so no
    line of an arm that measured nothing reads as a FAIL to the suite."""
    return ["not judged (the window faulted): " + ln for ln in lines]


def judge_offpair(w):
    """(iv): one rate's three windows [(rate, state, error_frames,
    faults)], state "off" / "listen" / "off". -> (ok, verdict). ok means
    clean; BOARD-SIDE is errors only with the controller installed
    listen-only and none in either window with it removed; FAULT when a
    window faulted."""
    if len(w) != 3 or [x[1] for x in w] != ["off", "listen", "off"]:
        return False, "NOT JUDGED (the off / listen-only / off windows are " \
                      "not all there)"
    if any(x[3] for x in w):
        return FAULT, "NOT JUDGED (a window faulted)"
    offs = [x[2] for x in w if x[1] == "off"]
    lis = w[1][2]
    if max(offs) == 0 and lis > 0:
        return False, ("BOARD-SIDE: errors only with the board's controller "
                       "installed listen-only at %s (%d), none with it "
                       "removed (%s)" % (w[0][0], lis, offs))
    if max(offs) == 0 and lis == 0:
        return True, "clean in all three windows"
    return False, ("NOT THE BOARD ALONE: errors with its controller removed "
                   "too (off %s, listen-only %d)" % (offs, lis))


RECSTART_REPS = 3          # listen-only starts
RECSTART_FAST_N = 5        # STATUS reads back to back straight after each
RECSTART_QUIET_S = 2.0     # and one more this long after, CAN1 quiet
RECSTART_NODE_AT = (0.03, 0.1, 0.3, 1.0, 3.0, 5.0)  # s into node traffic
RECSTART_CANFAIL_S = 1.0   # normal mode at 250k, the node open


def judge_recstart(can_a, starts, node_reads, can_b, quiet_rows):
    """REC in listen-only, on the REGISTER (rec_reg=), reviewer 2026-10-16.
    The API's rec= is 0 in listen-only by construction (IDF v5.4.1 twai.c,
    twai_get_status_info_v2); TEC is set to 0 by twai_hal_start and is
    recorded, not read.

    can_a: step 0's STATUS (normal mode, one unACKed frame): tec_reg must
    equal tec and rec_reg rec. can_b: normal mode at 250k with the node
    open: rec > 0 and rec_reg == rec. Either failing -> NOT JUDGED.
    starts: one list per listen-only start of (label, status); node_reads:
    [(label, status)] under the node's 500k frames; quiet_rows: witness
    rows over the starts (must be empty).

    Outcomes, declared before the run (reviewer):
      every start reads 128 -> THE WRITE TAKES; then under node traffic
        still 128 -> FROZEN in listen-only;
        non-increasing below 128, first read >= 100 -> NOT FROZEN (good
          receptions walk it down);
        anything else (e.g. 0 at once) -> SOMETHING ELSE: stop;
      every start reads 0 -> THE WRITE NEVER TAKES on this silicon;
      anything else -> NEITHER: stop.
    -> (judged, verdict, lines)."""
    lines = []

    def regs(st):
        return (status_field(st, "rec_reg"), status_field(st, "rec"),
                status_field(st, "tec_reg"), status_field(st, "tec"))

    ra, a_api, ta, ta_api = regs(can_a)
    lines.append("wiring check A (step 0, normal, one unACKed frame): "
                 "tec_reg=%s tec=%s rec_reg=%s rec=%s" % (ta, ta_api, ra,
                                                         a_api))
    rb, b_api, tb, tb_api = regs(can_b)
    lines.append("wiring check B (normal at 250k, node open): rec_reg=%s rec=%s "
                 "tec_reg=%s tec=%s" % (rb, b_api, tb, tb_api))
    if None in (ra, a_api, ta, ta_api) or ta != ta_api or ra != a_api \
            or ta_api < 128:
        return False, "NOT JUDGED (wiring check A: the registers do not match " \
                      "the API in normal mode, or no TEC to compare)", lines
    if None in (rb, b_api) or b_api == 0 or rb != b_api:
        return False, "NOT JUDGED (wiring check B: no REC in normal mode, or " \
                      "rec_reg differs from the API's rec)", lines
    if quiet_rows:
        return False, "NOT JUDGED (CAN1 was not quiet: %d row(s) on the " \
                      "witness during the starts)" % len(quiet_rows), lines
    sv = []
    for k, st_list in enumerate(starts):
        vals = []
        for label, st in st_list:
            r = status_field(st, "rec_reg")
            lines.append("start %d, %-26s rec_reg=%s (API rec=%s, tec_reg=%s "
                         "recorded) mode=%s" % (k + 1, label, r,
                                                status_field(st, "rec"),
                                                status_field(st, "tec_reg"),
                                                hello_field(st, "mode")))
            if r is None or hello_field(st, "mode") != "listen" or \
                    hello_field(st, "installed") != "1":
                return False, ("NOT JUDGED (start %d, %s: no rec_reg, or "
                               "not installed listen-only)" % (k + 1, label)
                               ), lines
            vals.append(r)
        sv.append(vals)
    if not sv or not all(sv):
        return False, "NOT JUDGED (no reads after a start)", lines
    nv = []
    for label, st in node_reads:
        r = status_field(st, "rec_reg")
        lines.append("node traffic, %-20s rec_reg=%s rx_frames=%s"
                     % (label, r, status_field(st, "rx_frames")))
        if r is None or hello_field(st, "mode") != "listen":
            return False, "NOT JUDGED (node read %s)" % label, lines
        nv.append(r)
    flat = [v for vals in sv for v in vals]
    if all(v == 128 for v in flat):
        if nv and all(v == 128 for v in nv):
            return True, "THE WRITE TAKES (128 at every start); FROZEN at " \
                         "128 under node traffic", lines
        if nv and all(v < 128 for v in nv) and nv[0] >= 100 and \
                all(y <= x for x, y in zip(nv, nv[1:])):
            return True, ("THE WRITE TAKES (128 at every start); NOT FROZEN: "
                          "walked down under node traffic %s" % nv), lines
        return True, ("THE WRITE TAKES (128 at every start); SOMETHING ELSE "
                      "under node traffic %s -- STOP" % nv), lines
    if all(v == 0 for v in flat):
        return True, ("THE WRITE NEVER TAKES on this silicon (rec_reg 0 at "
                      "every read after %d starts)" % len(sv)), lines
    return True, "NEITHER: rec_reg after the starts %s -- STOP" % sv, lines


def bursts_of(err_ts, gap=BURST_GAP_S):
    """Error-frame timestamps -> list of bursts (each a list of stamps)."""
    out = []
    for t in sorted(err_ts):
        if out and t - out[-1][-1] < gap:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def score_bitrate(rows, sent_seqs, window_s, node_fails=0):
    """Step 2: a measurement, not a pass/fail on the board -> (metrics,
    lines). Bursts are runs of error frames closer than BURST_GAP_S.
    sent_seqs: the node's sequence numbers queued in THIS window; node_fails:
    its sends refused in this window (queue full). The share is this
    window's seqs seen over the sends it attempted (queued + refused).
    Seqs from earlier windows (the Kvaser's queue draining late) are counted
    apart and never in the share: the 00:01 run counted them and printed
    "397 of 79 (502.5 %)" (fixed 2026-10-16)."""
    errs = [r[0] for r in rows if r[2]]
    b = bursts_of(errs)
    sizes = sorted(len(x) for x in b)
    # gaps between sorted error frames within 2 ms of the grouping threshold:
    # the only groupings a ~1 ms stamp error could change (reviewer,
    # 2026-10-16)
    se = sorted(errs)
    near = sum(1 for a, c in zip(se, se[1:])
               if abs((c - a) - BURST_GAP_S) <= NEAR_GAP_S)
    sent = set(sent_seqs)
    seen_all = set(s for s in (node_seq(r) for r in rows) if s is not None)
    seen = len(seen_all & sent)
    late = len(seen_all - sent)
    tried = len(sent) + node_fails
    m = {"error_frames": len(errs), "bursts": len(b),
         "per_burst_min": sizes[0] if sizes else 0,
         "per_burst_p50": sizes[len(sizes) // 2] if sizes else 0,
         "per_burst_max": sizes[-1] if sizes else 0,
         "bursts_per_min": len(b) * 60.0 / window_s if window_s else 0.0,
         "node_tried": tried, "node_queued": len(sent),
         "node_refused": node_fails, "node_seen": seen, "node_late": late,
         "node_share": seen / float(tried) if tried else None,
         "near_threshold": near}
    lines = ["%d error frame(s) in %d burst(s) over %.0f s: per burst min %d "
             "/ p50 %d / max %d, %.1f bursts per minute; node frames that got "
             "through: %d of the %d it tried to send in the window (%s; %d "
             "queued, %d refused for a full queue); %d frame(s) from earlier "
             "windows' backlog also seen, not counted" % (
                 m["error_frames"], m["bursts"], window_s,
                 m["per_burst_min"], m["per_burst_p50"], m["per_burst_max"],
                 m["bursts_per_min"], seen, tried,
                 "%.1f %%" % (100 * m["node_share"])
                 if m["node_share"] is not None else "n/a",
                 len(sent), node_fails, late)]
    lines.append("error-frame gaps within %.0f ms of the %.0f ms burst "
                 "threshold: %d" % (NEAR_GAP_S * 1000, BURST_GAP_S * 1000,
                                    near))
    return m, lines


def bitrate_flag(n_back, near):
    """Step 2: a window with tolerated backward stamps AND error-frame gaps
    near the burst threshold may have a grouping the glitch changed ->
    a FLAG line, or None (reviewer, 2026-10-16)."""
    if n_back and near:
        return ("FLAG: %d backward stamp step(s) and %d gap(s) within %.0f ms "
                "of the %.0f ms burst threshold -- the burst count may be off "
                "by up to %d" % (n_back, near, NEAR_GAP_S * 1000,
                                 BURST_GAP_S * 1000, min(n_back, near)))
    return None


def settle_node(take_fails, now, sleep, max_s=SETTLE_MAX_S,
                clean_s=SETTLE_CLEAN_S, poll_s=0.1):
    """Step 3, before the baseline (reviewer, 2026-10-16): wait up to max_s
    for clean_s running with no failed node send (the board idle).
    -> (settled, seconds taken). take_fails/now/sleep are injected so the
    self-test can drive it."""
    t0 = now()
    take_fails()
    clean_from = t0
    while now() - t0 < max_s:
        sleep(poll_s)
        if take_fails():
            clean_from = now()
        elif now() - clean_from >= clean_s:
            return True, now() - t0
    return False, now() - t0


def lateness(rows, period=NODE_PERIOD_S):
    """Each node frame's lateness against a least-squares schedule fitted
    on its sequence number: (seq, lateness_s), lateness >= 0 by
    construction (the fit is shifted to the earliest frame)."""
    pts = sorted((s, r[0]) for r in rows
                 for s in [node_seq(r)] if s is not None)
    if len(pts) < 3:
        return []
    n = len(pts)
    mx = sum(s for s, _ in pts) / float(n)
    my = sum(t for _, t in pts) / float(n)
    sxx = sum((s - mx) ** 2 for s, _ in pts)
    slope = (sum((s - mx) * (t - my) for s, t in pts) / sxx) if sxx \
        else period
    res = [(s, t - (my + slope * (s - mx))) for s, t in pts]
    lo = min(r for _s, r in res)
    return [(s, r - lo) for s, r in res]


def split_node_side(rows, period=NODE_PERIOD_S):
    """Step 3 (reviewer, 2026-10-16, (ii)/(iii)): which node frames the WIRE
    shows were late on the node's side. A node frame is node-side only when
    it ends a gap between node frames longer than NODE_GAP_MIN_S that has
    board frames in it (the bus was in use, no node frame presented) AND
    starts a back-to-back drain (the next node frame within DRAIN_MAX_S);
    the rest of that drain is node-side too. 0x100 outranks every flood id,
    so a presented frame waits one frame at most (~0.25 ms), far below
    NODE_GAP_MIN_S: arbitration delay is never classed node-side. A leading
    drain counts from the window's first row. -> (excluded seqs, gaps),
    gaps a list of (after_seq or None, gap_s, board_frames, drain)."""
    import bisect
    rs = sorted(rows, key=lambda r: r[0])
    nf = sorted((r[0], s) for r in rs for s in [node_seq(r)] if s is not None)
    bt = [r[0] for r in rs if board_frame(r)]
    excl, gaps = set(), []
    if not nf:
        return excl, gaps
    starts = [(rs[0][0], None, 0)] + [(nf[i - 1][0], nf[i - 1][1], i)
                                       for i in range(1, len(nf))]
    for t_prev, s_prev, i in starts:
        if i == 0:
            t_prev = rs[0][0]
        g = nf[i][0] - t_prev
        if g <= NODE_GAP_MIN_S:
            continue
        nb = bisect.bisect_left(bt, nf[i][0]) - bisect.bisect_right(bt, t_prev)
        drain = i + 1 < len(nf) and nf[i + 1][0] - nf[i][0] < DRAIN_MAX_S
        gaps.append((s_prev, g, nb, drain))
        if nb > 0 and drain:
            j = i
            excl.add(nf[j][1])
            while j + 1 < len(nf) and nf[j + 1][0] - nf[j][0] < DRAIN_MAX_S:
                j += 1
                excl.add(nf[j][1])
    return excl, gaps


def lateness_split(rows, period=NODE_PERIOD_S):
    """-> (scored [(seq, late)], excluded [(seq, late)], gaps): the schedule
    is fitted on the scored frames only, the excluded ones measured against
    it, all shifted so the earliest scored frame is 0."""
    excl, gaps = split_node_side(rows, period)
    pts = sorted((s, r[0]) for r in rows
                 for s in [node_seq(r)] if s is not None)
    fit = [(s, t) for s, t in pts if s not in excl]
    if len(fit) < 3:
        return [], [], gaps
    n = len(fit)
    mx = sum(s for s, _ in fit) / float(n)
    my = sum(t for _, t in fit) / float(n)
    sxx = sum((s - mx) ** 2 for s, _ in fit)
    slope = (sum((s - mx) * (t - my) for s, t in fit) / sxx) if sxx \
        else period
    res = [(s, t - (my + slope * (s - mx))) for s, t in pts]
    lo = min(r for s, r in res if s not in excl)
    sc = [(s, r - lo) for s, r in res if s not in excl]
    ex = [(s, r - lo) for s, r in res if s in excl]
    return sc, ex, gaps


def score_flood(base_rows, flood_rows, flood_id):
    """Step 3 -> (ok, metrics, lines). ok is about the MEASUREMENT being
    valid (the flood was on the bus and the node's frames were seen in
    both windows); the delay itself is the reported number. Since
    2026-10-16 (reviewer) the delay is scored on the frames the wire does
    not show late on the node's side (split_node_side); the excluded ones
    are reported apart with their count and lateness, never dropped."""
    lines, ok = [], True
    nflood = sum(1 for r in flood_rows if board_frame(r) and r[1] == flood_id)
    sb, eb, gb = lateness_split(base_rows)
    sf, ef, gf = lateness_split(flood_rows)
    lb = sorted(x for _s, x in sb)
    lf = sorted(x for _s, x in sf)
    if nflood < 100:
        ok = False
        lines.append("FAIL: only %d frame(s) of the 0x%X flood on the bus -- "
                     "no flood to measure against" % (nflood, flood_id))
    if not lb or not lf:
        ok = False
        lines.append("FAIL: the node's frame was not seen in %s"
                     % ("either window" if not lb and not lf else
                        "the baseline" if not lb else "the flood window"))
    m = {"flood_frames": nflood, "base_excluded": len(eb),
         "flood_excluded": len(ef)}

    def p(v, q):
        return v[min(len(v) - 1, int(q * len(v)))]
    if lb and lf:
        m.update(base_p50=p(lb, .5), base_p99=p(lb, .99), base_max=lb[-1],
                 flood_p50=p(lf, .5), flood_p99=p(lf, .99), flood_max=lf[-1])
        m["added_max"] = m["flood_max"] - m["base_max"]
        m["added_p99"] = m["flood_p99"] - m["base_p99"]
        lines.append("0x%X flood, %d frames: node 0x%X lateness, scored "
                     "frames (%d baseline, %d flooding): baseline p50 %.1f / "
                     "p99 %.1f / max %.1f us; flooding p50 %.1f / p99 %.1f / "
                     "max %.1f us; ADDED max %.1f us, p99 %.1f us"
                     % (flood_id, nflood, NODE_ID, len(lb), len(lf),
                        m["base_p50"] * 1e6, m["base_p99"] * 1e6,
                        m["base_max"] * 1e6, m["flood_p50"] * 1e6,
                        m["flood_p99"] * 1e6, m["flood_max"] * 1e6,
                        m["added_max"] * 1e6, m["added_p99"] * 1e6))
    for name, ex, gp in (("baseline", eb, gb), ("flooding", ef, gf)):
        le = sorted(x for _s, x in ex)
        q = [g for g in gp if g[2] > 0 and g[3]]
        lines.append(
            "%s: %d node frame(s) EXCLUDED as node-side (the wire shows a gap "
            "over %.0f ms with board frames and no node frame, then a "
            "back-to-back drain)%s; %d such gap(s)%s; %d other gap(s) over "
            "%.0f ms (no board frames or no drain: scored)"
            % (name, len(ex), NODE_GAP_MIN_S * 1000,
               (", their lateness p50 %.1f / max %.1f us"
                % (p(le, .5) * 1e6, le[-1] * 1e6)) if le else "",
               len(q),
               (", longest %.1f ms with %d board frame(s)"
                % (max(g[1] for g in q) * 1000,
                   max(q, key=lambda g: g[1])[2])) if q else "",
               len(gp) - len(q), NODE_GAP_MIN_S * 1000))
    lines.append("expected, not judged: a higher-priority frame waits at most "
                 "one frame already in transmission, about 0.25 ms at 500 "
                 "kbit/s")
    return ok, m, lines


def score_stall(rows, n, acked, stall_id=STALL_ID):
    """Step 4 -> (ok, metrics, lines). A stalled task must leave the
    controller sending nothing beyond the n frames it queued: never more
    than n distinct counters. With an ACKing peer each goes out exactly
    once, so exactly n frames; with nothing ACKing, repeats of a counter
    are the controller's retries and are reported."""
    lines, ok = [], True
    mine = [r for r in rows if board_frame(r) and r[1] == stall_id]
    c = collections.Counter(board_counter(r) for r in mine)
    distinct, total = len(c), len(mine)
    errs = sum(1 for r in rows if r[2])
    m = {"distinct": distinct, "total": total, "retries": total - distinct,
         "error_frames": errs}
    if distinct > n:
        ok = False
        lines.append("FAIL: %d distinct frames after STALL %d -- the "
                     "controller sent beyond what was queued" % (distinct, n))
    if acked:
        if distinct < n:
            ok = False
            lines.append("FAIL: only %d of the %d queued frames reached the "
                         "bus with a peer ACKing" % (distinct, n))
        if total != distinct:
            ok = False
            lines.append("FAIL: %d repeated frame(s) with a peer ACKing"
                         % (total - distinct))
    if ok:
        lines.append("STALL %d, %s: %d distinct frame(s), %d on the bus in "
                     "all (%d retries), %d error frame(s); nothing beyond the "
                     "queued %d" % (n, "peer ACKing" if acked else
                                    "NOTHING ACKing", distinct, total,
                                    total - distinct, errs, n))
    return ok, m, lines


DOM_PULSE_US = 5000        # spec 9 at 731e9c15: release after 5 ms anyway
DOM_REPS = 20
DOM_SHORT_US = 500         # the can-fail: shorter than the expected timeout
DOM_CLUSTER_GAP_S = 0.050  # error frames further apart are separate events
DOM_LONG_S = 0.004         # an event this long reads as "5 ms of errors"


REPLY_SUFFIX_KEYS = ("mode", "bitrate")   # every reply ends with these


def parse_dom(line):
    """'@DOM i=0 pulse_us=5000 rxd_low_us=1 rxd_high_us=1250 ... mode=listen
    bitrate=500k' -> dict of ints, with 'by' a string. The image ends every
    reply with mode= and bitrate=, which are skipped (the first run crashed
    on them, 2026-10-15); any other value that is not an integer raises
    ValueError, which score_dominant reports as a FAIL."""
    d = {}
    for f in line.split()[1:]:
        k, _, v = f.partition("=")
        if k in REPLY_SUFFIX_KEYS:
            continue
        if k == "by":
            d[k] = v
            continue
        try:
            d[k] = int(v)
        except ValueError:
            raise ValueError("@DOM field %s=%s is not an integer" % (k, v))
    return d


def score_dominant(dom_lines, pulse_us, short=False):
    """Step 5 -> (ok, metrics, lines), from the board's own @DOM lines.
    Long pulses (the measurement): every pulse must see RXD go low (the
    transceiver drove the bus) and then high while TXD was still low
    (by=transceiver); min / median / max of that interval. A short pulse
    (the can-fail, `short`): RXD must follow TXD back high AFTER the
    release (by=txd, rxd_high_us >= released_us) -- a capture that
    reported a constant, or the timeout, would fail here."""
    lines, ok = [], True
    try:
        ds = [parse_dom(ln) for ln in dom_lines]
    except ValueError as e:
        return False, {}, ["FAIL: unreadable @DOM line (%s)" % e]
    if not ds:
        return False, {}, ["FAIL: no @DOM line from the board"]
    never_low = [d for d in ds if d["rxd_low_us"] < 0]
    if never_low:
        ok = False
        lines.append("FAIL: RXD never went low on %d of %d pulse(s) -- the "
                     "line is not being driven or not being read"
                     % (len(never_low), len(ds)))
    m = {"pulses": len(ds)}
    if short:
        bad = [d for d in ds if d["by"] != "txd"
               or d["rxd_high_us"] < d["released_us"]]
        if bad:
            ok = False
            lines.append("FAIL: a %d us pulse did not show RXD following TXD "
                         "back high after the release (%s) -- the capture "
                         "does not track the line" % (pulse_us, bad[0]))
        else:
            lag = [d["rxd_high_us"] - d["released_us"] for d in ds]
            m["follow_lag_us"] = max(lag)
            lines.append("can-fail: %d us pulse(s): RXD high %d-%d us after "
                         "TXD released at ~%d us (by=txd), as it must be"
                         % (pulse_us, min(lag), max(lag),
                            ds[0]["released_us"]))
        return ok, m, lines
    xs = sorted(d["rxd_high_us"] for d in ds if d["by"] == "transceiver")
    other = [d for d in ds if d["by"] != "transceiver"]
    if other:
        ok = False
        lines.append("FAIL: %d of %d pulse(s) not released by the transceiver "
                     "within %d us (first: %s)" % (len(other), len(ds),
                                                   pulse_us, other[0]))
    if xs:
        m.update(min_us=xs[0], median_us=xs[len(xs) // 2], max_us=xs[-1],
                 n=len(xs))
        lines.append("MCP2562 permanent-dominant timeout, %d pulse(s): min "
                     "%d us, median %d us, max %d us (TXD low to RXD high, "
                     "the board's own timer)" % (len(xs), xs[0],
                                                 xs[len(xs) // 2], xs[-1]))
    return ok, m, lines


def error_events(rows, gap_s=DOM_CLUSTER_GAP_S):
    """Witness error frames grouped into events (gaps > gap_s apart) ->
    [(start, duration_s, n)]. The coarse cross-check of step 5: each pulse
    should read as one short disturbance, not 5 ms of errors."""
    ts = sorted(r[0] for r in rows if r[2])
    out = []
    for t in ts:
        if out and t - (out[-1][0] + out[-1][1]) <= gap_s:
            s, _dur, n = out[-1]
            out[-1] = (s, t - s, n + 1)
        else:
            out.append((t, 0.0, 1))
    return out


def score_dom_witness(rows, pulses):
    """-> (ok, lines). One event per pulse at most, none of them as long as
    DOM_LONG_S. Fewer events than pulses is recorded, not failed: a
    witness may show a single dominant stretch as one error frame or none."""
    ev = error_events(rows)
    lines = []
    long_ = [e for e in ev if e[1] >= DOM_LONG_S]
    ok = not long_ and len(ev) <= pulses
    if long_:
        lines.append("FAIL: an error event lasting %.1f ms on the witness -- "
                     "reads as the line held, not released"
                     % (long_[0][1] * 1000))
    if len(ev) > pulses:
        lines.append("FAIL: %d error events for %d pulses" % (len(ev),
                                                                pulses))
    lines.append("witness cross-check: %d error event(s) for %d pulse(s), "
                 "longest %.2f ms" % (len(ev), pulses,
                                      max([e[1] for e in ev] or [0]) * 1000))
    return ok, lines


def hello_field(hello, key):
    for f in (hello or "").split():
        if f.startswith(key + "="):
            return f.split("=", 1)[1]
    return None


def status_field(status, key):
    """An integer field of a vport_bench @STATUS line, or None."""
    v = hello_field(status, key)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


SILENT_TEC = 128     # one unacked frame drives TEC to error passive


def score_silence(sent, status):
    """Step 0: the witness alone on the segment, the port normal, ONE frame
    sent. sent: the @SENT reply; status: the @STATUS reply after it.
    Nobody ACKing shows as tec >= SILENT_TEC (the frame retried into error
    passive). tec below it means something acknowledged."""
    lines = []
    if not sent or "queued=1" not in sent:
        lines.append("FAIL: the board did not queue the probe frame "
                     "(%s), so silence was not tested" % (sent or "no reply"))
        return False, lines
    tec = status_field(status, "tec")
    if tec is None:
        lines.append("FAIL: no tec in the board's STATUS (%s)"
                     % (status or "no reply"))
        return False, lines
    if tec < SILENT_TEC:
        lines.append("FAIL: tec %d after one frame with only the witness "
                     "open: something on the segment ACKs it (the witness, "
                     "the WiCAN, another node) -- the run is REFUSED" % tec)
        return False, lines
    lines.append("witness silent: tec %d after one frame with only the "
                 "witness open, so nobody acknowledged it" % tec)
    return True, lines


# --- hardware ---------------------------------------------------------------

WITNESS_CUT_S = 10.0    # a window's cut must be answered within this
_ROW = struct.Struct("<dIBB8s")   # device stamp, id, error frame, len, data


# PCAN-Basic statuses the reader classifies itself (2026-10-25; the values
# are python-can 4.6.1's can.interfaces.pcan.basic, checked in the
# self-test when python-can is importable).
PCAN_OK = 0x0
PCAN_OVERRUN = 0x2          # the CAN controller was read too late
PCAN_BUSLIGHT = 0x4
PCAN_BUSHEAVY = 0x8
PCAN_QRCVEMPTY = 0x20
PCAN_QOVERRUN = 0x40        # the receive queue was read too late
PCAN_ILLDATA = 0x20000
PCAN_MSG_ERRFRAME = 0x40
WITNESS_POLL_ROWS = 256     # rows read between two looks at the command pipe


def classify_read(result):
    """One PCAN-Basic Read status -> "row", "empty", "warning", "ignored" or
    "failure". python-can 4.6.1's _recv_internal order, with one change
    (2026-10-25): an overrun bit (OVERRUN, QOVERRUN) is a failure whatever
    else is set. python-can tests BUSLIGHT / BUSHEAVY first, so a queue
    overrun reported with a bus-heavy bit was only logged as a warning and
    its message dropped: rows lost with nothing counted."""
    if result == PCAN_OK:
        return "row"
    if result & (PCAN_OVERRUN | PCAN_QOVERRUN):
        return "failure"
    if result == PCAN_QRCVEMPTY:
        return "empty"
    if result & (PCAN_BUSLIGHT | PCAN_BUSHEAVY):
        return "warning"
    if result == PCAN_ILLDATA:
        return "ignored"
    return "failure"


class _PcanReader(object):
    """The witness's reads, straight from PCAN-Basic on the channel
    python-can opened (bit rate, state, error frames and the receive event
    are python-can's). Rows are decoded exactly as python-can 4.6.1 decodes
    a classic-CAN message, so the measurement is unchanged; what changes is
    the classification (classify_read) and the cost per row: no Message
    object and no BusABC.recv per frame (2026-10-25)."""

    def __init__(self, basic, handle, wait, text, epoch, close, status,
                 mode="classic"):
        self.basic, self.handle, self.wait = basic, handle, wait
        self.text, self.epoch = text, epoch
        self.close, self.status = close, status
        self.mode = mode
        self.t_read = self.t_wait = 0.0   # time in Read and in the wait

    def read(self, timeout):
        """-> (kind, row) for a row, (kind, text) for a warning or a
        failure, (kind, None) otherwise; on an empty queue it waits up to
        timeout s for the receive event."""
        c0 = time.perf_counter()
        result, msg, ts = self.basic.Read(self.handle)
        c1 = time.perf_counter()
        self.t_read += c1 - c0
        r = int(result)
        kind = classify_read(r)
        if kind == "row":
            t = self.epoch + (
                (ts.micros + 1000 * ts.millis
                 + 0x100000000 * 1000 * ts.millis_overflow)
                / (1000.0 * 1000.0))
            if self.mode == "fast":
                # the same bytes: DATA is 8 long, so [:LEN] stops at 8
                d = bytes(msg.DATA)[:msg.LEN]
            else:
                d = bytes(msg.DATA[:msg.LEN])[:8]
            return kind, (t, msg.ID,
                          1 if msg.MSGTYPE & PCAN_MSG_ERRFRAME else 0, d)
        if kind == "empty":
            self.wait(timeout)
            self.t_wait += time.perf_counter() - c1
            return kind, None
        if kind == "ignored":
            return kind, None
        return kind, "PCAN read status 0x%X: %s" % (
            r, " / ".join(str(self.text(r)).splitlines()))


class _DirectRead(object):
    """The fast reader's Read (user ruling 2026-10-25, raise the reader's
    rate): PCAN-Basic's CAN_Read on one TPCANMsg and one TPCANTimestamp
    kept for the process, where python-can's PCANBasic.Read makes two new
    structures and a TPCANStatus per call. Returns the same (status, msg,
    timestamp); the structures are overwritten by the next call, so a row
    is decoded before the next read."""

    def __init__(self, dll, msg, ts):
        import ctypes
        self.call = dll.CAN_Read
        self.msg, self.ts = msg, ts
        self.pm, self.pt = ctypes.byref(msg), ctypes.byref(ts)

    def Read(self, handle):
        return self.call(handle, self.pm, self.pt), self.msg, self.ts


# --witness-reader: "fast" or "classic". fast by default since the
# reader-rate run fd23 (2026-10-25): classic, python-can's Read, fell
# 1.9-2.5 s behind in the 250k floods and its queue overran; fast kept
# every window within 8.8 ms
WITNESS_READER = "fast"
READER_MODES = ("fast", "classic")


def _open_witness_reader(acks, spec=None, mode="classic"):
    """The PCAN (never the Kvaser: it cannot be silent), LISTEN-ONLY
    (state=PASSIVE: no ACK, no error flags) or ACKING (state=ACTIVE), as a
    _PcanReader. spec: the self-test's fake PCAN-Basic instead. mode
    "fast": _DirectRead and the cheaper decode; "classic": python-can's
    PCANBasic.Read, the reader as run before 2026-10-25's rate fix."""
    if spec is not None:
        fb = _FakeBasic(*spec)
        return _PcanReader(fb, 0, fb.wait, fb.text, 0.0, lambda: None,
                           lambda: "fake bus OK", mode)
    import can
    from can.interfaces.pcan import pcan as pc
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                  bitrate=BUS_BITRATE,
                  state=(can.BusState.ACTIVE if acks
                         else can.BusState.PASSIVE))
    if getattr(bus, "_can_protocol", None) is can.CanProtocol.CAN_FD:
        bus.shutdown()
        raise RuntimeError("the witness opened as CAN FD: its reads are "
                           "classic-CAN only")
    if pc.HAS_EVENTS and pc.IS_WINDOWS:
        ev = bus._recv_event

        def wait(timeout):
            pc.WaitForSingleObject(ev, int(timeout * 1000))
    else:
        def wait(timeout):
            time.sleep(0.001)
    basic = bus.m_objPCANBasic
    if mode == "fast":
        from can.interfaces.pcan import basic as pb
        basic = _DirectRead(basic._PCANBasic__m_dllBasic, pb.TPCANMsg(),
                            pb.TPCANTimestamp())
    return _PcanReader(basic, bus.m_PcanHandle, wait,
                       bus._get_formatted_error, pc.boottimeEpoch,
                       bus.shutdown, bus.status_string, mode)


def reader_stats(n, reads, t_read, t_wait, wall, lag_min, lag_max, lag_end):
    """One window's reader figures, as the reader process forwards them
    with its cut: rows, Read calls, seconds in Read and in the wait for
    the receive event, the window's wall time, and the read time less the
    stamp (lag, s) of its rows: least, most, and the last row's."""
    return {"rows": n, "reads": reads, "t_read": t_read, "t_wait": t_wait,
            "wall": wall, "lag_min": lag_min, "lag_max": lag_max,
            "lag_end": lag_end}


def _witness_proc(acks, spec, folder, conn, mode="classic"):
    """The witness's reader in its own process at ABOVE_NORMAL (F2,
    reviewer 2026-10-23): it only drains the PCAN and writes compact rows
    to one file per window, so the bench process's work (the last window's
    rows, scoring, SeaDrive writes) shares no GIL or heap with it. Each
    receive failure is kept by its text (F1); the PCAN's bus-state warnings
    are counted here, where the PCAN is, and forwarded (2026-10-25: since F2
    the bench process's logging handler saw none). Commands on conn:
    ("cut",) closes this window's file and answers ("cut", k, rows, kinds,
    failures, warnings, last warning, reader_stats); ("status",) answers
    the PCAN's status string; ("stop",). mode: _open_witness_reader's; the
    fast mode also looks at the command pipe only every WITNESS_POLL_ROWS
    rows or on an empty queue, where the classic one looks before every
    read (2026-10-25)."""
    prio = above_normal()
    try:
        reader = _open_witness_reader(acks, spec, mode)
    except Exception as exc:                               # noqa: BLE001
        conn.send(("error", fail_text(exc)))
        return
    conn.send(("ready", os.getpid(), prio))
    k, n, kinds, fails, warns, wlast = 0, 0, {}, 0, 0, ""
    f = open(os.path.join(folder, "w%06d.bin" % k), "wb", 1 << 20)
    since = 0
    fast = mode == "fast"
    now = time.perf_counter
    w0, reads, lag_min, lag_max, lag = now(), 0, None, None, None
    try:
        while True:
            if since >= WITNESS_POLL_ROWS or (not fast and conn.poll()):
                since = 0
                if conn.poll():
                    cmd = conn.recv()
                    if cmd[0] == "cut":
                        f.close()
                        st = reader_stats(n, reads, reader.t_read,
                                          reader.t_wait, now() - w0, lag_min,
                                          lag_max, lag)
                        conn.send(("cut", k, n, kinds, fails, warns, wlast, st))
                        k, n, kinds, fails, warns, wlast = (k + 1, 0, {}, 0,
                                                            0, "")
                        w0, reads, lag_min, lag_max, lag = (now(), 0, None,
                                                            None, None)
                        reader.t_read = reader.t_wait = 0.0
                        f = open(os.path.join(folder, "w%06d.bin" % k),
                                 "wb", 1 << 20)
                    elif cmd[0] == "status":
                        try:
                            st = reader.status()
                        except Exception as exc:           # noqa: BLE001
                            st = "status unreadable (%s)" % exc
                        conn.send(("status", st))
                    elif cmd[0] == "stop":
                        break
            reads += 1
            try:
                kind, v = reader.read(0.02)
            except Exception as exc:                       # noqa: BLE001
                kind, v = "failure", fail_text(exc)
            if kind == "row":
                t, ident, err, d = v
                f.write(_ROW.pack(t, ident, err, len(d), d))
                lag = now() - t
                if lag_max is None or lag > lag_max:
                    lag_max = lag
                if lag_min is None or lag < lag_min:
                    lag_min = lag
                n += 1
                since += 1
            elif kind == "failure":
                note_failure(kinds, v, time.perf_counter())
                fails += 1
                since = WITNESS_POLL_ROWS
            elif kind == "warning":
                warns, wlast = warns + 1, v
            else:
                since = WITNESS_POLL_ROWS
    finally:
        f.close()
        reader.close()


def _read_window(path):
    """A window file -> [(stamp, id, error frame, data)]."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return []
    out = []
    for off in range(0, len(raw) - len(raw) % _ROW.size, _ROW.size):
        t, ident, err, ln, data = _ROW.unpack_from(raw, off)
        out.append((t, ident, bool(err), data[:ln]))
    return out


class _FakePcanMsg(object):
    """A TPCANMsg's fields as the reader reads them."""

    def __init__(self, ident, msgtype, data):
        self.ID, self.MSGTYPE = ident, msgtype
        self.LEN, self.DATA = len(data), list(data) + [0] * (8 - len(data))


class _FakePcanTs(object):
    """A TPCANTimestamp's fields: t s as millis and micros."""

    def __init__(self, t):
        us = int(round(t * 1000000))
        self.millis_overflow, self.millis, self.micros = 0, us // 1000, us % 1000


_FAKE_TEXT = {PCAN_OVERRUN: "The CAN controller was read too late",
              PCAN_BUSLIGHT: "Bus error: an error counter reached the "
                             "'light' limit",
              PCAN_BUSHEAVY: "Bus error: an error counter reached the "
                             "'heavy' limit",
              PCAN_QOVERRUN: "The receive queue was read too late",
              0x40000: "Bus error: the CAN controller is error passive"}


class _FakeBasic(object):
    """The self-test's PCAN-Basic: Read gives node rows seq 1..n at up to
    rate per second; at each index in statuses ((index, status), ...) that
    status instead of a row (the frame is lost, as python-can loses it); at
    die_at the process exits (os._exit(3))."""

    def __init__(self, n, statuses=(), die_at=None, rate=0.0, clock=False,
                 delay=0.0, skew=0.0):
        self.n, self.statuses = n, dict(statuses)
        self.die_at, self.rate = die_at, rate
        # clock: row k is stamped when it "arrived", t0 + k / rate, on this
        # clock (plus skew); delay: each Read takes this long (a slow reader)
        self.clock, self.delay, self.skew = clock, delay, skew
        self.k, self.t0 = 0, time.perf_counter()

    def Read(self, _handle):
        if self.delay:
            end = time.perf_counter() + self.delay
            while time.perf_counter() < end:
                pass
        if self.k >= self.n or (self.rate and time.perf_counter() - self.t0
                                < self.k / self.rate):
            return PCAN_QRCVEMPTY, None, None
        self.k += 1
        if self.die_at == self.k:
            os._exit(3)
        if self.k in self.statuses:
            return self.statuses[self.k], None, None
        t = (self.t0 + self.k / self.rate + self.skew if self.clock
             else float(self.k))
        return (PCAN_OK, _FakePcanMsg(NODE_ID, 0, node_payload(self.k)),
                _FakePcanTs(t))

    def wait(self, timeout):
        time.sleep(min(timeout, 0.0005) if self.k < self.n else timeout)

    @staticmethod
    def text(code):
        bits = [b for b in sorted(_FAKE_TEXT) if code & b]
        return "\n".join(_FAKE_TEXT[b] for b in bits) or "unknown 0x%X" % code


def _witness_root():
    d = os.path.join(tempfile.gettempdir(), "interposer-tester", "witness")
    os.makedirs(d, exist_ok=True)
    return d


class Witness(object):
    """The PCAN, collecting every row with its device stamp. Opened
    LISTEN-ONLY (state=PASSIVE: no ACK, no error flags) for steps 0, 4, 5
    and step 1's no-ACK check; opened ACKING (state=ACTIVE, at the bus's
    500 kbit/s) for steps 1-3's windows, so the node's frames complete
    whatever the board does (reviewer, 2026-10-15: the first run had
    nothing acknowledging the node). Not the Kvaser: it cannot be silent
    (module docstring). Since 2026-10-23 (F2) the reader runs in its own
    process (_witness_proc); take() cuts its window file and reads it. A
    reader that dies or stops answering faults the window (counted as a
    receive failure with its own text), never a silently short window."""

    def __init__(self, acks=False, spec=None, mode=None):
        import multiprocessing as mp
        self.acks = acks
        self.mode = mode or WITNESS_READER
        self.stats = None           # the last take's reader_stats
        self.folder = tempfile.mkdtemp(prefix="w", dir=_witness_root())
        ctx = mp.get_context("spawn")
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_witness_proc, args=(
            acks, spec, self.folder, child, self.mode), daemon=True)
        self.proc.start()
        self.lock = threading.Lock()
        self.overflows = 0          # receive failures since the last take
        self.fail_kinds = {}        # text -> [n, first, last host s]
        self.warns, self.warn_last = 0, ""   # PCAN bus-state warnings
        self.k, self.dead = 0, None
        if not self.conn.poll(30):
            raise RuntimeError("the witness process did not start")
        r = self.conn.recv()
        if r[0] != "ready":
            raise RuntimeError("the witness process could not open the "
                               "bus: %s" % (r[1],))
        self.pid, self.prio = r[1], r[2]

    def _gone(self, why):
        if self.dead is None:
            self.dead = why
        with self.lock:
            self.overflows += 1
        note_failure(self.fail_kinds, "the witness reader process %s"
                     % self.dead, time.perf_counter(), self.lock)

    def take(self):
        """-> the rows since the last take (this window's file)."""
        self.stats = None
        if self.dead is None:
            r = None
            try:
                self.conn.send(("cut",))
                end = time.perf_counter() + WITNESS_CUT_S
                while not self.conn.poll(0.1):
                    if (not self.proc.is_alive()
                            or time.perf_counter() > end):
                        break
                else:
                    r = self.conn.recv()
            except (OSError, EOFError):
                r = None
            if r is not None and r[0] == "cut" and r[1] == self.k:
                _c, k, n, kinds, fails, warns, wlast = r[:7]
                self.stats = r[7] if len(r) > 7 else None
                rows = _read_window(os.path.join(self.folder,
                                                 "w%06d.bin" % k))
                self._drop(k)
                self.k += 1
                with self.lock:
                    self.overflows += fails
                    self.warns += warns
                    if warns:
                        self.warn_last = wlast
                    for text, (c, first, last) in kinds.items():
                        a = self.fail_kinds.get(text)
                        if a is None:
                            self.fail_kinds[text] = [c, first, last]
                        else:
                            a[0] += c
                            a[1], a[2] = min(a[1], first), max(a[2], last)
                if len(rows) != n:
                    self._gone("wrote %d of the %d rows it read" % (
                        len(rows), n))
                return rows
            self.proc.join(1.0)
            self._gone("died or stopped answering (exit code %s)"
                       % getattr(self.proc, "exitcode", None))
        else:
            self._gone(self.dead)
        rows = _read_window(os.path.join(self.folder, "w%06d.bin" % self.k))
        self._drop(self.k)
        return rows

    def _drop(self, k):
        try:
            os.remove(os.path.join(self.folder, "w%06d.bin" % k))
        except OSError:
            pass

    def status_string(self):
        """The PCAN's own status string, from the reader process."""
        if self.dead is not None:
            return "status unreadable (the witness reader process %s)" % (
                self.dead)
        try:
            self.conn.send(("status",))
            if self.conn.poll(WITNESS_CUT_S):
                r = self.conn.recv()
                if r[0] == "status":
                    return r[1]
        except (OSError, EOFError):
            pass
        return "status unreadable (no answer from the witness reader)"

    def take_incomplete(self):
        """-> the receive errors since the last call (0: the window's rows
        are complete as far as the witness knows)."""
        with self.lock:
            n, self.overflows = self.overflows, 0
        return n

    def take_failures(self):
        """-> {text: [n, first, last host s]} since the last call."""
        with self.lock:
            k, self.fail_kinds = self.fail_kinds, {}
        return k

    def take_warnings(self):
        """-> (n, last text) of the PCAN's bus-state warnings since the
        last call, counted by the reader process."""
        with self.lock:
            n, last = self.warns, self.warn_last
            self.warns, self.warn_last = 0, ""
        return n, last

    def close(self):
        try:
            self.conn.send(("stop",))
        except (OSError, EOFError):
            pass
        self.proc.join(3.0)
        if self.proc.is_alive() and hasattr(self.proc, "terminate"):
            self.proc.terminate()
            self.proc.join(2.0)
        shutil.rmtree(self.folder, ignore_errors=True)

    def describe(self):
        return ("PCAN at %d bit/s, %s reader, %s" % (
            BUS_BITRATE, self.mode, "NORMAL mode, ACKNOWLEDGING (the node's frames "
            "complete on its ACK, not the board's)" if self.acks else
            "LISTEN-ONLY (state=PASSIVE: no ACK, no error flags)"))


def set_witness(wit, acks):
    """-> a witness in the asked mode, reopening only if it differs."""
    if wit is not None and wit.acks == acks:
        return wit
    if wit is not None:
        wit.close()
    w = Witness(acks=acks)
    print("  witness now: %s" % w.describe())
    return w


def above_normal():
    """This process to ABOVE_NORMAL on Windows (bench_hold12's helper),
    read back; -> the class or None."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.GetPriorityClass.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k32.OpenProcess(0x0200 | 0x0400, False, os.getpid())
    if not h:
        return None
    try:
        k32.SetPriorityClass(h, 0x8000)
        return int(k32.GetPriorityClass(h))
    finally:
        k32.CloseHandle(h)


def node_loop(send, now, sleep, put, stop, period=NODE_PERIOD_S):
    """The node's pacing loop, pure (the self-test drives it): every period
    send(seq); put((seq, lag_s, ok, t_call, t_back)) where lag_s is the send
    call's time against the schedule t0 + seq * period on the sender's own
    clock -- a host-side stall shows here, whatever the wire shows. A send
    that raises is ok=0 (the Kvaser's queue full). t_call and t_back: the
    clock when the send was called and when it returned (2026-10-25, the
    window-edge diagnostic: perf_counter is one clock for every process on
    Windows, so they compare with the bench process's window cuts)."""
    t0 = now()
    seq = 0
    while not stop():
        tc = now()
        try:
            send(seq)
            ok = 1
        except Exception:                                  # noqa: BLE001
            ok = 0
        put((seq, tc - (t0 + seq * period), ok, tc, now()))
        seq += 1
        sleep(max(0.0, t0 + seq * period - now()))


def _node_proc(channel, q, stop_ev, ready_ev):
    """The node in its own process at ABOVE_NORMAL (reviewer, 2026-10-16,
    (i)): its pacing is no longer shared with the witness's GIL (the
    witness reader has its own process too since 2026-10-23, F2)."""
    import can
    q.put(("prio", above_normal()))
    bus = can.Bus(interface="kvaser", channel=channel, bitrate=BUS_BITRATE,
                  driver_mode=True, receive_own_messages=False)
    ready_ev.set()

    def send(seq):
        bus.send(can.Message(arbitration_id=NODE_ID, is_extended_id=False,
                             data=node_payload(seq)), timeout=0.05)
    try:
        node_loop(send, time.perf_counter, time.sleep, q.put,
                  stop_ev.is_set)
    finally:
        bus.shutdown()
        q.put(None)


class Node(object):
    """The Kvaser, normal mode: another node sending NODE_ID every
    NODE_PERIOD_S, and ACKing. It cannot be made silent, so 'no ACKer'
    means closing it (NoNode). Since 2026-10-16 it runs in its own process
    at ABOVE_NORMAL; each send's host-side lag is logged (take_log)."""

    def __init__(self, channel):
        import multiprocessing as mp
        self.channel = channel
        ctx = mp.get_context("spawn")
        self.q = ctx.Queue()
        self.stop_ev = ctx.Event()
        self.ready_ev = ctx.Event()
        self.p = ctx.Process(target=_node_proc, args=(
            channel, self.q, self.stop_ev, self.ready_ev), daemon=True)
        self.sent, self.fails, self.log = [], 0, []
        self.prio = None
        self.lock = threading.Lock()
        self.t = None

    def start(self):
        self.p.start()
        if not self.ready_ev.wait(30):
            raise RuntimeError("the node process did not open the Kvaser")
        self.t = threading.Thread(target=self._drain, daemon=True)
        self.t.start()
        return self

    def _drain(self):
        while True:
            try:
                x = self.q.get(timeout=0.5)
            except Exception:                              # noqa: BLE001
                if not self.p.is_alive():
                    return
                continue
            if x is None:
                return
            if x[0] == "prio":
                self.prio = x[1]
                continue
            with self.lock:
                self.log.append(x)
                if x[2]:
                    self.sent.append(x[0])
                else:
                    self.fails += 1

    def take(self):
        with self.lock:
            s, self.sent = self.sent, []
        return s

    def take_fails(self):
        with self.lock:
            f, self.fails = self.fails, 0
        return f

    def take_log(self):
        with self.lock:
            g, self.log = self.log, []
        return g

    def close(self):
        self.stop_ev.set()
        self.p.join(timeout=5.0)
        if self.t:
            self.t.join(timeout=2.0)
        if self.p.is_alive():
            self.p.terminate()


class NoNode(object):
    """No node on the segment: the Kvaser closed."""

    def take(self):
        return []

    def take_fails(self):
        return 0

    def take_log(self):
        return []

    def close(self):
        pass


# Each command's own reply (vport_bench.cpp). Board.cmd waits for that tag
# (or @ERR); any other @ line meanwhile is a stale reply, recorded in
# Board.stale and never taken (2026-10-15: cmd used to take the first @
# line, so one late reply would shift every pairing after it).
REPLY_TAG = {"HELLO?": "@HELLO", "MODE": "@MODE", "BITRATE": "@BITRATE",
             "SEND": "@SENT", "FLOOD": "@FLOODING", "STOP": "@STOPPED",
             "STALL": "@STALLED", "DOMINANT": "@DOMINANT",
             "STATUS": "@STATUS", "RESET": "@RESET",
             "AUTORECOVER": "@AUTORECOVER", "DRIVER": "@DRIVER"}


def reply_tag(line):
    return REPLY_TAG.get(line.split()[0] if line.split() else "", "@")


class Board(object):
    def __init__(self, port, ser=None):
        if ser is None:
            import serial
            ser = serial.Serial(port, 115200, timeout=0.2)
        self.ser = ser
        self.stale = []

    def cmd(self, line, want=None, timeout=10.0):
        want = want or reply_tag(line)
        self.ser.write((line + "\n").encode("ascii"))
        self.ser.flush()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            b = self.ser.readline()
            if not b:
                continue
            ln = b.decode("ascii", "replace").rstrip("\r\n")
            if ln.startswith(want) or ln.startswith("@ERR"):
                print("    board> %s" % ln)
                return ln
            if ln.startswith("@"):
                self.stale.append((line, ln))
                print("    board> STALE (not the reply to %s): %s" % (line, ln))
        print("    board> (no reply to %s)" % line)
        return None

    def cmd_lines(self, line, final, collect, timeout=30.0):
        """Send `line`; return (every reply starting `collect`, the reply
        starting `final`, or None on timeout)."""
        self.ser.write((line + "\n").encode("ascii"))
        self.ser.flush()
        got = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            b = self.ser.readline()
            if not b:
                continue
            ln = b.decode("ascii", "replace").rstrip("\r\n")
            if ln.startswith(collect):
                got.append(ln)
            elif ln.startswith(final) or ln.startswith("@ERR"):
                print("    board> %s" % ln)
                return got, ln
        print("    board> (no %s reply to %s)" % (final, line))
        return got, None

    def close(self):
        self.ser.close()


def fail_text(exc):
    """A receive failure's kind: its class and message, one line."""
    return ("%s: %s" % (type(exc).__name__, exc)).replace("\n", " ")[:160]


def note_failure(kinds, text, t, lock=None):
    """Count one failure of this text at host time t in kinds
    ({text: [n, first, last]})."""
    if lock is not None:
        with lock:
            return note_failure(kinds, text, t)
    k = kinds.get(text)
    if k is None:
        kinds[text] = [1, t, t]
    else:
        k[0] += 1
        k[2] = t


def failure_lines(kinds, t0):
    """-> one line per failure text, most frequent first, its times from
    the window's start t0 (host s)."""
    out = []
    for text, (n, first, last) in sorted(kinds.items(),
                                         key=lambda kv: -kv[1][0]):
        out.append("witness receive failure x%d: %s (first +%.3f s, last "
                   "+%.3f s)" % (n, text, first - t0, last - t0))
    return out


BITRATE_RATES = ("250k", "1M")


def parse_bitrate_order(text):
    """--bitrate-order: the step 2 rates in the order to run them, each of
    BITRATE_RATES once (diagnostic, reviewer 2026-10-23)."""
    got = [x.strip() for x in text.split(",") if x.strip()]
    if sorted(got) != sorted(BITRATE_RATES):
        raise ValueError("--bitrate-order: each of %s exactly once, not %r"
                         % (", ".join(BITRATE_RATES), text))
    return got


# The m1 DIAGNOSTIC step (reviewer, 2026-10-25, the user's ruling on item
# (c)): a big 250k flood, then a 1M idle window, by arm: a settle after
# BITRATE 1M or none (H1), and the flood window's rows written to the run
# folder (SeaDrive) or to a local temp folder (H2). In no default --steps;
# judges nothing; the judged step 2 is unchanged.
M1_ARMS = ("nosettle-run", "settle-run", "nosettle-local", "settle-local")


def parse_m1_arms(text):
    """--m1-arms: a comma list of M1_ARMS, run in turn -> [(name, settle,
    local)]."""
    got = [x.strip() for x in text.split(",") if x.strip()]
    bad = [x for x in got if x not in M1_ARMS]
    if bad or not got:
        raise ValueError("--m1-arms: a comma list of %s, not %r"
                         % (", ".join(M1_ARMS), text))
    return [(x, x.startswith("settle"), x.endswith("local")) for x in got]


def m1_idle_metrics(rows, head_s=1.25, bin_s=0.5):
    """A 1M idle window's witness rows -> dict: rows, rows/s, the rows/s in
    its first head_s, the largest device-time gap (ms) and where (s), and
    the bin_s bins under a tenth of the median bin (c40b/9911's faulted
    windows: a ~1.25 s burst, then ~5.5 s near-empty). Stamps sorted first
    (file order is not time order)."""
    ts = sorted(r[0] for r in rows)
    out = {"rows": len(ts), "rate": 0.0, "head_rate": 0.0, "gap_ms": 0.0,
           "gap_at": 0.0, "low_bins": 0}
    if len(ts) < 2:
        return out
    t0, span = ts[0], ts[-1] - ts[0]
    out["rate"] = len(ts) / span if span else 0.0
    out["head_rate"] = sum(1 for t in ts if t - t0 < head_s) / head_s
    g, i = max((ts[k + 1] - ts[k], k) for k in range(len(ts) - 1))
    out["gap_ms"], out["gap_at"] = g * 1000, ts[i] - t0
    bins = [0] * (int(span / bin_s) + 1)
    for t in ts:
        bins[int((t - t0) / bin_s)] += 1
    full = sorted(bins[:-1]) or bins
    med = full[len(full) // 2]
    out["low_bins"] = sum(1 for c in bins[:-1] if c < med / 10.0)
    return out


def cpu_busy_pct(prev, cur):
    """System CPU busy %% between two (idle, kernel, user) samples of
    GetSystemTimes (kernel includes idle) -> float, or None."""
    di, dk, du = (cur[0] - prev[0], cur[1] - prev[1], cur[2] - prev[2])
    tot = dk + du
    if tot <= 0:
        return None
    return 100.0 * (tot - di) / tot


def host_summary(samples, t0, t1):
    """samples: [(host s, cpu %% or None, memory load %%, working set MB)]
    -> one line for the window t0..t1, or "" with none in it."""
    w = [x for x in samples if t0 <= x[0] <= t1]
    if not w:
        return ""
    cpu = [x[1] for x in w if x[1] is not None]
    return ("host during the window (%d samples): CPU mean %s / max %s, "
            "memory load max %d %%, this process's working set max %.0f MB"
            % (len(w), "%.0f %%" % (sum(cpu) / len(cpu)) if cpu else "n/a",
               "%.0f %%" % max(cpu) if cpu else "n/a",
               max(x[2] for x in w), max(x[3] for x in w)))


class HostSampler(object):
    """Samples the host every period s on Windows (diagnostic for H3,
    reviewer 2026-10-23): system CPU busy, memory load, and this process's
    working set. Nothing elsewhere."""

    def __init__(self, period=0.5):
        self.samples, self.period = [], period
        self.stop_ev = threading.Event()
        self.ok = os.name == "nt"
        if self.ok:
            self.t = threading.Thread(target=self._run, daemon=True)
            self.t.start()

    @staticmethod
    def _read():
        import ctypes
        from ctypes import wintypes

        class FT(ctypes.Structure):
            _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

        class MS(ctypes.Structure):
            _fields_ = [("len", wintypes.DWORD), ("load", wintypes.DWORD),
                        ("rest", ctypes.c_ulonglong * 7)]

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                        ("peak_ws", ctypes.c_size_t), ("ws", ctypes.c_size_t),
                        ("rest", ctypes.c_size_t * 6)]
        k32 = ctypes.windll.kernel32
        i, k, u = FT(), FT(), FT()
        k32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
        ms = MS()
        ms.len = ctypes.sizeof(MS)
        k32.GlobalMemoryStatusEx(ctypes.byref(ms))
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        # the current-process pseudo-handle (-1) as a HANDLE: through
        # GetCurrentProcess's default int restype it can come back
        # truncated on 64-bit (the new interposer tester, 2026-10-23)
        k32.K32GetProcessMemoryInfo(wintypes.HANDLE(-1),
                                    ctypes.byref(pmc), pmc.cb)
        v = lambda f: (f.hi << 32) | f.lo                  # noqa: E731
        return (v(i), v(k), v(u)), ms.load, pmc.ws / 1048576.0

    def _run(self):
        prev = None
        while not self.stop_ev.is_set():
            try:
                times, load, ws = self._read()
            except Exception:                              # noqa: BLE001
                self.ok = False
                return
            cpu = cpu_busy_pct(prev, times) if prev else None
            prev = times
            self.samples.append((time.perf_counter(), cpu, load, ws))
            self.stop_ev.wait(self.period)

    def close(self):
        self.stop_ev.set()


HOST = None         # a HostSampler, set by run_bench (diagnostic)


def process_snapshot(path):
    """What else runs on the host (diagnostic, H3): tasklist's CSV with CPU
    time, to path. -> a one-line note."""
    if os.name != "nt":
        return "process snapshot: not Windows"
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/v", "/fo", "csv"],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception as exc:                               # noqa: BLE001
        return "process snapshot failed: %s" % exc
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return "process snapshot: %d processes -> %s" % (
        max(0, len(out.splitlines()) - 1), os.path.basename(path))


def window(board, wit, node, seconds, command=None, label=None,
           rows_dir=None, from_command=False):
    """Clear both logs, optionally start a command, wait, return
    (rows, node_sent). The node's send log for the window is kept in
    node.window_log and the window's load in node.window_load (reviewer,
    2026-10-16: send rate and host-side receive load per window).
    The rows are the window's by the witness's own stamps, not by when its
    reader handled the cut (user rulings 2026-10-25: the vport-listen
    windows, then every witness window): the witness is read on for
    STAMP_DRAIN_S after the cut, and the rows kept, from the start's take,
    the cut's and the drain's, are those stamped within the node's window
    (stamp_bounds). from_command: the board's frames come from the
    command, so the window starts at the command if that is earlier than
    the node's first send (user ruling 2026-10-28: the STALL burst goes
    out before the node's first send). wit.window_stamp says what was kept
    and dropped;
    wit.window_reader holds the reader's figures (reader_stats, the cut's
    and the drain's together) and wit.window_stamp_fault a TEST FAULT line
    when the reader fell behind or its stamps are not on this clock
    (reader_fault), which witness_fault reports."""
    pre = wit.take()
    wit.take_incomplete()
    if hasattr(wit, "take_failures"):
        wit.take_failures()
    if hasattr(wit, "take_warnings"):
        wit.take_warnings()
    t_w0 = time.perf_counter()
    node.take()
    node.take_fails()
    node.take_log()
    t_cmd = None
    if command:
        t_cmd = time.perf_counter()
        board.cmd(command)
    time.sleep(seconds)
    raw = wit.take()
    st_cut = getattr(wit, "stats", None)
    t_wcut = time.perf_counter()
    sent = node.take()
    t_ncut = time.perf_counter()
    node.window_fails = node.take_fails()
    node.window_log = node.take_log()
    # read on, so rows stamped in the window but not yet read reach it;
    # before the failures are taken, so a receive failure meanwhile is this
    # window's
    time.sleep(STAMP_DRAIN_S)
    drained = wit.take()
    st_drain = getattr(wit, "stats", None)
    lo, hi = stamp_bounds(node.window_log, t_w0, t_wcut,
                          t_cmd if from_command else None)
    rows, wit.window_stamp = assign_by_stamp(pre, raw, drained, lo, hi)
    wit.window_bounds = (lo, hi)
    wit.window_reader = merge_stats(st_cut, st_drain)
    wit.window_stamp_fault = reader_fault(wit.window_reader)
    node.window_load = window_load(rows, node.window_log, seconds)
    wit.window_incomplete = wit.take_incomplete()
    t_w1 = time.perf_counter()
    kinds = wit.take_failures() if hasattr(wit, "take_failures") else {}
    wit.window_failures = failure_lines(kinds, t_w0)
    wit.window_fail_top = (max(kinds, key=lambda x: kinds[x][0])
                           if kinds else "")
    wit.window_warnings = (wit.take_warnings()
                           if hasattr(wit, "take_warnings") else (0, ""))
    wit.window_host = (host_summary(HOST.samples, t_w0, t_w1)
                       if HOST is not None else "")
    wit.window_saved = save_rows(rows, label or command, rows_dir)
    print("    diagnostic, witness: " + stamp_line(wit.window_stamp) + "; "
          + reader_line(wit.window_reader, rows, lo, hi)
          + ("; the window from the command (%s), %.1f ms before the "
             "node's first send" % (command, 1000 * (
                 min(x[3] for x in node.window_log) - t_cmd))
             if from_command and t_cmd is not None and node.window_log
             else "; the window from the command (%s), no node sends"
             % command if from_command and t_cmd is not None else ""))
    if EDGE_DIAG:
        # diagnostic only (reviewer, 2026-10-25): after the cut, what the
        # witness and the node log next; the next window's start discards
        # both anyway, so nothing judged changes but EDGE_POST_S of delay
        time.sleep(EDGE_POST_S)
        post = drained + wit.take()
        post_log = node.take_log()
        node.take()
        lines = edge_summary(node.window_log, raw, post, post_log, t_wcut,
                             t_ncut)
        save_edges(lines, node.window_log, post, post_log, rows_dir)
        print("    diagnostic, window edges: " + lines[0])
    return rows, sent


EDGE_DIAG = False   # --diag-edges: the window-edge diagnostic (above)
EDGE_POST_S = 0.5   # how long after a window's cut its next rows are kept

# by-stamp windows (user rulings 2026-10-25: the vport-listen windows, then
# every witness window). On the bench (run 6a6a) the witness's reader
# handled a cut 1.5-12.8 ms after it had stamped rows it had not yet read,
# and a node frame's stamp was -0.38..+0.90 ms from its send call in the
# idle windows (+26 ms with the board normal and the node queued).
STAMP_DRAIN_S = 0.5     # the witness is read on this long after the cut
STAMP_LEAD_S = 0.005    # a window starts this long before its first send call
STAMP_TAIL_S = 0.100    # and ends this long after its last send returned
# A window whose board frames come from its command starts at the command
# when that is earlier (user ruling 2026-10-28; run 6019's vport-stall kept
# 3 of the 16 STALL frames, the other 13 stamped before the node's first
# send): the stall step, and the vport-listen windows that send a command
# (the wrong-rate, flood and stall arms), where a board frame the command
# let out must be seen. Not the FLOOD windows of steps 2 and m1, whose
# flood runs the whole window and is judged on its rate and timing, nor
# step 3's, which open FLOOD_SETTLE_S after the flood started.
# the reader (user ruling 2026-10-25: it must keep up with a wrong-bit-rate
# error storm, and a reader that falls behind must not pass): a row read
# more than READER_LAG_TOL_S after its stamp faults the window, and so does
# one read more than STAMP_CLOCK_TOL_S BEFORE its stamp (stamps on another
# clock than the one the window is cut on)
READER_LAG_TOL_S = 0.25
STAMP_CLOCK_TOL_S = 0.005


def stamp_bounds(log, t_lo, t_hi, t_cmd=None):
    """-> (lo, hi): the node's window in witness-stamp time, from its first
    send call less STAMP_LEAD_S to its last send's return plus
    STAMP_TAIL_S; with no sends logged, [t_lo, t_hi] (the bench's own
    window). t_cmd, a window from its command: lo is the command if that
    is earlier."""
    if log:
        lo = min(x[3] for x in log) - STAMP_LEAD_S
        if t_cmd is not None:
            lo = min(lo, t_cmd)
        return (lo, max(x[4] for x in log) + STAMP_TAIL_S)
    return t_lo, t_hi


def assign_by_stamp(pre, rows, drained, lo, hi):
    """pre: what the reader gave at the window's start; rows: at the cut;
    drained: in the STAMP_DRAIN_S after. -> (the rows of all three stamped
    within [lo, hi], in stamp order; (kept from the start's take, from the
    cut, from after it, dropped as stamped before lo, dropped as stamped
    after hi))."""
    a = [r for r in pre if lo <= r[0] <= hi]
    b = [r for r in rows if lo <= r[0] <= hi]
    c = [r for r in drained if lo <= r[0] <= hi]
    every = list(pre) + list(rows) + list(drained)
    before = sum(1 for r in every if r[0] < lo)
    after = sum(1 for r in every if r[0] > hi)
    return (sorted(a + b + c, key=lambda r: r[0]),
            (len(a), len(b), len(c), before, after))


def merge_stats(*parts):
    """reader_stats of several takes -> one (None if none had any)."""
    parts = [p for p in parts if p]
    if not parts:
        return None
    out = {}
    for key in ("rows", "reads", "t_read", "t_wait", "wall"):
        out[key] = sum(p[key] for p in parts)
    lmin = [p["lag_min"] for p in parts if p["lag_min"] is not None]
    lmax = [p["lag_max"] for p in parts if p["lag_max"] is not None]
    lend = [p["lag_end"] for p in parts if p["lag_end"] is not None]
    out["lag_min"] = min(lmin) if lmin else None
    out["lag_max"] = max(lmax) if lmax else None
    out["lag_end"] = lend[-1] if lend else None
    return out


def reader_fault(st):
    """"" or a TEST FAULT line: a row read more than READER_LAG_TOL_S after
    its stamp (the reader fell behind, or its stamps run behind this
    clock), or one read more than STAMP_CLOCK_TOL_S before it (the stamps
    run ahead). No figures (a fake witness, a dead reader): ""; a dead
    reader is witness_fault's own."""
    if not st or st.get("lag_max") is None:
        return ""
    if st["lag_max"] > READER_LAG_TOL_S:
        return ("TEST FAULT: the witness's reader fell behind: a row read "
                "%.3f s after its stamp, beyond %.3f s (or its stamps are "
                "not on this clock) -- the window is not judged"
                % (st["lag_max"], READER_LAG_TOL_S))
    if st["lag_min"] < -STAMP_CLOCK_TOL_S:
        return ("TEST FAULT: a witness row was read %.3f s BEFORE its stamp: "
                "its stamps are not on this clock, so the window cannot be "
                "cut by them" % -st["lag_min"])
    return ""


def stamp_line(info):
    a, b, c, before, after = info
    return ("rows by stamp: %d kept (%d read before the window, %d by the "
            "cut, %d after it); %d stamped before the window and %d after "
            "it dropped" % (a + b + c, a, b, c, before, after))


def reader_line(st, rows, lo, hi):
    """The reader's figures for one window: its rate (rows over the time
    it was not waiting on an empty queue), the Read call's cost, the lag,
    and how much of the window its kept rows' stamps cover."""
    cover = ("stamps %.2f..%.2f s of the window's %.2f s"
             % (rows[0][0] - lo, rows[-1][0] - lo, hi - lo) if rows
             else "no rows")
    if not st:
        return "reader figures not available; " + cover
    busy = st["wall"] - st["t_wait"]
    return ("reader: %d rows in %d reads, %.0f rows/s while not waiting "
            "(%.2f of %.2f s), Read %.1f us/call; lag %s; %s" % (
                st["rows"], st["reads"],
                st["rows"] / busy if busy > 0 else float("nan"), busy,
                st["wall"], 1e6 * st["t_read"] / max(st["reads"], 1),
                ("%.1f..%.1f ms, last %.1f ms" % (
                    1000 * st["lag_min"], 1000 * st["lag_max"],
                    1000 * st["lag_end"])) if st["lag_max"] is not None
                else "-", cover))


def edge_summary(log, rows, post, post_log, t_wcut, t_ncut):
    """The window-edge diagnostic (reviewer, 2026-10-25: listen/idle's "2 of
    1001 node frame(s) never reached the witness") -> lines, the first a
    one-line summary. log / post_log: the node's send log in the window and
    in the EDGE_POST_S after it ((seq, lag, ok, t_call, t_back)); rows /
    post: the witness's rows in the window and after its cut; t_wcut /
    t_ncut: when the bench process had the witness's cut and the node's."""
    sent = sorted(x[0] for x in log if x[2])
    seen = set(q for q in (node_seq(r) for r in rows) if q is not None)
    later = set(q for q in (node_seq(r) for r in post) if q is not None)
    missing = [q for q in sent if q not in seen]
    extra = sorted(seen - set(sent))
    back = dict((x[0], x[4]) for x in log if len(x) > 4)
    found = [q for q in missing if q in later]
    head = ("node sent %d (%s), witness holds %d (%s); %d missing, %d of "
            "them in the witness's rows after its cut; %d held but not in "
            "the node's log; node cut %.1f ms after the witness's"
            % (len(sent), "%d..%d" % (sent[0], sent[-1]) if sent else "none",
               len(seen), "%d..%d" % (min(seen), max(seen)) if seen
               else "none", len(missing), len(found), len(extra),
               (t_ncut - t_wcut) * 1000))
    lines = [head]
    for q in missing:
        b = back.get(q)
        lines.append("missing seq %d: send returned %s; %s"
                     % (q, ("%.1f ms before the witness's cut"
                            % ((t_wcut - b) * 1000)) if b is not None
                        else "(no time)",
                        "in the witness's next rows" if q in later
                        else "NOT in the witness's next rows"))
    for q in extra:
        lines.append("held, not in the node's log: seq %d" % q)
    lines.append("after the cut: %d witness rows, %d node sends logged"
                 % (len(post), len(post_log)))
    return lines


def save_edges(lines, log, post, post_log, rows_dir=None):
    """The diagnostic's lines, the node's send log and the witness's rows
    after the cut, beside the window's rows (NN_edges.txt, NN the window's
    number)."""
    d = rows_dir or RAW_DIR
    if not d:
        return
    with open(os.path.join(d, "%02d_edges.txt" % _raw_n[0]), "w",
              encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")
        f.write("# node send log in the window: seq lag_ms ok t_call "
                "t_back\n")
        for x in log:
            f.write("%d %.3f %d %s\n" % (x[0], x[1] * 1000, x[2], " ".join(
                "%.6f" % v for v in x[3:5])))
        f.write("# node send log after the cut\n")
        for x in post_log:
            f.write("%d %.3f %d %s\n" % (x[0], x[1] * 1000, x[2], " ".join(
                "%.6f" % v for v in x[3:5])))
        f.write("# witness rows after the cut\n")
        for t, ident, err, data in post:
            f.write("%.6f %X %d %s\n" % (t, ident, 1 if err else 0,
                                         data.hex()))


def window_load(rows, log, seconds):
    """One window's node send rate (host side, from the node process's log)
    and the witness's host-side receive load -> a line."""
    if not seconds:
        return ""
    lags = sorted(x[1] for x in log)
    ok = sum(1 for x in log if x[2])
    wire = sum(1 for r in rows if node_seq(r) is not None)
    return ("load: node sends %.1f/s attempted, %.1f/s accepted, send lag "
            "against its schedule p99 %.2f / max %.2f ms; node frames on the "
            "wire %.1f/s; witness rows taken %.0f/s (%d error rows)"
            % (len(log) / seconds, ok / seconds,
               (lags[min(len(lags) - 1, int(.99 * len(lags)))] * 1000)
               if lags else float("nan"),
               (lags[-1] * 1000) if lags else float("nan"),
               wire / seconds, len(rows) / seconds,
               sum(1 for r in rows if r[2])))


RAW_DIR = None      # set by run_bench: every window's witness rows go here
_raw_n = [0]


def save_rows(rows, label, rows_dir=None):
    """Every window's witness rows, error frames with their data, to
    RAW_DIR/NN_<label>.txt -- so a count can be traced to its frames.
    rows_dir: another folder instead (the m1 diagnostic's local arm).
    -> (path, bytes, seconds the write took), or None."""
    d = rows_dir or RAW_DIR
    if not d:
        return None
    _raw_n[0] += 1
    name = "%02d_%s.txt" % (_raw_n[0], (label or "window").replace(" ", "_"))
    path = os.path.join(d, name)
    t0 = time.perf_counter()
    with open(path, "w", encoding="utf-8") as f:
        for t, ident, err, data in rows:
            f.write("%.6f %X %d %s\n" % (t, ident, 1 if err else 0, data.hex()))
    return path, os.path.getsize(path), time.perf_counter() - t0


def witness_fault(wit, what):
    """(f): a window in which the witness's receive failed is incomplete ->
    a TEST FAULT line, or None. Since 2026-10-25 also a window whose
    reader fell behind or whose stamps are not on this clock
    (wit.window_stamp_fault, from window())."""
    n = getattr(wit, "window_incomplete", 0)
    if n:
        top = getattr(wit, "window_fail_top", "")
        return ("TEST FAULT: %s: the witness's receive failed %d time(s) in "
                "the window (%s) -- its rows are incomplete"
                % (what, n, top or "no failure text kept"))
    sf = getattr(wit, "window_stamp_fault", "")
    if sf:
        return sf.replace("TEST FAULT: ", "TEST FAULT: %s: " % what, 1)
    return None


def node_fault(node, what):
    """(d): a window in which the node's sends failed tests nothing that
    rests on the node -> a TEST FAULT line, or None."""
    f = getattr(node, "window_fails", 0)
    if f:
        return ("TEST FAULT: %s: %d of the node's sends failed in the window "
                "-- its frames were not completing, so nothing resting on "
                "them is judged" % (what, f))
    return None


LISTEN_ARMS = ("no-ack", "idle", "wrong-250k", "wrong-1m", "flood-7F4",
               "flood-649", "stall")


def listen_arm_keys(spec):
    """--listen-arms: "all" or a comma list of LISTEN_ARMS keys -> the keys,
    in LISTEN_ARMS order; ValueError on an unknown or empty one (tester,
    2026-10-21: the reviewer ruled wrong-250k and wrong-1m KNOWN-FAIL, so
    the suite runs them as a check of their own)."""
    if spec == "all":
        return list(LISTEN_ARMS)
    keys = [k.strip() for k in spec.split(",") if k.strip()]
    bad = [k for k in keys if k not in LISTEN_ARMS]
    if bad or not keys:
        raise ValueError("--listen-arms: unknown %s (known: %s)"
                         % (bad or "(none given)", ", ".join(LISTEN_ARMS)))
    return [k for k in LISTEN_ARMS if k in keys]


def listen_subs(keys):
    """The listen-only windows for the selected keys: (name, commands)."""
    subs = []
    if "idle" in keys:
        subs.append(("idle", None))
    for r, k in (("250k", "wrong-250k"), ("1M", "wrong-1m")):
        if k in keys:
            subs.append(("bitrate %s + flood 7F4" % r,
                         ("BITRATE " + r, "FLOOD 7F4")))
    for i in FLOOD_IDS:
        if "flood-%X" % i in keys:
            subs.append(("flood %X" % i, ("BITRATE 500k", "FLOOD %X" % i)))
    if "stall" in keys:
        subs.append(("stall %d" % STALL_N, ("BITRATE 500k",
                                            "STALL %d" % STALL_N)))
    return subs


def listen_window(board, wit, node, secs, name, cmds):
    """One of step 1's windows, the witness acknowledging: the sub's
    commands, a window cut by the witness's stamps (user ruling
    2026-10-25), STOP, score_listen. -> (ok, lines); ok FAULT when the
    node, the witness or the stamps' clock faulted the window."""
    if cmds:
        board.cmd(cmds[0])
    rows, sent = window(board, wit, node, secs, cmds[1] if cmds else None,
                        from_command=bool(cmds))
    board.cmd("STOP")
    ok, lines = score_listen(rows, sent)
    nfs = [nf for nf in (node_fault(node, "listen-only, " + name),
                         witness_fault(wit, "listen-only, " + name)) if nf]
    if nfs:
        ok = FAULT
        lines = nfs + unjudged(lines)
    return ok, lines


def m1_trial_line(n, arm, flood_rows, saved, idle, wf, fails, warns,
                  flood_wf=""):
    """One m1 trial -> its line (pure, for the self-test). wf: the 1M
    idle window's witness fault; flood_wf: the flood window's."""
    return ("m1 trial %d %s: flood %d rows, file %s, %s; 1M idle %d "
            "rows (%.0f/s), first 1.25 s %.0f rows/s, largest gap %.1f "
            "ms at +%.2f s, %d low 0.5 s bin(s); %s; %d PCAN "
            "warning(s)%s"
            % (n, arm, flood_rows,
               ("%.1f MB written in %.2f s" % (saved[1] / 1e6, saved[2]))
               if saved else "not saved",
               ("flood window WITNESS FAULT: " + flood_wf) if flood_wf
               else "flood window complete",
               idle["rows"], idle["rate"], idle["head_rate"],
               idle["gap_ms"], idle["gap_at"], idle["low_bins"],
               ("WITNESS FAULT: " + wf) if wf else "witness complete",
               warns, "".join("; " + ln for ln in fails)))


def run_m1(args, board, wit, node, verdicts):
    """The m1 DIAGNOSTIC step (above M1_ARMS): judges nothing. Each trial
    as the judged step 2 runs 250k then 1M: BITRATE 250k, an idle window,
    the FLOOD 7F4 window, STOP, STATUS, the flood window scored as step 2
    scores it, BITRATE 1M, the arm's settle, then the 1M idle window."""
    arms = parse_m1_arms(args.m1_arms)
    local = os.path.join(tempfile.gettempdir(), "interposer-tester",
                         "m1-local", os.path.basename(RAW_DIR or "run"))
    os.makedirs(local, exist_ok=True)
    print("\n== m1 DIAGNOSTIC (judges nothing): a 250k flood, then a 1M "
          "idle window; %d trial(s), arms in turn %s; settle %.1f s; "
          "windows: 250k idle %.0f s, flood %.0f s, 1M idle %.0f s; local "
          "rows folder %s" % (args.m1_trials, ", ".join(a[0] for a in arms),
                              args.m1_settle_s, args.m1_pre_s,
                              args.m1_flood_s, args.m1_idle_s, local))
    board.cmd("MODE normal")
    board.cmd("AUTORECOVER on")
    table = []
    for i in range(args.m1_trials):
        arm, settle, here = arms[i % len(arms)]
        tag = "m1 %d %s" % (i + 1, arm)
        board.cmd("BITRATE 250k")
        window(board, wit, node, args.m1_pre_s, label=tag + " 250k idle")
        rows_f, sent_f = window(board, wit, node, args.m1_flood_s,
                                "FLOOD 7F4", label=tag + " 250k flood",
                                rows_dir=local if here else None)
        saved = getattr(wit, "window_saved", None)
        wf_f = witness_fault(wit, tag + " 250k flood")
        board.cmd("STOP")
        board.cmd("STATUS")
        tsw = [x[0] for x in rows_f if x[2]] or [x[0] for x in rows_f]
        timing_ok(tsw, 1.0, back_tol_ms=BACK_TOL_MS)
        score_bitrate(rows_f, sent_f, args.m1_flood_s,
                      node_fails=getattr(node, "window_fails", 0))
        board.cmd("BITRATE 1M")
        if settle:
            time.sleep(args.m1_settle_s)
        rows_i, _s = window(board, wit, node, args.m1_idle_s,
                            label=tag + " 1M idle")
        wf = witness_fault(wit, tag + " 1M idle")
        pw, _l = getattr(wit, "window_warnings", (0, ""))
        idle = m1_idle_metrics(rows_i)
        print("  " + m1_trial_line(i + 1, arm, len(rows_f), saved, idle, wf,
                                   getattr(wit, "window_failures", []), pw,
                                   flood_wf=wf_f))
        host = getattr(wit, "window_host", "")
        if host:
            print("    1M idle, diagnostic: " + host)
        board.cmd("STOP")
        board.cmd("STATUS")
        table.append((arm, len(rows_f), bool(wf), bool(wf_f)))
        if here and saved:
            try:
                os.remove(saved[0])
            except OSError:
                pass
    board.cmd("BITRATE 500k")
    board.cmd("MODE listen")
    print("  m1 by arm (trials, witness faults in the 1M idle window and "
          "in the flood window, flood rows):")
    for a in arms:
        got = [x for x in table if x[0] == a[0]]
        print("    %-15s %d trial(s), %d fault(s), %d flood window "
              "fault(s); flood rows %s"
              % (a[0], len(got), sum(1 for x in got if x[2]),
                 sum(1 for x in got if x[3]),
                 ", ".join(str(x[1]) for x in got) or "-"))
    verdicts.append(("m1/diagnostic", "summary"))


def run_bench(args):
    print("run at %s; vehicle port only, MCP2515 path: NO" %
          datetime.datetime.now().isoformat())
    print("pins: %s; bus %d bit/s; node (Kvaser channel %d, normal, ACKs) "
          "0x%X every %.0f ms; witness PCAN LISTEN-ONLY (state=PASSIVE) for "
          "steps 0, 1's no-ACK check, 4 and 5, ACKING at %d bit/s for steps "
          "1-3's windows" % (PINS, BUS_BITRATE, args.kvaser_channel, NODE_ID,
                             NODE_PERIOD_S * 1000, BUS_BITRATE))
    elf_sha = None
    if args.elf:
        with open(args.elf, "rb") as f:
            elf_sha = hashlib.sha256(f.read()).hexdigest()
        print("--elf %s sha256 %s; --src-sha %s" % (args.elf, elf_sha,
                                                    args.src_sha))
    global RAW_DIR, HOST
    HOST = HostSampler()
    print("host sampler (diagnostic): %s" % ("CPU, memory load and this "
          "process's working set every %.1f s" % HOST.period if HOST.ok
          else "not available on this host"))
    if EDGE_DIAG:
        print("window edges (diagnostic, --diag-edges): after each window, "
              "%.1f s more of the node's send log and the witness's rows, "
              "kept in NN_edges.txt; judges nothing" % EDGE_POST_S)
    print("witness windows by stamp (user rulings 2026-10-25): read on %.1f s "
          "after each cut, rows kept by their stamps within the node's "
          "window; %s reader; a row read > %.3f s after its stamp, or > %.3f "
          "s before it, faults the window" % (
              STAMP_DRAIN_S, WITNESS_READER, READER_LAG_TOL_S,
              STAMP_CLOCK_TOL_S))
    RAW_DIR = os.path.splitext(args.out)[0] + "_rows" if args.out else None
    if RAW_DIR:
        os.makedirs(RAW_DIR, exist_ok=True)
    board = Board(args.port)
    rc = 0
    wit = node = None
    try:
        hello = board.cmd("HELLO?", "@HELLO")
        sha = hello_field(hello, "elf_sha256")
        if elf_sha and sha != elf_sha:
            for ln in start_refused("the board's ELF sha256 %s is not "
                                    "--elf's %s" % (sha, elf_sha)):
                print(ln)
            return 1
        if hello_field(hello, "tx_gpio") != "7" or \
                hello_field(hello, "rx_gpio") != "6":
            for ln in start_refused("the image reports pins other than TX 7 "
                                    "/ RX 6"):
                print(ln)
            return 1
        wit = Witness()
        steps = [s for s in args.steps.split(",") if s]
        secs = args.seconds
        verdicts = []

        # step 0 always runs, before the node opens: every later step that
        # judges an ACK depends on the witness not giving one
        print("\n== 0 witness silence (the witness alone, the node closed)")
        board.cmd("MODE normal")
        sent = board.cmd("SEND %X 1" % SILENCE_ID, "@SENT")
        time.sleep(0.5)
        status = board.cmd("STATUS", "@STATUS")
        board.cmd("RESET")
        board.cmd("MODE listen")
        ok, lines = score_silence(sent, status)
        if not ok:
            for ln in start_refused("the witness is not shown silent",
                                    lines):
                print(ln)
            return 1
        for ln in lines:
            print("    " + ln)
        node = Node(args.kvaser_channel).start()

        if "listen" in steps:
            arms = listen_arm_keys(args.listen_arms)
            print("\n== 1 listen-only baseline (arms: %s)" % ", ".join(arms))
            if "no-ack" in arms:
                # (c) the silence claim: the witness PASSIVE, the node open
                # -- in listen-only nothing may acknowledge the node's
                # frames; the can-fail is the same window with the board in
                # normal mode, where the board's ACK must let them complete
                wit = set_witness(wit, False)
                board.cmd("MODE listen")
                rows, _s = window(board, wit, node, 3.0)
                v, lines = score_noack(rows)
                ok = (True if v == "unacked" else FAULT if v == "fault"
                      else False)
                verdicts.append(("listen/does-not-ack", ok))
                print("  listen-only does not ACK: %s" % verdict_word(ok))
                for ln in lines:
                    print("    " + ln)
                board.cmd("MODE normal")
                rows, _s = window(board, wit, node, 3.0)
                v2, lines = score_noack(rows)
                okc = (True if v2 == "acked" else FAULT if v2 == "fault"
                       else False)
                verdicts.append(("listen/does-not-ack can-fail", okc))
                print("  can-fail, the board in NORMAL mode: %s (the check "
                      "must see the board's ACK)" % verdict_word(okc))
                for ln in lines:
                    print("    " + ln)
            board.cmd("MODE listen")
            # the windows: the witness acknowledging, so the node's frames
            # complete whatever the board does
            wit = set_witness(wit, True)
            time.sleep(1.0)
            for name, cmds in listen_subs(arms):
                ok, lines = listen_window(board, wit, node, secs, name, cmds)
                verdicts.append(("listen/" + name, ok))
                print("  listen-only, %s: %s" % (name, verdict_word(ok)))
                for ln in lines:
                    print("    " + ln)
            board.cmd("BITRATE 500k")
            board.cmd("RESET")

        if "recstart" in steps:
            # reviewer, 2026-10-16: is the listen-only REC=128 of IDF's
            # errata workaround in force? Read on the REGISTER (rec_reg=).
            # Wiring check A is step 0's STATUS above (normal mode); wiring check B
            # (normal at 250k, REC > 0) runs AFTER the listen-only reads so
            # that its REC cannot be carried into the first start; the
            # read after it shows whether a fresh install clears it.
            print("\n== 1r REC on the register: listen-only starts (CAN1 "
                  "quiet), node traffic, then the normal-mode wiring check")
            wit = set_witness(wit, True)
            if not isinstance(node, NoNode):
                node.close()
                node = NoNode()
            time.sleep(1.0)
            wit.take()
            starts = []
            for i in range(RECSTART_REPS):
                r = board.cmd("MODE listen")
                t0 = time.perf_counter()
                reads = []
                for k in range(RECSTART_FAST_N):
                    st = board.cmd("STATUS")
                    reads.append(("+%.0f ms" % ((time.perf_counter() - t0)
                                                * 1000.0),
                                  st if r and "driver=ESP_OK" in r else None))
                time.sleep(max(0.0, RECSTART_QUIET_S
                               - (time.perf_counter() - t0)))
                reads.append(("+%.1f s quiet" % RECSTART_QUIET_S,
                              board.cmd("STATUS")))
                starts.append(reads)
            quiet_rows = wit.take()
            save_rows(quiet_rows, "recstart quiet")
            node = Node(args.kvaser_channel).start()
            t0 = time.perf_counter()
            node_reads = []
            for at in RECSTART_NODE_AT:
                time.sleep(max(0.0, at - (time.perf_counter() - t0)))
                node_reads.append(("+%.2f s" % at, board.cmd("STATUS")))
            node_rows = wit.take()
            save_rows(node_rows, "recstart node")
            board.cmd("MODE normal")
            board.cmd("BITRATE 250k")
            time.sleep(RECSTART_CANFAIL_S)
            can_b = board.cmd("STATUS")
            node.close()
            node = NoNode()
            board.cmd("BITRATE 500k")
            board.cmd("MODE listen")
            carry = board.cmd("STATUS")
            ok, v, lines = judge_recstart(status, starts, node_reads, can_b,
                                          quiet_rows)
            for ln in lines:
                print("    " + ln)
            print("  node window: %d witness row(s), %d error frame(s)"
                  % (len(node_rows), sum(1 for x in node_rows if x[2])))
            print("  after wiring check B, a fresh listen-only install at 500k: "
                  "rec_reg=%s (whether an install clears the register)"
                  % status_field(carry, "rec_reg"))
            print("  RESULT: %s" % v)
            verdicts.append(("recstart (judged)", ok))

        if "discrim" in steps:
            # reviewer-requested (2026-10-15): the error frames seen in
            # listen-only at a wrong bit rate -- the board, or a bench node?
            # The PCAN acknowledging (it flags only errors it sees). With
            # the node CLOSED nothing transmits at all, so an error frame
            # there was driven by something on a silent bus.
            print("\n== 1b listen-only at a wrong bit rate: who makes the "
                  "error frames?")
            wit = set_witness(wit, True)
            board.cmd("MODE listen")
            for label, rate, node_open in (("1M, node open", "1M", True),
                                           ("250k, node open", "250k", True),
                                           ("1M, node CLOSED", "1M", False),
                                           ("250k, node CLOSED", "250k",
                                            False),
                                           ("500k, node CLOSED (control)",
                                            "500k", False)):
                if node_open and isinstance(node, NoNode):
                    node = Node(args.kvaser_channel).start()
                    time.sleep(1.0)
                if not node_open and not isinstance(node, NoNode):
                    node.close()
                    node = NoNode()
                    time.sleep(1.0)
                board.cmd("BITRATE " + rate)
                time.sleep(0.5)
                rows, _s = window(board, wit, node, secs,
                                  "discrim " + label.split(" (")[0])
                pw, pwlast = wit.window_warnings
                try:
                    pst = wit.status_string()
                except Exception as e:                     # noqa: BLE001
                    pst = "status unreadable (%s)" % e
                st = board.cmd("STATUS")
                wf = witness_fault(wit, label)
                errs = sum(1 for x in rows if x[2])
                others = sum(1 for x in rows if not x[2])
                print("  listen-only %s: %d error frame(s), %d other row(s) "
                      "on the witness%s; the PCAN's own status: %d bus-state "
                      "warning(s)%s, at the end '%s'; board STATUS bus_err=%s "
                      "rx_frames=%s mode=%s bitrate=%s"
                      % (label, errs, others, (" -- " + wf) if wf else "",
                         pw, (" (last: %s)" % pwlast) if pw else "", pst,
                         hello_field(st, "bus_err"),
                         hello_field(st, "rx_frames"),
                         hello_field(st, "mode"), hello_field(st, "bitrate")))
            board.cmd("BITRATE 500k")
            print("  (iv) the node open with the board's TWAI stopped: NOT "
                  "RUN -- vport_bench has no command that leaves the driver "
                  "uninstalled (only DOMINANT does, during its pulses); it "
                  "would need a reflash")
            if isinstance(node, NoNode):
                node = Node(args.kvaser_channel).start()
                time.sleep(1.0)

        if "offpair" in steps:
            # (iv), reviewer 2026-10-16: the node open at 500k, the PCAN
            # acknowledging at 500k; per wrong rate, back to back on one
            # image: the board's TWAI stopped and uninstalled (DRIVER off,
            # TXD a GPIO held recessive), then installed listen-only at
            # that rate, then off again. The pair differs only in the
            # board's state. Errors in the listen-only window and none in
            # either off window would put the source in the board.
            print("\n== 1c the board's controller removed vs listen-only at "
                  "a wrong rate (node open, PCAN acknowledging)")
            wit = set_witness(wit, True)
            if isinstance(node, NoNode):
                node = Node(args.kvaser_channel).start()
            time.sleep(1.0)
            board.cmd("MODE listen")
            pair = []
            for rate in ("1M", "250k"):
                for state in ("off", "listen", "off"):
                    if state == "off":
                        r = board.cmd("DRIVER off")
                        st = board.cmd("STATUS")
                        proven = bool(r and "installed=0" in r and st
                                      and "installed=0" in st)
                    else:
                        board.cmd("BITRATE " + rate)
                        st = board.cmd("STATUS")
                        proven = bool(st and "installed=1" in st
                                      and "mode=listen" in st
                                      and ("bitrate=%s" % rate) in st)
                    time.sleep(0.5)
                    label = "%s, board %s" % (rate, "TWAI OFF" if state ==
                                              "off" else "listen-only at "
                                              + rate)
                    rows, sent = window(board, wit, node, secs,
                                        "offpair " + label)
                    pw, _l = wit.window_warnings
                    errs = sum(1 for x in rows if x[2])
                    seen = len(set(q for q in (node_seq(x) for x in rows)
                                   if q is not None))
                    faults = [f for f in (witness_fault(wit, label),
                                          node_fault(node, label)) if f]
                    if not proven:
                        faults.append("TEST FAULT: the board's state was not "
                                      "shown (%s)" % (st or "no STATUS"))
                    pair.append((rate, state, errs, faults))
                    print("  %s: %d error frame(s), %d node frame(s) seen "
                          "of %d sent, %d PCAN bus-state warning(s)%s"
                          % (label, errs, seen, len(sent), pw,
                             "".join(" -- " + f for f in faults)))
                    if state == "off":
                        board.cmd("DRIVER on")
            board.cmd("BITRATE 500k")
            for rate in ("1M", "250k"):
                ok, v = judge_offpair([x for x in pair if x[0] == rate])
                verdicts.append(("offpair/%s" % rate, ok))
                print("  %s: %s" % (rate, v))

        if "bitrate" in steps:
            print("\n== 2 wrong bit rate (normal mode, AUTORECOVER on)")
            wit = set_witness(wit, True)
            print("  the witness: %s, acknowledging the node (Kvaser 0x%X at "
                  "%d bit/s); the board is at the window's bit rate, so the "
                  "witness's ACK is for the node's frames and cannot be "
                  "credited to the board" % (wit.describe(), NODE_ID,
                                             BUS_BITRATE))
            board.cmd("MODE normal")
            board.cmd("AUTORECOVER on")
            print("  rates in the order %s%s" % (
                ", ".join(args.bitrate_rates),
                "" if tuple(args.bitrate_rates) == BITRATE_RATES
                else " (--bitrate-order, diagnostic)"))
            print("  " + process_snapshot(os.path.join(
                RAW_DIR or ".", "processes_step2_start.csv")))
            for r in args.bitrate_rates:
                board.cmd("BITRATE " + r)
                for name, cmd in (("idle", None), ("flooding 7F4",
                                                   "FLOOD 7F4")):
                    rows, sent = window(board, wit, node, args.bitrate_seconds,
                                        cmd)
                    board.cmd("STOP")
                    board.cmd("STATUS")
                    pw, pwlast = getattr(wit, "window_warnings", (0, ""))
                    for ln in (getattr(wit, "window_failures", []) +
                               [getattr(wit, "window_host", "")] +
                               ["the PCAN's bus-state warnings: %d (last: %s)"
                                % (pw, pwlast) if pw else ""]):
                        if ln:
                            print("  %s %s, diagnostic: %s" % (r, name, ln))
                    tsw = ([x[0] for x in rows if x[2]] or
                           [x[0] for x in rows])
                    t_ok, why = timing_ok(tsw, 1.0, back_tol_ms=BACK_TOL_MS)
                    if not t_ok:
                        v, ln = refused_arm(why)
                        print("  %s %s: %s" % (r, name, ln))
                        verdicts.append(("bitrate/%s/%s" % (r, name), v))
                        continue
                    # (g) here the node failing to complete IS the effect
                    # (the board at a wrong rate wrecks traffic): reported,
                    # never a fault. Only an incomplete witness faults it.
                    wf = witness_fault(wit, "%s %s" % (r, name))
                    if wf:
                        print("  %s, %s: %s" % (r, name, wf))
                        verdicts.append(("bitrate/%s/%s" % (r, name), FAULT))
                        continue
                    nfails = getattr(node, "window_fails", 0)
                    m2, lines = score_bitrate(rows, sent,
                                              args.bitrate_seconds,
                                              node_fails=nfails)
                    print("  %s, %s: %s" % (r, name, lines[0]))
                    print("    %s; %s" % (lines[1], why))
                    flag = bitrate_flag(back_steps(tsw)[0],
                                        m2["near_threshold"])
                    if flag:
                        print("    " + flag)
            board.cmd("BITRATE 500k")
            board.cmd("MODE listen")
            print("  " + process_snapshot(os.path.join(
                RAW_DIR or ".", "processes_step2_end.csv")))

        if "m1" in steps:
            wit = set_witness(wit, True)
            run_m1(args, board, wit, node, verdicts)

        if "flood" in steps:
            print("\n== 3 flood: delay added to the node's 0x%X" % NODE_ID)
            wit = set_witness(wit, True)
            board.cmd("MODE normal")
            # settle (reviewer, 2026-10-16): the 00:01 baseline ran straight
            # after step 2's 1M flood and lost a node send. The board idle,
            # wait for SETTLE_CLEAN_S with no failed node send.
            settled, took = settle_node(node.take_fails, time.perf_counter,
                                        time.sleep)
            print("  node settle (board idle in normal mode at 500k): %s "
                  "after %.1f s" % ("settled, %.0f s with no failed send"
                                    % SETTLE_CLEAN_S if settled else
                                    "NEVER SETTLED", took))
            print("  node process priority class: %s (0x8000 = "
                  "ABOVE_NORMAL)" % (hex(node.prio) if getattr(
                      node, "prio", None) else "unknown"))
            base, _s = window(board, wit, node, secs, label="flood_baseline")
            print("  baseline %s" % node.window_load)
            nf = (node_fault(node, "flood baseline")
                  or witness_fault(wit, "flood baseline"))
            if not settled:
                nf = ("TEST FAULT: the node never ran %.0f s without a failed "
                      "send in %.0f s (board idle), so step 3 measures nothing"
                      % (SETTLE_CLEAN_S, SETTLE_MAX_S))
            t_ok, why = timing_ok([x[0] for x in base], 0.01)
            if nf:
                print("  %s" % nf)
                verdicts.append(("flood", FAULT))
            elif not t_ok:
                v, ln = refused_arm(why)
                print("  flood baseline: %s" % ln)
                verdicts.append(("flood", v))
            else:
                print("  %s" % why)
                for fid in FLOOD_IDS:
                    # (ii): the flood runs FLOOD_SETTLE_S before the window
                    # opens, so a backlog from the switch is not in it
                    board.cmd("FLOOD %X" % fid)
                    time.sleep(FLOOD_SETTLE_S)
                    rows, _s = window(board, wit, node, secs,
                                      label="FLOOD_%X" % fid)
                    board.cmd("STOP")
                    print("    0x%X %s (window opened %.1f s after the flood "
                          "started)" % (fid, node.window_load, FLOOD_SETTLE_S))
                    ok, _m, lines = score_flood(base, rows, fid)
                    nfs = [nf for nf in (node_fault(node, "flood %X" % fid),
                                         witness_fault(wit, "flood %X" % fid))
                           if nf]
                    if nfs:
                        ok = FAULT
                        lines = nfs + unjudged(lines)
                    verdicts.append(("flood/%X" % fid, ok))
                    for ln in lines:
                        print("    " + ln)
            board.cmd("MODE listen")

        if "stall" in steps:
            print("\n== 4 stalled task")
            wit = set_witness(wit, False)
            board.cmd("MODE normal")
            rows, _s = window(board, wit, node, 2.0, "STALL %d" % STALL_N,
                              from_command=True)
            ok, _m, lines = score_stall(rows, STALL_N, acked=True)
            verdicts.append(("stall/acked", ok))
            for ln in lines:
                print("    " + ln)
            board.cmd("RESET")
            if args.no_acker_ready:
                node.close()
                node = NoNode()
                rows, _s = window(board, wit, node, 5.0,
                                  "STALL %d" % STALL_N, from_command=True)
                board.cmd("STATUS")
                ok, _m, lines = score_stall(rows, STALL_N, acked=False)
                verdicts.append(("stall/no-acker", ok))
                for ln in lines:
                    print("    " + ln)
                board.cmd("RESET")
                node = Node(args.kvaser_channel).start()
            else:
                print("    no-ACKer variant NOT RUN: needs --no-acker-ready "
                      "(the WiCAN off or passive; the node is closed)")
            board.cmd("MODE listen")

        if "dominant" in steps:
            # spec 9 at 731e9c15 (user, 2026-10-13): timed by the board
            # itself, TXD low to RXD recessive; the adapters a coarse check
            print("\n== 5 MCP2562 permanent-dominant timeout (board-timed)")
            wit = set_witness(wit, False)
            # the node closed: its unacknowledged retries were in the first
            # re-run's cross-check (one 7.9 ms event)
            if not isinstance(node, NoNode):
                node.close()
                node = NoNode()
                time.sleep(1.0)
            wit.take()
            node.take()
            got, summ = board.cmd_lines("DOMINANT %d 1" % DOM_SHORT_US,
                                        "@DOMINANT", "@DOM ")
            ok, _m, lines = score_dominant(got, DOM_SHORT_US, short=True)
            verdicts.append(("dominant/can-fail-0.5ms", ok and summ))
            for ln in lines:
                print("    " + ln)
            wit.take()
            node.take()
            got, summ = board.cmd_lines("DOMINANT %d %d" % (DOM_PULSE_US,
                                                            DOM_REPS),
                                        "@DOMINANT", "@DOM ")
            time.sleep(0.5)
            rows = wit.take()
            node.take()
            ok, _m, lines = score_dominant(got, DOM_PULSE_US)
            verdicts.append(("dominant/timeout", ok and summ))
            for ln in got:
                print("    board> " + ln)
            for ln in lines:
                print("    " + ln)
            okw, lines = score_dom_witness(rows, DOM_REPS)
            verdicts.append(("dominant/witness", okw))
            for ln in lines:
                print("    " + ln)

        print("\n== verdicts")
        for name, ok in verdicts:
            print("  %-28s %s" % (name, verdict_word(ok)))
        rc, final = combine(verdicts)
        if final:
            print(final)
    finally:
        try:
            board.cmd("STOP")
            board.cmd("MODE listen")
        except Exception:                                  # noqa: BLE001
            pass
        board.close()
        if node:
            node.close()
        if wit:
            wit.close()
        if HOST is not None:
            HOST.close()
    return rc


# --- self-test: no hardware ------------------------------------------------

def _bframe(t, ident, counter):
    return (t, ident, False, bytes(counter.to_bytes(4, "little")
                                   + ident.to_bytes(4, "little")))


def _pframe(t, seq):
    return (t, NODE_ID, False, bytes(node_payload(seq)))


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    # classification
    check(board_frame(_bframe(0, 0x7F4, 3)), "a board frame is recognised")
    check(not board_frame(_pframe(0, 3)), "a node frame is not a board frame")
    check(not board_frame((0, 0x7F4, True, b"")),
          "an error frame is not a board frame")
    check(node_seq(_pframe(0, 70000)) == 70000, "the node seq round-trips")

    # 0 silence: the witness must be shown not to ACK, on the day
    sent1 = ("@SENT id=7EF ext=0 n=1 queued=1 refused=0 last_err=ESP_OK "
             "mode=normal bitrate=500k")
    st = ("@STATUS installed=1 state=RUNNING tec=%d rec=0 tx_failed=0 "
          "bus_err=16 arb_lost=0 msgs_to_tx=1 msgs_to_rx=0 mode=normal "
          "bitrate=500k")
    ok, ln = score_silence(sent1, st % 128)
    check(ok, "silence: tec 128 after one frame -> PASS (%s)" % ln[0])
    ok, ln = score_silence(sent1, st % 0)
    check(not ok and "ACKs" in ln[0],
          "silence: tec 0 (an ACKing witness, the 10-09 Kvaser) FAILS and "
          "refuses the run -> %s" % ln[0])
    ok, ln = score_silence(sent1, st % 120)
    check(not ok, "silence: tec 120, short of error passive, FAILS")
    ok, ln = score_silence(sent1, None)
    check(not ok and "no tec" in ln[0], "silence: no STATUS reply FAILS")
    ok, ln = score_silence(sent1.replace("queued=1 refused=0",
                                         "queued=0 refused=1"), st % 128)
    check(not ok and "did not queue" in ln[0],
          "silence: a refused probe (port still listen-only) FAILS rather "
          "than passing on a tec it never earned")
    check(status_field("@STATUS installed=1 tec=7 rec=0", "tec") == 7 and
          status_field("@STATUS installed=1 tec=x", "tec") is None,
          "status_field reads integers and rejects junk")
    import inspect
    wsrc = inspect.getsource(Witness) + '"""' + inspect.getsource(
        _open_witness_reader).split('"""')[2]
    # the node's bus is opened in its process function since 2026-10-16
    nsrc = inspect.getsource(_node_proc) + inspect.getsource(Node)
    wcode = wsrc.split('"""')[2] + wsrc.split('"""')[-1]
    check('interface="pcan"' in wcode and "BusState.PASSIVE" in wcode
          and "kvaser" not in wcode,
          "the witness is the PCAN, opened PASSIVE (not the Kvaser)")
    check('interface="kvaser"' in nsrc and "driver_mode=True" in nsrc,
          "the node is the Kvaser in normal mode")
    check("above_normal()" in inspect.getsource(_node_proc)
          and 'get_context("spawn")' in inspect.getsource(Node),
          "the node runs in its own (spawned) process at ABOVE_NORMAL")

    # 1 listen
    pc = [_pframe(i * 0.01 + 0.0001234, i) for i in range(500)]
    ok, ln = score_listen(pc, list(range(500)))
    check(ok, "listen: only the node, all seen -> PASS")
    ok, ln = score_listen(pc + [_bframe(2.0, 0x7F4, 0)], list(range(500)))
    check(not ok and any("board frame" in x for x in ln),
          "listen: ONE board frame FAILS -> %s" % ln[:1])
    ok, ln = score_listen(pc + [(2.0, 0, True, b"")], list(range(500)))
    check(not ok and any("error frame" in x for x in ln),
          "listen: one error frame FAILS")
    ok, ln = score_listen(pc[:-1], list(range(500)))
    check(not ok and any("never reached" in x for x in ln),
          "listen: one node frame missing FAILS")
    ok, ln = score_listen([], [])
    check(not ok, "listen: a node that sent nothing shows nothing and FAILS")

    # 2 bitrate
    errs = [(10.0 * b + 0.0005 * k + 0.0000371, 0, True, b"")
            for b in range(3) for k in range(16)]
    m, ln = score_bitrate(pc + errs, list(range(500)), 60.0)
    check(m["bursts"] == 3 and m["per_burst_p50"] == 16
          and abs(m["bursts_per_min"] - 3.0) < 1e-9
          and abs(m["node_share"] - 1.0) < 1e-9,
          "bitrate: 3 bursts of 16 in 60 s -> 3 / min, 16 per burst -> %s"
          % ln[0])
    m, ln = score_bitrate(pc, list(range(500)), 60.0)
    check(m["bursts"] == 0 and m["error_frames"] == 0,
          "bitrate: a log with no error frames reports no burst")
    m, ln = score_bitrate(pc[:250] + errs, list(range(500)), 60.0)
    check(abs(m["node_share"] - 0.5) < 1e-9,
          "bitrate: half the node frames seen -> share 50 %")
    # the 00:01 run's "397 of 79 (502.5 %)": the Kvaser's backlog from
    # earlier windows drains into this one. 79 queued and 20 refused this
    # window, 40 of the 79 seen, plus 397 seqs from an earlier window
    old = [_pframe(5.0 + i * 0.001, 10000 + i) for i in range(397)]
    mine = list(range(20000, 20079))
    seen40 = [_pframe(6.0 + i * 0.01, s) for i, s in enumerate(mine[:40])]
    m, ln = score_bitrate(old + seen40 + errs, mine, 60.0, node_fails=20)
    check(m["node_seen"] == 40 and m["node_tried"] == 99
          and m["node_late"] == 397
          and abs(m["node_share"] - 40 / 99.0) < 1e-9,
          "can-fail: earlier windows' backlog (397 seqs) is not counted, and "
          "refused sends are in the denominator -> 40 of 99 -> %s" % ln[0])

    # timing refusal
    coarse = [i * 0.001 for i in range(200)]
    ok, why = timing_ok(coarse, 0.01)
    check(not ok and "quantised to 1.000 ms" in why,
          "timing: 1 ms stamps are refused for the flood step -> %s" % why)
    ok, why = timing_ok(coarse, 1.0)
    check(ok, "timing: ...but are enough for burst grouping")
    # a fine clock under a host-paced sender: phases spread over the ms
    fine = [i * 0.01 + ((i * 7919) % 1000) * 1e-6 + 3e-7
            for i in range(200)]
    ok, why = timing_ok(fine, 0.01)
    check(ok, "timing: fine stamps pass -> %s" % why)
    ok, why = timing_ok(list(reversed(fine)), 0.01)
    check(not ok and "backwards" in why, "timing: stamps running backwards "
          "are refused")
    # step 2's tolerance (reviewer, 2026-10-16): backward steps up to 2 ms
    # each are accepted and recorded, larger ones refuse; step 3 strict
    g15 = [i * 0.01 for i in range(100)]
    g15[50] = g15[49] - 0.0015
    ok, why = timing_ok(g15, 1.0, back_tol_ms=BACK_TOL_MS)
    check(ok and "1 backward step(s), the largest 1.500 ms, tolerated" in why,
          "timing: step 2, a 1.5 ms backward step is accepted and recorded "
          "-> %s" % why)
    g25 = [i * 0.01 for i in range(100)]
    g25[50] = g25[49] - 0.0025
    ok, why = timing_ok(g25, 1.0, back_tol_ms=BACK_TOL_MS)
    check(not ok and "largest 2.500 ms" in why,
          "can-fail: timing: step 2, a 2.5 ms backward step is refused -> %s"
          % why)
    ok, why = timing_ok(g15, 0.01)
    check(not ok and "backwards" in why,
          "can-fail: timing: step 3 stays strict, a 1.5 ms backward step is "
          "refused")
    # gaps near the 10 ms burst threshold, and the flag
    nerr = [(t, 0, True, b"") for t in (1.0, 1.0005, 1.0100, 1.0300, 1.0405)]
    m, ln = score_bitrate(nerr, [], 60.0)
    check(m["near_threshold"] == 2 and "threshold: 2" in ln[1],
          "bitrate: gaps of 9.5 and 10.5 ms are counted near the threshold, "
          "0.5 and 20 ms are not -> %s" % ln[1])
    check(bitrate_flag(3, 2) is not None and bitrate_flag(0, 2) is None
          and bitrate_flag(3, 0) is None,
          "can-fail: FLAG only with backward steps AND near-threshold gaps")
    # step 3's settle, on a fake clock
    clk = [0.0]

    def fnow():
        return clk[0]

    def fsleep(s):
        clk[0] += s

    def fails_until(t_end):
        return lambda: 1 if clk[0] < t_end else 0
    clk[0] = 0.0
    ok, took = settle_node(lambda: 1, fnow, fsleep)
    check(not ok and took >= SETTLE_MAX_S,
          "can-fail: settle: a node that never stops failing is NEVER "
          "SETTLED after %.1f s (-> TEST FAULT)" % took)
    clk[0] = 0.0
    ok, took = settle_node(fails_until(3.0), fnow, fsleep)
    check(ok and 4.9 <= took <= 5.3,
          "settle: failing for 3 s, then clean -> settled after %.1f s" % took)
    clk[0] = 0.0
    ok, took = settle_node(lambda: 0, fnow, fsleep)
    check(ok and 1.9 <= took <= 2.2,
          "settle: a clean node settles after 2 s (%.1f s)" % took)

    # 3 flood
    base = [_pframe(i * 0.01 + 0.000003 * (i % 3), i) for i in range(1000)]
    flood = [_pframe(i * 0.01 + 0.000003 * (i % 3)
                     + (0.000230 if i % 2 else 0.0), i) for i in range(1000)]
    flood += [_bframe(i * 0.00025, 0x7F4, i) for i in range(4000)]
    ok, m, ln = score_flood(base, flood, 0x7F4)
    check(ok and 200e-6 < m["added_max"] < 260e-6,
          "flood: a 230 us push on alternate frames reads as ~230 us added "
          "-> %s" % ln[-1:])
    ok, m, ln = score_flood(base, base, 0x7F4)
    check(not ok and any("no flood" in x for x in ln),
          "flood: no flood frames on the bus FAILS the measurement")
    ok, m, ln = score_flood([], flood, 0x7F4)
    check(not ok and any("baseline" in x for x in ln),
          "flood: no node frames in the baseline FAILS")
    # node-side exclusions (reviewer, 2026-10-16): only the wire condition
    # excludes, and never hides board-caused delay
    bus4k = [_bframe(i * 0.00025, 0x7F4, i) for i in range(40000)]

    def nodes(times):
        return [_pframe(t, i) for i, t in enumerate(times)]
    sched = [i * 0.01 for i in range(1000)]
    held = list(sched)
    held[500] += 0.00025          # held one board frame, then sent
    ok, m, ln = score_flood(nodes(sched), nodes(held) + bus4k, 0x7F4)
    check(ok and m["flood_excluded"] == 0
          and 200e-6 < m["added_max"] < 300e-6,
          "can-fail: a node frame held one board frame and then sent is "
          "scored as arbitration delay (0 excluded, ~250 us added) -> %s"
          % ln[0])
    stall = list(sched)
    stall[500] = stall[499] + 0.0215      # a 21.5 ms gap, board frames in it
    stall[501] = stall[500] + 0.001       # then a back-to-back drain
    stall[502] = stall[501] + 0.001
    ok, m, ln = score_flood(nodes(sched), nodes(stall) + bus4k, 0x7F4)
    check(ok and m["flood_excluded"] == 3 and m["added_max"] < 50e-6
          and any("3 node frame(s) EXCLUDED" in x and "lateness" in x
                  for x in ln),
          "a 21.5 ms gap with board frames then a drain: its 3 frames are "
          "excluded and reported with their lateness, the scored delay is "
          "unchanged -> %s" % [x for x in ln if "flooding:" in x])
    ok, m, ln = score_flood(nodes(sched), nodes(stall), 0x7F4)
    check(m["flood_excluded"] == 0,
          "can-fail: the same gap with NO board frames in it is not excluded "
          "(scored)")
    skip = [t for i, t in enumerate(sched) if i not in (500, 501)]
    ok, m, ln = score_flood(nodes(sched), [_pframe(t, i) for i, t in
                                          zip([j for j in range(1000)
                                               if j not in (500, 501)],
                                              skip)] + bus4k, 0x7F4)
    check(m["flood_excluded"] == 0 and any("1 other gap(s)" in x for x in ln),
          "can-fail: a gap with board frames but no drain after it (frames "
          "missing, the next on schedule) excludes nothing")
    check(any("expected, not judged" in x and "0.25 ms" in x for x in ln),
          "flood: the expectation is stated, not judged")
    # the node's pacing loop, on a fake clock: lag and refused sends logged
    clk = [0.0]
    log = []

    def fsend(seq):
        if seq == 3:
            raise RuntimeError("queue full")
        if seq == 5:
            clk[0] += 0.030              # a 30 ms host stall in the send

    def fsleep(s):
        clk[0] += s
    node_loop(fsend, lambda: clk[0], fsleep, log.append,
              lambda: len(log) >= 10)
    check([x[2] for x in log] == [1, 1, 1, 0, 1, 1, 1, 1, 1, 1]
          and abs(log[6][1] - 0.020) < 1e-6 and abs(log[7][1] - 0.010) < 1e-6
          and abs(log[2][1]) < 1e-9,
          "node loop: a refused send is ok=0, and a 30 ms stall inside send 5 "
          "shows as send 6's lag (20 ms: 30 less one period) and send 7's "
          "(10 ms) -> lags %s"
          % ["%.3f" % x[1] for x in log])
    ln = window_load([_pframe(0.01 * i, i) for i in range(100)]
                     + [(0.5, 0, True, b"")], log, 1.0)
    check("10.0/s attempted, 9.0/s accepted" in ln and "100.0/s" in ln
          and "101/s" in ln and "1 error rows" in ln,
          "window load: send rate, accepted rate, wire rate and witness rows "
          "-> %s" % ln)

    # 4 stall
    st = [_bframe(0.1 + k * 0.0003, STALL_ID, k) for k in range(STALL_N)]
    ok, m, ln = score_stall(st, STALL_N, acked=True)
    check(ok, "stall, ACKed: exactly n frames -> PASS")
    ok, m, ln = score_stall(st + [_bframe(1.0, STALL_ID, STALL_N)], STALL_N,
                            acked=True)
    check(not ok and any("beyond" in x for x in ln),
          "stall: n+1 distinct frames FAIL (sent beyond the queued)")
    ok, m, ln = score_stall(st[:-1], STALL_N, acked=True)
    check(not ok and any("only" in x for x in ln),
          "stall, ACKed: n-1 frames FAIL")
    ok, m, ln = score_stall(st + st[:1], STALL_N, acked=True)
    check(not ok and any("repeated" in x for x in ln),
          "stall, ACKed: a repeat with a peer ACKing FAILS")
    retry = [_bframe(0.1 + j * 0.0003, STALL_ID, 0) for j in range(40)]
    ok, m, ln = score_stall(retry, STALL_N, acked=False)
    check(ok and m["retries"] == 39 and m["distinct"] == 1,
          "stall, no ACKer: one frame retried 40 times is reported, nothing "
          "beyond -> %s" % ln)
    ok, m, ln = score_stall(retry + [_bframe(1, STALL_ID, k)
                                     for k in range(1, STALL_N + 1)],
                            STALL_N, acked=False)
    check(not ok, "stall, no ACKer: n+1 distinct frames still FAIL")

    def dom(i, low, high, rel, by, pulse=DOM_PULSE_US):
        return ("@DOM i=%d pulse_us=%d rxd_low_us=%d rxd_high_us=%d "
                "released_us=%d by=%s" % (i, pulse, low, high, rel, by))
    good = [dom(i, 1, 1240 + i, 1240 + i, "transceiver") for i in range(20)]
    ok, m, ln = score_dominant(good, DOM_PULSE_US)
    check(ok and m["min_us"] == 1240 and m["median_us"] == 1250
          and m["max_us"] == 1259,
          "dominant: 20 transceiver releases, min/median/max 1240/1250/1259 "
          "us -> %s" % ln[-1])
    ok, m, ln = score_dominant(good[:19] + [dom(19, 1, -1, 5000, "none")],
                               DOM_PULSE_US)
    check(not ok, "dominant: one pulse not released within 5 ms FAILS")
    ok, m, ln = score_dominant(good[:19] + [dom(19, -1, -1, 5000, "none")],
                               DOM_PULSE_US)
    check(not ok and "never went low" in ln[0],
          "dominant: RXD never low (line not driven / not read) FAILS")
    short_ok = [dom(0, 1, 503, 500, "txd", pulse=500)]
    ok, m, ln = score_dominant(short_ok, 500, short=True)
    check(ok, "dominant can-fail: RXD high 3 us after a 500 us release "
          "passes -> %s" % ln[-1])
    ok, m, ln = score_dominant([dom(0, 1, 1250, 500, "transceiver",
                                    pulse=500)], 500, short=True)
    check(not ok, "dominant can-fail: a constant 1250 us (not tracking the "
          "line) FAILS")
    ok, m, ln = score_dominant([dom(0, 1, 300, 500, "txd", pulse=500)], 500,
                               short=True)
    check(not ok, "dominant can-fail: RXD high BEFORE the release FAILS")
    ok, m, ln = score_dominant([], DOM_PULSE_US)
    check(not ok, "dominant: no @DOM line FAILS")
    ev = [(10.0 + k * 0.1 + j * 0.0002, 0, True, b"") for k in range(20)
          for j in range(3)]
    ok, ln = score_dom_witness(ev, 20)
    check(ok and "20 error event(s)" in ln[-1],
          "dominant witness: 20 short events for 20 pulses pass -> %s"
          % ln[-1])
    longev = ev + [(30.0 + j * 0.0005, 0, True, b"") for j in range(11)]
    ok, ln = score_dom_witness(longev, 21)
    check(not ok and "5.0 ms" in ln[0],
          "dominant witness: a 5 ms run of error frames FAILS -> %s" % ln[0])
    ok, ln = score_dom_witness([], 20)
    check(ok and "0 error event(s)" in ln[-1],
          "dominant witness: no error frame at all is recorded, not failed")

    # (a) the reply suffix
    real = ("@DOM i=0 pulse_us=500 rxd_low_us=1 rxd_high_us=503 "
            "released_us=500 by=txd mode=listen bitrate=500k")
    d = parse_dom(real)
    check(d.get("rxd_high_us") == 503 and d.get("by") == "txd"
          and "mode" not in d, "parse_dom: the real line with its mode= / "
          "bitrate= suffix parses -> %s" % d)
    ok, m, ln = score_dominant([real.replace("rxd_high_us=503",
                                             "rxd_high_us=5x3")], 500,
                               short=True)
    check(not ok and "unreadable" in ln[0],
          "can-fail: a garbled @DOM value is refused, not crashed on -> %s"
          % (ln[0] if ln else ""))
    # (c) the no-ACK scorer
    v, ln = score_noack([_pframe(1.0 + k * 0.0003, 7) for k in range(50)])
    check(v == "unacked", "no-ACK: 50 retries of one sequence -> unacked")
    v, ln = score_noack([_pframe(1.0 + k * 0.01, 7 + k) for k in range(50)])
    check(v == "acked", "can-fail: 50 sequence numbers -> acked (the check "
          "sees an ACK)")
    v, ln = score_noack([_pframe(1.0, 7)])
    check(v == "fault", "no-ACK: one node row is a TEST FAULT, not a pass")
    v, ln = score_noack([])
    check(v == "fault", "no-ACK: no node row at all is a TEST FAULT")
    # (d) node faults
    class _N(object):
        window_fails = 3
    check(node_fault(_N(), "x") is not None and node_fault(NoNode(), "x")
          is None, "a window with failed node sends is a TEST FAULT")
    # (f) the witness survives a receive error (its reader in its own
    # process since F2, a fake bus in it)
    w = Witness(spec=(6, ((3, PCAN_QOVERRUN),)))
    time.sleep(0.6)
    got, inc = w.take(), w.take_incomplete()
    w.close()
    check(len(got) == 5 and inc == 1,
          "can-fail: a receive error mid-window is counted (%d) and the "
          "witness keeps reading (%d rows after it, not 0)" % (inc, len(got)))
    w.window_incomplete = inc
    check(witness_fault(w, "x") is not None,
          "a window with a witness receive error is a TEST FAULT")
    w.window_incomplete = 0
    check(witness_fault(w, "x") is None, "a complete window is not")

    # (f2) failures kept by text (diagnostic, reviewer 2026-10-23)
    q_, e_ = PCAN_QOVERRUN, 0x40000
    w = Witness(spec=(6, ((2, q_), (3, e_), (4, q_), (5, q_))))
    time.sleep(0.6)
    w.take()
    kinds, inc = w.take_failures(), w.take_incomplete()
    w.close()
    qo = kinds.get("PCAN read status 0x40: The receive queue was read too "
                   "late")
    bp = kinds.get("PCAN read status 0x40000: Bus error: the CAN controller "
                   "is error passive")
    check(inc == 4 and qo is not None and bp is not None and qo[0] == 3
          and bp[0] == 1,
          "can-fail: receive failures are counted by their text (3 queue, "
          "1 error-passive; got %s)" % sorted((k, v[0]) for k, v in
                                              kinds.items()))
    check(qo is not None and qo[1] < qo[2],
          "can-fail: a failure text keeps its first and last time")
    kk = {}
    note_failure(kk, "a", 10.0)
    note_failure(kk, "b", 10.5)
    note_failure(kk, "a", 12.0)
    fl = failure_lines(kk, 9.0)
    check(fl == ["witness receive failure x2: a (first +1.000 s, last "
                 "+3.000 s)", "witness receive failure x1: b (first +1.500 "
                 "s, last +1.500 s)"],
          "failure lines: most frequent first, times from the window start "
          "-> %s" % fl)
    check(parse_bitrate_order("250k,1M") == ["250k", "1M"]
          and parse_bitrate_order("1M, 250k") == ["1M", "250k"],
          "can-fail: --bitrate-order keeps the order it is given")
    for bad in ("1M", "1M,1M", "500k,1M", "250k,1M,250k", ""):
        try:
            parse_bitrate_order(bad)
            ok = False
        except ValueError:
            ok = True
        check(ok, "--bitrate-order %r refused" % bad)
    check(abs(cpu_busy_pct((100, 300, 100), (150, 400, 200)) - 75.0) < 1e-9,
          "can-fail: CPU busy = 1 - idle / (kernel + user) (75 %)")
    check(cpu_busy_pct((1, 1, 1), (1, 1, 1)) is None,
          "CPU busy with no time passed -> None")
    hs = host_summary([(0.5, 90.0, 80, 900.0), (1.0, 10.0, 40, 100.0),
                       (2.0, 30.0, 50, 200.0), (3.5, 99.0, 99, 999.0)],
                      1.0, 3.0)
    check(hs == "host during the window (2 samples): CPU mean 20 % / max "
               "30 %, memory load max 50 %, this process's working set "
               "max 200 MB",
          "can-fail: the host summary takes only the window's samples -> %s"
          % hs)
    check(host_summary([(0.5, 1.0, 1, 1.0)], 1.0, 2.0) == "",
          "no host sample in the window -> no line")
    if os.name == "nt":
        times, load, ws = HostSampler._read()
        check(min(times) > 0 and 1 <= load <= 100 and 1.0 < ws < 65536.0,
              "can-fail: the host read is plausible (CPU times > 0, memory "
              "load %d %%, this process's working set %.1f MB)" % (load, ws))

    # start refusals end VOID, never FAIL (reviewer, 2026-10-23)
    import re as _re
    _ok, sl = score_silence("@SENT id=7F0", "@STATUS tec=0 rec=0")
    for why, lines in (("the board's ELF sha256 a is not --elf's b", ()),
                       ("the image reports pins other than TX 7 / RX 6", ()),
                       ("the witness is not shown silent", sl)):
        out = start_refused(why, lines)
        judged = [ln for ln in out if _re.match(r"^\s*(FAIL|VOID)\b", ln)]
        check(bool(sl) and not _ok and judged == [out[-1]]
              and out[-1].startswith("VOID: REFUSED") and why in out[-1]
              and len(out) == len(lines) + 1,
              "can-fail: a start refusal (%s) ends on one VOID line and "
              "no line reads FAIL -> %s" % (why[:30], judged))

    # (F2) the reader is its own process at ABOVE_NORMAL, its windows
    # complete and in order with nothing leaking across a boundary
    w = Witness(spec=(600, ((150, PCAN_QOVERRUN), (450, PCAN_QOVERRUN)),
                      None, 2000.0))
    check(w.pid != os.getpid() and (os.name != "nt" or w.prio == 0x8000),
          "can-fail: the witness reader is its own process (pid %s, this "
          "one %d) at ABOVE_NORMAL (class %s)" % (w.pid, os.getpid(),
                                                  w.prio))
    wins = []
    for _k in range(8):
        time.sleep(0.08)
        wins.append([node_seq(r) for r in w.take()])
    time.sleep(0.3)
    wins.append([node_seq(r) for r in w.take()])
    nfail = w.take_incomplete()
    fk = w.take_failures()
    st = w.status_string()
    w.close()
    allseq = [x for win in wins for x in win]
    want = [x for x in range(1, 601) if x not in (150, 450)]
    check(allseq == want,
          "can-fail: every row read arrives once, in order (%d of %d, %d "
          "duplicates)" % (len(set(allseq)), len(want),
                           len(allseq) - len(set(allseq))))
    nonempty = [x for x in wins if x]
    check(len(nonempty) >= 3 and all(a[-1] < b[0] for a, b in
                                     zip(nonempty, nonempty[1:])),
          "can-fail: no row leaks across a window boundary (%d windows "
          "with rows)" % len(nonempty))
    check(nfail == 2 and fk.get("PCAN read status 0x40: The receive queue "
                                "was read too late", [0])[0] == 2,
          "can-fail: the reader's failures reach the bench process (%d, "
          "%s)" % (nfail, sorted(fk)))
    check(st == "fake bus OK", "the PCAN status is read through the reader "
          "-> %r" % st)
    w = Witness(spec=(50, (), 30))
    time.sleep(0.6)
    got = w.take()
    inc, fk = w.take_incomplete(), w.take_failures()
    w.window_incomplete = inc
    wf = witness_fault(w, "x")
    w.close()
    check(inc >= 1 and wf is not None and any("process" in t for t in fk),
          "can-fail: a reader that dies mid-window is a TEST FAULT, not a "
          "short window (%d row(s) kept, %s)" % (len(got), sorted(fk)))

    # the reader's own classification (2026-10-25): python-can's, except
    # that an overrun bit is a failure whatever else is set
    import logging
    QB = PCAN_QOVERRUN | PCAN_BUSHEAVY
    table = [(PCAN_OK, "row"), (PCAN_QRCVEMPTY, "empty"),
             (PCAN_QOVERRUN, "failure"), (PCAN_OVERRUN, "failure"),
             (QB, "failure"), (PCAN_QOVERRUN | PCAN_BUSLIGHT, "failure"),
             (PCAN_OVERRUN | PCAN_BUSHEAVY, "failure"),
             (PCAN_QOVERRUN | PCAN_QRCVEMPTY, "failure"),
             (PCAN_BUSHEAVY, "warning"), (PCAN_BUSLIGHT, "warning"),
             (PCAN_QRCVEMPTY | PCAN_BUSHEAVY, "warning"),
             (PCAN_ILLDATA, "ignored"), (0x40000, "failure"),
             (0x10, "failure")]
    got = [(hex(c), classify_read(c)) for c, _k in table]
    check(got == [(hex(c), k) for c, k in table],
          "can-fail: the reader's classification, an overrun with a bus-heavy "
          "bit a failure -> %s" % got)
    try:
        from can.interfaces.pcan import basic as pb
        from can.interfaces.pcan import pcan as pc
        import can as _can
    except Exception:                                      # noqa: BLE001
        pb = None
        print("  --    python-can not importable: its equivalence not "
              "checked here")
    if pb is not None:
        check((PCAN_OK, PCAN_OVERRUN, PCAN_BUSLIGHT, PCAN_BUSHEAVY,
               PCAN_QRCVEMPTY, PCAN_QOVERRUN, PCAN_ILLDATA,
               PCAN_MSG_ERRFRAME) ==
              (pb.PCAN_ERROR_OK, pb.PCAN_ERROR_OVERRUN, pb.PCAN_ERROR_BUSLIGHT,
               pb.PCAN_ERROR_BUSHEAVY, pb.PCAN_ERROR_QRCVEMPTY,
               pb.PCAN_ERROR_QOVERRUN, pb.PCAN_ERROR_ILLDATA,
               pb.PCAN_MESSAGE_ERRFRAME.value),
              "the status values are python-can's")

        class _OneRead(object):
            """PCAN-Basic giving one status, with real TPCAN structs."""

            def __init__(self, code, ident=0x100, mtype=0, data=b"",
                         ts=(0, 0, 0)):
                self.code = code
                self.msg = pb.TPCANMsg()
                self.msg.ID, self.msg.MSGTYPE = ident, mtype
                self.msg.LEN = len(data)
                for i, b in enumerate(data):
                    self.msg.DATA[i] = b
                self.ts = pb.TPCANTimestamp()
                (self.ts.millis, self.ts.millis_overflow,
                 self.ts.micros) = ts

            def Read(self, _h):
                return self.code, self.msg, self.ts

            @staticmethod
            def GetErrorText(code, _lang):
                return pb.PCAN_ERROR_OK, ("text %X" % code).encode()

        def pcan_says(fake):
            """python-can 4.6.1's own _recv_internal on fake: "row" and
            (t, id, err, data), "warning", "failure", or "nothing"."""
            bus = object.__new__(pc.PcanBus)
            bus.m_objPCANBasic, bus.m_PcanHandle = fake, 0
            bus._can_protocol = _can.CanProtocol.CAN_20
            bus.channel_info = "fake"
            seen = []
            h = logging.Handler()
            h.emit = lambda rec: seen.append(rec)
            lg = logging.getLogger("can.pcan")
            lg.addHandler(h)
            try:
                m, _f = bus._recv_internal(0)
            except _can.CanError:
                return "failure", None
            finally:
                lg.removeHandler(h)
            if m is not None:
                return "row", (m.timestamp, m.arbitration_id,
                               1 if m.is_error_frame else 0,
                               bytes(m.data)[:8])
            return ("warning" if seen else "nothing"), None

        same = []
        for c, _k in table:
            if c & (PCAN_OVERRUN | PCAN_QOVERRUN):
                continue
            mine = classify_read(c)
            same.append((hex(c), mine, pcan_says(_OneRead(c))[0]))
        check(all(t == {"empty": "nothing", "ignored": "nothing"}.get(m, m)
                  for _c, m, t in same),
              "can-fail: without an overrun bit the classification is "
              "python-can's own -> %s" % same)
        check(pcan_says(_OneRead(QB))[0] == "warning"
              and classify_read(QB) == "failure",
              "python-can 4.6.1 takes an overrun with BUSHEAVY for a warning "
              "(the row lost, nothing raised); the reader counts a failure")
        rows_same = []
        for ident, mtype, data, ts in (
                (0x100, 0, bytes(node_payload(7)), (1362714797, 0, 123)),
                (0x7F4, 0, b"\x01\x02", (4294967, 3, 999)),
                (0x0, PCAN_MSG_ERRFRAME, b"\x04\x00\x00\x00\x00\x00\x00\x00",
                 (88, 0, 1)),
                (0x18FF50E5, 0x02, b"", (5, 1, 0))):
            f1 = _OneRead(PCAN_OK, ident, mtype, data, ts)
            ref = pcan_says(f1)[1]
            rd = _PcanReader(f1, 0, None, None, pc.boottimeEpoch,
                             None, None)
            rows_same.append(rd.read(0)[1] == ref)
        check(all(rows_same),
              "can-fail: the reader's rows are python-can's, field for field "
              "(stamp, id, error frame, data; millis_overflow included) -> "
              "%s" % rows_same)
        fast_same = []
        for ident, mtype, data, ts in (
                (0x100, 0, bytes(node_payload(7)), (1362714797, 0, 123)),
                (0x7F4, 0, b"\x01\x02", (4294967, 3, 999)),
                (0x0, PCAN_MSG_ERRFRAME, b"\x04\x00\x00\x00\x00\x00\x00\x00",
                 (88, 0, 1)),
                (0x18FF50E5, 0x02, b"", (5, 1, 0))):
            f1 = _OneRead(PCAN_OK, ident, mtype, data, ts)
            ref = pcan_says(f1)[1]
            dm, dt = pb.TPCANMsg(), pb.TPCANTimestamp()

            class _Dll(object):
                def __init__(self, src):
                    self.src = src

                def CAN_Read(self, _h, _pm, _pt):
                    import ctypes
                    ctypes.memmove(ctypes.addressof(dm),
                                   ctypes.addressof(self.src.msg),
                                   ctypes.sizeof(dm))
                    ctypes.memmove(ctypes.addressof(dt),
                                   ctypes.addressof(self.src.ts),
                                   ctypes.sizeof(dt))
                    return PCAN_OK
            dr = _DirectRead(_Dll(f1), dm, dt)
            rd = _PcanReader(dr, 0, None, None, pc.boottimeEpoch, None,
                             None, "fast")
            got = rd.read(0)[1]
            fast_same.append(got == ref)
        check(all(fast_same),
              "can-fail: the fast reader's rows (CAN_Read on kept structs) are "
              "python-can's, field for field -> %s" % fast_same)

    # an overrun with a bus-heavy bit, through the reader process: counted,
    # its text kept, the window a TEST FAULT that names it
    w = Witness(spec=(6, ((3, QB),)))
    time.sleep(0.6)
    rows_ = w.take()
    wk = w.take_failures()
    w.window_incomplete = w.take_incomplete()
    w.window_fail_top = max(wk, key=lambda x: wk[x][0]) if wk else ""
    wf = witness_fault(w, "1M idle")
    w.close()
    check(len(rows_) == 5 and w.window_incomplete == 1 and wf is not None
          and "receive queue was read too late" in wf,
          "can-fail: a queue overrun with BUSHEAVY in the reader is a "
          "receive failure; the window is a TEST FAULT naming it (%d rows, "
          "%s)" % (len(rows_), wf))
    # bus-state warnings, counted where the PCAN is and forwarded per cut
    w = Witness(spec=(6, ((2, PCAN_BUSHEAVY), (4, PCAN_BUSLIGHT))))
    time.sleep(0.6)
    rows_, inc = w.take(), w.take_incomplete()
    pw, pwlast = w.take_warnings()
    pw2 = w.take_warnings()[0]
    w.close()
    check(len(rows_) == 4 and inc == 0 and pw == 2 and "light" in pwlast
          and pw2 == 0,
          "can-fail: the PCAN's bus-state warnings reach the bench process, "
          "per window (%d, last %r), and are no receive failure (%d)"
          % (pw, pwlast, inc))

    # (e) reply pairing
    class _Ser(object):
        def __init__(self, lines):
            self.lines = [x.encode("ascii") + b"\n" for x in lines]

        def write(self, b):
            pass

        def flush(self):
            pass

        def readline(self):
            return self.lines.pop(0) if self.lines else b""
    bd = Board(None, ser=_Ser(["@STOPPED id=649 queued=1 mode=listen",
                               "@MODE driver=ESP_OK mode=listen"]))
    r = bd.cmd("MODE listen", timeout=0.5)
    check(r is not None and r.startswith("@MODE") and len(bd.stale) == 1,
          "can-fail: a late @STOPPED before MODE's reply is recorded stale, "
          "and MODE gets its own @MODE")
    bd = Board(None, ser=_Ser(["@ERR MODE normal|listen"]))
    check((bd.cmd("MODE x", timeout=0.5) or "").startswith("@ERR"),
          "an @ERR is taken as the command's reply")
    check(reply_tag("DRIVER off") == "@DRIVER", "DRIVER's reply tag")
    ok, v = judge_offpair([("1M", "off", 0, []), ("1M", "listen", 900, []),
                           ("1M", "off", 0, [])])
    check(not ok and v.startswith("BOARD-SIDE"),
          "off-pair: errors only listen-only -> BOARD-SIDE")
    ok, v = judge_offpair([("1M", "off", 0, []), ("1M", "listen", 0, []),
                           ("1M", "off", 0, [])])
    check(ok, "off-pair: clean everywhere -> clean")
    ok, v = judge_offpair([("1M", "off", 0, []), ("1M", "listen", 900, []),
                           ("1M", "off", 40, [])])
    check(not ok and v.startswith("NOT THE BOARD ALONE"),
          "can-fail: errors in an off window too -> not the board alone")
    ok, v = judge_offpair([("1M", "off", 0, ["TEST FAULT: x"]),
                           ("1M", "listen", 900, []), ("1M", "off", 0, [])])
    check(ok is FAULT and "NOT JUDGED" in v,
          "off-pair: a faulted window (e.g. the board's state not shown) "
          "is not judged: a TEST FAULT, not a FAIL")
    ok, v = judge_offpair([("1M", "listen", 900, [])])
    check(not ok and "NOT JUDGED" in v, "off-pair: missing windows are not "
          "judged")

    # recstart, on rec_reg
    def _st(rec_reg, mode="listen", rec=None, tec=0, tec_reg=0):
        return ("@STATUS installed=1 state=running tec=%d rec=%d tec_reg=%d "
                "rec_reg=%s bus_err=0 rx_frames=10 mode=%s bitrate=500k"
                % (tec, rec if rec is not None else 0, tec_reg, rec_reg,
                   mode))
    can_a = _st(0, "normal", rec=0, tec=128, tec_reg=128)
    can_b = _st(130, "normal", rec=130)

    def _starts(v, n=3, k=6):
        return [[("r%d" % j, _st(v)) for j in range(k)] for _i in range(n)]

    def _node(vals):
        return [("n%d" % j, _st(x)) for j, x in enumerate(vals)]
    ok, v, _l = judge_recstart(can_a, _starts(128), _node([128] * 6), can_b,
                               [])
    check(ok and "FROZEN at 128" in v, "recstart: 128 at start, 128 under "
          "traffic -> write takes, frozen -> %s" % v)
    ok, v, _l = judge_recstart(can_a, _starts(128),
                               _node([125, 118, 98, 30, 0, 0]), can_b, [])
    check(ok and "NOT FROZEN" in v, "recstart: 128, then walked down -> not "
          "frozen -> %s" % v)
    ok, v, _l = judge_recstart(can_a, _starts(128), _node([0] * 6), can_b, [])
    check(ok and "SOMETHING ELSE" in v and "STOP" in v, "recstart: 128, then "
          "0 at once -> something else, stop")
    ok, v, _l = judge_recstart(can_a, _starts(0), _node([0] * 6), can_b, [])
    check(ok and "NEVER TAKES" in v, "recstart: 0 at every start -> the "
          "write never takes")
    st = _starts(128)
    st[1][5] = ("r5", _st(0))
    ok, v, _l = judge_recstart(can_a, st, _node([128] * 6), can_b, [])
    check(ok and v.startswith("NEITHER"), "recstart: 128 then 0 within the "
          "quiet 2 s -> neither, stop")
    ok, v, _l = judge_recstart(_st(0, "normal", rec=0, tec=128, tec_reg=0),
                               _starts(0), _node([0] * 6), can_b, [])
    check(not ok and "wiring check A" in v, "can-fail: tec_reg != tec in normal "
          "mode -> not judged")
    ok, v, _l = judge_recstart(can_a, _starts(0), _node([0] * 6),
                               _st(0, "normal", rec=130), [])
    check(not ok and "wiring check B" in v, "can-fail: rec_reg 0 while the API "
          "reads 130 in normal mode -> not judged (a register read that "
          "always says 0 is caught)")
    ok, v, _l = judge_recstart(can_a, _starts(0), _node([0] * 6),
                               _st(0, "normal", rec=0), [])
    check(not ok and "wiring check B" in v, "can-fail: no REC at all in the "
          "normal-mode window -> not judged")
    ok, v, _l = judge_recstart(can_a, _starts(0), _node([0] * 6), can_b,
                               [(1.0, 0x123, False, b"")])
    check(not ok and "not quiet" in v, "can-fail: a frame on CAN1 during "
          "the starts -> not judged")
    st = _starts(0)
    st[0][0] = ("r0", "@STATUS installed=1 rec=0 mode=listen")
    ok, v, _l = judge_recstart(can_a, st, _node([0] * 6), can_b, [])
    check(not ok and "NOT JUDGED" in v, "can-fail: a STATUS without rec_reg "
          "(the old image) -> not judged")
    st = _starts(0)
    st[2][0] = ("r0", None)
    ok, v, _l = judge_recstart(can_a, st, _node([0] * 6), can_b, [])
    check(not ok and "NOT JUDGED" in v, "can-fail: a failed MODE (STATUS "
          "dropped) -> not judged")

    check(all(reply_tag(c) == t for c, t in (("STOP", "@STOPPED"),
                                              ("FLOOD 7F4", "@FLOODING"),
                                              ("SEND 7EF 1", "@SENT"),
                                              ("HELLO?", "@HELLO"))),
          "each command's reply tag")

    # 2026-10-21: --listen-arms (the reviewer's KNOWN-FAIL split)
    names = [n for n, _ in listen_subs(listen_arm_keys("all"))]
    check(names == ["idle", "bitrate 250k + flood 7F4",
                    "bitrate 1M + flood 7F4", "flood 7F4", "flood 649",
                    "stall %d" % STALL_N],
          "arms: all gives the six windows of before, in order")
    rest = listen_arm_keys("no-ack,idle,flood-7F4,flood-649,stall")
    names = [n for n, _ in listen_subs(rest)]
    check("no-ack" in rest and not any(n.startswith("bitrate") for n in names)
          and "flood 7F4" in names and len(names) == 4,
          "arms: the suite's vport-listen has no wrong-rate window")
    wrong = [n for n, _ in listen_subs(listen_arm_keys("wrong-250k,wrong-1m"))]
    check(wrong == ["bitrate 250k + flood 7F4", "bitrate 1M + flood 7F4"]
          and "no-ack" not in listen_arm_keys("wrong-250k,wrong-1m"),
          "arms: the known-fail check has exactly the two wrong-rate windows")
    check(listen_arm_keys("stall,idle") == ["idle", "stall"],
          "arms: run in the fixed order whatever the order given")
    for spec in ("flood-7f4", "", "idle,bogus"):
        try:
            listen_arm_keys(spec)
            ok = False
        except ValueError:
            ok = True
        check(ok, "arms: %r is refused" % spec)

    # TEST FAULT arms (reviewer, 2026-10-23, c40b's vport-bitrate)
    rc, ln = combine([("bitrate/1M/idle", FAULT)])
    check(rc == 1 and ln.startswith("VOID:"), "a fault-only run is VOID")
    rc, ln = combine([("bitrate/1M/idle", FAULT), ("flood/7F4", False)])
    check(rc == 1 and ln.startswith("FAIL: flood/7F4"),
          "a fault plus a valid FAIL is FAIL")
    rc, ln = combine([("bitrate/1M/idle", FAULT), ("stall/acked", True),
                      ("dominant/timeout", "summary")])
    check(rc == 1 and ln.startswith("VOID:"),
          "a fault plus valid PASSes is VOID, never PASS")
    rc, ln = combine([("stall/acked", True), ("dominant/timeout", "summary")])
    check(rc == 0 and ln is None, "all PASS: rc 0 and no line")
    rc, ln = combine([("stall/acked", True), ("dominant/timeout", None)])
    check(rc == 1 and ln.startswith("FAIL: dominant/timeout"),
          "a falsy verdict that is not a fault is a FAIL")
    check(verdict_word(FAULT) == "VOID (TEST FAULT)" and
          verdict_word(True) == "PASS" and verdict_word(False) == "FAIL",
          "the verdict words")
    v, ln = refused_arm("only 4 witness stamp(s)")
    rc, final = combine([("bitrate/250k/idle", v)])
    check(rc == 1 and final.startswith("VOID:") and ln.startswith("REFUSED"),
          "a timing-REFUSED-only run is VOID (the witness's record, not the "
          "board)")
    rc, final = combine([("flood", refused_arm("x")[0]), ("stall/acked", True)])
    check(rc == 1 and final.startswith("VOID:"),
          "a REFUSED flood baseline beside a valid PASS is VOID, never PASS")
    suite_rx = re.compile(r"^\s*(FAIL|VOID)\b")   # bench_suite._lines
    check(not any(suite_rx.search(x) for x in
                  unjudged(["FAIL: 66 error frame(s)", "  FAIL x"])),
          "a faulted arm's scorer lines do not read as FAIL to the suite")

    # the window-edge diagnostic and the m1 step (2026-10-25, diagnostic
    # only; reviewer: listen/idle's 2 of 1001, and the user's ruling on (c))
    nclk, nlog = [0.0], []

    def nsend(seq):
        if seq == 5:
            nclk[0] += 0.030

    def nsleep(s):
        nclk[0] += s
    node_loop(nsend, lambda: nclk[0], nsleep, nlog.append,
              lambda: len(nlog) >= 8)
    check(all(len(x) == 5 and x[4] >= x[3] for x in nlog)
          and abs((nlog[5][4] - nlog[5][3]) - 0.030) < 1e-6
          and abs(nlog[4][4] - nlog[4][3]) < 1e-9,
          "node loop: each entry carries the send's call and return times "
          "(send 5's 30 ms stall between them, send 4 none)")
    elog = [(q, 0.0, 1, 100.0 + 0.01 * (q - 848), 100.002 + 0.01 * (q - 848))
            for q in range(848, 1849)]
    erows = [_pframe(0.01 * q, q) for q in range(846, 1847)]
    epost = [_pframe(0.01 * q, q) for q in range(1847, 1851)]
    t_wc = 100.002 + 0.01 * 1000 + 0.004
    el = edge_summary(elog, erows, epost, [(1849, 0.0, 1, 0, 0)], t_wc,
                      t_wc + 0.0021)
    check(el[0].startswith("node sent 1001 (848..1848), witness holds 1001 "
                           "(846..1846); 2 missing, 2 of them in the "
                           "witness's rows after its cut; 2 held")
          and "node cut 2.1 ms after" in el[0]
          and el[1] == ("missing seq 1847: send returned 14.0 ms before the "
                        "witness's cut; in the witness's next rows")
          and "held, not in the node's log: seq 846" in el
          and el[-1] == "after the cut: 4 witness rows, 1 node sends logged",
          "edges: the 2-of-1001 case, both missing frames found after the "
          "cut, the early seqs listed, the cut times -> %s" % el[:2])
    el = edge_summary(elog, erows, [], [], t_wc, t_wc)
    check("2 missing, 0 of them" in el[0]
          and el[2].endswith("NOT in the witness's next rows"),
          "can-fail: edges: missing frames absent after the cut are NOT found")
    el = edge_summary(elog[:-2], erows, [], [], t_wc, t_wc)
    check("0 missing" in el[0], "edges: nothing missing reads 0 missing")
    ed = tempfile.mkdtemp(prefix="bvst_")
    try:
        g = globals()
        keep, g["RAW_DIR"] = g["RAW_DIR"], None
        check(save_rows(erows[:3], "x") is None,
              "save_rows: no folder, nothing written")
        sv = save_rows(erows[:3], "local flood", ed)
        n0 = _raw_n[0]
        save_edges(["head line"], elog[:2], epost[:1], [], ed)
        ef = os.path.join(ed, "%02d_edges.txt" % n0)
        etext = open(ef, encoding="utf-8").read() if os.path.isfile(ef) \
            else ""
        g["RAW_DIR"] = keep
        check(sv is not None and os.path.dirname(sv[0]) == ed
              and sv[1] == os.path.getsize(sv[0]) and sv[1] > 0
              and sv[0].endswith("_local_flood.txt"),
              "save_rows: rows_dir is used, the bytes and write time "
              "returned -> %s" % (sv,))
        check(etext.startswith("head line\n")
              and "# witness rows after the cut\n" in etext
              and ("%X 0 %s" % (NODE_ID, bytes(node_payload(1847)).hex()))
              in etext and "848 0.000 1 100.000000 100.002000" in etext,
              "save_edges: the lines, the send log with its times and the "
              "rows after the cut, beside the window's rows")
    finally:
        shutil.rmtree(ed, ignore_errors=True)
    check([a[0] for a in parse_m1_arms(",".join(M1_ARMS))] == list(M1_ARMS)
          and parse_m1_arms("settle-local") == [("settle-local", True, True)]
          and parse_m1_arms("nosettle-run") == [("nosettle-run", False,
                                                 False)],
          "m1 arms: each arm's settle and rows folder")
    for txt in ("settle-elsewhere", "", "nosettle-run,bogus"):
        try:
            parse_m1_arms(txt)
            ok = False
        except ValueError:
            ok = True
        check(ok, "can-fail: m1 arms %r refused" % txt)
    mrows = ([(0.01 * k, 0, True, b"") for k in range(100)]
             + [(3.0 + 0.01 * k, 0, True, b"") for k in range(500)])
    try:
        mm = m1_idle_metrics(list(reversed(mrows)))
    except Exception as exc:                               # noqa: BLE001
        mm = {"rows": None, "error": fail_text(exc)}
    check(mm["rows"] == 600 and abs(mm["head_rate"] - 80.0) < 1e-6
          and abs(mm["gap_ms"] - 2010.0) < 1e-3
          and abs(mm["gap_at"] - 0.99) < 1e-6 and mm["low_bins"] == 4,
          "m1 metrics: a 1 s burst, a 2 s hole, then steady (given in "
          "reverse order) -> %s" % mm)
    mm = m1_idle_metrics(mrows)
    ln = m1_trial_line(3, "settle-run", 1700000, ("p", 50500000, 1.25), mm,
                       "TEST FAULT: x", ["1M idle, diagnostic: y"], 2)
    check(ln.startswith("m1 trial 3 settle-run: flood 1700000 rows, file "
                        "50.5 MB written in 1.25 s, flood window complete; 1M "
                        "idle 600 rows")
          and "WITNESS FAULT: TEST FAULT: x" in ln and ln.endswith(
              "2 PCAN warning(s); 1M idle, diagnostic: y"),
          "m1 trial line: flood size, the idle window's figures, the fault")
    check("witness complete" in m1_trial_line(1, "a", 0, None, mm, "", [], 0),
          "m1 trial line: no fault reads complete")
    ln = m1_trial_line(1, "a", 5, None, mm, "", [], 0,
                       flood_wf="TEST FAULT: z")
    check("flood window WITNESS FAULT: TEST FAULT: z" in ln
          and "witness complete" in ln,
          "m1 trial line: the flood window's own fault shown apart")
    check("flood window complete" in m1_trial_line(1, "a", 0, None, mm, "",
                                                   [], 0),
          "m1 trial line: no flood fault reads complete")
    check(WITNESS_READER == "fast" and Witness.__init__.__defaults__[-1]
          is None,
          "the witness reader is fast by default (run fd23)")

    # windows cut by the witness's stamps (user rulings 2026-10-25: the
    # vport-listen windows, then every witness window)
    slog = [(q, 0.0, 1, 200.0 + 0.01 * (q - 848),
             200.0006 + 0.01 * (q - 848)) for q in range(848, 1849)]
    ssent = list(range(848, 1849))

    def sp(q, d=0.0005):
        return _pframe(200.0 + 0.01 * (q - 848) + d, q)
    s_pre = [sp(845), sp(848)]               # 848 read before the start take
    s_cut = [sp(q) for q in range(846, 848)] + [sp(q) for q in
                                                 range(849, 1847)]
    s_drain = ([sp(1847), sp(1848, 0.025)]     # 1848 queued: +25 ms
               + [sp(q) for q in range(1849, 1852)] + [sp(1860)])
    lo, hi = stamp_bounds(slog, 0.0, 1.0)
    check(abs(lo - 199.995) < 1e-9 and abs(hi - 210.1006) < 1e-9,
          "stamp window: the node's first send call - 5 ms to its last "
          "return + 100 ms -> %.4f..%.4f" % (lo, hi))
    check(stamp_bounds([], 3.0, 4.0) == (3.0, 4.0),
          "stamp window: no node log -> the bench's own window")
    lo_c, hi_c = stamp_bounds(slog, 0.0, 1.0, 199.95)
    check(abs(lo_c - 199.95) < 1e-9 and abs(hi_c - hi) < 1e-9
          and stamp_bounds(slog, 0.0, 1.0, 199.999) == (lo, hi)
          and stamp_bounds([], 3.0, 4.0, 3.5) == (3.0, 4.0),
          "stamp window from the command: starts at the command when it "
          "is earlier than the first send - 5 ms, else as before -> "
          "%.4f..%.4f" % (lo_c, hi_c))
    # run 6019's vport-stall: the 16 frames go out at the command, before
    # the node's first send
    burst = [_bframe(199.96 + 0.0003 * k, STALL_ID, k) for k in range(16)]
    k_c, _i = assign_by_stamp([], burst + [sp(848)], [], lo_c, hi_c)
    ok_c, _m, ln_c = score_stall(k_c, STALL_N, acked=True)
    k_n, i_n = assign_by_stamp([], burst + [sp(848)], [], lo, hi)
    ok_n, _m, ln_n = score_stall(k_n, STALL_N, acked=True)
    check(ok_c and not ok_n and i_n[3] == 16
          and "only 0 of the 16" in ln_n[0],
          "stall burst before the node's first send: kept from the command "
          "(%s), 16 dropped from the node's send (%s)" % (ln_c[0], ln_n[0]))
    # through window(): the board stamps its burst at the command, its
    # reply comes 60 ms later, the node's sends start 30 ms after the command
    class _CB(object):
        t = None

        def cmd(self, c, *a):
            self.t = time.perf_counter()
            time.sleep(0.060)
            return "@OK"

    class _CW(object):
        def __init__(self, b):
            self.b, self.n, self.stats = b, 0, None

        def take(self):
            self.n += 1
            if self.n != 2:
                return []
            return [_bframe(self.b.t + 0.0005 + 0.0003 * k, STALL_ID, k)
                    for k in range(16)]

        def take_incomplete(self):
            return 0

    class _CN(object):
        def __init__(self, b):
            self.b, self.n = b, 0

        def take(self):
            return []

        def take_fails(self):
            return 0

        def take_log(self):
            self.n += 1
            if self.n != 2:
                return []
            t0 = self.b.t + 0.030
            return [(q, 0.0, 1, t0 + 0.01 * q, t0 + 0.01 * q + 0.0006)
                    for q in range(10)]
    for fc in (True, False):
        b = _CB()
        try:
            rows_w, _s = window(b, _CW(b), _CN(b), 0.0, "STALL 16",
                                from_command=fc)
            ok_w, _m, ln_w = score_stall(rows_w, STALL_N, acked=True)
        except Exception as exc:                           # noqa: BLE001
            ok_w, ln_w = "an exception", [fail_text(exc)]
        check(ok_w is fc,
              "%sa STALL window through window(), from_command=%s -> %s"
              % ("" if fc else "can-fail: ", fc, ln_w[0]))
    import inspect
    src = inspect.getsource(run_bench) + inspect.getsource(listen_window)
    check(src.count("from_command=True") == 2
          and "from_command=bool(cmds)" in src,
          "the stall step's two windows and the listen windows with a "
          "command start at the command; no other window does")

    def sl(pre, cut, drain):
        k, info = assign_by_stamp(pre, cut, drain, lo, hi)
        return score_listen(k, ssent), info
    (ok, ln), info = sl(s_pre, s_cut, s_drain)
    ok_old, ln_old = score_listen(s_cut, ssent)
    check(ok and info == (1, 998, 5, 3, 1) and not ok_old
          and "3 of 1001 node frame(s) never reached" in ln_old[-1],
          "stamp window: a planted split at both edges (848 read before the "
          "start take, 1847-1848 after the cut, 846-847 stamped before) "
          "passes; the cut alone FAILs -> %s, %s / %s" % (ok, info, ln_old))
    (ok, ln), _i = sl(s_pre, [r for r in s_cut if node_seq(r) != 1300],
                      s_drain)
    check(not ok and "1 of 1001 node frame(s) never reached the witness, "
          "first seq 1300" in ln[-1],
          "can-fail: stamp window: a real loss mid-window FAILs -> %s" % ln)
    (ok, ln), _i = sl(s_pre, s_cut,
                      [r for r in s_drain if node_seq(r) != 1848])
    check(not ok and "first seq 1848" in ln[-1],
          "can-fail: stamp window: a real loss at the end FAILs -> %s" % ln)
    (ok, ln), _i = sl([sp(845)], s_cut, s_drain)
    check(not ok and "first seq 848" in ln[-1],
          "can-fail: stamp window: a real loss at the start FAILs -> %s" % ln)
    (ok, ln), _i = sl(s_pre, s_cut, [sp(1847)] + [_pframe(hi + 1.0, 1848)])
    check(not ok and "first seq 1848" in ln[-1],
          "can-fail: stamp window: a frame stamped after the window is not "
          "its -> %s" % ln)
    (ok, ln), _i = sl(s_pre, s_cut, s_drain + [_bframe(205.0, 0x7F4, 1)])
    check(not ok and "board frame" in ln[0],
          "can-fail: stamp window: a board frame read after the cut but "
          "stamped in the window FAILs -> %s" % ln[0])
    (ok, ln), _i = sl(s_pre + [(lo - 0.5, 0x2, True, b"\x01")], s_cut,
                      s_drain)
    check(ok, "stamp window: an error row stamped before the window is not "
          "its -> %s" % ln)
    st1 = reader_stats(10, 20, 0.001, 0.5, 1.0, 0.0002, 0.012, 0.001)
    st2 = reader_stats(5, 9, 0.001, 0.2, 0.5, 0.0001, 0.400, 0.002)
    mg = merge_stats(st1, None, st2)
    check(mg["rows"] == 15 and mg["reads"] == 29 and mg["lag_max"] == 0.4
          and mg["lag_min"] == 0.0001 and mg["lag_end"] == 0.002
          and merge_stats(None, None) is None,
          "reader figures: the cut's and the drain's merged -> %s" % mg)
    check(reader_fault(st1) == "" and reader_fault(None) == ""
          and "fell behind" in reader_fault(mg)
          and "BEFORE its stamp" in reader_fault(
              reader_stats(1, 1, 0, 0, 1, -0.02, 0.001, 0.001)),
          "can-fail: reader guard: a 400 ms lag and a stamp 20 ms ahead "
          "fault; 12 ms does not")

    class _SB(object):
        def cmd(self, c, *a):
            return "@OK"

    class _SW(object):
        """A witness whose takes are scripted: (rows, stats) each."""

        def __init__(self, takes):
            self.takes, self.stats = list(takes), None

        def take(self):
            rows, self.stats = self.takes.pop(0) if self.takes else ([],
                                                                     None)
            return rows

        def take_incomplete(self):
            return 0

        def take_failures(self):
            return {}

        def take_warnings(self):
            return 0, ""

    class _SN(object):
        def __init__(self, log):
            self.sends, self.logs = [[], list(ssent)], [[], list(log)]

        def take(self):
            return self.sends.pop(0) if self.sends else []

        def take_fails(self):
            return 0

        def take_log(self):
            return self.logs.pop(0) if self.logs else []
    good = reader_stats(1000, 1200, 0.01, 9.0, 10.0, 0.0003, 0.013, 0.001)
    behind = reader_stats(1000, 1000, 0.5, 0.0, 10.0, 0.4, 2.2, 0.5)
    ahead = reader_stats(1000, 1200, 0.01, 9.0, 10.0, -0.05, 0.013, 0.001)
    for what, takes, want in (
            ("the planted split", [(s_pre, None), (s_cut, good),
                                   (s_drain, good)], True),
            ("a real loss", [(s_pre, None), (s_cut[:500] + s_cut[501:], good),
                             (s_drain, good)], False),
            ("a reader 2.2 s behind (the m1 burst)",
             [(s_pre, None), (s_cut, behind), (s_drain, good)], FAULT),
            ("a reader behind in the drain only",
             [(s_pre, None), (s_cut, good), (s_drain, behind)], FAULT),
            ("stamps 50 ms ahead of the clock",
             [(s_pre, None), (s_cut, ahead), (s_drain, good)], FAULT)):
        try:
            sw = _SW(takes)
            ok, ln = listen_window(_SB(), sw, _SN(slog), 0.0, "idle", None)
        except Exception as exc:                           # noqa: BLE001
            ok, ln = "an exception", [fail_text(exc)]
        check(ok is want if want is FAULT else ok == want,
              "%sa listen window through window(), %s -> %s, %s" % (
                  "" if want is True else "can-fail: ", what,
                  verdict_word(ok) if ok in (True, False, FAULT) else ok,
                  ln[0] if ln else ""))
    # a real witness on the fake PCAN: a reader that falls behind, and one
    # that keeps up, in both modes
    for mode, delay, want in (("classic", 0.0, False), ("fast", 0.0, False),
                              ("classic", 0.001, True)):
        w = Witness(spec=(4000, (), None, 2000.0, True, delay), mode=mode)
        time.sleep(1.2)
        got = w.take()
        stw = w.stats
        w.close()
        rf = reader_fault(stw)
        check(bool(rf) == want and len(got) > 0
              and (stw or {}).get("rows") == len(got),
              "%sa %s reader at %.0f us per read against 2000 rows/s: %s "
              "(lag max %s s, %d rows)" % (
                  "can-fail: " if want else "", mode, delay * 1e6,
                  "faulted" if rf else "kept up",
                  "%.3f" % stw["lag_max"] if stw and stw["lag_max"]
                  is not None else "-", len(got)))
        if want:
            check(got and got[-1][0] < time.perf_counter() - 0.3,
                  "can-fail: the slow reader's window is short: its last "
                  "row was stamped %.2f s before the cut"
                  % (time.perf_counter() - got[-1][0]))
    w = Witness(spec=(400, (), None, 2000.0, True, 0.0, 0.05))
    time.sleep(0.5)
    w.take()
    rf = reader_fault(w.stats)
    w.close()
    check("BEFORE its stamp" in rf,
          "can-fail: stamps 50 ms ahead of the reader's clock fault the "
          "window -> %s" % rf)
    w = Witness(spec=(600, ((150, PCAN_QOVERRUN), (450, PCAN_QOVERRUN)),
                      None, 2000.0), mode="fast")
    wins = []
    for _k in range(8):
        time.sleep(0.08)
        wins.append([node_seq(r) for r in w.take()])
    time.sleep(0.3)
    wins.append([node_seq(r) for r in w.take()])
    nfail = w.take_incomplete()
    w.close()
    allseq = [x for win in wins for x in win]
    check(allseq == [x for x in range(1, 601) if x not in (150, 450)]
          and nfail == 2 and sum(1 for x in wins if x) >= 3,
          "can-fail: the fast reader: every row once and in order, across "
          "windows, its 2 failures counted (%d rows, %d failures)"
          % (len(allseq), nfail))

    print("\nbench_vport self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("unknown arms accepted",
     "    if bad or not keys:\n        raise ValueError",
     "    if False:\n        raise ValueError"),
    ("given order kept",
     "    return [k for k in LISTEN_ARMS if k in keys]",
     "    return keys"),
    ("wrong-rate windows always run",
     "        if k in keys:\n            subs.append((\"bitrate %s + flood 7F4\"",
     "        if True:\n            subs.append((\"bitrate %s + flood 7F4\""),
    ("flood arms always run",
     "        if \"flood-%X\" % i in keys:",
     "        if True:"),
    ("idle never runs",
     "    if \"idle\" in keys:\n        subs.append((\"idle\", None))",
     "    if False:\n        subs.append((\"idle\", None))"),
    ("a TEST FAULT arm counts as a PASS",
     "    faults = [n for n, ok in verdicts if ok is FAULT]",
     "    faults = []"),
    ("a TEST FAULT arm is a FAIL (the rule before 2026-10-23)",
     "    fails = [n for n, ok in verdicts if ok is not FAULT and not ok]",
     "    fails = [n for n, ok in verdicts if ok is FAULT or not ok]"),
    ("a TEST FAULT outranks a valid FAIL",
     "    if fails:\n        return 1, \"FAIL: %s%s\"",
     "    if fails and not faults:\n        return 1, \"FAIL: %s%s\""),
    ("a faulted arm's scorer lines left unmarked",
     "    return [\"not judged (the window faulted): \" + ln for ln in lines]",
     "    return list(lines)"),
    ("a timing REFUSED counted as a FAIL",
     "    return FAULT, \"REFUSED, not judged",
     "    return False, \"REFUSED, not judged"),
    ("diag: failures collapsed into one kind",
     "                note_failure(kinds, v, time.perf_counter())",
     "                note_failure(kinds, \"failure\", time.perf_counter())"),
    ("F2: the reader drops rows",
     "                n += 1\n                since += 1",
     "                n += 1\n                since += 1\n                if n % 97 == 0:\n                    f.seek(-_ROW.size, 1)"),
    ("F2: a failure not forwarded",
     "conn.send((\"cut\", k, n, kinds, fails, warns, wlast, st))",
     "conn.send((\"cut\", k, n, {}, 0, warns, wlast, st))"),
    ("F2: rows leak across a window boundary",
     "                                 \"wb\", 1 << 20)\n                    elif",
     "                                 \"wb\", 1 << 20)\n                        f.write(open(os.path.join(folder, \"w%06d.bin\" % (k - 1)), \"rb\").read())\n                    elif"),
    ("F2: the reader runs in-process",
     "        self.proc = ctx.Process(target=_witness_proc, args=(",
     "        self.proc = threading.Thread(target=_witness_proc, args=("),
    ("F2: the reader not at ABOVE_NORMAL",
     "    prio = above_normal()\n    try:\n        reader = _open_witness_reader(",
     "    prio = None\n    try:\n        reader = _open_witness_reader("),
    # the reader's own classification and decode (2026-10-25)
    ("reader: python-can's order (an overrun with BUSHEAVY a warning)",
     "    if result & (PCAN_OVERRUN | PCAN_QOVERRUN):\n        return \"failure\"\n    if result == PCAN_QRCVEMPTY:\n        return \"empty\"\n    if result & (PCAN_BUSLIGHT | PCAN_BUSHEAVY):\n        return \"warning\"",
     "    if result == PCAN_QRCVEMPTY:\n        return \"empty\"\n    if result & (PCAN_BUSLIGHT | PCAN_BUSHEAVY):\n        return \"warning\"\n    if result & (PCAN_OVERRUN | PCAN_QOVERRUN):\n        return \"failure\""),
    ("reader: a queue overrun only a warning",
     "    if result & (PCAN_OVERRUN | PCAN_QOVERRUN):\n        return \"failure\"",
     "    if result & (PCAN_OVERRUN | PCAN_QOVERRUN):\n        return \"warning\""),
    ("reader: the controller's overrun not checked",
     "    if result & (PCAN_OVERRUN | PCAN_QOVERRUN):",
     "    if result & PCAN_QOVERRUN:"),
    ("reader: warnings not forwarded",
     "conn.send((\"cut\", k, n, kinds, fails, warns, wlast, st))",
     "conn.send((\"cut\", k, n, kinds, fails, 0, \"\", st))"),
    ("reader: the TEST FAULT line without the failure's text",
     "                % (what, n, top or \"no failure text kept\"))",
     "                % (what, n, \"queue overflow\"))"),
    ("reader: stamps without millis_overflow",
     "                 + 0x100000000 * 1000 * ts.millis_overflow)",
     "                 + 0)"),
    ("reader: the error-frame flag lost",
     "                          1 if msg.MSGTYPE & PCAN_MSG_ERRFRAME else 0, d)",
     "                          0, d)"),
    ("F2: a dead reader is a short window, not a fault",
     "            self._gone(\"died or stopped answering (exit code %s)\"\n                       % getattr(self.proc, \"exitcode\", None))",
     "            pass"),
    ("diag: a failure's first time moves",
     "        k[0] += 1\n        k[2] = t",
     "        k[0] += 1\n        k[1] = k[2] = t"),
    ("diag: --bitrate-order ignored",
     "    return got\n",
     "    return list(BITRATE_RATES)\n"),
    ("diag: CPU busy counts idle",
     "    return 100.0 * (tot - di) / tot",
     "    return 100.0 * di / tot"),
    ("diag: working set read in bytes",
     "pmc.ws / 1048576.0", "float(pmc.ws)"),
    ("diag: host summary ignores the window",
     "    w = [x for x in samples if t0 <= x[0] <= t1]",
     "    w = list(samples)"),
    ("start refusal: no VOID line",
     "            [\"VOID: REFUSED, no step run: %s\" % why])",
     "            [\"REFUSED, no step run: %s\" % why])"),
    ("start refusal: the refused check's lines unmarked",
     "    return ([\"not judged (the run is refused): \" + ln for ln in lines] +",
     "    return (list(lines) +"),
    ("a faulted off-pair window judged a FAIL",
     "        return FAULT, \"NOT JUDGED (a window faulted)\"",
     "        return False, \"NOT JUDGED (a window faulted)\""),
    ("edges: the rows after the cut not searched",
     "    found = [q for q in missing if q in later]",
     "    found = []"),
    ("edges: the next rows marked found regardless",
     "                        \"in the witness's next rows\" if q in later",
     "                        \"in the witness's next rows\" if True"),
    ("node log: the return time taken before the send",
     "        put((seq, tc - (t0 + seq * period), ok, tc, now()))",
     "        put((seq, tc - (t0 + seq * period), ok, tc, tc))"),
    ("save_rows: rows_dir ignored",
     "    d = rows_dir or RAW_DIR\n    if not d:\n        return None",
     "    d = RAW_DIR\n    if not d:\n        return None"),
    ("m1: unknown arms accepted",
     "    if bad or not got:\n        raise ValueError(\"--m1-arms",
     "    if False:\n        raise ValueError(\"--m1-arms"),
    ("m1: the idle window's stamps not sorted",
     "    ts = sorted(r[0] for r in rows)\n    out = {\"rows\"",
     "    ts = [r[0] for r in rows]\n    out = {\"rows\""),
    # stamp windows and the reader (user rulings 2026-10-25)
    ("stamp: rows after the cut ignored",
     "    c = [r for r in drained if lo <= r[0] <= hi]",
     "    c = []"),
    ("stamp: every row after the cut kept",
     "    c = [r for r in drained if lo <= r[0] <= hi]",
     "    c = list(drained)"),
    ("stamp: rows stamped before the window kept",
     "    b = [r for r in rows if lo <= r[0] <= hi]",
     "    b = [r for r in rows if r[0] <= hi]"),
    ("stamp: the start's take thrown away",
     "    a = [r for r in pre if lo <= r[0] <= hi]",
     "    a = []"),
    ("stamp: no tail after the last send",
     "STAMP_TAIL_S = 0.100", "STAMP_TAIL_S = 0.0"),
    ("stamp: the bench's window, not the node's sends",
     "    if log:\n        lo = min(x[3] for x in log)",
     "    if False:\n        lo = min(x[3] for x in log)"),
    ("stamp: a window from the command starts at the first send",
     "        if t_cmd is not None:\n            lo = min(lo, t_cmd)",
     "        if False:\n            lo = min(lo, t_cmd)"),
    ("stamp: every window from the command",
     "                          t_cmd if from_command else None)",
     "                          t_cmd)"),
    ("stamp: the stall step not from the command",
     "                              from_command=True)\n",
     "                              from_command=False)\n"),
    ("stamp: the command's time taken after its reply",
     "        t_cmd = time.perf_counter()\n        board.cmd(command)\n",
     "        board.cmd(command)\n        t_cmd = time.perf_counter()\n"),
    ("stamp: the drain not read",
     "    drained = wit.take()\n    st_drain",
     "    drained = []\n    st_drain"),
    ("reader: a lagging reader passes",
     "    if st[\"lag_max\"] > READER_LAG_TOL_S:",
     "    if st[\"lag_max\"] > 1e9:"),
    ("reader: stamps ahead of the clock pass",
     "    if st[\"lag_min\"] < -STAMP_CLOCK_TOL_S:",
     "    if st[\"lag_min\"] < -1e9:"),
    ("reader: witness_fault ignores the stamp fault",
     "    sf = getattr(wit, \"window_stamp_fault\", \"\")",
     "    sf = \"\""),
    ("reader: lag not measured",
     "                lag = now() - t\n",
     "                lag = 0.0\n"),
    ("reader: the drain's figures dropped",
     "    wit.window_reader = merge_stats(st_cut, st_drain)",
     "    wit.window_reader = merge_stats(st_cut)"),
    ("fast reader: LEN ignored",
     "                d = bytes(msg.DATA)[:msg.LEN]",
     "                d = bytes(msg.DATA)"),
    ("m1: the flood window's fault not shown",
     "               (\"flood window WITNESS FAULT: \" + flood_wf) if flood_wf\n",
     "               (\"flood window WITNESS FAULT: \" + flood_wf) if False\n"),
    ("the classic reader by default",
     'WITNESS_READER = "fast"',
     'WITNESS_READER = "classic"'),
    ("m1: a witness fault not shown in the trial line",
     "               (\"WITNESS FAULT: \" + wf) if wf else \"witness complete\",",
     "               \"witness complete\","),
]


def main():
    global EDGE_DIAG, WITNESS_READER
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--port", help="the X2's USB Serial/JTAG port")
    ap.add_argument("--elf", help="build/vport_bench.elf, checked against the "
                                  "board's @HELLO")
    ap.add_argument("--src-sha", default="(not given)",
                    help="git log -1 --format=%%h -- firmware/test/vport_bench")
    ap.add_argument("--steps", default="listen,bitrate,flood,stall,dominant")
    ap.add_argument("--seconds", type=float, default=10.0,
                    help="each listen / flood window")
    ap.add_argument("--bitrate-order", default="250k,1M",
                    help="step 2's rates in this order (diagnostic, "
                         "reviewer 2026-10-23); default 250k,1M")
    ap.add_argument("--bitrate-seconds", type=float, default=60.0,
                    help="each wrong-bit-rate window (bursts per minute)")
    ap.add_argument("--kvaser-channel", type=int, default=0)
    ap.add_argument("--no-acker-ready", action="store_true",
                    help="the WiCAN is off or passive: run step 4's "
                         "no-ACKer variant with the node (Kvaser) closed")
    ap.add_argument("--listen-arms", default="all",
                    help="step 1's arms: all, or a comma list of %s"
                         % ", ".join(LISTEN_ARMS))
    ap.add_argument("--diag-edges", action="store_true",
                    help="diagnostic: after each window, the node's send "
                         "log and the witness's rows after the cut "
                         "(NN_edges.txt; reviewer 2026-10-25)")
    ap.add_argument("--witness-reader", default=WITNESS_READER,
                    choices=READER_MODES,
                    help="the witness reader: fast (direct CAN_Read) or "
                         "classic (python-can's PCANBasic.Read); default %s"
                         % WITNESS_READER)
    ap.add_argument("--m1-trials", type=int, default=4,
                    help="step m1 (diagnostic): trials")
    ap.add_argument("--m1-arms", default=",".join(M1_ARMS),
                    help="step m1: arms in turn, a comma list of %s"
                         % ", ".join(M1_ARMS))
    ap.add_argument("--m1-settle-s", type=float, default=10.0,
                    help="step m1: the settle arms' wait after BITRATE 1M")
    ap.add_argument("--m1-pre-s", type=float, default=10.0,
                    help="step m1: the 250k idle window before the flood")
    ap.add_argument("--m1-flood-s", type=float, default=60.0,
                    help="step m1: the 250k flood window")
    ap.add_argument("--m1-idle-s", type=float, default=20.0,
                    help="step m1: the 1M idle window")
    ap.add_argument("--out", default=None)
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test under each of MUTANTS")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    try:
        listen_arm_keys(args.listen_arms)
        args.bitrate_rates = parse_bitrate_order(args.bitrate_order)
        parse_m1_arms(args.m1_arms)
    except ValueError as e:
        ap.error(str(e))
    EDGE_DIAG = args.diag_edges
    WITNESS_READER = args.witness_reader
    if not args.port:
        ap.error("--port is required for a bench run")
    # with no --out, the default output goes where it went before the
    # move: SeaDrive's interposer-firmware/ folder, never this tree
    import paths
    here = None if args.out else paths.fixtures()
    out = args.out or os.path.join(here, "bench_vport_%s.txt"
                                   % datetime.datetime.now()
                                   .strftime("%Y%m%dT%H%M%S"))
    import bench_charger_witness as bcw
    tee = bcw.Tee(out)
    sys.stdout = tee
    try:
        rc = run_bench(args)
    finally:
        sys.stdout = tee.out
        tee.f.close()
        print("saved verbatim to %s" % out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
