#!/usr/bin/env python3
"""Test capture_stop_check.py on synthetic captures read by the real tshark. With
--mutants, run its mutants.

    py -3.14 -B gen-inhibit/tools/bench/test_capture_stop_check.py [--mutants]

The captures are classic pcap files written here (Ethernet, IPv4, TCP, port 80; tshark
reads them by content whatever the name). Cases: a complete capture whose last client
FIN the device answers (PASS, with a FIN and with only an ACK); the same file cut short;
the last client FIN unanswered while an earlier one was answered; the only client FIN
before the window; a device segment after the FIN on another connection only; a capture
record stopped by the max bound, not clean, stopped before the window ended, missing; a
folder (B21 and B11raw names paired with their records), an empty folder, and a folder
with one bad capture. CAPTURE_STOP_CHECK overrides the script under test. Scratch in the
worktree's scratch/. Gen-inhibit tester, 2026-10-20.
"""
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("CAPTURE_STOP_CHECK") or os.path.join(HERE, "capture_stop_check.py")
DEV, LAPTOP = "192.168.80.1", "192.168.80.2"
FIN, SYN, RST, ACK = 0x01, 0x02, 0x04, 0x10
WIN = {"probe_start": 1000.0, "probe_end": 1010.0, "tail_s": 10}
REC = {"how": "stopfile", "stopped_cleanly": True, "rc": 0, "t_start": 997.0, "t_stop_requested": 1020.0,
       "t_exit": 1020.2}
fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def ip(a):
    return bytes(int(x) for x in a.split("."))


def frame(src, dst, sport, dport, flags):
    tcp = struct.pack("!HHIIBBHHH", sport, dport, 1, 1, 5 << 4, flags, 64240, 0, 0)
    iph = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 1, 0, 64, 6, 0, ip(src), ip(dst))
    return b"\x02\x00\x00\x00\x00\x01" + b"\x02\x00\x00\x00\x00\x02" + b"\x08\x00" + iph + tcp


def pcap(path, pkts):
    """pkts: (t, from_device, client_port, flags)."""
    out = [struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)]
    for t, from_dev, port, flags in pkts:
        f = frame(DEV, LAPTOP, 80, port, flags) if from_dev else frame(LAPTOP, DEV, port, 80, flags)
        out.append(struct.pack("<IIII", int(t), int(round((t - int(t)) * 1e6)), len(f), len(f)) + f)
    open(path, "wb").write(b"".join(out))


def conn(port, t0, t_fin, answer=FIN | ACK):
    """A connection: SYN, SYN-ACK, ACK, the client's FIN at t_fin, the device's answer."""
    ps = [(t0, False, port, SYN), (t0 + 0.003, True, port, SYN | ACK), (t0 + 0.004, False, port, ACK),
          (t_fin, False, port, FIN | ACK)]
    if answer is not None:
        ps.append((t_fin + 0.004, True, port, answer))
        ps.append((t_fin + 0.0041, False, port, ACK))
    return ps


def load():
    spec = importlib.util.spec_from_file_location("csc", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cli(folder):
    r = subprocess.run([sys.executable, "-B", SCRIPT, folder], capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout


def tests():
    tmp = gi_paths.scratch("capture-stop-check-test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        M = load()
        good = os.path.join(tmp, "good.pcapng")
        pcap(good, conn(50001, 1001.0, 1004.0) + conn(50002, 1008.0, 1009.5))
        r = M.judge(good, REC, WIN)
        check("complete capture: PASS, the last client FIN (port 50002) answered with a FIN",
              r["verdict"] == "PASS" and r["last_client_fin"][1] == 50002 and r["device_answer"] == "fin", r)
        ackonly = os.path.join(tmp, "ackonly.pcapng")
        pcap(ackonly, conn(50001, 1001.0, 1009.0, answer=ACK))
        r = M.judge(ackonly, REC, WIN)
        check("the device's answer is only an ACK: PASS, recorded as ack",
              r["verdict"] == "PASS" and r["device_answer"] == "ack", r)
        cut = os.path.join(tmp, "cut.pcapng")
        open(cut, "wb").write(open(good, "rb").read()[:-10])
        r = M.judge(cut, REC, WIN)
        check("cut short: INCONCLUSIVE, tshark's cut-short warning named",
              r["verdict"].startswith("INCONCLUSIVE") and "tshark: cut short" in r["reasons"], r)
        check("cut short: tshark's exit status named", any(x.startswith("tshark exit") for x in r["reasons"]), r)
        unans = os.path.join(tmp, "unans.pcapng")
        pcap(unans, conn(50001, 1001.0, 1004.0) + conn(50002, 1008.0, 1009.5, answer=None))
        r = M.judge(unans, REC, WIN)
        check("the last client FIN unanswered (an earlier one answered): INCONCLUSIVE",
              r["verdict"].startswith("INCONCLUSIVE")
              and "nothing from the device after the last client FIN" in r["reasons"], r)
        early = os.path.join(tmp, "early.pcapng")
        pcap(early, conn(50001, 990.0, 995.0))
        r = M.judge(early, REC, WIN)
        check("the only client FIN before the window: INCONCLUSIVE",
              r["verdict"].startswith("INCONCLUSIVE") and "no client FIN in the window" in r["reasons"], r)
        other = os.path.join(tmp, "other.pcapng")
        pcap(other, conn(50002, 1008.0, 1009.5, answer=None) + [(1009.6, True, 50003, ACK)])
        r = M.judge(other, REC, WIN)
        check("a device segment after the FIN on another connection only: INCONCLUSIVE",
              r["verdict"].startswith("INCONCLUSIVE")
              and "nothing from the device after the last client FIN" in r["reasons"], r)
        for name, rec, want in [
                ("stopped by the max bound", dict(REC, how="max"), "stopped by max, not the stop file"),
                ("not stopped cleanly", dict(REC, stopped_cleanly=False, rc=1), "not stopped cleanly (rc 1)"),
                ("stop asked for before the window ended", dict(REC, t_stop_requested=1009.0),
                 "stop asked for before the window ended"),
                ("no capture record", None, "no capture record")]:
            r = M.judge(good, rec, WIN)
            check("%s: INCONCLUSIVE" % name, r["verdict"].startswith("INCONCLUSIVE") and want in r["reasons"], r)
        r = M.judge(good, REC, None)
        check("no window record: INCONCLUSIVE", r["verdict"].startswith("INCONCLUSIVE")
              and "no window record" in r["reasons"], r)
        f1 = os.path.join(tmp, "folder1")
        os.makedirs(f1)
        shutil.copy(good, os.path.join(f1, "B21_e3d9437_image_20261020T030000.pcapng"))
        json.dump(REC, open(os.path.join(f1, "B21_capture_image_20261020T030000.json"), "w"))
        json.dump(WIN, open(os.path.join(f1, "B21_probe_window_image_20261020T030000.json"), "w"))
        n = "B11raw_e4_quiesce_inhibit_e3d9437_20261020T031000_arm1"
        shutil.copy(good, os.path.join(f1, n + ".pcapng"))
        json.dump(REC, open(os.path.join(f1, "capture_%s.json" % n), "w"))
        json.dump({"arm_start": 1000.0, "arm_end": 1010.0, "tail_s": 5}, open(os.path.join(f1, "window_%s.json" % n), "w"))
        rc, out = cli(f1)
        check("folder: a B21 and a B11raw capture paired with their records, exit 0",
              rc == 0 and "2 captures, 2 PASS: PASS" in out, out)
        f2 = os.path.join(tmp, "folder2")
        os.makedirs(f2)
        rc, out = cli(f2)
        check("empty folder: exit 1, INCONCLUSIVE", rc == 1 and "no B21 or B11raw capture" in out, out)
        shutil.copy(unans, os.path.join(f1, "B21_e3d9437_multipart_20261020T030500.pcapng"))
        json.dump(REC, open(os.path.join(f1, "B21_capture_multipart_20261020T030500.json"), "w"))
        json.dump(WIN, open(os.path.join(f1, "B21_probe_window_multipart_20261020T030500.json"), "w"))
        rc, out = cli(f1)
        check("folder with one bad capture: exit 1", rc == 1 and "3 captures, 2 PASS" in out, out)
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("cut short not looked at", '    if "cut short" in err:\n', '    if False:\n'),
    ("tshark's exit not looked at", '    if rc != 0:\n', '    if False:\n'),
    ("a client FIN before the window counts", 'and dp == 80 and start <= t <= end]', 'and dp == 80 and t <= end]'),
    ("the first client FIN in the window, not the last", '    t_fin, port = max(fins)\n', '    t_fin, port = min(fins)\n'),
    ("any device segment after the FIN counts", 'if src == device and dp == port and t > t_fin]',
     'if src == device and t > t_fin]'),
    ("the max bound accepted", 'if cap_rec.get("how") != "stopfile":', 'if cap_rec.get("how") not in ("stopfile", "max"):'),
    ("cleanliness not looked at", 'if cap_rec.get("stopped_cleanly") is not True or cap_rec.get("rc") != 0:',
     'if False:'),
    ("the stop time not looked at", 'if window is not None and not (cap_rec.get("t_stop_requested") or 0)',
     'if False and not (cap_rec.get("t_stop_requested") or 0)'),
    ("an empty folder passes", '    ok = bool(rows) and all(', '    ok = all('),
    ("B11raw captures not paired", 'glob.glob(os.path.join(folder, "B11raw_e4_*.pcapng"))',
     'glob.glob(os.path.join(folder, "B11raw_e4_*.pcap"))'),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read().replace("\r\n", "\n")
    base = gi_paths.scratch("mutants-capture_stop_check")
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        path = os.path.join(d, "capture_stop_check.py")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, CAPTURE_STOP_CHECK=path), timeout=600)
        fl = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        dead = any("ran without raising" not in f for f in fl)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                  len(fl), ("; " + fl[0][:100]) if fl else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else tests())
