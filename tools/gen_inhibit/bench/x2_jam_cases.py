"""Step-9 cases with the X2 jammer: spec 11 bus-off, arm (ii), 12.4 threshold.

Gen-inhibit tester, 2026-10-08. Case plans A, B, C approved by the reviewer
2026-10-08. The jammer is x2_jammer/ (test equipment, NOT FOR VEHICLE) on the
X2's CAN1, which is on the WiCAN's segment; it is driven over COM3 through
x2_jammer.Console. Every time below is on ONE clock: host epoch (time.time()),
with the Kvaser's hardware timestamps, which are on that clock (median receipt
lag ~1 ms, measured each run and recorded).

  A  busoff     spec 11 bus-off half. WiCAN in mode 2 (RESPOND), no replay,
                PCAN closed; its only transmissions are its diag pages. Per arm:
                5 s baseline, TRAIN 20 200 3000, 15 s after, page ~10 Hz, 0x7F1
                off the wire. Judged per arm by tec_busoff_sweep.judge_bus_on().
                What happens after bus-off (recovering / running / staying
                bus_off until the mode change) is RECORDED, NOT JUDGED: no spec
                section governs it (reviewer 2026-10-08; with the user).
                --control adds an arm with no train: running and bus_on true
                throughout, no mismatch.
  B  livetrain  arm (ii). PCAN replays the interlock slice (normal mode), Kvaser
                witnesses (normal mode). 3 s replay with the device OFF (the
                discriminator's negative control), mode 3, wait live, 3 s
                live, TRAIN 20 200 3000, 15 s after, mode 0. Judged by
                live_jam_arm.judge() A/B/C. Recorded in order: train start,
                the PCAN's own status changes (its bus-off), the WiCAN's
                bus_off from the page, the first latched reason, the last
                0x051 on the wire, 0x051 per 100 ms. DELIVERED ERROR COUNTS ARE
                NOT USED DURING THE TRAIN: under a dense train the Kvaser
                reports error frames in bursts of 430 and goes blind for up to
                1.5 s (x2_jammer/README.md). The train also destroys the VCM's
                0x051, so which trip comes first is confounded by design.
  C  threshold  spec 12.4 error-frame row, "at the trip threshold". PCAN
                replays, Kvaser witnesses. 3 s device OFF, mode 3, wait live,
                5 s live, PULSES 20 n interval, 12 s after, mode 0. Judged by
                judge_threshold(): one error frame per pulse must be delivered
                (else INCONCLUSIVE); trip 6 must latch iff >= 10 delivered error
                frames fall within a 10 s window, and never before the 10th.
                Main arms: 10 at 1000 ms (spans 9.0 s) and 9 at 1100 ms (never
                10). --lead-s sets how long the arm is live before the pulses
                (default 5 s), for several start phases relative to arming.
                Window: spec 7 "How the window moves" (user, 2026-10-08),
                sliding and INCLUSIVE at 10.0 s. The edge itself cannot be
                tested here -- the bench cannot place two pulses exactly 10.0 s
                apart -- so it is judged on the host (spec_expect_check.py).

  py -3.14 tools/gen_inhibit/bench/x2_jam_cases.py busoff --arms 3 [--control]
  py -3.14 ... livetrain --arms 3 [--p 5000 --ms 5000]   (arm (i): a steady, slower train)
  Every case needs --power (how the WiCAN is powered; recorded as given).
  py -3.14 ... threshold --arms 3 --n 10 --interval-ms 1000
Writes runs/x2_jam_<case>_<stamp>.json (never overwrites).
"""
import argparse
import gzip
import json
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import can                                                      # noqa: E402
import lowid_flood_skip_bench as LF                             # noqa: E402
import live_jam_arm as LJ                                       # noqa: E402
import tec_busoff_sweep as TB                                   # noqa: E402
import x2_jammer as XJ                                          # noqa: E402
import gi_ecc as GE                                             # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
BITRATE = 500000
KEEP_IDS = {0x051, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9}
TRIP6_TEXT = LJ.TRIP6_TEXT


# ---------------------------------------------------------------- pure parts

def max_in_window(ts, window_s):
    """Most frames with the last no more than window_s after the first.

    Sliding window, INCLUSIVE edge: spec 7 "How the window moves" (user,
    2026-10-08). It was the strict edge, the reviewer's proposal, until the
    ruling; the main arms do not depend on the edge either way.
    Returns (count, time of the frame that first completes 10, or None).
    """
    ts = sorted(ts)
    best, t10, j = 0, None, 0
    for i in range(len(ts)):
        while ts[i] - ts[j] > window_s + 1e-9:
            j += 1
        n = i - j + 1
        if n >= 10 and t10 is None:
            t10 = ts[i]
        best = max(best, n)
    return best, t10


def resets_while_armed(samples, t_arm, t_off):
    """Times of post-state resets while armed: tx_ok falling, or mode not 3."""
    out, last = [], None
    for t, sn in samples:
        if sn is None or not (t_arm + 1.0 <= t < t_off):
            continue
        tx = sn.get("tx_ok")
        if (last is not None and tx is not None and tx < last) or sn.get("mode") not in (3, None):
            out.append(t)
        if tx is not None:
            last = tx
    return out


# Spec 7 (2026-10-20, "An error frame's time is when the device records it"): an error
# frame is recorded at the next worker pass, after a received frame or the 200 ms receive
# timeout. A trip at the 10th is therefore due within this long after its error frame.
TRIP6_RECORD_S = 0.2
# Spec 7: a test that judges from the bus keeps the span at least this far from 10 s.
TRIP6_BAND_S = 0.25


def band_refusal(n_judged, interval_ms, window_s=10.0):
    """Why these judged pulses cannot be judged from the bus, or None. Ten consecutive
    pulses span 9 intervals; spec 7 (2026-10-20) lets a burst whose bus span is within
    0.25 s of 10 s trip or not at its 10th, so a bus judge must keep clear of that band.
    Fewer than 10 judged pulses never fill a window and are not refused."""
    if n_judged < 10:
        return None
    span = round(9 * interval_ms / 1000.0, 6)
    if abs(span - window_s) < TRIP6_BAND_S:
        return ("10 pulses at %s ms span %.3f s, within %.2f s of %.0f s: spec 7 lets that trip or not "
                "at the 10th, so it cannot be judged from the bus" % (interval_ms, span, TRIP6_BAND_S, window_s))
    return None


def judge_threshold(errs, samples, ph, n_cmd, interval_s, window_s=10.0,
                    clock_slack_s=0.02):
    """Case C. errs: witness error-frame times; samples: [(t, snap)];
    ph: arm_posted, pulses_start, pulses_end, mode0_posted.

    INCONCLUSIVE: a reset while armed; not live within 1 s before the pulses;
    abort already latched before them; error frames between the arm and the
    pulses; delivery other than exactly one error frame per pulse.
    FINDING: a latch whose reason is not trip 6.
    FAIL: trip 6 latched with fewer than 10 delivered in any window, latched
      before the 10th delivered error frame, did not latch with 10 in one, or
      latched late: a page read whose request went out more than TRIP6_RECORD_S
      (plus clock_slack_s) after the 10th still showed no latch (reviewer,
      2026-10-20; the bound is 0.2 s plus the record's own poll spacing there).
    PASS otherwise.
    """
    samples = sorted(samples, key=lambda x: x[0])
    p0, p1 = ph["pulses_start"], ph["pulses_end"]
    out = {}
    r = resets_while_armed(samples, ph["arm_posted"], ph["mode0_posted"])
    if r:
        return dict(out, verdict="INCONCLUSIVE", why="device reset while armed",
                    reset_rel_pulses_s=[round(t - p0, 3) for t in r[:3]])
    pre = [(t, s) for t, s in samples if s is not None and ph["arm_posted"] < t < p0]
    if not pre or pre[-1][0] < p0 - 1.0 or not pre[-1][1].get("inhibit_live"):
        return dict(out, verdict="INCONCLUSIVE", why="not live at the start of the pulses")
    if pre[-1][1].get("abort_latched"):
        return dict(out, verdict="INCONCLUSIVE", why="abort already latched before the pulses")
    early = [t for t in errs if ph["arm_posted"] <= t < p0 - 0.3]
    if early:
        return dict(out, verdict="INCONCLUSIVE",
                    why="%d error frames between the arm and the pulses" % len(early))
    pe = sorted(t for t in errs if p0 - 0.3 <= t <= ph["mode0_posted"])
    be = [(t, (s or {}).get("drv_bus_error")) for t, s in samples
          if s is not None and t >= ph["arm_posted"]]
    pp = XJ.per_pulse(pe, n_cmd, interval_s, be)
    n_max, t10 = max_in_window(pe, window_s)
    out.update({"per_pulse": pp, "max_in_window": n_max,
                "t_10th_rel_pulses_s": None if t10 is None else round(t10 - p0, 3)})
    if not pp["exactly_one_each"]:
        return dict(out, verdict="INCONCLUSIVE",
                    why="delivery was not one error frame per pulse")
    lat = next(((t, s) for t, s in samples if t >= p0 and s is not None
                and s.get("abort_latched")), None)
    if lat is not None:
        out["latched_reason"] = lat[1].get("abort_reason")
        out["latched_seen_rel_pulses_s"] = round(lat[0] - p0, 3)
        if TRIP6_TEXT not in (lat[1].get("abort_reason") or ""):
            return dict(out, verdict="FINDING",
                        why="another trip latched: %r" % lat[1].get("abort_reason"))
    expect = n_max >= 10
    out["expect_trip6"] = expect
    if expect:
        if lat is None:
            return dict(out, verdict="FAIL",
                        why="10 delivered error frames within the window and no trip 6")
        if lat[0] < t10 - clock_slack_s:
            return dict(out, verdict="FAIL",
                        why="trip 6 latched before the 10th delivered error frame")
        out["latch_seen_after_10th_s"] = round(lat[0] - t10, 3)
        before = [(t, s) for t, s in samples if s is not None and p0 <= t < lat[0]]
        out["latch_bound_s"] = round(TRIP6_RECORD_S + (lat[0] - before[-1][0] if before else 0.0), 3)
        due = t10 + TRIP6_RECORD_S + clock_slack_s
        late = [t for t, s in before if (s.get("req_start") or t) >= due]
        if late:
            return dict(out, verdict="FAIL",
                        why="trip 6 not latched %.3f s after the 10th delivered error frame (a page read then "
                            "still showed none; spec 7: recorded within %.1f s)" % (late[0] - t10, TRIP6_RECORD_S))
        return dict(out, verdict="PASS", why="")
    if lat is not None:
        return dict(out, verdict="FAIL",
                    why="trip 6 latched with at most %d in any window" % n_max)
    return dict(out, verdict="PASS", why="")


JAMMER_MAX_MS = 30000     # the jammer refuses PULSES unless n x interval + 100 ms <= 30 s


def judged_errs(errs, p0, interval_s, judge_first):
    """The error frames judge_threshold sees when only the first judge_first pulses
    are judged (B6 investigation, reviewer 2026-10-20: 29 pulses, trip 6 judged on the
    first 10 as B6 does). Everything before pulse judge_first + 1 is kept, so the
    judge's own checks on the time before the pulses still see it; nothing from
    half an interval before pulse judge_first + 1 onward is."""
    if not judge_first:
        return list(errs)
    cut = p0 + (judge_first - 0.5) * interval_s
    return [t for t in errs if t < cut]


def threshold_refusal(a):
    """Why these threshold arguments cannot run, or None. Refused before the bench
    is touched rather than left to the jammer or to an arm."""
    if a.n * a.interval_ms + 100 > JAMMER_MAX_MS:
        return "PULSES %d x %d ms + 100 ms is over the jammer's %d ms limit" % (a.n, a.interval_ms, JAMMER_MAX_MS)
    if a.judge_first is not None and not 1 <= a.judge_first <= a.n:
        return "--judge-first %d is not between 1 and --n %d" % (a.judge_first, a.n)
    if a.ecc and not a.reset_wican:
        return ("--ecc needs --reset-wican: /gi_ecc's rings are cleared only by a reboot, "
                "so without one an arm's page holds the arms before it")
    why = band_refusal(min(a.n, a.judge_first or a.n), a.interval_ms)
    if why:
        return why
    if getattr(a, "ecc_gate", False) and not a.ecc:
        return "--ecc-gate needs --ecc: the gate reads the first arm's /gi_ecc page"
    so = getattr(a, "stop_over_counts", None)
    if so is not None and not a.ecc:
        return "--stop-over-counts needs --ecc: the over-counts are read from each arm's /gi_ecc page"
    if so is not None and so < 1:
        return "--stop-over-counts %d is below 1" % so
    return None


def ecc_gate_stop(e):
    """Why the run stops after its first arm, or None (reviewer, 2026-10-20: the B6
    session 2 liveness gate). It stops when /gi_ecc was not read or not in the format,
    or when gi_ecc.liveness says so: more than half the rows with a segment the TRM does
    not define (one distinct ecc is no longer a stop: reviewer, 2026-10-21).
    Undefined-segment rows below that, and dir disagreements, are reported in the
    summary and never stop."""
    e = e or {}
    if e.get("problems"):
        return "/gi_ecc not usable: %s" % "; ".join(e["problems"])
    g = e.get("liveness")
    if g is None:
        return "/gi_ecc gave no liveness counts"
    if g.get("stop"):
        return "liveness gate: %s" % "; ".join(g.get("why") or [])
    return None


def over_count_stop(arms, n):
    """Why the run stops after its latest arm, or None (reviewer, 2026-10-21: B6
    session 3 stops at the end of the arm where the 3rd over-count lands). n is how
    many over-count pulses this call still has to find; arms are this call's arms so
    far, and an arm's over-counts are its /gi_ecc summary's (an unread page adds none)."""
    seen = [(k, p) for k, x in enumerate(arms) for p in ((x.get("ecc") or {}).get("overcounts") or [])]
    if len(seen) < n:
        return None
    return "after arm %d: %d over-count pulse(s) reached --stop-over-counts %d (%s)" % (
        len(arms) - 1, len(seen), n, ", ".join("arm %d pulse %d" % s for s in seen))


def identity(p, want_sha):
    """(the record's build entry, refusal or None) from the /gen_inhibit page.

    Never /check_status (it carries credentials until the WiCAN's reset; user,
    2026-10). want_sha: the elf_sha256 this run must be on, or None."""
    sha = ((p or {}).get("elf_sha256") or "").lower() or None
    b = {"elf_sha256": sha, "from": "/gen_inhibit"}
    if want_sha and sha != want_sha.lower():
        return b, "the WiCAN runs elf_sha256 %s, not %s" % (sha, want_sha.lower())
    return b, None


def ecc_entry(get, wit_errs, interval_s, judge_first):
    """Read /gi_ecc after an arm: (raw page or None, summary). The summary reports;
    it judges nothing about the device (gi_ecc.py)."""
    page, txt = GE.fetch(get)
    if page is None:
        return None, {"problems": ["/gi_ecc not read: %s" % txt], "caveats": []}
    return page, GE.summary(page, wit_errs, interval_s, judge_first)


def replay_rate_device_off(frames, ph):
    """0x051/s on the wire between replay start + 0.5 s and the arm: the
    replay alone. ~100/s for one VCM stream; 200+ means stacked replays."""
    t0, t1 = ph["replay_start"] + 0.5, ph["arm_posted"]
    if t1 <= t0:
        return None
    return round(sum(1 for t, a, _ in frames if a == 0x051 and t0 <= t < t1) / (t1 - t0), 1)


def judge_b_busoff(samples, wire, dev_0x051, t_latch, margin_s=0.05):
    """(B) as the reviewer ruled 2026-10-08 for a latch followed by bus-off.

    samples: [(t, snap)]; wire: [(t, arb, hex)]; dev_0x051: times of the
    device's 0x051; t_latch: time of the first page reading with the abort
    latched.
    PASS: every page reading from the latch on shows abort_latched and not
    live, and no device 0x051 follows the latch (beyond margin_s).
    The wire-flag half: any 0x7F1 that reaches the wire after the latch must
    have bit5 (inhibit_live) clear and bit7 (abort_latched) set, else FAIL. If
    none reaches the wire and the page reads bus_off, that half is recorded as
    "not observable: controller bus-off from +x s, no frame can reach the
    wire", relative to the latch.
    """
    after = [(t, s) for t, s in samples if t >= t_latch and s is not None]
    problems = []
    if any(not s.get("abort_latched") or s.get("inhibit_live") for t, s in after
           if s.get("mode") == 3):
        problems.append("a page reading after the latch is not latched or reads live")
    late = [t for t in dev_0x051 if t > t_latch + margin_s]
    if late:
        problems.append("%d device 0x051 after the latch" % len(late))
    f7 = [(t, h) for t, a, h in wire if a == 0x7F1 and t > t_latch]
    bad = [(t, h) for t, h in f7 if (bytes.fromhex(h)[3] & 0x20) or not (bytes.fromhex(h)[3] & 0x80)]
    if bad:
        problems.append("%d 0x7F1 after the latch with bit5 set or bit7 clear" % len(bad))
    boff = next((t for t, s in after if s.get("drv_state") == "bus_off"), None)
    if f7:
        wire_half = "%d 0x7F1 after the latch, flags checked" % len(f7)
    elif boff is not None:
        wire_half = ("not observable: controller bus-off from +%.3f s, no frame can "
                     "reach the wire" % (boff - t_latch))
    else:
        wire_half = "no 0x7F1 after the latch and no bus_off reading"
        problems.append(wire_half)
    return {"verdict": "FAIL" if problems else "PASS", "problems": problems,
            "wire_half": wire_half, "device_0x051_after_latch": len(late),
            "page_readings_after_latch": len(after)}


def drv_summary(samples, j0):
    """Arm (i) record (reviewer 2026-10-12): failed transmits before and after the
    jam, the driver's worst TEC/REC and the states it showed, and every abort
    reason in the order first seen. samples: [(t, snap)]; j0: jam start (epoch).
    tx_failed_before is the last reading before j0 with the driver present;
    tx_failed_after the last reading with it present (None if never)."""
    drv = [(t, s) for t, s in samples if s and s.get("drv_present")]
    before = [s.get("drv_tx_failed") for t, s in drv if t < j0]
    after = [s.get("drv_tx_failed") for t, s in drv]
    reasons = []
    for t, s in samples:
        r = (s or {}).get("abort_reason")
        if r and r not in reasons:
            reasons.append(r)
    return {"tx_failed_before": before[-1] if before else None,
            "tx_failed_after": after[-1] if after else None,
            "max_tec": max((s.get("drv_tec") or 0) for t, s in drv) if drv else None,
            "max_rec": max((s.get("drv_rec") or 0) for t, s in drv) if drv else None,
            "states": sorted(set(str(s.get("drv_state")) for t, s in samples if s)),
            "abort_reasons_in_order": reasons}


def live_totals(arms):
    """Live arms and live seconds in a session (reviewer 2026-10-08: a reset
    RATE needs the denominator). An arm's live time runs from its first page
    reading with inhibit_live true to its last one; samples: [(t, snap)]."""
    n, total = 0, 0.0
    for a in arms:
        lv = [t for t, s in a.get("samples") or [] if s and s.get("inhibit_live")]
        if lv:
            n += 1
            total += max(lv) - min(lv)
    return {"arms": len(arms), "arms_live": n, "live_s": round(total, 1)}


def state_transitions(samples, t_ref):
    """[(t - t_ref, state, bus_on)] whenever (state, bus_on) changes."""
    out, prev = [], object()
    for t, s in sorted(samples, key=lambda x: x[0]):
        if s is None:
            continue
        k = (s.get("drv_state") if s.get("drv_present") else "drv_null", s.get("bus_on"))
        if k != prev:
            out.append((round(t - t_ref, 3), k[0], k[1]))
            prev = k
    return out


def control_ok(jb):
    """Case A's no-train arm: running only, bus_on agreeing, nothing else."""
    return (jb.get("page_mismatch_count") == 0
            and set(jb.get("page_states", {})) == {"running"})


EXTRA_FIELDS = ("disabled", "disable_reason", "self_off", "key_on", "key_fresh",
                "shutdown_suppressed", "uptime_ms", "reset_reason")


def snapx(p):
    """live_jam_arm.snap plus the disable / self-off fields. Added 2026-10-08
    after a case C arm read mode 0 with nothing latched and the page answering
    throughout, and these fields had not been captured."""
    s = LJ.snap(p)
    if s is not None:
        for k in EXTRA_FIELDS:
            s[k] = p.get(k)
    return s


# ------------------------------------------------------------- bench parts

# canlib canReadStatus flags (canlib.h). Read off the witness's own controller,
# so a silent wire can be told from a deaf witness (reviewer, 2026-10-16, item 4:
# one B5 arm at 500/s saw no frames at all after the jam).
KV_FLAGS = {0x1: "error_passive", 0x2: "bus_off", 0x4: "error_warning", 0x8: "error_active",
            0x10: "tx_pending", 0x20: "rx_pending", 0x80: "txerr", 0x100: "rxerr",
            0x200: "hw_overrun", 0x400: "sw_overrun"}
STATUS_EVERY_S = 0.1


def kvaser_flags(fl):
    """canReadStatus flags -> sorted names (unknown bits as hex)."""
    out = [n for b, n in KV_FLAGS.items() if fl & b]
    rest = fl & ~sum(KV_FLAGS)
    if rest:
        out.append("0x%X" % rest)
    return sorted(out)


def witness_state_summary(status, lo, hi):
    """The witness controller's state over [lo, hi] from its status samples
    [(t, tec, rec, overrun, flags, err_frames, overruns)]. None if not sampled
    there: an unsampled witness is not a healthy one."""
    s = [x for x in status if lo <= x[0] <= hi]
    if not s:
        return None
    names = [set(kvaser_flags(x[4])) for x in s]
    off = [x[0] for x, n in zip(s, names) if "bus_off" in n]
    passive = [x[0] for x, n in zip(s, names) if "error_passive" in n]
    return {"samples": len(s), "tec_max": max(x[1] for x in s), "rec_max": max(x[2] for x in s),
            "overrun_max": max(x[3] for x in s),
            "bus_off": bool(off), "bus_off_from": off[0] if off else None,
            "bus_off_last": off[-1] if off else None,
            "error_passive": bool(passive),
            "overruns_stat_delta": s[-1][6] - s[0][6], "err_frames_stat_delta": s[-1][5] - s[0][5],
            "flags_seen": sorted(set().union(*names))}


class Witness(threading.Thread):
    """Kvaser, NORMAL mode: KEEP_IDS frames and error frames, hw and receipt time;
    and its own controller state every STATUS_EVERY_S (status)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.bus = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
        self.stop_flag = threading.Event()
        self.frames = []     # (hw, rx, arb, hex)
        self.errs = []       # (hw, rx)
        self.status = []     # (t, tec, rec, overrun, flags, err_frames, overruns)
        self.status_error = None
        self.lock = threading.Lock()
        self._next_status = 0.0

    def _sample_status(self):
        import ctypes
        import can.interfaces.kvaser.canlib as kv
        try:
            dll = getattr(kv, "__canlib")
            h = self.bus._read_handle
            tx, rx, ov, fl = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_ulong()
            dll.canReadErrorCounters(h, ctypes.byref(tx), ctypes.byref(rx), ctypes.byref(ov))
            dll.canReadStatus(h, ctypes.byref(fl))
            st = self.bus.get_stats()
            with self.lock:
                self.status.append((time.time(), tx.value, rx.value, ov.value, fl.value,
                                    st.err_frame, st.overruns))
        except Exception as e:          # recorded, never silently skipped
            self.status_error = "%s: %s" % (type(e).__name__, e)

    def run(self):
        while not self.stop_flag.is_set():
            if time.time() >= self._next_status:
                self._next_status = time.time() + STATUS_EVERY_S
                self._sample_status()
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                rx = time.time()
                with self.lock:
                    if m.is_error_frame:
                        self.errs.append((m.timestamp, rx))
                    elif m.arbitration_id in KEEP_IDS:
                        self.frames.append((m.timestamp, rx, m.arbitration_id,
                                            bytes(m.data).hex()))
                m = self.bus.recv(timeout=0.0)

    def close(self):
        self.stop_flag.set()
        self.join(timeout=2.0)
        self.bus.shutdown()

    def clocked(self):
        """(frames [(t, arb, hex)], errs [t], clock note) on the epoch clock."""
        with self.lock:
            fr, er = list(self.frames), list(self.errs)
        lags = [rx - hw for hw, rx, _, _ in fr] + [rx - hw for hw, rx in er]
        if lags and abs(statistics.median(lags)) < 2.0:
            note = "kvaser_hw (median receipt lag %.4f s)" % statistics.median(lags)
            return ([(hw, a, h) for hw, _, a, h in fr], [hw for hw, _ in er], note)
        note = "receipt (hw not epoch)" if lags else "none"
        return ([(rx, a, h) for _, rx, a, h in fr], [rx for _, rx in er], note)


class Poller(threading.Thread):
    def __init__(self, every=0.1):
        super().__init__(daemon=True)
        self.every = every
        self.samples = []    # (t, snap or None)
        self.stop_flag = threading.Event()

    def run(self):
        # A reading is stamped when its RESPONSE arrives, and the request's
        # start is kept in it as "req_start". Until 2026-10-09 the stamp was
        # time.time() taken before the request, so a reading described state
        # from up to one request later than its stamp: case C arm
        # x2_jam_threshold_20261009T043059 arm 0 read the latch stamped 24 ms
        # before the 10th error frame and was judged "latched before the
        # 10th". Stamped at the response, a reading can only be later than
        # the state it shows, so "latched before" is never claimed early.
        while not self.stop_flag.is_set():
            self.samples.append(timed_page())
            time.sleep(self.every)


def timed_page():
    """(response time, snap) with the request start inside the snap."""
    t0 = time.time()
    sn = snapx(LF.page())
    t1 = time.time()
    if sn is not None:
        sn["req_start"] = t0
    return t1, sn


PCAN_BUSOFF = 0x10    # can.interfaces.pcan.basic.PCAN_ERROR_BUSOFF


def pcan_bus_off_time(changes):
    """First time the PCAN's status code carries the bus-off bit, or None."""
    return next((t for t, code, _ in changes
                 if isinstance(code, int) and code & PCAN_BUSOFF), None)


class PcanStatus(threading.Thread):
    """Records the PCAN's own status (code and string) whenever it changes.

    status_string() returned None on 2026-10-08 01:38 and crashed case B, so
    the numeric status() is the primary reading and the string is a label.
    """

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.changes = []    # (t, status code or error text, status string)
        self.stop_flag = threading.Event()

    def run(self):
        last = object()
        while not self.stop_flag.is_set():
            try:
                code = int(self.bus.status())
            except Exception as e:
                code = "status read failed: %s" % type(e).__name__
            if code != last:
                try:
                    label = self.bus.status_string()
                except Exception:
                    label = None
                self.changes.append((time.time(), code, label))
                last = code
            time.sleep(0.02)


def wait_live(timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = snapx(LF.page())
        if s and s.get("inhibit_live"):
            return time.time()
        time.sleep(0.2)
    return None


def train(con, w, p, ms):
    t = time.time()
    con.send("TRAIN %d %d %d" % (w, p, ms))
    ack = con.wait(lambda x: x["kind"] in ("ACK", "ERR"), 3.0, t)
    return ack


def sleep_until(t):
    while time.time() < t:
        time.sleep(0.05)


def bins_0x051(frames, t0, t1, step=0.1):
    n = int(round((t1 - t0) / step))
    out = [0] * max(n, 0)
    for t, a, _ in frames:
        if a == 0x051 and t0 <= t < t1:
            k = int((t - t0) / step)
            if 0 <= k < n:
                out[k] += 1
    return out


def arm_busoff(a, con, wit, k, with_train):
    arm = {"n": k, "train": with_train}
    LF.dev_post("/gen_inhibit_set?mode=2")
    arm["mode_posted"] = time.time()
    time.sleep(1.0)
    pol = Poller()
    pol.start()
    time.sleep(5.0)
    t_tr = time.time()
    if with_train:
        ack = train(con, a.w, a.p, a.ms)
        arm["ack"] = ack[1]["raw"] if ack else None
        t_tr = ack[0] if ack else t_tr
    arm["t_train"] = t_tr
    sleep_until(t_tr + a.ms / 1000.0 + 15.0)
    pol.stop_flag.set()
    pol.join(timeout=2.0)
    arm["mode0_posted"] = time.time()   # before the request; see arm_live
    LF.dev_post("/gen_inhibit_set?mode=0")
    time.sleep(1.0)
    arm["post_after_mode_off"] = snapx(LF.page())
    frames, _, clock = wit.clocked()
    w7f1 = [(t, h) for t, a_, h in frames if a_ == 0x7F1
            and arm["mode_posted"] <= t <= arm["mode0_posted"]]
    shaped = {"baseline": {"samples": [{"pt": t, "snap": s} for t, s in pol.samples]},
              "wire_0x7F1": w7f1}
    arm["clock"] = clock
    arm["judge_bus_on"] = TB.judge_bus_on(shaped)
    arm["transitions_rel_train_s"] = state_transitions(pol.samples, t_tr)
    if with_train:
        arm["after_train_recorded_not_judged"] = [
            x for x in arm["transitions_rel_train_s"] if x[0] >= a.ms / 1000.0]
        arm["state_at_train_end"] = next(
            (x for x in reversed(arm["transitions_rel_train_s"]) if x[0] <= a.ms / 1000.0),
            None)
    else:
        arm["control_ok"] = control_ok(arm["judge_bus_on"])
    arm["samples"] = pol.samples
    attach_console(arm, arm["mode_posted"], arm["mode0_posted"] + 2.0)
    return arm


def case_busoff(a, rec, con, wit):
    rec["arms"] = []
    plan = [True] * a.arms + ([False] if a.control else [])
    for k, tr in enumerate(plan):
        arm = arm_busoff(a, con, wit, k, tr)
        rec["arms"].append(arm)
        jb = arm["judge_bus_on"]
        print("arm %d train=%s: %s | %s | transitions %s" % (
            k, tr, jb["verdict"], jb["summary"][:120],
            arm["transitions_rel_train_s"][:8]))
        time.sleep(3.0)
    tr = [x for x in rec["arms"] if x["train"]]
    v = [x["judge_bus_on"]["verdict"] for x in tr]
    rec["verdict"] = ("FAIL" if "FAIL" in v else "PASS" if v and all(x == "PASS" for x in v)
                      else "INCONCLUSIVE")
    if a.control:
        c = [x for x in rec["arms"] if not x["train"]][0]
        rec["control_ok"] = c["control_ok"]
        if not c["control_ok"]:
            rec["verdict"] = "INCONCLUSIVE"
            rec["why"] = "control arm (no train) did not read running throughout"


class StoppableSender:
    """Wraps a bus for LF.play: send() raises once stop() is called.

    WHY: LF.play has no stop flag and runs for its `seconds`. Shutting the
    PCAN down does not end it -- PCAN-Basic addresses the channel by a global
    handle, so when the next arm re-opened PCAN_USBBUS1 the old thread's
    sends worked again and replays stacked: 100, 200, 300 0x051/s with the
    device OFF in arms 0, 1, 2 (2026-10-08 01:39-01:47). play() counts a
    failed send and carries on, so after stop() it sends nothing.
    """

    def __init__(self, bus):
        self.bus = bus
        self.stopped = threading.Event()

    def send(self, msg, timeout=None):
        if self.stopped.is_set():
            raise can.CanError("replay stopped")
        return self.bus.send(msg, timeout=timeout)

    def stop(self):
        self.stopped.set()


def replay_start(seconds=90.0, auto_reset=False):
    # auto_reset: the PCAN rejoins after its own bus-off (python-can's default is
    # off). busoff-rearm needs the replay back after the jam, so the gate could
    # go live; the other cases keep the default they ran with.
    pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE,
                 auto_reset=auto_reset)
    stats = {}
    stim = LF.build_stimulus()
    tx = StoppableSender(pc)
    th = threading.Thread(target=LF.play, args=(tx, stim, seconds),
                          kwargs={"stats": stats}, daemon=True)
    th.start()
    st = PcanStatus(pc)
    st.start()
    return pc, th, stats, st, tx


def replay_stop(pc, th, st, tx):
    tx.stop()
    th.join(timeout=2.0)
    st.stop_flag.set()
    st.join(timeout=1.0)
    try:
        pc.shutdown()
    except Exception:
        pass


CC = None   # the session's COM4 ConsoleCapture, started in main()


def start_wican_console(a, factory):
    """The COM4 ConsoleCapture, started, or None with --no-wican-console (reviewer,
    2026-10-20: no WiCAN console captures until the WiCAN's factory reset, since its
    console has printed its config). With None the reset is POST /system_reboot and
    each arm records "not captured"."""
    if getattr(a, "no_wican_console", False):
        return None
    cc = factory()
    cc.start()
    return cc


def console_totals(cc):
    """The session record's console fields, and its printed summary."""
    if cc is None:
        return {"wican_console": "off (--no-wican-console)"}, "COM4 console off"
    cc.stop_flag.set()
    cc.join(timeout=3.0)
    return ({"wican_console_events": cc.events, "wican_console_line_count": len(cc.lines),
             "wican_console_banner_lines": cc.banner_lines()},
            "COM4 lines %d, events %s" % (len(cc.lines), cc.events[:4]))


def attach_console(arm, t0, t1):
    """COM4 banner lines and port events between t0 and t1 (epoch)."""
    if CC is None:
        arm["wican_console"] = "not captured"
        return
    off = time.time() - time.perf_counter()
    arm["wican_console_banner"] = [(round(t + off - t0, 3), x) for t, x in CC.banner_lines()
                                   if t0 <= t + off <= t1]
    arm["wican_console_events"] = [(round(t + off - t0, 3), e) for t, e in CC.events
                                   if t0 <= t + off <= t1]
    arm["wican_console_lines"] = sum(1 for t, _ in CC.lines if t0 <= t + off <= t1)


def arm_live(a, con, wit, k, body):
    """Common shape for B and C: replay, device-OFF window, arm, wait live."""
    arm = {"n": k}
    ph = {}
    pc, th, stats, st, tx = replay_start()
    ph["replay_start"] = time.time()
    pol = Poller()
    pol.start()
    try:
        time.sleep(3.0)
        LF.dev_post("/gen_inhibit_set?mode=3")
        ph["arm_posted"] = time.time()
        tl = wait_live()
        arm["went_live_after_s"] = None if tl is None else round(tl - ph["arm_posted"], 2)
        if tl is not None:
            body(arm, ph, tl)
    finally:
        # Taken BEFORE the request: the poller runs concurrently, and on
        # 2026-10-08 01:39 it read mode 0 (the harness's own post) 11 ms
        # before a post-return timestamp, which the judge took for a reset.
        ph["mode0_posted"] = time.time()
        LF.dev_post("/gen_inhibit_set?mode=0")
        ph["mode0_returned"] = time.time()
        time.sleep(1.0)
        pol.stop_flag.set()
        pol.join(timeout=2.0)
        replay_stop(pc, th, st, tx)
        arm["replay_thread_alive_after_stop"] = th.is_alive()
    arm["phases"] = ph
    arm["samples"] = pol.samples
    attach_console(arm, ph["replay_start"], ph["mode0_posted"] + 2.0)
    arm["pcan_status_changes"] = st.changes
    arm["replay_stats"] = dict(stats)
    return arm


def case_livetrain(a, rec, con, wit):
    rec["delivered_error_counts_during_train"] = (
        "NOT USED: under a dense train the Kvaser reports error frames in bursts "
        "of 430 and goes blind for up to 1.5 s (x2_jammer/README.md)")
    rec["confound"] = ("the train also destroys the VCM's 0x051, so the device loses "
                       "fresh 0x051 whatever its own state; which trip latches "
                       "first is confounded by design (reviewer 2026-10-08)")
    rec["arms"] = []
    for k in range(a.arms):
        def body(arm, ph, tl):
            sleep_until(tl + 3.0)
            ack = train(con, a.w, a.p, a.ms)
            arm["ack"] = ack[1]["raw"] if ack else None
            ph["jam_start"] = ack[0] if ack else time.time()
            ph["jam_stop"] = ph["jam_start"] + a.ms / 1000.0
            sleep_until(ph["jam_stop"] + 15.0)
        arm = arm_live(a, con, wit, k, body)
        rec["arms"].append(arm)
        ph = arm["phases"]
        frames, errs, clock = wit.clocked()
        arm["clock"] = clock
        lo, hi = ph["replay_start"] - 1, ph["mode0_posted"] + 1
        arm["wire"] = [x for x in frames if lo <= x[0] <= hi]
        arm["witness_error_times"] = [t for t in errs if lo <= t <= hi]
        arm["replay_0x051_per_s_device_off"] = replay_rate_device_off(frames, ph)
        with wit.lock:
            st_all = list(wit.status)
        arm["witness_status"] = [x for x in st_all if lo <= x[0] <= hi]
        arm["witness_status_error"] = wit.status_error
        if "jam_start" in ph:
            arm["witness_state_after_jam"] = witness_state_summary(st_all, ph["jam_stop"],
                                                                   ph["mode0_posted"])
            arm["witness_state_whole_arm"] = witness_state_summary(st_all, lo, hi)
        if "jam_start" not in ph:
            arm["judge"] = {"verdict": "INCONCLUSIVE", "summary": "never went live"}
        else:
            shaped = {"phases": ph,
                      "samples": [{"pt": t, "snap": s} for t, s in arm["samples"]],
                      "wire": [x for x in frames if ph["replay_start"] - 1 <= x[0]
                               <= ph["mode0_posted"] + 1],
                      "witness_error_pts": [t for t in errs if ph["replay_start"] - 1 <= t
                                            <= ph["mode0_posted"] + 1],
                      "witness_sees_replay": True}
            j = LJ.judge(shaped)
            unused = {key: j.pop(key) for key in
                      ("witness_error_frames_in_jam", "delivered_error_rate_per_s",
                       "t_10th_error_rel_jam_s", "witness_errors_before_latch_sample")
                      if key in j}
            j["not_used_witness_blind_under_dense_train"] = unused
            arm["judge"] = j
            j0 = ph["jam_start"]
            pcan_off = pcan_bus_off_time(arm["pcan_status_changes"])
            boff = next((t for t, s in arm["samples"] if s and s.get("drv_state") == "bus_off"),
                        None)
            lat = next((t for t, s in arm["samples"] if t >= j0 and s and s.get("abort_latched")),
                       None)
            last051 = max((t for t, a_, _ in frames if a_ == 0x051
                           and t <= ph["mode0_posted"]), default=None)
            arm["ordering_rel_train_s"] = {
                "train_start": 0.0,
                "replayer_bus_off": None if pcan_off is None else round(pcan_off - j0, 3),
                "wican_bus_off_page": None if boff is None else round(boff - j0, 3),
                "first_latch_seen": None if lat is None else round(lat - j0, 3),
                "first_latch_reason": j.get("latched_reason"),
                "last_0x051_on_wire": None if last051 is None else round(last051 - j0, 3)}
            arm["pcan_status_rel_train_s"] = [(round(t - j0, 3), c, lbl)
                                              for t, c, lbl in arm["pcan_status_changes"]]
            arm["x051_per_100ms_from_train_minus_1s"] = bins_0x051(frames, j0 - 1.0,
                                                                   ph["jam_stop"] + 3.0)
            arm["drv_summary"] = drv_summary(arm["samples"], j0)
        print("arm %d (replay %s/s): %s | %s | ordering %s" % (
            k, arm.get("replay_0x051_per_s_device_off"), arm["judge"].get("verdict"), arm["judge"].get("summary", "")[:200],
            arm.get("ordering_rel_train_s")))
        ws = arm.get("witness_state_after_jam")
        print("    witness after the jam: %s; frames after jam_stop+1 s %d" % (
            "NOT SAMPLED (%s)" % arm.get("witness_status_error") if ws is None else
            "bus_off %s (from %s), error_passive %s, tec max %s, rec max %s, flags %s" % (
                ws["bus_off"], None if ws["bus_off_from"] is None else round(ws["bus_off_from"] - ph["jam_start"], 3),
                ws["error_passive"], ws["tec_max"], ws["rec_max"], ws["flags_seen"]),
            sum(1 for x in arm["wire"] if "jam_stop" in ph and x[0] > ph["jam_stop"] + 1.0)))
        time.sleep(3.0)
    v = [x["judge"].get("verdict") for x in rec["arms"]]
    rec["verdict"] = "FAIL" if "FAIL" in v else "SEE ARMS"


def case_threshold(a, rec, con, wit):
    rec["window_semantics"] = ("spec 7 (user, 2026-10-08): sliding, inclusive at "
                               "10.0 s; the edge itself is judged on the host (the "
                               "bench cannot place two pulses exactly 10.0 s apart)")
    rec["lead_s"] = a.lead_s
    rec["wican_reset_before_each_arm"] = bool(a.reset_wican)
    rec["arms"] = []
    for k in range(a.arms):
        pre_reset = reset_wican_to_off() if a.reset_wican else None
        if pre_reset is not None and not pre_reset["ok"]:
            arm = {"k": k, "wican_reset_before": pre_reset,
                   "judge": {"verdict": "INCONCLUSIVE",
                             "why": "not armed: the reset before it was not shown (%s)" % pre_reset["why"]}}
            rec["arms"].append(arm)
            print("arm %d: INCONCLUSIVE %s" % (k, arm["judge"]["why"]))
            continue

        def body(arm, ph, tl):
            arm["lead_s"] = a.lead_s
            sleep_until(tl + a.lead_s)
            t = time.time()
            con.send("PULSES %d %d %d" % (a.w, a.n, a.interval_ms))
            ack = con.wait(lambda x: x["kind"] in ("ACK", "ERR"), 3.0, t)
            arm["ack"] = ack[1]["raw"] if ack else None
            ph["pulses_start"] = ack[0] if ack else t
            done = con.wait(lambda x: x["kind"] == "DONE",
                            a.n * a.interval_ms / 1000.0 + 5.0, t)
            arm["done"] = done[1]["raw"] if done else None
            ph["pulses_end"] = done[0] if done else time.time()
            sleep_until(ph["pulses_end"] + 12.0)
        arm = arm_live(a, con, wit, k, body)
        arm["wican_reset_before"] = pre_reset
        rec["arms"].append(arm)
        ph = arm["phases"]
        frames, errs, clock = wit.clocked()
        arm["clock"] = clock
        lo, hi = ph["replay_start"] - 1, ph["mode0_posted"] + 1
        arm["wire"] = [x for x in frames if lo <= x[0] <= hi]
        arm["witness_error_times"] = [t for t in errs if lo <= t <= hi]
        arm["replay_0x051_per_s_device_off"] = replay_rate_device_off(frames, ph)
        iv = a.interval_ms / 1000.0
        jf = a.judge_first or a.n
        if "pulses_start" not in ph:
            arm["judge"] = {"verdict": "INCONCLUSIVE", "why": "never went live"}
        else:
            arm["judge"] = judge_threshold(judged_errs(errs, ph["pulses_start"], iv, a.judge_first),
                                           arm["samples"], ph, jf, iv)
            arm["err_rel_pulses_s"] = [round(t - ph["pulses_start"], 4) for t in errs
                                       if ph["arm_posted"] <= t <= ph["mode0_posted"]]
            pe = sorted(t for t in errs if ph["pulses_start"] - 0.3 <= t <= ph["mode0_posted"])
            if a.judge_first:
                be = [(t, (s or {}).get("drv_bus_error")) for t, s in arm["samples"]
                      if s is not None and t >= ph["arm_posted"]]
                arm["judged_first"] = jf
                arm["all_pulses"] = XJ.per_pulse(pe, a.n, iv, be)
            if a.ecc:
                arm["ecc_page"], arm["ecc"] = ecc_entry(LF.dev_get, pe, iv, jf)
        j = arm["judge"]
        print("arm %d (replay %s/s): %s %s | per pulse %s | bus_error deltas %s | max in window %s | latch %s %s" % (
            k, arm.get("replay_0x051_per_s_device_off"), j.get("verdict"), j.get("why"),
            (j.get("per_pulse") or {}).get("per_pulse"),
            (j.get("per_pulse") or {}).get("bus_error_delta_per_pulse"),
            j.get("max_in_window"), j.get("latched_reason"), j.get("latched_seen_rel_pulses_s")))
        if a.judge_first and arm.get("all_pulses"):
            print("  all %d pulses: per pulse %s | bus_error deltas %s" % (
                a.n, arm["all_pulses"]["per_pulse"], arm["all_pulses"]["bus_error_delta_per_pulse"]))
        if a.ecc:
            e = arm.get("ecc") or {}
            print("  /gi_ecc: problems %s | aligned %s %s | over-counts %s | caveats %d" % (
                e.get("problems"), e.get("aligned"), e.get("why", ""), e.get("overcounts"), len(e.get("caveats") or [])))
            g, dc = e.get("liveness") or {}, e.get("dir") or {}
            print("  /gi_ecc liveness: rows %s distinct ecc %s undefined seg %s stop %s | dir TX %s RX %s "
                  "not judged %s disagree %d" % (
                      g.get("rows"), g.get("distinct_ecc"), g.get("undefined_seg"), g.get("stop"), dc.get("TX"),
                      dc.get("RX"), dc.get("not judged"), len(dc.get("disagree") or [])))
        if getattr(a, "ecc_gate", False) and k == 0:
            why = ecc_gate_stop(arm.get("ecc"))
            if why:
                rec["stopped"] = "after arm 0: %s" % why
                rec["stopped_by"] = "gate"
                print("STOPPED %s" % rec["stopped"])
                break
        if getattr(a, "stop_over_counts", None) is not None:
            why = over_count_stop(rec["arms"], a.stop_over_counts)
            if why:
                rec["stopped"] = why
                rec["stopped_by"] = "over-count"
                print("STOPPED %s" % why)
                break
        time.sleep(3.0)
    if getattr(a, "stop_over_counts", None) is not None:
        rec["over_count_pulses"] = sum(len((x.get("ecc") or {}).get("overcounts") or []) for x in rec["arms"])
        print("over-count pulses in this call: %d" % rec["over_count_pulses"])
    v = [x["judge"].get("verdict") for x in rec["arms"]]
    rec["verdict"] = ("FAIL" if "FAIL" in v else "FINDING" if "FINDING" in v else
                      "PASS" if v and all(x == "PASS" for x in v) else "INCONCLUSIVE")
    if rec.get("stopped"):
        rec["verdict"] = "STOPPED"


def reset_wican_to_off():
    """Reset the WiCAN, then wait for its page in mode 0 with nothing latched.

    A trip 6 latch carries across a disarm and re-arm until a key-on (spec
    7.1), so threshold arms after the first need a cleared device; a chip
    reset clears it. Returns a record of what was done and read; `ok` says
    whether the reset is SHOWN (see reset_to_off)."""
    return reset_to_off(CC, LF.page, LF.dev_post, time.time, time.sleep)


def reset_to_off(cc, page, post, now, sleep, timeout_s=40.0):
    """The reset, with its proof. USB-JTAG through COM4 when the console is open,
    else POST /system_reboot (the WiCAN's USB has been unplugged since 2026-10-09).

    2026-10-16: this used to pulse COM4 and return whatever the page said. With
    USB unplugged there was no COM4, nothing reset, and every --reset-wican arm
    ran on a device still latched from the case before (baseline B6, voided).
    Now the reset counts only if the page comes back in mode 0 with an uptime
    BELOW the one read before it and nothing latched; otherwise ok is False and
    the caller does not arm."""
    p0 = page() or {}
    out = {"t": now(), "uptime_before_ms": p0.get("uptime_ms")}
    if cc is not None and getattr(cc, "ser", None) is not None:
        out["how"] = "usb-jtag (COM4)"
        out["pulsed"] = cc.pulse_rts(0.2)
    else:
        out["how"] = "POST /system_reboot (no COM4)"
        try:
            out["posted"] = post("/system_reboot")
        except Exception as e:
            out["posted"] = "%s: %s" % (type(e).__name__, e)
    sleep(1.0)
    t_end = now() + timeout_s
    after = None
    while now() < t_end:
        p = page()
        if (p is not None and p.get("mode") == 0 and p.get("uptime_ms") is not None
                and out["uptime_before_ms"] is not None
                and p["uptime_ms"] < out["uptime_before_ms"]):
            after = p
            break
        sleep(1.0)
    out["after"] = snapx(after) if after is not None else snapx(page())
    if out["uptime_before_ms"] is None:
        out["ok"], out["why"] = False, "no uptime before the reset, so a reset cannot be shown"
    elif after is None:
        out["ok"], out["why"] = False, "no page in mode 0 with a lower uptime within %.0f s" % timeout_s
    elif after.get("abort_latched"):
        out["ok"], out["why"] = False, "reset, but still latched: %r" % after.get("abort_reason")
    else:
        out["ok"], out["why"] = True, "reset shown: uptime %s -> %s ms, mode 0, nothing latched" % (
            out["uptime_before_ms"], after["uptime_ms"])
    return out


def case_rearm(a, rec, con, wit):
    """Dry INHIBIT arms, NO jammer command: error frames right after arming.

    Added 2026-10-08 after case B: on re-arms following a bus-off arm the
    witness saw 16 error frames ~0.12-0.18 s after mode 3, with no command
    sent to the jammer. --reset-wican resets the WiCAN through its COM4
    USB-JTAG (console_capture.pulse_rts) before the first arm, to tell a
    state that persists across arms from one cleared by a chip reset.
    """
    rec["jammer_commands"] = "none: the jammer is idle for this case"
    if a.reset_wican:
        r = reset_wican_to_off()
        rec["wican_reset"] = r
        rec["after_reset"] = r["after"]
        if not r["ok"]:
            rec["arms"] = []
            rec["verdict"] = "INCONCLUSIVE"
            rec["why"] = "not armed: the reset before the first arm was not shown (%s)" % r["why"]
            print(rec["why"])
            return
    rec["arms"] = []
    for k in range(a.arms):
        def body(arm, ph, tl):
            sleep_until(tl + 5.0)
        arm = arm_live(a, con, wit, k, body)
        rec["arms"].append(arm)
        ph = arm["phases"]
        frames, errs, clock = wit.clocked()
        ap = ph["arm_posted"]
        e = [t for t in errs if ap <= t <= ph["mode0_posted"]]
        arm["clock"] = clock
        arm["witness_error_times"] = [t for t in errs if ph["replay_start"] - 1 <= t
                                      <= ph["mode0_posted"] + 1]
        arm["err_rel_arm_s"] = [round(t - ap, 4) for t in e]
        lat = next(((t, s) for t, s in arm["samples"] if t > ap and s and s.get("abort_latched")),
                   None)
        st = [s for t, s in arm["samples"] if t > ap and s]
        arm["summary"] = {"errors_while_armed": len(e),
                          "latched": None if lat is None else (round(lat[0] - ap, 3),
                                                               lat[1].get("abort_reason")),
                          "max_tec": max((s.get("drv_tec") or 0) for s in st) if st else None,
                          "states": sorted(set(str(s.get("drv_state")) for s in st))}
        print("arm %d: live after %s s | %s" % (k, arm.get("went_live_after_s"), arm["summary"]))
        time.sleep(3.0)


# ------------------------------------------------- spec 7: bus-off and re-arm

TRAIL_S = 0.003      # the superseded trail window, still reported beside the content split
WINDOW_S = 30.0      # each observation window (reviewer, 2026-10-14)
MIN_VCM_PER_S = 50.0  # the replay is "running" in a window only above this


def split_0x051(frames, t0, t1):
    """(VCM 0x051 times, device 0x051 times) in [t0, t1), by CONTENT.

    The device's 0x051 is byte-identical to a VCM command (zero torque, the
    VCM's next counter), so each device frame puts two identical 0x051 next to
    each other on the wire, before the VCM's own copy (on time) or after it
    (late). The replayed slice never repeats a payload in adjacent 0x051 (its
    counter advances every frame). So a run of n adjacent identical 0x051 holds
    n - 1 device frames; the first n - 1 are booked as the device's.

    WHY NOT TIME (2026-10-14). The first version booked any 0x051 under
    TRAIL_S behind the previous one, the trail window e4 already marks
    SUPERSEDED. On the 08:23 e998385 run it booked 11, 87 and 25 frames per
    30 s as the device's while the page read bus_off at every poll: the slice
    itself has one 0x051 pair 1.9 ms apart per 12 s loop, and the replay
    thread bunches frames after a stall. LIMIT: a device frame whose VCM copy
    the replay lost is not seen (it reads as a normal counter step)."""
    fr = sorted((t, h) for t, a_, h in frames if a_ == 0x051)
    vcm, dev = [], []
    for i, (t, h) in enumerate(fr):
        mine = i + 1 < len(fr) and fr[i + 1][1] == h
        if t0 <= t < t1:
            (dev if mine else vcm).append(t)
    return vcm, dev


def trail_0x051(frames, t0, t1):
    """The superseded count: 0x051 under TRAIL_S behind the previous 0x051 in
    [t0, t1). Reported only, never judged (see split_0x051)."""
    ts = sorted(t for t, a_, _ in frames if a_ == 0x051)
    return sum(1 for p, t in zip(ts, ts[1:]) if t0 <= t < t1 and t - p < TRAIL_S)


def save_witness(path, frames, errs, clock):
    """Every kept witness frame and error frame, so a run can be judged again."""
    with gzip.open(path, "wt", encoding="ascii") as fh:
        json.dump({"clock": clock, "frames": frames, "errs": errs}, fh)
    return path


def window_states(samples, t0, t1):
    """The answered page readings in [t0, t1): [(t, drv_state, bus_on, inhibit_live)]."""
    return [(t, s.get("drv_state"), s.get("bus_on"), s.get("inhibit_live"))
            for t, s in samples if t0 <= t < t1 and s]


def judge_busoff_rearm(samples, frames, ph, reply_status, expect):
    """Spec 7, "A bus-off ends the device's part until a re-arm or a reboot"
    (2026-10-14, user). Phases (epoch): jam_start, jam_stop, rearm_posted,
    rearm_end. expect: "stays" (the build before the change: a direct re-arm
    leaves the controller bus_off) or "reinstalls" (the change: it runs again).

    Preconditions, each INCONCLUSIVE if unmet: the page read bus_off after the
    jam; each window has >= 20 answered readings; the replay runs (>= 50 VCM
    0x051/s) in the window after the re-arm, so the gate could go live.
    No-arm window [jam_stop, rearm_posted): any reading other than bus_off is
    an automatic recovery, which the spec rules out in both halves."""
    out = {"expect": expect, "reply_status": reply_status}
    boff = next((t for t, st, _, _ in window_states(samples, ph["jam_start"], ph["rearm_posted"])
                 if st == "bus_off"), None)
    out["bus_off_page_rel_jam_s"] = None if boff is None else round(boff - ph["jam_start"], 3)
    noarm = window_states(samples, ph["jam_stop"], ph["rearm_posted"])
    after = window_states(samples, ph["rearm_posted"], ph["rearm_end"])
    vcm_a, dev_a = split_0x051(frames, ph["rearm_posted"], ph["rearm_end"])
    _, dev_n = split_0x051(frames, ph["jam_stop"], ph["rearm_posted"])
    span = ph["rearm_end"] - ph["rearm_posted"]
    out.update(noarm_readings=len(noarm), after_readings=len(after),
               noarm_states=sorted(set(str(x[1]) for x in noarm)),
               after_states=sorted(set(str(x[1]) for x in after)),
               vcm_0x051_per_s_after=round(len(vcm_a) / span, 1) if span > 0 else None,
               device_0x051_noarm=len(dev_n), device_0x051_after=len(dev_a),
               trail_0x051_noarm=trail_0x051(frames, ph["jam_stop"], ph["rearm_posted"]),
               trail_0x051_after=trail_0x051(frames, ph["rearm_posted"], ph["rearm_end"]))
    # A reboot at the re-arm: uptime falls below what it was before (reviewer,
    # 2026-10-14: an install failure aborts in can.c). Checked before the reply,
    # which a reboot leaves missing.
    up_before = [s.get("uptime_ms") for t, s in samples
                 if s and t < ph["rearm_posted"] and s.get("uptime_ms") is not None]
    up_after = [(t, s.get("uptime_ms")) for t, s in samples
                if s and ph["rearm_posted"] <= t < ph["rearm_end"] and s.get("uptime_ms") is not None]
    drop = next((t for t, u in up_after if up_before and u < max(up_before)), None)
    out["reboot_after_rearm_rel_s"] = None if drop is None else round(drop - ph["rearm_posted"], 3)
    last_after = next((s for t, s in reversed(samples)
                       if s and ph["rearm_posted"] <= t < ph["rearm_end"]), None) or {}
    out["after_mode_self_off"] = [last_after.get("mode"), last_after.get("self_off")]
    run_after = next((x for x in after if x[1] == "running" and x[2] is True), None)
    live_after = next((x for x in after if x[3]), None)
    out["running_after_rel_rearm_s"] = None if run_after is None else round(run_after[0] - ph["rearm_posted"], 3)
    out["live_after_rel_rearm_s"] = None if live_after is None else round(live_after[0] - ph["rearm_posted"], 3)
    out["first_device_0x051_after_rel_rearm_s"] = (round(dev_a[0] - ph["rearm_posted"], 3)
                                                   if dev_a else None)
    if boff is None:
        out["verdict"] = "INCONCLUSIVE: the page never read bus_off after the jam"
    elif len(noarm) < 20 or len(after) < 20:
        out["verdict"] = "INCONCLUSIVE: too few answered readings (%d no-arm, %d after)" % (len(noarm), len(after))
    elif any(x[1] != "bus_off" for x in noarm) or dev_n:
        out["verdict"] = ("NOT AS SPECIFIED: recovered with no arm (states %s, device 0x051 %d)"
                          % (out["noarm_states"], len(dev_n)))
    elif out["vcm_0x051_per_s_after"] is None or out["vcm_0x051_per_s_after"] < MIN_VCM_PER_S:
        out["verdict"] = ("INCONCLUSIVE: the replay was not running after the re-arm (%s VCM 0x051/s)"
                          % out["vcm_0x051_per_s_after"])
    elif drop is not None:
        out["verdict"] = ("NOT AS SPECIFIED: the device rebooted at the re-arm (uptime fell, %.1f s "
                          "after it; an install failure)" % (drop - ph["rearm_posted"]))
    elif reply_status == 400:
        out["verdict"] = ("NOT AS SPECIFIED: 400 'cannot arm' -- the specified refusal when the "
                          "controller is not running after the reinstall (page after: mode %s, "
                          "self_off %r)" % tuple(out["after_mode_self_off"]))
    elif reply_status != 200:
        out["verdict"] = "NOT AS SPECIFIED: the re-arm answered %s, not 200" % reply_status
    elif expect == "stays":
        if run_after is None and not dev_a and all(x[1] == "bus_off" for x in after):
            out["verdict"] = "FAILS AS EXPECTED: 200, still bus_off for %.0f s, no device 0x051" % span
        else:
            out["verdict"] = ("NOT AS EXPECTED: the old build came back (states %s, device 0x051 %d)"
                              % (out["after_states"], len(dev_a)))
    else:
        if run_after is not None and live_after is not None and dev_a:
            out["verdict"] = "AS SPECIFIED: running and bus_on, live, device 0x051 on the wire"
        elif run_after is not None:
            out["verdict"] = ("SEE ARM: running and bus_on after the re-arm, but not live "
                              "(live %s, device 0x051 %d); the gate's reason is in the readings"
                              % (live_after is not None, len(dev_a)))
        else:
            out["verdict"] = "NOT AS SPECIFIED: still %s after the re-arm" % out["after_states"]
    return out


LATCH_FIELDS = ("mode", "inhibit_live", "abort_latched", "abort_reason", "arm_block", "bus_on",
                "self_off", "err_burst", "err_n", "uptime_ms")


def latch_fields(p):
    """The page's arm/abort/latch fields and the driver state, with the read time."""
    if p is None:
        return {"t": time.time(), "answered": False}
    out = {k: p.get(k) for k in LATCH_FIELDS}
    out["drv_state"] = (p.get("drv") or {}).get("state")
    out["t"] = time.time()
    return out


def restore_controller():
    """mode 0, then an arm, until the page reads running and bus_on; then mode 0.
    A reboot if that fails. Returns what was done and read (reviewer step 4)."""
    out = {"t": time.time(), "method": "mode 0 then arm"}
    LF.dev_post("/gen_inhibit_set?mode=0")
    time.sleep(1.0)
    LF.dev_post("/gen_inhibit_set?mode=3")
    t_end = time.time() + 10.0
    ok = None
    while time.time() < t_end:
        s = snapx(LF.page())
        if s and s.get("drv_state") == "running" and s.get("bus_on") is True:
            ok = s
            break
        time.sleep(0.5)
    LF.dev_post("/gen_inhibit_set?mode=0")
    out["running_after_arm"] = ok
    if ok is None:
        out["method"] = "reboot (mode 0 then arm did not bring the controller back)"
        p0 = LF.page() or {}
        out["uptime_before_ms"] = p0.get("uptime_ms")
        try:
            LF.dev_post("/system_reboot")
        except Exception as e:
            out["reboot_post"] = "%s: %s" % (type(e).__name__, e)
        t_end = time.time() + 40.0
        time.sleep(3.0)
        while time.time() < t_end:
            p = LF.page()
            if p is not None and p.get("uptime_ms") is not None and (
                    out["uptime_before_ms"] is None or p["uptime_ms"] < out["uptime_before_ms"]):
                out["after_reboot"] = {"uptime_ms": p.get("uptime_ms"),
                                       "reset_reason": p.get("reset_reason"), "mode": p.get("mode")}
                break
            time.sleep(1.0)
    return out


def case_busoff_rearm(a, rec, con, wit):
    """Spec 7 re-arm after bus-off (reviewer's method, 2026-10-14), per arm:
    replay (PCAN, auto_reset on, so it resumes after the jam), mode 3, wait live,
    TRAIN to bus-off, stop; 30 s with no arm; POST mode=3 directly, the reply
    kept whole (raw_http_probe); 30 s; restore (mode 0 then arm, or a reboot)."""
    import raw_http_probe as RHP
    if a.expect is None:
        raise SystemExit("busoff-rearm needs --expect stays|reinstalls")
    rec["expect"] = a.expect
    rec["replay_source"] = "PCAN: lowid_flood_skip_bench.build_stimulus (the interlock slice), auto_reset on"
    rec["x2_state_rule6"] = "jammer"
    rec["arms"] = []
    for k in range(a.arms):
        arm, ph = {"n": k}, {}
        pc, th, stats, st, tx = replay_start(seconds=150.0, auto_reset=True)
        ph["replay_start"] = time.time()
        pol = Poller(every=1.0)
        pol.start()
        try:
            time.sleep(3.0)
            LF.dev_post("/gen_inhibit_set?mode=3")
            ph["arm_posted"] = time.time()
            tl = wait_live()
            arm["went_live_after_s"] = None if tl is None else round(tl - ph["arm_posted"], 2)
            if tl is not None:
                sleep_until(tl + 3.0)
                ack = train(con, a.w, a.p, a.ms)
                arm["ack"] = ack[1]["raw"] if ack else None
                ph["jam_start"] = ack[0] if ack else time.time()
                ph["jam_stop"] = ph["jam_start"] + a.ms / 1000.0
                sleep_until(ph["jam_stop"] + WINDOW_S)
                # The abort/latch fields either side of the re-arm (reviewer,
                # 2026-10-14): a device that reinstalls but does not go live is
                # then told apart -- a trip still latched, or a controller off the bus.
                arm["page_before_rearm"] = latch_fields(LF.page())
                ph["rearm_posted"] = time.time()
                r = RHP.raw_request(RHP_HOST, "/gen_inhibit_set?mode=3", timeout=10.0, method="POST")
                arm["rearm_reply"] = r
                arm["page_after_rearm"] = latch_fields(LF.page())
                sleep_until(ph["rearm_posted"] + WINDOW_S)
                ph["rearm_end"] = time.time()
                arm["restore"] = restore_controller()
        finally:
            ph["mode0_posted"] = time.time()
            LF.dev_post("/gen_inhibit_set?mode=0")
            time.sleep(1.0)
            pol.stop_flag.set()
            pol.join(timeout=3.0)
            replay_stop(pc, th, st, tx)
        arm["phases"] = ph
        arm["samples"] = pol.samples
        arm["pcan_status_changes"] = st.changes
        arm["replay_stats"] = dict(stats)
        frames, errs, clock = wit.clocked()
        arm["clock"] = clock
        if "rearm_end" in ph:
            st_line = (arm["rearm_reply"].get("status_line") or "").split()
            status = int(st_line[1]) if len(st_line) > 1 and st_line[1].isdigit() else None
            arm["judge"] = judge_busoff_rearm(pol.samples, frames, ph, status, a.expect)
            arm["x051_per_s"] = {name: round(len(split_0x051(frames, t0, t1)[0]) / (t1 - t0), 1)
                                 for name, t0, t1 in (("before_jam", ph["arm_posted"], ph["jam_start"]),
                                                      ("noarm", ph["jam_stop"], ph["rearm_posted"]),
                                                      ("after", ph["rearm_posted"], ph["rearm_end"]))}
        else:
            arm["judge"] = {"verdict": "INCONCLUSIVE: never went live before the jam"}
        arm["witness_error_count"] = sum(1 for t in errs if ph["replay_start"] - 1 <= t <= ph["mode0_posted"] + 1)
        rec["arms"].append(arm)
        print("arm %d: %s | restore %s" % (k, arm["judge"]["verdict"],
                                           (arm.get("restore") or {}).get("method")))
        time.sleep(3.0)
    v = [x["judge"]["verdict"] for x in rec["arms"]]
    rec["verdict"] = ("NOT AS SPECIFIED" if any(x.startswith("NOT") for x in v)
                      else "INCONCLUSIVE" if any(x.startswith("INCONCLUSIVE") for x in v)
                      else v[0].split(":")[0] if len(set(x.split(":")[0] for x in v)) == 1 else "SEE ARMS")


RHP_HOST = "192.168.80.1"

CASES = {"busoff": case_busoff, "livetrain": case_livetrain, "threshold": case_threshold,
         "rearm": case_rearm, "busoff-rearm": case_busoff_rearm}


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", choices=sorted(CASES))
    ap.add_argument("--power", required=True,
                    help="how the WiCAN is powered, recorded as given (since 2026-10-12; "
                         "it was a fixed 'USB only (PC)', wrong once USB was unplugged)")
    ap.add_argument("--arms", type=int, default=3)
    ap.add_argument("--control", action="store_true")
    ap.add_argument("--w", type=int, default=20)
    ap.add_argument("--p", type=int, default=200)
    ap.add_argument("--ms", type=int, default=3000)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--interval-ms", type=int, default=1000)
    ap.add_argument("--lead-s", type=float, default=5.0,
                    help="threshold: seconds live before the pulses start")
    ap.add_argument("--reset-wican", action="store_true",
                    help="rearm: one USB-JTAG reset before the first arm; threshold: "
                         "one before EVERY arm, since a trip 6 latch survives re-arming")
    ap.add_argument("--judge-first", type=int, default=None,
                    help="threshold: judge trip 6 on the first N pulses only (B6 investigation: "
                         "--n 29 --judge-first 10); every pulse is still recorded")
    ap.add_argument("--ecc", action="store_true",
                    help="threshold: read /gi_ecc (the _ecc measurement build) after each arm; "
                         "needs --reset-wican")
    ap.add_argument("--ecc-gate", action="store_true",
                    help="threshold: stop after the first arm if its /gi_ecc page fails the liveness "
                         "gate (gi_ecc.liveness); exit 3. Needs --ecc")
    ap.add_argument("--stop-over-counts", type=int, default=None,
                    help="threshold: stop at the end of the arm where this call's over-count pulses "
                         "(from each arm's /gi_ecc summary) reach N; exit 4. Needs --ecc")
    ap.add_argument("--want-sha", default=None,
                    help="refuse unless the WiCAN's /gen_inhibit elf_sha256 is this")
    ap.add_argument("--no-wican-console", action="store_true",
                    help="no COM4 console capture of the WiCAN; resets go by POST /system_reboot")
    ap.add_argument("--expect", choices=("stays", "reinstalls"), default=None,
                    help="busoff-rearm: 'stays' on the build before the spec 7 change "
                         "(a direct re-arm leaves the controller bus_off), 'reinstalls' after it")
    return ap


def main():
    a = build_parser().parse_args()
    if a.case == "threshold":
        why = threshold_refusal(a)
        if why:
            print("REFUSED: %s" % why)
            return 2
    p = LF.page()
    if p is None or p.get("mode") != 0:
        print("FAIL: device must be in mode 0")
        return 1
    build, why = identity(p, a.want_sha)
    if why:
        print("REFUSED: %s" % why)
        return 2
    rec = {"when": time.strftime("%Y%m%dT%H%M%S"), "case": a.case, "args": vars(a),
           "build": build, "power": a.power,
           "jammer": "X2 CAN1, x2_jammer bb20e4a37b64 / md5 a89ece8c...",
           "witness": "Kvaser, normal mode"}
    global CC
    from console_capture import ConsoleCapture
    CC = start_wican_console(a, ConsoleCapture)      # COM4, every live arm (reviewer 2026-10-08)
    con = XJ.Console("COM3")
    con.start()
    wit = None
    time.sleep(0.5)
    try:
        wit = Witness()
        wit.start()
        time.sleep(1.0)
        CASES[a.case](a, rec, con, wit)
    finally:
        try:
            con.send("STOP")
            time.sleep(0.3)
        except Exception:
            pass
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        rec["console"] = XJ._console_dump(con)
        con.stop_flag.set()
        con.join(timeout=2.0)
        if wit is not None:
            frames, errs, clock = wit.clocked()
            rec["witness_clock"] = clock
            rec["witness_err_total"] = len(errs)
            rec["witness_frames_kept"] = len(frames)
            os.makedirs(RUNS, exist_ok=True)
            rec["witness_file"] = os.path.basename(save_witness(
                os.path.join(RUNS, "x2_jam_%s_%s_witness.json.gz" % (a.case, rec["when"])),
                frames, errs, clock))
            wit.close()
        rec["post"] = snapx(LF.page())
        rec["live_totals"] = live_totals(rec.get("arms") or [])
        fields, line = console_totals(CC)
        rec.update(fields)
        print("live totals %s; %s" % (rec["live_totals"], line))
        os.makedirs(RUNS, exist_ok=True)
        path = os.path.join(RUNS, "x2_jam_%s_%s.json" % (a.case, rec["when"]))
        with open(path, "x", encoding="ascii", errors="replace") as fh:
            json.dump(rec, fh, indent=1)
        print("verdict %s %s" % (rec.get("verdict"), rec.get("why", "")))
        print("wrote", path)
    # 3: the --ecc-gate stopped the run after its first arm; 4: --stop-over-counts stopped
    # it after the arm that reached N (b6_ecc_run.sh stops the session on either).
    return 4 if rec.get("stopped_by") == "over-count" else 3 if rec.get("stopped") else 0


if __name__ == "__main__":
    sys.exit(main())
