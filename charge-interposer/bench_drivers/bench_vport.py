"""What the X2's vehicle port can put on the vehicle segment by itself:
spec 9's bench measurements, host side (step 4 prep).

The board runs the vport_bench image (charge-interposer/fw_harness/
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
  1 listen  the port in listen-only under each of 3-4 below: the witness
            must see no board frame and no error frame, and every node
            frame must get through. Not at a wrong bit rate: spec 9 leaves
            it out of the listen-only cases (user, 2026-10-16 and
            2026-11-27), so the wrong-250k and wrong-1m arms are gone
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

THE NODE ON KVASER_USB (tester, 2026-11-13; the charge-interposer
reviewer's A, D3, D5 and its ruling on the no-ACK windows the same day).
The Kvaser is netdev kvaser. An UP link acknowledges with no socket open,
so "the node closed" is its link DOWN, read back in sysfs: step 0 takes it
down before the probe (a link that does not go down REFUSES the run, VOID
with the kernel's lines), NoNode does too, and Node.start() brings it up.
A node frame is SENT when its own echo came back within 100 ms of send()
returning (window_sent), counted in the window of its send CALL, and
stamp_bounds is unchanged. Where the node's frames are expected to
complete (an ACKer present, the board at 500k) a kvaser TX stall (ENOBUFS,
or an echo later than 100 ms) makes the run VOID with the kernel's lines,
and the Kvaser is recovered (stall_report). In the no-ACK windows (step
1's no-ack arm and its can-fail) and the wrong-rate ones (1b discrim,
1c offpair, step 2, m1) the rule is suspended
(Node.expect_acks(False)): ENOBUFS is a refused send, as CANlib's full
queue was, and an unechoed frame is not sent. The node starts with the rule
suspended, since step 0 leaves nothing to acknowledge it, and each step
turns it on once its ACKer is on the bus (the reviewer's ruling (2),
2026-11-20: run 40e6's six "0 echoed" stalls). When the ACK returns, every
frame still held must echo within 100 ms of that moment, flushed before
the next window starts (node_sender), else a stall; the flush's frames are
kept out of the next window's node share, timing and settle rule
(own_node_rows) and recorded as "flushed from <window>: n".

The charge-interposer reviewer's rulings of 2026-11-20 (Q1, Q3 and the
stall-16 item):
- The node's link comes up with restart-ms NODE_RESTART_MS (100), read
  back, on every link_up (Node.start, and the recovery after a stall): after
  a bus-off in a wrong-rate window it recovers and keeps trying, as a
  vehicle node does. A wrong-rate span is marked expect_acks(False,
  wrong_rate=True); frames the kernel dropped while the node was bus-off in
  its windows never echo, and the flush when the ACK returns forgives that
  many (node_sender, EchoWatch.finish(forgive)) and no more. window() reads
  the node's link counters before and after (kvaser_link.link_counters,
  can-tools' bench_layer.link_counters; the first read before the window
  starts, since one read takes 0.67-0.77 s on the bench and the node sends
  meanwhile) and judge_node_link says what they
  mean: a wrong-rate window reports its bus-offs, restarts, tx_dropped and
  tx_aborted_errors and adds tx_dropped's growth to the forgiveness; a
  window expecting the node's frames to complete is VOID on a node bus-off
  (node_fault; the stall step's acked window and recstart's node traffic
  too). A bus-off that drops a frame there also stalls the run, as D5 says,
  and the VOID record then gives the node's counters since it started.
- score_noack (step 1's no-ack arm and its can-fail) also counts the
  window's rows of the node's head frame, the one unechoed as the window
  starts and retried through it, whatever window sent it (noack_rows);
  every other earlier frame stays out, and the share, timing and settle
  checks never see it.
- A node frame is the window's own when its seq is in the window's send log
  OR among its sent seqs: the log is taken at the cut, and a send called
  just before the cut may have its echo counted while its log entry is
  still on its way (C3's stall 16, seq 5211). The rows kept out are named
  by seq on the window's line.

The charge-interposer reviewer's ruling (a), 2026-11-20: sends from a
wrong-rate span are the effect being measured, so the flush does not judge
them. The node process is told of the span (Node.wrong_ev, set before its
first send there), node_sender notes each send it makes there with no ACK
expected, and at the flush EchoWatch.finish(excuse) excuses every one of
them still unechoed, however many; tx_dropped's count (Q1) still forgives
the others, and more of those is the stall, as before. The flush prints
each wrong-rate window's figures (span_figures): its sends never echoed, by
send-call time; tx_dropped's growth over it; and the first less the second,
its sends neither echoed nor dropped, so its share figure can be read
against them. The stall rule where the node's frames are expected to
complete is unchanged, and so is the wrong-rate window's TEST FAULT when
the node's sends failed.

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
import shutil
import sys
import struct
import threading
import time

# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never from
# the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter_roles               # noqa: E402  (every adapter by its bench role)
import kvaser_link                 # noqa: E402  (the Kvaser on kvaser_usb)
import scratch_dir                  # noqa: E402

BUS_BITRATE = 500000
NODE_ID = 0x100                  # above 0x649 and 0x7F4 in priority
NODE_MAGIC = 0x5A
NODE_PERIOD_S = 0.010
# the node's bus-off restart (the reviewer's ruling Q1, 2026-11-20): set and
# read back on every link_up of the node, so after a bus-off in a wrong-rate
# window it recovers and keeps trying, as a vehicle node does
NODE_RESTART_MS = 100
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


# Step 2's windows are judged only when the witness's kept stamps cover
# them (the charge-interposer reviewer, 2026-11-21): the first stamp within
# COVER_EDGE_S of the window's start, the last within COVER_EDGE_S of its
# end, no gap between stamps over COVER_GAP_S. Basis, every step-2 window
# judged with full coverage in b467's vp1-vp3 (12 windows, Windows) and the
# Linux hand takes E5 (4), E1, E3, D3 (3 each), C4 and B1 (250k flood):
# largest gap 21.4 ms (E3 250k flood; the node sends every 10 ms), first
# stamp at most 0.03 s after the start, last at most ~0.02 s before the end
# (the 0.01 s figures of the runs' diagnostic lines). The bounds are over 10
# times those. The 1M flooding windows of C4, D3, E1 and E3 cover 0 to 0.05
# s of 60 s: E3's was judged on them.
COVER_EDGE_S = 0.25
COVER_GAP_S = 0.25


def coverage_ok(ts, lo, hi, edge_s=COVER_EDGE_S, gap_s=COVER_GAP_S):
    """-> (ok, why): the window [lo, hi] (witness-stamp time) covered by the
    stamps ts, its first within edge_s of lo, its last within edge_s of hi,
    no gap between two over gap_s. No bounds or no stamps: not covered."""
    if lo is None or hi is None:
        return False, "the window's bounds in witness-stamp time are unknown"
    ts = sorted(ts)
    if not ts:
        return False, "no witness stamps in the window"
    head, tail = ts[0] - lo, hi - ts[-1]
    gap, at = max(((b - a, a - lo) for a, b in zip(ts, ts[1:])),
                  default=(0.0, 0.0))
    why = ("witness stamps %.3f..%.3f s of the window's %.3f s, largest gap "
           "%.3f s at +%.3f s (bounds: %.2f s at each end, %.2f s between "
           "stamps)" % (head, ts[-1] - lo, hi - lo, gap, at, edge_s, gap_s))
    if head > edge_s or tail > edge_s or gap > gap_s:
        return False, "the witness's stamps do not cover the window: " + why
    return True, why


def bitrate_stamps(rows):
    """Step 2's timing stamps: the error frames', or every row's with
    none."""
    return [x[0] for x in rows if x[2]] or [x[0] for x in rows]


def bitrate_judgeable(rows, bounds):
    """Step 2: may this window be judged? -> (ok, why, cover line). Its
    stamps (bitrate_stamps) must resolve 1 ms (timing_ok), and every kept
    row's stamps must cover the window, bounds = (lo, hi) in witness-stamp
    time (coverage_ok, 2026-11-21); why is the first refusal's reason."""
    t_ok, why = timing_ok(bitrate_stamps(rows), 1.0, back_tol_ms=BACK_TOL_MS)
    c_ok, cwhy = coverage_ok([x[0] for x in rows], *(bounds or (None, None)))
    if t_ok and not c_ok:
        t_ok, why = c_ok, cwhy
    return t_ok, why, cwhy


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


def noack_rows(rows, node):
    """score_noack's rows: the window's rows (its own node frames, D3) and
    the window's rows of the node's head frame, the one unechoed when the
    window started and retried through it, whatever window sent it (the
    charge-interposer reviewer's ruling Q3, 2026-11-20; window() keeps them
    aside as node.window_head_rows). Every other frame from an earlier
    window stays out, and the share, timing and settle checks never see the
    head frame's rows. On Linux nothing acknowledges the node from its
    start, so its head frame is a frame sent before the window, and with it
    kept out the arm read "only 0 node frame(s)" (11-20, C3 and C5; 40e6)."""
    return sorted(list(rows) + list(getattr(node, "window_head_rows", [])),
                  key=lambda r: r[0])


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
# A declared STOP outcome (recstart's SOMETHING ELSE and NEITHER): VOID as a
# fault is, under its own name (the reviewer's sweep ruling 3, 2026-11-21).
STOP_OUTCOME = object()


def verdict_word(ok):
    return ("VOID (TEST FAULT)" if ok is FAULT else
            "VOID (declared STOP)" if ok is STOP_OUTCOME else
            "PASS" if ok else "FAIL")


# Why step 4's no-ACKer variant is not run without --no-acker-ready
NO_ACKER_WHY = ("needs --no-acker-ready (the WiCAN off or passive; the node "
                "is closed)")


def verdict_lines(verdicts, not_run=()):
    """The '== verdicts' section: one line per arm, then, when a variant was
    not run, one line for it with why and a RESULT line naming it (the
    suite puts a PASS's RESULT line in its detail). The reviewer's sweep
    ruling 4 (2026-11-21): vport-stall stays, with the reason its no-ACKer
    variant is not run in the verdict line. The RESULT line names no arm
    verdict, so accept_adapter's vport-windows reading of a report (its
    arms, its final line, every TEST FAULT line) is unchanged."""
    out = ["  %-28s %s" % (n, verdict_word(ok)) for n, ok in verdicts]
    out += ["  %-28s NOT RUN: %s" % (n, why) for n, why in not_run]
    if not_run:
        out.append("RESULT: %d arm(s) run; NOT RUN: %s" % (len(verdicts), "; ".join(
            "%s (%s)" % (n, why) for n, why in not_run)))
    return out


def combine(verdicts):
    """[(name, ok)] -> (rc, final line or None). A valid FAIL anywhere ->
    rc 1 and a FAIL line; else any FAULT -> rc 1 and a VOID line (the
    suite's script_judge reads a VOID line with no FAIL line as VOID);
    else rc 0. The lines start in column 0 so the suite's judge finds
    them. A declared STOP outcome (STOP_OUTCOME) counts as a fault does,
    named apart (sweep ruling 3, 2026-11-21)."""
    faults = [n for n, ok in verdicts if ok is FAULT]
    stops = [n for n, ok in verdicts if ok is STOP_OUTCOME]
    fails = [n for n, ok in verdicts if ok is not FAULT and not ok]
    nots = (["TEST FAULT, not measured: %s" % ", ".join(faults)] if faults
            else []) + (["declared STOP, not judged: %s" % ", ".join(stops)]
                        if stops else [])
    if fails:
        return 1, "FAIL: %s%s" % (", ".join(fails),
                                  "".join("; " + x for x in nots))
    if faults or stops:
        return 1, ("VOID: %s, and no arm FAILs on a valid measurement"
                   % "; ".join((["TEST FAULT on %s" % ", ".join(faults)]
                                if faults else []) +
                               (["declared STOP on %s" % ", ".join(stops)]
                                if stops else [])))
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
    -> (judged, verdict, lines): judged True for a declared result,
    STOP_OUTCOME for a STOP (stop_outcome), FAULT for NOT JUDGED
    (not_judged); both of the last end their lines on a VOID line."""
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
        return not_judged("NOT JUDGED (wiring check A: the registers do not "
                          "match the API in normal mode, or no TEC to "
                          "compare)", lines)
    if None in (rb, b_api) or b_api == 0 or rb != b_api:
        return not_judged("NOT JUDGED (wiring check B: no REC in normal mode, "
                          "or rec_reg differs from the API's rec)", lines)
    if quiet_rows:
        return not_judged("NOT JUDGED (CAN1 was not quiet: %d row(s) on the "
                          "witness during the starts)" % len(quiet_rows), lines)
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
                return not_judged("NOT JUDGED (start %d, %s: no rec_reg, or "
                                  "not installed listen-only)" % (k + 1, label),
                                  lines)
            vals.append(r)
        sv.append(vals)
    if not sv or not all(sv):
        return not_judged("NOT JUDGED (no reads after a start)", lines)
    nv = []
    for label, st in node_reads:
        r = status_field(st, "rec_reg")
        lines.append("node traffic, %-20s rec_reg=%s rx_frames=%s"
                     % (label, r, status_field(st, "rx_frames")))
        if r is None or hello_field(st, "mode") != "listen":
            return not_judged("NOT JUDGED (node read %s)" % label, lines)
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
        return stop_outcome("THE WRITE TAKES (128 at every start); SOMETHING "
                            "ELSE under node traffic %s -- STOP" % nv, lines)
    if all(v == 0 for v in flat):
        return True, ("THE WRITE NEVER TAKES on this silicon (rec_reg 0 at "
                      "every read after %d starts)" % len(sv)), lines
    return stop_outcome("NEITHER: rec_reg after the starts %s -- STOP" % sv,
                        lines)


def stop_outcome(text, lines):
    """A declared STOP outcome of recstart (SOMETHING ELSE, NEITHER) ->
    (STOP_OUTCOME, its verdict, its lines with a VOID line carrying the
    text). The reviewer's sweep ruling 3 (2026-11-21): it is VOID with its
    text, not exit 0."""
    return STOP_OUTCOME, "VOID (declared STOP): " + text, lines + [
        "VOID: recstart's declared STOP outcome, not judged: " + text]


def not_judged(text, lines):
    """recstart's premise not met (a wiring check, CAN1 not quiet, a read
    missing) -> (FAULT, its verdict, its lines with a VOID line carrying the
    text). The reviewer's sweep, lower priority (2026-11-21), at this
    driver's next change: a premise failure prints a VOID line; before, it
    was a FAIL."""
    return FAULT, "VOID (TEST FAULT): " + text, lines + [
        "VOID: recstart's premise not met: " + text]


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
PCAN_BUSOFF = 0x10
PCAN_BUSPASSIVE = 0x40000
PCAN_MSG_ERRFRAME = 0x40
PCAN_MSG_STATUS = 0x80      # a status message: a state change, not a frame
# the bits of a Read result that, alone (with or without QRCVEMPTY), make
# it a bus state or an overrun returned in place of a message: can-tools'
# CanbenchPcanBus test (canbench/live/pcan_bus.py, _STATE_RESULT)
PCAN_STATE_RESULT = (PCAN_BUSLIGHT | PCAN_BUSHEAVY | PCAN_BUSOFF
                     | PCAN_BUSPASSIVE | PCAN_OVERRUN | PCAN_QOVERRUN)
WITNESS_POLL_ROWS = 256     # rows read between two looks at the command pipe
_STATE_OF = {}              # status word -> pcan_state's answer


def pcan_state(word):
    """A PCAN status word -- a bus state Read returned, or a status
    message's 4 data bytes, MSB first -> ("state", name) or ("failure",
    name), by can-tools' own decoding (canbench.errors.from_pcan_status,
    lost_frames, decode; the charge-interposer reviewer, 2026-11-20).
    PCAN-Basic for Linux hands status messages up as Read results (CAN
    replay, from the 9.3.3 source), so a state is not a lost frame:
    - frames lost (an overrun bit, errors.lost_frames) -> "failure", as
      before;
    - bus-off -> "failure", as before: a bus-off witness receives nothing,
      so its window would read the node's frames as never arrived;
    - any other state -> "state", named as can-tools names it (active,
      warning, passive; "light" for BUSLIGHT alone, which it leaves
      state-less)."""
    res = _STATE_OF.get(word)
    if res is None:
        E = adapter_roles.errors()
        em = E.from_pcan_status(word)
        st = E.decode(em).state
        if E.lost_frames(em):
            res = ("failure", "frames lost" + (" (%s)" % st if st else ""))
        elif st == "busoff":
            res = ("failure", "bus-off")
        else:
            res = ("state", st or "light")
        _STATE_OF[word] = res
    return res


# PCAN-Basic's status bits by name, for the PCAN's own status string
PCAN_BIT_NAMES = ((0x1, "XMTFULL"), (PCAN_OVERRUN, "OVERRUN"),
                  (PCAN_BUSLIGHT, "BUSLIGHT"), (PCAN_BUSHEAVY, "BUSHEAVY"),
                  (PCAN_BUSOFF, "BUSOFF"), (PCAN_QRCVEMPTY, "QRCVEMPTY"),
                  (PCAN_QOVERRUN, "QOVERRUN"), (0x80, "QXMTFULL"),
                  (PCAN_BUSPASSIVE, "BUSPASSIVE"))


def pcan_status_text(word):
    """A PCAN-Basic GetStatus word -> its text, never empty: "OK", or the
    word in hex with every set bit by name (any bit not in PCAN_BIT_NAMES
    in hex) and, for state and overrun bits only, can-tools' reading
    (pcan_state). python-can's status_string looks the word up whole and
    returns None for a combination such as 0x40008 (passive and bus heavy),
    so three of the hand take E3's four windows printed no status
    (2026-11-20)."""
    if word == PCAN_OK:
        return "OK"
    names = [n for b, n in PCAN_BIT_NAMES if word & b]
    rest = word
    for b, _n in PCAN_BIT_NAMES:
        rest &= ~b
    if rest:
        names.append("other 0x%X" % rest)
    txt = "0x%X %s" % (word, "+".join(names))
    st = word & ~PCAN_QRCVEMPTY
    if st and not st & ~PCAN_STATE_RESULT:
        txt += " (%s)" % pcan_state(st)[1]
    return txt


def pcan_status_reader(bus):
    """-> the witness reader's status callable for a python-can PcanBus:
    its GetStatus word as pcan_status_text gives it."""
    return lambda: pcan_status_text(bus.status())


def classify_read(result):
    """One PCAN-Basic Read status -> "row", "empty", "state", "ignored" or
    "failure". Until 2026-11-20 a state other than BUSLIGHT / BUSHEAVY was a
    failure, so on Linux, where PCAN-Basic returns status messages as Read
    results, each error-passive report was counted as a lost frame and
    faulted the window. Now a result of state and overrun bits only (with
    or without QRCVEMPTY) is decoded by pcan_state: an overrun or bus-off
    is a failure, any other state a "state". An overrun bit is a failure
    whatever else is set (2026-10-25: python-can 4.6.1 tests BUSLIGHT /
    BUSHEAVY first, so it took a queue overrun with a bus-heavy bit for a
    warning and dropped the message)."""
    if result == PCAN_OK:
        return "row"
    if result == PCAN_QRCVEMPTY:
        return "empty"
    if result == PCAN_ILLDATA:
        return "ignored"
    word = result & ~PCAN_QRCVEMPTY
    if word & PCAN_STATE_RESULT and not word & ~PCAN_STATE_RESULT:
        return pcan_state(word)[0]
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
        """-> (kind, row) for a row, (kind, text) for a state or a
        failure, (kind, None) otherwise; on an empty queue (a state
        returned with QRCVEMPTY included) it waits up to timeout s for the
        receive event. A status message (Read OK, MSGTYPE STATUS) is a
        state or a failure by its word (pcan_state), never a row: before
        2026-11-20 each one was written as a data frame whose id was the
        driver's state number."""
        c0 = time.perf_counter()
        result, msg, ts = self.basic.Read(self.handle)
        c1 = time.perf_counter()
        self.t_read += c1 - c0
        r = int(result)
        kind = classify_read(r)
        if kind == "row":
            if msg.MSGTYPE & PCAN_MSG_STATUS:
                word = int.from_bytes(bytes(msg.DATA)[:4], "big")
                kind, name = pcan_state(word)
                return kind, "PCAN %s %s: status message 0x%05X (id %d)" % (
                    "bus state" if kind == "state" else "receive failure,",
                    name, word, msg.ID)
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
        if kind == "state":
            if r & PCAN_QRCVEMPTY:
                self.wait(timeout)
                self.t_wait += time.perf_counter() - c1
            return kind, "PCAN bus state %s: read status 0x%X" % (
                pcan_state(r & ~PCAN_QRCVEMPTY)[1], r)
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
    # role pcanfd (shared/bench SPEC section 8, 2026-11-18)
    bus = adapter_roles.pcanfd(BUS_BITRATE,
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
                       bus.shutdown, pcan_status_reader(bus), mode)


def reader_stats(n, reads, t_read, t_wait, wall, lag_min, lag_max, lag_end,
                 bins=None):
    """One window's reader figures, as the reader process forwards them
    with its cut: rows, Read calls, seconds in Read and in the wait for
    the receive event, the window's wall time, and the read time less the
    stamp (lag, s) of its rows: least, most, and the last row's. bins:
    per FIT_BIN_S of stamps, (stamp of the least lag, least lag, stamp of
    the most lag, most lag), in stamp order: the points the stamp map is
    fitted on (StampMap) and the lags it is judged on (reader_fault)."""
    return {"rows": n, "reads": reads, "t_read": t_read, "t_wait": t_wait,
            "wall": wall, "lag_min": lag_min, "lag_max": lag_max,
            "lag_end": lag_end, "bins": sorted(bins or ())}


def _bins_out(bins):
    """The reader's {bin: [s_min, lag_min, s_max, lag_max]} -> tuples."""
    return [tuple(v) for v in bins.values()]


def bin_row(bins, cur_b, cur, t, lag):
    """One row (stamp t, lag) into the reader's stamp-map bins: per
    FIT_BIN_S of stamps, [stamp of the least lag, least lag, stamp of the
    most lag, most lag]. A bin's first row opens it with its own stamp and
    lag; a later row replaces the least (most) only with a strictly lower
    (higher) lag. cur_b, cur: what the last call returned (None, None at
    the start), so a run of rows in one bin skips the dict lookup; a row
    back in an earlier bin finds that bin's entry. Returns (bin, entry).
    The one copy, used by the reader (_witness_proc) and by the self-test's
    scripted bins, and checked on fixed rows (the reviewer's B3,
    2026-11-12: in record accept_3_20261111T234853_563d the mutant that
    never updates a bin's least lag was caught only on some timings)."""
    b = int(t // FIT_BIN_S)
    if b != cur_b:
        cur_b, cur = b, bins.get(b)
        if cur is None:
            cur = bins[b] = [t, lag, t, lag]
    if lag < cur[1]:
        cur[0], cur[1] = t, lag
    if lag > cur[3]:
        cur[2], cur[3] = t, lag
    return cur_b, cur


def _witness_proc(acks, spec, folder, conn, mode="classic"):
    """The witness's reader in its own process at ABOVE_NORMAL (F2,
    reviewer 2026-10-23): it only drains the PCAN and writes compact rows
    to one file per window, so the bench process's work (the last window's
    rows, scoring, SeaDrive writes) shares no GIL or heap with it. Each
    receive failure is kept by its text (F1); the PCAN's bus states (Read
    results and status messages, pcan_state) are counted here by their
    text, where the PCAN is, and forwarded (2026-10-25: since F2 the bench
    process's logging handler saw none; by text since 2026-11-20). Commands
    on conn: ("cut",) closes this window's file and answers ("cut", k,
    rows, kinds, failures, states, last state, reader_stats, {state text:
    [n, first, last]}); ("status",) answers
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
    states = {}     # state text -> [n, first, last host s]
    f = open(os.path.join(folder, "w%06d.bin" % k), "wb", 1 << 20)
    since = 0
    fast = mode == "fast"
    now = time.perf_counter
    w0, reads, lag_min, lag_max, lag = now(), 0, None, None, None
    # the stamp map's points (StampMap): per FIT_BIN_S of stamps, the rows
    # read soonest and latest after their stamps
    bins, cur_b, cur = {}, None, None
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
                                          lag_max, lag, _bins_out(bins))
                        conn.send(("cut", k, n, kinds, fails, warns, wlast, st,
                                   states))
                        k, n, kinds, fails, warns, wlast = (k + 1, 0, {}, 0,
                                                            0, "")
                        states = {}
                        w0, reads, lag_min, lag_max, lag = (now(), 0, None,
                                                            None, None)
                        bins, cur_b, cur = {}, None, None
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
                cur_b, cur = bin_row(bins, cur_b, cur, t, lag)
                n += 1
                since += 1
            elif kind == "failure":
                note_failure(kinds, v, time.perf_counter())
                fails += 1
                since = WITNESS_POLL_ROWS
            elif kind == "state":
                # counted like a row towards the next look at the pipe: a
                # PCAN returning only states must still answer the cut
                note_failure(states, v, time.perf_counter())
                warns, wlast = warns + 1, v
                since += 1
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
              PCAN_BUSOFF: "Bus error: the CAN controller is bus-off",
              0x40000: "Bus error: the CAN controller is error passive"}


class _FakeBasic(object):
    """The self-test's PCAN-Basic: Read gives node rows seq 1..n at up to
    rate per second; at each index in statuses ((index, status), ...) that
    status instead of a row (the row is not given; a status that is a
    tuple (id, msgtype, data) is a message Read returns OK, such as a
    status message); at die_at the process exits (os._exit(3)); after the
    n rows, Read returns tail for ever if given (a PCAN returning only
    states), else QRCVEMPTY."""

    def __init__(self, n, statuses=(), die_at=None, rate=0.0, clock=False,
                 delay=0.0, skew=0.0, scale=0.0, step=None, tail=None):
        self.n, self.statuses = n, dict(statuses)
        self.die_at, self.rate = die_at, rate
        self.tail = tail
        # clock: row k is stamped when it "arrived", t0 + k / rate, on this
        # clock (plus skew, and running scale faster: an epoch clock off
        # rate); step (k, s): rows after k stamped s later; delay: each Read
        # takes this long (a slow reader)
        self.clock, self.delay, self.skew = clock, delay, skew
        self.scale, self.step = scale, step
        self.k, self.t0 = 0, time.perf_counter()

    def Read(self, _handle):
        if self.delay:
            end = time.perf_counter() + self.delay
            while time.perf_counter() < end:
                pass
        if self.k >= self.n and self.tail is not None:
            return self.tail, None, None
        if self.k >= self.n or (self.rate and time.perf_counter() - self.t0
                                < self.k / self.rate):
            return PCAN_QRCVEMPTY, None, None
        self.k += 1
        if self.die_at == self.k:
            os._exit(3)
        if self.k in self.statuses:
            s = self.statuses[self.k]
            if isinstance(s, tuple):
                return (PCAN_OK, _FakePcanMsg(s[0], s[1], s[2]),
                        _FakePcanTs(float(self.k)))
            return s, None, None
        t = (self.t0 + self.k / self.rate * (1.0 + self.scale) + self.skew
             + (self.step[1] if self.step and self.k > self.step[0] else 0.0)
             if self.clock else float(self.k))
        return (PCAN_OK, _FakePcanMsg(NODE_ID, 0, node_payload(self.k)),
                _FakePcanTs(t))

    def wait(self, timeout):
        time.sleep(min(timeout, 0.0005) if self.k < self.n else timeout)

    @staticmethod
    def text(code):
        bits = [b for b in sorted(_FAKE_TEXT) if code & b]
        return "\n".join(_FAKE_TEXT[b] for b in bits) or "unknown 0x%X" % code


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
        # its stamps -> this process's perf_counter, fitted on every take's
        # rows (StampMap; reviewer, 2026-11-11)
        self.stamp_map = StampMap()
        # a scratch_dir run folder, removed by close(); one a crash
        # leaves is pruned by the next Witness once this process is gone
        self.folder = scratch_dir.make("vport-witness")
        ctx = mp.get_context("spawn")
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_witness_proc, args=(
            acks, spec, self.folder, child, self.mode), daemon=True)
        self.proc.start()
        self.lock = threading.Lock()
        self.overflows = 0          # receive failures since the last take
        self.fail_kinds = {}        # text -> [n, first, last host s]
        self.warns, self.warn_last = 0, ""   # PCAN bus states: n, last
        self.state_kinds = {}       # state text -> [n, first, last host s]
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
                self.stamp_map.add(self.stats)
                rows = _read_window(os.path.join(self.folder,
                                                 "w%06d.bin" % k))
                self._drop(k)
                self.k += 1
                with self.lock:
                    self.overflows += fails
                    self.warns += warns
                    if warns:
                        self.warn_last = wlast
                    for got, into in ((kinds, self.fail_kinds),
                                      (r[8] if len(r) > 8 else {},
                                       self.state_kinds)):
                        for text, (c, first, last) in got.items():
                            a = into.get(text)
                            if a is None:
                                into[text] = [c, first, last]
                            else:
                                a[0] += c
                                a[1], a[2] = (min(a[1], first),
                                              max(a[2], last))
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
        """-> (n, last text) of the PCAN's bus states (pcan_state: Read
        results and status messages that are neither a frame lost nor
        bus-off) since the last call, counted by the reader process."""
        with self.lock:
            n, last = self.warns, self.warn_last
            self.warns, self.warn_last = 0, ""
        return n, last

    def take_states(self):
        """-> {state text: [n, first, last host s]} since the last call."""
        with self.lock:
            k, self.state_kinds = self.state_kinds, {}
        return k

    def close(self):
        try:
            self.conn.send(("stop",))
        except (OSError, EOFError):
            pass
        self.proc.join(3.0)
        if self.proc.is_alive() and hasattr(self.proc, "terminate"):
            self.proc.terminate()
            self.proc.join(2.0)
        scratch_dir.remove(self.folder)

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


# F2's Linux form (the charge-interposer reviewer, 2026-11-12; AGENTS.md
# "Parallel work on the shared machine", point 1): the witness reader and
# the node place themselves in bench.slice. Each asks the user manager for
# a transient scope in that slice holding its own pid (StartTransientUnit,
# through busctl, as `systemd-run --scope` does for a new command), then
# reads back from /proc/self/cgroup where it landed. A process priority
# would not do it: nice only ranks processes inside one cgroup.
BENCH_SLICE = "bench.slice"
BUSCTL = ["busctl"]
CGROUP_SELF = "/proc/self/cgroup"


def cgroup_slice(text):
    """The innermost *.slice in a /proc/<pid>/cgroup text, or None."""
    for ln in (text or "").splitlines():
        if ln.startswith("0::"):
            s = [p for p in ln[3:].strip().split("/") if p.endswith(".slice")]
            return s[-1] if s else None
    return None


def slice_of(pid=None):
    """The slice of this process (or of pid), read from its cgroup file."""
    path = CGROUP_SELF if pid is None else "/proc/%d/cgroup" % pid
    try:
        with open(path, encoding="utf-8") as f:
            return cgroup_slice(f.read())
    except OSError:
        return None


def place_in_slice(slice_name=BENCH_SLICE):
    """This process into slice_name, in a transient scope of its own;
    -> the slice read back afterwards (slice_name when it took)."""
    import subprocess
    if slice_of() == slice_name:
        return slice_name
    pid = os.getpid()
    try:
        subprocess.run(BUSCTL + [
            "--user", "call", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
            "StartTransientUnit", "ssa(sv)a(sa(sv))",
            "vtrux-vport-%d.scope" % pid, "fail", "2", "PIDs", "au", "1",
            str(pid), "Slice", "s", slice_name, "0"],
            capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    for _ in range(40):                 # the move lands as the job runs
        if slice_of() == slice_name:
            return slice_name
        time.sleep(0.05)
    return slice_of()


def prio_text(p):
    """A priority as above_normal() gave it -> text for a line."""
    if isinstance(p, int):
        return "%s (0x8000 = ABOVE_NORMAL)" % hex(p)
    if p == BENCH_SLICE:
        return "in %s (read back from its cgroup)" % BENCH_SLICE
    return ("NOT IN %s: %s" % (BENCH_SLICE, p)) if p else "unknown"


def above_normal():
    """This process to ABOVE_NORMAL on Windows (bench_hold12's helper),
    read back; -> the class, or None if it could not be set. On Linux,
    into bench.slice (place_in_slice) -> the slice read back."""
    if os.name != "nt":
        return place_in_slice(BENCH_SLICE)
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
            pause = send(seq)
            ok = 1
        except kvaser_link.Stall:
            raise                       # a kvaser TX stall is never a refused send
        except Exception:                                  # noqa: BLE001
            ok, pause = 0, None
        if pause:
            t0 += pause                 # a flush held the node: no catch-up burst
            tc += pause
        put((seq, tc - (t0 + seq * period), ok, tc, now()))
        seq += 1
        sleep(max(0.0, t0 + seq * period - now()))


class NodeStall(Exception):
    """The node met a kvaser TX stall where its frames were expected to
    complete: the run is VOID (the reviewer's D3/D5, 2026-11-13)."""


def node_sender(watch, msg_of, expect, now, put, forgive=lambda: 0, wrong=lambda: False):
    """The node's send(seq) for node_loop, on a kvaser_link.EchoWatch (the
    reviewer's rulings, 2026-11-13). expect() is True where the node's
    frames are expected to complete (an ACKer present, the board at 500k):
    there a kvaser TX stall raises. False in the no-ACK and wrong-rate
    windows: the bound is suspended and ENOBUFS is a refused send. When it
    turns True again (the ACK back) every frame still pending must echo
    within STALL_S of that moment -- the flush, done before the next send
    and reported as ("expected", n pending, seconds, n forgiven, [their
    keys]) -- else Stall. forgive(), read at the flush: how many pending
    frames the kernel dropped while the node was bus-off in the wrong-rate
    windows since (tx_dropped's growth read in them; the charge-interposer
    reviewer's ruling Q1, 2026-11-20); up to that many never echoing are
    forgiven (EchoWatch.finish), more is the Stall. wrong() is True in a
    wrong-rate span: a send made there with no ACK expected is the effect
    being measured, so the flush does not judge it (the charge-interposer
    reviewer's ruling (a), 2026-11-20): every such send still unechoed once
    the bound passes is excused, however many, and forgive() counts only
    the others. The flush's put is then ("expected", n pending, seconds,
    n forgiven or excused, [their keys], n excused). Returns the seconds a
    flush held the node (node_loop shifts its schedule).
    The socket is read after every send, a refused one too (2026-11-20, the
    hand take for the reviewer's ruling (2)): with nothing acknowledging,
    sends are refused for seconds at a time, and a node that skipped its
    reads then found its receive queue full when the ACK came back, lost
    the held frames' echoes and VOIDed the flush (run 40e6's vport-listen,
    "28 sends unechoed, 552 echoed"). canbench's EchoWatch.send does that
    read (read_after_send, default True), so the node's own poll(0.0)
    after a refused send is gone (the reviewer, 2026-11-23: at
    bench_vport's next change); the poll(0.0) after a send checks the
    bound."""
    st = {"exp": True}
    span = set()                        # keys of the sends from a wrong-rate span

    def send(seq):
        pause = None
        exp = bool(expect())
        if exp != st["exp"]:
            st["exp"] = exp
            if exp:
                t = now()
                watch.suspended = False
                watch.rebase(t)
                n = len(watch.pending)
                gone = watch.finish(int(forgive()), excuse=span.__contains__)
                pause = now() - t
                put(("expected", n, pause, len(gone), [g[1] for g in gone],
                     sum(1 for g in gone if g[1] in span)))
                span.clear()
            else:
                watch.suspended = True
        msg = msg_of(seq)
        if not exp and wrong():
            span.add(kvaser_link._key(msg))
        try:
            watch.send(msg, timeout=0.05)
        except kvaser_link.Stall as e:
            if exp:
                raise
            raise RuntimeError("refused, no ACK expected: %s" % e)
        watch.poll(0.0)
        return pause
    return send


class _HeldBus(object):
    """The self-test's Kvaser on kvaser_usb, as the 2026-11-20 hand take
    showed it (runs/vport_stall_20261120): with nothing acknowledging it
    holds HOLD frames and refuses the rest (ENOBUFS, or python-can's
    "Transmit buffer full"), and each send interval puts ERRS error frames
    into the socket's receive queue of CAP frames, where one past full is
    lost, as the kernel drops it; when the ACK comes back every held frame
    echoes at once."""
    HOLD, CAP, ERRS = 75, 200, 37

    class M(object):
        def __init__(self, aid, data, is_rx=True, err=False):
            self.arbitration_id, self.data = aid, data
            self.is_extended_id, self.is_rx, self.is_error_frame = False, is_rx, err

    def __init__(self, clock, refuse="enobufs"):
        self.clock, self.refuse = clock, refuse
        self.acked, self.held, self.rx, self.lost = False, [], collections.deque(), 0

    def _in(self, m):
        if len(self.rx) >= self.CAP:
            self.lost += 1
        else:
            self.rx.append(m)

    def _echo(self, msg):
        self._in(self.M(msg.arbitration_id, msg.data, is_rx=False))

    def send(self, msg, timeout=None):
        if self.acked:
            self._echo(msg)
            return
        for _ in range(self.ERRS):
            self._in(self.M(0x20000288, b"\0" * 8, err=True))
        if len(self.held) >= self.HOLD:
            if self.refuse == "enobufs":
                raise OSError(105, "No buffer space available")
            raise RuntimeError("Transmit buffer full")
        self.held.append(msg)

    def ack(self, drop=0):
        """The ACK back: every held frame echoes, but the first `drop`,
        which the kernel dropped (a bus-off meanwhile) and never echo."""
        self.acked = True
        for m in self.held[drop:]:
            self._echo(m)
        self.held = []

    def recv(self, timeout=0.0):
        self.clock[0] += timeout            # time passes while it waits
        return self.rx.popleft() if self.rx else None


def held_flush_cases(drop=0, forgive=0, wrong_from=None):
    """node_sender on a real kvaser_link.EchoWatch over _HeldBus: 4 s with
    nothing acknowledging (frames held, the rest refused, error frames
    arriving), then the ACK back and the flush. drop: held frames the
    kernel dropped, never echoing; forgive: what the node's flush is told
    to forgive (ruling Q1, 2026-11-20); wrong_from: the seq from which the
    span is wrong-rate (ruling (a), 2026-11-20), None for none. -> [(kind,
    the flush's put or ("stall", text), why)] for a refused send by ENOBUFS
    and by python-can's buffer-full timeout."""
    out = []
    for kind in ("enobufs", "transmit buffer full"):
        clock, put = [0.0], []
        bus = _HeldBus(clock, refuse="enobufs" if kind == "enobufs" else "timeout")
        w = kvaser_link.EchoWatch(bus, clock=lambda: clock[0])
        exp, cur = [False], [0]
        snd = node_sender(w, lambda s: _HeldBus.M(NODE_ID, s.to_bytes(8, "little")),
                          lambda: exp[0], lambda: clock[0], put.append,
                          forgive=lambda: forgive,
                          wrong=lambda: wrong_from is not None and cur[0] >= wrong_from)
        res = None
        for seq in range(400):
            cur[0] = seq
            clock[0] += NODE_PERIOD_S
            try:
                snd(seq)
            except kvaser_link.Stall as e:
                res = ("stall", str(e))
                break
            except Exception:                              # noqa: BLE001
                pass                                       # a refused send
        if res is None:
            bus.ack(drop)
            exp[0] = True
            try:
                snd(400)
                res = put[-1] if put else ("no flush",)
            except kvaser_link.Stall as e:
                res = ("stall", str(e))
        out.append((kind, res, "%d frames lost on the full receive queue" % bus.lost))
    return out


def _node_proc(q, stop_ev, ready_ev, expect_ev, forgive_v=None, wrong_ev=None):
    """The node in its own process at ABOVE_NORMAL (reviewer, 2026-10-16,
    (i)): its pacing is no longer shared with the witness's GIL (the
    witness reader has its own process too since 2026-10-23, F2). Since
    2026-11-13 the Kvaser on kvaser_usb, netdev kvaser, its echoes kept:
    each echo is put as ("echo", seq, t_call, t_returned, t_echo), so the
    bench counts a frame SENT only when its own echo came back (D3), and a
    kvaser TX stall where frames should complete is put as ("stall", text,
    epoch) and ends the node."""
    import can
    q.put(("prio", above_normal()))
    # no error frames: the node reads only its echoes, and a flood of error
    # frames with nothing acknowledging would fill its receive queue
    bus = kvaser_link.open_bus(receive_own_messages=True, error_frames=False)
    calls = {}

    def on_echo(k, tr, te):
        s = int.from_bytes(bytes(k[2][1:5]), "little")
        q.put(("echo", s, calls.pop(s, None), tr, te))
    watch = kvaser_link.EchoWatch(bus, on_echo=on_echo)
    ready_ev.set()
    inner = node_sender(watch, lambda seq: can.Message(
        arbitration_id=NODE_ID, is_extended_id=False, data=node_payload(seq)),
        expect_ev.is_set, time.perf_counter, q.put,
        forgive=(lambda: forgive_v.value) if forgive_v is not None else (lambda: 0),
        wrong=wrong_ev.is_set if wrong_ev is not None else (lambda: False))

    def send(seq):
        calls[seq] = time.perf_counter()
        return inner(seq)
    try:
        node_loop(send, time.perf_counter, time.sleep, q.put, stop_ev.is_set)
    except kvaser_link.Stall as e:
        q.put(("stall", str(e), kvaser_link.epoch_of(e.t)))
    finally:
        bus.shutdown()
        q.put(None)


class Node(object):
    """The Kvaser, normal mode: another node sending NODE_ID every
    NODE_PERIOD_S, and ACKing. An UP kvaser_usb link acknowledges with no
    socket open, so 'no ACKer' means its link DOWN (NoNode; 2026-11-13,
    the reviewer's A), and start() brings it up and reads it back first.
    Since 2026-10-16 it runs in its own process at ABOVE_NORMAL; each
    send's host-side lag is logged (take_log). Since 2026-11-13 a frame is
    SENT when its own echo came back (take_echoes; D3), each echo carrying
    its send call's time so window() attributes it to the window that sent
    it; expect_acks() switches the stall rule (node_sender). It starts
    with no ACK expected (2026-11-20, the reviewer's ruling (2)): step 0
    leaves the board and the witness listen-only, so nothing acknowledges
    its first frames, and in run 40e6 six runs were VOIDed by that stall
    before their step's ACKer came up. Each step turns expect_acks(True)
    on once its ACKer is on the bus.

    The charge-interposer reviewer's rulings Q1 and Q3, 2026-11-20: its
    link comes up with restart-ms NODE_RESTART_MS, read back, so after a
    bus-off in a wrong-rate window it recovers and keeps trying, as a
    vehicle node does (on b467 under canlib it kept trying through each
    window); expect_acks(False, wrong_rate=True) marks a wrong-rate span,
    in which window() adds the kernel's drops while bus-off to `forgive`,
    handed to the node's flush when the ACK returns. head() is its head
    frame: the oldest seq sent and not yet echoed (score_noack judges its
    retries)."""

    def __init__(self, channel=None):
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        self.q = ctx.Queue()
        self.stop_ev = ctx.Event()
        self.ready_ev = ctx.Event()
        self.expect_ev = ctx.Event()      # clear: no ACK expected yet
        self.forgive_v = ctx.Value("i", 0)  # read by the node's flush
        self.wrong_ev = ctx.Event()       # set: the node's sends are from a wrong-rate span
        self.p = ctx.Process(target=_node_proc, args=(
            self.q, self.stop_ev, self.ready_ev, self.expect_ev, self.forgive_v,
            self.wrong_ev), daemon=True)
        self.sent, self.fails, self.log, self.echoes = [], 0, [], []
        self.windows = []                 # (label, t_w0, t_cut) of every window
        self.prio = None
        self.stall = None
        self.flushes = []                 # ("expected", n, seconds, n forgiven, keys)
        self.flushed_ev = threading.Event()
        self.lock = threading.Lock()
        self.t = None
        self.wrong_rate = False           # the span since expect_acks(False) is wrong-rate
        self.forgive = 0                  # tx_dropped's growth in its wrong-rate windows
        self.link_start = None            # its link counters when it started
        self.unechoed = set()             # seqs sent (ok) and not yet echoed
        self.early = set()                # seqs echoed before their log entry came
        self.call_t = {}                  # unechoed seq -> its send call's time
        self.flush_gone = []              # [(seq, t_call)] the last flush excused or forgave
        self.span_windows = []            # [(label, t0, t1, tx_dropped growth)] of the span

    def start(self):
        print("  node: %s" % kvaser_link.set_link(True, restart_ms=NODE_RESTART_MS))
        self.link_start = kvaser_link.link_counters()
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
            self.take_msg(x)

    def take_msg(self, x):
        """One message from the node process (the self-test feeds these)."""
        if x[0] == "prio":
            self.prio = x[1]
            return
        with self.lock:
            if x[0] == "echo":
                self.echoes.append(tuple(x[1:]))
                self.sent.append(x[1])
                self.call_t.pop(x[1], None)
                if x[1] in self.unechoed:
                    self.unechoed.discard(x[1])
                else:
                    self.early.add(x[1])        # its log entry is still on its way
            elif x[0] == "expected":
                self.flushes.append(x)
                # frames the flush forgave (dropped by the kernel) or excused
                # (from a wrong-rate span) never echo
                self.flush_gone = []
                for k in (x[4] if len(x) > 4 else ()):
                    s = int.from_bytes(bytes(k[2][1:5]), "little")
                    self.unechoed.discard(s)
                    self.flush_gone.append((s, self.call_t.pop(s, None)))
                self.flushed_ev.set()
            elif x[0] == "stall":
                self.stall = x
                self.flushed_ev.set()
            else:
                self.log.append(x)
                if not x[2]:
                    self.fails += 1
                elif x[0] in self.early:
                    self.early.discard(x[0])
                else:
                    self.unechoed.add(x[0])
                    self.call_t[x[0]] = x[3]    # its send call, for the span's figures

    def counters(self):
        """The node's link counters (kvaser_link.link_counters; ruling Q1)."""
        return kvaser_link.link_counters()

    def expecting(self):
        """True while the node's frames are expected to complete."""
        return self.expect_ev.is_set()

    def head(self):
        """The node's head frame: the oldest seq sent and not yet echoed,
        or None (the reviewer's ruling Q3, 2026-11-20)."""
        with self.lock:
            return min(self.unechoed) if self.unechoed else None

    def check_stall(self):
        if self.stall is not None:
            raise NodeStall(self.stall)

    def expect_acks(self, on, wait_s=2.0, wrong_rate=False):
        """on: the node's frames are expected to complete from now (an ACKer
        present, the board at 500k); off: a no-ACK or wrong-rate window
        (wrong_rate: a wrong-rate span, whose windows count the node's
        bus-offs and the kernel's drops meanwhile; the reviewer's ruling Q1,
        2026-11-20). Turning it on hands the flush the drops to forgive and
        waits for the node's flush of the frames held since (NodeStall if
        more than those did not echo within STALL_S of the ACK). The node's
        sends while a wrong-rate span is marked (wrong_ev) are excused at
        that flush, however many never echo (the reviewer's ruling (a),
        2026-11-20), and the flush prints each wrong-rate window's figures
        (span_figures)."""
        self.check_stall()
        if not on:
            if self.expect_ev.is_set():
                self.forgive = 0
                self.wrong_rate = wrong_rate
                self.span_windows = []
            else:
                self.wrong_rate = self.wrong_rate or wrong_rate
            if self.wrong_rate:
                self.wrong_ev.set()           # before the span's first send
            self.expect_ev.clear()
            return None
        if self.expect_ev.is_set():
            return None
        self.forgive_v.value = self.forgive
        self.flushed_ev.clear()
        self.expect_ev.set()
        if not self.flushed_ev.wait(wait_s):
            raise NodeStall(("stall", "the node reported no flush within %.0f s of "
                             "the ACK returning" % wait_s, time.time()))
        self.check_stall()
        f = self.flushes[-1]
        n_exc = f[5] if len(f) > 5 else 0
        print("  node: the ACK back, %d held frame(s) flushed in %.1f ms (kept out of "
              "the next window)%s" % (f[1] - f[3], f[2] * 1000, (
                  "; %d never echoed: %d sent in the wrong-rate span, excused (ruling "
                  "(a)), %d forgiven as dropped by the kernel while bus-off (tx_dropped "
                  "grew %d in the wrong-rate windows)"
                  % (f[3], n_exc, f[3] - n_exc, self.forgive)) if f[3] else ""))
        if self.wrong_rate:
            for ln in span_figures(self.flush_gone, self.span_windows):
                print("    " + ln)
        self.forgive, self.wrong_rate, self.span_windows = 0, False, []
        self.forgive_v.value = 0
        self.wrong_ev.clear()
        return f

    def take_echoes(self):
        """-> [(seq, t_call, t_returned, t_echo)] since the last take."""
        self.check_stall()
        with self.lock:
            e, self.echoes = self.echoes, []
            self.sent = []
        return e

    def take(self):
        self.check_stall()
        with self.lock:
            s, self.sent = self.sent, []
            self.echoes = []
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
    """No node on the segment: the Kvaser's link DOWN, read back in sysfs
    (an UP kvaser_usb link acknowledges with no socket open; under CANlib
    this was the Kvaser closed). link: a stand-in (the self-test)."""

    def __init__(self, link=None):
        line = (link or kvaser_link).set_link(False)
        print("  no node: %s" % line)
        self.windows = []

    def expect_acks(self, on, wait_s=2.0, wrong_rate=False):
        return None

    def check_stall(self):
        return None

    def take_echoes(self):
        return []

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


def witness_status(wit):
    """The PCAN's own status string from the witness's reader, or "" for
    a witness that has none (the self-test's stand-ins)."""
    if not hasattr(wit, "status_string"):
        return ""
    try:
        return wit.status_string()
    except Exception as e:                                 # noqa: BLE001
        return "status unreadable (%s)" % e


def state_lines(kinds, t0):
    """-> one line per PCAN bus-state text (pcan_state), most frequent
    first, its times from the window's start t0 (host s): what the PCAN
    reported, never a receive failure (2026-11-20)."""
    out = []
    for text, (n, first, last) in sorted(kinds.items(),
                                         key=lambda kv: -kv[1][0]):
        out.append("the PCAN's bus state x%d: %s (first +%.3f s, last "
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
# folder (SeaDrive) or to a local scratch folder (H2). In no default --steps;
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


def ws_mb(nbytes):
    """A working set in bytes -> MB (both systems' reads)."""
    return nbytes / 1048576.0


def linux_host_read(stat, meminfo, status):
    """/proc/stat, /proc/meminfo and /proc/self/status as text -> ((idle,
    kernel, user), memory load %%, working set MB), in the form
    HostSampler's Windows read gives (kernel includes idle, as
    GetSystemTimes', so cpu_busy_pct serves both). idle is idle + iowait,
    kernel system + irq + softirq + idle, user user + nice + steal; memory
    load is MemTotal less MemAvailable over MemTotal; the working set is
    VmRSS."""
    f = [int(x) for x in stat.splitlines()[0].split()[1:9]]
    user, nice, system, idle, iowait, irq, softirq, steal = f
    idle_all = idle + iowait
    m = dict((ln.split(":")[0], int(ln.split()[1]))
             for ln in meminfo.splitlines() if ":" in ln and ln.split()[1:])
    load = int(round(100.0 * (m["MemTotal"] - m["MemAvailable"])
                     / m["MemTotal"]))
    rss_kb = [int(ln.split()[1]) for ln in status.splitlines()
              if ln.startswith("VmRSS:")][0]
    return ((idle_all, system + irq + softirq + idle_all, user + nice + steal),
            load, ws_mb(rss_kb * 1024))


class HostSampler(object):
    """Samples the host every period s (diagnostic for H3, reviewer
    2026-10-23): system CPU busy, memory load, and this process's working
    set. Windows through kernel32 and psapi; Linux from /proc (2026-11-11,
    linux_host_read)."""

    def __init__(self, period=0.5):
        self.samples, self.period = [], period
        self.stop_ev = threading.Event()
        self.ok = os.name == "nt" or os.path.exists("/proc/self/status")
        if self.ok:
            self.t = threading.Thread(target=self._run, daemon=True)
            self.t.start()

    @staticmethod
    def _read():
        if os.name != "nt":
            with open("/proc/stat") as a, open("/proc/meminfo") as b, \
                    open("/proc/self/status") as c:
                return linux_host_read(a.read(), b.read(), c.read())
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
        return (v(i), v(k), v(u)), ms.load, ws_mb(pmc.ws)

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


# what process_snapshot runs, per system
SNAPSHOT_CMD = {"nt": ["tasklist", "/v", "/fo", "csv"],
                "posix": ["ps", "-eo",
                          "pid,ppid,ni,pcpu,pmem,rss,etime,cgroup,args"]}


def process_snapshot(path):
    """What else runs on the host (diagnostic, H3): tasklist's CSV with CPU
    time on Windows, ps with CPU, memory, nice and cgroup on Linux, to
    path. -> a one-line note."""
    import subprocess
    try:
        out = subprocess.run(SNAPSHOT_CMD["nt" if os.name == "nt" else "posix"],
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
    when the reader fell behind or its stamps do not follow the map
    (reader_fault), which witness_fault reports.
    The window's bounds are taken on this process's clock and turned into
    witness stamps by the witness's stamp map (StampMap; reviewer,
    2026-11-11: on Linux the PCAN's stamps are on the Unix epoch, not on
    perf_counter), as fitted on the rows read before the window: the
    window's own rows are judged against it, never fitted into it. A
    window with no rows read before it (the run's first) is mapped by its
    own rows, and its diagnostic line says so. wit.window_map is the map
    used. A witness with no map (the self-test's scripted ones) keeps the
    stamps as they are (offset 0, rate 1), the map before 2026-11-11."""
    # the node's link counters, read before and after (ruling Q1): its
    # bus-offs, restarts and the kernel's drops in the window. Read before
    # the window starts: one read takes 0.67-0.77 s on the bench
    # (2026-11-20, D2), and frames the node sent during it, after t_w0 but
    # before its log is taken, would count as sent with their rows outside
    # the window's stamps
    link0 = node.counters() if hasattr(node, "counters") else None
    pre = wit.take()
    smap = getattr(wit, "stamp_map", None)
    fit = smap.frozen() if smap is not None else None
    wit.take_incomplete()
    if hasattr(wit, "take_failures"):
        wit.take_failures()
    if hasattr(wit, "take_warnings"):
        wit.take_warnings()
    if hasattr(wit, "take_states"):
        wit.take_states()
    t_w0 = time.perf_counter()
    # the node's echoes (D3, 2026-11-13): a frame is sent when its own echo
    # came back, and it belongs to the window of its SEND CALL; echoes read
    # now are of frames sent before this window (a flush from a no-ACK or
    # wrong-rate window), kept out of it and recorded (the reviewer's
    # condition, 2026-11-13)
    echo_mode = hasattr(node, "take_echoes")
    pre_e = node.take_echoes() if echo_mode else (node.take() and [])
    # the node's head frame as the window starts: sent, not yet echoed, and
    # retried through a window with nothing acknowledging (the reviewer's
    # ruling Q3, 2026-11-20)
    head = node.head() if hasattr(node, "head") else None
    expecting = node.expecting() if hasattr(node, "expecting") else True
    wrong_rate = bool(getattr(node, "wrong_rate", False))
    node.window_busoff_fault = None
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
    if echo_mode:
        cut_e = node.take_echoes()
    else:
        sent = node.take()
    t_ncut = time.perf_counter()
    node.window_fails = node.take_fails()
    node.window_log = node.take_log()
    if link0 is not None:
        node.window_link = kvaser_link.counter_growth(link0, node.counters())
        ln, node.window_busoff_fault, add = judge_node_link(node.window_link, expecting,
                                                            wrong_rate)
        node.forgive += add
        if wrong_rate and not expecting and hasattr(node, "span_windows"):
            # its figures are printed at the span's flush (ruling (a))
            node.span_windows.append((label or command or "window", t_w0, t_ncut,
                                      node.window_link["tx_dropped"]))
        if ln:
            print("    " + ln)
    # read on, so rows stamped in the window but not yet read reach it;
    # before the failures are taken, so a receive failure meanwhile is this
    # window's
    time.sleep(STAMP_DRAIN_S)
    drained = wit.take()
    st_drain = getattr(wit, "stats", None)
    if echo_mode:
        # echoes of this window's sends that came back after its cut count
        sent, node.window_flushed = window_sent(
            pre_e + cut_e + node.take_echoes(), t_w0, t_ncut,
            getattr(node, "windows", []))
        node.windows.append((label or command or "window", t_w0, t_ncut))
    if fit is not None and not fit.n:
        fit = smap.frozen(own=True)
    h_lo, h_hi = stamp_bounds(node.window_log, t_w0, t_wcut,
                              t_cmd if from_command else None)
    lo, hi = ((fit.to_stamp(h_lo), fit.to_stamp(h_hi)) if fit is not None
              else (h_lo, h_hi))
    rows, wit.window_stamp = assign_by_stamp(pre, raw, drained, lo, hi)
    node.window_head = head
    node.window_head_rows = []
    if echo_mode:
        # node frames on the wire sent before this window (the flush's
        # burst) are kept out of its share, timing and settle: only the
        # seqs this window's send log or its sent seqs hold are its node
        # frames. The head frame's rows kept out are kept aside for
        # score_noack, which judges its retries (ruling Q3)
        node.window_head_rows = [r for r in rows if head is not None
                                 and node_seq(r) == head]
        rows, n_out, gone = own_node_rows(rows, node.window_log, sent)
        if head in set(node_seq(r) for r in rows):
            node.window_head_rows = []
        if n_out or node.window_flushed:
            print("    " + flushed_line(node.window_flushed, n_out, gone))
    wit.window_bounds = (lo, hi)
    wit.window_map = fit
    wit.window_reader = merge_stats(st_cut, st_drain)
    wit.window_stamp_fault = reader_fault(wit.window_reader, fit)
    node.window_load = window_load(rows, node.window_log, seconds)
    wit.window_incomplete = wit.take_incomplete()
    t_w1 = time.perf_counter()
    kinds = wit.take_failures() if hasattr(wit, "take_failures") else {}
    wit.window_failures = failure_lines(kinds, t_w0)
    wit.window_fail_top = (max(kinds, key=lambda x: kinds[x][0])
                           if kinds else "")
    wit.window_warnings = (wit.take_warnings()
                           if hasattr(wit, "take_warnings") else (0, ""))
    wit.window_states = state_lines(wit.take_states() if hasattr(
        wit, "take_states") else {}, t_w0)
    wit.window_host =(host_summary(HOST.samples, t_w0, t_w1)
                       if HOST is not None else "")
    wit.window_saved = save_rows(rows, label or command, rows_dir)
    print("    diagnostic, witness: " + stamp_line(wit.window_stamp) + "; "
          + reader_line(wit.window_reader, rows, lo, hi, fit)
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


def stall_report(x, t0_epoch, node=None, journal=None, recover=None):
    """A node stall ("stall", text, epoch) -> its record: the VOID line
    with the kernel's lines, then the recovery's line, the node's process
    closed first. recover: a stand-in for kvaser_link.recover (tests).
    Since the reviewer's ruling Q1 (2026-11-20) a line after the VOID line
    gives the node's link counters since it started, so a stall from frames
    the kernel dropped at a bus-off reads as one."""
    since = None
    if node is not None and getattr(node, "link_start", None) is not None:
        try:
            g = kvaser_link.counter_growth(node.link_start, node.counters())
            since = ("the node's link since it started: %d bus-off(s), %d restart(s), "
                     "tx_dropped +%d, tx_aborted_errors +%d"
                     % (g["bus_off"], g["restarts"], g["tx_dropped"], g["tx_aborted_errors"]))
        except kvaser_link.LinkError as e:
            since = "the node's link counters could not be read: %s" % e
    if node is not None:
        node.close()
    when = kvaser_link.clock_text(x[2]) if x[2] else "?"
    lines = [kvaser_link.void_line("%s at %s (the node, where its frames were "
                                   "expected to complete)" % (x[1], when),
                                   t0_epoch, time.time(), journal)]
    if since:
        lines.append(since)
    # the node's link comes back with its restart-ms (a usbreset leaves 0)
    lines.append((recover or kvaser_link.recover)(restart_ms=NODE_RESTART_MS)[1])
    return lines


def window_sent(echoes, t_w0, t_cut, windows):
    """echoes [(seq, t_call, t_returned, t_echo)] read around one window
    -> (the seqs SENT in it: sent between t_w0 and its cut, their echo
    within STALL_S of send() returning (D3); {source label: n} of echoes of
    frames sent BEFORE the window, the flush of a no-ACK or wrong-rate
    window, kept out of it and recorded). windows: [(label, t0, t1)] of the
    earlier windows, to name a flush's source."""
    sent, flushed = [], {}
    for seq, tc, tr, te in echoes:
        if tc is not None and t_w0 <= tc <= t_cut:
            if tr is not None and te - tr <= kvaser_link.STALL_S:
                sent.append(seq)
        elif tc is None or tc < t_w0:
            src = next((lb for lb, a, b in reversed(windows)
                        if tc is not None and a <= tc <= b), "between windows")
            flushed[src] = flushed.get(src, 0) + 1
    return sent, flushed


def own_node_rows(rows, log, sent=()):
    """rows less the node frames that are not this window's: whose seq
    neither its send log nor its sent seqs hold (frames sent before the
    window, or after its cut, on the wire in it). sent: the window's sent
    seqs (window_sent), which count as its own even when the log, taken at
    the cut, has not yet received their entries (2026-11-20, the reviewer's
    stall-16 item: in C3's stall 16, seq 5211's send was called before the
    cut and its echo counted it sent, but its log entry was still on its
    way, so its row was kept out and read as never reaching the witness).
    -> (rows, the number kept out, their seqs in order)."""
    mine = set(x[0] for x in log) | set(sent)
    out = [r for r in rows if node_seq(r) is None or node_seq(r) in mine]
    gone = sorted(set(node_seq(r) for r in rows
                      if node_seq(r) is not None and node_seq(r) not in mine))
    return out, len(rows) - len(out), gone


def seq_ranges(seqs):
    """[3, 4, 5, 9] -> "3..5, 9"."""
    out, run = [], []
    for s in seqs:
        if run and s == run[-1] + 1:
            run.append(s)
            continue
        if run:
            out.append("%d..%d" % (run[0], run[-1]) if len(run) > 1 else "%d" % run[0])
        run = [s]
    if run:
        out.append("%d..%d" % (run[0], run[-1]) if len(run) > 1 else "%d" % run[0])
    return ", ".join(out)


def flushed_line(flushed, n_rows, seqs=()):
    return ("node frames not sent in this window, kept out of it (its share, "
            "timing and settle): %d on the wire%s; echoes %s"
            % (n_rows, (" (seq %s)" % seq_ranges(seqs)) if seqs else "",
               ", ".join("flushed from %s: %d" % kv for kv in sorted(flushed.items()))
               or "none"))


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
# clock than the one the window is cut on). Since 2026-11-11 both are
# measured against the stamp map (StampMap), the thresholds unchanged.
READER_LAG_TOL_S = 0.25
STAMP_CLOCK_TOL_S = 0.005
# the stamp map's points: per this much of stamps, per take, the reader
# keeps its least- and most-lagged row (reader_stats' bins)
FIT_BIN_S = 1.0
# and fits a rate only once its points span this much: two points a few
# seconds apart, each with a millisecond of read latency, would give a
# rate off by hundreds of ppm (until then the rate is the host's, b = 0)
FIT_RATE_SPAN_S = 30.0


def envelope(pts, min_span=None):
    """[(stamp, lag)] in stamp order -> (a, b, s0): the line lag = a + b *
    (stamp - s0) below every point with the least summed distance to them,
    the lower convex hull's edge over the points' mean stamp (the sum is
    n times the mean lag less the line's value at the mean stamp, so the
    best line is the highest one there that stays below every point).
    Points spanning less than min_span (FIT_RATE_SPAN_S): b = 0 and a the
    least lag."""
    s0 = pts[0][0]
    xy = [(s - s0, o) for s, o in pts]
    if xy[-1][0] < (FIT_RATE_SPAN_S if min_span is None else min_span):
        return min(o for _x, o in xy), 0.0, s0
    hull = []
    for p in xy:
        while len(hull) >= 2 and (
                (hull[-1][0] - hull[-2][0]) * (p[1] - hull[-2][1])
                - (hull[-1][1] - hull[-2][1]) * (p[0] - hull[-2][0])) <= 0:
            hull.pop()
        hull.append(p)
    hull = [p for k, p in enumerate(hull)
            if k == 0 or p[0] != hull[k - 1][0]]
    if len(hull) < 2:
        return min(o for _x, o in xy), 0.0, s0
    xm = sum(x for x, _o in xy) / len(xy)
    for (x0, y0), (x1, y1) in zip(hull, hull[1:]):
        if x1 >= xm:
            break
    b = (y1 - y0) / (x1 - x0)
    return y0 - b * x0, b, s0


class StampFit(object):
    """One stamp map: host time = stamp + off(stamp), off(s) = a + b * (s -
    s0), fitted on n points (own: on the judged window's own rows)."""

    def __init__(self, a=0.0, b=0.0, s0=0.0, n=0, own=False):
        self.a, self.b, self.s0, self.n, self.own = a, b, s0, n, own

    def off(self, s):
        return self.a + self.b * (s - self.s0)

    def to_host(self, s):
        return s + self.off(s)

    def to_stamp(self, h):
        return (h - self.a + self.b * self.s0) / (1.0 + self.b)

    def describe(self):
        if not self.n:
            return "no stamp map (stamps taken as they are)"
        return ("stamp map: host - stamp %.6f s at stamp %.3f, drifting "
                "%+.1f ppm, from %d point(s)%s" % (
                    self.a, self.s0, 1e6 * self.b, self.n,
                    " of this window's own rows (none read before it)"
                    if self.own else " read before the window"))


class StampMap(object):
    """The witness's stamps -> this process's perf_counter (reviewer,
    2026-11-11). The windows are cut on perf_counter and the rows by their
    stamps; before 2026-11-11 the two were taken as one clock (offset 0,
    rate 1), true of the PCAN on Windows, while on Linux its stamps are
    on the Unix epoch (CAN replay tooling, 2026-11-11, pcanfd ts_mode 2:
    rate -2.5 / -1.8 ppm against perf_counter, latency over the least p99
    3.1 ms, max 8.8 ms). Fitted on the run's own rows: every take adds,
    per FIT_BIN_S of stamps, its least-lagged row as a point, and the map
    is the points' lower envelope (envelope): the reads that came soonest
    after their frames, moved neither by a slow read nor by a reader that
    fell behind for a whole window. Offset and rate only (the rate once the
    points span FIT_RATE_SPAN_S): a step in either
    clock is not fitted, and reader_fault reports one that puts rows ahead
    of the map. No points: offset 0, rate 1."""

    def __init__(self):
        self.pts = []
        self.fit = StampFit()

    def add(self, st):
        new = [(x[0], x[1]) for x in ((st or {}).get("bins") or ())]
        if not new:
            return
        self.pts.extend(new)
        self.pts.sort()
        a, b, s0 = envelope(self.pts)
        self.fit = StampFit(a, b, s0, len(self.pts))

    def frozen(self, own=False):
        """The map as it stands, apart from later points."""
        f = self.fit
        return StampFit(f.a, f.b, f.s0, f.n, own)


def mapped_lags(st, fit):
    """-> (least, most) lag of st's rows against fit (read time less the
    mapped stamp, s); st's own lag figures (offset 0, rate 1) with no map
    or no bins."""
    bins = st.get("bins") or ()
    if fit is None or not bins:
        return st["lag_min"], st["lag_max"]
    return (min(x[1] - fit.off(x[0]) for x in bins),
            max(x[3] - fit.off(x[2]) for x in bins))


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
    out["bins"] = sorted(b for p in parts for b in (p.get("bins") or ()))
    return out


def reader_fault(st, fit=None):
    """"" or a TEST FAULT line: a row read more than READER_LAG_TOL_S after
    its stamp as fit maps it (the reader fell behind, or its stamps run
    behind the map), or one read more than STAMP_CLOCK_TOL_S before it
    (the stamps run ahead of the map). fit None: the stamps as they are
    (offset 0, rate 1). No figures (a fake witness, a dead reader): ""; a
    dead reader is witness_fault's own."""
    if not st or st.get("lag_max") is None:
        return ""
    lmin, lmax = mapped_lags(st, fit)
    if lmax > READER_LAG_TOL_S:
        return ("TEST FAULT: the witness's reader fell behind: a row read "
                "%.3f s after its stamp, beyond %.3f s (or its stamps run "
                "behind the stamp map) -- the window is not judged"
                % (lmax, READER_LAG_TOL_S))
    if lmin < -STAMP_CLOCK_TOL_S:
        return ("TEST FAULT: a witness row was read %.3f s BEFORE its stamp "
                "(%s): its stamps do not follow the map, so the window "
                "cannot be cut by them"
                % (-lmin, fit.describe() if fit is not None
                   else "stamps taken as they are"))
    return ""


def stamp_line(info):
    a, b, c, before, after = info
    return ("rows by stamp: %d kept (%d read before the window, %d by the "
            "cut, %d after it); %d stamped before the window and %d after "
            "it dropped" % (a + b + c, a, b, c, before, after))


def reader_line(st, rows, lo, hi, fit=None):
    """The reader's figures for one window: its rate (rows over the time
    it was not waiting on an empty queue), the Read call's cost, the lag
    against the stamp map (fit; the stamps as they are with none), how
    much of the window its kept rows' stamps cover, and the map."""
    cover = ("stamps %.2f..%.2f s of the window's %.2f s"
             % (rows[0][0] - lo, rows[-1][0] - lo, hi - lo) if rows
             else "no rows")
    mp = "; " + (fit.describe() if fit is not None
                 else "no stamp map (stamps taken as they are)")
    if not st:
        return "reader figures not available; " + cover + mp
    busy = st["wall"] - st["t_wait"]
    if st["lag_max"] is not None:
        lmin, lmax = mapped_lags(st, fit)
        lag = "%.1f..%.1f ms" % (1000 * lmin, 1000 * lmax)
    else:
        lag = "-"
    return ("reader: %d rows in %d reads, %.0f rows/s while not waiting "
            "(%.2f of %.2f s), Read %.1f us/call; lag %s; %s%s" % (
                st["rows"], st["reads"],
                st["rows"] / busy if busy > 0 else float("nan"), busy,
                st["wall"], 1e6 * st["t_read"] / max(st["reads"], 1),
                lag, cover, mp))


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


def judge_node_link(g, expecting, wrong_rate):
    """The node's link counters' growth over one window (kvaser_link.
    counter_growth) -> (line or None, fault or None, frames to forgive), by
    the charge-interposer reviewer's ruling Q1 (2026-11-20):
    - a window expecting the node's frames to complete: a bus-off makes it
      VOID (the fault, reported by node_fault);
    - a wrong-rate window: its bus-offs are reported with it, and the frames
      the kernel dropped meanwhile (tx_dropped's growth) are forgiven at the
      flush when the ACK returns; since the reviewer's ruling (a)
      (2026-11-20) the node's own sends from the span are excused there
      whatever their number, so the count forgives only earlier sends, and
      the flush prints the window's figures (span_figures);
    - any other no-ACK window (the no-ack arm): reported, nothing forgiven.
    tx_aborted_errors is reported beside them (C1, 11-20: 32 of the frames
    the flush missed were not in tx_dropped)."""
    nums = ("%d bus-off(s), %d restart(s), tx_dropped +%d, tx_aborted_errors +%d"
            % (g["bus_off"], g["restarts"], g["tx_dropped"], g["tx_aborted_errors"]))
    if expecting:
        if g["bus_off"]:
            return (None, "the node went bus-off in the window, where its frames were "
                          "expected to complete: " + nums, 0)
        if g["tx_dropped"] or g["tx_aborted_errors"] or g["restarts"]:
            return "node's link in the window: " + nums, None, 0
        return None, None, 0
    if wrong_rate:
        return ("node's link in this wrong-rate window: %s (the node's sends from the "
                "span never echoed are excused at the flush, ruling (a); the %d dropped "
                "may forgive earlier sends there)" % (nums, g["tx_dropped"]), None,
                g["tx_dropped"])
    if any(g.values()):
        return ("node's link in the window: %s (not a wrong-rate window: nothing "
                "forgiven)" % nums, None, 0)
    return None, None, 0


def span_figures(gone, windows):
    """A wrong-rate span's figures, printed at its flush (the charge-
    interposer reviewer's ruling (a), 2026-11-20: the flush does not judge
    the span's sends, and each wrong-rate window still reports its
    tx_dropped growth and its sends neither echoed nor dropped, so its
    share figure can be read against them). gone: [(seq, t_call or None)]
    the sends the flush excused or forgave, never echoed; windows: [(label,
    t0, t1, tx_dropped growth)] the span's wrong-rate windows. -> lines: per
    window, its sends never echoed (by send-call time), tx_dropped's growth
    over it (the link counters read around it), and the first less the
    second as its sends neither echoed nor dropped (0 when tx_dropped grew
    more, which says the excess were frames sent outside it); then the
    span's sends never echoed outside every window, and the totals."""
    lines, left = [], list(gone)
    for lb, a, b, dr in windows:
        n = sum(1 for _s, tc in left if tc is not None and a <= tc <= b)
        left = [(s, tc) for s, tc in left if not (tc is not None and a <= tc <= b)]
        nd = ("%d" % (n - dr) if n >= dr else
              "0 (tx_dropped grew %d more than its sends left unechoed: frames sent "
              "outside it)" % (dr - n))
        lines.append("wrong-rate window %s: %d of its sends never echoed, tx_dropped +%d "
                     "over it, neither echoed nor dropped %s" % (lb, n, dr, nd))
    lines.append("the span: %d sends never echoed, %d of them outside its %d wrong-rate "
                 "window(s); tx_dropped +%d over those windows"
                 % (len(gone), len(left), len(windows), sum(w[3] for w in windows)))
    return lines


def node_busoff_fault(node, what):
    """A window that expected the node's frames to complete and saw it go
    bus-off (window(): judge_node_link) -> a TEST FAULT line, or None."""
    bf = getattr(node, "window_busoff_fault", None)
    return "TEST FAULT: %s: %s" % (what, bf) if bf else None


def node_fault(node, what):
    """(d): a window in which the node's sends failed tests nothing that
    rests on the node -> a TEST FAULT line, or None. A node bus-off in a
    window expecting its frames to complete too (ruling Q1, 2026-11-20)."""
    bf = node_busoff_fault(node, what)
    if bf:
        return bf
    f = getattr(node, "window_fails", 0)
    if f:
        return ("TEST FAULT: %s: %d of the node's sends failed in the window "
                "-- its frames were not completing, so nothing resting on "
                "them is judged" % (what, f))
    return None


LISTEN_ARMS = ("no-ack", "idle", "flood-7F4", "flood-649", "stall")


def listen_arm_keys(spec):
    """--listen-arms: "all" or a comma list of LISTEN_ARMS keys -> the keys,
    in LISTEN_ARMS order; ValueError on an unknown or empty one (tester,
    2026-10-21). The wrong-250k and wrong-1m arms are gone (the reviewer,
    2026-11-27, on the user's ruling: spec 9 leaves the wrong bit rate out
    of the listen-only cases), so their names are refused too."""
    if spec == "all":
        return list(LISTEN_ARMS)
    keys = [k.strip() for k in spec.split(",") if k.strip()]
    bad = [k for k in keys if k not in LISTEN_ARMS]
    if bad or not keys:
        raise ValueError("--listen-arms: unknown %s (known: %s)"
                         % (bad or "(none given)", ", ".join(LISTEN_ARMS)))
    return [k for k in LISTEN_ARMS if k in keys]


def listen_subs(keys):
    """The listen-only windows for the selected keys: (name, commands).
    Each window with commands sets the bus rate, 500k, first."""
    subs = []
    if "idle" in keys:
        subs.append(("idle", None))
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
    node, the witness or the stamps' clock faulted the window. A sub with
    commands sets 500k first; every window then expects the node's frames
    to complete, after the node's flush (Node.expect_acks; the reviewer,
    2026-11-13). No window is at a wrong bit rate (spec 9; the reviewer,
    2026-11-27), so none suspends the stall rule."""
    expect = getattr(node, "expect_acks", lambda on, **kw: None)   # scripted nodes: none
    if cmds:
        board.cmd(cmds[0])
    expect(True)
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
            "bus state(s)%s"
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
    scores it, BITRATE 1M, the arm's settle, then the 1M idle window.
    The local arm's rows go to a scratch_dir run folder, vport-m1-local,
    removed when the step ends, pass or fail: each trial's rows file is
    removed after its trial anyway, and an edges file there
    (--edge-diag) is moved into the run folder first. Never the system
    temp folder (AGENTS.md; reviewer, 2026-10-31)."""
    arms = parse_m1_arms(args.m1_arms)
    local = scratch_dir.make("vport-m1-local")
    try:
        _m1_trials(args, board, wit, node, verdicts, arms, local)
    finally:
        m1_keep_edges(local, RAW_DIR)
        scratch_dir.remove(local)


def m1_keep_edges(local, raw_dir):
    """The local arm's NN_edges.txt files -> raw_dir (the run folder),
    where the run arm's are, so removing the local folder loses only
    the rows files. -> the names moved."""
    moved = []
    if not raw_dir:
        return moved
    for n in sorted(os.listdir(local)):
        if n.endswith("_edges.txt"):
            shutil.move(os.path.join(local, n), os.path.join(raw_dir, n))
            moved.append(n)
    return moved


def _m1_trials(args, board, wit, node, verdicts, arms, local):
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


def judge_bitrate_window(r, name, rows, sent, wit, node, window_s, pst):
    """Step 2, one wrong-rate window after it ends -> (verdict or None,
    lines to print). pst: the PCAN's own status at the window's end. Out of
    run_bench since run 69d8 (2026-11-21), whose vp1-vp3 stopped here on a
    name the coverage change had moved, so the self-test drives every path
    of it to its end."""
    out = []
    for ln in (getattr(wit, "window_failures", []) +
               [getattr(wit, "window_host", "")] +
               getattr(wit, "window_states", []) +
               ["the PCAN's own status at the window's end: "
                "'%s'" % pst if pst else ""]):
        if ln:
            out.append("  %s %s, diagnostic: %s" % (r, name, ln))
    t_ok, why, cwhy = bitrate_judgeable(rows,
                                        getattr(wit, "window_bounds", None))
    out.append("  %s %s, diagnostic: %s" % (r, name, cwhy))
    arm = "bitrate/%s/%s" % (r, name)
    if not t_ok:
        v, ln = refused_arm(why)
        out.append("  %s %s: %s" % (r, name, ln))
        return (arm, v), out
    # (g) here the node failing to complete IS the effect (the board at a
    # wrong rate wrecks traffic): reported, never a fault. Only an
    # incomplete witness faults it.
    wf = witness_fault(wit, "%s %s" % (r, name))
    if wf:
        out.append("  %s, %s: %s" % (r, name, wf))
        return (arm, FAULT), out
    m2, lines = score_bitrate(rows, sent, window_s,
                              node_fails=getattr(node, "window_fails", 0))
    out.append("  %s, %s: %s" % (r, name, lines[0]))
    out.append("    %s; %s" % (lines[1], why))
    flag = bitrate_flag(back_steps(bitrate_stamps(rows))[0],
                        m2["near_threshold"])
    if flag:
        out.append("    " + flag)
    return None, out


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
    t_ep0 = time.time()
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
        not_run = []      # (name, why): a variant not run, named in the verdicts

        # step 0 always runs, before the node opens: every later step that
        # judges an ACK depends on the witness not giving one
        print("\n== 0 witness silence (the witness alone, the node's link DOWN)")
        # D3 (the reviewer, 2026-11-13): "the node closed" is the Kvaser's
        # link DOWN, read back in sysfs (an UP kvaser_usb link ACKs with no
        # socket open)
        try:
            print("    " + kvaser_link.set_link(False))
        except kvaser_link.LinkError as e:
            for ln in start_refused("step 0: %s" % kvaser_link.link_void(
                    e, t_ep0, time.time())[len("VOID: "):]):
                print(ln)
            return 1
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
                # normal mode, where the board's ACK must let them complete.
                # No ACK is expected of the node's frames here: the stall
                # rule is suspended until the witness acknowledges again
                # (the reviewer, 2026-11-13)
                node.expect_acks(False)
                wit = set_witness(wit, False)
                board.cmd("MODE listen")
                rows, _s = window(board, wit, node, 3.0)
                v, lines = score_noack(noack_rows(rows, node))
                ok = (True if v == "unacked" else FAULT if v == "fault"
                      else False)
                verdicts.append(("listen/does-not-ack", ok))
                print("  listen-only does not ACK: %s" % verdict_word(ok))
                for ln in lines:
                    print("    " + ln)
                board.cmd("MODE normal")
                rows, _s = window(board, wit, node, 3.0)
                v2, lines = score_noack(noack_rows(rows, node))
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
            node.expect_acks(True)
            for name, cmds in listen_subs(arms):
                ok, lines = listen_window(board, wit, node, secs, name, cmds)
                verdicts.append(("listen/" + name, ok))
                print("  listen-only, %s: %s" % (name, verdict_word(ok)))
                for ln in lines:
                    print("    " + ln)
            board.cmd("BITRATE 500k")
            node.expect_acks(True)
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
            node.expect_acks(True)           # the witness acknowledging
            link_r0 = node.counters()
            t0 = time.perf_counter()
            node_reads = []
            for at in RECSTART_NODE_AT:
                time.sleep(max(0.0, at - (time.perf_counter() - t0)))
                node_reads.append(("+%.2f s" % at, board.cmd("STATUS")))
            # ruling Q1: the node's frames were expected to complete here
            ln_r, bf_r, _a = judge_node_link(kvaser_link.counter_growth(
                link_r0, node.counters()), True, False)
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
            if ln_r:
                lines = ["node traffic, diagnostic: " + ln_r] + lines
            if bf_r:
                ok, v = FAULT, "VOID (TEST FAULT): node traffic: %s" % bf_r
                lines = ["TEST FAULT: node traffic: %s" % bf_r] + unjudged(lines)
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
            node.expect_acks(False, wrong_rate=True)   # wrong rates: no ACK expected
            for label, rate, node_open in (("1M, node open", "1M", True),
                                           ("250k, node open", "250k", True),
                                           ("1M, node CLOSED", "1M", False),
                                           ("250k, node CLOSED", "250k",
                                            False),
                                           ("500k, node CLOSED (control)",
                                            "500k", False)):
                if node_open and isinstance(node, NoNode):
                    node = Node(args.kvaser_channel).start()
                    node.expect_acks(False, wrong_rate=True)
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
                pst = witness_status(wit)
                st = board.cmd("STATUS")
                wf = witness_fault(wit, label)
                errs = sum(1 for x in rows if x[2])
                others = sum(1 for x in rows if not x[2])
                print("  listen-only %s: %d error frame(s), %d other row(s) "
                      "on the witness%s; the PCAN's own status: %d bus "
                      "state(s)%s, at the end '%s'; board STATUS bus_err=%s "
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
            node.expect_acks(True)

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
            node.expect_acks(False, wrong_rate=True)   # wrong rates: no ACK expected
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
                          "of %d sent, %d PCAN bus state(s)%s"
                          % (label, errs, seen, len(sent), pw,
                             "".join(" -- " + f for f in faults)))
                    if state == "off":
                        board.cmd("DRIVER on")
            board.cmd("BITRATE 500k")
            node.expect_acks(True)
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
            node.expect_acks(False, wrong_rate=True)   # wrong rates: no ACK expected
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
                    # the PCAN's own status as the window ends (2026-11-20:
                    # with its bus states, why a window has few rows)
                    pst = witness_status(wit)
                    board.cmd("STOP")
                    board.cmd("STATUS")
                    v, lines = judge_bitrate_window(
                        r, name, rows, sent, wit, node,
                        args.bitrate_seconds, pst)
                    for ln in lines:
                        print(ln)
                    if v:
                        verdicts.append(v)
            board.cmd("BITRATE 500k")
            board.cmd("MODE listen")
            node.expect_acks(True)
            print("  " + process_snapshot(os.path.join(
                RAW_DIR or ".", "processes_step2_end.csv")))

        if "m1" in steps:
            wit = set_witness(wit, True)
            node.expect_acks(False, wrong_rate=True)   # wrong rates: no ACK expected
            run_m1(args, board, wit, node, verdicts)
            node.expect_acks(True)           # m1 ends at 500k, listen-only

        if "flood" in steps:
            print("\n== 3 flood: delay added to the node's 0x%X" % NODE_ID)
            wit = set_witness(wit, True)
            board.cmd("MODE normal")
            node.expect_acks(True)           # the witness and the board ACK
            # settle (reviewer, 2026-10-16): the 00:01 baseline ran straight
            # after step 2's 1M flood and lost a node send. The board idle,
            # wait for SETTLE_CLEAN_S with no failed node send.
            settled, took = settle_node(node.take_fails, time.perf_counter,
                                        time.sleep)
            print("  node settle (board idle in normal mode at 500k): %s "
                  "after %.1f s" % ("settled, %.0f s with no failed send"
                                    % SETTLE_CLEAN_S if settled else
                                    "NEVER SETTLED", took))
            print("  node process priority: %s"
                  % prio_text(getattr(node, "prio", None)))
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
            node.expect_acks(True)           # the board acknowledging
            rows, _s = window(board, wit, node, 2.0, "STALL %d" % STALL_N,
                              from_command=True)
            ok, _m, lines = score_stall(rows, STALL_N, acked=True)
            bf = node_busoff_fault(node, "stall, peer ACKing")
            if bf:                           # ruling Q1: a node bus-off VOIDs it
                ok, lines = FAULT, [bf] + unjudged(lines)
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
                node.expect_acks(True)       # the board acknowledging
            else:
                not_run.append(("stall/no-acker", NO_ACKER_WHY))
                print("    no-ACKer variant NOT RUN: " + NO_ACKER_WHY)
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
        for ln in verdict_lines(verdicts, not_run):
            print(ln)
        rc, final = combine(verdicts)
        if final:
            print(final)
    except NodeStall as e:
        # a kvaser TX stall where the node's frames were expected to
        # complete (D3/D5): the run is VOID, its verdicts so far not judged,
        # and the Kvaser recovered once the node's process has closed it
        print("\n".join(stall_report(e.args[0], t_ep0, node)))
        node = None
        rc = 3
    except kvaser_link.LinkError as e:
        print(kvaser_link.link_void(e, t_ep0, time.time()))
        rc = 3
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


STEP_HEAD = re.compile(r'^        if "(\w+)" in steps:\s*$', re.M)


def step_blocks(src):
    """run_bench's source -> {step name: its block's text}, each up to the
    next step or the verdicts (the self-test's check of the node's stall
    rule per step)."""
    heads = list(STEP_HEAD.finditer(src))
    end = src.find('print("\\n== verdicts")')
    out = {}
    for i, m in enumerate(heads):
        stop = heads[i + 1].start() if i + 1 < len(heads) else end
        out[m.group(1)] = src[m.start():stop]
    return out


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
    check("adapter_roles.pcanfd(" in wcode and "BusState.PASSIVE" in wcode
          and "kvaser" not in wcode,
          "the witness is the PCAN, role pcanfd, opened PASSIVE (not the Kvaser)")
    check("kvaser_link.open_bus(receive_own_messages=True, error_frames=False)" in nsrc
          and "kvaser_link.set_link(True, restart_ms=NODE_RESTART_MS)" in nsrc
          and nsrc.count("kvaser_link.set_link(") == 1 and NODE_RESTART_MS == 100,
          "the node is the Kvaser on kvaser_usb, its link brought up first with "
          "restart-ms 100 (ruling Q1, 2026-11-20), its echoes kept (2026-11-13), no "
          "error frames taken (2026-11-20)")
    check("wrong=wrong_ev.is_set if wrong_ev is not None" in inspect.getsource(_node_proc)
          and "self.forgive_v,\n            self.wrong_ev), daemon=True)"
          in inspect.getsource(Node),
          "the node process is handed the wrong-rate mark, and its sender reads it "
          "(ruling (a), 2026-11-20)")

    # 2026-11-13, the reviewer's D3/D5 and the (a)/(b) ruling: the node's
    # stall rule per window kind, a frame sent when its echo came back,
    # each echo in the window of its send call, a flush kept out of the
    # next window
    class _W(object):
        def __init__(self, enobufs=False, stall_on_finish=False):
            self.suspended, self.pending, self.calls = False, [], []
            self.enobufs, self.stall_on_finish, self.rebased = enobufs, stall_on_finish, []

        def send(self, msg, timeout=None):
            if self.enobufs:
                raise kvaser_link.Stall("kvaser TX stall: send refused with ENOBUFS", 1.0)
            self.calls.append(msg)

        def poll(self, timeout=0.0):
            pass

        def rebase(self, t):
            self.rebased.append(t)

        def finish(self, forgive=0, excuse=None):
            self.forgiven_asked = forgive
            if self.stall_on_finish:
                raise kvaser_link.Stall("kvaser TX stall: no own echo 101 ms", 2.0)
            self.pending = []
            return []
    exp, put = [True], []
    clock = [10.0]
    w = _W()
    snd = node_sender(w, lambda s: s, lambda: exp[0], lambda: clock[0], put.append)
    snd(0)
    exp[0] = False
    snd(1)
    check(w.suspended and w.calls == [0, 1],
          "node: a no-ACK window suspends the bound and keeps sending")
    w.enobufs = True
    try:
        snd(2)
        check(False, "node: ENOBUFS in a no-ACK window raised as a stall")
    except kvaser_link.Stall:
        check(False, "node: ENOBUFS in a no-ACK window raised as a stall")
    except RuntimeError as e:
        check("refused" in str(e), "node: ENOBUFS in a no-ACK window is a refused send")
    w.enobufs = False
    w.pending = ["held", "held"]
    exp[0] = True
    clock[0] = 12.5
    snd(3)
    check(not w.suspended and w.rebased == [12.5] and put and put[-1][:2] == ("expected", 2),
          "node: the ACK back -> the held frames' bound from that moment, flushed and "
          "reported before the next send -> %s" % put)
    w.enobufs = True
    try:
        snd(4)
        check(False, "node: ENOBUFS where frames should complete was not a stall")
    except kvaser_link.Stall:
        check(True, "node: ENOBUFS where frames should complete is a stall")
    w2 = _W(stall_on_finish=True)
    exp2 = [False]
    snd2 = node_sender(w2, lambda s: s, lambda: exp2[0], lambda: 0.0, [].append)
    snd2(0)
    w2.pending = ["held"]
    exp2[0] = True
    try:
        snd2(1)
        check(False, "node: a held frame not echoed within 100 ms of the ACK was accepted")
    except kvaser_link.Stall:
        check(True, "node: a held frame not echoed within 100 ms of the ACK is a stall")
    for kind, held_flush, why in held_flush_cases():
        check(held_flush[0] == "expected" and held_flush[1] == _HeldBus.HOLD,
              "node: %s, the ACK back -> every held frame's echo read, the flush "
              "passes (%s) -> %s" % (kind, why, held_flush[:4]))
    # ruling Q1 (2026-11-20): frames the kernel dropped while the node was
    # bus-off never echo; the flush forgives as many as tx_dropped grew in
    # the wrong-rate windows, and no more
    w3 = _W()
    exp3 = [False]
    snd3 = node_sender(w3, lambda s: s, lambda: exp3[0], lambda: 0.0, [].append,
                       forgive=lambda: 7)
    snd3(0)
    exp3[0] = True
    snd3(1)
    check(getattr(w3, "forgiven_asked", None) == 7,
          "node: the flush is handed the drops to forgive -> %s"
          % getattr(w3, "forgiven_asked", None))
    for kind, held_flush, why in held_flush_cases(drop=5, forgive=5):
        check(held_flush[0] == "expected" and held_flush[1] == _HeldBus.HOLD
              and held_flush[3] == 5 and len(held_flush[4]) == 5
              and [int.from_bytes(bytes(k[2][:4]), "little") for k in held_flush[4]]
              == [0, 1, 2, 3, 4],
              "node: %s, 5 held frames dropped by the kernel, 5 forgiven -> the flush "
              "passes, the 5 named -> %s" % (kind, held_flush[:4]))
    for drop, fg in ((5, 4), (5, 0)):
        for kind, held_flush, why in held_flush_cases(drop=drop, forgive=fg):
            check(held_flush[0] == "stall" and (
                not fg or "%d unaccounted" % (drop - fg) in held_flush[1]),
                "node: %s, %d dropped, %d forgiven -> the flush stalls%s -> %s"
                % (kind, drop, fg, ", naming the unaccounted" if fg else "",
                   held_flush))
    # ruling (a) (2026-11-20): the sends from a wrong-rate span are not
    # judged at the flush, however many never echo; a no-ACK send before the
    # span turned wrong-rate still is (with Q1's count)
    for kind, held_flush, why in held_flush_cases(drop=20, forgive=0, wrong_from=0):
        check(held_flush[0] == "expected" and held_flush[1] == _HeldBus.HOLD
              and held_flush[3] == 20 and len(held_flush) > 5 and held_flush[5] == 20
              and [int.from_bytes(bytes(k[2][:4]), "little") for k in held_flush[4]]
              == list(range(20)),
              "node: %s, 20 sends of a wrong-rate span never echo, nothing forgiven by "
              "count -> the flush passes, the 20 excused and named -> %s"
              % (kind, held_flush[:4] + held_flush[5:]))
    for kind, held_flush, why in held_flush_cases(drop=5, forgive=0, wrong_from=10):
        check(held_flush[0] == "stall" and "5 sends unechoed" in held_flush[1],
              "node: %s, 5 no-ACK sends from before the span turned wrong-rate never "
              "echo -> the flush stalls on them -> %s" % (kind, held_flush))
    for kind, held_flush, why in held_flush_cases(drop=15, forgive=10, wrong_from=10):
        check(held_flush[0] == "expected" and held_flush[3] == 15 and held_flush[5] == 5,
              "node: %s, 10 sends before the span and 5 in it never echo, 10 forgiven "
              "by count -> the flush passes, 5 excused -> %s"
              % (kind, held_flush[:4] + held_flush[5:]))
    for kind, held_flush, why in held_flush_cases(drop=15, forgive=5, wrong_from=10):
        check(held_flush[0] == "stall" and "5 of them excused" in held_flush[1]
              and "5 unaccounted" in held_flush[1],
              "node: %s, 10 sends before the span and 5 in it never echo, 5 forgiven by "
              "count -> the flush stalls on the other 5 -> %s" % (kind, held_flush))
    log = []
    stalls = [0]

    def stall_send(seq):
        if seq == 3:
            raise kvaser_link.Stall("kvaser TX stall: planted", 1.0)
    try:
        node_loop(stall_send, lambda: 0.0, lambda s: None, log.append,
                  lambda: len(log) > 10)
    except kvaser_link.Stall:
        stalls[0] = 1
    check(stalls[0] == 1 and len(log) == 3,
          "node_loop: a stall ends the loop, never logged as a refused send (%d logged)"
          % len(log))
    shifted = []
    node_loop(lambda seq: 0.25 if seq == 2 else None, lambda: 0.0,
              lambda s: shifted.append(s), [].append, lambda: len(shifted) > 4)
    check(shifted[2] >= 0.27, "node_loop: a flush's pause shifts the schedule (no "
                              "catch-up burst): sleeps %s" % shifted[:4])
    wins = [("no-ack listen", 100.0, 103.0)]
    ech = [(1, 100.5, 100.5005, 104.0),      # sent in no-ack, flushed into the next
           (5, 104.1, 104.1004, 104.1012),   # this window's, echoed at once
           (6, 104.2, 104.2004, 104.4),      # this window's, echoed 200 ms late
           (9, 107.5, 107.5004, 107.501)]    # after the cut
    sent, fl = window_sent(ech, 104.0, 107.0, wins)
    check(sent == [5] and fl == {"no-ack listen": 1},
          "window_sent: sent = this window's sends echoed within 100 ms; the flush "
          "from 'no-ack listen' kept out and named -> %s %s" % (sent, fl))
    rows = [(104.0005, NODE_ID, False, bytes(node_payload(1))),
            (104.1008, NODE_ID, False, bytes(node_payload(5))),
            (104.15, 0x7F4, False, bytes(8))]
    kept, n_out, gone = own_node_rows(rows, [(5, 0.0, 1, 104.1, 104.1004)])
    check(n_out == 1 and [node_seq(r) for r in kept] == [5, None] and gone == [1],
          "own_node_rows: the flushed frame on the wire kept out of the window's rows")
    kept, n_out, gone = own_node_rows(rows, [], sent=[5])
    check(n_out == 1 and [node_seq(r) for r in kept] == [5, None],
          "own_node_rows: a seq the window sent counts as its own with no log entry "
          "yet (the stall-16 item, 2026-11-20)")
    ln = flushed_line({"no-ack listen": 1}, 3, [1, 2, 3, 9])
    check("flushed from no-ack listen: 1" in ln and "3 on the wire (seq 1..3, 9)" in ln,
          "the flush recorded, with the seqs kept out: %s" % ln)

    class _CutNode(object):
        """The stall-16 cut (C3, 2026-11-20): seqs 1 and 2 both called before
        the cut and both echoed, but only seq 1's log entry has come when
        the window takes its log at the cut."""

        def __init__(self):
            self.windows, self.ke, self.kl, self.base = [], 0, 0, None

        def take_echoes(self):
            self.ke += 1
            if self.ke == 1:
                self.base = time.perf_counter()
                time.sleep(0.003)         # both sends called before the cut
                return []
            if self.ke == 2:
                b = self.base
                return [(1, b + 1e-3, b + 11e-4, b + 12e-4), (2, b + 2e-3, b + 21e-4, b + 22e-4)]
            return []

        def take_fails(self):
            return 0

        def take_log(self):
            self.kl += 1
            if self.kl == 2:
                return [(1, 0.0, 1, self.base + 1e-3, self.base + 11e-4)]
            return []

    class _CutWit(object):
        stats = None

        def __init__(self, node):
            self.node, self.k = node, 0

        def take(self):
            self.k += 1
            if self.k != 2:
                return []
            b = self.node.base
            return [(b + 16e-4, NODE_ID, False, bytes(node_payload(1))),
                    (b + 26e-4, NODE_ID, False, bytes(node_payload(2)))]

        def take_incomplete(self):
            return 0
    cn = _CutNode()
    rows_c, sent_c = window(None, _CutWit(cn), cn, 0.0)
    okc, lines_c = score_listen(rows_c, sent_c)
    check(sent_c == [1, 2] and okc,
          "the window's cut: a send called before the cut, its log entry not yet "
          "come, has its row kept (C3's stall 16, seq 5211) -> sent %s, %s"
          % (sent_c, lines_c))

    class _SlowCountNode(_CutNode):
        """D2 (2026-11-20): reading the link counters takes 0.67-0.77 s on
        the bench, and the node sends meanwhile. Each read before the cut
        here calls a send, seq 99 (the last such call is the one echoed),
        with no log entry in the window and no row."""

        def __init__(self):
            _CutNode.__init__(self)
            self.tc99 = None

        def counters(self):
            if self.ke < 2:
                self.tc99 = time.perf_counter()
                time.sleep(0.002)
            return {"bus_off": 0, "restarts": 0, "tx_dropped": 0, "tx_aborted_errors": 0}

        def expecting(self):
            return True

        def take_echoes(self):
            e = _CutNode.take_echoes(self)
            if self.ke == 2:
                t = self.tc99
                e = [(99, t, t + 1e-4, t + 2e-4)] + e
            return e
    sc = _SlowCountNode()
    sc.forgive = 0
    rows_sc, sent_sc = window(None, _CutWit(sc), sc, 0.0)
    ok_sc, lines_sc = score_listen(rows_sc, sent_sc)
    check(sent_sc == [1, 2] and ok_sc,
          "window(): a frame sent while the link counters are read is not the window's "
          "(D2's 67-69 frames 'never reached the witness') -> sent %s, %s"
          % (sent_sc, lines_sc))

    # ruling Q1 (2026-11-20): the node's link counters per window
    def _g(bo=0, rs=0, dr=0, ab=0):
        return {"bus_off": bo, "restarts": rs, "tx_dropped": dr, "tx_aborted_errors": ab}
    ln, f, a = judge_node_link(_g(1, 1, 173, 0), True, False)
    check(f and "bus-off" in f and a == 0,
          "node link: a bus-off where frames were expected to complete is a fault -> %s" % f)
    check(judge_node_link(_g(), True, False) == (None, None, 0),
          "node link: nothing in an ACK window -> no line, no fault")
    ln, f, a = judge_node_link(_g(2, 2, 173, 32), False, True)
    check(not f and a == 173 and "2 bus-off(s)" in ln and "wrong-rate" in ln
          and "tx_aborted_errors +32" in ln,
          "node link: a wrong-rate window reports its bus-offs and forgives tx_dropped's "
          "growth -> %s" % ln)
    ln, f, a = judge_node_link(_g(0, 0, 0, 0), False, True)
    check(not f and a == 0 and "0 bus-off(s)" in (ln or ""),
          "node link: a wrong-rate window reports even none -> %s" % ln)
    ln, f, a = judge_node_link(_g(1, 1, 50, 0), False, False)
    check(not f and a == 0 and "nothing forgiven" in (ln or ""),
          "node link: a no-ACK window that is not wrong-rate forgives nothing -> %s" % ln)

    class _LinkNode(_CutNode):
        """A node whose link counters grow by `grow` over the window."""

        def __init__(self, grow, expecting, wrong_rate):
            _CutNode.__init__(self)
            self.grow, self.exp, self.wrong_rate, self.forgive = grow, expecting, wrong_rate, 0
            self.reads, self.span_windows = 0, []

        def counters(self):
            self.reads += 1
            c = {"bus_off": 3, "restarts": 3, "tx_dropped": 40, "tx_aborted_errors": 0,
                 "state": "ERROR-ACTIVE"}
            if self.reads > 1:
                for k, v in self.grow.items():
                    c[k] += v
            return c

        def expecting(self):
            return self.exp
    lw = _LinkNode(_g(1, 1, 173, 0), False, True)
    window(None, _CutWit(lw), lw, 0.0)
    lw.reads = 0
    window(None, _CutWit(lw), lw, 0.0)
    check(lw.forgive == 346 and node_fault(lw, "x") is None,
          "window(): each wrong-rate window's drops added to the node's forgiveness, no "
          "fault -> forgive %d" % lw.forgive)
    check([(w[0], w[3]) for w in lw.span_windows] == [("window", 173), ("window", 173)]
          and all(w[1] < w[2] for w in lw.span_windows),
          "window(): each wrong-rate window kept for the span's figures, with its bounds "
          "and tx_dropped's growth (ruling (a)) -> %s" % lw.span_windows)
    la = _LinkNode(_g(1, 1, 0, 0), True, False)
    window(None, _CutWit(la), la, 0.0)
    check(la.span_windows == [],
          "window(): a window expecting the node's frames is not a span window -> %s"
          % la.span_windows)
    ln_ = _LinkNode(_g(0, 0, 9, 0), False, False)
    window(None, _CutWit(ln_), ln_, 0.0)
    check(ln_.span_windows == [],
          "window(): a no-ACK window that is not wrong-rate is not a span window -> %s"
          % ln_.span_windows)
    nf_a = node_fault(la, "listen-only, idle")
    check(la.forgive == 0 and nf_a and nf_a.startswith("TEST FAULT: listen-only, idle: the "
                                                       "node went bus-off"),
          "window(): a node bus-off in a window expecting its frames to complete -> "
          "node_fault VOIDs it -> %s" % nf_a)
    blk = step_blocks(inspect.getsource(run_bench))
    check("node_busoff_fault(node, \"stall, peer ACKing\")" in blk.get("stall", "")
          and "judge_node_link(kvaser_link.counter_growth(" in blk.get("recstart", ""),
          "the stall step's acked window and recstart's node traffic VOID on a node bus-off")
    sn = _LinkNode(_g(1, 1, 173, 0), True, False)
    sn.link_start = sn.counters()
    sn.close = lambda: None
    rep_s = stall_report(("stall", "kvaser TX stall: planted", 1794590000.0), 0.0, sn,
                         lambda s, u: "", lambda **kw: (True, "recovered"))
    check(len(rep_s) == 3 and rep_s[1].startswith("the node's link since it started: 1 "
                                                  "bus-off(s)") and "tx_dropped +173" in rep_s[1],
          "stall_report: the node's bus-offs and drops since its start beside the VOID line "
          "-> %s" % rep_s[1:])

    # ruling Q3 (2026-11-20): score_noack counts the window's rows of the
    # node's head frame, unechoed as the window starts and retried through
    # it, whatever window sent it; every other earlier frame stays out
    class _HeadNode(_CutNode):
        def head(self):
            return 0

        def take_echoes(self):
            self.ke += 1
            if self.ke == 1:
                self.base = time.perf_counter()
            return []

        def take_log(self):
            self.kl += 1
            if self.kl == 2:      # the window's sends: refused, nothing acknowledging
                return [(80 + i, 0.0, 0, self.base + i * 1e-3, self.base + i * 1e-3)
                        for i in range(5)]
            return []

    class _HeadWit(_CutWit):
        def take(self):
            self.k += 1
            if self.k != 2:
                return []
            b = self.node.base
            return ([(b + i * 2e-4, NODE_ID, False, bytes(node_payload(0)))
                     for i in range(NOACK_MIN_ROWS + 5)]
                    + [(b + 3e-3, NODE_ID, False, bytes(node_payload(7)))])
    hn = _HeadNode()
    rows_h, _s = window(None, _HeadWit(hn), hn, 0.0)
    v_h, lines_h = score_noack(noack_rows(rows_h, hn))
    v_out, lines_out = score_noack(rows_h)
    check(v_h == "unacked" and hn.window_head == 0
          and len(hn.window_head_rows) == NOACK_MIN_ROWS + 5
          and not any(node_seq(r) is not None for r in rows_h),
          "no-ack: the head frame's retries (sent before the window) counted by "
          "score_noack, the earlier frame seq 7 and the share/timing rows still "
          "without them -> %s, %s" % (v_h, lines_h))
    check(v_out == "fault" and "TEST FAULT" in lines_out[0],
          "can-fail: the head frame kept out reads TEST FAULT -> %s" % lines_out)

    # the node's head frame (ruling Q3): the oldest seq sent and not echoed;
    # an echo before its log entry; frames a flush forgave leave it
    hd = Node()
    for x in [(0, 0.0, 1, 1.0, 1.0), (1, 0.0, 1, 1.01, 1.01), (2, 0.0, 0, 1.02, 1.02),
              ("echo", 5, 1.03, 1.03, 1.031), (5, 0.0, 1, 1.03, 1.03)]:
        hd.take_msg(x)
    h0 = hd.head()
    hd.take_msg(("echo", 0, 1.0, 1.0, 2.0))
    h1 = hd.head()
    hd.take_msg(("expected", 1, 0.001, 1, [(NODE_ID, False, bytes(node_payload(1)))]))
    check((h0, h1, hd.head()) == (0, 1, None),
          "Node.head: 0 while unechoed, a refused send and an early echo never the "
          "head, 1 after 0 echoes, none once 1 is forgiven -> %s"
          % ((h0, h1, hd.head()),))

    # ruling Q1: a wrong-rate span's drops reach the flush, then reset
    import threading as _th
    fn = Node()
    fn.expect_ev.set()
    fn.expect_acks(False, wrong_rate=True)
    fn.forgive = 6                        # what window() adds in the wrong-rate windows
    seen = []

    def _flush():
        fn.expect_ev.wait(2.0)
        seen.append(fn.forgive_v.value)
        fn.take_msg(("expected", 80, 0.01, 6, []))
    tf = _th.Thread(target=_flush)
    tf.start()
    wr, we = fn.wrong_rate, fn.wrong_ev.is_set()
    import contextlib as _cl
    import io as _io
    out_fn = _io.StringIO()
    with _cl.redirect_stdout(out_fn):
        fn.expect_acks(True)
    tf.join(2.0)
    check("the span: 0 sends never echoed" in out_fn.getvalue(),
          "Node.expect_acks: a wrong-rate span's flush prints its figures -> %r"
          % out_fn.getvalue())
    we_after = fn.wrong_ev.is_set()
    fn.expect_acks(False)
    check(wr and seen == [6] and fn.forgive == 0 and fn.forgive_v.value == 0
          and not fn.wrong_rate,
          "Node.expect_acks: a wrong-rate span's drops handed to the flush, then "
          "reset; a no-ACK span after is not wrong-rate -> %s" % seen)
    check(we and not we_after and not fn.wrong_ev.is_set(),
          "Node.expect_acks: the node process told of a wrong-rate span, untold after its "
          "flush, and a plain no-ACK span is not one (ruling (a)) -> %s"
          % ((we, we_after, fn.wrong_ev.is_set()),))
    fn.expect_acks(False, wrong_rate=True)
    check(fn.wrong_ev.is_set() and not fn.expect_ev.is_set(),
          "Node.expect_acks: a no-ACK span that turns wrong-rate tells the node process")

    # ruling (a): the flush's never-echoed sends traced to their windows by
    # their send calls, and each wrong-rate window's figures
    fa = Node()
    for x in [(0, 0.0, 1, 1.00, 1.0), (1, 0.0, 1, 1.01, 1.01), (2, 0.0, 1, 2.00, 2.0),
              (3, 0.0, 1, 2.01, 2.01), (4, 0.0, 1, 2.50, 2.5), (5, 0.0, 1, 3.00, 3.0),
              ("echo", 1, 1.01, 1.01, 9.0)]:
        fa.take_msg(x)
    fa.take_msg(("expected", 5, 0.1, 5, [(NODE_ID, False, bytes(node_payload(s)))
                                         for s in (0, 2, 3, 4, 5)], 5))
    check(sorted(fa.flush_gone) == [(0, 1.0), (2, 2.0), (3, 2.01), (4, 2.5), (5, 3.0)]
          and fa.call_t == {},
          "Node: the flush's never-echoed sends kept with their send calls -> %s"
          % fa.flush_gone)
    sf = span_figures(fa.flush_gone, [("w1", 0.9, 1.1, 0), ("w2", 1.9, 2.1, 1),
                                      ("w3", 2.9, 3.1, 4)])
    check(len(sf) == 4
          and sf[0] == ("wrong-rate window w1: 1 of its sends never echoed, tx_dropped +0 "
                        "over it, neither echoed nor dropped 1")
          and sf[1].startswith("wrong-rate window w2: 2 of its sends never echoed, "
                               "tx_dropped +1 over it, neither echoed nor dropped 1")
          and sf[2].startswith("wrong-rate window w3: 1 of its sends never echoed, "
                               "tx_dropped +4 over it, neither echoed nor dropped 0 "
                               "(tx_dropped grew 3 more")
          and sf[3].startswith("the span: 5 sends never echoed, 1 of them outside its 3 "
                               "wrong-rate window(s); tx_dropped +5"),
          "span_figures: per window its sends never echoed, tx_dropped's growth and the "
          "difference, then the span's -> %s" % sf)

    class _EN(object):
        """A node with echoes: one flushed frame read at the window's start."""

        def __init__(self):
            self.windows, self.k = [("no-ack", 0.0, 0.5)], 0

        def take_echoes(self):
            self.k += 1
            if self.k == 1:
                return [(1, 0.25, 0.2505, time.perf_counter())]
            return []

        def take_fails(self):
            return 0

        def take_log(self):
            return []

    class _EW(object):
        stats = None

        def take(self):
            return []

        def take_incomplete(self):
            return 0
    en = _EN()
    rows_e, sent_e = window(None, _EW(), en, 0.0)
    check(sent_e == [] and en.window_flushed == {"no-ack": 1} and len(en.windows) == 2,
          "can-fail: a flush read at the next window's start is not that window's "
          "traffic -> sent %s, flushed %s" % (sent_e, en.window_flushed))
    planted = "2026-11-13T12:00:00.1-08:00 h kernel: usb 1-3.2.1: error -71\n"
    rep = stall_report(("stall", "kvaser TX stall: planted", 1794590000.0), 0.0, None,
                       lambda s, u: planted, lambda **kw: (
                           kw == {"restart_ms": NODE_RESTART_MS},
                           "kvaser recovered: planted %s" % kw))
    check(rep[0].startswith("VOID: kvaser TX stall: planted at") and "error -71" in rep[0]
          and rep[1] == "kvaser recovered: planted {'restart_ms': %d}" % NODE_RESTART_MS,
          "stall_report: the VOID line with the kernel's lines, then the recovery, "
          "the node's restart-ms asked again -> %s" % rep[1:])

    class _L(object):
        calls = []

        @classmethod
        def set_link(cls, up):
            cls.calls.append(up)
            return "kvaser link down"
    NoNode(_L)
    check(_L.calls == [False], "NoNode: the Kvaser's link DOWN, read back")
    rsrc = inspect.getsource(run_bench)
    i0, i1 = rsrc.find("kvaser_link.set_link(False)"), rsrc.find('board.cmd("SEND %X 1"')
    check(0 <= i0 < i1, "step 0: the node's link DOWN and read back before the probe")
    check("above_normal()" in inspect.getsource(_node_proc)
          and 'get_context("spawn")' in inspect.getsource(Node),
          "the node runs in its own (spawned) process at ABOVE_NORMAL")
    # the node's start (the reviewer's ruling (2), 2026-11-20): step 0 leaves
    # the board and the witness listen-only, so the node starts with no ACK
    # expected, and each step that has its frames complete turns it on
    fresh = Node()
    check(not fresh.expect_ev.is_set(),
          "the node starts with no ACK expected (nothing acknowledges it after step 0)")
    blocks = step_blocks(rsrc)
    s0 = rsrc[rsrc.find("== 0 witness silence"):rsrc.find('if "listen" in steps')]
    check("Node(args.kvaser_channel).start()" in s0 and "expect_acks(True)" not in s0
          and "set_witness(wit, True)" not in s0,
          "step 0 starts the node with no ACKer on the bus and no ACK expected")
    need = ("listen", "recstart", "discrim", "offpair", "bitrate", "m1", "flood", "stall")
    lack = [s for s in need if "node.expect_acks(True)" not in blocks.get(s, "")]
    check(sorted(blocks) == sorted(need + ("dominant",)) and not lack,
          "each step whose node frames complete turns the stall rule on (missing: %s; "
          "steps %s)" % (lack, sorted(blocks)))
    for s in ("flood", "stall"):
        b = blocks.get(s, "")
        i_ack = b.find('board.cmd("MODE normal")')
        i_on = b.find("node.expect_acks(True)")
        i_win = b.find("window(")
        check(0 <= i_ack < i_on < i_win,
              "%s: the stall rule on after the board's ACK and before its first window"
              % s)

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
    # coverage (the reviewer, 2026-11-21): a 60.2 s window at stamp 100.0
    lo, hi = 100.0, 160.2
    full = [lo + 0.01 + 0.01 * k for k in range(6018)]   # every 10 ms
    one_gap = [t for t in full if not 130.0 < t < 130.2]  # a 0.2 s gap
    cases = [
        ("full, 10 ms apart", full, (lo, hi), True),
        ("a 0.2 s gap inside", one_gap, (lo, hi), True),
        ("E3's 1M flood: 0.045 s of 60.1 s",
         [lo + 0.0004 * k for k in range(110)], (lo, lo + 60.1), False),
        ("a 0.3 s gap inside", [t for t in full if not 130.0 < t < 130.3],
         (lo, hi), False),
        ("first stamp 0.3 s after the start", [t for t in full
                                               if t > lo + 0.3], (lo, hi),
         False),
        ("last stamp 0.3 s before the end", [t for t in full
                                             if t < hi - 0.3], (lo, hi),
         False),
        ("no stamps", [], (lo, hi), False),
        ("no bounds", full, (None, None), False),
    ]
    got = [(n, coverage_ok(ts, *b)[0]) for n, ts, b, _w in cases]
    check(got == [(n, w) for n, _ts, _b, w in cases],
          "can-fail: coverage: judged only when the stamps cover the window "
          "(0.25 s at each end, 0.25 s between stamps) -> %s" % got)
    ok, why = coverage_ok([lo + 0.0004 * k for k in range(110)], lo,
                          lo + 60.1)
    check(not ok and "do not cover" in why
          and "0.000..0.044 s of the window's 60.100 s" in why,
          "can-fail: coverage: the refusal names the stamps' span -> %s" % why)
    # step 2's gate: E3's 1M flood (10 ms error frames over 0.045 s) passes
    # timing_ok and is refused on coverage; b467's kind of window is judged
    e3 = [(lo + 0.0004 * k, 0, True, b"") for k in range(110)]
    b4 = [(t, 0, True, b"") for t in full]
    gates = [(bitrate_judgeable(e3, (lo, lo + 60.1))[0],
              timing_ok([x[0] for x in e3], 1.0, back_tol_ms=BACK_TOL_MS)[0]),
             (bitrate_judgeable(b4, (lo, hi))[0],
              timing_ok([x[0] for x in b4], 1.0, back_tol_ms=BACK_TOL_MS)[0]),
             bitrate_judgeable(e3[:5], (lo, lo + 60.1))[1],
             bitrate_judgeable([(t, 0x100, t > lo + 9.0, b"")
                                for t in full], (lo, hi))[0]]
    check(gates == [(False, True), (True, True),
                    "fewer than 10 witness timestamps", True],
          "can-fail: step 2's gate: E3's 1M flood refused on coverage though "
          "timing_ok passes it; a covered window judged; timing_ok's own "
          "refusal kept first; a 250k idle window with node frames only for "
          "its first 9 s (b467, E5) covered by every row's stamps -> %s"
          % (gates,))
    # step 2's window judgement, each path to its end (run 69d8: vp1-vp3
    # stopped on a NameError after a judged 250k idle window)

    class _W:
        window_failures, window_host = [], "host line"
        window_states = ["the PCAN's bus state x2: passive"]
        window_bounds = (lo, hi)
        window_incomplete, window_stamp_fault = 0, ""

    class _N:
        window_fails = 3

    def jw(rows, **kw):
        w = _W()
        for k, val in kw.items():
            setattr(w, k, val)
        try:
            return judge_bitrate_window("250k", "idle", rows, [1, 2], w,
                                        _N(), 60.0, "0x4 BUSLIGHT (light)")
        except Exception as e:                          # noqa: BLE001
            return "raised %s: %s" % (type(e).__name__, e), []
    v, out = jw(b4 + [_pframe(lo + 1.0, 1)])
    check(v is None and len(out) == 6
          and out[0] == "  250k idle, diagnostic: host line"
          and out[1] == ("  250k idle, diagnostic: the PCAN's bus state x2: "
                         "passive")
          and out[2] == ("  250k idle, diagnostic: the PCAN's own status at "
                         "the window's end: '0x4 BUSLIGHT (light)'")
          and "witness stamps" in out[3]
          and out[4].startswith("  250k, idle: 6018 error frame(s)")
          and "of the 5 it tried" in out[4]
          and out[5].startswith("    error-frame gaps within 2 ms")
          and not any("FLAG" in x for x in out),
          "can-fail: step 2's window judgement: a covered window is scored "
          "to its end (diagnostics, the cover line, the score and its gaps "
          "line), no verdict, no FLAG -> %s %s" % (v, out))
    back = list(b4)
    back[3000] = (back[2999][0] - 0.001,) + back[3000][1:]
    v, out = jw(back)
    check(v is None and out and out[-1].startswith("    FLAG: 1 backward "
                                                    "stamp step(s)"),
          "can-fail: step 2's window judgement: a tolerated backward step "
          "with gaps near the burst threshold ends in the FLAG line -> %s %s"
          % (v, out[-2:]))
    v, out = jw(e3)
    check(v == ("bitrate/250k/idle", FAULT) and out
          and "REFUSED, not judged" in out[-1]
          and "do not cover" in out[-1],
          "can-fail: step 2's window judgement: an uncovered window is "
          "REFUSED, a FAULT on its arm -> %s %s" % (v, out[-1:]))
    v, out = jw(b4, window_incomplete=2, window_fail_top="read too late")
    check(v == ("bitrate/250k/idle", FAULT) and out
          and out[-1].startswith("  250k, idle: TEST FAULT: 250k idle: the "
                                 "witness's receive failed 2 time(s)"),
          "can-fail: step 2's window judgement: a witness receive failure "
          "is a TEST FAULT on its arm, not scored -> %s %s" % (v, out[-1:]))
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

    class _NoLink(object):
        """No canctl from a self-test (2026-11-13: NoNode() sets the link)."""
        @staticmethod
        def set_link(up):
            return "kvaser link down (stand-in)"
    check(node_fault(_N(), "x") is not None and node_fault(NoNode(_NoLink), "x")
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
    # (2026-11-20: bus-off is the second kind; error passive is a state,
    # counted apart and no failure)
    q_, e_ = PCAN_QOVERRUN, PCAN_BUSOFF
    w = Witness(spec=(7, ((2, q_), (3, e_), (4, q_), (5, q_),
                          (6, PCAN_BUSPASSIVE))))
    time.sleep(0.6)
    w.take()
    kinds, inc = w.take_failures(), w.take_incomplete()
    sk = w.take_states()
    w.close()
    qo = kinds.get("PCAN read status 0x40: The receive queue was read too "
                   "late")
    bp = kinds.get("PCAN read status 0x10: Bus error: the CAN controller "
                   "is bus-off")
    check(inc == 4 and qo is not None and bp is not None and qo[0] == 3
          and bp[0] == 1 and len(kinds) == 2,
          "can-fail: receive failures are counted by their text (3 queue, "
          "1 bus-off; got %s)" % sorted((k, v[0]) for k, v in
                                        kinds.items()))
    check(sorted((k, v[0]) for k, v in sk.items())
          == [("PCAN bus state passive: read status 0x40000", 1)],
          "can-fail: error passive returned by Read is a bus state, counted "
          "by its text apart from the failures -> %s"
          % sorted((k, v[0]) for k, v in sk.items()))
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
    # Linux's read from /proc, on fixed text (both systems run it)
    lt, ll, lw = linux_host_read(
        "cpu  100 20 30 400 50 6 4 2 0 0\ncpu0 1 2 3 4 5 6 7 8 0 0\n",
        "MemTotal:       16000000 kB\nMemFree:         1000000 kB\n"
        "MemAvailable:    4000000 kB\n",
        "Name:\tpython3\nVmSize:\t 900000 kB\nVmRSS:\t  204800 kB\n")
    check(lt == (450, 490, 122) and ll == 75 and lw == 200.0
          and ws_mb(3 * 1048576) == 3.0,
          "can-fail: the Linux host read: idle + iowait, kernel with idle, "
          "user + nice + steal, memory load from MemAvailable (75 %%), the "
          "working set from VmRSS in MB (200) -> %s, %s, %s" % (lt, ll, lw))
    check(SNAPSHOT_CMD["posix"][0] == "ps" and "cgroup" in SNAPSHOT_CMD[
        "posix"][2] and SNAPSHOT_CMD["nt"][0] == "tasklist",
          "the process snapshot: ps with the cgroup on Linux, tasklist on "
          "Windows")
    times, load, ws = HostSampler._read()
    check(min(times) > 0 and 1 <= load <= 100 and 1.0 < ws < 65536.0,
          "can-fail: the host read on this system is plausible (CPU times "
          "> 0, memory load %d %%, this process's working set %.1f MB)"
          % (load, ws))

    # start refusals end VOID, never FAIL (reviewer, 2026-10-23)
    import re as _re
    _ok, sil = score_silence("@SENT id=7F0", "@STATUS tec=0 rec=0")
    for why, lines in (("the board's ELF sha256 a is not --elf's b", ()),
                       ("the image reports pins other than TX 7 / RX 6", ()),
                       ("the witness is not shown silent", sil)):
        out = start_refused(why, lines)
        judged = [ln for ln in out if _re.match(r"^\s*(FAIL|VOID)\b", ln)]
        check(bool(sil) and not _ok and judged == [out[-1]]
              and out[-1].startswith("VOID: REFUSED") and why in out[-1]
              and len(out) == len(lines) + 1,
              "can-fail: a start refusal (%s) ends on one VOID line and "
              "no line reads FAIL -> %s" % (why[:30], judged))

    # (F2) the reader is its own process at ABOVE_NORMAL, its windows
    # complete and in order with nothing leaking across a boundary
    w = Witness(spec=(600, ((150, PCAN_QOVERRUN), (450, PCAN_QOVERRUN)),
                      None, 2000.0))
    want_prio = 0x8000 if os.name == "nt" else BENCH_SLICE
    # Linux: what the reader says is checked against its cgroup, read here
    # from outside it, so a claim with no placement behind it fails
    seen = want_prio if os.name == "nt" else slice_of(w.pid)
    check(w.pid != os.getpid() and w.prio == want_prio and seen == want_prio,
          "can-fail: the witness reader is its own process (pid %s, this "
          "one %d), its priority %s (Windows: ABOVE_NORMAL; Linux: in %s, "
          "its cgroup read from outside -> %s)"
          % (w.pid, os.getpid(), prio_text(w.prio), BENCH_SLICE, seen))
    check(prio_text(0x8000).startswith("0x8000")
          and prio_text(BENCH_SLICE).startswith("in bench.slice")
          and prio_text("app.slice") == "NOT IN bench.slice: app.slice"
          and prio_text(None) == "unknown",
          "the priority line: a class in hex, Linux's slice as read back")
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
             (PCAN_BUSHEAVY, "state"), (PCAN_BUSLIGHT, "state"),
             (PCAN_QRCVEMPTY | PCAN_BUSHEAVY, "state"),
             (PCAN_ILLDATA, "ignored"),
             # 2026-11-20: error passive is a state (it was a failure); an
             # overrun with it, bus-off, or a bit outside the state and
             # overrun bits is still a failure
             (PCAN_BUSPASSIVE, "state"),
             (PCAN_BUSPASSIVE | PCAN_QRCVEMPTY, "state"),
             (PCAN_BUSPASSIVE | PCAN_BUSHEAVY, "state"),
             (PCAN_BUSPASSIVE | PCAN_QOVERRUN, "failure"),
             (PCAN_BUSPASSIVE | PCAN_ILLDATA, "failure"),
             (PCAN_BUSOFF, "failure"), (PCAN_BUSOFF | PCAN_QRCVEMPTY, "failure"),
             (PCAN_BUSOFF | PCAN_BUSPASSIVE, "failure"),
             (0x1, "failure"), (0x80, "failure")]
    got = [(hex(c), classify_read(c)) for c, _k in table]
    check(got == [(hex(c), k) for c, k in table],
          "can-fail: the reader's classification, an overrun with a bus-heavy "
          "bit a failure, error passive a state, bus-off a failure -> %s"
          % got)
    names = [(hex(w), pcan_state(w)) for w in (
        PCAN_BUSPASSIVE, PCAN_BUSHEAVY, PCAN_BUSLIGHT, 0, PCAN_BUSOFF,
        PCAN_QOVERRUN, PCAN_OVERRUN | PCAN_BUSPASSIVE)]
    check(names == [("0x40000", ("state", "passive")),
                    ("0x8", ("state", "warning")),
                    ("0x4", ("state", "light")),
                    ("0x0", ("state", "active")),
                    ("0x10", ("failure", "bus-off")),
                    ("0x40", ("failure", "frames lost")),
                    ("0x40002", ("failure", "frames lost (passive)"))],
          "can-fail: a status word's state, by can-tools' decoding: passive, "
          "warning, light, active; bus-off and frames lost failures -> %s"
          % names)
    class _CodeRead(object):
        def __init__(self, code):
            self.code = code

        def Read(self, _h):
            return self.code, None, None
    waits = []
    for code in (PCAN_BUSPASSIVE | PCAN_QRCVEMPTY, PCAN_BUSPASSIVE,
                 PCAN_QRCVEMPTY):
        calls = []
        rd = _PcanReader(_CodeRead(code), 0, calls.append, lambda c: "",
                         0.0, None, None)
        rd.read(0.02)
        waits.append(len(calls))
    check(waits == [1, 0, 1],
          "can-fail: a state returned with an empty queue waits for the "
          "receive event, one without does not (the next entry is read at "
          "once) -> %s" % waits)
    stl = state_lines({"b": [2, 10.5, 12.0], "a": [5, 10.0, 11.0]}, 9.0)
    check(stl == ["the PCAN's bus state x5: a (first +1.000 s, last +2.000 "
                  "s)", "the PCAN's bus state x2: b (first +1.500 s, last "
                  "+3.000 s)"],
          "can-fail: state lines, most frequent first, times from the "
          "window's start -> %s" % stl)
    # the PCAN's own status string (2026-11-20, E3): every word gives a
    # text, a combination included, with each bit by name
    stt = [(hex(w), pcan_status_text(w)) for w in (
        0, PCAN_BUSPASSIVE | PCAN_BUSHEAVY, PCAN_BUSOFF,
        PCAN_BUSPASSIVE | PCAN_QRCVEMPTY, PCAN_OVERRUN | PCAN_QOVERRUN,
        0x1, 0x100 | PCAN_BUSLIGHT)]
    check(stt == [("0x0", "OK"),
                  ("0x40008", "0x40008 BUSHEAVY+BUSPASSIVE (passive)"),
                  ("0x10", "0x10 BUSOFF (bus-off)"),
                  ("0x40020", "0x40020 QRCVEMPTY+BUSPASSIVE (passive)"),
                  ("0x42", "0x42 OVERRUN+QOVERRUN (frames lost)"),
                  ("0x1", "0x1 XMTFULL"),
                  ("0x104", "0x104 BUSLIGHT+other 0x100")],
          "can-fail: the PCAN's status string, every bit by name and "
          "can-tools' reading of its state bits -> %s" % stt)

    class _StatusBus(object):
        """python-can's PcanBus as far as the status goes: status() is
        GetStatus; status_string() looks the word up whole, as python-can
        4.6.1 does (PCAN_DICT_STATUS has single values only)."""
        def __init__(self, word):
            self.word = word

        def status(self):
            return self.word

        def status_string(self):
            return {0: "OK", PCAN_BUSPASSIVE: "BUSPASSIVE",
                    PCAN_BUSHEAVY: "BUSWARNING"}.get(self.word)
    srd = [pcan_status_reader(_StatusBus(w))() for w in (
        PCAN_BUSPASSIVE | PCAN_BUSHEAVY, 0)]
    check(srd == ["0x40008 BUSHEAVY+BUSPASSIVE (passive)", "OK"],
          "can-fail: the witness reader's status for a combined word is "
          "its text, not python-can's None -> %s" % srd)
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

        same, passive = [], []
        for c, _k in table:
            if c & (PCAN_OVERRUN | PCAN_QOVERRUN):
                continue
            mine = classify_read(c)
            row = (hex(c), mine, pcan_says(_OneRead(c))[0])
            # python-can raises on error passive unless a light / heavy
            # bit is set; the reader counts a state (2026-11-20)
            (passive if mine == "state" and c & PCAN_BUSPASSIVE and not c & (
                PCAN_BUSLIGHT | PCAN_BUSHEAVY) else same).append(row)
        check(all(t == {"empty": "nothing", "ignored": "nothing",
                        "state": "warning"}.get(m, m) for _c, m, t in same),
              "can-fail: without an overrun bit or error passive alone the "
              "classification is python-can's own (its warning the "
              "reader's state) -> %s" % same)
        check(len(passive) == 2 and all(t == "failure"
                                        for _c, _m, t in passive),
              "python-can 4.6.1 raises on error passive returned by Read, "
              "with or without QRCVEMPTY; the reader counts a state -> %s"
              % passive)
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

        # 2026-11-20: the reader's states agree with can-tools' own PCAN
        # class (CanbenchPcanBus, bus_states="report") on the same Reads:
        # its frame lost or bus-off is the reader's failure, its state the
        # reader's state of that name, its frame the reader's row
        import collections as _co
        import can_tools_pin            # the pinned canbench, by file
        can_tools_pin.use()
        from canbench.live import pcan_bus as _pbus
        E_ = adapter_roles.errors()

        def tools_say(fake):
            bus = object.__new__(_pbus.CanbenchPcanBus)
            bus.m_objPCANBasic, bus.m_PcanHandle = fake, 0x51
            bus._can_protocol = _can.CanProtocol.CAN_20
            bus.channel_info, bus._recv_event = "fake", None
            bus._pending = _co.deque()
            bus._last_type, bus._last_raw = 0, (0, b"")
            bus._synth_status, bus._last_ts = None, 0.0
            bus.busoff_reset, bus.busoff_reset_interval = False, 0.0
            bus.busoff_resets, bus._last_reset = 0, float("-inf")
            bus._filters = None
            bus._wrap_read("Read")
            saved = pc.HAS_EVENTS
            pc.HAS_EVENTS = False
            try:
                m, _f = bus._recv_internal(0)
            finally:
                pc.HAS_EVENTS = saved
            if m is None:
                return "nothing", None
            if not m.is_error_frame:
                return "row", None
            st = E_.decode(m).state
            if E_.lost_frames(m) or st == "busoff":
                return "failure", None
            return "state", st or "light"

        agree = []
        for code, ident, mtype, data in (
                (PCAN_BUSPASSIVE, 0, 0, b""),
                (PCAN_BUSPASSIVE | PCAN_QRCVEMPTY, 0, 0, b""),
                (PCAN_BUSHEAVY, 0, 0, b""), (PCAN_BUSLIGHT, 0, 0, b""),
                (PCAN_QOVERRUN | PCAN_BUSHEAVY, 0, 0, b""),
                (PCAN_BUSOFF, 0, 0, b""),
                (PCAN_OK, 2, PCAN_MSG_STATUS, b"\x00\x00\x00\x00"),
                (PCAN_OK, 3, PCAN_MSG_STATUS, b"\x00\x04\x00\x00"),
                (PCAN_OK, 0, PCAN_MSG_STATUS, b"\x00\x00\x00\x08"),
                (PCAN_OK, 0, PCAN_MSG_STATUS, b"\x00\x00\x00\x02"),
                (PCAN_OK, 4, PCAN_MSG_STATUS, b"\x00\x00\x00\x10"),
                (PCAN_OK, 0x100, 0, b"\x01\x02")):
            theirs = tools_say(_OneRead(code, ident, mtype, data))
            rd = _PcanReader(_OneRead(code, ident, mtype, data), 0,
                             lambda _t: None, lambda c: "text %X" % c,
                             0.0, None, None, "fast")
            kind, v = rd.read(0)
            mt = re.search(r"bus state (\S+):", v) if kind == "state" else None
            agree.append((hex(code), mtype, theirs,
                          (kind, mt.group(1) if mt else None)))
        check(all(t == m for _c, _t, t, m in agree)
              and [m[0] for _c, _t, _x, m in agree] == [
                  "state", "state", "state", "state", "failure", "failure",
                  "state", "state", "state", "failure", "failure", "row"],
              "can-fail: Read states and status messages classified as "
              "can-tools' CanbenchPcanBus decodes them -> %s" % agree)

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
    # 2026-11-20, Linux: error passive returned by Read, and status
    # messages (Read OK, MSGTYPE STATUS), through the reader process: state
    # rows counted by text, never a data row or a receive failure; a status
    # message saying frames were lost is a failure
    w = Witness(spec=(9, ((2, PCAN_BUSPASSIVE), (3, PCAN_BUSHEAVY),
                          (4, PCAN_BUSPASSIVE),
                          (5, (2, PCAN_MSG_STATUS, b"\x00\x00\x00\x00")),
                          (6, (3, PCAN_MSG_STATUS, b"\x00\x04\x00\x00")),
                          (8, (0, PCAN_MSG_STATUS, b"\x00\x00\x00\x02")))))
    time.sleep(0.6)
    rows_, inc = w.take(), w.take_incomplete()
    sk, fk = w.take_states(), w.take_failures()
    pw = w.take_warnings()[0]
    w.close()
    want = [("PCAN bus state active: status message 0x00000 (id 2)", 1),
            ("PCAN bus state passive: read status 0x40000", 2),
            ("PCAN bus state passive: status message 0x40000 (id 3)", 1),
            ("PCAN bus state warning: read status 0x8", 1)]
    check(sorted((k, v[0]) for k, v in sk.items()) == want and pw == 5
          and [node_seq(r) for r in rows_] == [1, 7, 9]
          and inc == 1 and list(fk) == [
              "PCAN receive failure, frames lost: status message 0x00002 "
              "(id 0)"],
          "can-fail: Read states and status messages are state rows by "
          "their text (%s, %d), not data rows (seqs %s), and only the "
          "status message of an overrun is a failure (%d: %s)"
          % (sorted((k, v[0]) for k, v in sk.items()), pw,
             [node_seq(r) for r in rows_], inc, list(fk)))
    # a PCAN returning only states (each one a queue entry, read at once)
    # still answers the cut, its rows and states counted
    w = Witness(spec=(5, (), None, 0.0, False, 0.0, 0.0, 0.0, None,
                      PCAN_BUSPASSIVE))
    time.sleep(0.6)
    t0_ = time.perf_counter()
    rows_, inc = w.take(), w.take_incomplete()
    dt_ = time.perf_counter() - t0_
    sk = w.take_states()
    dead = w.dead
    w.close()
    ns = sum(v[0] for v in sk.values())
    check(len(rows_) == 5 and inc == 0 and dead is None and dt_ < 2.0
          and ns > WITNESS_POLL_ROWS,
          "can-fail: a reader given only states after its rows answers the "
          "cut (%d rows, %d states, %.2f s, %s, %d failure(s))"
          % (len(rows_), ns, dt_, dead, inc))

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
    check(ok is STOP_OUTCOME and v.startswith("VOID (declared STOP): THE "
                                              "WRITE TAKES") and
          "SOMETHING ELSE" in v and "STOP" in v and
          _l[-1].startswith("VOID: recstart's declared STOP outcome, not "
                            "judged: THE WRITE TAKES") and
          "SOMETHING ELSE" in _l[-1],
          "CAN-FAIL recstart: 128, then 0 at once -> something else, stop: "
          "VOID with its text (sweep ruling 3) -> %s / %s" % (v, _l[-1]))
    ok, v, _l = judge_recstart(can_a, _starts(0), _node([0] * 6), can_b, [])
    check(ok and "NEVER TAKES" in v, "recstart: 0 at every start -> the "
          "write never takes")
    st = _starts(128)
    st[1][5] = ("r5", _st(0))
    ok, v, _l = judge_recstart(can_a, st, _node([128] * 6), can_b, [])
    check(ok is STOP_OUTCOME and v == "VOID (declared STOP): NEITHER: rec_reg "
          "after the starts %s -- STOP" % [[128] * 6, [128] * 5 + [0], [128] * 6]
          and _l[-1] == "VOID: recstart's declared STOP outcome, not judged: "
          + v[len("VOID (declared STOP): "):],
          "CAN-FAIL recstart: 128 then 0 within the quiet 2 s -> neither, "
          "stop: VOID with its text (sweep ruling 3) -> %s" % v)
    nj = "VOID: recstart's premise not met: NOT JUDGED ("
    ok, v, _l = judge_recstart(_st(0, "normal", rec=0, tec=128, tec_reg=0),
                               _starts(0), _node([0] * 6), can_b, [])
    check(ok is FAULT and "wiring check A" in v and _l[-1].startswith(nj),
          "can-fail: tec_reg != tec in normal mode -> not judged: VOID "
          "(premise, 2026-11-23)")
    ok, v, _l = judge_recstart(can_a, _starts(0), _node([0] * 6),
                               _st(0, "normal", rec=130), [])
    check(ok is FAULT and "wiring check B" in v and _l[-1].startswith(nj),
          "can-fail: rec_reg 0 while the API "
          "reads 130 in normal mode -> not judged (a register read that "
          "always says 0 is caught)")
    ok, v, _l = judge_recstart(can_a, _starts(0), _node([0] * 6),
                               _st(0, "normal", rec=0), [])
    check(ok is FAULT and "wiring check B" in v and _l[-1].startswith(nj),
          "can-fail: no REC at all in the normal-mode window -> not judged")
    ok, v, _l = judge_recstart(can_a, _starts(0), _node([0] * 6), can_b,
                               [(1.0, 0x123, False, b"")])
    check(ok is FAULT and "not quiet" in v and _l[-1].startswith(nj),
          "can-fail: a frame on CAN1 during the starts -> not judged")
    st = _starts(0)
    st[0][0] = ("r0", "@STATUS installed=1 rec=0 mode=listen")
    ok, v, _l = judge_recstart(can_a, st, _node([0] * 6), can_b, [])
    check(ok is FAULT and "NOT JUDGED" in v and _l[-1].startswith(nj),
          "can-fail: a STATUS without rec_reg (the old image) -> not judged")
    st = _starts(0)
    st[2][0] = ("r0", None)
    ok, v, _l = judge_recstart(can_a, st, _node([0] * 6), can_b, [])
    check(ok is FAULT and "NOT JUDGED" in v and _l[-1].startswith(nj),
          "can-fail: a failed MODE (STATUS dropped) -> not judged")
    ok, v, _l = judge_recstart(can_a, _starts(128), [("n0", None)], can_b, [])
    check(ok is FAULT and "node read n0" in v and _l[-1].startswith(nj),
          "can-fail: a node read without rec_reg -> not judged: VOID")
    rc, final = combine([("recstart (judged)", FAULT)])
    check(rc == 1 and final.startswith("VOID: TEST FAULT on recstart"),
          "CAN-FAIL recstart's NOT JUDGED is VOID, not FAIL (premise, "
          "2026-11-23) -> %s" % final)
    # sweep ruling 3: a declared STOP is VOID, named apart from a fault
    rc, final = combine([("recstart (judged)", STOP_OUTCOME)])
    check(rc == 1 and final == "VOID: declared STOP on recstart (judged), and "
          "no arm FAILs on a valid measurement",
          "CAN-FAIL combine: a declared STOP alone is VOID, rc 1 -> %s" % final)
    rc, final = combine([("a", STOP_OUTCOME), ("b", FAULT), ("c", True)])
    check(rc == 1 and final == "VOID: TEST FAULT on b; declared STOP on a, "
          "and no arm FAILs on a valid measurement",
          "combine: a fault and a STOP both named -> %s" % final)
    rc, final = combine([("a", STOP_OUTCOME), ("d", False)])
    check(rc == 1 and final == "FAIL: d; declared STOP, not judged: a",
          "CAN-FAIL combine: a valid FAIL beside a STOP stays FAIL -> %s" % final)
    rc, final = combine([("a", STOP_OUTCOME), ("b", FAULT), ("d", False)])
    check(rc == 1 and final == "FAIL: d; TEST FAULT, not measured: b; "
          "declared STOP, not judged: a",
          "CAN-FAIL combine: a FAIL names both the fault and the STOP beside "
          "it -> %s" % final)
    check(verdict_word(STOP_OUTCOME) == "VOID (declared STOP)",
          "the STOP's verdict word")
    # sweep ruling 4: the no-ACKer variant's reason in the verdict lines
    vl = verdict_lines([("stall/acked", True)],
                       [("stall/no-acker", NO_ACKER_WHY)])
    check(vl == ["  %-28s PASS" % "stall/acked",
                 "  %-28s NOT RUN: %s" % ("stall/no-acker", NO_ACKER_WHY),
                 "RESULT: 1 arm(s) run; NOT RUN: stall/no-acker (%s)"
                 % NO_ACKER_WHY],
          "CAN-FAIL verdicts: the no-ACKer variant not run is named with "
          "why, in the verdicts and the RESULT line -> %s" % vl)
    check(verdict_lines([("bitrate/1M/idle", FAULT)]) ==
          ["  %-28s VOID (TEST FAULT)" % "bitrate/1M/idle"],
          "verdicts: nothing not run -> no RESULT line (vport-windows' "
          "reading of a report unchanged)")
    stall_src = step_blocks(inspect.getsource(run_bench))["stall"]
    check("not_run.append((\"stall/no-acker\", NO_ACKER_WHY))" in stall_src
          and "verdict_lines(verdicts, not_run)" in inspect.getsource(run_bench),
          "CAN-FAIL stall: the no-ACKer variant not run is kept for the "
          "verdicts, and the verdicts printed with it")

    check(all(reply_tag(c) == t for c, t in (("STOP", "@STOPPED"),
                                              ("FLOOD 7F4", "@FLOODING"),
                                              ("SEND 7EF 1", "@SENT"),
                                              ("HELLO?", "@HELLO"))),
          "each command's reply tag")

    # 2026-10-21: --listen-arms (the reviewer's KNOWN-FAIL split)
    # 2026-11-27: the wrong-rate arms gone (the reviewer, on the user's
    # ruling: spec 9 leaves the wrong bit rate out of the listen-only cases)
    subs = listen_subs(listen_arm_keys("all"))
    names = [n for n, _ in subs]
    check(names == ["idle", "flood 7F4", "flood 649", "stall %d" % STALL_N],
          "arms: all gives the four windows, in order -> %s" % names)
    check(listen_arm_keys("all") == ["no-ack", "idle", "flood-7F4",
                                     "flood-649", "stall"],
          "arms: all is the no-ack arm and the four windows")
    check(all(c is None or c[0] == "BITRATE 500k" for _, c in subs),
          "arms: every listen window with commands sets 500k first -> %s"
          % [c for _, c in subs])
    check(listen_arm_keys("stall,idle") == ["idle", "stall"],
          "arms: run in the fixed order whatever the order given")
    # a subset runs only its own windows (the wrong-rate selection checked
    # this until 2026-11-27)
    part = {s: [n for n, _ in listen_subs(listen_arm_keys(s))]
            for s in ("idle,stall", "flood-649", "no-ack")}
    check(part == {"idle,stall": ["idle", "stall %d" % STALL_N],
                   "flood-649": ["flood 649"], "no-ack": []},
          "arms: a subset runs only its own windows -> %s" % part)
    for spec in ("flood-7f4", "", "idle,bogus", "wrong-250k", "wrong-1m",
                 "idle,wrong-1m"):
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
    ed = scratch_dir.make("vport-selftest")
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
        scratch_dir.remove(ed)
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
    # m1's local arm (reviewer, 2026-10-31): a vport-m1-local run folder,
    # removed when the step ends, pass or fail, its edges files moved to
    # the run folder first; never the system temp folder
    g = globals()
    keep_raw, keep_trials = g["RAW_DIR"], g["_m1_trials"]
    raw = scratch_dir.make("vport-selftest")
    seen = []
    rows_name = "08_m1_1_settle-local_250k_flood.txt"

    def _trials(args, board, wit, node, verdicts, arms, local):
        seen.append(local)
        for n in ("07_edges.txt", rows_name):
            with open(os.path.join(local, n), "w", encoding="utf-8") as f:
                f.write("x\n")
        raise RuntimeError("a trial failed")

    def _norm(p):
        return os.path.normcase(os.path.realpath(p))

    try:
        g["RAW_DIR"], g["_m1_trials"] = raw, _trials
        try:
            run_m1(argparse.Namespace(m1_arms="settle-local"), None, None,
                   None, [])
            raised = "no"
        except RuntimeError as exc:
            raised = str(exc)
        loc = seen[0] if seen else ""
        td = scratch_dir.tool_dir("vport-m1-local")
        check(raised == "a trial failed" and bool(loc)
              and _norm(os.path.dirname(loc)) == _norm(td),
              "m1: the local arm's folder is a vport-m1-local run folder, "
              "the trial's error passed on -> %s (%s)" % (loc, raised))
        check(bool(loc) and not os.path.exists(loc),
              "m1: the local folder removed when a trial raises -> %s" % loc)
        check(os.path.isfile(os.path.join(raw, "07_edges.txt"))
              and not os.path.isfile(os.path.join(raw, rows_name)),
              "m1: the local arm's edges file moved to the run folder, "
              "its rows file not")
        nd = os.path.join(raw, "no-raw")
        os.makedirs(nd)
        open(os.path.join(nd, "09_edges.txt"), "w").close()
        check(m1_keep_edges(nd, None) == []
              and os.path.isfile(os.path.join(nd, "09_edges.txt")),
              "m1: no run folder, the edges file left where it is")
    finally:
        g["RAW_DIR"], g["_m1_trials"] = keep_raw, keep_trials
        for x in [raw] + seen:
            if os.path.isdir(x):
                scratch_dir.remove(x)
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
              "2 PCAN bus state(s); 1M idle, diagnostic: y"),
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

    # 2026-11-27: every listen window expects the node's frames to complete
    # (no wrong-rate window suspends the stall rule any more)
    class _RB(_SB):
        def __init__(self):
            self.cmds = []

        def cmd(self, c, *a):
            self.cmds.append(c)
            return "@OK"

    class _RN(_SN):
        def __init__(self, log):
            _SN.__init__(self, log)
            self.expects = []

        def expect_acks(self, on, **kw):
            self.expects.append((on, kw))
    seen = []
    for name, cmds in listen_subs(listen_arm_keys("all")):
        rb, rn = _RB(), _RN(slog)
        try:
            listen_window(rb, _SW([(s_pre, None), (s_cut, good),
                                   (s_drain, good)]), rn, 0.0, name, cmds)
            seen.append((name, rn.expects, rb.cmds[:1]))
        except Exception as exc:                           # noqa: BLE001
            seen.append((name, fail_text(exc), []))
    check(seen and all(e == [(True, {})] for _, e, _c in seen),
          "each listen window expects the node's frames to complete, and "
          "only that -> %s" % [(n, e) for n, e, _c in seen])
    check(all(c == ["BITRATE 500k"] for n, _e, c in seen if n != "idle"),
          "each listen window with commands sends BITRATE 500k first -> %s"
          % [(n, c) for n, _e, c in seen])
    # the stamp map (reviewer, 2026-11-11): stamps on an epoch clock 470
    # ppm fast, as the PCAN's are on Linux but off rate, the windows cut on
    # this process's clock. S(h): the stamp of host time h.
    import random
    rnd = random.Random(7)
    EP, PPM = 1794450000.0, 470e-6

    def S(h):
        return EP + h * (1.0 + PPM)

    def true_off(s):
        return (s - EP) / (1.0 + PPM) - s

    pts = []
    for h in range(50, 151):
        lat = 0.0003 + rnd.uniform(0.0, 0.003)
        pts.append((S(h), h + lat - S(h)))
    pts[40] = (pts[40][0], pts[40][1] + 0.2)      # a reader late once
    sm = StampMap()
    sm.add(reader_stats(len(pts), 0, 0, 0, 1, 0, 0, 0,
                        [(s, o, s, o) for s, o in pts]))
    fit = sm.frozen()
    err = [fit.off(s) - true_off(s) for s, _o in pts]
    check(fit.n == 101 and 0.0 <= min(err) and max(err) < 0.001
          and abs(1e6 * fit.b + 470) < 10
          and abs(fit.to_stamp(fit.to_host(S(120.5))) - S(120.5)) < 1e-6,
          "stamp map: an epoch clock 470 ppm fast, 0.3-3.3 ms read latency "
          "and one read 200 ms late -> the map within %.2f..%.2f ms of the "
          "true offset, %+.1f ppm" % (1000 * min(err), 1000 * max(err),
                                       1e6 * fit.b))
    check(envelope([(5.0, 0.002)]) == (0.002, 0.0, 5.0)
          and StampMap().frozen().describe().startswith("no stamp map"),
          "stamp map: one point -> rate 0; no points -> stamps as they are")

    def stats_of(rows_read):
        """[(row, read host s)] -> reader_stats with its bins."""
        bins, cb, c = {}, None, None
        for r, rd in rows_read:
            cb, c = bin_row(bins, cb, c, r[0], rd - r[0])
        lags = [rd - r[0] for r, rd in rows_read]
        return reader_stats(len(lags), len(lags), 0.01, 0.0, 1.0,
                            min(lags), max(lags), lags[-1], _bins_out(bins))

    # the reader's per-bin update on fixed rows, no timing in it (the
    # reviewer's B3, 2026-11-12): (stamp, lag) in the order the reader
    # reads them. Bin 10: a later row with a lower lag takes the least, a
    # later one with a higher lag the most, a row in between changes
    # nothing; bin 11 opens with its own row; a row back in bin 10 with the
    # lowest lag yet takes its least, and one with the same lag does not
    fixed = [(10.1, 0.005), (10.4, 0.002), (10.6, 0.009), (10.8, 0.003),
             (11.2, 0.004), (10.9, 0.001), (10.95, 0.001)]
    fb, fcb, fc = {}, None, None
    for t, lag in fixed:
        fcb, fc = bin_row(fb, fcb, fc, t, lag)
    want = {10: [10.9, 0.001, 10.6, 0.009], 11: [11.2, 0.004, 11.2, 0.004]}
    check(fb == want,
          "stamp map bins on fixed rows: a later row with a lower lag takes "
          "the bin's least, a higher one its most, a row back in an earlier "
          "bin updates that bin, an equal lag changes nothing -> %s" % fb)
    # (the equal-lag row left out: read time less stamp is not exact in
    # floating point, so a tie there is not a tie)
    fr = [(_bframe(t, 0x7F4, k), t + lag) for k, (t, lag) in enumerate(fixed[:-1])]
    got = sorted(stats_of(fr)["bins"])
    check([tuple(round(x, 9) for x in v) for v in got]
          == [tuple(want[10]), tuple(want[11])],
          "the self-test's scripted bins are the reader's (bin_row) -> %s" % got)

    class _MW(object):
        """A scripted witness with a stamp map: (rows read, read lag) per
        take, as the reader would bin them."""

        def __init__(self, takes):
            self.takes, self.stats = list(takes), None
            self.stamp_map = StampMap()

        def take(self):
            rr = self.takes.pop(0) if self.takes else []
            self.stats = stats_of(rr) if rr else None
            self.stamp_map.add(self.stats)
            return [r for r, _rd in rr if r is not None]

        def take_incomplete(self):
            return 0

        def take_failures(self):
            return {}

        def take_warnings(self):
            return 0, ""

    def epoch_takes(step=0.0):
        """Rows read before the window (host 50..150 s, one 200 ms late),
        then the window's node frames 848..1848 (sent at host 200 s on,
        slog), stamped 0.5 ms after the send, read 1 ms after; step:
        the window's stamps that much later."""
        pre = [(_bframe(S(h), 0x7F4, k), h + 0.0004 + (0.2 if h == 90
                                                        else 0.0))
               for k, h in enumerate(range(50, 151))]
        win = [(_pframe(S(200.0 + 0.01 * (q - 848) + 0.0005) + step, q),
                200.0 + 0.01 * (q - 848) + 0.0015) for q in range(848, 1849)]
        return [pre, win[:-2], win[-2:]]

    def epoch_window(takes):
        try:
            mw = _MW(takes)
            ok, ln = listen_window(_SB(), mw, _SN(slog), 0.0, "idle", None)
        except Exception as exc:                           # noqa: BLE001
            mw, ok, ln = None, "an exception", [fail_text(exc)]
        return mw, ok, ln

    mw, ok, ln = epoch_window(epoch_takes())
    check(ok is True and mw.window_stamp[:3] == (0, 999, 2)
          and not mw.window_map.own and mw.window_map.n == 101,
          "stamp map through window(): stamps on an epoch clock 470 ppm "
          "fast, mapped by the 101 points read before the window -> %s, %s"
          % (verdict_word(ok) if ok in (True, False, FAULT) else ok,
             ln[0] if ln else ""))
    tk = epoch_takes()
    old_rows, _i = assign_by_stamp(
        [], [r for r, _rd in tk[1]], [r for r, _rd in tk[2]],
        *stamp_bounds(slog, 0.0, 1.0))
    old_rf = reader_fault(merge_stats(stats_of(tk[1]), stats_of(tk[2])))
    check(not old_rows and "BEFORE its stamp" in old_rf,
          "can-fail: the same window on the map before 2026-11-11 (offset "
          "0, rate 1): %d rows kept, %s" % (len(old_rows), old_rf[:60]))
    mw, ok, ln = epoch_window(epoch_takes(step=0.05))
    check(ok is FAULT and "BEFORE its stamp" in (mw.window_stamp_fault
                                                 if mw else ""),
          "can-fail: stamp map through window(): the window's stamps 50 ms "
          "ahead of the map fitted before it -> %s, %s" % (
              verdict_word(ok) if ok in (True, False, FAULT) else ok,
              ln[0] if ln else ""))
    tk = epoch_takes()
    mw, ok, ln = epoch_window([[]] + tk[1:])
    check(ok is True and mw.window_map.own and mw.window_map.n >= 10,
          "stamp map through window(): nothing read before the run's first "
          "window -> mapped by its own rows (%s) -> %s" % (
              mw.window_map.describe() if mw else "-",
              verdict_word(ok) if ok in (True, False, FAULT) else ok))

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
          "window on no map (offset 0, rate 1) -> %s" % rf)
    # the reader process's bins reach the Witness's stamp map: stamps on an
    # epoch clock 470 ppm fast; then the same with a 50 ms step ahead
    for step, want in ((None, False), ((500, 0.05), True)):
        w = Witness(spec=(1200, (), None, 2000.0, True, 0.0, EP, PPM, step))
        time.sleep(0.15)
        w.take()
        fit = w.stamp_map.frozen()
        time.sleep(0.35)
        w.take()
        rf, rf_old = reader_fault(w.stats, fit), reader_fault(w.stats)
        nb = len((w.stats or {}).get("bins") or ())
        w.close()
        check(fit.n >= 1 and nb >= 1 and bool(rf) == want
              and "BEFORE its stamp" in (rf if want else rf_old),
              "%sa real witness, stamps on an epoch clock 470 ppm fast%s: "
              "%s on the map fitted on its first take (%s); %s on no map"
              % ("can-fail: " if want else "", ", 50 ms ahead after row 500"
                 if want else "", "faulted" if rf else "kept",
                 fit.describe(), "faulted" if rf_old else "kept"))
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
    # 2026-11-27: the wrong-rate arms gone; their mutant "wrong-rate windows
    # always run" went with them
    ("a flood listen window at a wrong bit rate",
     "            subs.append((\"flood %X\" % i, (\"BITRATE 500k\", \"FLOOD %X\" % i)))",
     "            subs.append((\"flood %X\" % i, (\"BITRATE 1M\", \"FLOOD %X\" % i)))"),
    ("a listen window suspends the stall rule",
     "    expect(True)\n    rows, sent = window(board, wit, node, secs, cmds[1] if cmds else None,",
     "    expect(False, wrong_rate=True)\n    rows, sent = window(board, wit, node, secs, cmds[1] if cmds else None,"),
    ("a listen window with commands skips its BITRATE",
     "    if cmds:\n        board.cmd(cmds[0])\n    expect(True)",
     "    expect(True)"),
    ("the wrong-rate arms accepted again",
     "LISTEN_ARMS = (\"no-ack\", \"idle\", \"flood-7F4\", \"flood-649\", \"stall\")",
     "LISTEN_ARMS = (\"no-ack\", \"idle\", \"wrong-250k\", \"wrong-1m\", \"flood-7F4\", \"flood-649\", \"stall\")"),
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
     "conn.send((\"cut\", k, n, kinds, fails, warns, wlast, st,\n",
     "conn.send((\"cut\", k, n, {}, 0, warns, wlast, st,\n"),
    ("F2: rows leak across a window boundary",
     "                                 \"wb\", 1 << 20)\n                    elif",
     "                                 \"wb\", 1 << 20)\n                        f.write(open(os.path.join(folder, \"w%06d.bin\" % (k - 1)), \"rb\").read())\n                    elif"),
    ("F2: the reader runs in-process",
     "        self.proc = ctx.Process(target=_witness_proc, args=(",
     "        self.proc = threading.Thread(target=_witness_proc, args=("),
    # the reader's own classification and decode (2026-10-25)
    ("reader: python-can's order (an overrun with BUSHEAVY a warning)",
     "    word = result & ~PCAN_QRCVEMPTY\n",
     "    if result & (PCAN_BUSLIGHT | PCAN_BUSHEAVY):\n        return \"state\"\n"
     "    word = result & ~PCAN_QRCVEMPTY\n"),
    ("reader: frames lost only a state",
     "        if E.lost_frames(em):",
     "        if False:"),
    ("reader: the controller's overrun not checked",
     "        if E.lost_frames(em):",
     "        if word & PCAN_QOVERRUN:"),
    ("reader: warnings not forwarded",
     "conn.send((\"cut\", k, n, kinds, fails, warns, wlast, st,\n",
     "conn.send((\"cut\", k, n, kinds, fails, 0, \"\", st,\n"),
    # the reader's bus states by can-tools' decoding (the charge-interposer
    # reviewer, 2026-11-20)
    ("reader: error passive a failure (the rule before 2026-11-20)",
     "        return pcan_state(word)[0]\n    return \"failure\"",
     "        return (\"failure\" if word & PCAN_BUSPASSIVE\n"
     "                else pcan_state(word)[0])\n    return \"failure\""),
    ("reader: bus-off a state",
     "        elif st == \"busoff\":",
     "        elif False:"),
    ("reader: a status message written as a data row",
     "            if msg.MSGTYPE & PCAN_MSG_STATUS:\n",
     "            if False:\n"),
    ("reader: a status message's word taken from its id",
     "                word = int.from_bytes(bytes(msg.DATA)[:4], \"big\")",
     "                word = msg.ID"),
    ("reader: a state never brings the next look at the pipe",
     "                warns, wlast = warns + 1, v\n                since += 1\n",
     "                warns, wlast = warns + 1, v\n"),
    ("reader: a state returned with an empty queue not waited",
     "            if r & PCAN_QRCVEMPTY:\n                self.wait(timeout)",
     "            if False:\n                self.wait(timeout)"),
    ("reader: states not forwarded by text",
     "                        conn.send((\"cut\", k, n, kinds, fails, warns, wlast, st,\n"
     "                                   states))",
     "                        conn.send((\"cut\", k, n, kinds, fails, warns, wlast, st,\n"
     "                                   {}))"),
    ("the bench process drops the reader's states",
     "                                      (r[8] if len(r) > 8 else {},",
     "                                      ({},"),
    # step 2's windows judged only when the witness's stamps cover them
    # (the charge-interposer reviewer, 2026-11-21)
    ("coverage: the step-2 gate ignores it (E3's 1M flood judged)",
     "    if t_ok and not c_ok:\n        t_ok, why = c_ok, cwhy\n",
     "    if False:\n        t_ok, why = c_ok, cwhy\n"),
    ("coverage: the window's end unchecked",
     "    if head > edge_s or tail > edge_s or gap > gap_s:",
     "    if head > edge_s or gap > gap_s:"),
    ("coverage: the window's start unchecked",
     "    if head > edge_s or tail > edge_s or gap > gap_s:",
     "    if tail > edge_s or gap > gap_s:"),
    ("coverage: gaps between stamps unchecked",
     "    if head > edge_s or tail > edge_s or gap > gap_s:",
     "    if head > edge_s or tail > edge_s:"),
    ("coverage: unknown bounds taken as covered",
     "        return False, \"the window's bounds in witness-stamp time are unknown\"",
     "        return True, \"the window's bounds in witness-stamp time are unknown\""),
    ("coverage: no stamps taken as covered",
     "        return False, \"no witness stamps in the window\"",
     "        return True, \"no witness stamps in the window\""),
    ("coverage: over the error frames' stamps only",
     "    c_ok, cwhy = coverage_ok([x[0] for x in rows], *(bounds or (None, None)))",
     "    c_ok, cwhy = coverage_ok(bitrate_stamps(rows), *(bounds or (None, None)))"),
    ("coverage: the gap bound 1 s",
     "COVER_GAP_S = 0.25\n", "COVER_GAP_S = 1.0\n"),
    # step 2's window judgement out of run_bench (run 69d8, 2026-11-21)
    ("window judgement: the flag reads a name nothing binds (69d8's NameError)",
     "    flag = bitrate_flag(back_steps(bitrate_stamps(rows))[0],",
     "    flag = bitrate_flag(back_steps(tsw)[0],"),
    ("window judgement: a refused window scored anyway",
     "        out.append(\"  %s %s: %s\" % (r, name, ln))\n        return (arm, v), out\n",
     "        out.append(\"  %s %s: %s\" % (r, name, ln))\n"),
    ("window judgement: a witness receive failure scored anyway",
     "        out.append(\"  %s, %s: %s\" % (r, name, wf))\n        return (arm, FAULT), out\n",
     "        out.append(\"  %s, %s: %s\" % (r, name, wf))\n"),
    ("window judgement: the FLAG line dropped",
     "    if flag:\n        out.append(\"    \" + flag)\n",
     "    if False:\n        out.append(\"    \" + flag)\n"),
    ("window judgement: the PCAN's diagnostics dropped",
     "               \"'%s'\" % pst if pst else \"\"]):\n        if ln:",
     "               \"'%s'\" % pst if pst else \"\"]):\n        if False:"),
    ("window judgement: the node's refused sends not counted",
     "                              node_fails=getattr(node, \"window_fails\", 0))",
     "                              node_fails=0)"),
    # the PCAN's own status string (2026-11-20, the hand take E3)
    ("status: python-can's whole-word lookup (None for a combination)",
     "    return lambda: pcan_status_text(bus.status())",
     "    return bus.status_string"),
    ("status: a bit outside the named ones dropped",
     "    if rest:\n        names.append(\"other 0x%X\" % rest)",
     "    if False:\n        names.append(\"other 0x%X\" % rest)"),
    ("status: the state bits not read by can-tools",
     "        txt += \" (%s)\" % pcan_state(st)[1]",
     "        pass"),
    ("status: an empty queue hides the state",
     "    st = word & ~PCAN_QRCVEMPTY\n",
     "    st = word\n"),
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
     "    return nbytes / 1048576.0", "    return float(nbytes)"),
    ("diag: Linux idle without iowait",
     "    idle_all = idle + iowait", "    idle_all = idle"),
    ("diag: Linux memory load from MemFree",
     "    load = int(round(100.0 * (m[\"MemTotal\"] - m[\"MemAvailable\"])",
     "    load = int(round(100.0 * (m[\"MemTotal\"] - m[\"MemFree\"])"),
    ("diag: Linux working set from VmSize",
     "              if ln.startswith(\"VmRSS:\")][0]",
     "              if ln.startswith(\"VmSize:\")][0]"),
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
    ("m1: the local arm's folder outside vport-m1-local",
     "    local = scratch_dir.make(\"vport-m1-local\")",
     "    local = scratch_dir.make(\"vport-selftest\")"),
    ("m1: the local folder left when a trial raises",
     "    try:\n        _m1_trials(args, board, wit, node, verdicts, arms, "
     "local)\n    finally:\n        m1_keep_edges(local, RAW_DIR)\n"
     "        scratch_dir.remove(local)\n",
     "    _m1_trials(args, board, wit, node, verdicts, arms, local)\n"
     "    m1_keep_edges(local, RAW_DIR)\n    scratch_dir.remove(local)\n"),
    ("m1: the local arm's edges files not moved",
     "        if n.endswith(\"_edges.txt\"):",
     "        if False:"),
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
     "                              t_cmd if from_command else None)",
     "                              t_cmd)"),
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
     "    if lmax > READER_LAG_TOL_S:",
     "    if lmax > 1e9:"),
    ("reader: stamps ahead of the clock pass",
     "    if lmin < -STAMP_CLOCK_TOL_S:",
     "    if lmin < -1e9:"),
    # the stamp map (reviewer, 2026-11-11)
    ("map: no rate ever fitted",
     "FIT_RATE_SPAN_S = 30.0", "FIT_RATE_SPAN_S = 1e12"),
    ("map: the upper envelope (the latest reads)",
     "                - (hull[-1][1] - hull[-2][1]) * (p[0] - hull[-2][0])) <= 0:",
     "                - (hull[-1][1] - hull[-2][1]) * (p[0] - hull[-2][0])) >= 0:"),
    ("map: the line through the least point, not the edge over the mean",
     "        if x1 >= xm:\n            break",
     "        if True:\n            break"),
    ("map: not applied to the window's bounds",
     "    lo, hi = ((fit.to_stamp(h_lo), fit.to_stamp(h_hi)) if fit is not None\n              else (h_lo, h_hi))",
     "    lo, hi = h_lo, h_hi"),
    ("map: not applied to the lag figures",
     "    if fit is None or not bins:\n        return st[\"lag_min\"], st[\"lag_max\"]",
     "    if True:\n        return st[\"lag_min\"], st[\"lag_max\"]"),
    ("map: the window's own rows fitted into its map",
     "    if fit is not None and not fit.n:\n        fit = smap.frozen(own=True)",
     "    if fit is not None:\n        fit = smap.frozen(own=True)"),
    ("map: not fed by the witness's takes",
     "                self.stamp_map.add(self.stats)\n",
     "\n"),
    ("map: the reader keeps no bins",
     "                                          lag_max, lag, _bins_out(bins))",
     "                                          lag_max, lag, [])"),
    ("map: a bin's least lag never updated",
     "    if lag < cur[1]:\n        cur[0], cur[1] = t, lag",
     "    if False:\n        cur[0], cur[1] = t, lag"),
    ("map: a bin's most lag never updated (B3)",
     "    if lag > cur[3]:\n        cur[2], cur[3] = t, lag",
     "    if False:\n        cur[2], cur[3] = t, lag"),
    ("map: a row back in an earlier bin opens it again (B3)",
     "        cur_b, cur = b, bins.get(b)\n", "        cur_b, cur = b, None\n"),
    ("map: an equal lag replaces the bin's least (B3)",
     "    if lag < cur[1]:\n        cur[0], cur[1] = t, lag",
     "    if lag <= cur[1]:\n        cur[0], cur[1] = t, lag"),
    ("map: the reader bins no rows (B3)",
     "                cur_b, cur = bin_row(bins, cur_b, cur, t, lag)\n", "\n"),
    ("map: stamps to host and back inverted",
     "        return (h - self.a + self.b * self.s0) / (1.0 + self.b)",
     "        return (h + self.a + self.b * self.s0) / (1.0 + self.b)"),
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
    # 2026-11-13, the reviewer's D3/D5 and the (a)/(b) ruling
    ("node: a stall logged as a refused send",
     "        except kvaser_link.Stall:\n            raise                       # a kvaser TX stall",
     "        except ZeroDivisionError:\n            raise                       # a kvaser TX stall"),
    ("node: ENOBUFS in a no-ACK window a stall", "            if exp:\n                raise\n",
     "            raise\n"),
    ("node: ENOBUFS where frames should complete refused",
     "            if exp:\n                raise\n", "            if False:\n                raise\n"),
    ("node: no flush when the ACK returns",
     "                gone = watch.finish(int(forgive()), excuse=span.__contains__)\n",
     "                gone = []\n"),
    ("node: the held frames' bound from their send, not the ACK",
     "                watch.rebase(t)\n", ""),
    ("node: no schedule shift after a flush", "            t0 += pause", "            pass"),
    ("the flush counted into the next window",
     "        if tc is not None and t_w0 <= tc <= t_cut:",
     "        if tc is not None and tc <= t_cut:"),
    ("a late echo counted as sent",
     "            if tr is not None and te - tr <= kvaser_link.STALL_S:",
     "            if tr is not None:"),
    ("the flushed frames' rows kept in the window",
     "    out = [r for r in rows if node_seq(r) is None or node_seq(r) in mine]",
     "    out = list(rows)"),
    ("NoNode leaves the link as it is", "        line = (link or kvaser_link).set_link(False)",
     "        line = \"not set\""),
    ("step 0 without the link down", "            print(\"    \" + kvaser_link.set_link(False))",
     "            print(\"    not set\")"),
]
# The priority mutant runs where its code runs (the reviewer, 2026-11-11):
# F2's ABOVE_NORMAL on Windows; on Linux (F2's Linux form, the reviewer,
# 2026-11-12), the reader claiming bench.slice without placing itself there,
# which its cgroup, read from outside, gives away. Run from a process that
# is itself in bench.slice, the Linux mutant would not show: the self-test
# runs in build.slice (the harness's placement) or a terminal's app.slice.
PRIO_MUTANT = {
    "nt": ("F2: the reader not at ABOVE_NORMAL",
           "    prio = above_normal()\n    try:\n        reader = _open_witness_reader(",
           "    prio = None\n    try:\n        reader = _open_witness_reader("),
    "posix": ("F2 (Linux): the reader claims bench.slice without placing itself",
              "    if os.name != \"nt\":\n        return place_in_slice(BENCH_SLICE)",
              "    if os.name != \"nt\":\n        return BENCH_SLICE"),
}
PRIO_SKIPPED = {
    "nt": "F2's Linux mutant (Linux code only)",
    "posix": "F2's ABOVE_NORMAL mutant (Windows code only)",
}
MUTANTS.append(PRIO_MUTANT["nt" if os.name == "nt" else "posix"])
# the node's start (the reviewer's ruling (2), 2026-11-20): the 40e6 form,
# the node expecting an ACK from its first frame, and each step's turn-on
MUTANTS += [
    ("the node starts expecting an ACK (40e6's step-0 stall)",
     "        self.expect_ev = ctx.Event()      # clear: no ACK expected yet\n",
     "        self.expect_ev = ctx.Event()      # clear: no ACK expected yet\n"
     "        self.expect_ev.set()\n"),
    ("recstart never turns the stall rule on",
     "            node.expect_acks(True)           # the witness acknowledging\n",
     ""),
    ("flood never turns the stall rule on",
     "            node.expect_acks(True)           # the witness and the board ACK\n",
     ""),
    ("the stall step never turns the stall rule on",
     "            node.expect_acks(True)           # the board acknowledging\n"
     "            rows, _s = window(board, wit, node, 2.0",
     "            rows, _s = window(board, wit, node, 2.0"),
    ("flood turns the stall rule on before the board ACKs",
     "            board.cmd(\"MODE normal\")\n"
     "            node.expect_acks(True)           # the witness and the board ACK\n",
     "            node.expect_acks(True)           # the witness and the board ACK\n"
     "            board.cmd(\"MODE normal\")\n"),
    # 2026-11-20, the hand take for ruling (2): the node's own echoes lost
    # on a full receive queue. Its two mutants (the node's poll(0.0) after
    # a refused send dropped) retired 2026-11-23: canbench's EchoWatch
    # reads after every send, refused ones too (read_after_send, default
    # True, passed by kvaser_link and checked in kvaser_link_test), so the
    # read is can-tools' code now and held_flush_cases pass without it.
    # The two poll(0.0) lines themselves went 2026-11-27, at bench_vport's
    # next change (the reviewer, 2026-11-23).
    ("the node takes error frames",
     "    bus = kvaser_link.open_bus(receive_own_messages=True, error_frames=False)",
     "    bus = kvaser_link.open_bus(receive_own_messages=True)"),
    # 2026-11-20, the reviewer's stall-16 item: the window's cut
    ("a sent seq without its log entry kept out (the stall-16 cut)",
     "    mine = set(x[0] for x in log) | set(sent)",
     "    mine = set(x[0] for x in log)"),
    # ruling Q3 (2026-11-20): the no-ack arm's head frame
    ("score_noack without the head frame's rows",
     "    return sorted(list(rows) + list(getattr(node, \"window_head_rows\", [])),",
     "    return sorted(list(rows),"),
    ("the head frame's rows not kept aside",
     "        node.window_head_rows = [r for r in rows if head is not None\n"
     "                                 and node_seq(r) == head]\n",
     ""),
    ("the head frame's rows left in the share/timing rows",
     "    mine = set(x[0] for x in log) | set(sent)",
     "    mine = set(x[0] for x in log) | set(sent) | {0}"),
    ("the head: any seq sent, echoed or not",
     "                if x[1] in self.unechoed:\n                    self.unechoed.discard(x[1])\n",
     "                if x[1] in self.unechoed:\n                    pass\n"),
    ("the head: a refused send counted",
     "                if not x[2]:\n                    self.fails += 1\n                elif x[0] in self.early:",
     "                if not x[2]:\n                    self.fails += 1\n                    self.unechoed.add(x[0])\n"
     "                elif x[0] in self.early:"),
    ("the head: forgiven frames stay the head",
     "                    self.unechoed.discard(s)\n",
     "                    pass\n"),
    # ruling Q1 (2026-11-20): restart-ms and the kernel's drops while bus-off
    ("the node's link up without restart-ms",
     "        print(\"  node: %s\" % kvaser_link.set_link(True, restart_ms=NODE_RESTART_MS))",
     "        print(\"  node: %s\" % kvaser_link.set_link(True))"),
    ("the node's recovery without restart-ms",
     "    lines.append((recover or kvaser_link.recover)(restart_ms=NODE_RESTART_MS)[1])",
     "    lines.append((recover or kvaser_link.recover)()[1])"),
    ("the flush forgives nothing",
     "                gone = watch.finish(int(forgive()), excuse",
     "                gone = watch.finish(0, excuse"),
    ("the drops never handed to the node's flush",
     "        self.forgive_v.value = self.forgive\n",
     ""),
    ("a node bus-off in an ACK window not a fault",
     "        if g[\"bus_off\"]:\n            return (None, \"the node went bus-off",
     "        if False:\n            return (None, \"the node went bus-off"),
    ("a wrong-rate window's drops not forgiven",
     "                g[\"tx_dropped\"])\n    if any(g.values()):",
     "                0)\n    if any(g.values()):"),
    ("every no-ACK window's drops forgiven",
     "    if wrong_rate:\n        return (\"node's link in this wrong-rate window",
     "    if True:\n        return (\"node's link in this wrong-rate window"),
    ("node_fault blind to a bus-off", "    bf = node_busoff_fault(node, what)\n    if bf:\n        return bf\n",
     ""),
    ("window(): the drops never added", "        node.forgive += add\n", ""),
    ("the stall step blind to a node bus-off",
     "            bf = node_busoff_fault(node, \"stall, peer ACKing\")\n",
     "            bf = None\n"),
    ("stall_report without the node's link since its start",
     "    if since:\n        lines.append(since)\n", ""),
    ("the drops carried into the next span",
     "        self.forgive, self.wrong_rate, self.span_windows = 0, False, []\n"
     "        self.forgive_v.value = 0\n",
     ""),
    # D2 (2026-11-20): the counters read takes 0.67-0.77 s
    ("the link counters read inside the window",
     "    t_w0 = time.perf_counter()\n    # the node's echoes (D3, 2026-11-13)",
     "    t_w0 = time.perf_counter()\n"
     "    link0 = node.counters() if hasattr(node, \"counters\") else None\n"
     "    # the node's echoes (D3, 2026-11-13)"),
    # ruling (a) (2026-11-20): the sends from a wrong-rate span not judged at
    # the flush; each wrong-rate window's figures
    ("the flush excuses nothing from a wrong-rate span",
     "excuse=span.__contains__)", "excuse=None)"),
    ("every no-ACK send excused",
     "        if not exp and wrong():\n            span.add(",
     "        if not exp:\n            span.add("),
    ("the node process never told of a wrong-rate span",
     "            if self.wrong_rate:\n                self.wrong_ev.set()           # before the span's first send\n",
     ""),
    ("the wrong-rate mark kept after the flush",
     "        self.wrong_ev.clear()\n        return f", "        return f"),
    ("the node process not handed the mark",
     "        wrong=wrong_ev.is_set if wrong_ev is not None else (lambda: False))",
     "        wrong=lambda: False)"),
    ("the node process started without the mark",
     "self.forgive_v,\n            self.wrong_ev), daemon=True)",
     "self.forgive_v), daemon=True)"),
    ("the span's windows not kept",
     "        if wrong_rate and not expecting and hasattr(node, \"span_windows\"):",
     "        if False:"),
    ("every no-ACK window a span window",
     "        if wrong_rate and not expecting and hasattr(node, \"span_windows\"):",
     "        if not expecting and hasattr(node, \"span_windows\"):"),
    ("the flush's sends without their send calls",
     "                    self.call_t[x[0]] = x[3]    # its send call, for the span's figures\n",
     ""),
    ("neither echoed nor dropped without the drops",
     "        nd = (\"%d\" % (n - dr) if n >= dr else",
     "        nd = (\"%d\" % n if n >= dr else"),
    ("a send counted in two windows",
     "        left = [(s, tc) for s, tc in left if not (tc is not None and a <= tc <= b)]\n",
     ""),
    ("the span's figures not printed at its flush",
     "            for ln in span_figures(self.flush_gone, self.span_windows):\n"
     "                print(\"    \" + ln)\n",
     "            pass\n"),
    # 2026-11-23, the reviewer's sweep rulings 3 and 4, and the premise rule
    ("recstart: a declared STOP passes again",
     "    return STOP_OUTCOME, \"VOID (declared STOP): \" + text, lines + [",
     "    return True, \"VOID (declared STOP): \" + text, lines + ["),
    ("recstart: a declared STOP without its VOID line",
     "        \"VOID: recstart's declared STOP outcome, not judged: \" + text]",
     "        ]"),
    ("recstart: NOT JUDGED is a FAIL again",
     "    return FAULT, \"VOID (TEST FAULT): \" + text, lines + [",
     "    return False, \"VOID (TEST FAULT): \" + text, lines + ["),
    ("recstart: NOT JUDGED without its VOID line",
     "        \"VOID: recstart's premise not met: \" + text]", "        ]"),
    ("combine: a STOP alone exits 0",
     "    if faults or stops:\n", "    if faults:\n"),
    ("combine: a STOP beside a FAIL not named",
     "                                  \"\".join(\"; \" + x for x in nots))",
     "                                  \"\".join(\"; \" + x for x in nots[:1]))"),
    ("stall: the no-ACKer variant's reason not kept",
     "                not_run.append((\"stall/no-acker\", NO_ACKER_WHY))\n", ""),
    ("verdicts: a variant not run left out",
     "    out += [\"  %-28s NOT RUN: %s\" % (n, why) for n, why in not_run]\n", ""),
    ("verdicts: no RESULT line",
     "    if not_run:\n        out.append(", "    if False:\n        out.append("),
    ("verdicts: a RESULT line on every report",
     "    if not_run:\n        out.append(", "    if True:\n        out.append("),
]


def main():
    global EDGE_DIAG, WITNESS_READER
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--port", help="the X2's USB Serial/JTAG port")
    ap.add_argument("--elf", help="build/vport_bench.elf, checked against the "
                                  "board's @HELLO")
    ap.add_argument("--src-sha", default="(not given)",
                    help="git log -1 --format=%%h -- fw_harness/vport_bench "
                         "(firmware/test/vport_bench before port step 2a)")
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
        print("not run on this system: %s"
              % PRIO_SKIPPED["nt" if os.name == "nt" else "posix"])
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
