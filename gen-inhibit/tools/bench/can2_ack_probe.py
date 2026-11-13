"""Does anything on the X2's CAN2 (the charger segment) ACK? canchg sends one frame and the
link's own counters say whether it was confirmed.

Gen-inhibit tester, 2026-11-13, the gen-inhibit reviewer's addition to the jammer port's CAN2
isolation check (x2_test_images SPEC T8): canchg and the X2's MCP2515 are the only nodes on
the charger segment, so with the jammer image (its MCP2515 held in configuration mode,
"neither transmits nor ACKs") a frame canchg sends gets no ACK. Its can-fail is the same probe
with the X2 on an SLCAN image opened normal on CAN2, which must ACK.

  py -3.13 -B gen-inhibit/tools/bench/can2_ack_probe.py --expect no_ack|acked [--role canchg]

What it does, bounded (each step recorded, runs/can2_ack_probe_<stamp>.json, never
overwritten):
  1. the bench lock must be held by a gen-inhibit role with this process or an ancestor as
     its pid (kvaser_bus.require_bench), and the role's link must be down (nobody else on it);
  2. `canctl up <role> 500000`, normal mode, not listen-only. No berr: canchg's gs_usb does not
     offer BERR-REPORTING (its ctrlmode_supported is LOOPBACK, LISTEN-ONLY, CC-LEN8-DLC, read
     2026-11-13), so the probe judges on the counters below, and on whatever error frames the
     driver passes up;
  3. one frame, 0x7A1 (the bench sends only 0x7A0-0x7AF), through python-can's socketcan;
  4. OBSERVE_S of watching, then the link's counters again, then `canctl down <role>`, which
     also drops the frame if it is still being retried.

THE JUDGE (judge_probe): NO_ACK when the link shows the retries: an ACK-error frame, a
controller TX warning/passive frame, the error-warning or error-passive counter rising, or the
state no longer ERROR-ACTIVE, whatever the tx counter says. ACKED when the frame was counted
sent (tx packets +1) with no retries and no error frame of any kind. UNCLEAR otherwise (for
example nothing counted and nothing seen: the frame may never have left).
The tx counter alone is not an ACK on canchg's gs_usb: on madhouse, 2026-11-13 01:08, with the
X2 on slcan_spi and canx2 detached (so nothing on CAN2 could ACK), tx packets rose by 1 while
the link reported an ACK-error frame and went ERROR-PASSIVE (runs/jammerbench_6fc4067/,
can2_ack_probe_20261113T010803.json). The first judge counted tx +1 as confirmation and so
came out UNCLEAR there; the gen-inhibit reviewer's ruling of 2026-11-13 names an ACK error or
TEC rising as no-ACK evidence on its own. check(expect, verdict): PASS when they agree, FAIL
when it is the other one, INCONCLUSIVE when UNCLEAR.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CANCTL = ["sudo", "-n", "/usr/local/sbin/canctl"]
BITRATE = 500000
PROBE_ID = 0x7A1
OBSERVE_S = 1.0
CAN_ERR_FLAG = 0x20000000
CAN_ERR_CRTL = 0x00000004
CAN_ERR_ACK = 0x00000020
CRTL_TX_WARNING = 0x08
CRTL_TX_PASSIVE = 0x20


def parse_link(js):
    """`ip -d -s -j link show <role>` -> {state, tx_packets, error_warning, error_passive,
    bus_error, operstate}; None for anything missing."""
    d = (js or [{}])[0]
    li = d.get("linkinfo") or {}
    xs = li.get("info_xstats") or {}
    return {"operstate": d.get("operstate"),
            "state": (li.get("info_data") or {}).get("state"),
            "tx_packets": ((d.get("stats64") or {}).get("tx") or {}).get("packets"),
            "error_warning": xs.get("error_warning"), "error_passive": xs.get("error_passive"),
            "bus_error": xs.get("bus_error")}


def err_kinds(err_frames):
    """[(can_id, data bytes)] error frames -> {ack, tx_warn_or_passive, other}."""
    out = {"ack": 0, "tx_warn_or_passive": 0, "other": 0}
    for cid, data in err_frames:
        if cid & CAN_ERR_ACK:
            out["ack"] += 1
        elif cid & CAN_ERR_CRTL and len(data) > 1 and data[1] & (CRTL_TX_WARNING | CRTL_TX_PASSIVE):
            out["tx_warn_or_passive"] += 1
        else:
            out["other"] += 1
    return out


def judge_probe(before, after, err_frames):
    """before/after: parse_link() of the role; err_frames: [(can_id, data)] seen while
    watching. -> {verdict: ACKED|NO_ACK|UNCLEAR, why, tx_delta, kinds, ...}."""
    def delta(k):
        a, b = after.get(k), before.get(k)
        return None if a is None or b is None else a - b
    tx = delta("tx_packets")
    kinds = err_kinds(err_frames)
    retried = [w for w, hit in (
        ("ACK-error frame", kinds["ack"] > 0),
        ("TX warning/passive frame", kinds["tx_warn_or_passive"] > 0),
        ("error_warning rose", (delta("error_warning") or 0) > 0),
        ("error_passive rose", (delta("error_passive") or 0) > 0),
        ("state %s" % after.get("state"), after.get("state") not in (None, "ERROR-ACTIVE")))
        if hit]
    out = {"tx_delta": tx, "kinds": kinds, "retried": retried,
           "error_warning_delta": delta("error_warning"),
           "error_passive_delta": delta("error_passive")}
    if retried:
        # whatever the tx counter says: gs_usb counts a frame nobody ACKed (2026-11-13)
        out["verdict"], out["why"] = "NO_ACK", "retries seen (tx delta %s): %s" % (tx, ", ".join(retried))
    elif tx is not None and tx >= 1 and not any(kinds.values()):
        out["verdict"], out["why"] = "ACKED", "confirmed (tx packets +%s), no error frame, no retries" % tx
    else:
        out["verdict"], out["why"] = "UNCLEAR", "tx delta %s, retries seen %s" % (tx, retried)
    return out


def check(expect, verdict):
    if verdict == "UNCLEAR":
        return "INCONCLUSIVE"
    return "PASS" if verdict == expect.upper() else "FAIL"


def canctl(*args):
    p = subprocess.run(CANCTL + list(args), capture_output=True, text=True, timeout=30)
    return {"argv": "canctl " + " ".join(args), "rc": p.returncode,
            "out": (p.stdout + p.stderr).strip()[-400:]}


def link(role):
    p = subprocess.run(["ip", "-d", "-s", "-j", "link", "show", role], capture_output=True,
                       text=True, timeout=10)
    return parse_link(json.loads(p.stdout)) if p.returncode == 0 and p.stdout.strip() else None


def probe(role="canchg", observe_s=OBSERVE_S):
    """Steps 2-4 of the docstring on a link that is down. The record of what was done and
    judge_probe's result; the link is brought down again whatever happens after the up."""
    import can
    rec = {"role": role, "probe_id": "0x%03X" % PROBE_ID, "observe_s": observe_s, "steps": []}
    rec["link_at_start"] = link(role)
    if not rec["link_at_start"] or rec["link_at_start"].get("operstate") != "DOWN":
        rec["refused"] = "%s is not down at the start (%s): someone may be using it" % (
            role, rec["link_at_start"])
        return rec
    up = canctl("up", role, str(BITRATE))
    rec["steps"].append(up)
    try:
        if up["rc"] != 0:
            rec["refused"] = "canctl up failed"
            return rec
        bus = can.Bus(interface="socketcan", channel=role)
        errs = []
        try:
            time.sleep(0.3)
            while bus.recv(timeout=0.0) is not None:
                pass
            rec["before"] = link(role)
            t0 = time.time()
            bus.send(can.Message(arbitration_id=PROBE_ID, is_extended_id=False,
                                 data=bytes([0xA1, 0, 0, 0, 0, 0, 0, 0])))
            rec["sent_at"] = t0
            frames = 0
            while time.time() < t0 + observe_s:
                m = bus.recv(timeout=0.05)
                if m is None:
                    continue
                if m.is_error_frame:
                    errs.append((m.arbitration_id | CAN_ERR_FLAG, bytes(m.data)))
                else:
                    frames += 1
            rec["after"] = link(role)
            rec["frames_seen"] = frames
            rec["error_frames"] = [("0x%08X" % c, d.hex()) for c, d in errs]
        finally:
            bus.shutdown()
    finally:
        rec["steps"].append(canctl("down", role))
        rec["link_at_end"] = link(role)
    rec["judge"] = judge_probe(rec["before"], rec["after"], errs)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", choices=("no_ack", "acked"), required=True)
    ap.add_argument("--role", default="canchg", choices=("canchg",))
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import gi_paths
    from kvaser_bus import require_bench, BenchNotHeld
    try:
        require_bench()
    except BenchNotHeld as e:
        print("REFUSED: %s (nothing run, no record)" % e)
        return 2
    runs = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
    rec = {"when": time.strftime("%Y%m%dT%H%M%S"), "tool": "can2_ack_probe", "expect": a.expect}
    rec.update(probe(a.role))
    if "refused" in rec:
        rec["verdict"] = "REFUSED"
    else:
        rec["verdict"] = check(a.expect, rec["judge"]["verdict"])
    os.makedirs(runs, exist_ok=True)
    path = os.path.join(runs, "can2_ack_probe_%s.json" % rec["when"])
    with open(path, "x", encoding="ascii") as fh:
        json.dump(rec, fh, indent=1)
    print("verdict %s | expect %s | %s | wrote %s" % (
        rec["verdict"], a.expect, rec.get("judge", {}).get("why", rec.get("refused")), path))
    return 0 if rec["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE") else 2


if __name__ == "__main__":
    sys.exit(main())
