"""OBSERVE is listen-only in hardware: does a transmitter see ACK errors? (spec 12.4)

Gen-inhibit tester, 2026-10-10. Spec 12.4: "OBSERVE is listen-only in hardware:
with the WiCAN as the only node able to ACK, the transmitter sees ACK errors;
with the WiCAN in RESPOND, it does not. `tx_ok=0` is not evidence." Spec 3
table: OBSERVE is "controller in listen-only -- nothing at all on the wire, no
ACK, no diag". Listed in spec 13 as "Bench, no harness".

Making the WiCAN the only possible ACKer on its segment:
  - the Kvaser is CLOSED (it cannot be silent: it ACKs whenever it is open,
    bench-holder.md 2026-10-09) -- this tool never opens it;
  - the X2 is held in ROM download mode (x2_hold.py) so its TWAI is not running;
    --x2 records which state the caller put it in;
  - the Innomaker is on the X2's CAN2, not this segment (bench-holder.md);
  - the PCAN is the transmitter. A transmitter never ACKs its own frame.

Each ARM: set the WiCAN's mode (POST), read the page back (mode, bus_on,
drv.state), open the PCAN in normal mode, send ONE frame (--id, never 0x051),
wait --hold-s, read the PCAN's status, close it. A frame nobody ACKs is
retransmitted until the PCAN is closed, and each ACK error raises its TEC by 8
up to error passive (128, where the ACK-error exception stops it), so the
status reads BUSHEAVY/BUSPASSIVE or BUSLIGHT/BUSWARNING; an ACKed frame leaves
it OK. A fresh open per arm starts each arm at TEC 0.

The arms and what each must show (--modes, default 0,1,2,1,0):
  mode 0 (OFF, driver not installed) -- the CONTROL: nothing can ACK, so ACK
      errors. If it reads OK, something else on the segment ACKs and the run is
      VOID, because the WiCAN is not then the only possible ACKer.
  mode 1 (OBSERVE) -- ACK errors (listen-only).
  mode 2 (RESPOND) -- no ACK errors (the positive control the spec names).
An arm whose page does not read back the requested mode is INVALID.
Ends by setting mode 0 and reading it back.

    py -3.14 observe_ack_probe.py --power "..." --x2 held [--modes 0,1,2,1,0]
Writes runs/observe_ack_probe_<stamp>.json.
"""
import argparse
import json
import os
import sys
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()

EXPECT_ACK_ERRORS = {0: True, 1: True, 2: False}
MODE_NAMES = {0: "OFF", 1: "OBSERVE", 2: "RESPOND"}


def status_name(code):
    from can.interfaces.pcan import basic as B
    if code == B.PCAN_ERROR_OK:
        return "OK"
    names = [n[len("PCAN_ERROR_"):] for n in dir(B) if n.startswith("PCAN_ERROR_")
             and n not in ("PCAN_ERROR_ANYBUSERR", "PCAN_ERROR_OK")
             and getattr(B, n) and (code & getattr(B, n)) == getattr(B, n)]
    return "|".join(sorted(names)) or ("0x%x" % code)


def ack_errors(status):
    """True if the PCAN's status shows bus errors (warning or passive level)."""
    return any(x in status.split("|") for x in
               ("BUSLIGHT", "BUSHEAVY", "BUSWARNING", "BUSPASSIVE", "BUSOFF"))


def judge(arms):
    """arms: [{mode, mode_read, ack_errors}] -> verdict dict."""
    rows = []
    for a in arms:
        if a.get("mode_read") != a["mode"]:
            rows.append(dict(a, result="INVALID (page read mode %r)" % a.get("mode_read")))
            continue
        exp = EXPECT_ACK_ERRORS.get(a["mode"])
        rows.append(dict(a, expected_ack_errors=exp,
                         result="as specified" if a["ack_errors"] == exp else "NOT as specified"))
    controls = [r for r in rows if r["mode"] == 0 and not r["result"].startswith("INVALID")]
    void = not controls or any(not r["ack_errors"] for r in controls)
    tested = {m for m in (1, 2) if any(r["mode"] == m and not r["result"].startswith("INVALID")
                                       for r in rows)}
    if void:
        verdict = ("VOID: no valid mode-0 control" if not controls else
                   "VOID: the mode-0 control was ACKed, so the WiCAN is not the only possible ACKer")
    elif tested != {1, 2}:
        verdict = "INCOMPLETE: modes tested %s, need OBSERVE and RESPOND" % sorted(tested)
    elif any(r["result"] != "as specified" for r in rows):
        verdict = "NOT as specified"
    else:
        verdict = "as specified"
    return {"rows": rows, "verdict": verdict}


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--power", required=True, help="how the WiCAN is powered; recorded")
    ap.add_argument("--x2", required=True, choices=["held", "app", "unpowered"],
                    help="the X2's state, as the caller set it; recorded")
    ap.add_argument("--modes", default="0,1,2,1,0")
    ap.add_argument("--id", default="0x6A5")
    ap.add_argument("--hold-s", type=float, default=0.5)
    ap.add_argument("--settle-s", type=float, default=1.5)
    return ap


def one_arm(LF, can, mode, a):
    rec = {"mode": mode, "post_ok": LF.dev_post("/gen_inhibit_set?mode=%d&offset=500" % mode)}
    time.sleep(a.settle_s)
    p = LF.page() or {}
    drv = p.get("drv") or {}
    rec.update({"mode_read": p.get("mode"), "bus_on": p.get("bus_on"),
                "drv_state": drv.get("state"), "rec_before": drv.get("rec")})
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    try:
        st0 = bus.status()
        bus.send(can.Message(arbitration_id=int(a.id, 16), is_extended_id=False,
                             data=bytes([0xA5, mode, 0, 0, 0, 0, 0, 0])))
        t0 = time.perf_counter()
        n_rx = n_err = 0
        while time.perf_counter() - t0 < a.hold_s:
            m = bus.recv(timeout=0.05)
            if m is not None:
                n_rx += 1
                n_err += 1 if m.is_error_frame else 0
        st = bus.status()
    finally:
        bus.shutdown()
    rec.update({"pcan_status_open": status_name(st0), "pcan_status": status_name(st),
                "pcan_rx": n_rx, "pcan_error_frames": n_err})
    rec["ack_errors"] = ack_errors(rec["pcan_status"])
    p2 = LF.page() or {}
    rec["drv_after"] = {k: (p2.get("drv") or {}).get(k) for k in ("state", "tec", "rec", "rx_missed",
                                                                  "rx_overrun", "bus_error")}
    return rec


def main():
    a = build_parser().parse_args()
    import can
    import lowid_flood_skip_bench as LF
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "power": a.power, "x2": a.x2, "args": vars(a), "arms": []}
    try:
        for m in [int(x) for x in a.modes.split(",")]:
            arm = one_arm(LF, can, m, a)
            rec["arms"].append(arm)
            print("mode %d (%s): page mode %s, drv %s -> PCAN %s (rx %d, err frames %d)"
                  % (m, MODE_NAMES.get(m), arm["mode_read"], arm["drv_state"],
                     arm["pcan_status"], arm["pcan_rx"], arm["pcan_error_frames"]))
    finally:
        LF.dev_post("/gen_inhibit_set?mode=0")
        time.sleep(1.0)
        rec["mode_at_end"] = (LF.page() or {}).get("mode")
    rec["judge"] = judge(rec["arms"])
    path = os.path.join(RUNS, "observe_ack_probe_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    for r in rec["judge"]["rows"]:
        print("  mode %d: ack errors %s -> %s" % (r["mode"], r["ack_errors"], r["result"]))
    print("verdict: %s; mode at end %s" % (rec["judge"]["verdict"], rec["mode_at_end"]))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
