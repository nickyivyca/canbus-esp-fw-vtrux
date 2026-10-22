#!/usr/bin/env python3
"""Judges for the spec 13 "an existing harness lacks the case" bench rows.

Gen-inhibit tester, 2026-10-15. Pure: no hardware, no clock. The bench half is
interlock_bench.py; the cases and what each must show are its CASES table.
Readings for each case: the reviewer, 2026-10-15 (after the user asked for the
outstanding list).

EVERY VERDICT COMES FROM THE WIRE. The witness (Kvaser) record is decoded with
the DBCs: the device's state from its diag pages (vtrux-wican-diag.dbc: 0x7F1
flags and disable reason, 0x7F8 abort reason, arm block and flags2, 0x7F2
tx_fail), the stimulus from the truck DBCs (0x411 SoC, 0x440 contactors, 0x592
key, 0x639 shift). The status page is recorded beside it and not judged.

WHICH 0x051 IS THE DEVICE'S: by content (as x2_jam_cases.split_0x051). In
INHIBIT the device's frame is byte-identical to the VCM's next command, and a
stimulus never repeats a payload in adjacent 0x051 -- which the bench checks on
the stimulus before it runs (interlock_bench.stimulus_premise). A device frame
"answers" the last VCM frame before it.

A SHUTDOWN EPISODE (0x051 B0 = 0x10, spec 6.3) is judged differently: the
device holds B0 at 0x08 (spec 4.1), so a device frame there would not copy the
next command, and the content rule would miss it. Inside an episode every 0x051
whose B0 is not 0x10 is the device's.

THE EVIDENCE-RULE MODEL (predict_trip). Spec 7 trips 2, 3 and 5 and the
evidence rule, applied to what the witness saw: each tracked signal goes stale
at its last frame plus its window (0.5 s; 0x617 1.0 s), and trips on the first
0x051 after that. The GENE family stale together is trip 3; 0x054 alone with
0x471 fresh is trip 5. A VCM frame within EDGE_S of an expiry could fall either
side of it on the device, so the model returns every reason that can come
first, and the device's reason must be one of them. The 0x051 that is the
evidence may itself be answered; every one after it must not be (spec 7, "A
single signal lost on a live bus still trips"; user ruling 2026-10-16, after
B20 showed seven cases answering exactly that frame).
"""

import bisect
import os
import sys

import cantools
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))


def project_root():
    """The reverse-it root: five levels up from here, or -- for a copy run from a
    scratch dir, as the mutant runner does -- the sys.path entry that holds it."""
    for cand in [gi_paths.reverse_it_dir()] + list(sys.path):
        if os.path.isfile(os.path.join(cand, "projects", "vtrux", "vtrux-wican-diag.dbc")):
            return os.path.normpath(cand)
    raise RuntimeError("cannot find the project root (projects/vtrux/*.dbc)")


DBC_DIR = os.path.join(project_root(), "projects", "vtrux")

WINDOW_S = {0x411: 0.5, 0x440: 0.5, 0x617: 1.0, 0x639: 0.5, 0x054: 0.5, 0x471: 0.5}
CMD_WINDOW_S = 0.5
ONCE_HEARD = (0x054, 0x471)
STALE_REASON = {0x411: "stale_soc", 0x440: "stale_contactor", 0x617: "stale_fault",
                0x639: "stale_shift"}
EDGE_S = 0.002          # device and witness receive the same frame within this
PAGE_LAG_S = 1.1        # a 1 Hz diag page built after an event is on the wire by then
CLOSED = (11, 12)       # bcm_mainc_stat closed states, spec 6.2
SOC_MIN_RAW = 2100      # 21.00 %, spec 6.2: below it releases
DEBOUNCE_N = 5          # spec 6.2: 5 consecutive valid readings

_DB = {}


def db(name):
    if name not in _DB:
        _DB[name] = cantools.database.load_file(os.path.join(DBC_DIR, name), strict=False)
    return _DB[name]


def decode(dbc, ident, hexs):
    """Raw (unscaled) signal values, or None if the frame does not decode."""
    try:
        return db(dbc).decode_message(ident, bytes.fromhex(hexs), scaling=False,
                                      decode_choices=False, allow_truncated=True)
    except Exception:
        return None


def choice(dbc, ident, sig, v):
    m = db(dbc).get_message_by_frame_id(ident)
    c = m.get_signal_by_name(sig).choices or {}
    return str(c.get(v, v))


DIAG = "vtrux-wican-diag.dbc"
PT = "epri-pt-bus.dbc"
PTX = "vtrux-powertrain-experimental.dbc"


# ------------------------------------------------------------------ the record

TWIN_SCAN_S = 0.05         # how far after a 0x051 to look for its identical twin
UNIQUE_S = 0.02            # a send whose payload appears once within this is unambiguous


def attribute_by_sender(frames, sent, cmd=0x051):
    """Which 0x051 are the sender's and which the device's, from the sender's own
    log (reviewer, 2026-10-16; replaces the content rule when a log is given).

    frames: the witness [(t, id, hex)]; sent: the sender's [(t, id, hex, ok)] in
    send order, on the same host clock. The walk is provenance_classifier's
    (e4): the sender's frames reach the wire in send order, so the wire is walked
    with a pointer into the sends, and a wire frame is the sender's only when it
    is the next unconsumed send; any other 0x051 is the device's.

    THE TRAP THAT RULE ALONE FALLS INTO. The device's answer to command N is
    byte-identical to the next command N+1, and lands ~0.26 ms after N, so the
    wire reads N, answer, N+1 and BOTH of the last two equal send N+1. When an
    identical 0x051 follows before the next send's frame, the two are told apart
    by time: the one nearer send N+1's time plus the sender-to-wire offset is
    the sender's. The offset and its residual come from the sends whose payload
    appears exactly once within UNIQUE_S. If the two candidates are not separated
    by more than that residual, the pair is AMBIGUOUS: recorded, never guessed
    (the run is then UNRESOLVED by the caller). On a dense replay (rekey-long)
    other ids sit between consecutive commands, so the pointer alone separates
    them and no pair arises.

    Returns (vcm [(t, hex)], dev [(t, hex)], info)."""
    wire = sorted(frames, key=lambda x: x[0])
    src = [(t, i, h) for t, i, h, ok in sent if ok]
    # Offset and residual, from unambiguous command sends.
    wt = [(t, h) for t, i, h in wire if i == cmd]
    wtt = [t for t, _ in wt]
    deltas = []
    for s, i, h in src:
        if i != cmd:
            continue
        lo = bisect.bisect_left(wtt, s - UNIQUE_S)
        hi = bisect.bisect_right(wtt, s + UNIQUE_S)
        c = [wtt[k] - s for k in range(lo, hi) if wt[k][1] == h]
        if len(c) == 1:
            deltas.append(c[0])
    deltas.sort()
    off = deltas[len(deltas) // 2] if deltas else 0.0
    tol = max(abs(d - off) for d in deltas) if deltas else None
    info = {"offset_s": round(off, 6), "residual_max_s": None if tol is None else round(tol, 6),
            "unique_matches": len(deltas), "ambiguous": [], "pairs_by_time": 0,
            "sends": len(src), "sends_consumed": 0}
    vcm, dev = [], []
    used = set()
    p = 0
    for j, (t, i, h) in enumerate(wire):
        if j in used:
            continue
        if p < len(src) and (i, h) == (src[p][1], src[p][2]):
            pick = j
            if i == cmd:
                nxt = src[p + 1] if p + 1 < len(src) else None
                twin = None
                for k in range(j + 1, len(wire)):
                    tk, ik, hk = wire[k]
                    if tk - t > TWIN_SCAN_S or (nxt is not None and (ik, hk) == (nxt[1], nxt[2])):
                        break
                    if ik == cmd and hk == h and k not in used:
                        twin = k
                        break
                if twin is not None:
                    exp = src[p][0] + off
                    da, db = abs(t - exp), abs(wire[twin][0] - exp)
                    if tol is None or abs(da - db) <= tol:
                        info["ambiguous"].append({"send_t": round(src[p][0], 6), "data": h,
                                                  "wire": [round(t, 6), round(wire[twin][0], 6)]})
                    info["pairs_by_time"] += 1
                    pick = j if da < db else twin
                    other = twin if pick == j else j
                    dev.append((wire[other][0], h))
                    used.add(twin)
                vcm.append((wire[pick][0], h))
            p += 1
            info["sends_consumed"] = p
            continue
        if i == cmd:
            dev.append((t, h))
    vcm.sort()
    dev.sort()
    return vcm, dev, info


class Ctx:
    """One run's witness record, split and decoded once.

    frames: [(t, id, hex)] on one clock; posts: [{"mode", "t_ret", ...}].
    sent: the sender's log [(t, id, hex, ok)]; given, 0x051 is attributed by it
    (attribute_by_sender), else by the content rule above.
    """

    def __init__(self, frames, posts=None, samples=None, alias=None, sent=None):
        self.frames = sorted(frames, key=lambda x: x[0])
        # alias: {injected 0x051 hex: the payload it replaced}. The device's
        # answer to the frame before an injected one copies the command the VCM
        # would have sent, i.e. the original; without this the content rule
        # books that device frame as a VCM frame.
        alias = alias or {}
        self.posts = posts or []
        self.samples = samples or []
        self.by_id = {}
        for t, i, h in self.frames:
            self.by_id.setdefault(i, []).append((t, h))
        c = self.by_id.get(0x051, [])
        self.attribution = None
        if sent is not None:
            self.vcm, self.dev, self.attribution = attribute_by_sender(self.frames, sent)
        else:
            self.vcm, self.dev = [], []
            for k, (t, h) in enumerate(c):
                mine = k + 1 < len(c) and alias.get(c[k + 1][1], c[k + 1][1]) == h
                (self.dev if mine else self.vcm).append((t, h))
        self.vcm_t = [t for t, _ in self.vcm]
        self.p7f1 = self._pages(0x7F1)
        self.p7f8 = self._pages(0x7F8)
        self.p7f2 = self._pages(0x7F2)

    def _pages(self, ident):
        out = []
        for t, h in self.by_id.get(ident, []):
            d = decode(DIAG, ident, h)
            if d is not None:
                out.append((t, d))
        return out

    def times(self, ident):
        return [t for t, _ in self.by_id.get(ident, [])]

    def answered(self, td):
        """The VCM frame a device frame at td answers: the last one before it."""
        k = bisect.bisect_left(self.vcm_t, td) - 1
        return self.vcm_t[k] if k >= 0 else None

    def dev_answering(self, lo, hi):
        """Device frames answering a VCM frame at lo <= t < hi."""
        out = []
        for t, _ in self.dev:
            a = self.answered(t)
            if a is not None and lo <= a < hi:
                out.append(t)
        return out

    def vcm_after(self, t):
        k = bisect.bisect_right(self.vcm_t, t)
        return self.vcm_t[k] if k < len(self.vcm_t) else None

    def series(self, ident, dbc, sig):
        out = []
        for t, h in self.by_id.get(ident, []):
            d = decode(dbc, ident, h)
            if d is not None and sig in d:
                out.append((t, int(d[sig])))
        return out

    def post_times(self, mode):
        return [p["t_ret"] for p in self.posts if p.get("mode") == mode and p.get("t_ret") is not None]

    def first_sent(self, mode):
        """When the first POST of this mode was sent: the earliest its mode change
        can take effect (it takes effect somewhere before the POST returns). A
        "never live before X" window starts here, since starting it at the return
        drops an X that came while the POST was in flight. The 2026-10-15 build
        trial had one: a POST slowed by a build returned 1 ms after the first
        closed 0x440, and a correct device was counted live before the close."""
        for p in self.posts:
            if p.get("mode") == mode and p.get("t_ret") is not None:
                return p.get("t_sent", p["t_ret"])
        return None


def flag(d, bit, sig="diag_flags"):
    return bool((int(d.get(sig, 0)) >> bit) & 1)


def abort_name(d):
    return choice(DIAG, 0x7F8, "diag_abort_reason", int(d["diag_abort_reason"]))


def block_name(d):
    return choice(DIAG, 0x7F8, "diag_arm_block", int(d["diag_arm_block"]))


def disable_name(d):
    return choice(DIAG, 0x7F1, "diag_disable_reason", int(d["diag_disable_reason"]))


# ----------------------------------------------------------------- the verdict

class Verdict:
    def __init__(self):
        self.pre = []        # (name, ok, detail): the case tested what it says
        self.checks = []     # (name, ok, detail): spec requirements
        self.facts = {}

    def need(self, name, ok, detail=""):
        self.pre.append((name, bool(ok), detail))

    def check(self, name, ok, detail=""):
        self.checks.append((name, bool(ok), detail))

    def result(self):
        if any(not ok for _, ok, _ in self.pre):
            v = "INCONCLUSIVE"
        elif any(not ok for _, ok, _ in self.checks):
            v = "NOT AS SPECIFIED"
        else:
            v = "AS SPECIFIED"
        return {"verdict": v,
                "preconditions": [{"name": n, "ok": o, "detail": d} for n, o, d in self.pre],
                "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in self.checks],
                "facts": self.facts}


def r3(x):
    return None if x is None else round(x, 4)


# ------------------------------------------------------------- common pieces

def live_before(c, v, t, span=0.5, label="live before the event"):
    """Precondition: the device answered VCM frames in the span before t."""
    n = len(c.dev_answering(t - span, t))
    v.need(label, n > 0, "%d device 0x051 answering VCM frames in the %.1f s before" % (n, span))
    return n


def silent_from(c, v, t, name, t_end=None):
    """Check: no device frame answers a VCM frame at or after t."""
    bad = c.dev_answering(t, t_end if t_end is not None else float("inf"))
    v.check(name, not bad, "%d device 0x051 answering VCM frames from then on%s"
            % (len(bad), "" if not bad else ", first %+.4f s" % (bad[0] - t)))
    return bad


def pages_between(pages, t0, t1):
    return [(t, d) for t, d in pages if t0 <= t < t1]


def marker_series(c):
    """The spec 6.2 SoC-valid marker, rebuilt from the wire: set by a closed
    0x440, cleared when 0x411 or 0x440 stops being fresh. Returns [(t, raw)]
    for the 0x411 readings received while it was set."""
    mainc = c.series(0x440, PT, "bcm_mainc_stat")
    soc = c.series(0x411, PTX, "bms_soc_hires")
    ev = sorted([(t, "c", s) for t, s in mainc] + [(t, "s", s) for t, s in soc])
    valid, setm, last_c, last_s = [], False, None, None
    for t, kind, val in ev:
        if setm and ((last_c is not None and t - last_c > 0.5) or
                     (last_s is not None and t - last_s > 0.5)):
            setm = False
        if kind == "c":
            last_c = t
            if val in CLOSED:
                setm = True
        else:
            last_s = t
            if setm:
                valid.append((t, val))
    return valid


def debounce_times(valid, t_from):
    """(t of the 4th, t of the 5th) consecutive valid sub-threshold reading at or
    after t_from, a raw 0 never counting and a reading at or above the threshold
    restarting the count (spec 6.2). None where it never got that far."""
    run = []
    for t, raw in valid:
        if t < t_from:
            continue
        if raw == 0:
            continue
        if raw < SOC_MIN_RAW:
            run.append(t)
            if len(run) == DEBOUNCE_N:
                return run[3], run[4]
        else:
            run = []
    return (run[3] if len(run) >= 4 else None), None


def latch_and_flag(c, v, t_latch, reason, t_end):
    """A section 6 latch, judged at the latch itself (spec 6.4; reviewer
    2026-10-15): from the page that has caught up with it until the first mode
    POST after it, every 0x7F1 reads the reason with the live flag clear, and
    every 0x7F8 reads arm_block "disabled", not a stale gate reason. Pages after
    a disarm and re-arm are recorded as facts, not judged: the re-arm half is a
    bonus, and while disarmed the block legitimately reads not_armed.

    "The page that has caught up" (reviewer, 2026-10-20, after B20 went
    INCONCLUSIVE on page phase in the e3d9437 acceptance run): the judged pages
    start at the first page after the latch that already reads the latch (0x7F1
    the reason, 0x7F8 "disabled"), or at latch + PAGE_LAG_S if that comes first.
    A page inside the lag that has not caught up is not judged; every page from
    the first one that has is. Without this, the window [latch + PAGE_LAG_S,
    POST) was ~0.92 s wide in liveflag-mmode against a ~1.24 s page period."""
    posts = [p["t_ret"] for p in c.posts if p.get("t_ret") is not None and p["t_ret"] > t_latch]
    t_stop = min([t_end] + posts)

    def caught_up(pages, reads):
        early = [t for t, d in pages_between(pages, t_latch, t_latch + PAGE_LAG_S) if reads(d)]
        return early[0] if early else t_latch + PAGE_LAG_S

    after = pages_between(c.p7f1, caught_up(c.p7f1, lambda d: disable_name(d) == reason), t_stop)
    v.need("0x7F1 pages between the latch and any mode change", len(after) >= 1,
           "%d pages%s" % (len(after), ", from %+.2f s" % (after[0][0] - t_latch) if after else ""))
    wrong = [round(t - t_latch, 2) for t, d in after if disable_name(d) != reason]
    v.check("disable_reason reads %s after the latch" % reason, not wrong,
            "pages not reading it at %s s" % wrong if wrong else "%d pages" % len(after))
    lit = [round(t - t_latch, 2) for t, d in after if flag(d, 5)]
    v.check("live flag clear after the latch", not lit, "bit5 set at %s s" % lit if lit else "")
    blk = pages_between(c.p7f8, caught_up(c.p7f8, lambda d: block_name(d) == "disabled"), t_stop)
    v.need("0x7F8 pages between the latch and any mode change", len(blk) >= 1,
           "%d pages%s" % (len(blk), ", from %+.2f s" % (blk[0][0] - t_latch) if blk else ""))
    wrongb = [(round(t - t_latch, 2), block_name(d)) for t, d in blk if block_name(d) != "disabled"]
    v.check("arm_block reports the disable", not wrongb,
            "%s" % wrongb if wrongb else "%d pages" % len(blk))
    rearm = [p["t_ret"] for p in c.posts if p.get("mode") == 3 and p.get("t_ret") is not None
             and p["t_ret"] > t_latch]
    if rearm:
        ra1 = pages_between(c.p7f1, rearm[0] + PAGE_LAG_S, t_end)
        ra8 = pages_between(c.p7f8, rearm[0] + PAGE_LAG_S, t_end)
        v.facts["after_rearm"] = {
            "pages": len(ra1),
            "live_flag_set_at_s": [round(t - rearm[0], 2) for t, d in ra1 if flag(d, 5)],
            "disable_reason": sorted({disable_name(d) for _, d in ra1}),
            "arm_block": sorted({block_name(d) for _, d in ra8})}


# --------------------------------------------------------- the SoC and M cases

def judge_soc_latch(c, case, v):
    """A sustained valid sub-threshold SoC latches low_soc on the 5th reading,
    not before; transmission stops for good, through any recovery."""
    valid = marker_series(c)
    lows = [t for t, raw in valid if 0 < raw < SOC_MIN_RAW]
    v.need("valid sub-threshold readings on the wire", len(lows) >= DEBOUNCE_N, "%d" % len(lows))
    if len(lows) < DEBOUNCE_N:
        return
    t4, t5 = debounce_times(valid, lows[0])
    v.need("five consecutive valid sub-threshold readings", t5 is not None, "")
    if t5 is None:
        return
    t_end = c.vcm_t[-1] if c.vcm_t else t5
    v.facts.update(first_low=r3(lows[0]), t4=r3(t4), t5=r3(t5))
    live_before(c, v, lows[0])
    hold = case.get("hold_above")
    if hold:
        hv = [t for t, raw in valid if raw == hold]
        v.need("readings at %d on the wire" % hold, len(hv) >= 20, "%d" % len(hv))
        if hv:
            pre = pages_between(c.p7f1, hv[0], lows[0])
            dis = [round(t - hv[0], 2) for t, d in pre if flag(d, 0)]
            gap = c.dev_answering(hv[-1] - 0.5, hv[-1])
            v.check("%d keeps inhibiting" % hold, not dis and gap,
                    "disabled at %s s" % dis if dis else
                    "%d device 0x051 in the last 0.5 s of it" % len(gap))
    n = len(c.dev_answering(t4, t5))
    v.check("not released before the 5th reading", n > 0,
            "%d device 0x051 answering VCM frames between the 4th and 5th" % n)
    silent_from(c, v, t5, "no device 0x051 after the 5th reading")
    latch_and_flag(c, v, t5, "low_soc", t_end)
    rec = case.get("recover_to")
    if rec:
        back = [t for t, raw in valid if t > t5 and raw == rec]
        v.need("readings back at %d after the latch" % rec, len(back) >= 20, "%d" % len(back))


def judge_mmode_latch(c, case, v):
    """shift_lever_pos 4 latches m_mode at once (spec 6.1); the live flag clears
    with it and a re-arm while latched does not set it again (spec 6.4)."""
    sh = c.series(0x639, PTX, "shift_lever_pos")
    m4 = [t for t, p in sh if p == 4]
    v.need("shift_lever_pos 4 on the wire", len(m4) >= 3, "%d frames" % len(m4))
    if not m4:
        return
    t_end = c.vcm_t[-1] if c.vcm_t else m4[0]
    live_before(c, v, m4[0])
    silent_from(c, v, m4[0], "no device 0x051 after the first position 4")
    latch_and_flag(c, v, m4[0], "m_mode", t_end)


def _wake_gate(c, v, t_from, t_to, label):
    """Never live before the close: no device 0x051 before the first 0x411 that
    follows the first closed 0x440 in [t_from, t_to). Returns that time."""
    mainc = [(t, s) for t, s in c.series(0x440, PT, "bcm_mainc_stat") if t_from <= t < t_to]
    closed = [t for t, s in mainc if s in CLOSED]
    if not closed:
        return None
    soc_t = [t for t in c.times(0x411) if closed[0] < t < t_to]
    since = soc_t[0] if soc_t else None
    early = c.dev_answering(t_from, since if since else t_to)
    v.check("%s: never live before the close" % label, not early,
            "%d device 0x051 before the first 0x411 after the first closed 0x440" % len(early))
    return since


def judge_wake(c, case, v):
    """Spec 6.2: a wake reading below 21 % before the close does not release;
    0x411 before the close is ignored; never live before the close."""
    t_arm = (c.post_times(3) or [None])[0]
    v.need("arm POST recorded", t_arm is not None, "")
    if t_arm is None:
        return
    mainc = c.series(0x440, PT, "bcm_mainc_stat")
    closed = [t for t, s in mainc if s in CLOSED]
    soc = c.series(0x411, PTX, "bms_soc_hires")
    pre_low = [t for t, raw in soc if 0 < raw < SOC_MIN_RAW and (not closed or t < closed[0])]
    v.need("sub-threshold readings before the close", len(pre_low) >= DEBOUNCE_N, "%d" % len(pre_low))
    v.need("contactors close on the wire", bool(closed), "")
    if not closed:
        return
    since = _wake_gate(c, v, c.first_sent(3), float("inf"), "wake")
    t_end = c.vcm_t[-1] if c.vcm_t else closed[0]
    if since is not None:
        n = len(c.dev_answering(since, t_end))
        v.check("live after the close", n > 0, "%d device 0x051 after it" % n)
    rel = [round(t - t_arm, 2) for t, d in c.p7f1 if t_arm <= t < t_end + 0.5 and flag(d, 0)]
    v.check("no low_soc release", not rel, "disabled on pages at %s s" % rel if rel else
            "%d pages" % len(pages_between(c.p7f1, t_arm, t_end + 0.5)))


def judge_never_closed(c, case, v):
    """Contactors never closed: never live, block names the SoC marker, no abort,
    nothing latched (spec 6.2, spec 7 condition 2)."""
    t_arm = (c.post_times(3) or [None])[0]
    v.need("arm POST recorded", t_arm is not None, "")
    if t_arm is None:
        return
    mainc = c.series(0x440, PT, "bcm_mainc_stat")
    v.need("0x440 on the wire, never closed", bool(mainc) and all(s not in CLOSED for _, s in mainc),
           "%d frames, states %s" % (len(mainc), sorted({s for _, s in mainc})))
    v.need("0x051 on the wire", len(c.vcm) >= 50, "%d" % len(c.vcm))
    t_end = c.vcm_t[-1] if c.vcm_t else t_arm
    v.check("never live", not c.dev, "%d device 0x051" % len(c.dev))
    blk = pages_between(c.p7f8, t_arm + PAGE_LAG_S, t_end)
    v.need("0x7F8 pages while armed", len(blk) >= 2, "%d" % len(blk))
    wrong = [(round(t - t_arm, 2), block_name(d)) for t, d in blk if block_name(d) != "no_soc_valid"]
    v.check("arm_block names the SoC marker", not wrong, "%s" % wrong if wrong else "%d pages" % len(blk))
    ab = [round(t - t_arm, 2) for t, d in blk if int(d["diag_abort_reason"]) != 0]
    v.check("no abort", not ab, "abort on pages at %s s" % ab if ab else "")
    dis = [round(t - t_arm, 2) for t, d in pages_between(c.p7f1, t_arm, t_end) if flag(d, 0)]
    v.check("nothing latched", not dis, "disabled at %s s" % dis if dis else "")


def judge_gate_open(c, case, v):
    """Spec 7 condition 2: a re-arm with the SoC-valid marker set and 0x440
    reading open stays not live, block contactors_open. The control (same
    stimulus, contactors closed) goes live again."""
    arms = c.post_times(3)
    v.need("a re-arm after the first arm", len(arms) >= 2, "%d mode-3 POSTs" % len(arms))
    if len(arms) < 2:
        return
    t_re = arms[-1]
    t_end = c.vcm_t[-1] if c.vcm_t else t_re
    v.facts["rearm"] = r3(t_re)
    live_before(c, v, t_re - 0.1, span=1.0, label="live before the disarm")
    st = [s for t, s in c.series(0x440, PT, "bcm_mainc_stat") if t > t_re]
    want_open = not case.get("control")
    v.need("0x440 after the re-arm reads %s" % ("open" if want_open else "closed"),
           st and all((s not in CLOSED) == want_open for s in st), "states %s" % sorted(set(st)))
    f2 = pages_between(c.p7f8, t_re + PAGE_LAG_S, t_end)
    v.need("0x7F8 pages after the re-arm", len(f2) >= 2, "%d" % len(f2))
    unset = [round(t - t_re, 2) for t, d in f2 if not flag(d, 0, "diag_flags2")]
    v.need("SoC-valid marker set after the re-arm", not unset,
           "marker clear at %s s: the case would test the marker, not condition 2" % unset if unset else "")
    if want_open:
        silent_from(c, v, t_re, "not live after the re-arm")
        wrong = [(round(t - t_re, 2), block_name(d)) for t, d in f2 if block_name(d) != "contactors_open"]
        v.check("arm_block names condition 2", not wrong, "%s" % wrong if wrong else "%d pages" % len(f2))
        ab = [round(t - t_re, 2) for t, d in f2 if int(d["diag_abort_reason"]) != 0]
        v.check("no abort", not ab, "abort at %s s" % ab if ab else "")
    else:
        n = len(c.dev_answering(t_re, t_end))
        v.check("control: live again after the re-arm", n > 0, "%d device 0x051" % n)


def judge_suppress_twice(c, case, v):
    """Spec 6.3: B0 = 0x10 suppresses without latching; two episodes in one
    power cycle, each releasing by itself; the flag bit tracks it (6.4)."""
    b0 = [(t, int(h[:2], 16)) for t, h in c.by_id.get(0x051, [])]
    eps, cur = [], None
    for t, b in b0:
        if b == 0x10:
            cur = [t, t] if cur is None else [cur[0], t]
        elif cur is not None and t - cur[1] > 0.1:
            eps.append(cur)
            cur = None
    if cur is not None:
        eps.append(cur)
    v.need("two 0x10 episodes on the wire", len(eps) == 2, "%d" % len(eps))
    if len(eps) != 2:
        return
    v.facts["episodes"] = [[r3(a), r3(b)] for a, b in eps]
    live_before(c, v, eps[0][0])
    t_end = c.vcm_t[-1]
    for k, (a, b) in enumerate(eps):
        inside = [round(t - a, 4) for t, x in b0 if a < t < b and x != 0x10]
        v.check("episode %d: no device 0x051" % (k + 1), not inside,
                "%d frames with B0 != 0x10 inside it" % len(inside))
        nxt_start = eps[k + 1][0] if k + 1 < len(eps) else t_end
        n = len(c.dev_answering(b, nxt_start))
        v.check("episode %d: transmission resumes after it" % (k + 1), n > 0, "%d device 0x051" % n)
        inp = pages_between(c.p7f1, a + 0.1, b)
        v.need("episode %d: a 0x7F1 page inside it" % (k + 1), bool(inp), "%d" % len(inp))
        off = [round(t - a, 2) for t, d in inp if not flag(d, 4)]
        v.check("episode %d: shutdown_suppressed set" % (k + 1), not off, "clear at %s s" % off if off else "")
    outside = [(t, d) for t, d in pages_between(c.p7f1, 0, t_end)
               if not any(a - 0.05 <= t <= b + PAGE_LAG_S for a, b in eps)]
    lit = [round(t - eps[0][0], 2) for t, d in outside if flag(d, 4)]
    v.check("shutdown_suppressed clear outside the episodes", not lit, "set at %s s" % lit if lit else "")
    lat = [round(t - eps[0][0], 2) for t, d in pages_between(c.p7f1, eps[0][0], t_end) if flag(d, 0) or flag(d, 7)]
    v.check("nothing latched", not lat, "disabled or aborted at %s s" % lat if lat else "")


# ----------------------------------------------------------- the trips (spec 7)

def stale_at(times, window, t_from):
    """The first moment at or after t_from when a signal is stale: the last frame
    before a gap longer than its window, plus the window."""
    ts = [t for t in times]
    k = bisect.bisect_right(ts, t_from) - 1
    if k < 0:
        return None
    for j in range(k, len(ts)):
        nxt = ts[j + 1] if j + 1 < len(ts) else None
        if nxt is None or nxt - ts[j] > window:
            exp = ts[j] + window
            if exp >= t_from:
                return exp
    return None


def predict_trip(c, t_live, t_arm):
    """The reasons spec 7 allows to come first, for a device live from t_live
    and armed (this arm cycle) from t_arm. Returns (reasons, earliest VCM frame
    a trip can fire on, details)."""
    cand = {}
    for ident, w in WINDOW_S.items():
        ts = c.times(ident)
        heard = [t for t in ts if t >= t_arm] if ident in ONCE_HEARD else ts
        if not heard:
            continue
        exp = stale_at(ts, w, max(t_live, heard[0]))
        if exp is None:
            continue
        lo, hi = c.vcm_after(exp - EDGE_S), c.vcm_after(exp + EDGE_S)
        if lo is None:
            continue
        cand[ident] = (exp, lo, hi)
    cmd_exp = stale_at(c.vcm_t, CMD_WINDOW_S, t_live)
    if not cand:
        return ({"bus_lost"} if cmd_exp else set()), None, {}
    first_hi = min(hi if hi is not None else float("inf") for _, _, hi in cand.values())
    win = [i for i, (exp, lo, hi) in cand.items() if lo <= first_hi]
    if cmd_exp is not None and cmd_exp < min(cand[i][1] for i in win):
        return {"bus_lost"}, None, {}
    reasons = set()
    for i in win:
        if i == 0x471:
            reasons.add("inverter_lost")
        elif i == 0x054:
            # Trip 5 only while 0x471 is still fresh at the frame it fires on;
            # a stale 0x471 is in `win` itself and adds trip 3.
            other = cand.get(0x471)
            t_fire = cand[i][1]
            if other is None or other[0] >= t_fire - EDGE_S:
                reasons.add("stale_rpm")
        else:
            reasons.add(STALE_REASON[i])
    t_first = min(cand[i][1] for i in win)
    det = {("0x%03X" % i): {"stale_at": r3(e), "fires_lo": r3(lo), "fires_hi": r3(hi)}
           for i, (e, lo, hi) in cand.items()}
    return reasons, t_first, det


def first_abort(c, t_from):
    for t, d in c.p7f8:
        if t >= t_from and int(d["diag_abort_reason"]) != 0:
            return t, abort_name(d)
    return None, None


def judge_trip(c, case, v):
    """A live trip by stimulus: the signal(s) in case["cut"] stop mid-arm (proved
    on the wire), and the device trips on the first 0x051 after the window
    ends -- not before, not after -- with the reason the model predicts, which
    must be the case's own expectation. That evidence 0x051 may be answered;
    nothing after it may be (spec 7, user 2026-10-16)."""
    t_arm = (c.post_times(3) or [None])[0]
    v.need("arm POST recorded", t_arm is not None, "")
    if t_arm is None or not c.dev:
        v.need("device live", bool(c.dev), "no device 0x051 at all")
        return
    t_live = c.dev[0][0]
    cut_at = case["cut_at_epoch"]
    for ident in case["cut"]:
        ts = c.times(ident)
        before = [t for t in ts if cut_at - 1.0 <= t < cut_at]
        after = [t for t in ts if t >= cut_at + 0.05]
        v.need("cut 0x%03X proved on the wire" % ident, before and not after,
               "%d frames in the second before, %d after" % (len(before), len(after)))
    others = [i for i in WINDOW_S if i not in case["cut"]]
    heard = [i for i in others if [t for t in c.times(i) if t >= t_arm]]
    gone = [("0x%03X" % i) for i in heard if not [t for t in c.times(i) if t >= cut_at + 1.5]]
    v.need("every other interlock signal still on the wire", not gone, "stopped: %s" % gone)
    v.need("0x051 continues after the cut", len([t for t in c.vcm_t if t > cut_at]) >= 100, "")
    live_before(c, v, cut_at)
    reasons, t_fire, det = predict_trip(c, t_live, t_arm)
    v.facts.update(model=det, model_reasons=sorted(reasons), fires_on=r3(t_fire))
    v.need("the model predicts the case's own trip", case["expect"] in reasons,
           "model %s, case %s" % (sorted(reasons), case["expect"]))
    if t_fire is None:
        return
    exp = min(det["0x%03X" % i]["stale_at"] for i in case["cut"] if ("0x%03X" % i) in det) \
        if any(("0x%03X" % i) in det for i in case["cut"]) else None
    if exp is not None:
        n = len(c.dev_answering(exp - 0.1, exp - EDGE_S))
        v.check("not early: answering until the window ends", n > 0,
                "%d device 0x051 answering VCM frames in the 0.1 s before expiry" % n)
        # The evidence 0x051 is the first after the expiry, and it may itself be
        # answered; the inhibit stops from the next one (spec 7, "A single signal
        # lost on a live bus still trips"; user 2026-10-16). A frame within EDGE_S
        # of the expiry may be either side of it on the device, so the latest
        # frame that can be the evidence is the first after expiry + EDGE_S.
        evidence = c.vcm_after(exp + EDGE_S)
        v.need("0x051 after the window ends", evidence is not None, "")
        if evidence is not None:
            after = c.vcm_after(evidence)
            v.need("a 0x051 after the evidence 0x051", after is not None, "")
            v.facts.update(evidence_0x051=r3(evidence), evidence_answered=len(
                c.dev_answering(c.vcm_after(exp - EDGE_S), after or float("inf"))))
            if after is not None:
                silent_from(c, v, after,
                            "not late: nothing answered after the evidence 0x051")
        last = c.dev[-1][0]
        v.facts.update(stale_at=r3(exp), last_device_0x051_minus_expiry=r3(last - exp))
    ta, name = first_abort(c, t_live)
    v.check("the trip names %s on the wire" % case["expect"], name == case["expect"],
            "first 0x7F8 abort reason %r at %s" % (name, r3(None if ta is None else ta - (exp or 0))))
    if ta is not None and exp is not None:
        v.check("no abort reported before the window ends", ta >= exp - EDGE_S,
                "first abort page %+.3f s from expiry" % (ta - exp))


def judge_short_frame(c, case, v):
    """Spec 7 trip 8: a 0x051 with DLC < 6 while live aborts, is not answered,
    and does not count as tx_fail. The control (DLC >= 6) changes nothing."""
    odd = [(t, h) for t, h in c.vcm if len(h) // 2 != 6]
    v.need("exactly one injected 0x051 on the wire", len(odd) == 1,
           "%d with DLC != 6: %s" % (len(odd), [len(h) // 2 for _, h in odd]))
    if len(odd) != 1:
        return
    ti, hi = odd[0]
    dlc = len(hi) // 2
    v.need("its DLC is the case's", dlc == case["dlc"], "DLC %d" % dlc)
    live_before(c, v, ti)
    t_end = c.vcm_t[-1]
    pre_fail = [int(d["diag_tx_fail"]) for t, d in c.p7f2 if t < ti]
    if dlc < 6:
        silent_from(c, v, ti, "the short frame is not answered, nor anything after it")
        ta, name = first_abort(c, ti - 0.05)
        v.check("the trip names short_vcm_frame on the wire", name == "short_vcm_frame",
                "first abort %r" % name)
        post = [int(d["diag_tx_fail"]) for t, d in c.p7f2 if t > ti + PAGE_LAG_S]
        v.need("0x7F2 pages before and after", pre_fail and post, "%d / %d" % (len(pre_fail), len(post)))
        if pre_fail and post:
            v.check("tx_fail not incremented", max(post) == pre_fail[-1],
                    "before %d, after %s" % (pre_fail[-1], sorted(set(post))))
    else:
        mine = [t for t, h in c.dev if c.answered(t) == ti]
        v.check("control: the DLC %d frame is answered" % dlc, len(mine) == 1,
                "%d device 0x051 answering it" % len(mine))
        n = len(c.dev_answering(ti, t_end))
        v.check("control: still answering after it", n >= 50, "%d device 0x051" % n)
        ab = [round(t - ti, 2) for t, d in pages_between(c.p7f8, ti, t_end) if int(d["diag_abort_reason"]) != 0]
        v.check("control: no abort", not ab, "abort at %s s" % ab if ab else "")


def judge_rekey(c, case, v):
    """A real capture with key cycles (replay-rekey-long): never low_soc, never
    live before a close, and at each key-off the key reads 0 on the wire before
    the trip, whose reason the model allows (spec 7 trip 5 text: tests must pin
    the ordering)."""
    t_arm = (c.post_times(3) or [None])[0]
    v.need("arm POST recorded", t_arm is not None, "")
    if t_arm is None:
        return
    key = c.series(0x592, PT, "IgnitionKeyState")
    offs = [t for (t0, k0), (t, k) in zip(key, key[1:]) if k0 == 1 and k == 0]
    ons = [t for (t0, k0), (t, k) in zip(key, key[1:]) if k0 == 0 and k == 1]
    v.need("key-offs on the wire", len(offs) >= 1, "%d off, %d on" % (len(offs), len(ons)))
    v.facts.update(key_off=[r3(x) for x in offs], key_on=[r3(x) for x in ons])
    t_end = c.vcm_t[-1] if c.vcm_t else t_arm
    rel = [round(t - t_arm, 2) for t, d in c.p7f1 if t_arm <= t <= t_end and flag(d, 0)]
    v.check("no low_soc release, whole capture", not rel, "disabled at %s s" % rel if rel else
            "%d pages" % len(pages_between(c.p7f1, t_arm, t_end)))
    starts = [t_arm] + [t for t in ons if t > t_arm]
    for k, s in enumerate(starts):
        nxt = starts[k + 1] if k + 1 < len(starts) else t_end + 1
        _wake_gate(c, v, c.first_sent(3) if k == 0 else s, nxt, "segment %d" % (k + 1))
        seg_dev = [t for t, _ in c.dev if s <= t < nxt]
        koff = [t for t in offs if s < t < nxt]
        if not seg_dev or not koff:
            continue
        t_live, t_off = seg_dev[0], koff[0]
        silent_from(c, v, t_off, "segment %d: no device 0x051 after the key-off" % (k + 1), nxt)
        reasons, t_fire, det = predict_trip(c, t_live, s)
        ta, name = first_abort(c, s)
        v.facts["segment_%d" % (k + 1)] = {"live": r3(t_live), "key_off": r3(t_off), "model": det,
                                          "model_reasons": sorted(reasons), "abort_page": r3(ta),
                                          "abort": name}
        v.check("segment %d: key reads 0 before the trip" % (k + 1),
                ta is not None and ta > t_off and (t_fire is None or t_fire > t_off),
                "key-off %s, model fires %s, first abort page %s" % (r3(t_off), r3(t_fire), r3(ta)))
        v.check("segment %d: the trip is one the model allows" % (k + 1), name in reasons,
                "device %r, model %s" % (name, sorted(reasons)))


def judge_event_trip(c, case, v):
    """A live trip set off by a value, not a silence: trip 1 (0x617 B7 = 0xCA,
    evaluated on that frame) and trip 4 (GENE_RotSpd >= 300 rpm sustained for
    0.3 s). The device answers until the trip and nothing after it, and names it."""
    t_arm = (c.post_times(3) or [None])[0]
    v.need("arm POST recorded", t_arm is not None, "")
    cut = case["cut_at_epoch"]
    if case["event"] == "vcm_fault":
        fr = [(t, int(h[14:16], 16)) for t, h in c.by_id.get(0x617, []) if len(h) >= 16]
        before = [t for t, b in fr if cut - 1.5 <= t < cut and b != 0xCA]
        bad = [t for t, b in fr if t >= cut and b == 0xCA]
        v.need("0x617 B7 changed to 0xCA on the wire", before and bad and
               all(b == 0xCA for t, b in fr if t >= cut + 0.05),
               "%d clear frames before, %d with 0xCA after" % (len(before), len(bad)))
        if not bad:
            return
        t_ev, lo, hi = bad[0], bad[0], bad[0]
    else:
        rpm = c.series(0x054, PT, "GENE_RotSpd")
        hi_t = [t for t, r in rpm if t >= cut and r - 32767 >= 300]
        pre = [t for t, r in rpm if cut - 1.0 <= t < cut and r - 32767 < 300]
        v.need("GENE_RotSpd >= 300 rpm from the change on, below it before", hi_t and pre and
               all(r - 32767 >= 300 for t, r in rpm if t >= cut + 0.05),
               "%d frames below before, %d at or above after" % (len(pre), len(hi_t)))
        if not hi_t:
            return
        t_ev = hi_t[0] + 0.3
        due = [t for t in hi_t if t >= t_ev - EDGE_S]
        if not due:
            v.need("0x054 frames past the debounce", False, "")
            return
        lo = hi_t[0] + 0.3 - 0.012        # one 0x054 period of slack each side
        hi = due[0] + 0.012
    v.facts.update(event=r3(t_ev))
    live_before(c, v, lo)
    n = len(c.dev_answering(lo - 0.1, lo))
    v.check("not early: answering until the trip", n > 0, "%d device 0x051 in the 0.1 s before it" % n)
    silent_from(c, v, hi + EDGE_S if hi > lo else hi, "not late: nothing answered after it")
    ta, name = first_abort(c, t_arm or 0)
    v.check("the trip names %s on the wire" % case["expect"], name == case["expect"], "first abort %r" % name)
    if ta is not None:
        v.check("no abort reported before it", ta >= lo - EDGE_S, "first abort page %+.3f s" % (ta - lo))


def judge_bus_stop(c, case, v):
    """Spec 7 trip 2: every frame stops while live; the device reports bus_lost,
    and it stays latched when the bus returns (no answer to anything after)."""
    t_arm = (c.post_times(3) or [None])[0]
    v.need("arm POST recorded", t_arm is not None, "")
    stop, back = case["cut_at_epoch"], case["resume_at_epoch"]
    sent_ids = {i for t, i, h in c.frames if i not in (0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9)
                and i < 0x7F0}
    gap = [(t, i) for t, i, h in c.frames if stop + 0.05 <= t < back - 0.01 and i in sent_ids
           and not (i == 0x051 and any(abs(t - d) < 1e-9 for d, _ in c.dev))]
    before = [t for t in c.vcm_t if stop - 1.0 <= t < stop]
    after = [t for t in c.vcm_t if t >= back]
    v.need("the bus stopped and came back (proved on the wire)", before and after and not gap,
           "%d VCM frames before, %d after, %d stimulus frames inside the gap" % (len(before), len(after), len(gap)))
    if not before:
        return
    live_before(c, v, stop)
    reasons, t_fire, det = predict_trip(c, c.dev[0][0] if c.dev else stop, t_arm or 0)
    v.need("the model predicts bus_lost", "bus_lost" in reasons, "model %s" % sorted(reasons))
    silent_from(c, v, stop, "no device 0x051 after the stop, before or after the bus returns")
    ta, name = first_abort(c, t_arm or 0)
    v.check("the trip names bus_lost on the wire", name == "bus_lost", "first abort %r" % name)
    last = before[-1]
    if ta is not None:
        v.check("no abort reported before 0x051 goes stale", ta >= last + CMD_WINDOW_S - EDGE_S,
                "first abort page %+.3f s from the last 0x051" % (ta - last))


def judge_gate_refuse(c, case, v):
    """The gate holds the arm off on one failing condition, the case's, and
    arm_block names it (spec 7 conditions 1, 5, 6). Exactly one condition fails
    in each stimulus, so the block cannot honestly name another."""
    t_arm = (c.post_times(3) or [None])[0]
    v.need("arm POST recorded", t_arm is not None, "")
    if t_arm is None:
        return
    blk = case["block"]
    t_end = max(t for t, _, _ in c.frames)
    win = (t_arm, t_end)
    if blk == "gen_running":
        rpm = [r - 32767 for t, r in c.series(0x054, PT, "GENE_RotSpd") if win[0] <= t <= win[1]]
        v.need("GENE_RotSpd >= 300 rpm throughout", rpm and min(rpm) >= 300, "min %s" % (min(rpm) if rpm else None))
    elif blk == "vcm_torque":
        tq = [decode(PTX, 0x051, h) for t, h in c.vcm if win[0] <= t <= win[1]]
        tq = [d for d in tq if d]
        v.need("gen_torque_cmd non-zero and gen_rpm_ref at its null throughout",
               tq and all(int(d["gen_torque_cmd"]) != 32768 and int(d["gen_rpm_ref"]) < 32768 for d in tq),
               "%d frames" % len(tq))
    elif blk == "no_fresh_cmd":
        v.need("no 0x051 from the stimulus", not c.vcm, "%d" % len(c.vcm))
    if blk != "no_fresh_cmd":
        v.need("0x051 on the wire", len(c.vcm) >= 100, "%d" % len(c.vcm))
    closed = [t for t, st in c.series(0x440, PT, "bcm_mainc_stat") if st in CLOSED]
    v.need("the other gate conditions hold (contactors closed, 0x411, 0x617, 0x639 present)",
           closed and c.times(0x411) and c.times(0x617) and c.times(0x639), "")
    v.check("never live", not c.dev, "%d device 0x051" % len(c.dev))
    pages = pages_between(c.p7f8, t_arm + PAGE_LAG_S, t_end)
    v.need("0x7F8 pages while armed", len(pages) >= 2, "%d" % len(pages))
    wrong = [(round(t - t_arm, 2), block_name(d)) for t, d in pages if block_name(d) != blk]
    v.check("arm_block names %s" % blk, not wrong, "%s" % wrong[:5] if wrong else "%d pages" % len(pages))
    ab = [round(t - t_arm, 2) for t, d in pages if int(d["diag_abort_reason"]) != 0]
    v.check("no abort", not ab, "abort at %s s" % ab if ab else "")


JUDGES = {"soc_latch": judge_soc_latch, "mmode_latch": judge_mmode_latch, "wake": judge_wake,
          "never_closed": judge_never_closed, "gate_open": judge_gate_open,
          "suppress_twice": judge_suppress_twice, "trip": judge_trip,
          "short_frame": judge_short_frame, "rekey": judge_rekey, "event_trip": judge_event_trip,
          "bus_stop": judge_bus_stop, "gate_refuse": judge_gate_refuse}


# Spec 10: the diag pages go "round-robin at ~1 Hz each while armed", "sent all the
# time, including while inhibiting" (reviewer's reading, 2026-10-16: a check, latched
# or not). The bound: across the 26 B20 runs on c65c45e the largest gap of any id
# while armed, liveflag-mmode aside, was 2.13 s (trip2-bus-stop; the cadence is
# ~1.2-1.25 s), so 2.5 s -- two cadences, i.e. a page slot skipped -- with 0.37 s of
# margin (runs/baseline_c65c45e/bench/B20_diag_gap_survey_20261016.txt).
DIAG_IDS = (0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9)
DIAG_GAP_MAX_S = 2.5


def armed_spans(c):
    """[(from, to)] while the mode is not OFF: each non-OFF POST's return to the
    next POST, or to the last frame. The device starts each run in mode 0."""
    end = max((t for fr in c.by_id.values() for t, _ in fr), default=None)
    posts = sorted((p["t_ret"], p.get("mode")) for p in c.posts
                   if p.get("t_ret") is not None and p.get("ok", True) is not False)
    return [(t, posts[k + 1][0] if k + 1 < len(posts) else end)
            for k, (t, m) in enumerate(posts) if m != 0 and end is not None]


def diag_gaps(c):
    """{id: largest gap while armed}, counting from a span's start to its first
    page and from its last page to the span's end, for each diag id seen.
    Back-to-back spans (a non-OFF to non-OFF change, e.g. a 3->3 re-arm) are joined
    first (2026-10-16): restarting the gap at every POST would hide a silence that
    straddles a change."""
    spans = []
    for a, b in armed_spans(c):
        if spans and abs(spans[-1][1] - a) < 1e-9:
            spans[-1] = (spans[-1][0], b)
        else:
            spans.append((a, b))
    out = {}
    for ident in DIAG_IDS:
        ts = c.times(ident)
        if not ts:
            continue
        g = 0.0
        for a, b in spans:
            x = [a] + [t for t in ts if a <= t < b] + [b]
            g = max(g, max(q - p for p, q in zip(x, x[1:])))
        out["0x%03X" % ident] = round(g, 3)
    return out, spans


def judge(kind, frames, posts, samples, case, sent=None):
    c = Ctx(frames, posts, samples, case.get("alias"), sent=sent)
    v = Verdict()
    v.facts["device_0x051"] = len(c.dev)
    v.facts["vcm_0x051"] = len(c.vcm)
    if c.attribution is not None:
        a = c.attribution
        v.facts["attribution"] = dict(a, ambiguous=a["ambiguous"][:10], ambiguous_n=len(a["ambiguous"]),
                                      method="sender log")
        v.need("0x051 attribution unambiguous", not a["ambiguous"],
               "%d pair(s) within the residual %s s" % (len(a["ambiguous"]), a["residual_max_s"]))
    else:
        v.facts["attribution"] = {"method": "content"}
    v.facts["diag_pages"] = {"0x7F1": len(c.p7f1), "0x7F8": len(c.p7f8), "0x7F2": len(c.p7f2)}
    ref = [round(t, 3) for t, d in c.p7f8 if flag(d, 3, "diag_flags2")]
    v.check("emit_refused never set", not ref, "set at %s" % ref[:5] if ref else "")
    gaps, spans = diag_gaps(c)
    v.facts["diag_gap_max_s"] = gaps
    longest = max((b - a for a, b in spans), default=0.0)
    over = {k: g for k, g in gaps.items() if g > DIAG_GAP_MAX_S}
    v.check("diag pages keep their cadence while armed (no id silent > %.1f s)" % DIAG_GAP_MAX_S,
            not over and (gaps or longest <= DIAG_GAP_MAX_S),
            ("over: %s" % over) if over else ("no diag page at all while armed" if not gaps else
                                               "largest %s s" % max(gaps.values())))
    JUDGES[kind](c, case, v)
    return v.result()
