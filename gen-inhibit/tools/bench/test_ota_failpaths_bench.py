#!/usr/bin/env python3
"""Tests ota_failpaths_bench.py on loopback, with time scaled down: a server's receive timeout
of 0.5 s stands for the device's 5 s, so "4 in a row" drops 2 s after the last chunk, and the
judge is given a band of 1.7-2.8 s. Gen-inhibit tester, 2026-11-23.

Servers: drop on the 4th timeout in a row, the count restarting on each chunk (the spec's
behaviour); the same with a count that never restarts; drop on the 21st (the pre-fix 20,
'>'); stop reading for 3 s after 20 KiB with a 4 KiB receive buffer (the old whole-slot
erase), or for 0.3 s; read to the FIN and reply 500. Each kind is shown passing on the
spec's server and failing where it should:
  stall    PASS on 4-in-a-row; FAIL on 8 (dropped outside the band) and on 21 (not
           dropped within the test's 8 s)
  restart  PASS on a restarting count; FAIL on a count that never restarts (ends in a gap)
  full     W14 PASS with a 0.3 s pause; FAIL with a 3 s one; OTA row on the 303
  fin, rst the device's end recorded (REC), not judged
And the judges alone: longest_ack_stall on planted samples (idle time with nothing
outstanding never counts), judge_after on planted pages (same image, uptime continuous,
mode 0, in time; after `full`, restarted on the same image).
--mutants: copies of the probe with a judge or the sampler broken; each must fail a case.

    python3.13 -B test_ota_failpaths_bench.py [--mutants]
"""
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.environ.get("FP_TARGET") or os.path.join(HERE, "ota_failpaths_bench.py")
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location("fp", TARGET)
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

FAILS = []
BAND = (1.7, 2.8)
IMG = bytes((i * 7 + 3) & 0xFF for i in range(200000))


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:200]))
    if not ok:
        FAILS.append(label)


def server(handler, rcvbuf=None):
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if rcvbuf:
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)

    def run():
        try:
            c, _ = ls.accept()
        except OSError:
            return
        try:
            handler(c)
        except OSError:
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return ls.getsockname()[1], lambda: (ls.close(), t.join(5))


def dropper(max_timeouts, restart=True, tmo=0.5):
    def h(c):
        c.settimeout(tmo)
        n = 0
        while True:
            try:
                b = c.recv(4096)
            except socket.timeout:
                n += 1
                if n >= max_timeouts:
                    c.sendall(b"HTTP/1.1 408 Request Timeout\r\nContent-Length: 0\r\n\r\n")
                    return
                continue
            if not b:
                return
            if restart:
                n = 0
    return h


def pauser(pause, after=20480):
    def h(c):
        got = 0
        paused = False
        c.settimeout(10)
        while True:
            b = c.recv(1024)
            if not b:
                return
            got += len(b)
            if got >= after and not paused:
                paused = True
                time.sleep(pause)
            if b.endswith(("--%s--\r\n" % F.BOUNDARY).encode()):
                c.sendall(b"HTTP/1.1 303 See Other\r\nLocation: /\r\nContent-Length: 0\r\n\r\n")
                return
    return h


def to_fin(c):
    c.settimeout(10)
    while True:
        b = c.recv(4096)
        if not b:
            c.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n")
            return


def run(kind, handler, rcvbuf=None, **kw):
    port, close = server(handler, rcvbuf)
    try:
        rec = F.run_kind(kind, IMG, "127.0.0.1", port, stall_timeout=8, **kw)
    finally:
        close()
    return rec, F.judge(rec, BAND, 2.0)


def row(rows, name):
    return [r for r in rows if r[0] == name]


def cases():
    rec, rows = run("stall", dropper(4), cut=30000)
    w13 = row(rows, "W13")
    check("stall, 4 in a row: W13 PASS", len(w13) == 1 and w13[0][1] is True, rows)
    check("stall: the 408 recorded", rec.get("status", "").startswith("HTTP/1.1 408"), rec.get("status"))
    check("stall: samples carry snd_wnd and bytes_acked", any("snd_wnd" in r and "bytes_acked" in r for r in rec["samples"]))
    check("stall: W14 row passes (no pause)", row(rows, "W14") and row(rows, "W14")[0][1] is True, row(rows, "W14"))
    rec, rows = run("stall", dropper(21), cut=30000)
    check("stall, 21 in a row: W13 FAIL", row(rows, "W13")[0][1] is False, row(rows, "W13"))
    rec, rows = run("stall", dropper(8), cut=30000)
    check("stall, 8 in a row (dropped, outside the band): W13 FAIL", row(rows, "W13")[0][1] is False
          and "band" in row(rows, "W13")[0][2], row(rows, "W13"))

    rec, rows = run("restart", dropper(4), cut=30000, gap=1.2, gaps=3)
    w13 = row(rows, "W13")
    check("restart, count restarts: both W13 rows PASS", len(w13) == 2 and all(r[1] is True for r in w13), w13)
    rec, rows = run("restart", dropper(4, restart=False), cut=30000, gap=1.2, gaps=3)
    w13 = row(rows, "W13")
    check("restart, count never restarts: W13 FAIL, ended in a gap", any(r[1] is False and "gap" in r[2] for r in w13), w13)

    rec, rows = run("full", pauser(0.3), rcvbuf=4096)
    check("full, 0.3 s pause: W14 PASS", row(rows, "W14")[0][1] is True, row(rows, "W14"))
    check("full: OTA row PASS on the 303", row(rows, "OTA")[0][1] is True, row(rows, "OTA"))
    rec, rows = run("full", pauser(3.0), rcvbuf=4096)
    check("full, 3 s pause: W14 FAIL", row(rows, "W14")[0][1] is False, row(rows, "W14"))

    rec, rows = run("fin", to_fin, cut=30000)
    check("fin: the 500 recorded, not judged", row(rows, "W12")[0][1] is None and "500" in row(rows, "W12")[0][2], rows)
    rec, rows = run("rst", dropper(100), cut=30000)
    check("rst: recorded, not judged", row(rows, "W12")[0][1] is None and rec["end"] == "client_rst", rows)

    # the judges alone
    S = lambda t, sent, acked: {"t": t, "sent": sent, "bytes_acked": acked, "snd_wnd": 1000}
    samples = [S(0, 100, 0), S(1, 100, 50), S(2, 100, 50), S(3.5, 100, 50), S(4, 100, 100),
               S(9, 100, 100), S(20, 100, 100)]
    st, at = F.longest_ack_stall(samples)
    check("longest_ack_stall: 2.5 s with bytes outstanding, idle time not counted", st == 2.5 and at == 1, (st, at))
    before = {"elf_sha256": "aa", "uptime_ms": 1000, "mode": 0}
    good = {"ok": True, "page": {"elf_sha256": "aa", "uptime_ms": 5000, "mode": 0}, "delay": 2.0}
    check("after: same image, continuous, mode 0, in time: PASS", F.judge_after("stall", before, good, 10)[0][1] is True)
    for label, pg, delay in [("rebooted", {"elf_sha256": "aa", "uptime_ms": 500, "mode": 0}, 2.0),
                             ("other image", {"elf_sha256": "bb", "uptime_ms": 5000, "mode": 0}, 2.0),
                             ("mode 3", {"elf_sha256": "aa", "uptime_ms": 5000, "mode": 3}, 2.0),
                             ("late", {"elf_sha256": "aa", "uptime_ms": 5000, "mode": 0}, 15.0)]:
        r = F.judge_after("stall", before, {"ok": True, "page": pg, "delay": delay}, 10)
        check("after, %s: FAIL" % label, r[0][1] is False, r)
    check("after, no page: FAIL", F.judge_after("fin", before, {"ok": False, "error": "x"}, 10)[0][1] is False)
    check("after full, restarted on the same image: PASS",
          F.judge_after("full", before, {"ok": True, "page": {"elf_sha256": "aa", "uptime_ms": 4000}, "delay": 30}, 10)[0][1] is True)
    check("after full, not restarted: FAIL",
          F.judge_after("full", {"elf_sha256": "aa", "uptime_ms": 900000},
                        {"ok": True, "page": {"elf_sha256": "aa", "uptime_ms": 990000}, "delay": 30}, 10)[0][1] is False)


MUTANTS = [
    ("band ignored", "rows.append((\"W13\", band[0] <= d <= band[1],", "rows.append((\"W13\", True,"),
    ("window limit ignored", "rows.append((\"W14\", stall < window_limit,", "rows.append((\"W14\", True,"),
    ("idle counts as stall", "        outstanding = r[\"sent\"] > r[\"bytes_acked\"]", "        outstanding = True"),
    ("early end ignored", "rows.append((\"W13\", early is None,", "rows.append((\"W13\", True,"),
    # the first version's defect: the body handed over in one call and counted when it returned
    ("one send, counted after", "        for i in range(0, len(b), 1436):\n            seg = b[i:i + 1436]\n"
     "            smp.sent += len(seg)\n            s.sendall(seg)",
     "        s.sendall(b)\n        smp.sent += len(b)"),
    ("after ignores uptime", "and a[\"uptime_ms\"] > b[\"uptime_ms\"]", "and True"),
    ("after ignores the delay", "              and after[\"delay\"] <= answer_s)", "              )"),
]


def mutants():
    with open(TARGET, encoding="utf-8") as f:
        text = f.read()
    work = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "scratch", "failpaths-test",
                        "m%d" % os.getpid())
    os.makedirs(work, exist_ok=True)
    killed = 0
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        ctl = subprocess.run([sys.executable, "-B", __file__], capture_output=True, text=True, env=env, timeout=600)
        check("control: the unmutated probe passes", ctl.returncode == 0, ctl.stdout[-200:])
        for i, (name, old, new) in enumerate(MUTANTS):
            if text.count(old) != 1:
                check("mutant %s: text found once" % name, False, text.count(old))
                continue
            p = os.path.join(work, "fp_%d.py" % i)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text.replace(old, new))
            r = subprocess.run([sys.executable, "-B", __file__], capture_output=True, text=True,
                               env=dict(env, FP_TARGET=p), timeout=600)
            dead = r.returncode == 1 and "\nFAIL  " in "\n" + r.stdout and "Traceback" not in r.stderr
            killed += dead
            check("mutant %s: killed by a check" % name, dead, (r.stdout[-150:], r.stderr[-200:]))
    finally:
        shutil.rmtree(work)
    print("%d of %d mutants killed" % (killed, len(MUTANTS)))


if __name__ == "__main__":
    if "--mutants" in sys.argv[1:]:
        mutants()
    else:
        cases()
    print("%s: %d failed" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    sys.exit(1 if FAILS else 0)
