"""C12 (and the rest of the MCP2515 conformance sequence) on silicon: host side.

The board runs the c12_bench image (charge-interposer/firmware/test/
c12_bench, a standalone ESP-IDF image implementing mcpconf::Target over SPI;
reviewer, 2026-10-13). The sequence is the host suite's conformance.cpp,
unchanged, so its transcript diffs line for line against the fake's
(mcp2515_conformance_fake.txt). What the board cannot do itself it asks for
over its USB console, and this script answers through the Innomaker, the
charger segment's only adapter:

  @REQ CANINJECT / INJECT id ext tag dlc   send a frame; answer once its
                                           ECHO is back (it is on the wire,
                                           so the chip has it)
  @REQ CLEAR / TAGS                        data[0] of every frame RECEIVED
                                           (not our echoes) since CLEAR
  @REQ CANWITHHOLD / ACK 0|1               the Innomaker to listen-only
                                           (no ACK) and back

SINGLE-ADAPTER, NOT CLEAN (user via the reviewer, 2026-10-13). The verdict
is the chip's own registers read over SPI, so no adapter clock is in it. But
the Innomaker is both the sender and the only witness: it cannot show that
each injected frame reached the wire exactly once and in order, a retry or
duplicate it hides behind its echo, or the true on-wire spacing. The
two-adapter C12 stays open. MCP2515 path: YES (it is the chip).

C9 (no ACK) needs NOTHING ELSE on the charger segment; the operator says so
with --charger-segment-alone, and the Innomaker must support listen-only.
Without both, C9 records that it did not run.

Every reply to the board is one line of at most REPLY_MAX_CHARS. A TAGS
reply over that is cut to the first tags that fit (whole tags); the full
host count is in this script's log and, as a "# host count" line beside
wire.delivered, in the transcript (reviewer, 2026-10-22). The board reads
256-byte lines into a 256-byte USB RX ring buffer, and C9's listen-only
witness sees every unacknowledged retry: uncapped, its reply was ~8.2 KB,
and C9 run after c12-all hung after its last request (2 of 2). The overrun
as the cause is a HYPOTHESIS; nothing else C9 measures changes.

    py -3.14 bench_c12.py --self-test                      # no hardware
    py -3.14 bench_c12.py --can-fail                       # self-test mutants
    py -3.14 bench_c12.py --port COM7 --step C12 --elf <c12_bench.elf>

The run writes its record verbatim to --out (default beside this script,
time-stamped): image identity (ELF sha256 off the board, checked against
--elf), adapter, mode flags, every request, the transcript, and the diff
against the fake. Tester, 2026-10-13.
"""

import argparse
import datetime
import hashlib
import os
import sys
import threading
import time

# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never from
# the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))


def fake_transcript():
    """The host fake's stored transcript, a fixture that stays in
    SeaDrive (paths.fixtures(); beside this script before the move)."""
    import paths
    return os.path.join(paths.fixtures(), "mcp2515_conformance_fake.txt")


ECHO_WAIT_S = 1.0
DRAIN_S = 0.05
REPLY_MAX_CHARS = 200     # the board's line_buf and RX ring are 256 bytes


def capped_tags(tags, limit=REPLY_MAX_CHARS):
    """-> (reply, n_sent): "@TAGS a,b,..." with as many whole tags, oldest
    first, as keep the line within `limit` characters ("@TAGS -" if none)."""
    r, n = "@TAGS ", 0
    for x in tags:
        piece = ("," if n else "") + str(x)
        if len(r) + len(piece) > limit:
            break
        r += piece
        n += 1
    return (r if n else "@TAGS -"), n


# --- the transcript --------------------------------------------------------

def parse_transcript(lines):
    """-> {(step, key): (value, status)} from Recorder::print() lines."""
    out = {}
    for ln in lines:
        if not ln.startswith("  ") or ln.startswith("  #"):
            continue
        f = ln.split()
        if len(f) >= 4:
            out[(f[0], f[1])] = (f[2], f[3])
    return out


def diff_transcripts(fake, silicon, steps=None):
    """-> list of (step, key, fake_value, silicon_value, status) that
    differ, over the steps the silicon run covered (or `steps`). A key
    present on one side only is a difference too."""
    covered = steps or sorted({s for s, _k in silicon})
    out = []
    for (s, k), (v, st) in sorted(fake.items()):
        if s not in covered:
            continue
        sv = silicon.get((s, k))
        if sv is None or sv[0] != v:
            out.append((s, k, v, sv[0] if sv else "(absent)", st))
    for (s, k), (v, st) in sorted(silicon.items()):
        if (s, k) not in fake and s in covered:
            out.append((s, k, "(absent)", v, st))
    return out


# --- the request handler ---------------------------------------------------

class Adapter(object):
    """The Innomaker as this script needs it. `bus` is a python-can bus
    whose recv() returns echoes as is_rx False (gs_usb does); `gs` is the
    gs_usb device under it, for the listen-only switch (None in tests)."""

    def __init__(self, bus, gs=None, listen_only_ok=False):
        self.bus, self.gs = bus, gs
        self.listen_only_ok = listen_only_ok
        self.lock = threading.Lock()
        self.capturing = False
        self.tags = []
        self.echoes = []
        self.rx_total = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=2.0)

    def _run(self):
        while not self._stop.is_set():
            with self.lock:
                try:
                    m = self.bus.recv(timeout=0.01)
                except Exception:                          # noqa: BLE001
                    m = None
            if m is None or getattr(m, "is_error_frame", False):
                time.sleep(0.001)
                continue
            if m.is_rx:
                self.rx_total += 1
                if self.capturing and len(m.data):
                    self.tags.append(m.data[0])
            else:
                self.echoes.append((m.arbitration_id, bytes(m.data)))

    def send(self, msg):
        self.bus.send(msg, timeout=0.5)

    def set_ack(self, on):
        if self.gs is None:
            return
        from gs_usb.constants import (GS_CAN_MODE_HW_TIMESTAMP,
                                      GS_CAN_MODE_LISTEN_ONLY,
                                      GS_CAN_MODE_NORMAL)
        with self.lock:
            self.gs.stop()
            self.gs.start(GS_CAN_MODE_NORMAL | GS_CAN_MODE_HW_TIMESTAMP if on
                          else GS_CAN_MODE_LISTEN_ONLY |
                          GS_CAN_MODE_HW_TIMESTAMP)


class Handler(object):
    """Answers one @REQ line; keeps a log of every request and its
    answer, and the problems that make the run invalid."""

    def __init__(self, adapter, segment_alone, log):
        self.a, self.alone, self.log = adapter, segment_alone, log
        self.problems = []
        self.n_inject = 0
        self.replies = []
        self.tag_counts = []      # (host count, tags sent) per TAGS reply

    def answer(self, req):
        f = req.split()
        cmd = f[0] if f else ""
        if cmd == "CANINJECT":
            r = "@YES"
        elif cmd == "INJECT" and len(f) == 5:
            r = self._inject(int(f[1], 16), f[2] == "1", int(f[3], 16),
                             int(f[4]))
        elif cmd == "CLEAR":
            time.sleep(DRAIN_S)
            self.a.tags = []
            self.a.capturing = True
            r = "@OK"
        elif cmd == "TAGS":
            time.sleep(DRAIN_S)
            t = list(self.a.tags)
            r, sent = capped_tags(t)
            self.tag_counts.append((len(t), sent))
            if sent < len(t):
                self.log("  req %-34s -> %s (host count %d, capped to %d)"
                         % (req, r, len(t), sent))
                self.replies.append(r)
                return r
        elif cmd == "CANWITHHOLD":
            r = "@YES" if (self.alone and self.a.listen_only_ok) else "@NO"
        elif cmd == "ACK" and len(f) == 2:
            self.a.set_ack(f[1] == "1")
            r = "@OK"
        else:
            self.problems.append("unknown request %r" % req)
            r = "@ERR"
        if cmd == "TAGS":
            self.log("  req %-34s -> %s (host count %d)"
                     % (req, r, self.tag_counts[-1][0]))
        else:
            self.log("  req %-34s -> %s" % (req, r))
        self.replies.append(r)
        return r

    def _inject(self, ident, ext, tag, dlc):
        import can
        # conformance.h payloadByte(): byte 0 the tag, byte i tag + 0x1D * i
        # (2026-10-15, C13 reads held frames back whole and compares them)
        data = bytearray([(tag + 0x1D * i) & 0xFF if i else tag
                          for i in range(max(dlc, 1))])
        before = len(self.a.echoes)
        self.a.send(can.Message(arbitration_id=ident, is_extended_id=ext,
                                data=data))
        self.n_inject += 1
        end = time.monotonic() + ECHO_WAIT_S
        while time.monotonic() < end:
            if any(e == (ident, bytes(data))
                   for e in self.a.echoes[before:]):
                return "@OK"
            time.sleep(0.001)
        self.problems.append("INJECT %X tag %02X: no echo within %.1f s -- "
                             "the frame may never have reached the wire"
                             % (ident, tag, ECHO_WAIT_S))
        return "@OK"


def converse(ser_lines, write, handler, step, log, timeout_s=120.0):
    """Drive one RUN over a line source / sink. `ser_lines()` returns the
    next line from the board (or None on timeout), `write(s)` sends a
    line. -> (hello, transcript_lines, end_status)."""
    write("HELLO?")
    hello = None
    end = time.monotonic() + 10.0
    while hello is None and time.monotonic() < end:
        ln = ser_lines()
        if ln and ln.startswith("@HELLO"):
            hello = ln
    if hello is None:
        return None, [], "no @HELLO from the board"
    log("board: %s" % hello)
    write("RUN %s" % step)
    body, inside, status = [], False, None
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        ln = ser_lines()
        if ln is None:
            continue
        if ln.startswith("@REQ "):
            write(handler.answer(ln[5:]))
        elif ln.startswith("@BEGIN"):
            inside = True
        elif ln.startswith("@END"):
            status = ln
            break
        elif inside:
            body.append(ln)
        else:
            log("board (outside the run): %s" % ln)
    return hello, body, status or "no @END within %.0f s" % timeout_s


def hello_sha(hello):
    for f in (hello or "").split():
        if f.startswith("elf_sha256="):
            return f.split("=", 1)[1]
    return None


# --- hardware -------------------------------------------------------------

def open_innomaker():
    import bench_charger_witness as bcw
    from gs_usb.constants import GS_CAN_MODE_LISTEN_ONLY
    bus = bcw.open_gs_usb()
    gs = bus.gs_usb
    lo = bool(gs.device_capability.feature & GS_CAN_MODE_LISTEN_ONLY)
    return bus, gs, lo


def run_bench(args):
    import serial
    print("run at %s" % datetime.datetime.now().isoformat())
    print("SINGLE-ADAPTER, NOT CLEAN: the Innomaker sends and is the only "
          "witness; the verdict is the chip's SPI registers. MCP2515 path: "
          "YES.")
    elf_sha = None
    if args.elf:
        with open(args.elf, "rb") as f:
            elf_sha = hashlib.sha256(f.read()).hexdigest()
        print("--elf %s sha256 %s" % (args.elf, elf_sha))
    bus, gs, lo = open_innomaker()
    print("Innomaker: index 0, listen-only %s; --charger-segment-alone %s"
          % ("supported" if lo else "NOT supported",
             args.charger_segment_alone))
    a = Adapter(bus, gs, lo).start()
    h = Handler(a, args.charger_segment_alone, print)
    ser = serial.Serial(args.port, 115200, timeout=0.2)
    try:
        def lines():
            b = ser.readline()
            return b.decode("ascii", "replace").rstrip("\r\n") if b else None

        def write(s):
            ser.write((s + "\n").encode("ascii"))
            ser.flush()
        hello, body, status = converse(lines, write, h, args.step, print,
                                       args.timeout)
    finally:
        ser.close()
        a.stop()
        try:
            a.set_ack(True)
        except Exception:                                  # noqa: BLE001
            pass
        bus.shutdown()
    return report(hello, body, status, h, elf_sha, args.step)


def host_count_line(tag_counts):
    """The transcript line beside wire.delivered: what the host received,
    not what the board was sent. A "  #" line, so the diff skips it."""
    if not tag_counts:
        return "  # host count: no TAGS reply in this run"
    return "  # host count (bench_c12, not the board): %s" % "; ".join(
        "TAGS reply %d: %d frame(s) received since CLEAR, %s" % (
            i + 1, n, "all sent" if s == n else
            "the board was sent the first %d (reply cap %d chars)"
            % (s, REPLY_MAX_CHARS))
        for i, (n, s) in enumerate(tag_counts))


def report(hello, body, status, h, elf_sha, step):
    rc = 0
    sha = hello_sha(hello)
    print("image: %s" % (hello or "(none)"))
    if elf_sha and sha != elf_sha:
        print("REFUSED: the board's ELF sha256 %s is not --elf's %s"
              % (sha, elf_sha))
        rc = 1
    print("end: %s; %d frame(s) injected; %d request problem(s)"
          % (status, h.n_inject, len(h.problems)))
    for p in h.problems:
        print("  PROBLEM: %s" % p)
    if h.problems or not (status or "").endswith(" ok"):
        rc = 1
    print("\n--- transcript (silicon) ---")
    for ln in body:
        print(ln)
        f = ln.split()
        if ln.startswith("  ") and len(f) >= 2 and f[1] == "wire.delivered":
            print(host_count_line(h.tag_counts))
    fake = parse_transcript(open(fake_transcript()).read().splitlines())
    sil = parse_transcript(body)
    d = diff_transcripts(fake, sil)
    print("\n--- against the fake (%s): %d line(s) differ ---"
          % (os.path.basename(fake_transcript()), len(d)))
    for s, k, fv, sv, st in d:
        print("  %-24s %-24s fake %-10s silicon %-10s %s%s"
              % (s, k, fv, sv, st, "  <-- a DATASHEET line" if
                 st == "DATASHEET" else ""))
    return rc


# --- self-test: no hardware ----------------------------------------------

def self_test():
    import can
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    class FakeBus(object):
        """Echoes every send (is_rx False) unless `drop`; queues rx frames
        handed to it with rx()."""

        def __init__(self):
            self.q, self.sent, self.drop = [], [], False

        def send(self, m, timeout=None):
            self.sent.append(m)
            if not self.drop:
                e = can.Message(arbitration_id=m.arbitration_id,
                                is_extended_id=m.is_extended_id,
                                data=m.data, is_rx=False)
                self.q.append(e)

        def rx(self, tag):
            self.q.append(can.Message(arbitration_id=0x240, data=[tag, 0],
                                      is_rx=True))

        def recv(self, timeout=None):
            if self.q:
                return self.q.pop(0)
            time.sleep(timeout or 0)
            return None

    print("handler")
    fb = FakeBus()
    a = Adapter(fb, None, listen_only_ok=True).start()
    log = []
    h = Handler(a, True, log.append)
    check(h.answer("INJECT 18FF60E5 1 A0 8") == "@OK" and not h.problems
          and fb.sent[-1].arbitration_id == 0x18FF60E5
          and fb.sent[-1].is_extended_id and fb.sent[-1].data[0] == 0xA0
          and len(fb.sent[-1].data) == 8,
          "INJECT sends the frame and answers once its echo is back")
    fb.drop = True
    h.answer("INJECT 18FF60E5 1 B0 8")
    check(any("no echo" in p for p in h.problems),
          "an INJECT whose echo never comes is a run problem")
    fb.drop = False
    h.problems = []
    h.answer("CLEAR")
    fb.rx(1)
    fb.rx(0)
    h.answer("INJECT 100 0 07 8")
    fb.rx(2)
    check(h.answer("TAGS") == "@TAGS 1,0,2",
          "TAGS lists received frames only, oldest first, not our echoes")
    h.answer("CLEAR")
    check(h.answer("TAGS") == "@TAGS -", "TAGS after CLEAR is empty")
    a.tags = [0] * 4100 + [255] * 10        # C9's ~8.2 KB of retries
    r = h.answer("TAGS")
    check(len(r) <= REPLY_MAX_CHARS and r.startswith("@TAGS 0,0,")
          and not r.endswith(","),
          "a long TAGS reply is cut to whole tags within %d chars (%d)"
          % (REPLY_MAX_CHARS, len(r)))
    check(h.tag_counts[-1] == (4110, r.count(",") + 1)
          and "host count 4110" in log[-1],
          "...and the uncapped host count is recorded -> %s, %s"
          % (h.tag_counts[-1], log[-1][-40:]))
    a.tags = [255] * 60
    r = h.answer("TAGS")
    check(r.count(",") + 1 == 48 and len(r) == 6 + 48 * 4 - 1,
          "three-digit tags: as many whole tags as fit (%d chars)" % len(r))
    a.tags = [2, 1, 0]
    check(h.answer("TAGS") == "@TAGS 2,1,0" and h.tag_counts[-1] == (3, 3),
          "a short TAGS reply is sent whole")
    check(h.answer("CANWITHHOLD") == "@YES",
          "CANWITHHOLD: yes with the segment confirmed alone + listen-only")
    check(Handler(a, False, log.append).answer("CANWITHHOLD") == "@NO",
          "...no without --charger-segment-alone")
    check(Handler(Adapter(fb, None, False), True,
                  log.append).answer("CANWITHHOLD") == "@NO",
          "...no when the adapter has no listen-only mode")
    h.answer("FROB")
    check(h.problems and "unknown request" in h.problems[-1],
          "an unknown request is a run problem")
    check(max(len(x) for x in h.replies) <= REPLY_MAX_CHARS,
          "no reply to the board is over %d chars (longest %d)"
          % (REPLY_MAX_CHARS, max(len(x) for x in h.replies)))
    a.stop()

    print("converse")
    script = ["@HELLO c12_bench version=abc elf_sha256=%s idf=v5.4.1"
              % ("ab" * 32), "@REQ CANINJECT", "@BEGIN C12",
              "# MCP2515 conformance transcript",
              "  C12-rx-rollover  AB.rx-flags  0x03  DATASHEET  p23",
              "  C12-rx-rollover  AB.RXB0D0    0xA0  DATASHEET  p23",
              "@END C12 ok"]
    sent = []
    it = iter(script)
    fb2 = FakeBus()
    a2 = Adapter(fb2, None).start()
    h2 = Handler(a2, False, lambda s: None)
    hello, body, status = converse(lambda: next(it, None), sent.append, h2,
                                   "C12", lambda s: None, timeout_s=2.0)
    a2.stop()
    check(sent[:2] == ["HELLO?", "RUN C12"] and sent[2] == "@YES",
          "HELLO?, RUN C12, then the answer to the board's request -> %s"
          % sent)
    check(status == "@END C12 ok" and len(body) == 3,
          "the transcript between @BEGIN and @END is kept")
    check(hello_sha(hello) == "ab" * 32, "the ELF sha256 is read off @HELLO")

    print("diff against the fake")
    fake = {("C12-rx-rollover", "AB.rx-flags"): ("0x03", "DATASHEET"),
            ("C12-rx-rollover", "AB.RXB0D0"): ("0xA0", "DATASHEET"),
            ("C1-reset-defaults", "CANSTAT.mode"): ("0x80", "DATASHEET")}
    sil = parse_transcript(body)
    check(diff_transcripts(fake, sil) == [],
          "a matching C12 run differs on nothing (C1 is not in this run)")
    sil[("C12-rx-rollover", "AB.RXB0D0")] = ("0xB0", "DATASHEET")
    d = diff_transcripts(fake, sil)
    check(len(d) == 1 and d[0][3] == "0xB0",
          "a changed DATASHEET line is reported -> %s" % d)
    del sil[("C12-rx-rollover", "AB.rx-flags")]
    check(any(x[3] == "(absent)" for x in diff_transcripts(fake, sil)),
          "a line missing from the silicon run is reported")
    real = parse_transcript(open(fake_transcript()).read().splitlines())
    check(len(real) >= 50 and ("C12-rx-rollover", "noBUKT.RXB0D0") in real,
          "the stored fake transcript parses (%d lines)" % len(real))

    print("report refuses an image that is not --elf")
    import io
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        rc = report(hello, body, status, h2, "cd" * 32, "C12")
    finally:
        sys.stdout = old
    check(rc == 1 and "REFUSED" in buf.getvalue(),
          "a board ELF sha256 that is not the built one is refused")

    print("the host count beside wire.delivered")
    c9 = ["  C9-no-ack  TEC.at-2ms  128  DEFERRED  p47",
          "  C9-no-ack  wire.delivered  0,0,0  DATASHEET  nothing",
          "  C9-no-ack  next.key  1  DEFERRED  x"]
    h2.tag_counts = [(4110, 49)]
    buf = io.StringIO()
    sys.stdout = buf
    try:
        report(hello, c9, "@END C9 ok", h2, None, "C9")
    finally:
        sys.stdout = old
    out = buf.getvalue().splitlines()
    i = [n for n, ln in enumerate(out) if "wire.delivered" in ln][0]
    check(out[i + 1].startswith("  # host count") and "4110" in out[i + 1]
          and "first 49" in out[i + 1],
          "the transcript has the host count, cap noted, right after "
          "wire.delivered -> %s" % out[i + 1][:70])
    check(parse_transcript(out[i:i + 3]) == parse_transcript(c9[1:]),
          "...and it is not a transcript line (the diff skips it)")

    print("\nbench_c12 self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("TAGS reply not capped",
     "        if len(r) + len(piece) > limit:\n            break\n",
     ""),
    ("cap off by one tag",
     "        if len(r) + len(piece) > limit:",
     "        if len(r) > limit:"),
    ("host count not logged",
     "                self.log(\"  req %-34s -> %s (host count %d, capped to %d)\"\n"
     "                         % (req, r, len(t), sent))",
     "                self.log(\"  req %-34s -> %s\" % (req, r))"),
    ("tag count records what was sent",
     "            self.tag_counts.append((len(t), sent))",
     "            self.tag_counts.append((sent, sent))"),
    ("no host count in the transcript",
     "            print(host_count_line(h.tag_counts))",
     "            pass"),
    ("host count as a transcript line",
     "    return \"  # host count (bench_c12, not the board): %s\"",
     "    return \"  C9-no-ack host.count %s\""),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test against each of MUTANTS")
    ap.add_argument("--port", help="the board's USB console, e.g. COM7")
    ap.add_argument("--step", default="C12", help="C1..C12 or all")
    ap.add_argument("--elf", help="the c12_bench.elf flashed; its sha256 "
                    "must match the board's")
    ap.add_argument("--charger-segment-alone", action="store_true",
                    help="the operator confirms nothing but the X2 and the "
                    "Innomaker is on the charger segment (C9 needs it)")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if not args.port:
        ap.error("--port (or --self-test)")
    import paths      # no --out: SeaDrive, as before the move, never the tree
    out = args.out or os.path.join(paths.fixtures(), "bench_c12_%s_%s.txt" % (
        args.step, datetime.datetime.now().strftime("%Y%m%dT%H%M%S")))
    import bench_charger_witness as bcw
    tee = bcw.Tee(out)
    sys.stdout = tee
    try:
        rc = run_bench(args)
    finally:
        sys.stdout = tee.out
        tee.f.close()
        print("saved verbatim to %s" % out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
