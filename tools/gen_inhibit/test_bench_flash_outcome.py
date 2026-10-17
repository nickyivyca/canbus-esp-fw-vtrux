#!/usr/bin/env python3
"""Tests bench_flash.py's OTA verdict: classify(), boots(), watch(),
parse_curl() and image_elf_sha256(), with a fake device on a fake clock. No
network, no device.

Gen-inhibit tester, 2026-10-16 (reviewer, from the user): every OTA gets one of
a1 / a2 / b / c / d / d' / e, judged by elf_sha256 and by each boot's instant
against the upload's end. Each is faked here, along with what must NOT get one
of them: an unexpected status, another image, a rollback that fits none of
d/d'/e, and a window that closed before the 9.1 gate decided.

    py -3.14 test_bench_flash_outcome.py     # exit 0 only if every check passes
Its mutants: test_bench_flash_outcome_mutants.py.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench_flash as B      # noqa: E402

FAILS = []
OLD, NEW, ODD = "aa" * 32, "bb" * 32, "cc" * 32
T_END = 1000.0               # the upload's end on the fake clock


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


class Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Device:
    """boots: [(t_s after the upload's end, elf_sha256, reset_reason)]; the
    first is the boot running before the upload. A boot answers from 12 s of its
    own uptime; each read takes 50 ms."""
    def __init__(self, clock, boots):
        self.clock, self.boots = clock, [(T_END + t, s, r) for t, s, r in boots]

    def read(self):
        self.clock.sleep(0.05)
        t = self.clock.now()
        cur = [b for b in self.boots if b[0] <= t][-1]
        up = t - cur[0]
        if up < 12.0:
            return None
        return {"elf_sha256": cur[1], "uptime_ms": int(up * 1000), "reset_reason": cur[2],
                "mode": 0, "git_version": "c65c45e"}


def before_of(dev, clock):
    t_req = clock.now()
    r = dev.read()
    r.update(t_req=t_req, t_resp=clock.now())
    return r


def run(code, boots, image_sha):
    clock = Clock(T_END - 20.0)
    dev = Device(clock, boots)
    before = before_of(dev, clock)
    clock.t = T_END
    rd = B.watch(dev.read, code, T_END, before, image_sha, clock.now, clock.sleep)
    v, d = B.classify(code, T_END, before, rd, image_sha)
    return v, d, rd, before


def read_until(boots, code, image_sha, until_s):
    """Readings every 2 s from the upload's end to until_s, with no early stop."""
    clock = Clock(T_END - 20.0)
    dev = Device(clock, boots)
    before = before_of(dev, clock)
    clock.t = T_END
    rd = []
    while clock.now() - T_END < until_s:
        t_req = clock.now()
        r = dev.read()
        rec = dict(r) if r else {"answered": False}
        rec.update(t_req=t_req, t_resp=clock.now())
        rd.append(rec)
        clock.sleep(2.0)
    return B.classify(code, T_END, before, rd, image_sha)


class FakeCapture:
    """A capture that logs its calls into the run's event list. kind: 'ok', 'no-start'
    (refuses to start), 'raise-start', or 'raise-later' (stop and summary raise)."""
    SUMMARY = {"stream": 3, "from_syn": True, "segment": 1440, "device_acks": 3, "upload_end_s": 16.49,
               "rst_from": [], "device_data_s": None, "highest_ack": 20391, "bytes_acked": 20390,
               "last_ack_s": 5.94, "last_window": 90, "window_closed": True, "longest_gap_s": 3.54,
               "gap_after_ack": 17511, "gap_at_s": 1.48}

    def __init__(self, kind, events):
        self.kind, self.events, self.path = kind, events, "x.pcapng"

    def start(self):
        self.events.append("start")
        if self.kind == "raise-start":
            raise RuntimeError("dumpcap vanished")
        return (False, "no dumpcap") if self.kind == "no-start" else (True, "capturing")

    def stop(self):
        self.events.append("stop")
        if self.kind == "raise-later":
            raise RuntimeError("stop failed")
        return True, "stopped"

    def summary(self, t_start, t_end):
        self.events.append("summary")
        if self.kind == "raise-later":
            raise RuntimeError("tshark failed")
        return dict(self.SUMMARY), None


def judged(code, boots, image_sha, kind):
    """upload_and_judge on the fake device and clock, with the capture `kind`
    (None: off). Returns (result, stdout, events); a crash is returned, not raised."""
    import contextlib
    import io
    clock = Clock(T_END - 20.0)
    dev = Device(clock, boots)
    before = before_of(dev, clock)
    clock.t = T_END - 15.0
    events = []

    def post():
        events.append("post")
        clock.t = T_END
        return {"http_code": code, "size_upload": 131072 if code is None else 1113456,
                "time_total": 15.0, "body": "", "curl_error": "", "curl_rc": 0}

    def read():
        events.append("read")
        return dev.read()
    cap = FakeCapture(kind, events) if kind else None
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            res = B.upload_and_judge(post, read, before, image_sha, 1113456, clock.now, clock.sleep, cap)
    except Exception as e:      # noqa: BLE001 -- a crash is a failed check, not a kill
        return {"crash": "%s: %s" % (type(e).__name__, e)}, out.getvalue(), events
    return res, out.getvalue(), events


def judged_key(res):
    if "crash" in res:
        return res
    return (res["verdict"], res["detail"], [(r.get("t_req"), r.get("t_resp")) for r in res["readings"]])


def wire_checks():
    """The capture's line is message only (reviewer, 2026-10-17): the verdict, its
    detail and the readings are the same with the capture off, on, refusing to
    start, or raising."""
    up = [(-1000.0, OLD, "SW")]
    for code, boots_, want in ((None, up, "a1"), (303, up + [(2.5, NEW, "SW")], "c")):
        off = judged(code, boots_, NEW, None)
        check("wire: with the capture off the verdict is (%s)" % want, off[0].get("verdict") == want, judged_key(off[0]))
        for kind in ("ok", "no-start", "raise-start", "raise-later"):
            on = judged(code, boots_, NEW, kind)
            check("wire: (%s) the same verdict, detail and readings with the capture %s" % (want, kind),
                  judged_key(on[0]) == judged_key(off[0]), judged_key(on[0]))
        check("wire: (%s) off, no wire line and no skip note" % want, "wire" not in off[1].lower(), off[1][-150:])

    res, out, ev = judged(None, up, NEW, "ok")
    check("wire: a failed upload prints the capture's line, after the verdict",
          "VERDICT:" in out and out.index("VERDICT:") < out.find("wire: the device ACKed 20390 bytes")
          and "(window closed)" in out, out[-200:])
    check("wire: the line is in the result for the record", res.get("wire", {}).get("line", "").startswith("wire: the device"),
          res.get("wire"))
    check("wire: the capture starts before the post and stops after the last read",
          ev[:2] == ["start", "post"] and "stop" in ev and ev.index("stop") > max(i for i, e in enumerate(ev) if e == "read"),
          ev)
    res, out, ev = judged(303, up + [(2.5, NEW, "SW")], NEW, "ok")
    check("wire: an upload that got its 303 prints no line, and the capture is still stopped",
          "wire: the device" not in out and "summary" not in ev and "stop" in ev, ev)
    res, out, ev = judged(None, up, NEW, "no-start")
    check("wire: a capture that will not start is said to be skipped, and not used again",
          "skipped" in out and "no dumpcap" in out and ev == ["start", "post"] + ["read"] * ev.count("read"), ev)
    res, out, ev = judged(None, up, NEW, "raise-start")
    check("wire: a capture that raises at start is skipped too", "skipped" in out and "RuntimeError" in out, out[-200:])
    res, out, ev = judged(None, up, NEW, "raise-later")
    check("wire: a summary that raises says the diagnostic was skipped",
          "wire: diagnostic skipped" in out and "tshark failed" in out, out[-200:])


def main():
    wire_checks()
    up = [(-1000.0, OLD, "SW")]

    # -- no status ------------------------------------------------------------
    v, d, rd, _ = run(None, up, NEW)
    check("(a1) no status and the boot unchanged", v == "a1", d)
    check("(a1) decided once a read is 30 s past the end, not before",
          B.A1_CONFIRM_S <= rd[-1]["t_resp"] - T_END < B.A1_CONFIRM_S + 3, rd[-1]["t_resp"] - T_END)
    v, d = read_until(up, None, NEW, 20)
    check("(a1) not decided from reads that end 20 s after the upload", v == "undecided", d)
    v, d, rd, _ = run(None, up + [(2.5, NEW, "SW")], NEW)
    check("(a2+c) no status, a restart 2.5 s after the end, the new image past the gate",
          v == "a2+c", d)
    v, d, rd, _ = run(None, up + [(2.5, NEW, "SW"), (64.0, OLD, "SW")], NEW)
    check("(a2+d) no status, then the gate's rollback", v == "a2+d", d)

    # -- a status --------------------------------------------------------------
    for code in (403, 408, 500):
        v, d, rd, _ = run(code, up, NEW)
        check("(b) HTTP %d is refused" % code, v == "b", d)
    v, d, rd, _ = run(200, up, NEW)
    check("a 200 is not the handler's 303", v == "other", d)

    # -- after a 303 -------------------------------------------------------------
    v, d, rd, _ = run(303, up + [(2.5, NEW, "SW")], NEW)
    check("(c) the new image, one restart, past the gate", v == "c", d)
    check("(c) the detail carries the boot arithmetic", "end+2.4" in d or "end+2.5" in d, d)
    check("(c) not called before 90 s of the new image's uptime",
          rd[-1]["uptime_ms"] >= B.SETTLE_UPTIME_MS, rd[-1].get("uptime_ms"))
    check("(c) the watch stops once decided", rd[-1]["t_resp"] - T_END < 110,
          rd[-1]["t_resp"] - T_END)
    v, d = read_until(up + [(2.5, NEW, "SW")], 303, NEW, 60)
    check("not (c) before the gate has had its 60 s", v == "undecided", d)

    v, d, rd, _ = run(303, up + [(2.5, NEW, "SW"), (64.0, OLD, "SW")], NEW)
    check("(d) the new image, then the old one booted 64 s after the end, SW", v == "d", d)
    v, d, rd, _ = run(303, up + [(2.5, NEW, "SW"), (20.0, OLD, "PANIC")], NEW)
    check("(d') the new image, then the old one after a PANIC", v == "d'", d)
    v, d, rd, _ = run(303, up + [(2.5, NEW, "SW"), (20.0, OLD, "TASK_WDT")], NEW)
    check("(d') ... or a TASK_WDT", v == "d'", d)
    v, d, rd, _ = run(303, up + [(2.5, OLD, "SW")], NEW)
    check("(e) the old image, its one restart 2.5 s after the end", v == "e", d)
    check("(e) decided at the first read of that boot", rd[-1]["t_resp"] - T_END < 20,
          rd[-1]["t_resp"] - T_END)
    v, d, rd, _ = run(303, up + [(2.5, NEW, "SW"), (64.0, OLD, "POWERON")], NEW)
    check("the old image after a power cut 64 s on is not the gate's rollback",
          v == "undecided", d)
    v, d, rd, _ = run(303, up + [(30.0, OLD, "SW")], NEW)
    check("the old image booted 30 s after the end, SW, fits none of d/d'/e",
          v == "undecided", d)

    # Both slots holding one build (STEP 2): the sha cannot show a rollback.
    same = [(-1000.0, NEW, "SW")]
    v, d, rd, _ = run(303, same + [(2.5, NEW, "SW")], NEW)
    check("(c) same build in both slots, and the verdict says (e) cannot be told",
          v == "c" and "cannot be told" in d, d)
    v, d, rd, _ = run(303, same + [(2.5, NEW, "SW"), (64.0, NEW, "SW")], NEW)
    check("(d) same build in both slots, told by the second restart", v == "d", d)
    v, d = read_until(same + [(2.5, NEW, "SW"), (64.0, NEW, "SW")], 303, NEW, 400)
    check("(d) same build, rolled back, read long after: still not (c)", v == "d", d)
    v, d, rd, _ = run(303, same + [(2.5, NEW, "SW"), (20.0, NEW, "PANIC")], NEW)
    check("(d') same build in both slots, a crash", v == "d'", d)

    v, d, rd, _ = run(303, up + [(2.5, ODD, "SW")], NEW)
    check("another image after a 303 is reported as such", v == "other", d)
    v, d, rd, _ = run(303, up, NEW)
    check("a 303 with no restart in the window is undecided", v == "undecided", d)
    check("... after the whole window", rd[-1]["t_resp"] - T_END >= B.WATCH_S - 3,
          rd[-1]["t_resp"] - T_END)

    # -- boots() ---------------------------------------------------------------
    before = {"elf_sha256": OLD, "uptime_ms": 500000, "reset_reason": "SW",
              "t_req": 100.0, "t_resp": 100.3}
    rd = [{"elf_sha256": OLD, "uptime_ms": 510000, "reset_reason": "SW", "t_req": 110.0, "t_resp": 110.2},
          {"answered": False, "t_req": 112.0, "t_resp": 113.0},
          {"elf_sha256": NEW, "uptime_ms": 13000, "reset_reason": "SW", "t_req": 125.0, "t_resp": 125.1},
          {"elf_sha256": NEW, "uptime_ms": 15050, "reset_reason": "SW", "t_req": 127.0, "t_resp": 127.2},
          {"elf_sha256": OLD, "uptime_ms": 13000, "reset_reason": "SW", "t_req": 190.0, "t_resp": 190.1}]
    bs = B.boots(before, rd)
    check("boots: three boots, across an unanswered read",
          [(b["elf_sha256"][:2], b["reads"]) for b in bs] == [("aa", 2), ("bb", 2), ("aa", 1)],
          [(b["elf_sha256"][:2], b["reads"]) for b in bs])
    check("boots: a boot's instant is the read's time minus its uptime",
          abs(bs[1]["lo"] - 112.0) < 0.06 and abs(bs[1]["hi"] - 112.1) < 0.06, bs[1])
    rd2 = [{"elf_sha256": NEW, "uptime_ms": 13000, "reset_reason": "SW", "t_req": 125.0, "t_resp": 125.1},
           {"elf_sha256": NEW, "uptime_ms": 13000, "reset_reason": "SW", "t_req": 128.0, "t_resp": 128.1}]
    check("boots: two boots 3 s apart are two", len(B.boots(None, rd2)) == 2, B.boots(None, rd2))

    # -- parse_curl --------------------------------------------------------------
    p = B.parse_curl("\n000 184320 4.812", "curl: (56) Recv failure: Connection was reset", 56)
    check("parse_curl: 000 is no status, with what was sent and how long",
          p["http_code"] is None and p["size_upload"] == 184320 and p["time_total"] == 4.812
          and "(56)" in p["curl_error"], p)
    p = B.parse_curl("<html>redirect</html>\n303 1278064 15.204", "", 0)
    check("parse_curl: a 303 with its body", p["http_code"] == 303 and p["body"] == "<html>redirect</html>"
          and p["size_upload"] == 1278064, p)
    p = B.parse_curl("Upload stalled\n408 65536 30.1", "", 0)
    check("parse_curl: an error exit's body is kept", p["http_code"] == 408
          and p["body"] == "Upload stalled", p)
    p = B.parse_curl("", "curl: (7) Failed to connect", 7)
    check("parse_curl: nothing on stdout is no status", p["http_code"] is None, p)

    # -- image_elf_sha256 ----------------------------------------------------------
    blob = bytearray(0x200)
    blob[0x20:0x24] = (0xABCD5432).to_bytes(4, "little")
    blob[0x20 + 0x70:0x20 + 0x90] = b"\x11" * 32
    blob[0x20 + 0x90:0x20 + 0xB0] = bytes(range(32))
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "img.bin")
        open(path, "wb").write(bytes(blob))
        got = B.image_elf_sha256(path)
        check("image_elf_sha256 reads the descriptor's app_elf_sha256",
              got == bytes(range(32)).hex(), got)
        blob[0x20] = 0
        open(path, "wb").write(bytes(blob))
        check("image_elf_sha256: no descriptor magic, no sha", B.image_elf_sha256(path) is None)

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
