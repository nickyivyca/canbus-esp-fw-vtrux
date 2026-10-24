#!/usr/bin/env python3
"""Tests x2_jam_cases.judge_busoff_rearm and split_0x051 (spec 7 bus-off re-arm).

    py -3.14 test_busoff_rearm_judge.py      # exits 1 on any failure

Synthetic, no hardware. One arm is staged: live, jam at 100 s for 3 s, the page
bus_off from 100.4 s, 30 s with no arm, a direct re-arm at 133 s, 30 s after.
Each verdict has a case, and each precondition a case that trips it: no
bus_off, too few readings, a recovery with no arm (also a device 0x051 with no
arm while the page reads bus_off), the replay not running after the re-arm, a
reply other than 200, the old build coming back, the new build running but
not live.
"""

import sys

import x2_jam_cases as C

FAILS = []
PH = {"arm_posted": 90.0, "jam_start": 100.0, "jam_stop": 103.0,
      "rearm_posted": 133.0, "rearm_end": 163.0}


def check(label, cond, detail=""):
    print("%s  %s%s" % ("PASS" if cond else "FAIL", label, ("  (%s)" % (detail,)) if detail != "" else ""))
    if not cond:
        FAILS.append(label)


def s(state="bus_off", bus_on=False, live=False, up=None, mode=3, self_off=""):
    return {"drv_state": state, "bus_on": bus_on, "inhibit_live": live, "uptime_ms": up,
            "mode": mode, "self_off": self_off}


def samples(after="bus_off", recover_noarm_at=None, run_from=None, live_from=None, every=1.0,
            boff_from=100.4, gap=None, reboot_at=None, refused=False):
    out, t = [], 91.0
    while t < 163.0:
        if gap and gap[0] <= t < gap[1]:
            t += every
            continue
        if t < boff_from:
            sn = s("running", True, True)
        elif t < PH["rearm_posted"]:
            sn = s("running", True) if (recover_noarm_at and t >= recover_noarm_at) else s()
        else:
            if run_from is not None and t >= run_from:
                sn = s("running", True, live_from is not None and t >= live_from)
            else:
                sn = s(after)
        sn["uptime_ms"] = int((t - 91.0) * 1000) + 500000
        if reboot_at is not None and t >= reboot_at:
            sn["uptime_ms"] = int((t - reboot_at) * 1000) + 300
            sn.update(mode=0, drv_state=None, bus_on=None, inhibit_live=False)
        if refused and t >= PH["rearm_posted"]:
            sn.update(mode=0, self_off="quiesce")
        out.append((t, sn))
        t += every
    return out


def frames(vcm_after=True, dev_after_from=None, dev_noarm_at=None, jitter_at=None, late_at=None,
           double_at=None):
    """VCM 0x051 every 10 ms except during the jam, counter in B5 stepping 0-15;
    a device frame is a copy of the VCM's NEXT command, 0.5 ms behind the one it
    answers. jitter_at: the replay's next 0x051 1.9 ms behind instead of 10 ms
    (the slice's own pair, or bunching after a stall) -- not a device frame.
    late_at: the device's copy lands after the VCM's own. double_at: two copies."""
    out, t, k = [], 90.0, 0
    pay = lambda n: "080080ff7f%02x" % (n % 16)
    while t < 163.0:
        jam = PH["jam_start"] <= t < PH["jam_stop"] + 0.5
        after = t >= PH["rearm_posted"]
        if not jam and (vcm_after or not after):
            out.append((t, 0x051, pay(k)))
            if (dev_after_from is not None and t >= dev_after_from) or                     (dev_noarm_at is not None and abs(t - dev_noarm_at) < 0.005):
                out.append((t + 0.0005, 0x051, pay(k + 1)))
            if double_at is not None and abs(t - double_at) < 0.005:
                out.append((t + 0.0005, 0x051, pay(k + 1)))
                out.append((t + 0.0009, 0x051, pay(k + 1)))
            if late_at is not None and abs(t - late_at) < 0.005:
                out.append((t + 0.0105, 0x051, pay(k + 1)))
            if jitter_at is not None and abs(t - jitter_at) < 0.005:
                k += 1
                out.append((t + 0.0019, 0x051, pay(k)))
            out.append((t + 0.002, 0x7F1, "00"))
        k += 1
        t = round(t + 0.010, 4)
    return out


def v(**kw):
    sm = kw.pop("sm", samples())
    fr = kw.pop("fr", frames())
    return C.judge_busoff_rearm(sm, fr, PH, kw.pop("status", 200), kw.pop("expect", "stays"))


def main():
    vcm, dev = C.split_0x051(frames(dev_after_from=140.0), 133.0, 163.0)
    check("split: VCM 0x051 at 100/s after the re-arm", 2990 <= len(vcm) <= 3010, len(vcm))
    check("split: device frames only from 140 s", len(dev) > 0 and min(dev) >= 140.0, (len(dev), dev[:1]))
    check("split: no device frame with none trailing", C.split_0x051(frames(), 90.0, 163.0)[1] == [])
    vcm, dev = C.split_0x051(frames(jitter_at=120.0), 90.0, 163.0)
    check("split: a replay 0x051 1.9 ms behind the last is not the device's", dev == [], dev[:2])
    check("trail count still sees that pair", C.trail_0x051(frames(jitter_at=120.0), 90.0, 163.0) == 1,
          C.trail_0x051(frames(jitter_at=120.0), 90.0, 163.0))
    dev = C.split_0x051(frames(late_at=120.0), 90.0, 163.0)[1]
    check("split: a late device copy (after the VCM's own) is the device's", len(dev) == 1, dev)
    dev = C.split_0x051(frames(double_at=120.0), 90.0, 163.0)[1]
    check("split: two device copies of one command are two device frames", len(dev) == 2, dev)
    dev = C.split_0x051(frames(dev_after_from=140.0), 133.0, 163.0)[1]
    check("split: device frames booked at their own time, not the VCM copy's",
          all(round((x - 140.0) * 1000, 1) % 10 == 0.5 for x in dev[:5]), dev[:5])

    j = v()
    check("old build stays bus_off: FAILS AS EXPECTED", j["verdict"].startswith("FAILS AS EXPECTED"), j["verdict"])
    check("first bus_off reading recorded: 1.0 s after the jam at 1 Hz", j["bus_off_page_rel_jam_s"] == 1.0, j["bus_off_page_rel_jam_s"])
    j = v(sm=samples(run_from=134.0))
    check("old build comes back: NOT AS EXPECTED", j["verdict"].startswith("NOT AS EXPECTED"), j["verdict"])
    j = v(fr=frames(dev_after_from=140.0))
    check("old build: a device 0x051 after the re-arm is NOT AS EXPECTED", j["verdict"].startswith("NOT AS EXPECTED"),
          j["verdict"])

    j = v(sm=samples(run_from=134.0, live_from=135.0), fr=frames(dev_after_from=135.0), expect="reinstalls")
    check("new build runs, live, device 0x051: AS SPECIFIED", j["verdict"].startswith("AS SPECIFIED"), j["verdict"])
    check("running time rel re-arm", j["running_after_rel_rearm_s"] == 1.0, j["running_after_rel_rearm_s"])
    j = v(sm=samples(run_from=134.0), expect="reinstalls")
    check("new build runs, never live: SEE ARM", j["verdict"].startswith("SEE ARM"), j["verdict"])
    j = v(expect="reinstalls")
    check("new build stays bus_off: NOT AS SPECIFIED", j["verdict"].startswith("NOT AS SPECIFIED: still"), j["verdict"])

    for expect in ("stays", "reinstalls"):
        j = v(sm=samples(recover_noarm_at=120.0), expect=expect)
        check("%s: recovery with no arm is NOT AS SPECIFIED" % expect,
              j["verdict"].startswith("NOT AS SPECIFIED: recovered with no arm"), j["verdict"])
    j = v(fr=frames(dev_noarm_at=120.0))
    check("a device 0x051 in the no-arm window is a recovery", j["verdict"].startswith("NOT AS SPECIFIED: recovered"),
          j["verdict"])

    j = v(fr=frames(jitter_at=120.0))
    check("a replay jitter pair in the no-arm window is not a recovery",
          j["verdict"].startswith("FAILS AS EXPECTED") and j["trail_0x051_noarm"] == 1, (j["verdict"], j["trail_0x051_noarm"]))

    import gzip, json, os, tempfile
    import gi_paths   # the worktree's scratch/, not the system temp folder (harness-defects row, 2026-10-24)
    base = sys.argv[1] if len(sys.argv) > 1 else gi_paths.scratch("busoff_rearm_judge")
    os.makedirs(base, exist_ok=True)
    d = tempfile.mkdtemp(dir=base)
    fr = frames()[:50]
    p = C.save_witness(os.path.join(d, "w.json.gz"), fr, [100.0, 101.0], "kvaser_hw")
    back = json.load(gzip.open(p, "rt"))
    check("witness file keeps every frame, its payload and the errors",
          [tuple(x) for x in back["frames"]] == fr and back["errs"] == [100.0, 101.0]
          and back["clock"] == "kvaser_hw", len(back["frames"]))

    j = v(sm=samples(boff_from=999.0))
    check("never bus_off: INCONCLUSIVE", j["verdict"].startswith("INCONCLUSIVE: the page never"), j["verdict"])
    j = v(sm=samples(gap=(110.0, 125.0)))
    check("too few no-arm readings: INCONCLUSIVE", j["verdict"].startswith("INCONCLUSIVE: too few"), j["verdict"])
    j = v(fr=frames(vcm_after=False))
    check("replay not running after the re-arm: INCONCLUSIVE",
          j["verdict"].startswith("INCONCLUSIVE: the replay was not running"), j["verdict"])
    j = v(status=None)
    check("no reply status: NOT AS SPECIFIED", j["verdict"].startswith("NOT AS SPECIFIED: the re-arm answered"),
          j["verdict"])
    j = v(status=500, sm=samples(run_from=134.0, live_from=135.0), fr=frames(dev_after_from=135.0),
          expect="reinstalls")
    check("a 500 with the controller back is still NOT AS SPECIFIED",
          j["verdict"].startswith("NOT AS SPECIFIED: the re-arm answered"), j["verdict"])

    j = v(sm=samples(reboot_at=134.5), status=None, expect="reinstalls")
    check("a reboot at the re-arm is NOT AS SPECIFIED, named as a reboot",
          j["verdict"].startswith("NOT AS SPECIFIED: the device rebooted"), j["verdict"])
    check("reboot time recorded", j["reboot_after_rearm_rel_s"] == 2.0, j["reboot_after_rearm_rel_s"])
    j = v(sm=samples(refused=True), status=400, expect="reinstalls")
    check("400 cannot arm: NOT AS SPECIFIED, named, with mode 0 and quiesce",
          j["verdict"].startswith("NOT AS SPECIFIED: 400 'cannot arm'") and "mode 0" in j["verdict"]
          and "quiesce" in j["verdict"], j["verdict"])
    j = v()
    check("no reboot read when uptime only rises", j["reboot_after_rearm_rel_s"] is None)

    a = C.build_parser().parse_args(["busoff-rearm", "--power", "12 V", "--expect", "stays"])
    check("--expect parsed", a.expect == "stays" and a.case == "busoff-rearm")

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
