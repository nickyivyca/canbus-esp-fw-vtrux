"""Spec 2 / 2.3 diagnostic: does the board lose frames when the vehicle port
switches between listen-only and normal? (reviewer, 2026-10-15, after
bench_hold12's stimulus check lost one identifier frame ~1 ms after the
first charger frame on 756d6714.)

Stimulus, all on the wire:
  - the identifier (bench_hold12.ID, DLC 3, 24-bit counter) offered steadily
    at 2,000 fps by the PCAN on the vehicle segment, throughout;
  - the filler (bench_hold12.FILLER, extended SA 0xC0, DLC 8, bursts of 8 at
    1,000 fps, a 32-bit counter in bytes 0-3) from the Innomaker on the
    charger segment:
      SWITCH phase: CYCLES times on for ON_S, off for OFF_S (> 2 s, so the
        port goes back to listen-only each time): each start switches the
        port to normal, each stop switches it back 2 s later;
      CONTROL phase: on throughout for the same length: no switch.
  - the Kvaser (normal mode) witnesses the vehicle wire, the Innomaker's
    echoes the filler on the charger wire, and the Innomaker receives the
    forwarded identifier.

Reported per switch (analyse(), pure; --self-test):
  - vehicle -> charger: identifier frames on the vehicle wire within +/-5 ms
    of the switch, and how many of them never reached the charger side;
  - charger -> vehicle: whether each start's FIRST filler frame reached the
    vehicle wire (2.3: the port switches first, then forwards that frame);
  - the switch time the board printed.
  Loss counts per direction; frames lost away from any switch, and in the
  CONTROL phase, as the background.
Placement, per phase: 'W' at its start and 'w' at its end, so RECONCILE's
v2c and completions can be set against the frames on the vehicle wire in
the same window: v2c short = the board never received them; completions
short of v2c = lost in its transmit; both equal and the charger short = the
adapter missed them.

    py -3.14 bench_switch_loss.py --self-test
    py -3.14 bench_switch_loss.py --port COM3 --out <file.txt>

Tester, 2026-10-15. A diagnostic of 2.3's switch, not 12 entry 2's run.
"""

import argparse
import bisect
import datetime
import json
import multiprocessing as mp
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bench_hold12 as H                                     # noqa: E402

CYCLES = 20
ON_S = 1.5
OFF_S = 2.8
REVERT_S = 2.0              # spec 2.3: listen-only 2 s after the last frame
WIN_S = 0.005
LEAD_S = 2.0
MODE_RE = re.compile(r"^\[(\d+)\] \w+\s+vehicle port mode .* a=([01]) ")
SWITCH_US_RE = re.compile(r"vehicle port -> (normal|LISTEN-ONLY) \((\d+) us\)")


def fseq(data_hex):
    b = bytes.fromhex(data_hex)
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


# --- analysis, pure ---------------------------------------------------------

def analyse(veh, chg, serial, starts, stops, phases, dumps, more_starts=()):
    """veh: Kvaser rows (t_dev, t_host, id, ext, dlc, hex); chg: Innomaker
    rows (+ is_rx); serial: (host, line); starts/stops: host times of each
    filler start / stop; phases: {name: (t0, t1)} host windows; dumps:
    {name: [lines]}. -> list of report lines and a summary dict."""
    out, summ = [], {}
    vid = [(r[1], H.seq_of(r[5])) for r in veh if r[2] == H.ID and r[3]]
    vid.sort()
    vt = [t for t, _s in vid]
    got = set(H.seq_of(r[5]) for r in chg if r[2] == H.ID and r[3] and r[6])
    # switch times: the normal switch at each start's first filler echo; the
    # listen-only switch from the board's own event line, placed on the host
    # clock by the offset its normal-switch lines give against those echoes
    echo = sorted((r[1], fseq(r[5])) for r in chg
                  if r[2] == H.FILLER and r[3] and not r[6])
    et = [t for t, _q in echo]
    ev = []
    for h, ln in serial:
        m = MODE_RE.match(ln)
        if m:
            ev.append((int(m.group(1)) / 1000.0, int(m.group(2)), h))
    first_echo = []
    for s0 in list(starts) + list(more_starts):
        k = bisect.bisect_left(et, s0)
        first_echo.append(echo[k] if k < len(echo) else (None, None))
    offs = []
    norm_ev = [e for e in ev if e[1] == 1]
    for (tfe, _q) in first_echo:
        if tfe is None:
            continue
        near = [e for e in norm_ev if 0 <= e[2] - tfe < 0.5]
        if near:
            offs.append(tfe - near[0][0])
    off = sorted(offs)[len(offs) // 2] if offs else None
    sw_us = [(m.group(1), int(m.group(2))) for _h, ln in serial
             for m in [SWITCH_US_RE.search(ln)] if m]
    normals = [t for t, _q in first_echo if t is not None]
    listens = ([b + off for b, a, _h in ev if a == 0 and off is not None]
               if off is not None else [])
    s0, s1 = phases["switch"]
    listens = [t for t in listens if s0 <= t <= s1 + REVERT_S + 1.0]

    def window_loss(t):
        i, j = bisect.bisect_left(vt, t - WIN_S), bisect.bisect_right(vt, t + WIN_S)
        seqs = [vid[k][1] for k in range(i, j)]
        return len(seqs), [q for q in seqs if q not in got]
    rows_n, rows_l = [], []
    for kind, ts in (("listen-only -> normal", normals),
                     ("normal -> listen-only", listens)):
        lost_total, frames_total = 0, 0
        for i, t in enumerate(ts):
            n, lost = window_loss(t)
            frames_total += n
            lost_total += len(lost)
            (rows_n if kind.endswith("normal") else rows_l).append(
                "  %-22s #%2d host %.4f: %d identifier frame(s) within +/-5 ms, "
                "%d never received%s" % (kind, i + 1, t, n, len(lost),
                                         (" (seq %s)" % lost[:4]) if lost else ""))
        summ[kind] = (len(ts), frames_total, lost_total)
    out.append("switches: %d listen-only -> normal (at each start's first "
               "filler echo), %d normal -> listen-only (the board's event "
               "line, host offset %s s from %d start pairs); switch times "
               "printed: %s"
               % (len(normals), len(listens),
                  "%.4f" % off if off is not None else "unknown", len(offs),
                  ", ".join("%s %d us" % x for x in sw_us[:6]) +
                  (" ..." if len(sw_us) > 6 else "")))
    out += rows_n + rows_l
    for kind in ("listen-only -> normal", "normal -> listen-only"):
        n_sw, n_fr, n_lo = summ.get(kind, (0, 0, 0))
        out.append("LOSS at %s switches: %d frame(s) lost of %d within +/-5 ms "
                   "of %d switch(es)" % (kind, n_lo, n_fr, n_sw))
    # charger -> vehicle: each start's first filler frame on the vehicle wire
    bridged = set(fseq(r[5]) for r in veh if r[2] == H.FILLER and r[3])
    fwd_first = sum(1 for _t, q in first_echo if q is not None and q in bridged)
    out.append("charger -> vehicle: the first filler frame of %d of %d starts "
               "reached the vehicle wire" % (fwd_first, len(first_echo)))
    summ["first_forwarded"] = (fwd_first, len(first_echo))
    summ["first_missing_t"] = [t for t, q in first_echo
                               if q is not None and q not in bridged]
    # background: losses away from every switch window, per phase
    sw = sorted(normals + listens)
    for name, (a, b) in sorted(phases.items()):
        lost = [(t, q) for t, q in vid if a <= t <= b and q not in got]
        away = [(t, q) for t, q in lost
                if not any(abs(t - x) <= WIN_S for x in sw)]
        nv = sum(1 for t, _q in vid if a <= t <= b)
        fe = [q for t, q in echo if a <= t <= b]
        fl = [q for q in fe if q not in bridged]
        out.append("phase %s (%.1f s): identifier %d on the vehicle wire, %d "
                   "never received (%d away from any switch); filler %d echoed, "
                   "%d never bridged onto the vehicle wire"
                   % (name, b - a, nv, len(lost), len(away), len(fe), len(fl)))
        summ["phase_" + name] = (nv, len(lost), len(away), len(fe), len(fl))
        m = H.RECONCILE_RE.search("\n".join(dumps.get(name, [])))
        if m:
            out.append("  placement (%s): board v2c %s, completions %s, %s; "
                       "vehicle wire %d, charger side %d"
                       % (name, m.group(2), m.group(1), m.group(3), nv,
                          nv - len(lost)))
        else:
            out.append("  placement (%s): NO RECONCILE line in the dump" % name)
    return out, summ


KINDS = ("listen-only -> normal", "normal -> listen-only")


def judge(summ, phases):
    """Spec 2.3, "A switch loses no frame" (reviewer, 2026-10-22). -> (rc,
    lines): 0 PASS, 1 FAIL, 3 VOID.
      VOID, decided first, when there is nothing to judge: a switch direction
        never placed or with no identifier frame in its windows, the
        identifier absent in either phase, no filler start, or the no-switch
        control losing any IDENTIFIER frame (the control shows the method
        invents no loss; the filler is not part of it).
      FAIL on any identifier frame lost anywhere in the switch phase (the
        +/-5 ms window only attributes a loss to a switch; inside and outside
        are figures), or a start whose first filler frame never reached the
        vehicle wire.
      Figures, not judged here: filler never bridged in the control, and in
        the switch phase apart from the starts' first frames."""
    figs = []
    ps = summ.get("phase_switch", (0, 0, 0, 0, 0))
    pc = summ.get("phase_control", (0, 0, 0, 0, 0))
    inside = sum(summ.get(k, (0, 0, 0))[2] for k in KINDS)
    s0, s1 = phases["switch"]
    miss_sw = sum(1 for t in summ.get("first_missing_t", ()) if s0 <= t <= s1)
    figs.append("FIGURE identifier lost: %d within +/-5 ms of a switch (every "
                "switch, the control's start included), %d in the switch phase "
                "away from any switch" % (inside, ps[2]))
    figs.append("FIGURE filler never bridged (not judged by this case): "
                "control %d of %d; switch phase %d of %d apart from the "
                "starts' first frames" % (pc[4], pc[3], ps[4] - miss_sw, ps[3]))
    void = []
    for kind in KINDS:
        n_sw, n_fr, _n_lo = summ.get(kind, (0, 0, 0))
        if n_sw == 0 or n_fr == 0:
            void.append("VOID: %d %s switch(es) placed, %d identifier frame(s) "
                        "within +/-5 ms of them" % (n_sw, kind, n_fr))
    for ph in ("switch", "control"):
        if summ.get("phase_" + ph, (0,))[0] == 0:
            void.append("VOID: no identifier on the vehicle wire in the %s "
                        "phase" % ph)
    lost_ctl = summ.get("phase_control", (0, 0))[1]
    if lost_ctl:
        void.append("VOID: the no-switch control lost %d identifier frame(s)"
                    % lost_ctl)
    fwd, starts = summ.get("first_forwarded", (0, 0))
    if starts == 0:
        void.append("VOID: no filler start to switch the port")
    if void:
        return 3, figs + void
    fails = []
    for kind in KINDS:
        n_sw, n_fr, n_lo = summ[kind]
        if n_lo:
            fails.append("FAIL: %d identifier frame(s) lost at %d %s switch(es) "
                         "(spec 2.3: a switch loses no frame)"
                         % (n_lo, n_sw, kind))
    # away from every switch window, counted by analyse() per phase (the
    # in-window counts also cover the control start's switch, which lies
    # outside both phase windows, so a difference of the two would not do)
    if ps[2]:
        fails.append("FAIL: %d identifier frame(s) lost in the switch phase "
                     "away from every switch window (spec 2: no loss)" % ps[2])
    if fwd < starts:
        fails.append("FAIL: the first filler frame of %d of %d start(s) never "
                     "reached the vehicle wire (spec 2.3: a switch loses no "
                     "frame)" % (starts - fwd, starts))
    if fails:
        return 1, figs + fails
    return 0, figs + ["PASS: no identifier frame lost in the switch phase "
                      "(%d + %d switches); every start's first filler frame "
                      "reached the vehicle wire"
                      % (summ[KINDS[0]][0], summ[KINDS[1]][0])]


def _synth(lost_at_normal=(), lost_at_listen=(), lost_bg=(), first_not=()):
    """Synthetic: the identifier every 0.5 ms; CYCLES filler starts; losses
    planted near given switches; returns analyse() inputs."""
    veh, chg, serial = [], [], []
    t0 = 100.0
    starts, stops = [], []
    t = t0 + LEAD_S
    q = 0
    board0 = 50.0          # board seconds at host t0
    for c in range(CYCLES):
        starts.append(t)
        tt = t
        nb = 0
        while tt < t + ON_S:
            for j in range(8):
                ts = tt + j * 0.0003
                chg.append((ts, ts, H.FILLER, True, 8, "%08x" % q + "00" * 4,
                            False))
                if not (nb == 0 and j == 0 and c in first_not):
                    veh.append((ts + 0.0002, ts + 0.0002, H.FILLER, True, 8,
                                "%08x" % q + "00" * 4))
                q += 1
            tt += 0.008
            nb += 1
        stops.append(t + ON_S)
        serial.append((t + 0.05, "[%d] PASSTHROUGH  vehicle port mode (a=1 "
                       "normal, a=0 listen-only) a=1 b=1 c=0"
                       % ((t - t0 + board0) * 1000)))
        serial.append((t + 0.05, "vehicle port -> normal (290 us)"))
        tl = t + ON_S + REVERT_S
        serial.append((tl + 0.05, "[%d] PASSTHROUGH  vehicle port mode (a=1 "
                       "normal, a=0 listen-only) a=0 b=2000 c=0"
                       % ((tl - t0 + board0) * 1000)))
        t += ON_S + OFF_S
    s1 = t
    c0 = t + 1.0
    c1 = c0 + CYCLES * (ON_S + OFF_S)
    tt = c0
    while tt < c1:
        for j in range(8):
            ts = tt + j * 0.0003
            chg.append((ts, ts, H.FILLER, True, 8, "%08x" % q + "00" * 4, False))
            veh.append((ts + 0.0002, ts + 0.0002, H.FILLER, True, 8,
                        "%08x" % q + "00" * 4))
            q += 1
        tt += 0.008
    lost = set()
    ts = t0
    k = 0
    while ts < c1 + 0.5:
        h = "%02x%02x%02x" % ((k >> 16) & 255, (k >> 8) & 255, k & 255)
        veh.append((ts, ts, H.ID, True, 3, h))
        drop = False
        for i in lost_at_normal:
            if abs(ts - starts[i]) < 0.00025:
                drop = True
        for i in lost_at_listen:
            if abs(ts - (stops[i] + REVERT_S)) < 0.00025:
                drop = True
        for b in lost_bg:
            if abs(ts - b) < 0.00025:
                drop = True
        if not drop:
            chg.append((ts + 0.001, ts + 0.001, H.ID, True, 3, h, True))
        ts += 0.0005
        k += 1
    phases = {"switch": (t0 + LEAD_S - 0.5, s1), "control": (c0, c1)}
    dumps = {"switch": ["RECONCILE: completions=1 forwarded(v2c)=1 BALANCED"],
             "control": ["RECONCILE: completions=1 forwarded(v2c)=1 BALANCED"]}
    return veh, chg, serial, starts, stops, phases, dumps


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)
    out, s = analyse(*_synth())
    check(s["listen-only -> normal"][2] == 0 and s["normal -> listen-only"][2] == 0
          and s["listen-only -> normal"][0] == CYCLES
          and s["normal -> listen-only"][0] == CYCLES
          and s["first_forwarded"] == (CYCLES, CYCLES),
          "clean: %d switches each way, no loss, every first filler frame "
          "forwarded" % CYCLES)
    out, s = analyse(*_synth(lost_at_normal=(3, 7)))
    check(s["listen-only -> normal"][2] == 2 and s["normal -> listen-only"][2] == 0,
          "can-fail: two frames lost at listen-only -> normal switches are "
          "counted there -> %s" % (s["listen-only -> normal"],))
    out, s = analyse(*_synth(lost_at_listen=(5,)))
    check(s["normal -> listen-only"][2] == 1 and s["listen-only -> normal"][2] == 0,
          "can-fail: a frame lost at a normal -> listen-only switch is counted "
          "there -> %s" % (s["normal -> listen-only"],))
    veh, chg, serial, starts, stops, phases, dumps = _synth()
    bg = phases["control"][0] + 10.0
    out, s = analyse(*_synth(lost_bg=(bg,)))
    check(s["phase_control"][1] == 1 and s["phase_control"][2] == 1
          and s["listen-only -> normal"][2] == 0,
          "can-fail: a frame lost in the control phase is background, not a "
          "switch loss -> %s" % (s["phase_control"],))
    out, s = analyse(*_synth(first_not=(4,)))
    check(s["first_forwarded"] == (CYCLES - 1, CYCLES),
          "can-fail: a start whose first filler frame never reached the "
          "vehicle wire is counted -> %s" % (s["first_forwarded"],))

    # judge() -- spec 2.3 "A switch loses no frame" (reviewer, 2026-10-22)
    def verdict(*a, **k):
        args = _synth(**k) if not a else a
        _o, sm = analyse(*args)
        return judge(sm, args[5])[0], sm, args
    rc, sm, args = verdict()
    check(rc == 0, "judge: clean -> PASS (rc %d)" % rc)
    for kw, what in ((dict(lost_at_normal=(3, 7)), "two lost at listen-only -> "
                      "normal switches"),
                     (dict(lost_at_listen=(5,)), "one lost at a normal -> "
                      "listen-only switch"),
                     (dict(first_not=(4,)), "a start's first filler frame not "
                      "forwarded"),
                     (dict(lost_bg=(args[3][2] + 0.7,)), "one lost in the switch "
                      "phase, away from every switch")):
        rc = verdict(**kw)[0]
        check(rc == 1, "judge can-fail: %s -> FAIL (rc %d)" % (what, rc))
    rc = verdict(lost_bg=(bg,))[0]
    check(rc == 3, "judge: the control loses an identifier frame -> VOID "
          "(rc %d)" % rc)
    rc = verdict(lost_bg=(bg,), lost_at_normal=(3,))[0]
    check(rc == 3, "judge: a control loss VOIDs a switch loss too (rc %d)" % rc)
    a2 = list(_synth())
    a2[2] = [x for x in a2[2] if "a=0" not in x[1]]
    rc = verdict(*a2)[0]
    check(rc == 3, "judge: no normal -> listen-only switch placed -> VOID "
          "(rc %d)" % rc)
    for key, val, what in (
            ("phase_control", (0, 0, 0, sm["phase_control"][3], 0),
             "no identifier in the control phase"),
            ("first_forwarded", (0, 0), "no filler start")):
        s2 = dict(sm)
        s2[key] = val
        rc = judge(s2, args[5])[0]
        check(rc == 3, "judge: %s -> VOID (rc %d)" % (what, rc))
    s2 = dict(sm)
    pc = sm["phase_control"]
    s2["phase_control"] = pc[:4] + (29,)
    rc, ln = judge(s2, args[5])
    check(rc == 0 and any("control 29 of" in x for x in ln),
          "judge: filler lost in the control is a figure, not a verdict "
          "(rc %d)" % rc)
    print("\nbench_switch_loss self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --- the bench ------------------------------------------------------------------

def proc_filler_sched(fgo, ready, rows_path, cycles, on_s, off_s, control_s,
                      fstop=None):
    import bench_pacer
    import bench_rate_feasibility as RF
    import can
    prio = H.above_normal()
    bus = RF.open_gs_usb()
    rows, lock, halt = [], threading.Lock(), threading.Event()

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
    msg = can.Message(arbitration_id=H.FILLER, is_extended_id=True,
                      data=bytearray(8))
    cnt = [0]

    def seq_writer(_k):
        q = cnt[0]
        msg.data = bytearray(((q >> 24) & 255, (q >> 16) & 255,
                              (q >> 8) & 255, q & 255, 0, 0, 0, 0))

    def send():
        bus.send(msg, timeout=0.05)
        cnt[0] += 1
    ready.set()
    fgo.wait()
    starts, stops = [], []
    for _c in range(cycles):
        starts.append(time.perf_counter())
        bench_pacer.offer(send, H.FILLER_RATE, on_s, burst=H.FILLER_BURST,
                          seq_writer=seq_writer)
        stops.append(time.perf_counter())
        time.sleep(off_s)
    c0 = time.perf_counter()
    bench_pacer.offer(send, H.FILLER_RATE, control_s, burst=H.FILLER_BURST,
                      seq_writer=seq_writer)
    c1 = time.perf_counter()
    # keep receiving the forwarded identifier until it has ended (the first
    # bench run stopped here and lost the charger side of its last ~1.4 s)
    if fstop is not None:
        fstop.wait(120)
    time.sleep(0.5)
    halt.set()
    th.join(timeout=2.0)
    with open(rows_path, "w") as f:
        f.write("# t_dev\tt_host\tid\text\tdlc\thex\tis_rx; priority=%s; "
                "SCHEDULE %s\n" % (prio, json.dumps(dict(
                    starts=starts, stops=stops, control=[c0, c1]))))
        for r in rows:
            f.write("%.6f\t%.6f\t%X\t%d\t%d\t%s\t%d\n"
                    % (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))
    bus.shutdown()


def run_bench(a):
    import can
    import bench_usb_host as U
    H.above_normal()
    base = os.path.splitext(a.out)[0]
    out = open(a.out, "w", encoding="utf-8")

    def log(s):
        print(s)
        out.write(s + "\n")
        out.flush()
    switch_s = CYCLES * (ON_S + OFF_S)
    total = LEAD_S + switch_s + 1.0 + switch_s + 1.0
    log("run at %s; identifier 0x%08X at %.0f fps throughout (%.0f s); filler "
        "0x%08X: %d x (on %.1f s, off %.1f s), then on %.0f s (control)"
        % (datetime.datetime.now().isoformat(), H.ID, H.ID_RATE, total,
           H.FILLER, CYCLES, ON_S, OFF_S, switch_s))
    hp = U.HostPort(a.port, base + "_serial.txt")
    kv = can.Bus(interface="kvaser", channel=0, bitrate=H.BITRATE)
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
    fgo, fready, fstop = ctx.Event(), ctx.Event(), ctx.Event()
    pf = ctx.Process(target=proc_filler_sched, args=(
        fgo, fready, base + "_charger_rows.txt", CYCLES, ON_S, OFF_S,
        switch_s, fstop))
    pv = ctx.Process(target=H.proc_identifier, args=(
        go, done, base + "_identifier_sent.json", total))
    pf.start()
    pv.start()
    fready.wait(30)
    time.sleep(1.0)
    dumps = {}

    def dump_now():
        n0 = len(hp.stamped)
        hp.ser.write(b"w")
        hp.ser.flush()
        time.sleep(1.5)
        return [s for _h, s in hp.stamped[n0:]]

    def reset_w(label):
        hp.ser.write(b"W")
        hp.ser.flush()
        return hp.mark("W SENT (%s)" % label)
    go.set()
    time.sleep(LEAD_S - 0.5)
    w_sw = reset_w("switch phase")
    time.sleep(0.5)
    fgo.set()
    time.sleep(switch_s + 0.5)
    dumps["switch"] = dump_now()
    w_ct = reset_w("control phase")
    done.wait(total + 60)
    time.sleep(0.5)
    fstop.set()
    dumps["control"] = dump_now()
    pf.join(60)
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
    head = open(base + "_charger_rows.txt").readline()
    sched = json.loads(head.split("SCHEDULE ", 1)[1])
    chg = H.read_rows(base + "_charger_rows.txt", chg=True)
    sent = json.load(open(base + "_identifier_sent.json"))
    log("identifier sender: %s" % json.dumps(sent))
    phases = {"switch": (w_sw, sched["control"][0] - 0.2),
              "control": (sched["control"][0] + 0.1, sched["control"][1])}
    for k in ("switch", "control"):
        log("dump (%s), first lines:" % k)
        for s in dumps[k][:5]:
            log("    | %s" % s)
    lines, summ = analyse(veh, chg, serial, sched["starts"], sched["stops"],
                          phases, dumps, more_starts=[sched["control"][0]])
    for s in lines:
        log(s)
    rc, verdict = judge(summ, phases)
    for s in verdict:
        log(s)
    out.close()
    return rc


def reanalyse(base):
    """Re-run analyse() on a saved run's files (base = --out without .txt)."""
    import bench_hold12 as HH
    veh = HH.read_rows(base + "_vehicle_rows.txt")
    chg = HH.read_rows(base + "_charger_rows.txt", chg=True)
    head = open(base + "_charger_rows.txt").readline()
    sched = json.loads(head.split("SCHEDULE ", 1)[1])
    serial = []
    for ln in open(base + "_serial.txt", encoding="utf-8", errors="replace"):
        h, _, t = ln.rstrip("\n").partition("\t")
        try:
            serial.append((float(h), t))
        except ValueError:
            pass
    w = [h for h, t in serial if t.startswith("# W SENT (switch")]
    phases = {"switch": (w[0], sched["control"][0] - 0.2),
              "control": (sched["control"][0] + 0.1, sched["control"][1])}
    lines, summ = analyse(veh, chg, serial, sched["starts"], sched["stops"],
                          phases, {}, more_starts=[sched["control"][0]])
    lines += judge(summ, phases)[1]
    cs = set(H.seq_of(r[5]) for r in chg if r[2] == H.ID and r[3] and r[6])
    last = max(cs) if cs else -1
    vs = [H.seq_of(r[5]) for r in veh if r[2] == H.ID and r[3]]
    lost = sorted(q for q in set(vs) if q <= last and q not in cs)
    lines.append("over the whole charger-side record (seq 0..%d): %d lost, seq %s"
                 % (last, len(lost), lost))
    return lines


# --can-fail: each edit must make --self-test fail (script_can_fail.py)
MUTANTS = [
    ("in-window loss not judged",
     "        if n_lo:\n", "        if False:\n"),
    ("loss away from the switches not judged",
     "    if ps[2]:\n", "    if False:\n"),
    ("first filler frame not judged",
     "    if fwd < starts:\n", "    if False:\n"),
    ("a control loss is not VOID",
     "    if lost_ctl:\n", "    if False:\n"),
    ("VOID only without a switch loss",
     "    if void:\n        return 3", "    if void and not inside:\n        return 3"),
    ("an empty switch direction is not VOID",
     "        if n_sw == 0 or n_fr == 0:\n", "        if False:\n"),
    ("an identifier-less phase is not VOID",
     "        if summ.get(\"phase_\" + ph, (0,))[0] == 0:\n", "        if False:\n"),
    ("no filler start is not VOID",
     "    if starts == 0:\n", "    if False:\n"),
    ("the control's filler loss judged",
     "    if ps[2]:\n", "    if ps[2] or pc[4]:\n"),
    ("the control's filler figure lost",
     "% (pc[4], pc[3], ps[4] - miss_sw, ps[3])",
     "% (0, pc[3], ps[4] - miss_sw, ps[3])"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run --self-test against each MUTANTS edit")
    ap.add_argument("--reanalyse", metavar="BASE", default=None)
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if a.reanalyse:
        for ln in reanalyse(a.reanalyse):
            print(ln)
        return 0
    if not a.out:
        ap.error("--out is required")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
