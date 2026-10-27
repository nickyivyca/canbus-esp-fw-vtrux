"""Spec 12 entry 2: the same-identifier hold on silicon (step 4 item 9).

The spec's run, as the reviewer ruled it (2026-10-15):
  - the IDENTIFIER: extended 0x18FFDEC0 (read by neither the core nor the
    Bel unit), DLC exactly 3, a 24-bit counter (big-endian) in every
    payload, offered steadily at a mean of 2,000 fps for >= 50,000 frames
    by the PCAN on the vehicle segment;
  - the FILLER: extended 0x0CFF01C0 (SA 0xC0, so the board's vehicle port
    goes normal and the filler is bridged onto the vehicle segment, spec
    2.3; higher priority than the identifier; read by nobody), DLC 8, in
    bursts of 8 at a mean of 1,000 fps, by the Innomaker on the charger
    segment, started FIRST and running throughout;
  - measured ON THE WIRE: the identifier's rate, gaps and DLC by the
    Kvaser (normal mode, device stamps) on the vehicle segment; the
    filler's rate, burst shape and DLC by the Innomaker's own echoes
    (validated as a wire witness 2026-10-06: they plateau at the wire's
    ceiling); the vehicle segment's load recorded, not judged;
  - the vehicle port NORMAL before the identifier starts and throughout it:
    the board's 0x7F4 at its 1 Hz cadence on the Kvaser, and no serial
    "vehicle port -> LISTEN-ONLY" line in the window. Otherwise VOID.

JUDGED (judge(), pure; --self-test drives it):
  VOID  (the stimulus is not the specified run): fewer than 50,000
        identifier frames on the vehicle wire; its mean not within 5 % of
        2,000 fps; not steady (median gap within 5 % of 0.5 ms and 99 % of
        gaps under 1.0 ms; the largest recorded); DLC not 3; the filler's
        mean not within 5 % of 1,000 fps in the identifier's window, or not
        in bursts of 8, or DLC not 8; the port not normal throughout. On
        the witness build, also: the board's record not covering the whole
        run on a fresh board ('W' not before the identifier's first frame,
        or the last status line before W not v2c=0), since RECONCILE's v2c
        counts since boot and its completions since W (reviewer,
        2026-10-16).
  FAIL  any identifier frame out of sequence on the charger side, any
        dropped IN THE RUN WINDOW (witnessed on the vehicle wire, never
        received on the charger side), any received twice; on the witness
        build: RECONCILE
        not BALANCED, ORDER perIdInversions not 0, HOLD deferrals(passes) 0
        or absent ("a run in which the hold never deferred a frame did not
        test it").
  PASS  otherwise. With --flight (the TRUCK image, spec 8.3's flight image -- not the bridge) the board's dump is not
        read: order and drops only, as the spec says.
  Drops outside the run window are recorded with their seq and time from
  the filler start, not judged (reviewer, 2026-10-16): within 5 ms of it,
  as the port's listen-only -> normal switch loss (the known 2.3 defect,
  spec 12 Specified 2026-10-15); anywhere else, as unexplained.

Processes: the two senders run in their own processes so neither paces the
other through the GIL; the Kvaser witness and the board's USB serial
(bench_usb_host.HostPort: DTR/RTS low, so opening it does not reset the
board) live in this one. 'W' resets the board's witness window before the
identifier starts (so before any traffic), 'w' dumps it after
(bench_l3_replay's protocol). Until 2026-10-16 W was sent at the run window's
open, mid-traffic, which made RECONCILE mismatch by construction.

FRESH-BOARD GATE (witness build; user ruling 2026-10-23, via the reviewer):
before anything is sent, the board's status line is read (fresh_gate(),
pure; --self-test drives it with a fake board). v2c=0: the run proceeds, no
reset. v2c above 0 (the baseline's hold12 VOID: earlier cases had left 160
forwarded frames counted since boot): one USB reset (RTS/DTR, no flash;
bench_usb_host.HostPort.hard_reset), the port reopened, and up to 10 s for a
status line reading v2c=0. No status line at all, or none reading 0 after
the reset: VOID, and no sender is started, so no identifier frame is sent.

    py -3.14 bench_hold12.py --self-test
    py -3.14 bench_hold12.py --can-fail
    py -3.14 bench_hold12.py --port COM3 --out <file.txt> [--flight]

Tester, 2026-10-15.
"""

import argparse
import datetime
import json
import multiprocessing as mp
import os
import re
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never
# from the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

BITRATE = 500000
ID = 0x18FFDEC0
FILLER = 0x0CFF01C0
ID_RATE = 2000.0
FILLER_RATE = 1000.0
FILLER_BURST = 8
MIN_FRAMES = 50000
ID_SECONDS = 26.0           # 52,000 at 2,000 fps
PRE_S = 6.0                 # the identifier alone, before the filler
PRE_MIN_S = 5.0             # the pre-phase judged must span at least this
LEAD_S = 3.0                # the filler on before the run window opens
BURSTS_OF_8_MIN = 0.99      # share of complete filler bursts exactly 8
TAIL_S = 2.0
RATE_TOL = 0.05
NOMINAL_GAP_MS = 0.5
BURST_SPLIT_MS = 1.5        # within a burst ~0.3 ms, between ~5.9 ms
DIAG_GAP_MAX_S = 1.5        # 0x7F4's 1 Hz cadence, with margin
# the board prints "... BALANCED" or "... *** MISMATCH -- entries below ...":
# the stars are skipped so a MISMATCH is read as one, not as no line at all
RECONCILE_RE = re.compile(r"RECONCILE: completions=(\d+) forwarded\(v2c\)=(\d+)[\s*]*(\w+)")
ORDER_RE = re.compile(r"perIdInversions=(\d+)")
HOLD_RE = re.compile(r"HOLD: deferrals\(passes\)=(\d+)")
LISTEN_RE = re.compile(r"vehicle port -> LISTEN-ONLY")
V2C_RE = re.compile(r"\| v2c=(\d+) ")
W_MARK = "W SENT"
STATUS_WAIT_S = 6.0         # the board's status line comes every 5 s
RESET_WAIT_S = 10.0         # for a v2c=0 status line after the reset
SWITCH_S = 0.005            # the switch-loss window, as switch_diag used


def seq_of(data_hex):
    b = bytes.fromhex(data_hex)
    return (b[0] << 16) | (b[1] << 8) | b[2]


def frame_bits(ext, dlc):
    """Bits on the wire without stuffing: SOF..EOF + 3 IFS."""
    return (67 if ext else 47) + 8 * dlc


def pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


# --- the verdict, pure --------------------------------------------------------

def judge(veh, chg, serial, dump, t_pre0, t_pre1, t_id0, t_id1,
          flight=False, t_fgo=None):
    """veh: Kvaser rows (t_dev, t_host, id, ext, dlc, hex); chg: Innomaker
    rows (t_dev, t_host, id, ext, dlc, hex, is_rx); serial: (host, line);
    dump: the board's 'w' lines; [t_pre0, t_pre1]: host window of the
    filler-free pre-phase; [t_id0, t_id1]: host window of the run (the
    filler on, the port normal); t_fgo: host time the filler was started
    (default t_pre1 + 0.05, as run_bench sets t_pre1). -> (verdict, lines)."""
    void, fail, rec = [], [], []
    if t_fgo is None:
        t_fgo = t_pre1 + 0.05
    vid = [r for r in veh if r[2] == ID and r[3]]
    vseq = [seq_of(r[5]) for r in vid]
    if len(vid) < 2:
        return ("VOID", ["the identifier was not seen on the vehicle wire"])
    dlcs = sorted(set(r[4] for r in vid))
    if dlcs != [3]:
        void.append("identifier DLC %s, not 3" % dlcs)
    # "offered steadily": the pre-phase, the identifier alone on the wire
    pre = sorted(r[0] for r in vid if t_pre0 <= r[1] <= t_pre1)
    if len(pre) < 2:
        void.append("no identifier frames in the pre-phase")
    else:
        pg = [(y - x) * 1000.0 for x, y in zip(pre, pre[1:])]
        pspan = pre[-1] - pre[0]
        pmed = statistics.median(pg)
        punder = sum(1 for g in pg if g < 2 * NOMINAL_GAP_MS) / float(len(pg))
        rec.append("pre-phase, the identifier alone on the vehicle wire "
                   "(Kvaser, device clock): %d frames over %.2f s, %.1f fps, "
                   "gaps median %.3f / p99 %.3f / max %.3f ms, %.2f %% under "
                   "%.1f ms" % (len(pre), pspan, (len(pre) - 1) / pspan, pmed,
                                pct(pg, 0.99), max(pg), punder * 100,
                                2 * NOMINAL_GAP_MS))
        if pspan < PRE_MIN_S:
            void.append("pre-phase %.2f s, under %.1f s" % (pspan, PRE_MIN_S))
        if (abs(pmed - NOMINAL_GAP_MS) > RATE_TOL * NOMINAL_GAP_MS
                or punder < 0.99):
            void.append("identifier not offered steadily (pre-phase): median "
                        "gap %.3f ms (want %.3f +/- 5 %%), %.2f %% of gaps "
                        "under %.1f ms (want >= 99 %%)"
                        % (pmed, NOMINAL_GAP_MS, punder * 100,
                           2 * NOMINAL_GAP_MS))
    # the run: mean and count judged on the wire, gaps recorded
    run = sorted(r[0] for r in vid if t_id0 <= r[1] <= t_id1)
    if len(run) < 2:
        void.append("no identifier frames in the run window")
    else:
        gaps = [(y - x) * 1000.0 for x, y in zip(run, run[1:])]
        rate = (len(run) - 1) / (run[-1] - run[0])
        fil = sorted(r[0] for r in veh if r[2] == FILLER and r[3])
        slow = [(x, y) for x, y in zip(run, run[1:])
                if (y - x) * 1000.0 >= 2 * NOMINAL_GAP_MS]
        with_f = 0
        import bisect
        for x, y in slow:
            if bisect.bisect_left(fil, y) - bisect.bisect_right(fil, x) > 0:
                with_f += 1
        rec.append("run, the identifier on the vehicle wire with the filler "
                   "bridged: %d frames, %.1f fps; gaps median %.3f / p99 %.3f "
                   "/ max %.3f ms, %d of %d gaps >= %.1f ms, %d of those with a "
                   "bridged filler frame inside (recorded, not judged)"
                   % (len(run), rate, statistics.median(gaps), pct(gaps, 0.99),
                      max(gaps), len(slow), len(gaps), 2 * NOMINAL_GAP_MS,
                      with_f))
        if len(run) < MIN_FRAMES:
            void.append("%d identifier frames in the run, under %d"
                        % (len(run), MIN_FRAMES))
        if abs(rate - ID_RATE) > RATE_TOL * ID_RATE:
            void.append("identifier mean %.1f fps in the run, not within 5 %% "
                        "of %.0f" % (rate, ID_RATE))
    # the filler on the charger wire (the Innomaker's echoes)
    fe = sorted(r[0] for r in chg if r[2] == FILLER and r[3] and not r[6]
                and t_id0 <= r[1] <= t_id1)
    fdl = sorted(set(r[4] for r in chg if r[2] == FILLER and not r[6]))
    if len(fe) < 2:
        void.append("no filler echoes in the run window")
    else:
        frate = len(fe) / (t_id1 - t_id0)
        fg = [(y - x) * 1000.0 for x, y in zip(fe, fe[1:])]
        bursts, cur = [], 1
        for g in fg:
            if g > BURST_SPLIT_MS:
                bursts.append(cur)
                cur = 1
            else:
                cur += 1
        bursts.append(cur)
        inner = bursts[1:-1] or bursts
        within = [g for g in fg if g <= BURST_SPLIT_MS]
        between = [g for g in fg if g > BURST_SPLIT_MS]
        not8 = [x for x in inner if x != FILLER_BURST]
        share8 = 1.0 - len(not8) / float(len(inner))
        rec.append("filler on the charger wire (Innomaker echoes) in the run: "
                   "%d frames, %.1f fps, %d complete bursts, %.2f %% exactly "
                   "%d (the rest: %s), within-burst gap median %.3f ms, between "
                   "bursts median %.3f ms, DLC %s"
                   % (len(fe), frate, len(inner), share8 * 100, FILLER_BURST,
                      not8[:12] if not8 else "none",
                      statistics.median(within) if within else -1,
                      statistics.median(between) if between else -1, fdl))
        if abs(frate - FILLER_RATE) > RATE_TOL * FILLER_RATE:
            void.append("filler mean %.1f fps in the run, not within 5 %% of "
                        "%.0f" % (frate, FILLER_RATE))
        if share8 < BURSTS_OF_8_MIN:
            void.append("%.2f %% of complete filler bursts exactly %d, under "
                        "%.0f %%" % (share8 * 100, FILLER_BURST,
                                     BURSTS_OF_8_MIN * 100))
        if fdl != [8]:
            void.append("filler DLC %s, not 8" % fdl)
    # the vehicle port normal throughout
    d4 = sorted(r[1] for r in veh if r[2] == 0x7F4 and not r[3])
    win = [t for t in d4 if t_id0 - 2.0 <= t <= t_id1]
    pts = [t_id0 - 0.0] + [t for t in win if t >= t_id0] + [t_id1]
    before = [t for t in win if t < t_id0]
    longest = max((b - a for a, b in zip(pts, pts[1:])), default=99.0)
    lo = [(h, ln) for h, ln in serial if LISTEN_RE.search(ln)
          and t_id0 - 0.5 <= h <= t_id1]
    rec.append("vehicle port: %d 0x7F4 on the Kvaser in the window (+ %d in "
               "the 2 s before), longest stretch without one %.2f s; serial "
               "LISTEN-ONLY lines in the window: %d"
               % (len([t for t in win if t >= t_id0]), len(before), longest,
                  len(lo)))
    if not before or longest > DIAG_GAP_MAX_S:
        void.append("the vehicle port not shown normal throughout: %s"
                    % ("no 0x7F4 in the 2 s before the identifier"
                       if not before else
                       "%.2f s without a 0x7F4 inside the window" % longest))
    if lo:
        void.append("the vehicle port went LISTEN-ONLY at host %.3f" % lo[0][0])
    # the vehicle segment's load, recorded only
    vw = [r for r in veh if t_id0 <= r[1] <= t_id1]
    bits = sum(frame_bits(r[3], r[4]) for r in vw)
    rec.append("vehicle segment load in the window: %.1f %% of %d bit/s (%d "
               "frames, stuffing excluded; recorded, not judged)"
               % (100.0 * bits / (BITRATE * (t_id1 - t_id0)), BITRATE, len(vw)))
    # the charger side: order, drops, duplicates
    cs = [seq_of(r[5]) for r in chg if r[2] == ID and r[3] and r[6]]
    inv = [(i, cs[i - 1], cs[i]) for i in range(1, len(cs)) if cs[i] < cs[i - 1]]
    dup = len(cs) - len(set(cs))
    missing = sorted(set(vseq) - set(cs))
    rec.append("identifier on the charger side: %d received, %d out of "
               "sequence, %d duplicate(s), %d witnessed on the vehicle wire "
               "but never received" % (len(cs), len(inv), dup, len(missing)))
    if inv:
        fail.append("%d identifier frame(s) out of sequence on the charger "
                    "side, first %d after %d" % (len(inv), inv[0][2], inv[0][1]))
    # drops are judged in the run window only (reviewer, 2026-10-16). A frame
    # lost within SWITCH_S of the filler start is the port's listen-only ->
    # normal switch, the known 2.3 defect (spec 12, Specified 2026-10-15):
    # recorded with its seq and time, not judged. Any other drop outside the
    # window is recorded as unexplained, not judged.
    vt = {}
    for r in vid:
        vt.setdefault(seq_of(r[5]), r[1])
    in_win = [s for s in missing if t_id0 <= vt[s] <= t_id1]
    at_sw = [s for s in missing if s not in in_win
             and abs(vt[s] - t_fgo) <= SWITCH_S]
    other = [s for s in missing if s not in in_win and s not in at_sw]
    for s in at_sw:
        rec.append("dropped at the port's listen-only -> normal switch (the "
                   "known 2.3 defect, spec 12 Specified 2026-10-15; outside "
                   "the run window, not judged): seq %d, %+.4f s from the "
                   "filler start" % (s, vt[s] - t_fgo))
    for s in other:
        rec.append("dropped OUTSIDE the run window and NOT at the switch "
                   "(unexplained; not judged): seq %d, %+.4f s from the "
                   "filler start" % (s, vt[s] - t_fgo))
    rec.append("drops: %d in the run window (judged), %d at the switch, %d "
               "elsewhere outside the window" % (len(in_win), len(at_sw),
                                                 len(other)))
    if in_win:
        fail.append("%d identifier frame(s) dropped in the run window, first "
                    "seq %d" % (len(in_win), in_win[0]))
    if dup:
        fail.append("%d identifier frame(s) received twice" % dup)
    if not cs:
        fail.append("no identifier frame received on the charger side")
    # the board's own record
    if not flight:
        # the board's record must cover the whole run on a fresh board
        # (reviewer, 2026-10-16): RECONCILE's v2c counts since boot and its
        # completions since 'W', so W goes before the identifier's first
        # frame, and the board's last status line before W reads v2c=0.
        # Otherwise RECONCILE mismatches by construction: VOID, not FAIL.
        t_first = min(r[1] for r in vid)
        w_at = [h for h, s in serial if W_MARK in s]
        if not w_at:
            void.append("no '%s' mark: the board's witness window was never "
                        "reset" % W_MARK)
        elif w_at[0] >= t_first:
            void.append("the board's witness window was reset %.3f s AFTER "
                        "the identifier's first frame, so its record does not "
                        "cover the run (RECONCILE mismatches by construction)"
                        % (w_at[0] - t_first))
        else:
            # fresh: RECONCILE's v2c counts since boot, so on a fresh board it
            # cannot exceed the identifier frames seen on the vehicle wire
            # (nothing else on that segment is forwarded toward the charger).
            # A status line before W, when there is one (they come every 5 s,
            # so there may be none), must also read v2c=0. Revised 2026-10-16:
            # requiring that status line voided a clean run whose port opened
            # late in the 5 s cycle.
            st = [(h, int(m.group(1))) for h, s in serial
                  for m in [V2C_RE.search(s)] if m and h < w_at[0]]
            mr = RECONCILE_RE.search("\n".join(dump))
            how = []
            if st and st[-1][1] != 0:
                void.append("the board had forwarded v2c=%d before the witness "
                            "reset (not fresh), so RECONCILE mismatches by "
                            "construction" % st[-1][1])
            elif mr and int(mr.group(2)) > len(vid):
                void.append("the board's RECONCILE v2c=%s (since boot) exceeds "
                            "the %d identifier frames on the vehicle wire: not "
                            "fresh, so RECONCILE mismatches by construction"
                            % (mr.group(2), len(vid)))
            elif not st and not mr:
                void.append("neither a status line before the witness reset "
                            "nor a RECONCILE line: the board is not shown fresh")
            else:
                if st:
                    how.append("last status before it v2c=0")
                if mr:
                    how.append("RECONCILE v2c=%s since boot <= %d identifier "
                               "frames on the vehicle wire"
                               % (mr.group(2), len(vid)))
                rec.append("board record: witness reset %.3f s before the "
                           "identifier's first frame; fresh board: %s"
                           % (t_first - w_at[0], "; ".join(how)))
        text = "\n".join(dump)
        m = RECONCILE_RE.search(text)
        o = ORDER_RE.search(text)
        h = HOLD_RE.search(text)
        rec.append("board dump: %s; %s; %s"
                   % (m.group(0) if m else "NO RECONCILE line",
                      ("perIdInversions=%s" % o.group(1)) if o else
                      "NO ORDER line",
                      h.group(0) if h else "NO HOLD line"))
        if not m or m.group(3) != "BALANCED":
            fail.append("RECONCILE not BALANCED (%s)"
                        % (m.group(0) if m else "absent"))
        if not o or int(o.group(1)) != 0:
            fail.append("the board's ORDER record shows perIdInversions=%s"
                        % (o.group(1) if o else "absent"))
        if not h or int(h.group(1)) == 0:
            fail.append("the hold never deferred: %s"
                        % (h.group(0) if h else "no HOLD line"))
    if void:
        return ("VOID", rec + ["VOID: " + x for x in void]
                + ["(not judged: " + x + ")" for x in fail])
    if fail:
        return ("FAIL", rec + ["FAIL: " + x for x in fail])
    return ("PASS", rec)


# --- the fresh-board gate -----------------------------------------------------------

def fresh_gate(status_v2c, reset, now, status_s=STATUS_WAIT_S,
               reset_s=RESET_WAIT_S):
    """Before anything is sent: is the board fresh (v2c=0)? status_v2c(t)
    -> the v2c of the next status line within t s, or None; reset() sends
    the USB reset and reopens the port; now() -> s. -> (proceed, lines);
    a refusal's last line starts "VOID:"."""
    v = status_v2c(status_s)
    if v is None:
        return False, ["VOID: no status line from the board within %.0f s "
                       "before the run, so its v2c is unknown; no sender "
                       "started, no identifier frame sent" % status_s]
    if v == 0:
        return True, ["fresh board: status v2c=0 before the run; no reset"]
    lines = ["the board's status read v2c=%d before the run (not fresh): USB "
             "reset (RTS/DTR, no flash)" % v]
    t_r = now()
    reset()
    seen = []
    while True:
        left = t_r + reset_s - now()
        if left <= 0:
            break
        v2 = status_v2c(left)
        if v2 is None:
            break
        seen.append(v2)
        if v2 == 0:
            lines.append("after the reset: status v2c=0 at +%.1f s; the run "
                         "proceeds" % (now() - t_r))
            return True, lines
    lines.append("VOID: no status line reading v2c=0 within %.0f s of the "
                 "reset (read after it: %s); no sender started, no "
                 "identifier frame sent"
                 % (reset_s, ", ".join("v2c=%d" % x for x in seen)
                    or "no status line"))
    return False, lines


class _FakeBoard(object):
    """The gate's self-test board: a status line every period s from first
    s, reading before (None: no status line ever); after a reset, from
    reset + after_first s, reading the values of after in turn (the last
    one repeated), or none if after is empty."""

    def __init__(self, before, after, first=2.0, after_first=4.0,
                 period=5.0):
        self.t, self.before, self.after = 0.0, before, list(after)
        self.next_t = first if before is not None else None
        self.after_first, self.period = after_first, period
        self.resets, self.k = 0, 0

    def now(self):
        return self.t

    def reset(self):
        self.resets += 1
        self.t += 0.4                    # hard_reset's two 0.2 s sleeps
        self.next_t = (self.t + self.after_first) if self.after else None
        self.k = 0

    def status_v2c(self, timeout):
        if self.next_t is None or self.next_t > self.t + timeout:
            self.t += timeout
            return None
        self.t = self.next_t
        self.next_t += self.period
        if self.resets == 0:
            return self.before
        v = self.after[min(self.k, len(self.after) - 1)]
        self.k += 1
        return v


def gate_self_test(check):
    def run(before, after, **kw):
        b = _FakeBoard(before, after, **kw)
        ok, lines = fresh_gate(b.status_v2c, b.reset, b.now)
        return ok, lines, b.resets
    ok, lines, n = run(0, [])
    check(ok and n == 0, "gate: a fresh board (v2c=0) -> proceeds, no reset")
    ok, lines, n = run(160, [0])
    check(ok and n == 1, "gate: v2c=160, then 0 after one reset -> proceeds")
    ok, lines, n = run(160, [160])
    check(not ok and n == 1 and lines[-1].startswith("VOID:"),
          "gate: v2c=160 before and after the reset -> VOID")
    ok, lines, n = run(None, [])
    check(not ok and n == 0 and lines[-1].startswith("VOID:"),
          "gate: no status line at all -> VOID, no reset")
    ok, lines, n = run(160, [])
    check(not ok and n == 1 and lines[-1].startswith("VOID:"),
          "gate: no status line after the reset -> VOID")
    ok, lines, n = run(160, [0], after_first=10.5)
    check(not ok and n == 1,
          "gate: v2c=0 only 10.5 s after the reset -> VOID (the boundary)")
    ok, lines, n = run(160, [160, 0])
    check(ok and n == 1,
          "gate: v2c=160 once after the reset, then 0 -> proceeds")
    # the wiring: run_bench gates, and refuses, before any sender starts
    import inspect
    src = inspect.getsource(run_bench)
    i_gate, i_void = src.find("fresh_gate("), src.find("if not fresh:")
    i_send = src.find("pf.start()")
    check(0 <= i_gate < i_void < i_send,
          "gate: run_bench calls fresh_gate and refuses before pf.start()")


# --- self-test -------------------------------------------------------------------

def _synth(n_run=50400, pre_s=6.0, pre_jitter=None, run_gap=0.0005,
           run_jitter=None, filler_rate=1000.0, burst=8, split_every=None,
           swap=None, drop=None, dupe=None, listen_at=None, dump_hold=1234,
           dump_rec="BALANCED", dump_inv=0, dlc=3, w_rel=-0.7, v2c_before=0,
           dump_v2c=None):
    """A synthetic record: the identifier alone for pre_s, then the filler
    starts and runs LEAD_S before the run window, which holds n_run frames.
    -> veh, chg, serial, dump, t_pre0, t_pre1, t_id0, t_id1."""
    t_pre0 = 100.0
    t_pre1 = t_pre0 + pre_s
    t_id0 = t_pre1 + LEAD_S
    veh, chg, times = [], [], []
    t = t_pre0
    k = 0
    in_run = 0
    while True:
        if t < t_pre1:
            g = pre_jitter(k) if pre_jitter else 0.0005
        elif t < t_id0:
            g = 0.0005
        else:
            g = run_jitter(k) if run_jitter else run_gap
        times.append(t)
        if t >= t_id0:
            in_run += 1
            if in_run >= n_run:
                break
        t += g
        k += 1
    t_id1 = times[-1]
    for k, tt in enumerate(times):
        h = "%02x%02x%02x" % ((k >> 16) & 255, (k >> 8) & 255, k & 255)
        veh.append((tt, tt, ID, True, dlc,
                    h + ("00" * (dlc - 3) if dlc > 3 else "")))
    period = burst / filler_rate
    t = t_pre1
    nb = 0
    while t < t_id1 + 1.0:
        for j in range(burst):
            tt = t + j * 0.0003
            if split_every and nb % split_every == 0 and j >= 5:
                tt += 0.002                       # a burst split 5 + 3
            chg.append((tt, tt, FILLER, True, 8, "00" * 8, False))
            veh.append((tt + 0.0001, tt + 0.0001, FILLER, True, 8, "00" * 8))
        t += period
        nb += 1
    order = list(range(len(times)))
    if swap is not None:
        order[swap], order[swap + 1] = order[swap + 1], order[swap]
    if drop is not None:
        order.remove(drop)
    if dupe is not None:
        order.insert(dupe + 1, dupe)
    for k in order:
        h = "%02x%02x%02x" % ((k >> 16) & 255, (k >> 8) & 255, k & 255)
        chg.append((times[k] + 0.001, times[k] + 0.001, ID, True, 3, h, True))
    tt = t_pre1 + 0.2
    while tt < t_id1 + 1.0:
        veh.append((tt, tt, 0x7F4, False, 8, "04090100c0ff0000"))
        tt += 1.0
    serial = []
    if v2c_before is not None:
        serial.append((t_pre0 - 3.0, "t=15s state=PASSTHROUGH vmax=0mV "
                       "chgmax=0cA ibat=0cA soc=0.0%% chg=0 | v2c=%d c2v=0 "
                       "mod=0 synth=0" % v2c_before))
    if w_rel is not None:
        serial.append((t_pre0 + w_rel, "# W SENT at the start"))
    serial.append((t_pre1 + 0.01, "vehicle port -> normal (300 us)"))
    if listen_at is not None:
        serial.append((t_id0 + listen_at,
                       "vehicle port -> LISTEN-ONLY (300 us)"))
    dump = ["RECONCILE: completions=%d forwarded(v2c)=%d %s"
            % (n_run, n_run if dump_v2c is None else dump_v2c, dump_rec),
            "ORDER (whole run): inversions=0 maxDisplacement=0 "
            "perIdInversions=%d idsTracked=2 idTableOverflow=0" % dump_inv,
            "HOLD: deferrals(passes)=%d" % dump_hold]
    return veh, chg, serial, dump, t_pre0, t_pre1, t_id0, t_id1


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    def run(flight=False, **kw):
        veh, chg, serial, dump, a0, a1, t0, t1 = _synth(**kw)
        return judge(veh, chg, serial, [] if flight else dump, a0, a1, t0, t1,
                     flight=flight, t_fgo=a1)

    def has(lines, text):
        return any(text in x for x in lines)
    v, lines = run()
    check(v == "PASS", "the specified run, clean -> PASS")
    # the run's wire gaps reshaped by the bridged filler, as on the bench
    # (median ~0.43 ms, ~7 %% over 1 ms), the pre-phase steady: recorded only
    v, lines = run(run_jitter=lambda k: 0.0026 if k % 14 == 0 else 0.00035)
    check(v == "PASS" and has(lines, "recorded, not judged"),
          "the run's gaps reshaped by the bridged filler (the mean kept) are "
          "recorded, not judged -> PASS")
    v, lines = run(swap=20000)
    check(v == "FAIL" and has(lines, "out of sequence"),
          "can-fail: one swapped pair on the charger side -> FAIL")
    v, lines = run(drop=21000)
    check(v == "FAIL" and has(lines, "dropped in the run window"),
          "can-fail: one frame dropped in the run window -> FAIL")
    # drops outside the window (reviewer, 2026-10-16): 12001 is 0.5 ms after
    # the filler start (the switch loss), 15000 is in the lead, 1.5 s on;
    # the window opens at frame 18000
    v, lines = run(drop=12001)
    check(v == "PASS" and has(lines, "known 2.3 defect")
          and has(lines, "seq 12001, +0.0005 s"),
          "a frame lost at the switch is recorded as the known 2.3 defect "
          "with its seq and time, not judged -> PASS")
    v, lines = run(drop=15000)
    check(v == "PASS" and has(lines, "NOT at the switch")
          and has(lines, "seq 15000"),
          "a drop in the lead away from the switch is recorded as "
          "unexplained, not judged -> PASS")
    v, lines = run(drop=18000)
    check(v == "FAIL" and has(lines, "dropped in the run window, first seq "
                                     "18000"),
          "can-fail: the window's first frame dropped -> FAIL")
    # the board's record must cover the run on a fresh board (reviewer,
    # 2026-10-16), else RECONCILE/ORDER are VOID, not FAIL
    v, lines = run(w_rel=9.0)
    check(v == "VOID" and has(lines, "AFTER the identifier's first frame"),
          "can-fail: W sent mid-traffic (at the window open, as the first "
          "evidence run did) -> VOID")
    v, lines = run(w_rel=None)
    check(v == "VOID" and has(lines, "never reset"),
          "can-fail: no W mark -> VOID")
    v, lines = run(v2c_before=3075)
    check(v == "VOID" and has(lines, "not fresh"),
          "can-fail: the board forwarded frames before W (v2c=3075) -> VOID")
    # no status line before W (the port opened late in the board's 5 s
    # cycle, as on the 02:33 re-run): freshness from RECONCILE's v2c
    v, lines = run(v2c_before=None)
    check(v == "PASS" and has(lines, "fresh board: RECONCILE v2c="),
          "no status line before W: freshness shown by RECONCILE's v2c "
          "against the wire -> PASS")
    v, lines = run(v2c_before=None, dump_v2c=200000)
    check(v == "VOID" and has(lines, "exceeds") and has(lines, "not fresh"),
          "can-fail: no status line before W and RECONCILE v2c=200000 (since "
          "boot) above the wire's identifier count -> VOID")
    v, lines = run(dump_v2c=200000)
    check(v == "VOID" and has(lines, "exceeds"),
          "can-fail: a status line v2c=0 before W but RECONCILE v2c above "
          "the wire's identifier count -> VOID")
    v, lines = run(w_rel=9.0, flight=True)
    check(v == "PASS",
          "the flight image has no witness record: W's placement not judged "
          "-> PASS")
    v, lines = run(dupe=22000)
    check(v == "FAIL" and has(lines, "received twice"),
          "can-fail: one frame received twice -> FAIL")
    v, lines = run(dump_hold=0)
    check(v == "FAIL" and has(lines, "never deferred"),
          "can-fail: HOLD deferrals(passes)=0 -> FAIL")
    v, lines = run(dump_rec="MISMATCH")
    check(v == "FAIL" and has(lines, "RECONCILE"),
          "can-fail: RECONCILE MISMATCH -> FAIL")
    # the board's own wording (witness c3ecabc2, 2026-10-16 02:18), which the
    # first parser read as "absent"
    v, lines = run(dump_rec="*** MISMATCH -- entries below are NOT a "
                            "complete record ***")
    check(v == "FAIL" and has(lines, "RECONCILE not BALANCED (RECONCILE:")
          and not has(lines, "absent") and not has(lines, "NO RECONCILE"),
          "can-fail: the board's '*** MISMATCH' line is read as a MISMATCH, "
          "not as an absent line -> FAIL")
    v, lines = run(dump_inv=1)
    check(v == "FAIL" and has(lines, "perIdInversions=1"),
          "can-fail: the board's ORDER perIdInversions=1 -> FAIL")
    v, lines = run(pre_jitter=lambda k: 0.0012 if k % 50 == 0 else 0.00049)
    check(v == "VOID" and has(lines, "not offered steadily"),
          "can-fail: the pre-phase with 2 %% of gaps at 1.2 ms (the mean "
          "kept) -> VOID")
    v, lines = run(pre_s=4.0)
    check(v == "VOID" and has(lines, "pre-phase") and has(lines, "under 5.0 s"),
          "can-fail: a pre-phase of 4 s -> VOID")
    v, lines = run(run_gap=1 / 1800.0)
    check(v == "VOID" and has(lines, "in the run, not within 5 %"),
          "can-fail: the identifier at 1,800 fps in the run -> VOID")
    v, lines = run(n_run=40000)
    check(v == "VOID" and has(lines, "under 50000"),
          "can-fail: 40,000 frames in the run -> VOID")
    v, lines = run(dlc=4)
    check(v == "VOID" and has(lines, "DLC"),
          "can-fail: identifier DLC 4 -> VOID")
    v, lines = run(filler_rate=900.0)
    check(v == "VOID" and has(lines, "filler mean"),
          "can-fail: the filler at 900 fps -> VOID")
    v, lines = run(burst=6)
    check(v == "VOID" and has(lines, "exactly 8, under 99"),
          "can-fail: the filler in bursts of 6 -> VOID")
    v, lines = run(split_every=50)
    check(v == "VOID" and has(lines, "exactly 8, under 99"),
          "can-fail: 2 %% of filler bursts split 5 + 3 -> VOID")
    v, lines = run(split_every=400)
    check(v == "PASS" and has(lines, "% exactly 8"),
          "1 burst in 400 split 5 + 3 (99.5 %% exactly 8) -> PASS, the split "
          "ones recorded; 1 in 200 gives 98.99 %% and is VOID (the boundary)")
    v, lines = run(split_every=200)
    check(v == "VOID", "...and 1 in 200 (98.99 %%) -> VOID")
    v, lines = run(listen_at=10.0)
    check(v == "VOID" and has(lines, "LISTEN-ONLY"),
          "can-fail: the port listen-only mid-run -> VOID")
    v, lines = run(flight=True, dump_hold=0)
    check(v == "PASS", "--flight: no dump read, order and drops only -> PASS")
    v, lines = run(flight=True, swap=5)
    check(v == "FAIL", "--flight can-fail: a swapped pair still FAILS")
    gate_self_test(check)
    print("\nbench_hold12 self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --- the bench --------------------------------------------------------------------

def above_normal():
    """This process to ABOVE_NORMAL on Windows (as run_scenario's sims),
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


def gap_stats(gaps_ms, nominal_ms):
    """Sender-side gaps (pacer, ms) -> dict, for the record only."""
    g = list(gaps_ms[1:])
    if not g:
        return {}
    return dict(n=len(g), median_ms=statistics.median(g), p99_ms=pct(g, 0.99),
                max_ms=max(g), under_2x=sum(1 for x in g if x < 2 * nominal_ms)
                / float(len(g)))


def proc_identifier(go, done, out_path, seconds):
    """The PCAN sends the identifier, paced, when `go` is set."""
    import can
    import bench_pacer
    prio = above_normal()
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE,
                  auto_reset=True)
    buf = bytearray(3)
    msg = can.Message(arbitration_id=ID, is_extended_id=True, data=buf)

    def seq_writer(k):
        msg.data = bytearray(((k >> 16) & 255, (k >> 8) & 255, k & 255))

    def send():
        bus.send(msg, timeout=0.05)
    stop = threading.Event()

    def drain():
        while not stop.is_set():
            bus.recv(timeout=0.05)
    th = threading.Thread(target=drain, daemon=True)
    th.start()
    go.wait()
    t0 = time.perf_counter()
    off = bench_pacer.offer(send, ID_RATE, seconds, seq_writer=seq_writer)
    t1 = time.perf_counter()
    stop.set()
    th.join(timeout=1.0)
    json.dump(dict(sent=off.sent, errs=off.errs, first_err=off.first_err,
                   t0=t0, t1=t1, max_lag_ms=off.max_lag_ms, priority=prio,
                   sender_gaps=gap_stats(off._sender_gaps_ms, NOMINAL_GAP_MS)),
              open(out_path, "w"))
    bus.shutdown()
    done.set()


def proc_filler(fgo, ready, rows_path, seconds):
    """The Innomaker sends the filler and logs everything it receives on
    the same handle: its echoes (the filler's wire witness) and the
    forwarded identifier."""
    import bench_pacer
    import bench_rate_feasibility as RF
    import can
    prio = above_normal()
    bus = RF.open_gs_usb()
    rows = []
    lock = threading.Lock()
    halt = threading.Event()

    def rx():
        while not halt.is_set():
            m = bus.recv(timeout=0.05)
            if m is None:
                continue
            h = time.perf_counter()
            with lock:
                rows.append((m.timestamp, h, m.arbitration_id,
                             bool(m.is_extended_id), m.dlc, bytes(m.data).hex(),
                             bool(getattr(m, "is_rx", True))))
    th = threading.Thread(target=rx, daemon=True)
    th.start()
    msg = can.Message(arbitration_id=FILLER, is_extended_id=True,
                      data=bytearray(8))

    def send():
        bus.send(msg, timeout=0.05)
    ready.set()
    fgo.wait()
    off = bench_pacer.offer(send, FILLER_RATE, seconds, burst=FILLER_BURST)
    time.sleep(0.5)
    halt.set()
    th.join(timeout=2.0)
    with open(rows_path, "w") as f:
        f.write("# t_dev\tt_host\tid\text\tdlc\thex\tis_rx; filler sent=%d "
                "errs=%d first_err=%s priority=%s max_lag_ms=%.2f\n"
                % (off.sent, off.errs, off.first_err, prio, off.max_lag_ms))
        for r in rows:
            f.write("%.6f\t%.6f\t%X\t%d\t%d\t%s\t%d\n"
                    % (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))
    bus.shutdown()


def read_rows(path, chg=False):
    out = []
    for ln in open(path):
        if ln.startswith("#"):
            continue
        p = ln.rstrip("\n").split("\t")
        r = (float(p[0]), float(p[1]), int(p[2], 16), p[3] == "1", int(p[4]),
             p[5])
        out.append(r + ((p[6] == "1",) if chg else ()))
    return out


WINDOWS_RE = re.compile(r"windows \(host perf s\): pre-phase ([\d.]+)-([\d.]+), "
                        r"filler start ([\d.]+), run ([\d.]+)-([\d.]+)")


def rejudge(base, flight, out_path):
    """Judge a saved run again from its files (<base>_vehicle_rows.txt,
    _charger_rows.txt, _serial.txt and the windows line of <base>.txt), with
    the current judge. Agreement check: the charger-side counts line must be
    the same as the original run printed, else the files were not re-read
    as the run read them (exit 3)."""
    orig = open(base + ".txt", encoding="utf-8").read()
    m = WINDOWS_RE.search(orig)
    if not m:
        print("TEST FAULT: no windows line in %s.txt" % base)
        return 3
    t_pre0, t_pre1, t_fgo, t_id0, t_id1 = [float(x) for x in m.groups()]
    veh = read_rows(base + "_vehicle_rows.txt")
    chg = read_rows(base + "_charger_rows.txt", chg=True)
    serial = []
    for ln in open(base + "_serial.txt", encoding="utf-8", errors="replace"):
        h, _, s = ln.rstrip("\n").partition("\t")
        try:
            serial.append((float(h), s))
        except ValueError:
            continue
    dump = []
    if not flight:
        k = [i for i, (h, s) in enumerate(serial)
             if "completion-order dump" in s]
        dump = [s for h, s in serial[k[0]:]] if k else []
    v, lines = judge(veh, chg, serial, dump, t_pre0, t_pre1, t_id0, t_id1,
                     flight=flight, t_fgo=t_fgo)
    out = open(out_path, "w", encoding="utf-8")

    def log(s):
        print(s)
        out.write(s + "\n")
    log("RE-JUDGED %s from its saved files with the current judge "
        "(windows: pre-phase %.3f-%.3f, filler start %.3f, run %.3f-%.3f)"
        % (base.replace("\\", "/").split("/")[-1], t_pre0, t_pre1, t_fgo,
           t_id0, t_id1))
    cnt = [x for x in lines if x.startswith("identifier on the charger side")]
    same = bool(cnt) and ("  " + cnt[0]) in orig
    log("agreement with the original run's charger-side counts: %s"
        % ("SAME" if same else "DIFFERENT -- TEST FAULT"))
    for s in lines:
        log("  " + s)
    log("12 entry 2 (%s), re-judged: %s"
        % ("flight image" if flight else "witness build", v))
    out.close()
    if not same:
        return 3
    return 0 if v == "PASS" else 1


def run_bench(a):
    import can
    import bench_usb_host as U
    above_normal()
    base = os.path.splitext(a.out)[0]
    out = open(a.out, "w", encoding="utf-8")

    def log(s):
        print(s)
        out.write(s + "\n")
        out.flush()
    log("run at %s; %s image; identifier 0x%08X DLC 3 at %.0f fps: %.0f s "
        "alone (the pre-phase), then the filler 0x%08X DLC 8 in bursts of %d "
        "at %.0f fps, %.0f s lead, then a %.0f s run window"
        % (datetime.datetime.now().isoformat(),
           "FLIGHT (truck)" if a.flight else "WITNESS", ID, ID_RATE, PRE_S,
           FILLER, FILLER_BURST, FILLER_RATE, LEAD_S, ID_SECONDS))
    hp = U.HostPort(a.port, base + "_serial.txt")
    if not a.flight:
        port = {"hp": hp, "i": 0}

        def status_v2c(timeout):
            end = time.perf_counter() + timeout
            while True:
                st = port["hp"].stamped
                while port["i"] < len(st):
                    m = V2C_RE.search(st[port["i"]][1])
                    port["i"] += 1
                    if m:
                        return int(m.group(1))
                if time.perf_counter() >= end:
                    return None
                time.sleep(0.05)

        def reset():
            # the chip resets and its USB-Serial-JTAG port re-enumerates:
            # closed, then reopened by name (lines low: no second reset)
            old = port["hp"]
            old.hard_reset()
            try:
                old.close()
            except Exception as exc:               # noqa: BLE001
                log("  (the old port's close: %s)" % exc)
            end = time.perf_counter() + RESET_WAIT_S
            while time.perf_counter() < end:
                try:
                    port["hp"], port["i"] = U.HostPort(
                        a.port, base + "_serial.txt"), 0
                    port["hp"].mark("PORT REOPENED after the reset")
                    return
                except Exception:                  # noqa: BLE001
                    time.sleep(0.1)
            log("  the port did not come back within %.0f s of the reset"
                % RESET_WAIT_S)
        fresh, glines = fresh_gate(status_v2c, reset, time.perf_counter)
        hp = port["hp"]
        for s in glines:
            log(s)
        if not fresh:
            try:
                hp.close()
            except Exception:                      # noqa: BLE001
                pass
            log("12 entry 2 (witness build): VOID")
            out.close()
            return 1
    kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    veh, vlock, vhalt = [], threading.Lock(), threading.Event()

    def vrx():
        while not vhalt.is_set():
            m = kv.recv(timeout=0.05)
            if m is None or m.is_error_frame:
                continue
            h = time.perf_counter()
            with vlock:
                veh.append((m.timestamp, h, m.arbitration_id,
                            bool(m.is_extended_id), m.dlc, bytes(m.data).hex()))
    vt = threading.Thread(target=vrx, daemon=True)
    vt.start()
    ctx = mp.get_context("spawn")
    go, done = ctx.Event(), ctx.Event()
    fgo, fready = ctx.Event(), ctx.Event()
    fstop = fgo
    pf = ctx.Process(target=proc_filler, args=(
        fgo, fready, base + "_charger_rows.txt",
        LEAD_S + ID_SECONDS + TAIL_S + 2.0))
    pv = ctx.Process(target=proc_identifier, args=(
        go, done, base + "_identifier_sent.json",
        PRE_S + LEAD_S + ID_SECONDS))
    pf.start()
    pv.start()
    fready.wait(30)
    time.sleep(1.0)
    if not a.flight:
        # W before the identifier (reviewer, 2026-10-16; diag1's placement):
        # the board's record covers the whole run, so RECONCILE's v2c (since
        # boot) and completions (since W) agree on a fresh board. Sent at the
        # window open, mid-traffic, they mismatch by construction.
        hp.ser.write(b"W")
        hp.ser.flush()
        hp.mark("W SENT at the start (witness window reset)")
        time.sleep(0.3)
    go.set()                         # the identifier, alone: the pre-phase
    time.sleep(PRE_S)
    t_fgo = time.perf_counter()
    hp.mark("FILLER START")
    fgo.set()                        # the filler; the port goes normal
    time.sleep(LEAD_S)
    t_id0 = time.perf_counter()
    done.wait(PRE_S + LEAD_S + ID_SECONDS + 30)
    try:
        sent = json.load(open(base + "_identifier_sent.json"))
    except (OSError, ValueError) as exc:
        log("TEST FAULT: the identifier process left no summary (%s); "
            "no verdict" % exc)
        fstop.set()
        pf.join(LEAD_S + ID_SECONDS + TAIL_S + 30)
        pv.join(10)
        vhalt.set()
        vt.join(2.0)
        kv.shutdown()
        hp.close()
        out.close()
        return 3
    t_pre0, t_pre1, t_id1 = sent["t0"] + 0.2, t_fgo - 0.05, sent["t1"]
    time.sleep(1.0)
    dump = []
    if not a.flight:
        n0 = len(hp.stamped)
        hp.ser.write(b"w")
        hp.ser.flush()
        time.sleep(3.0)
        dump = [s for h, s in hp.stamped[n0:]]
    pf.join(LEAD_S + ID_SECONDS + TAIL_S + 30)
    pv.join(10)
    vhalt.set()
    vt.join(2.0)
    kv.shutdown()
    serial = list(hp.stamped)
    hp.close()
    with open(base + "_vehicle_rows.txt", "w") as f:
        f.write("# t_dev\tt_host\tid\text\tdlc\thex (Kvaser, normal mode)\n")
        for r in veh:
            f.write("%.6f\t%.6f\t%X\t%d\t%d\t%s\n" % r)
    chg = read_rows(base + "_charger_rows.txt", chg=True)
    log("identifier sender: %s" % json.dumps(sent))
    log("board dump (%d lines):" % len(dump))
    for s in dump[:12]:
        log("    | %s" % s)
    log("windows (host perf s): pre-phase %.3f-%.3f, filler start %.3f, run "
        "%.3f-%.3f" % (t_pre0, t_pre1, t_fgo, t_id0, t_id1))
    v, lines = judge(veh, chg, serial, dump, t_pre0, t_pre1, t_id0, t_id1,
                     flight=a.flight, t_fgo=t_fgo)
    if not a.flight:
        m = RECONCILE_RE.search("\n".join(dump))
        nv = len([r for r in veh if r[2] == ID and r[3]])
        nc = len([r for r in chg if r[2] == ID and r[3] and r[6]])
        log("PLACEMENT (witness window from before the identifier to after "
            "it): identifier frames on the vehicle wire %d; board v2c %s; "
            "board completions %s; received on the charger side %d. v2c below "
            "the wire count = the board never received them; completions "
            "below v2c = lost in the board's transmit; both equal and the "
            "charger short = the adapter missed them."
            % (nv, m.group(2) if m else "?", m.group(1) if m else "?", nc))
    for s in lines:
        log("  " + s)
    log("12 entry 2 (%s): %s" % ("flight image" if a.flight else "witness build",
                                  v))
    out.close()
    return 0 if v == "PASS" else 1


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("gate: no pre-check (run_bench never calls it)",
     "fresh, glines = fresh_gate(status_v2c, reset, time.perf_counter)",
     "fresh, glines = True, []"),
    ("gate: no refusal without a status line",
     'return False, ["VOID: no status line from the board',
     'return True, ["VOID: no status line from the board'),
    ("gate: no refusal after the reset",
     '                    or "no status line"))\n    return False, lines',
     '                    or "no status line"))\n    return True, lines'),
    ("gate: the reset never sent",
     "    t_r = now()\n    reset()\n", "    t_r = now()\n"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test under each of MUTANTS")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--flight", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rejudge", default=None, metavar="BASE",
                    help="judge a saved run (<BASE>.txt and its _rows/_serial "
                         "files) again with the current judge; --out names "
                         "the result file; --flight as the run was made")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if a.rejudge:
        if not a.out:
            ap.error("--out is required")
        return rejudge(a.rejudge, a.flight, a.out)
    if not a.out:
        ap.error("--out is required")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
