"""Step 4 item 4: receive order on silicon, charger -> vehicle (step 4 prep).

Spec 2: "Every frame is forwarded to the other port unmodified, in order";
the only accepted reordering is vehicle-to-charger, across identifiers. So
frames the charger sends back to back must reach the vehicle segment in the
order sent. The host case (test_l2_bridge rx-rollover-order, evidence
section 2) is where the receive-rollover window was found; this offers the
same shape on the real board, as fast as the charger wire carries it, so
both MCP2515 receive buffers fill:

  - distinct: three identifiers, A B C, back to back;
  - same-id:  one identifier three times.

Each burst carries (burst number, position) in data[0..2]; the witness on
the vehicle segment judges, per burst: every position present once, in
order. Run on the board's interposer image (any image that bridges); it
touches the MCP2515 RX path, which the port rewrites.

THE ADAPTERS, and what they cannot show. The Innomaker sends (the charger
segment's only adapter) and its echoes are drained throughout (gs_usb stalls
otherwise). The vehicle witness is the Kvaser in NORMAL mode, because it
must acknowledge: with the WiCAN's driver not installed nothing else on the
vehicle segment does, and a board retrying unacknowledged frames is a
different experiment. SINGLE-ADAPTER ON THE CHARGER SIDE: that a burst's
frames were adjacent on the charger wire is shown only by the Innomaker's
own echo stamps (device clock, if the device stamps), not by an independent
witness.

The board's serial c2v counter, read before and after, cross-checks the
count forwarded against the count offered and the count witnessed.

    py -3.14 bench_rx_order.py --self-test        # no hardware
    py -3.14 bench_rx_order.py --port COM3 --bursts 2000

Tester, 2026-10-13. Not yet run on hardware.
"""

import argparse
import collections
import datetime
import os
import re
import sys
import threading
import time

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BITRATE = 500000
MAGIC = 0x5C
VARIANTS = {
    # name: (ids per position, extended)
    "distinct": ((0x101, 0x102, 0x103), False),
    "same-id": ((0x101, 0x101, 0x101), False),
    "distinct-ext": ((0x18FF5101, 0x18FF5102, 0x18FF5103), True),
    "same-id-ext": ((0x18FF5101, 0x18FF5101, 0x18FF5101), True),
}
C2V_RE = re.compile(r"c2v[=: ]+(\d+)", re.IGNORECASE)

# Spec 2.3 (2026-10-13): the vehicle port starts listen-only and puts nothing
# on the vehicle segment until an extended frame with source address 0xC0
# arrives on the charger port, and falls back 2 s after the last one. None of
# the variants' ids carries SA 0xC0, so on a 2.3 image nothing would be
# forwarded and the run would read as total loss. A keep-alive does carry it:
# an id no vtrux DBC defines (BelInverter-v2 and epri-pt-bus use 0x18FFD4C0-
# 0x18FFDBC0 from 0xC0), so the core reads nothing into it, with a payload
# decode() rejects. It goes out before each variant and between bursts.
KEEPALIVE_ID = 0x18FF5AC0
KEEPALIVE_DATA = bytes([0xA5, 0, 0, 0, 0, 0, 0, 0])
KEEPALIVE_EVERY_S = 0.5           # well inside 2.3's 2 s fall-back
KEEPALIVE_WAIT_S = 1.0            # for the first one to come out the far side


def is_keepalive(m):
    return m.is_extended_id and m.arbitration_id == KEEPALIVE_ID


def keepalive_due(now, last):
    return last is None or now - last >= KEEPALIVE_EVERY_S


def rx_keep(m, vno):
    """What the vehicle-side reader keeps: this variant's burst frames and
    the keep-alives, received (not our own echoes)."""
    if not m.is_rx:
        return False
    if is_keepalive(m):
        return True
    d = decode(m.data)
    return d is not None and d[0] == vno


def premise_verdict(ka_sent, ka_seen):
    """The keep-alives are the evidence that the vehicle port was in normal
    mode. None seen on the vehicle side: the port never left listen-only
    (or the witness is not on that segment), so the variant proves nothing
    -- VOID, not a loss. Fewer seen than sent: bridged frames were lost,
    which spec 2 forbids like any other loss -- LOST."""
    if ka_seen == 0:
        return "VOID"
    return "OK" if ka_seen == ka_sent else "LOST"


def payload(variant_no, burst, pos):
    """data[0] magic, [1] variant, [2..3] burst (big-endian), [4] position.
    5 bytes: short frames, so a burst is as tight as the wire allows."""
    return bytearray([MAGIC, variant_no, (burst >> 8) & 0xFF, burst & 0xFF,
                      pos])


def decode(data):
    """-> (variant_no, burst, pos) or None if it is not one of ours."""
    if len(data) < 5 or data[0] != MAGIC:
        return None
    return data[1], (data[2] << 8) | data[3], data[4]


def judge(arrivals, n_bursts, per_burst):
    """arrivals: [(variant_no, burst, pos)] in vehicle-side arrival order,
    for ONE variant. -> dict of counts and examples. A burst is good when
    its positions arrive exactly once each, in order 0..per_burst-1."""
    by_burst = collections.OrderedDict()
    for _v, b, p in arrivals:
        by_burst.setdefault(b, []).append(p)
    want = list(range(per_burst))
    out_of_order, missing, duplicated, examples = 0, 0, 0, []
    for b in range(n_bursts):
        got = by_burst.get(b, [])
        if got == want:
            continue
        if len(set(got)) < len(got):
            duplicated += 1
        if set(got) != set(want):
            missing += 1
        elif got != want:
            out_of_order += 1
        if len(examples) < 5:
            examples.append((b, got))
    # bursts interleaving with each other (a later burst's frame before an
    # earlier burst finished) is also an order failure across bursts
    crossed = 0
    last_b = -1
    for _v, b, _p in arrivals:
        if b < last_b:
            crossed += 1
        last_b = max(last_b, b)
    return dict(bursts=n_bursts, received=len(arrivals),
                out_of_order=out_of_order, missing=missing,
                duplicated=duplicated, crossed=crossed, examples=examples,
                ok=(out_of_order == missing == duplicated == crossed == 0))


# --- hardware ---------------------------------------------------------------

class Drain(object):
    """Reads a bus throughout; keeps (host time, msg) for frames `keep`
    accepts. Shares the sending handle on gs_usb (a second Bus on the same
    device cannot be opened)."""

    def __init__(self, bus, keep):
        self.bus, self.keep = bus, keep
        self.got = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                m = self.bus.recv(timeout=0.05)
            except Exception:                              # noqa: BLE001
                return
            if m is not None and self.keep(m):
                self.got.append((time.perf_counter(), m))

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=3.0)


def last_c2v(lines):
    for ln in reversed(lines):
        m = C2V_RE.search(ln)
        if m:
            return int(m.group(1))
    return None


def run_variant(tx, rx, vname, vno, bursts, gap_s, serial_lines):
    import can
    ids, ext = VARIANTS[vname]
    echoes = Drain(tx, lambda m: not m.is_rx and decode(m.data)
                   and decode(m.data)[0] == vno).start()
    # ONE reader per bus: two Drain threads on the same handle race for each
    # frame, and a frame the wrong one takes is dropped by its filter. (The
    # first keep-alive run had a second Drain for the keep-alives and saw
    # none of them while the board counted each; 2026-10-14.)
    rx_all = Drain(rx, lambda m: rx_keep(m, vno)).start()

    class _View(object):
        def __init__(self, want):
            self.want = want

        @property
        def got(self):
            return [x for x in list(rx_all.got) if self.want(x[1])]
    seen = _View(lambda m: not is_keepalive(m))
    ka_seen = _View(is_keepalive)
    c2v0 = last_c2v(serial_lines) if serial_lines is not None else None
    sent, errs, ka_sent = 0, 0, 0
    last_ka = None

    def keepalive():
        tx.send(can.Message(arbitration_id=KEEPALIVE_ID, is_extended_id=True,
                            data=KEEPALIVE_DATA), timeout=0.2)
        return time.perf_counter()

    try:
        last_ka = keepalive()
        ka_sent += 1
        t_end = time.perf_counter() + KEEPALIVE_WAIT_S
        while not ka_seen.got and time.perf_counter() < t_end:
            time.sleep(0.01)
        if not ka_seen.got:
            # the port did not go normal: do not offer the bursts at all
            bursts = 0
        for b in range(bursts):
            if keepalive_due(time.perf_counter(), last_ka):
                last_ka = keepalive()
                ka_sent += 1
            for p, ident in enumerate(ids):
                try:
                    tx.send(can.Message(arbitration_id=ident,
                                        is_extended_id=ext,
                                        data=payload(vno, b, p)),
                            timeout=0.2)
                    sent += 1
                except Exception:                          # noqa: BLE001
                    errs += 1
            time.sleep(gap_s)
        time.sleep(1.5)
    finally:
        echoes.stop()
        rx_all.stop()
    time.sleep(6.0 if serial_lines is not None else 0)   # a status line
    c2v1 = last_c2v(serial_lines) if serial_lines is not None else None
    arr = [decode(m.data) for _h, m in seen.got]
    j = judge(arr, bursts, len(ids))
    # how tight were the bursts on the charger wire, by the echoes' device
    # stamps (single-adapter: the sender's own view)
    stamps = collections.defaultdict(list)
    for _h, m in echoes.got:
        d = decode(m.data)
        stamps[d[1]].append(m.timestamp)
    spans = sorted((max(v) - min(v)) * 1e6 for v in stamps.values()
                   if len(v) == len(ids))
    return dict(judge=j, sent=sent, send_errors=errs, echoes=len(echoes.got),
                offered=bursts, ka_sent=ka_sent, ka_seen=len(ka_seen.got),
                premise=premise_verdict(ka_sent, len(ka_seen.got)),
                c2v=(None if c2v0 is None or c2v1 is None else c2v1 - c2v0),
                echo_span_us_p50=spans[len(spans) // 2] if spans else None,
                echo_span_us_max=spans[-1] if spans else None)


def run_bench(args):
    import can
    import bench_charger_witness as bcw
    print("run at %s; MCP2515 path: YES (charger -> vehicle receive)"
          % datetime.datetime.now().isoformat())
    tx = bcw.open_gs_usb()
    rx = can.Bus(interface="kvaser", channel=args.kvaser_channel,
                 bitrate=BITRATE)
    lines = None
    ser = None
    if args.port:
        import serial
        lines = []
        ser = serial.Serial(args.port, 115200, timeout=0.2)

        def tap():
            buf = b""
            while ser.is_open:
                try:
                    buf += ser.read(512)
                except Exception:                          # noqa: BLE001
                    return
                while b"\n" in buf:
                    ln, _, buf = buf.partition(b"\n")
                    lines.append(ln.decode("ascii", "replace").rstrip())
        threading.Thread(target=tap, daemon=True).start()
        time.sleep(6.0)
    rc = 0
    try:
        for vno, vname in enumerate(args.variants.split(",")):
            r = run_variant(tx, rx, vname, vno, args.bursts, args.gap_ms /
                            1000.0, lines)
            j = r["judge"]
            print("\n== %s: %d bursts of %d, %d sent (%d send errors), %d "
                  "echoes, %d witnessed on the vehicle side"
                  % (vname, j["bursts"], len(VARIANTS[vname][0]), r["sent"],
                     r["send_errors"], r["echoes"], j["received"]))
            print("   per burst: %d out of order, %d with a frame missing, "
                  "%d duplicated; %d frames from a later burst ahead of an "
                  "earlier one" % (j["out_of_order"], j["missing"],
                                   j["duplicated"], j["crossed"]))
            for b, got in j["examples"]:
                print("   e.g. burst %d arrived as positions %s" % (b, got))
            print("   board c2v delta: %s (offered %d)"
                  % (r["c2v"], r["sent"]))
            print("   burst span on the charger wire by the Innomaker's "
                  "echo stamps: p50 %s us, max %s us (sender's own clock)"
                  % (r["echo_span_us_p50"], r["echo_span_us_max"]))
            print("   spec 2.3 keep-alive 0x%X: %d sent, %d seen on the vehicle "
                  "side -> %s" % (KEEPALIVE_ID, r["ka_sent"], r["ka_seen"],
                                  r["premise"]))
            if r["premise"] == "VOID":
                print("   VOID: no keep-alive reached the vehicle side, so the "
                      "vehicle port never left listen-only (or the witness is "
                      "not on that segment); no bursts were offered, and this "
                      "is not a firmware result")
                rc = rc or 2
            elif r["offered"] != args.bursts or \
                    r["sent"] != j["bursts"] * len(VARIANTS[vname][0]):
                print("   SHORT OFFER: not every frame was sent -- the "
                      "result is not a firmware result")
                rc = 1
            elif not j["ok"] or r["premise"] == "LOST":
                print("   ORDER/COMPLETENESS FAILED vs spec 2%s"
                      % (" (keep-alives lost too)" if r["premise"] == "LOST"
                         else ""))
                rc = 1
            else:
                print("   every burst arrived complete and in order")
    finally:
        tx.shutdown()
        rx.shutdown()
        if ser is not None:
            ser.close()
    return rc


# --- self-test --------------------------------------------------------------

def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    check(decode(payload(2, 513, 1)) == (2, 513, 1),
          "payload round-trips (variant, burst, position)")
    check(decode(bytearray([0, 1, 2, 3, 4])) is None,
          "a frame without the magic is not ours")
    good = [(0, b, p) for b in range(4) for p in range(3)]
    check(judge(good, 4, 3)["ok"], "every burst complete and in order passes")
    swapped = list(good)
    swapped[4], swapped[5] = swapped[5], swapped[4]      # burst 1: 0,2,1
    j = judge(swapped, 4, 3)
    check(not j["ok"] and j["out_of_order"] == 1
          and j["examples"][0] == (1, [0, 2, 1]),
          "A,C,B in one burst is caught as out of order -> %s" % j["examples"])
    j = judge([x for x in good if x != (0, 2, 1)], 4, 3)
    check(not j["ok"] and j["missing"] == 1, "a missing frame is caught")
    j = judge(good + [(0, 3, 2)], 4, 3)
    check(not j["ok"] and j["duplicated"] == 1, "a duplicate is caught")
    crossed = good[:5] + [good[6]] + [good[5]] + good[7:]
    j = judge(crossed, 4, 3)
    check(not j["ok"] and j["crossed"] >= 1,
          "a later burst's frame ahead of an earlier one is caught")
    j = judge(good[:9], 4, 3)
    check(not j["ok"] and j["missing"] == 1,
          "a burst that never arrived at all counts as missing")
    check(last_c2v(["x v2c=5 c2v=10", "y c2v=17 z"]) == 17,
          "the last c2v on the board's status lines is read")
    # spec 2.3 keep-alive (2026-10-14)
    check((KEEPALIVE_ID & 0xFF) == 0xC0 and KEEPALIVE_ID > 0x7FF,
          "the keep-alive is an extended id from source address 0xC0")
    check(KEEPALIVE_ID not in range(0x18FFD4C0, 0x18FFDBC0 + 1, 0x100),
          "the keep-alive is none of the charger's DBC-defined pages")
    check(decode(KEEPALIVE_DATA) is None,
          "a keep-alive is never judged as a burst frame")
    check(KEEPALIVE_EVERY_S < 2.0,
          "keep-alives come faster than 2.3's 2 s fall-back")
    check(keepalive_due(10.0, None) and keepalive_due(10.5, 10.0)
          and not keepalive_due(10.4, 10.0),
          "the keep-alive schedule: first at once, then every %.1f s"
          % KEEPALIVE_EVERY_S)
    check(premise_verdict(5, 0) == "VOID",
          "no keep-alive on the vehicle side is VOID, not a pass or a loss")
    check(premise_verdict(5, 5) == "OK", "every keep-alive seen is OK")
    check(premise_verdict(5, 4) == "LOST",
          "a lost keep-alive is a bridged frame lost")
    class M(object):
        def __init__(self, ident, ext, data, is_rx=True):
            self.arbitration_id, self.is_extended_id = ident, ext
            self.data, self.is_rx = bytearray(data), is_rx
    ka = M(KEEPALIVE_ID, True, KEEPALIVE_DATA)
    burst = M(0x101, False, payload(1, 0, 0))
    check(rx_keep(ka, 1) and rx_keep(burst, 1),
          "the ONE vehicle-side reader keeps both keep-alives and bursts")
    check(not rx_keep(burst, 2) and not rx_keep(M(0x7F4, False, [4, 9]), 1),
          "...but not another variant's frames or the board's status pages")
    check(not rx_keep(M(KEEPALIVE_ID, True, KEEPALIVE_DATA, is_rx=False), 1),
          "...and not an echo of our own")

    # the defect it replaced, reproduced: two readers on one handle, each
    # filtering for its own kind, lose the frames the other one takes
    class FakeBus(object):
        def __init__(self, msgs):
            self.q = list(msgs)
            self.lock = threading.Lock()

        def recv(self, timeout=0.05):
            with self.lock:
                if self.q:
                    return self.q.pop(0)
            time.sleep(0.001)
            return None
    mixed = [ka if i % 2 else burst for i in range(400)]
    two = FakeBus(mixed)
    d_b = Drain(two, lambda m: not is_keepalive(m) and rx_keep(m, 1)).start()
    d_k = Drain(two, lambda m: is_keepalive(m)).start()
    time.sleep(0.3)
    d_b.stop()
    d_k.stop()
    lost = 400 - len(d_b.got) - len(d_k.got)
    one = FakeBus(mixed)
    d_1 = Drain(one, lambda m: rx_keep(m, 1)).start()
    time.sleep(0.3)
    d_1.stop()
    check(len(d_1.got) == 400,
          "one reader keeps all 400 mixed frames (%d)" % len(d_1.got))
    check(lost > 0,
          "two readers on one handle lose frames (%d of 400 lost) -- why "
          "there is one" % lost)
    j = judge([], 0, 3)
    check(j["ok"],
          "(why VOID must be decided first: judge() of zero bursts is "
          "vacuously ok)")
    print("\nbench_rx_order self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--port", default=None,
                    help="the board's serial port, for the c2v cross-check "
                    "(opening it resets the board: open it before the run)")
    ap.add_argument("--kvaser-channel", type=int, default=0)
    ap.add_argument("--bursts", type=int, default=2000)
    ap.add_argument("--gap-ms", type=float, default=5.0,
                    help="pause between bursts")
    ap.add_argument("--variants", default="distinct,same-id,distinct-ext,"
                    "same-id-ext")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    here = os.path.dirname(os.path.abspath(__file__))
    out = args.out or os.path.join(here, "bench_rx_order_%s.txt"
                                   % datetime.datetime.now()
                                   .strftime("%Y%m%dT%H%M%S"))
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
