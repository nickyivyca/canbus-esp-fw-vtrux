#!/usr/bin/env python3
"""Mutants of witness_anchor.py, bench_witness.py and x2_jammer.py's session and can2-isolation
parts (the gen-inhibit reviewer's ruling (C), 2026-11-13, and its ruling (3) the same day: a
fresh PCAN open per train, one anchor per open); test_jammer_witnesses.py must fail on
each BY A CHECK. A crash is not a kill, and an edit that does not apply is an error.

    py -3.13 -B gen-inhibit/tools/bench/test_jammer_witnesses_mutants.py

Each mutant is a copy of the three sources, the test and the modules they import in the
worktree's scratch/, one edit applied to one file. The control, the unmutated copy, must pass
with no crash first. The ruling names two of them: the offset shifted 0.3 s late must VOID
(m1, m2), and a can2-isolation judge that judges a window on a Kvaser stamp must be caught
(the "window on a stamp" mutants). Ruling (3) names three: the witness opened at the train's
start, a lead over the 0.4 s bound, and a check judged on another open's anchor (the "train:"
and "per open:" mutants). The take from f0c6e03d (2026-11-13 18:31) adds the state frame read
by its data[1]: the PCAN's "error active" frame at each open must not put it down, warning,
passive, an overflow, unspecified or bus-off must, and a NOT-RUN train keeps its drive in the
record.

Not mutants (equivalent, so no test can kill them): dropping anchor()'s own "maps after its
receipt" check alone (with the offset taken as the minimum no frame can map after its receipt;
the check is there for an offset taken otherwise, which m1-m3 show VOIDs); dropping the
witness-down check before each check alone (the check at the session's end makes every driven
check NOT-RUN the same way); grouping by a gap of 0.5 s instead of GROUP_GAP_S (the same groups
on one-frame pulses 1 s apart; the gap's bounds are checked by value); judging a session's check
on every frame of the session rather than those mapped into its span (since the closing pulses,
every judge bounds its own windows by the check's times, so the span is a second fence: it was
killed while the timeout's window after its stop had no end). Gen-inhibit tester.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = "test_jammer_witnesses.py"
COPIES = ("witness_anchor.py", "bench_witness.py", "x2_jammer.py", "gi_paths.py", "witness_clock.py")
WA, BWF, XJ = "witness_anchor.py", "bench_witness.py", "x2_jammer.py"
MUTANTS = [
    # the anchor (P1 (1)-(3))
    (WA, "the offset 0.3 s late (the ruling's mutant)", "    off = min(r - d for d, r in points)\n",
     "    off = min(r - d for d, r in points) + 0.3\n"),
    (WA, "mapped() 0.3 s late", '    return t + anc["offset_s"]\n', '    return t + anc["offset_s"] + 0.3\n'),
    (WA, "the offset the median, not the minimum", "    off = min(r - d for d, r in points)\n",
     "    off = _median([r - d for d, r in points])\n"),
    (WA, "spread bound at 2 s", "SPREAD_P99_MAX_S = 0.25\n", "SPREAD_P99_MAX_S = 2.0\n"),
    (WA, "a spread at the bound accepted", '    elif not anc["spread_p99_s"] < SPREAD_P99_MAX_S:\n',
     '    elif anc["spread_p99_s"] > SPREAD_P99_MAX_S:\n'),
    (WA, "drift never VOIDs", '    elif anc["drift_s"] > DRIFT_MAX_S:\n', "    elif False:\n"),
    (WA, "the slope fitted on every frame, not the envelope", "    env = envelope(points)\n",
     "    env = [(d, r - d) for d, r in points]\n"),
    (WA, "any number of frames anchors", "    if len(points) < MIN_POINTS:\n", "    if len(points) < 1:\n"),
    (WA, "COOKED stamps accepted", "    if abs(lag) < EPOCH_LAG_S:\n", "    if False:\n"),
    (WA, "the device clock going back accepted", "    if back:\n        anc[", "    if False:\n        anc["),
    (WA, "two opens accepted", "    if opens != 1:\n", "    if False:\n"),
    (WA, "a replug accepted", "    if not same_device:\n", "    if False:\n"),
    (WA, "bracket: no lower bound (the send)", "        if not (t_send < m <= top):\n",
     "        if not (m <= top):\n"),
    (WA, "bracket: the frame's own receipt not a bound", "        top = r if t_done is None else min(r, t_done)\n",
     "        top = r if t_done is None else t_done\n"),
    (WA, "bracket: the DONE's receipt not a bound", "        top = r if t_done is None else min(r, t_done)\n",
     "        top = r\n"),
    # the bracket widened (reviewer, 2026-11-13): the reviewer's mutant, the old window
    (WA, "bracket: only frames received by the DONE (the window before the widening)",
     "    mine = [(d, r) for d, r in frames if t_send <= r < t_next]\n",
     "    mine = [(d, r) for d, r in frames if t_send <= r <= (t_next if t_done is None else t_done)]\n"),
    # the PCAN witness
    (BWF, "pcan: ts_mode not checked", '    if ident.get("ts_mode") != PCAN_TS_MODE:\n', "    if False:\n"),
    (BWF, "pcan: any bus class", '    if "Pcan" not in cls:\n', "    if False:\n"),
    (BWF, "pcan: any channel", '    if getattr(bus, "channel_info", None) != PCAN_CHANNEL:\n', "    if False:\n"),
    (BWF, "pcan: any adapter",
     '    if not ident.get("adapter_name") or not ident["adapter_name"].startswith("PCAN-USB"):\n', "    if False:\n"),
    (BWF, "pcan: nom_bitrate not checked", '    if ident.get("nom_bitrate") != str(BITRATE):\n', "    if False:\n"),
    (BWF, "pcan: the refused bus left open",
     "            try:\n                self.bus.shutdown()\n            finally:\n                raise WitnessRefused(why)\n",
     "            raise WitnessRefused(why)\n"),
    (BWF, "pcan: state frames counted as bus errors", "                            if is_bus_error(m.arbitration_id):\n",
     "                            if True:\n"),
    (BWF, "pcan: a failed read not kept", "                    m = self.bus.recv(timeout=0.0)\n        except Exception as e:",
     "                    m = self.bus.recv(timeout=0.0)\n        except ZeroDivisionError as e:"),
    (BWF, "pcan: same_device ignores the USB path", '        return all(a.get(k) == b.get(k) for k in ("adapter_name", "dev", "usb_path"))\n',
     '        return all(a.get(k) == b.get(k) for k in ("adapter_name", "dev"))\n'),
    # the Kvaser on SocketCAN
    (BWF, "netdev: any name", "    if iface != KVASER_IFACE:\n", "    if False:\n"),
    (BWF, "netdev: a vcan (no device) passes the device check", "    if not os.path.exists(drv):\n", "    if False:\n"),
    (BWF, "netdev: any driver", "    if name != KVASER_DRIVER:\n", "    if False:\n"),
    (BWF, "netdev: a link down accepted", '    if _read(os.path.join(base, "operstate")) != "up":\n', "    if False:\n"),
    (BWF, "netdev: any netdev type", "    if _read(os.path.join(base, \"type\")) != ARPHRD_CAN:\n", "    if False:\n"),
    (BWF, "socketcan: ENETDOWN not kept", "            if not self.stop_flag.is_set():\n                self.failed",
     "            if False:\n                self.failed"),
    (BWF, "socketcan: frames not tagged with the phase",
     "                    self.log.append((self.phase, now, sw, cid, payload.hex()))\n",
     '                    self.log.append(("open", now, sw, cid, payload.hex()))\n'),
    (BWF, "the gap under 10x the jitter", "GROUP_GAP_S = 0.1\n", "GROUP_GAP_S = 0.005\n"),
    (BWF, "the shortest spacing under 5x the gap", "MIN_INTERVAL_MS = 500\n", "MIN_INTERVAL_MS = 200\n"),
    (BWF, "groups: split at any gap", "        if out and t - out[-1][-1] <= GROUP_GAP_S:\n",
     "        if out and t - out[-1][-1] <= 0.0:\n"),
    (BWF, "pcan_holders: any device", '            if tgt.startswith("/dev/pcan"):\n', '            if tgt.startswith("/dev/"):\n'),
    (BWF, "pcan_holders: this process counted", "        if not d.isdigit() or int(d) in skip:\n",
     "        if not d.isdigit():\n"),
    # x2_jammer: can2_counts (counts only, no window on a Kvaser stamp)
    (XJ, "can2: the bus errors chosen by a window on their stamps (the ruling's mutant)",
     '    errs = [e for e in wit_frames if e[0] == "pulses" and e[3] & BW.CAN_ERR_FLAG and BW.is_bus_error(e[3])]\n',
     '    errs = [e for e in wit_frames if e[2] is not None and abs(e[2] - e[1]) < 0.3 and e[3] & BW.CAN_ERR_FLAG'
     ' and BW.is_bus_error(e[3])]\n'),
    (XJ, "can2: the 0x7A0 counted by a window on their stamps",
     "                  and (e[3] & BW.CAN_ERR_MASK) == 0x7A0)\n",
     "                  and (e[3] & BW.CAN_ERR_MASK) == 0x7A0 and abs((e[2] or 0) - e[1]) < 0.3)\n"),
    (XJ, "can2: state frames grouped", '    errs = [e for e in wit_frames if e[0] == "pulses" and e[3] & BW.CAN_ERR_FLAG and BW.is_bus_error(e[3])]\n',
     '    errs = [e for e in wit_frames if e[0] == "pulses" and e[3] & BW.CAN_ERR_FLAG]\n'),
    (XJ, "can2: the PCAN phase's error frames grouped",
     '    errs = [e for e in wit_frames if e[0] == "pulses" and e[3] & BW.CAN_ERR_FLAG and BW.is_bus_error(e[3])]\n',
     '    errs = [e for e in wit_frames if e[0] in ("pcan", "pulses") and e[3] & BW.CAN_ERR_FLAG and BW.is_bus_error(e[3])]\n'),
    (XJ, "can2: an unstamped bus error tolerated", '            "n_groups": len(gs) if not unstamped else -1,',
     '            "n_groups": len(gs),'),
    # x2_jammer: the session
    (XJ, "session: the anchor's VOID ignored", '        if anc["void"]:\n            c["verdict"] = "VOID: anchor of open',
     '        if False:\n            c["verdict"] = "VOID: anchor of open'),
    (XJ, "session: the brackets not folded", "        fold_bracket(c, br)\n", ""),
    (XJ, "session: every command's frames up to the check's end",
     '        br = [WA.bracket(anc, points, t_send, t_done, cmds[i + 1][0] if i + 1 < len(cmds) else inp["t_end"])\n',
     '        br = [WA.bracket(anc, points, t_send, t_done, inp["t_end"])\n'),
    (XJ, "fold_bracket: a FAIL turned into a VOID", '    if c.get("verdict") != "FAIL":\n        c["verdict_before_bracket"]',
     '    if True:\n        c["verdict_before_bracket"]'),
    (XJ, "fold_bracket: a command with a DONE and no frame passes",
     '    empty = [b["t_send"] for b in br if b["t_done"] is not None and b["n"] == 0]\n', "    empty = []\n"),
    (XJ, "session: a down open's checks judged anyway", '        if o["down"]:\n', "        if False:\n"),
    (XJ, "session: can2-isolation let into the session", '        if words and words[0] == "can2-isolation":\n',
     "        if False:\n"),
    (XJ, "session: no resource-log window", '        c["resource_log"] = resource(drv(c)["t_begin"], drv(c)["t_end"])\n',
     '        c["resource_log"] = {"window": [None, None]}\n'),
    (XJ, "session: the witness not named", '        c = {"spec": spec, "sub": ns.sub, "ns": ns, "witness": WITNESS_PCAN}\n',
     '        c = {"spec": spec, "sub": ns.sub, "ns": ns, "witness": WITNESS_KVASER_CANLIB}\n'),
    (XJ, "args: can2-isolation at any spacing", '    if a.sub == "can2-isolation" and a.interval_ms < MIN_INTERVAL_MS:\n',
     "    if False:\n"),
    (XJ, "args: --check anywhere", '    if a.check and a.sub != "session":\n', "    if False:\n"),
    # the USB record (gen-inhibit reviewer, 2026-11-13); "a made-up journal line must VOID"
    (XJ, "usb: any hub", 'USB_DEV_RE = re.compile(r"(?<![\\d.])1-3\\.2(?:\\.\\d+)*(?!\\d)")\n',
     'USB_DEV_RE = re.compile(r"1-3\\.")\n'),
    (XJ, "usb: -71 not an error word", 'USB_BAD_RE = re.compile(r"EPROTO|-71\\b|\\breset\\b|disconnect", re.I)\n',
     'USB_BAD_RE = re.compile(r"EPROTO|\\breset\\b|disconnect", re.I)\n'),
    (XJ, "usb: a disconnect not an error word", 'USB_BAD_RE = re.compile(r"EPROTO|-71\\b|\\breset\\b|disconnect", re.I)\n',
     'USB_BAD_RE = re.compile(r"EPROTO|-71\\b|\\breset\\b", re.I)\n'),
    (XJ, "usb: a line found never VOIDs", '    if lines and v != "NOT-RUN" and not v.startswith("REFUSED"):\n', "    if False:\n"),
    (XJ, "usb: an unreadable kernel log is clean", '        lines = ["kernel log not read: %s" % record["error"]]\n',
     "        lines = []\n"),
    (XJ, "usb: lines outside the window VOID", "        lines = [ln for t, ln in record.get(\"hits\", []) if lo <= t <= hi]\n",
     "        lines = [ln for t, ln in record.get(\"hits\", [])]\n"),
    (XJ, "usb: the session never folds it", '        usb_fold(c, rec_usb, drv(c)["t_begin"], drv(c)["t_end"])\n', ""),
    # a fresh open per train, one anchor per open (the gen-inhibit reviewer, 2026-11-13); the ruling
    # names three: the witness opened at the train's start, a lead over the bound, a check judged on
    # another open's anchor
    (XJ, "train: the witness opened at the train's start (the ruling's)",
     "    return max(t_send, t_send + eff_ms / 1000.0 - LEAD_S)\n", "    return t_send\n"),
    (XJ, "train: the lead 0.5 s (the ruling's)", "LEAD_S = 0.4\n", "LEAD_S = 0.5\n"),
    (XJ, "train: the lead's bound not checked", "    if LEAD_S > LEAD_MAX_S:\n", "    if False:\n"),
    (XJ, "train: the pulses in the lead not counted", "    if ns.p <= 0 or LEAD_S * 1e6 / ns.p > LEAD_PULSES_MAX:\n",
     "    if ns.p <= 0:\n"),
    (XJ, "train: the lead not checked in a session",
     '        why = args_why(ns) or (lead_why(ns) if ns.sub == "timeout" else None)\n', "        why = args_why(ns)\n"),
    (XJ, "train: on the shared open", '        if ns.sub == "timeout":\n', "        if False:\n"),
    (XJ, "train: judged as if its start was watched", "                   start_observed=False)\n",
     "                   start_observed=True)\n"),
    (XJ, "judge_stop: the start never judged", "    elif (start_observed and obs_start > t_start - pre_s) or",
     "    elif (False and obs_start > t_start - pre_s) or"),
    (XJ, "judge_stop: a late open judged on its start", "    elif (start_observed and obs_start > t_start - pre_s) or",
     "    elif (obs_start > t_start - pre_s) or"),
    (XJ, "train: its open's down never read", "                _close_open(got[0])\n",
     '                got[0]["wit"].close()\n                got[0]["down"], got[0]["t_fail"] = None, None\n'),
    (XJ, "per open: a check judged on another open's anchor (the ruling's)",
     '        outside += judge_session(o["checks"], pts, anc, clock, o["id"])\n',
     '        outside += judge_session(o["checks"], pts, opens[0]["anchor"], clock, o["id"])\n'),
    (XJ, "per open: every check on open 0's frames and anchor",
     '        outside += judge_session(o["checks"], pts, anc, clock, o["id"])\n',
     '        outside += judge_session(o["checks"], opens[0]["wit"].points(), opens[0]["anchor"], clock, o["id"])\n'),
    (XJ, "per open: a check not named with its open's anchor", '            c["anchor"] = "open %d" % o["id"]\n',
     "            pass\n"),
    (XJ, "down: every check of the open, whenever it failed",
     '                if "inp" in c and (o["t_fail"] is None or c["inp"]["t_end"] >= o["t_fail"]):\n',
     '                if "inp" in c:\n'),
    (XJ, "down: only the checks begun after it failed",
     '                if "inp" in c and (o["t_fail"] is None or c["inp"]["t_end"] >= o["t_fail"]):\n',
     '                if "inp" in c and (o["t_fail"] is None or c["inp"]["t_begin"] >= o["t_fail"]):\n'),
    (XJ, "down: a down with no time judged",
     '                if "inp" in c and (o["t_fail"] is None or c["inp"]["t_end"] >= o["t_fail"]):\n',
     '                if "inp" in c and (o["t_fail"] is not None and c["inp"]["t_end"] >= o["t_fail"]):\n'),
    (BWF, "pcan: a state frame not WitnessDown",
     '                            if state_down(m.arbitration_id, getattr(m, "data", None)) and self.failed is None:\n',
     "                            if False:\n"),
    (BWF, "pcan: bus-off not a state frame", "    if aid & CAN_ERR_BUSOFF:\n        return True\n", ""),
    # by data[1] (the take from f0c6e03d, 2026-11-13: the PCAN's "error active" frame at each open)
    (BWF, "pcan: every CAN_ERR_CRTL down (error active too)",
     "    return not (len(d) > 1 and d[1] == CAN_ERR_CRTL_ACTIVE)\n", "    return True\n"),
    (BWF, "pcan: down on warning or passive only (an overflow, unspecified, no data not)",
     "    return not (len(d) > 1 and d[1] == CAN_ERR_CRTL_ACTIVE)\n", "    return len(d) > 1 and bool(d[1] & 0x3C)\n"),
    (BWF, "pcan: the active bit masks a warning beside it",
     "    return not (len(d) > 1 and d[1] == CAN_ERR_CRTL_ACTIVE)\n",
     "    return not (len(d) > 1 and d[1] & CAN_ERR_CRTL_ACTIVE)\n"),
    (BWF, "pcan: no data[1] taken as error active",
     "    return not (len(d) > 1 and d[1] == CAN_ERR_CRTL_ACTIVE)\n",
     "    return not (len(d) < 2 or d[1] == CAN_ERR_CRTL_ACTIVE)\n"),
    (BWF, "pcan: the frame's data not passed",
     '                            if state_down(m.arbitration_id, getattr(m, "data", None)) and self.failed is None:\n',
     "                            if state_down(m.arbitration_id, None) and self.failed is None:\n"),
    (XJ, "down: a NOT-RUN check's drive dropped from the record", '                    c["inp_unjudged"] = c.pop("inp")\n',
     '                    del c["inp"]\n'),
]


def run(base, label, texts):
    """Copies (texts: {file: text}) and the test in base/label; the test's FAIL lines, crash, exit."""
    d = os.path.join(base, label)
    gi_paths.remove_tree(d)
    os.makedirs(d)
    for f in COPIES:
        if f in texts:
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(texts[f])
        else:
            shutil.copy(os.path.join(HERE, f), d)
    shutil.copy(os.path.join(HERE, TEST), d)
    r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
    fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL ")]
    crash = r.stderr.strip().splitlines()[-1][:120] if "Traceback" in r.stderr else ""
    return fails, crash, r.returncode


def main():
    base = gi_paths.scratch("mutants-jammer-witnesses")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read().replace("\r\n", "\n")
            for f in (WA, BWF, XJ)}
    try:
        fails, crash, rc = run(base, "control", {})
        control_ok = rc == 0 and not fails and not crash
        print("%s control: the unmutated copy  (exit %d, %d FAIL%s)" % (
            "PASS    " if control_ok else "FAILED  ", rc, len(fails), ", CRASHED: " + crash if crash else ""))
        killed = errors = 0
        for n, (f, name, old, new) in enumerate(MUTANTS, 1):
            if srcs[f].count(old) != 1:
                print("ERROR    m%d %s (%s): edit matched %d times" % (n, name, f, srcs[f].count(old)))
                errors += 1
                continue
            fails, crash, rc = run(base, "m%d" % n, {f: srcs[f].replace(old, new)})
            killed += bool(fails)
            print("%s m%d %s (%s)  (exit %d, %d FAIL%s%s)" % (
                "KILLED  " if fails else "SURVIVED", n, name, f, rc, len(fails),
                ", CRASHED: " + crash if crash else "", ("; " + fails[0][:90]) if fails else ""))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
