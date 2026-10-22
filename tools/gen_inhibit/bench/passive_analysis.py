"""PASSIVE on the wire (spec 3.1 / 12.4): judged from the diag pages, in the same arm.

Gen-inhibit tester, 2026-10-10; criteria as ruled by the reviewer the same day:
  1. PASSIVE never transmits 0x051: provenance (queue order) finds 0 device 0x051
     and every replayed frame.
  2. The would-transmit count equals the VCM commands the witness saw while PASSIVE
     would have been live, IN THE SAME ARM. The diag pages come about once a second,
     so it is judged per interval between consecutive counter samples: the counter's
     rise against the replayed 0x051 the witness saw in that interval (provenance).
     An interval is FULL (rise = commands, +/-1 for a page built a command before or
     after its own wire time), IDLE (rise 0), PARTIAL (0 < rise < commands: allowed
     only where the live span starts or ends) or OVER (rise > commands + 1: never
     allowed). The same is done for an INHIBIT arm with diag_tx_ok, so the two
     modes are judged by one method.
  3. Releases and trips are reported as INHIBIT would report them: the sequence of
     (arm_block, abort_reason) values from 0x7F8 and the first page with
     abort_latched (0x7F1 bit 7), each placed by REPLAY INDEX (the replayed frame at
     or before the page on the wire), so arms of one window compare directly.
  4. A reader can tell PASSIVE from INHIBIT from the frames: diag_mode on 0x7F1.
  5. (2026-10-12, spec 3.1, schema 7) The count resets on every arm, so the
     counter itself is bounded by the commands since the arm: from_zero().
     Through schema 6 it counted since boot and only criterion 2 applied.

    py -3.14 passive_analysis.py runs/<run>.json [...]
"""
import bisect
import json
import os
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DBC = gi_paths.diag_dbc()


def judge_intervals(samples, cmd_times):
    """samples: [(t, counter)] in time order; cmd_times: sorted wire times of replayed
    commands. Returns per-interval rows and a summary."""
    rows = []
    for (t1, c1), (t2, c2) in zip(samples, samples[1:]):
        n = bisect.bisect_right(cmd_times, t2) - bisect.bisect_right(cmd_times, t1)
        rise = c2 - c1
        if rise == 0:
            kind = "IDLE"
        elif abs(rise - n) <= 1:
            kind = "FULL"
        elif 0 < rise < n:
            kind = "PARTIAL"
        else:
            kind = "OVER"
        rows.append({"t1": t1, "t2": t2, "rise": rise, "commands": n, "kind": kind})
    kinds = [r["kind"] for r in rows]
    # a PARTIAL is allowed only at the edge of a run of FULL intervals
    bad_partial = [i for i, k in enumerate(kinds) if k == "PARTIAL"
                   and not ((i > 0 and kinds[i - 1] in ("IDLE",)) or i == 0
                            or (i + 1 < len(kinds) and kinds[i + 1] == "IDLE")
                            or i + 1 == len(kinds))]
    over = [i for i, k in enumerate(kinds) if k == "OVER"]
    full = [r for r in rows if r["kind"] == "FULL"]
    return rows, {"intervals": len(rows), "full": len(full), "idle": kinds.count("IDLE"),
                  "partial": kinds.count("PARTIAL"), "over": len(over),
                  "partial_not_at_an_edge": len(bad_partial),
                  "full_rise_minus_commands": sum(r["rise"] - r["commands"] for r in full),
                  "ok": not over and not bad_partial and len(full) > 0}


def from_zero(samples, cmd_times, t_arm):
    """Spec 3.1 (user, 2026-10-12): the would-transmit count resets on every arm,
    as tx_ok does. So the counter itself, not its rise, is bounded by the commands
    since the arm: the first sample after the arm and the last sample each read no
    more than the commands in (t_arm, t] + 1. A counter carried from an earlier arm
    in the same boot fails both. t_arm None = armed before the first command."""
    after = [(t, c) for t, c in samples if t_arm is None or t >= t_arm]
    if not after:
        return {"ok": None, "why": "no counter sample after the arm"}
    lo = -1.0 if t_arm is None else t_arm

    def n_to(t):
        return bisect.bisect_right(cmd_times, t) - bisect.bisect_right(cmd_times, lo)
    (t1, c1), (t2, c2) = after[0], after[-1]
    r = {"first_counter": c1, "commands_to_first": n_to(t1),
         "end_counter": c2, "commands_to_end": n_to(t2)}
    r["ok"] = c1 <= r["commands_to_first"] + 1 and c2 <= r["commands_to_end"] + 1
    return r


def decode_diag(wire):
    import cantools
    db = cantools.database.load_file(DBC)
    out = []
    for t, i, d in wire:
        if i in (0x7F1, 0x7F2, 0x7F8):
            try:
                out.append((t, i, db.decode_message(i, d, decode_choices=False)))
            except Exception:
                pass
    return out


def analyse(run):
    import provenance_classifier as P
    d = json.load(open(run, encoding="utf-8"))
    arm = list(d["arms"])[0]
    a = d["arms"][arm]
    r = P.rejudge(run, arm, keep_frames=True)
    wire = P.read_witness(os.path.join(os.path.dirname(run), a["witness_raw"]))
    diag = decode_diag(wire)
    cmds = sorted(r["vcm_times"])
    rep = r["replayed_wire_times"]
    idx = lambda t: bisect.bisect_right(rep, t) - 1
    mode = d.get("mode")
    if mode == "passive":
        samples = [(t, s["diag_would_tx"]) for t, i, s in diag if i == 0x7F8]
    else:
        samples = [(t, s["diag_tx_ok"]) for t, i, s in diag if i == 0x7F2]
    rows, summ = judge_intervals(samples, cmds)
    st = [(t, s) for t, i, s in diag if i == 0x7F1]
    s2 = [(t, s) for t, i, s in diag if i == 0x7F8]
    modes = sorted({s["diag_mode"] for _t, s in st})
    latch = next(((idx(t), round(t - rep[0], 3)) for t, s in st if int(s["diag_flags"]) & 0x80), None)
    live_pages = [idx(t) for t, s in st if int(s["diag_flags"]) & 0x20]
    seq = []
    for t, s in s2:
        v = (int(s["diag_arm_block"]), int(s["diag_abort_reason"]))
        if not seq or seq[-1][1:] != v:
            seq.append((idx(t),) + v)
    dev = json.dumps(a.get("device") or {})
    import re
    g = lambda k: (lambda m: m.group(1) if m else None)(re.search(r'"%s": ("[^"]*"|\d+)' % k, dev))
    return {"run": os.path.basename(run), "mode": mode, "diag_modes": modes,
            "provenance": r["verdict"], "device_0x051": r["device_frames"],
            "replayed_found": "%s/%s" % (r["replayed_found"], r["replayed_frames"]),
            "counter": "diag_would_tx" if mode == "passive" else "diag_tx_ok",
            "counter_end_page": samples[-1][1] if samples else None,
            "status_page_would_tx": g("would_tx"), "status_page_tx_ok": g("tx_ok"),
            "intervals": summ,
            # armed before the replay (the e4 arms' mode is POSTed first)
            "from_zero": from_zero(samples, cmds, None),
            "live_pages_replay_index": [live_pages[0], live_pages[-1]] if live_pages else None,
            "first_abort_latched_page": latch,
            "block_abort_sequence_by_replay_index": seq,
            "abort_reason_end": g("abort_reason")}


def main():
    for run in sys.argv[1:]:
        if run.endswith("_provenance.json"):
            continue
        print(json.dumps(analyse(run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
