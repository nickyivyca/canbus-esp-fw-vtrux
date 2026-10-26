#!/usr/bin/env python3
"""Judge going live with a backlog against the SPEC, not against a golden.

Gen-inhibit tester, 2026-10-12. Spec 7 gate condition 9 (added 2026-10-10,
user): "The receive queue is drained: the frame the worker has just taken was
the last one waiting." Spec 12.4 row "no stale answers on going live": "the
first inhibit frame after going live answers the VCM's newest 0x051, never one
it has already passed", with host cover from "a case with frames waiting at
go-live".

WHAT THE HOST CAN SHOW. With `load` on, host_runner sets gi_bus_t.rx_waiting
at each dequeue to the frames still queued behind the one taken, and prints
`<t> RXW n=<k>` whenever that value changes. The scenario builds the backlog
(golive-backlog-held: a preemption that leaves the 0x617 the gate is waiting
for with two VCM commands queued behind it). What the device's driver reports
as waiting, and when the shim reads it, are device-only (the shim suite and
the bench).

THE JUDGEMENT, per row:
  FAIL          the first inhibit frame after going live does not carry the
                counter of the newest 0x051 the scenario had presented by then,
                plus one (spec 4's stolen counter) -- it answers a command the
                VCM has already passed; or the device went live while the
                rx_waiting in force (the last RXW line before the live STATE
                line, in trace order) was not zero;
  INCONCLUSIVE  never live, no inhibit frame after going live, or no
                "receive queue not drained" hold before going live -- nothing
                was waiting, so the case could not fail;
  PASS          otherwise.
A FAIL outranks an INCONCLUSIVE: a core without condition 9 shows no hold AND
answers a passed command, and the answer is the finding.

    make && python3 make_scenarios.py && python3 drained_check.py
    python3 drained_check.py --selftest
    python3 drained_check.py --control <main/ of a core without condition 9>
The control builds host_runner with -DGI_NO_RX_WAITING against that core and
requires every row to FAIL. Exit status 0 only if every row PASSes (or, with
--control, every row FAILs).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "host_runner")
SCN = os.path.join(HERE, "scenarios")
ROWS = ("golive-backlog-held",)
HOLD = "receive queue not drained"

STATE_RE = re.compile(r"^(\d+) STATE mode=\d+ live=(\d) block=(.*?) abort=")
RXW_RE = re.compile(r"^(\d+) RXW n=(\d+)")
INH_RE = re.compile(r"^(\d+) TX INHIBIT id=051 dlc=6 data=([0-9A-Fa-f]{12})")


def vcm_frames(scn_lines):
    """[(t, counter)] for every 0x051 the scenario presents, in time order."""
    out = []
    for ln in scn_lines:
        p = ln.split()
        if len(p) >= 5 and p[0] == "f" and p[2].upper() == "051":
            data = bytes.fromhex(p[4])
            if len(data) >= 6:
                out.append((int(p[1]), data[5] & 0x0F))
    out.sort(key=lambda x: x[0])
    return out


def judge(scn_lines, trace_lines):
    """(verdict, detail)."""
    vcm = vcm_frames(scn_lines)
    rxw = 0
    rxw_max = 0
    t_hold = None
    t_live = None
    rxw_at_live = None
    first = None
    # host_runner prints a call's TX lines before the STATE line that follows
    # it, so an inhibit from the same call that went live precedes "live=1".
    pending = []
    for ln in trace_lines:
        m = RXW_RE.match(ln)
        if m:
            rxw = int(m.group(2))
            rxw_max = max(rxw_max, rxw)
            continue
        m = STATE_RE.match(ln)
        if m and t_live is None:
            if m.group(2) == "1":
                t_live, rxw_at_live = int(m.group(1)), rxw
                if pending:
                    first = pending[0]
            elif m.group(3) == HOLD and t_hold is None:
                t_hold = int(m.group(1))
            pending = []
            continue
        m = INH_RE.match(ln)
        if m and first is None:
            frame = (int(m.group(1)), int(m.group(2)[10:12], 16) & 0x0F)
            if t_live is None:
                pending.append(frame)
            else:
                first = frame
    if t_live is None:
        return "INCONCLUSIVE", "never live"
    bad = []
    if rxw_at_live:
        bad.append("live at t=%.6f s with rx_waiting=%d" % (t_live / 1e6, rxw_at_live))
    if first is not None:
        t_tx, c = first
        seen = [x for x in vcm if x[0] <= t_tx]
        if not seen:
            bad.append("first inhibit at t=%.6f s precedes every 0x051" % (t_tx / 1e6))
        else:
            t_v, v = seen[-1]
            if c != (v + 1) & 0x0F:
                bad.append("first inhibit at t=%.6f s carries counter %d; the newest "
                           "0x051 (t=%.6f s) has %d, so it answers one already passed"
                           % (t_tx / 1e6, c, t_v / 1e6, v))
    if bad:
        return "FAIL", "; ".join(bad)
    if first is None:
        return "INCONCLUSIVE", "no inhibit frame after going live"
    if t_hold is None:
        return "INCONCLUSIVE", "no '%s' hold before going live (rx_waiting max %d)" % (HOLD, rxw_max)
    return "PASS", ("held %.3f ms (rx_waiting max %d), live at t=%.6f s, first inhibit "
                    "counter %d answers the newest 0x051"
                    % ((t_live - t_hold) / 1e3, rxw_max, t_live / 1e6, first[1]))


# ------------------------------------------------------------------ selftest --
def _scn(ctrs):
    """0x051 at 1000, 2000, ... us with the given counters."""
    return ["mode 0 3 500"] + ["f %d 051 6 0800807FFF%02X" % (1000 * (i + 1), c)
                               for i, c in enumerate(ctrs)]


def _tr(*lines):
    return list(lines)


def selftest():
    st = "%d STATE mode=3 live=%d block=%s abort= latched=0"
    hold = st % (2100, 0, HOLD)
    live = st % (2300, 1, "")
    inh = "%d TX INHIBIT id=051 dlc=6 data=0800807FFF%02X ok=1"
    cases = [
        ("held, drained, answers the newest", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, "2200 RXW n=0", live, inh % (3000, 6)), "PASS"),
        ("RXW n=0 on the same microsecond as live (trace order rules)", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, "2300 RXW n=0", live, inh % (3000, 6)), "PASS"),
        ("counter wraps 15 -> 0", _scn([14, 15]),
         _tr("1100 RXW n=1", st % (1100, 0, HOLD), "1200 RXW n=0", st % (1200, 1, ""),
             inh % (2000, 0)), "PASS"),
        ("live with frames waiting", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, live, "2400 RXW n=0", inh % (3000, 6)), "FAIL"),
        ("first inhibit answers a passed command", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, "2200 RXW n=0", live, inh % (3000, 5)), "FAIL"),
        ("first inhibit answers a command two behind", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, "2200 RXW n=0", live, inh % (3000, 4)), "FAIL"),
        ("a 0x051 arriving after the answer is not the newest", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, "2200 RXW n=0", live, inh % (2500, 5)), "PASS"),
        ("no hold, stale answer (a core without condition 9)", _scn([3, 4, 5]),
         _tr(live, inh % (3000, 5)), "FAIL"),
        ("no hold, fresh answer: could not fail", _scn([3, 4, 5]),
         _tr(live, inh % (3000, 6)), "INCONCLUSIVE"),
        ("never live", _scn([3, 4, 5]), _tr("2100 RXW n=2", hold), "INCONCLUSIVE"),
        ("live, no inhibit after it", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, "2200 RXW n=0", live), "INCONCLUSIVE"),
        ("an inhibit BEFORE live is not the first after it", _scn([3, 4, 5]),
         _tr(inh % (1500, 4), "2100 RXW n=2", hold, "2200 RXW n=0", live, inh % (3000, 5)),
         "FAIL"),
        ("only the first inhibit is judged", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, "2200 RXW n=0", live, inh % (3000, 6), inh % (3001, 5)),
         "PASS"),
        ("an inhibit printed in the call that went live is the first", _scn([3, 4, 5]),
         _tr("2100 RXW n=2", hold, "2200 RXW n=0", inh % (2300, 4), live, inh % (3000, 6)),
         "FAIL"),
        ("a hold AFTER live does not count as the hold", _scn([3, 4, 5]),
         _tr(live, st % (2400, 0, HOLD), inh % (3000, 6)), "INCONCLUSIVE"),
    ]
    fails = 0
    for label, scn, tr, want in cases:
        got, detail = judge(scn, tr)
        ok = got == want
        fails += not ok
        print("  %-4s %-58s want %-12s got %-12s %s" % ("ok" if ok else "FAIL", label, want, got,
                                                       "" if ok else detail))
    print("selftest: %s" % ("PASS" if not fails else "FAIL (%d)" % fails))
    return 1 if fails else 0


def build_control(coredir):
    """host_runner without rx_waiting, against another core. Returns its path."""
    out = os.path.join(HERE, "host_runner_ctl")
    cmd = [os.environ.get("CC", "gcc"), "-std=c11", "-O2", "-DGI_NO_RX_WAITING",
           "-I" + coredir, "-o", out, os.path.join(HERE, "host_runner.c"),
           os.path.join(coredir, "gen_inhibit_core.c")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stdout.write(r.stderr)
        raise SystemExit("control build failed")
    return out


def main():
    if "--selftest" in sys.argv:
        return selftest()
    runner, want = RUNNER, "PASS"
    if "--control" in sys.argv:
        runner, want = build_control(sys.argv[sys.argv.index("--control") + 1]), "FAIL"
        print("# control: %s, every row must FAIL" % runner)
    worst = 0
    for name in ROWS:
        path = os.path.join(SCN, name + ".scn")
        if not os.path.exists(path):
            print("[ERROR       ] %-24s no scenario; run make_scenarios.py" % name)
            worst = 1
            continue
        with open(path) as fh:
            scn = [ln for ln in fh.read().splitlines() if ln and not ln.startswith("#")]
        with open(path) as fh:
            r = subprocess.run([runner], stdin=fh, capture_output=True, timeout=300)
        if r.returncode != 0:
            print("[ERROR       ] %-24s host_runner rc %d" % (name, r.returncode))
            worst = 1
            continue
        verdict, detail = judge(scn, r.stdout.decode("ascii", "replace").splitlines())
        print("[%-12s] %-24s %s" % (verdict, name, detail))
        if verdict != want:
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
