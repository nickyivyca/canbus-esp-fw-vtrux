"""Mode entry into live traffic: which commands go unanswered, relative to the POST.

Gen-inhibit tester, 2026-10-10 (reviewer). e4_device_calibration.py --arm-after S
POSTs the mode S seconds into a running replay, as on the truck, where the device
enters INHIBIT with the bus already at ~2,400 frames/s. This places the POST on the
witness clock and lists the unanswered commands after it.

  - THE POST ON THE WIRE. The run records the replay index at which the POST was
    issued (`post_index`: frames 0..index-1 had been handed to the adapter). The
    replay leaves the adapter in queue order, so provenance_classifier gives that
    frame's wire time: t_post. It is the wire moment of the first frame the replay
    queued after the POST started, so the true POST is at or a little before it.
  - ANSWERED. RESPOND: a 0x7F0 probe before the next replayed command. INHIBIT: a
    device 0x051 (provenance) before the next replayed command. A command with
    neither is UNANSWERED (in RESPOND that includes a probe merely late, so it is
    an upper bound on misses).
  - t_first: the first answer after t_post (for INHIBIT, going live).
  - Reported with the device's own figures: rx_missed, ctr_ok/ctr_bad, the
    probe/inhibit class queued count; and the replay's back-to-back fraction in
    the 200 ms after t_post against the rest, since the POST thread shares the
    GIL with the replay's pacing.

  - A RE-ARM (2026-10-16, baseline B3): a run with no --arm-after but a
    --post-schedule entry into INHIBIT (`20:3`) is placed the same way, from that
    entry's recorded index, and judged as an entry into INHIBIT from the run's
    starting mode. `device_answers_before_post` counts device 0x051 before t_post,
    because a starting mode that itself sends 0x051 would make t_first meaningless.

    py -3.14 mode_entry_analysis.py runs/<run>.json [...]
"""
import bisect
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def unanswered_after(cmds, answers, t_from):
    """Commands at or after t_from with no answer before the next command.
    cmds, answers: sorted wire times. Returns [(index, t)] and the first answer >= t_from."""
    out = []
    a0 = bisect.bisect_left(answers, t_from)
    first = answers[a0] if a0 < len(answers) else None
    k0 = bisect.bisect_left(cmds, t_from)
    for k in range(k0, len(cmds)):
        nxt = cmds[k + 1] if k + 1 < len(cmds) else cmds[k] + 0.05
        j = bisect.bisect_right(answers, cmds[k])
        if not (j < len(answers) and answers[j] < nxt):
            out.append((k, cmds[k]))
    return out, first


def b2b(times):
    g = [(b - a) * 1e6 for a, b in zip(times, times[1:])]
    return round(sum(1 for x in g if x < 300) / len(g), 3) if g else None


MODE_NAMES = {0: "off", 1: "observe", 2: "respond", 3: "inhibit", 4: "passive"}


def entry_point(run_mode, arm):
    """Where the mode entry is in the replay: (post_index, how, entered_mode) or None.
    --arm-after's post_index enters the run's own mode; otherwise the first
    --post-schedule entry into INHIBIT (mode 3) that recorded an index."""
    if arm.get("post_index") is not None:
        return arm["post_index"], "arm-after", run_mode
    for e in arm.get("post_schedule") or []:
        if e.get("mode") == 3 and e.get("index") is not None:
            return e["index"], "schedule %s:3 from %s" % (e.get("at_s"), run_mode), "inhibit"
    return None


def analyse(run):
    import provenance_classifier as P
    d = json.load(open(run, encoding="utf-8"))
    arm = list(d["arms"])[0]
    a = d["arms"][arm]
    r = P.rejudge(run, arm, keep_frames=True)
    rep_t = r["replayed_wire_times"]
    ep = entry_point(d.get("mode"), a)
    if ep is None or ep[0] >= len(rep_t):
        return {"run": os.path.basename(run),
                "error": "no entry in the run (neither --arm-after nor a --post-schedule entry into 3)"}
    pi, how, entered = ep
    t_post = rep_t[pi]
    wire = P.read_witness(os.path.join(os.path.dirname(run), a["witness_raw"]))
    cmds = sorted(r["vcm_times"])
    if entered == "respond":
        answers = sorted(t for t, i, _d in wire if i == 0x7F0)
        cls = "probe"
    else:
        answers = sorted(r["device_times"])
        cls = "inhibit"
    un, first = unanswered_after(cmds, answers, t_post)
    s = json.dumps(a.get("device") or {})
    g = lambda k: (lambda m: int(m.group(1)) if m else None)(re.search(r'"%s": (\d+)' % k, s))
    i = s.find('"%s": {' % cls)
    queued = json.loads(s[i + len(cls) + 4:s.index("}", i) + 1]).get("queued") if i >= 0 else None
    all_t = sorted(t for t, _i, _d in wire)
    near = [t for t in all_t if t_post <= t < t_post + 0.2]
    rest = [t for t in all_t if t >= t_post + 0.2]
    cmds_after = len(cmds) - bisect.bisect_left(cmds, t_post)
    sched = [e for e in (a.get("post_schedule") or []) if e.get("index") == pi]
    return {"run": os.path.basename(run), "mode": d.get("mode"), "entry": how,
            "entered": entered, "post_index": pi,
            "post_s": sched[0].get("post_s") if sched else a.get("post_s"),
            "post_http": sched[0].get("http") if sched else a.get("post_http"),
            "device_answers_before_post": bisect.bisect_left(answers, t_post),
            "provenance": r["verdict"], "commands_after_post": cmds_after,
            "first_answer_after_post_ms": round((first - t_post) * 1e3, 1) if first else None,
            "unanswered_after_post": len(un),
            "unanswered_ms_after_post": [round((t - t_post) * 1e3, 1) for _k, t in un],
            "unanswered_ms_after_first_answer": ([round((t - first) * 1e3, 1) for _k, t in un
                                                  if t >= first] if first else None),
            "rx_missed": g("rx_missed"), "rx_overrun": g("rx_overrun"), "ctr_ok": g("ctr_ok"),
            "ctr_bad": g("ctr_bad"), "class_queued": queued, "tx_ok": g("tx_ok"),
            "b2b_200ms_after_post": b2b(near), "b2b_rest": b2b(rest)}


def main():
    rows = [analyse(r) for r in sys.argv[1:] if not r.endswith("_provenance.json")]
    for x in rows:
        print(json.dumps(x))
    return 0


if __name__ == "__main__":
    sys.exit(main())
