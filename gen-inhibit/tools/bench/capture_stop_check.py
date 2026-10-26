#!/usr/bin/env python3
"""Did a capture stopped by capture_window.py come out complete? (reviewer, 2026-10-20)

    py -3.14 -B gen-inhibit/tools/bench/capture_stop_check.py <folder> [--out F.json]

For every B21 capture (B21_<commit>_<body>_<stamp>.pcapng, with B21_capture_<body>_<stamp>.json
and B21_probe_window_<body>_<stamp>.json) and every B11raw capture (<n>.pcapng, with
capture_<n>.json and window_<n>.json) in the folder, PASS only when all hold:
  - tshark reads the whole file: exit 0 and no "cut short" on its error output;
  - the window's last client FIN on port 80 is in it (the last laptop -> device FIN
    between the window's start and end), and after it a segment from the device on the
    same connection (its FIN or its ACK of the client's);
  - capture_window's record says it stopped on the stop file, cleanly, rc 0, and the
    stop was asked for after the window ended.
Anything else is INCONCLUSIVE (harness), never FAIL: this judges the capture, not the
device. A folder with no capture to judge is INCONCLUSIVE too. Exit 0 only when every
capture PASSes. Reads files only (unelevated tshark). Gen-inhibit tester, 2026-10-20.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

TSHARK = os.environ.get("GI_TSHARK") or r"C:\Program Files\Wireshark\tshark.exe"
DEVICE = "192.168.80.1"
FIELDS = ("frame.time_epoch", "ip.src", "tcp.srcport", "tcp.dstport", "tcp.flags.fin", "tcp.flags.reset")


def read(pcap):
    """(tshark rc, its error output, rows) for port-80 segments: (t, src, sport, dport, fin, rst)."""
    cmd = [TSHARK, "-r", pcap, "-Y", "tcp.port==80", "-T", "fields"]
    for f in FIELDS:
        cmd += ["-e", f]
    r = subprocess.run(cmd, capture_output=True, text=True)
    rows = []
    for line in r.stdout.splitlines():
        p = line.split("\t")
        if len(p) < len(FIELDS) or not p[0]:
            continue
        flag = lambda v: v in ("1", "True", "true")
        rows.append((float(p[0]), p[1], int(p[2]), int(p[3]), flag(p[4]), flag(p[5])))
    return r.returncode, r.stderr, rows


def last_fin_answered(rows, start, end, device=DEVICE):
    """(the last client FIN in [start, end] as (t, client port), or None; whether a device
    segment on that connection follows it, and what it was)."""
    fins = [(t, sp) for t, src, sp, dp, fin, rst in rows if src != device and fin and dp == 80 and start <= t <= end]
    if not fins:
        return None, False, None
    t_fin, port = max(fins)
    after = [(fin, rst) for t, src, sp, dp, fin, rst in rows if src == device and dp == port and t > t_fin]
    if not after:
        return (t_fin, port), False, None
    return (t_fin, port), True, "fin" if any(f for f, _ in after) else ("rst" if any(r for _, r in after) else "ack")


def judge(pcap, cap_rec, window):
    """One capture: {"verdict", "reasons", ...}. cap_rec and window are dicts or None."""
    reasons = []
    out = {"pcap": os.path.basename(pcap)}
    rc, err, rows = read(pcap)
    out.update(tshark_rc=rc, cut_short="cut short" in err, segments=len(rows))
    if rc != 0:
        reasons.append("tshark exit %d" % rc)
    if "cut short" in err:
        reasons.append("tshark: cut short")
    if window is None:
        reasons.append("no window record")
    else:
        start = window.get("probe_start", window.get("arm_start"))
        end = window.get("probe_end", window.get("arm_end"))
        fin, answered, how = last_fin_answered(rows, start, end)
        out.update(window=[start, end], last_client_fin=fin, device_answer=how)
        if fin is None:
            reasons.append("no client FIN in the window")
        elif not answered:
            reasons.append("nothing from the device after the last client FIN")
    if cap_rec is None:
        reasons.append("no capture record")
    else:
        out.update(how=cap_rec.get("how"), stopped_cleanly=cap_rec.get("stopped_cleanly"), rc=cap_rec.get("rc"),
                   t_stop_requested=cap_rec.get("t_stop_requested"))
        if cap_rec.get("how") != "stopfile":
            reasons.append("stopped by %s, not the stop file" % cap_rec.get("how"))
        if cap_rec.get("stopped_cleanly") is not True or cap_rec.get("rc") != 0:
            reasons.append("not stopped cleanly (rc %s)" % cap_rec.get("rc"))
        if window is not None and not (cap_rec.get("t_stop_requested") or 0) > (out["window"][1] or 0):
            reasons.append("stop asked for before the window ended")
    out["reasons"] = reasons
    out["verdict"] = "PASS" if not reasons else "INCONCLUSIVE (harness)"
    return out


def load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def triples(folder):
    """(pcap, capture record path, window path) for every B21 and B11raw capture."""
    out = []
    for p in sorted(glob.glob(os.path.join(folder, "B21_*.pcapng"))):
        m = re.match(r"B21_[0-9a-f]+_(\w+?)_(\d{8}T\d{6})\.pcapng$", os.path.basename(p))
        if m:
            body, stamp = m.groups()
            out.append((p, os.path.join(folder, "B21_capture_%s_%s.json" % (body, stamp)),
                        os.path.join(folder, "B21_probe_window_%s_%s.json" % (body, stamp))))
    for p in sorted(glob.glob(os.path.join(folder, "B11raw_e4_*.pcapng"))):
        n = os.path.basename(p)[:-len(".pcapng")]
        out.append((p, os.path.join(folder, "capture_%s.json" % n), os.path.join(folder, "window_%s.json" % n)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rows = []
    for folder in a.folder:
        for pcap, rec, win in triples(folder):
            rows.append(judge(pcap, load(rec), load(win)))
    for r in rows:
        print("%-24s %s  %s" % (r["verdict"], r["pcap"], "; ".join(r["reasons"]) or
                                "device answered the last client FIN with %s" % r["device_answer"]))
    ok = bool(rows) and all(r["verdict"] == "PASS" for r in rows)
    if not rows:
        print("INCONCLUSIVE (harness)   no B21 or B11raw capture in %s" % ", ".join(a.folder))
    print("%d captures, %d PASS: %s" % (len(rows), sum(r["verdict"] == "PASS" for r in rows),
                                        "PASS" if ok else "INCONCLUSIVE (harness)"))
    if a.out:
        with open(a.out, "w") as fh:
            json.dump({"captures": rows, "verdict": "PASS" if ok else "INCONCLUSIVE (harness)"}, fh, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
