"""Which 0x051 on the wire is ours? Decided by the replay's own record, not a window.

Gen-inhibit tester, 2026-10-10; design approved by the reviewer the same day.

WHY. On a full-bus replay the device's answer to VCM command k carries counter k+1
and is byte-identical to the VCM's own command k+1 (B0 0x08, zero torque, and we
take the VCM's next counter). When two frames carry k+1, content and order cannot
say which is ours; only time can. e4_device_calibration.py's classifier took its
time bound from the DEVICE (max_hold_us + max_queue_us + 300 us, capped at 3 ms),
and max_hold_us is stamped when the worker credits a frame, not when it is on the
wire. A 25 ms credit in the X2-held long arm (2026-10-10) set the window to its cap,
and three VCM commands the replay sent 2.39-2.9 ms after our answer were booked as
"double answers".

HOW. Provenance by QUEUE ORDER, not by time. Frames from one adapter leave in
queue order (they do not arbitrate against each other; e4_device_calibration.py
notes this), so the replay's frames appear on the wire in exactly the capture's
order, with every other node's frames interleaved.
  1. WALK the wire with a pointer into the replayed sequence. A wire frame equal to
     the next replayed frame (id and payload) is the replay's and advances the
     pointer; anything else is another node's (the device, the X2, diag pages).
     So a 0x051 is the replay's command k+1 only once every frame the replay
     queued before k+1 has been seen. Our answer to k, which is byte-identical
     to k+1, arrives while that pointer still sits on a frame queued between
     k and k+1, so it cannot be mistaken for k+1.
  2. AMBIGUOUS only when the pointer sits on command k+1 itself, the device has
     not yet answered k, and two frames equal to k+1 arrive before the replay's
     next frame. Order alone cannot say which is ours. Each such case is listed
     with its frames and makes the arm UNRESOLVED; none is guessed. The case
     needs two commands queued back to back with nothing between them.
  3. COUNT INVARIANT. Every replayed frame must be found, in order, and the
     device's 0x051 must equal its own count of them (tx_ok in INHIBIT; 0 in
     RESPOND, whose tx_ok counts 0x7F0 probes). A mismatch makes the arm
     UNRESOLVED; it is never absorbed.
  4. JUDGE each device 0x051 against the replayed command in front of it on the
     wire: counter +1 is on time; counter equal to it means the next command was
     already on the wire, i.e. LATE; anything else is UNEXPLAINED. Two device
     frames behind the same command are a DOUBLE.
TIMING IS REPORTED, NOT USED. The first version matched on scheduled replay times
fitted to the witness clock, with the fit residual's maximum as the bound. On the
real logs the residual tail reached 13-34 ms (p99 0.4-0.9 ms), because a frame
waits behind whatever the adapter has queued, so the bound covered whole command
periods and every INHIBIT arm came out ambiguous (runs/provenance_rejudge_timebased_20261010.txt).
That refusal was correct, and it is why the order is used instead. The fit and its
residuals (p50/p99/max) are still computed over the non-0x051 ids and reported.
The e4 runs to date record no PCAN TX timestamp; queue order needs none.
LIMIT: it relies on the adapter sending in queue order and on the witness missing no
frame. A lost or reordered replayed frame breaks the walk, which shows as replayed
frames not found (the count invariant), never as a quiet verdict.

    py -3.14 provenance_classifier.py rejudge runs/<e4 run>.json [--arm idle]
Writes runs/<e4 run>_<arm>_provenance.json beside it; the run file is not changed.
"""
import argparse
import gzip
import json
import os
import re
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
CMD_ID = 0x051
LINE = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else None


def align(src, wire):
    """Fit wire = a + b * src over the non-0x051 ids. src, wire: [(t, id, data)].

    Returns {a, b, pairs, residual_us: {p50, p99, max}, ids_used, ids_dropped}."""
    by_s, by_w = {}, {}
    for t, i, d in src:
        if i != CMD_ID:
            by_s.setdefault(i, []).append((t, d))
    for t, i, d in wire:
        if i != CMD_ID and i in by_s:
            by_w.setdefault(i, []).append((t, d))
    pairs, used, dropped = [], [], []
    for i, s in sorted(by_s.items()):
        w = by_w.get(i, [])
        if len(w) != len(s) or any(a[1] != b[1] for a, b in zip(s, w)):
            dropped.append([hex(i), len(s), len(w)])
            continue
        used.append(hex(i))
        pairs.extend((a[0], b[0]) for a, b in zip(s, w))
    if len(pairs) < 2:
        return {"a": None, "b": None, "pairs": len(pairs), "ids_used": used,
                "ids_dropped": dropped, "residual_us": None}
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    b = sum((p[0] - mx) * (p[1] - my) for p in pairs) / sxx if sxx else 1.0
    a = my - b * mx
    res = [abs(p[1] - (a + b * p[0])) * 1e6 for p in pairs]
    return {"a": a, "b": b, "pairs": n, "ids_used": used, "ids_dropped": dropped,
            "residual_us": {"p50": round(pct(res, 0.5), 1), "p99": round(pct(res, 0.99), 1),
                            "max": round(max(res), 1)}}


def classify(src, wire, device_cmd_tx, keep_frames=False):
    """Provenance classification of the wire 0x051 by queue order. src: the replayed
    window [(t, id, data)] in send order; wire: the witness [(t, id, data)] in time
    order. device_cmd_tx: how many 0x051 the device says it sent (tx_ok in INHIBIT,
    0 in RESPOND)."""
    out = {"align": align(src, wire), "unresolved": [], "ambiguous": []}
    seq = [(i, d) for _t, i, d in src]
    cmd_idx = [k for k, (i, _d) in enumerate(seq) if i == CMD_ID]
    p = 0
    last_cmd = None            # (wire t, data, src index) of the newest replayed command
    answered = set()           # src indices of replayed commands the device answered
    vcm, dev = [], []          # replayed commands on the wire; device 0x051
    rep_t = []                 # wire time of each replayed frame, by source index
    n = len(wire)
    j = 0
    while j < n:
        t, i, d = wire[j]
        if p < len(seq) and (i, d) == seq[p]:
            if i == CMD_ID and last_cmd is not None and last_cmd[2] not in answered:
                # Could be our answer to the previous command (byte-identical). Look
                # for a second identical frame before the replay's next frame.
                nxt = seq[p + 1] if p + 1 < len(seq) else None
                k = j + 1
                twin = None
                while k < n and (nxt is None or (wire[k][1], wire[k][2]) != nxt):
                    if wire[k][1] == CMD_ID and wire[k][2] == d:
                        twin = k
                        break
                    k += 1
                if twin is not None:
                    out["ambiguous"].append({
                        "at": round(t, 6), "data": d.hex(),
                        "previous_command": [round(last_cmd[0], 6), last_cmd[1].hex()],
                        "frames": [[round(wire[x][0], 6), wire[x][2].hex()] for x in (j, twin)]})
                    # Book one of each so the counts stay meaningful; the arm is
                    # UNRESOLVED regardless.
                    dev.append((t, d, last_cmd))
                    answered.add(last_cmd[2])
                    vcm.append((wire[twin][0], d))
                    last_cmd = (wire[twin][0], d, p)
                    rep_t.append(wire[twin][0])
                    p += 1
                    wire = wire[:twin] + wire[twin + 1:]
                    n -= 1
                    j += 1
                    continue
            if i == CMD_ID:
                vcm.append((t, d))
                last_cmd = (t, d, p)
            rep_t.append(t)
            p += 1
        elif i == CMD_ID:
            dev.append((t, d, last_cmd))
            if last_cmd is not None:
                answered.add(last_cmd[2])
        j += 1
    out["replayed_frames"] = len(seq)
    out["replayed_found"] = p
    out["replayed_commands"] = len(cmd_idx)
    out["assigned"] = len(vcm)
    out["device_frames"] = len(dev)
    out["device_cmd_tx"] = device_cmd_tx
    if p != len(seq):
        out["unresolved"].append("count: %d of %d replayed frames found in order (next missing: %s)"
                                 % (p, len(seq), "%03X#%s" % (seq[p][0], seq[p][1].hex())))
    if device_cmd_tx is not None and len(dev) != device_cmd_tx:
        out["unresolved"].append("count: %d device 0x051 on the wire, the device says %d"
                                 % (len(dev), device_cmd_tx))
    if out["ambiguous"]:
        out["unresolved"].append("ambiguous: %d command(s) with two identical frames and no "
                                 "replayed frame between" % len(out["ambiguous"]))
    late, double, unexplained, ontime = [], [], [], 0
    seen_front = {}
    for t, d, front in dev:
        if front is None:
            unexplained.append([round(t, 6), d.hex(), "no replayed command in front"])
            continue
        ft, fd, fk = front
        delta = (d[5] - fd[5]) & 0x0F if len(d) > 5 and len(fd) > 5 else None
        rec = [round(t, 6), d.hex(), round(ft, 6), fd.hex(), round((t - ft) * 1e6)]
        if delta == 1:
            ontime += 1
        elif delta == 0:
            late.append(rec)
        else:
            unexplained.append(rec + ["delta %r" % delta])
        if fk in seen_front:
            double.append([seen_front[fk], rec])
        seen_front[fk] = rec
    if keep_frames:
        out["vcm_times"] = [round(t, 6) for t, _d in vcm]
        out["device_times"] = [round(t, 6) for t, _d, _f in dev]
        out["replayed_wire_times"] = rep_t
    out.update({"on_time": ontime, "late": late, "double": double, "unexplained": unexplained,
                "verdict": "UNRESOLVED" if out["unresolved"] else
                           ("as specified" if not late and not double and not unexplained
                            else "NOT as specified")})
    return out


def read_witness(path):
    out = []
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as f:
        for ln in f:
            m = LINE.match(ln)
            if m:
                out.append((float(m.group(1)), int(m.group(2), 16), bytes.fromhex(m.group(3))))
    out.sort(key=lambda x: x[0])
    return out


def rejudge(run_path, arm="idle", keep_frames=False):
    sys.path.insert(0, HERE)
    import e4_device_calibration as E
    d = json.load(open(run_path, encoding="utf-8"))
    a = d["arms"][arm]
    E.CAPTURE = gi_paths.runs(d["capture"]) if not os.path.isabs(d["capture"]) else d["capture"]
    if not os.path.exists(E.CAPTURE):
        E.CAPTURE = gi_paths.data_dir(d["capture"])
    gi_paths.verify(E.CAPTURE)
    E.WINDOW_START = d["window_start"]
    frames = E.load_window(d["seconds"])
    src = [(t, i, dat) for t, i, dat, _x in frames]
    wit = a.get("witness_raw")
    wpath = os.path.join(os.path.dirname(run_path), os.path.basename(wit)) if wit else \
        run_path[:-5] + "_%s_witness.log.gz" % arm
    wire = read_witness(wpath)
    dev = a.get("device") or {}
    m = re.search(r'"tx_ok": (\d+)', json.dumps(dev))
    tx_ok = int(m.group(1)) if m else None
    # Only INHIBIT transmits 0x051 (RESPOND sends 0x7F0 probes, PASSIVE nothing).
    device_cmd_tx = tx_ok if d.get("mode", "inhibit") == "inhibit" else 0
    # tx_ok resets on every arming (gi_reset_stats), so an arm whose --post-schedule
    # re-arms counts only its last span: the whole-arm count cannot be checked here.
    # quiesce_analysis.py checks it per span instead (2026-10-10).
    if any(e.get("mode") in (3, 4) for e in (a.get("post_schedule") or [])):
        device_cmd_tx = None
    res = classify(src, wire, device_cmd_tx, keep_frames)
    res["method"] = ("queue order: the replay's frames on the wire in capture order; "
                     "timing (scheduled capture offsets fitted to the witness) reported only")
    res["run"] = os.path.basename(run_path)
    res["arm"] = arm
    res["current_classifier"] = {k: a.get(k) for k in ("late_at_wire", "double_answers",
                                                       "misattributed_vcm",
                                                       "missing_command_in_front",
                                                       "trail_window_us")}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["rejudge"])
    ap.add_argument("run")
    ap.add_argument("--arm", default="idle")
    a = ap.parse_args()
    res = rejudge(a.run, a.arm)
    out = a.run[:-5] + "_%s_provenance.json" % a.arm
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    al = res["align"]
    print("%s [%s]: replayed frames found %s of %s; timing residual p50/p99/max %s us (reported only)"
          % (res["run"], a.arm, res.get("replayed_found"), res.get("replayed_frames"),
             al["residual_us"]))
    print("  replayed %s, assigned %s, device 0x051 %s (device says %s); on time %s, late %d, "
          "double %d, unexplained %d -> %s; current classifier %s"
          % (res.get("replayed_commands"), res.get("assigned"), res.get("device_frames"),
             res.get("device_cmd_tx"), res.get("on_time"), len(res.get("late", [])),
             len(res.get("double", [])), len(res.get("unexplained", [])), res.get("verdict"),
             res["current_classifier"]))
    for u in res["unresolved"][:5]:
        print("  UNRESOLVED:", u)
    for amb in res.get("ambiguous", [])[:5]:
        print("  ambiguous:", amb)
    print("  wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
