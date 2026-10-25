"""Quiesce under traffic (spec 8): INHIBIT or PASSIVE -> OBSERVE -> back, mid-replay.

Gen-inhibit tester, 2026-10-10 (reviewer-approved plan). e4_device_calibration.py
arms the initial mode before the replay and then POSTs --post-schedule (e.g.
"10:1,20:3") into it, from a thread, recording the replay index of each POST. The
replay leaves the adapter in queue order, so provenance gives each POST's wire time.

Spec 8's required outcome, read from the wire and the device:
  - THE REQUESTED MODE IN EFFECT: in the OBSERVE span the device puts nothing on the
    wire -- no 0x051, no diag page, no probe. Reported: how long after the OBSERVE
    POST the device's last frame came (the switch completing), and any device frame
    more than SILENT_BY_S after it, which is a violation. (Listen-only itself is
    proven separately, by ACK errors on an isolated segment: the OBSERVE ACK probe.)
  - A CLEAN RE-ARM, only through the normal interlocks: after the back-POST the
    device goes live again. INHIBIT: time from the POST to the first device 0x051
    (provenance). PASSIVE: to the first 0x7F1 page with inhibit_live, which is only
    as fine as the diag page period (~1.2 s).
  - NO PANIC: checked by the driver from uptime (continuous across the arm, reset
    reason unchanged); reported here from the arm's end status page.

    py -3.14 quiesce_analysis.py runs/<run>.json [...]
"""
import bisect
import json
import os
import re
import sys
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
WICAN_IDS = (0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9)
SILENT_BY_S = 1.0


def span_report(t0, t1, dev_cmd, dev_other):
    """Device frames in [t0, t1): counts and first/last offsets (ms) from t0."""
    a = [t for t in dev_cmd if t0 <= t < t1]
    b = [t for t in dev_other if t0 <= t < t1]
    allf = sorted(a + b)
    return {"device_0x051": len(a), "device_other": len(b),
            "first_ms": round((allf[0] - t0) * 1e3, 1) if allf else None,
            "last_ms": round((allf[-1] - t0) * 1e3, 1) if allf else None}


def observe_verdict(span):
    """OBSERVE span: the last device frame must come within SILENT_BY_S of the POST."""
    if span["last_ms"] is None:
        return "silent from the POST"
    if span["last_ms"] <= SILENT_BY_S * 1e3:
        return "silent %.1f ms after the POST" % span["last_ms"]
    return "VIOLATION: a device frame %.1f ms after the OBSERVE POST" % span["last_ms"]


def live_page_times(wire, identity=None):
    """(times of the 0x7F1 pages with inhibit_live set (diag_flags bit5), the Decoder).
    Decoded by gi_diag on each page's own schema, never read from byte 3 by hand
    (toolkit item 10, reading Q7, 2026-10-24); an unknown schema raises."""
    pages, dec = gi_diag.decode_pages(wire, identity=identity, decode_choices=False, allow_truncated=True)
    return sorted(t for t, i, p in pages if i == 0x7F1 and p and int(p.get("diag_flags", 0)) & 0x20), dec


def analyse(run):
    import provenance_classifier as P
    d = json.load(open(run, encoding="utf-8"))
    arm = list(d["arms"])[0]
    a = d["arms"][arm]
    r = P.rejudge(run, arm, keep_frames=True)
    rep = r["replayed_wire_times"]
    wire = P.read_witness(os.path.join(os.path.dirname(run), a["witness_raw"]))
    dev_cmd = sorted(r["device_times"])
    dev_other = sorted(t for t, i, _d in wire if i in WICAN_IDS)
    live_pages, dec = live_page_times(wire, d.get("decoder"))
    sched = a.get("post_schedule") or []
    posts = [(rep[e["index"]], e["mode"], e) for e in sched if e.get("index") is not None]
    bounds = [rep[0]] + [t for t, _m, _e in posts] + [rep[-1] + 0.5]
    modes = [d.get("mode")] + ["mode %d" % m for _t, m, _e in posts]
    spans = []
    for k in range(len(bounds) - 1):
        sp = span_report(bounds[k], bounds[k + 1], dev_cmd, dev_other)
        sp["mode"] = modes[k]
        if k > 0:
            m = posts[k - 1][1]
            sp["post_http"] = posts[k - 1][2].get("http")
            sp["post_duration_ms"] = (round((posts[k - 1][2]["post_s"][1]
                                             - posts[k - 1][2]["post_s"][0]) * 1e3, 1)
                                      if posts[k - 1][2].get("post_s") and
                                      None not in posts[k - 1][2]["post_s"] else None)
            if m == 1:
                sp["verdict"] = observe_verdict(sp)
            elif m == 3:
                nxt = bisect.bisect_left(dev_cmd, bounds[k])
                sp["live_after_ms"] = (round((dev_cmd[nxt] - bounds[k]) * 1e3, 1)
                                       if nxt < len(dev_cmd) and dev_cmd[nxt] < bounds[k + 1]
                                       else None)
            elif m == 4:
                nxt = bisect.bisect_left(live_pages, bounds[k])
                sp["live_page_after_ms"] = (round((live_pages[nxt] - bounds[k]) * 1e3, 1)
                                            if nxt < len(live_pages) and
                                            live_pages[nxt] < bounds[k + 1] else None)
        spans.append(sp)
    dev = json.dumps(a.get("device") or {})
    g = lambda k: (lambda m: m.group(1) if m else None)(re.search(r'"%s": ("[^"]*"|\d+)' % k, dev))
    # tx_ok counts since the last arming: compare with the device 0x051 in the last span
    # that was (re)armed into INHIBIT.
    last_inh = [k for k, m in enumerate(modes) if m in ("inhibit", "mode 3")]
    tx_check = None
    if last_inh:
        k = last_inh[-1]
        n = len([t for t in dev_cmd if bounds[k] <= t < bounds[k + 1]])
        tx_check = {"span": k, "device_0x051_in_span": n, "tx_ok": int(g("tx_ok") or 0),
                    "agree": n == int(g("tx_ok") or 0)}
    return {"run": os.path.basename(run), "initial_mode": d.get("mode"),
            "tx_ok_vs_last_inhibit_span": tx_check,
            "late": r["late"], "double": r["double"], "unexplained": r["unexplained"],
            "provenance": r["verdict"], "spans": spans,
            "end_page": {"mode": g("mode"), "self_off": g("self_off"), "uptime_ms": g("uptime_ms"),
                         "reset_reason": g("reset_reason"), "abort_reason": g("abort_reason")},
            "diag_decode": dec.summary()}


def main():
    for run in sys.argv[1:]:
        if not run.endswith("_provenance.json"):
            print(json.dumps(analyse(run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
