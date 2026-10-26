"""No stale answers on going live (spec 12.4 row added 2026-10-10, user).

Gen-inhibit tester, 2026-10-10. The row: "the first inhibit frame after going live
answers the VCM's newest 0x051, never one it has already passed. Run under full-bus
replay on every entry into INHIBIT: from OFF, OBSERVE, RESPOND and PASSIVE, and from
an auto-arm boot. The witness shows no device 0x051 with a counter the VCM had
already passed." Spec 7 gate condition 9 (the receive queue is drained) and spec 3
(wake on a mode change).

NO NEW RIG. The arms are e4_device_calibration.py runs (full-bus replay of
scottsvalley_armable_300s.log through the PCAN, Kvaser witness, provenance by queue
order attached live), the same as the 18:52 re-arm set that found the stale burst
leaving OBSERVE; the auto-arm boots are autoarm_boot_to_live.py runs. `plan` prints
the commands. This file only JUDGES their run files.

STALE, from the provenance classifier's per-frame judgement. Each device 0x051 is
compared with the replayed command in front of it on the wire, by the counter in B5's
low nibble (mod 16): +1 answers the newest command (on time); 0 answers a command the
VCM has already passed by one (the classifier's LATE); a counter BEHIND the command
in front (delta 9-15, i.e. -7..-1) answers one passed by more (the classifier lists it
as UNEXPLAINED with its delta: the 10-of-10 OBSERVE re-arm burst was delta 14 and 15).
Both are stale. A counter AHEAD by 2-8 is unexplained but not stale; it is reported
separately, and the classifier's own verdict still fails the arm. A device frame with
no replayed command in front is not judged here (it is unexplained to the classifier).

VERDICT per run: UNRESOLVED if the classifier is (counts or ambiguity); VOID if no
device 0x051 after the entry (never live); NOT if any stale frame after the entry;
otherwise the classifier's own verdict (so doubles and ahead-counters still fail).

    py -3.14 golive_stale_check.py plan
    py -3.14 golive_stale_check.py judge runs/e4_golive_*.json runs/autoarm_*.json
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STALE_BEHIND = range(9, 16)          # delta -7..-1 mod 16


def delta_of(rec):
    """The classifier's record -> counter delta, or None if it names none."""
    for x in rec[5:]:
        if isinstance(x, str) and x.startswith("delta "):
            v = x.split()[1]
            return None if v == "None" else int(v)
    return None


def stale_frames(prov, after=None):
    """[(t, data hex, front data hex, delta, kind)] for stale device 0x051 at or after `after`."""
    out = []
    for r in prov.get("late") or []:
        if after is None or r[0] >= after:
            out.append((r[0], r[1], r[3], 0, "late (counter = the command in front)"))
    for r in prov.get("unexplained") or []:
        if len(r) < 4:
            continue
        d = delta_of(r)
        if d in STALE_BEHIND and (after is None or r[0] >= after):
            out.append((r[0], r[1], r[3], d, "behind the command in front by %d" % (16 - d)))
    return sorted(out)


def ahead_frames(prov, after=None):
    return [r for r in prov.get("unexplained") or []
            if len(r) >= 4 and delta_of(r) not in STALE_BEHIND and delta_of(r) not in (None, 0, 1)
            and (after is None or r[0] >= after)]


def judge(prov, device_after, after=None):
    """prov: the classifier's output; device_after: device 0x051 after the entry;
    after: the entry instant on the witness clock (None: the whole run is after it).
    Only frames at or after the entry are judged, so a pre-boot span cannot decide it."""
    if prov.get("verdict") == "UNRESOLVED" or prov.get("unresolved"):
        return "UNRESOLVED: %s" % "; ".join(prov.get("unresolved") or ["classifier"])
    if not device_after:
        return "VOID: no device 0x051 after the entry (never live)"
    st = stale_frames(prov, after)
    if st:
        return "NOT as specified: %d stale device 0x051 (first: %s)" % (len(st), st[0][4])
    ah = ahead_frames(prov, after)
    dbl = [x for x in prov.get("double") or [] if after is None or x[-1][0] >= after]
    nofront = [r for r in prov.get("unexplained") or [] if len(r) < 4 and (after is None or r[0] >= after)]
    if ah or dbl or nofront:
        return "NOT as specified: doubles %d, counter ahead %d, no command in front %d" % (
            len(dbl), len(ah), len(nofront))
    return "as specified"


ENTRIES = [
    # (label, e4 args): the 18:52 re-arm set's timings, unchanged
    ("from OFF", "--mode inhibit --arm-after 5"),
    ("from OBSERVE", "--mode observe --post-schedule 20:3"),
    ("from RESPOND", "--mode respond --post-schedule 20:3"),
    ("from PASSIVE", "--mode passive --post-schedule 20:3"),
]


def plan():
    base = ("GI_WICAN_SSID=... py -3.14 gen-inhibit/tools/bench/e4_device_calibration.py "
            "--arm idle --capture runs/scottsvalley_armable_300s.log --seconds 35 --offset-us 500 %s "
            "--out runs/e4_golive_%s_<stamp>_arm<n>.json")
    for label, args in ENTRIES:
        print("%-14s x5: %s" % (label, base % (args, label.split()[-1].lower())))
    print("%-14s x3-5: autoarm_boot_to_live.py with the AUTO-ARM image, --reboot-at (a /system_reboot "
          "mid-replay) on scottsvalley_armable_300s.log; judged on device 0x051 after the boot" % "auto-arm boot")


def judge_file(path):
    d = json.load(open(path, encoding="utf-8"))
    if "arms" in d:                                   # e4
        a = d["arms"].get("idle") or next(iter(d["arms"].values()))
        prov = a.get("provenance") or {}
        if isinstance(prov, str) or not prov:
            return {"run": os.path.basename(path), "verdict": "UNRESOLVED: no provenance (%s)" % prov}
        if d.get("mode") == "inhibit" and not d.get("arm_after_s"):
            return {"run": os.path.basename(path), "verdict": "VOID: armed INHIBIT from the start, no entry"}
        n_dev = prov.get("device_frames") or 0
        return {"run": os.path.basename(path), "entry_from": "OFF" if d.get("arm_after_s") else d.get("mode"),
                "device_0x051": n_dev, "stale": stale_frames(prov), "ahead": ahead_frames(prov),
                "verdict": judge(prov, n_dev)}
    prov = d.get("provenance") or {}                  # autoarm_boot_to_live
    after = d.get("boot_wire")
    n_after = d.get("device_0x051_after_boot") or 0
    if after is None:
        return {"run": os.path.basename(path), "verdict": "VOID: no boot instant on the witness clock"}
    return {"run": os.path.basename(path), "entry_from": "auto-arm boot", "device_0x051": n_after,
            "stale": stale_frames(prov, after), "ahead": ahead_frames(prov, after),
            "verdict": judge(prov, n_after, after)}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("plan", "judge"):
        print(__doc__)
        return 2
    if sys.argv[1] == "plan":
        plan()
        return 0
    for p in sys.argv[2:]:
        r = judge_file(p)
        print("%-58s %-14s dev0x051 %-6s stale %-3d  %s" % (r["run"], r.get("entry_from"), r.get("device_0x051"),
                                                          len(r.get("stale") or []), r["verdict"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
