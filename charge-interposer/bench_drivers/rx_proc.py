"""A CAN reader in its own process, for a check that reads one adapter while
it sends busily on another (the charge-interposer reviewer's M6,
2026-11-13).

Measured under a hand take the same day (runs/kvaser_usb_measure_
20261113T102011, reverse-it interposer-firmware): with reader threads beside
a busy-wait sender in one process, the readers lost the GIL. The PCAN's
driver queue overflowed (582 kernel "lost: err -28" lines) and the echo
reader got 927 of 2,000 frames. With each reader in a process of its own
(run 2) every frame arrived. So a reader that a check's verdict counts on,
beside a sender in the same check, runs here.

    r = ProcReader("bench_usb_host", "open_pcan", {"acks": True}, name="pcan")
    r.wait_ready()          # the bus is open in the child
    ... send ...
    res = r.finish()        # stop, drain DRAIN_S, rows back
    res["rows"]             # (t_dev, t_host, id, ext, dlc, data_hex, is_rx, is_err)
    res["recv_errors"]      # the CanError texts recv() raised (a PCAN queue
                            # overrun shows here), with their host times
    res["status"]           # the bus's status() at the end, if it has one

The opener is named by module and function, with keyword arguments, so the
child (a spawn, not a fork: the parent may hold threads) imports and calls it
itself; bench_drivers/ and charge-interposer/ are on its path. t_host is
time.perf_counter(), CLOCK_MONOTONIC on Linux, the same clock in every
process. Own echoes (is_rx False) are kept only with keep_echoes.

lost_lines(t0, t1) are the kernel's lines in an epoch window that report
frames a driver lost (pcan's "lost: err -28"; kvaser_link.kernel_lines
filtered): a check whose witness lost frames is VOID with them.

    python3.13 -B rx_proc.py --self-test | --can-fail
"""

import multiprocessing as mp
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
for _p in (CODE, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DRAIN_S = 0.5
READY_S = 30.0
LOST_RE = re.compile(r"\blost\b|overrun|overflow", re.I)


def _child(mod, fn, kw, keep_echoes, conn, ready, stop, paths, count_ids=(), count=None):
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)
    import importlib
    rows, errs, status = [], [], None
    try:
        bus = getattr(importlib.import_module(mod), fn)(**kw)
    except Exception as e:                              # noqa: BLE001
        conn.send({"opened": False, "error": "%s: %s" % (type(e).__name__, e),
                   "rows": [], "recv_errors": [], "status": None})
        conn.close()
        ready.set()
        return
    ready.set()
    end = None
    try:
        while True:
            if stop.is_set() and end is None:
                end = time.perf_counter() + DRAIN_S
            if end is not None and time.perf_counter() >= end:
                break
            try:
                m = bus.recv(timeout=0.05)
            except Exception as e:                      # noqa: BLE001
                errs.append((time.perf_counter(), "%s: %s" % (type(e).__name__, e)))
                continue
            if m is None:
                continue
            is_rx = bool(getattr(m, "is_rx", True))
            if not is_rx and not keep_echoes:
                continue
            if is_rx and count is not None and m.arbitration_id in count_ids:
                with count.get_lock():
                    count.value += 1
            rows.append((m.timestamp, time.perf_counter(), m.arbitration_id,
                         bool(m.is_extended_id), m.dlc, bytes(m.data).hex(), is_rx,
                         bool(m.is_error_frame)))
        try:
            status = bus.status() if hasattr(bus, "status") else None
        except Exception as e:                          # noqa: BLE001
            status = "status() raised %s" % e
    finally:
        try:
            bus.shutdown()
        except Exception as e:                          # noqa: BLE001
            errs.append((time.perf_counter(), "shutdown: %s" % e))
    conn.send({"opened": True, "error": None, "rows": rows, "recv_errors": errs,
               "status": status})
    conn.close()


class ProcReader:
    def __init__(self, mod, fn, kw=None, name="reader", keep_echoes=False, count_ids=()):
        self.name = name
        ctx = mp.get_context("spawn")
        self._ready, self._stop = ctx.Event(), ctx.Event()
        self._count = ctx.Value("i", 0)
        self._rx, tx = ctx.Pipe(duplex=False)
        self._p = ctx.Process(target=_child, args=(
            mod, fn, dict(kw or {}), keep_echoes, tx, self._ready, self._stop,
            [CODE, HERE], tuple(count_ids), self._count), daemon=True)
        self._p.start()
        tx.close()
        self._res = None

    def wait_ready(self, timeout=READY_S):
        """True once the child has opened its bus (or failed to: finish()
        then says why)."""
        return self._ready.wait(timeout)

    def count(self):
        """Received frames (not echoes) with an identifier in count_ids so
        far, live."""
        return self._count.value

    def finish(self, timeout=60.0):
        """Stop, drain, and -> the child's result dict. A child that sends
        nothing within timeout is killed: {"opened": None, "error": ...}."""
        if self._res is not None:
            return self._res
        self._stop.set()
        res = None
        if self._rx.poll(timeout):
            try:
                res = self._rx.recv()
            except EOFError:
                res = None
        self._p.join(5.0)
        if self._p.is_alive():
            self._p.kill()
            self._p.join(5.0)
        if res is None:
            res = {"opened": None, "rows": [], "recv_errors": [], "status": None,
                   "error": "the %s reader process sent nothing (exit %s)"
                            % (self.name, self._p.exitcode)}
        self._res = res
        return res


def lost_lines(t0, t1, journal=None):
    """The kernel's USB/CAN lines in [t0, t1] (epoch) that report lost
    frames."""
    import kvaser_link
    return [ln for ln in kvaser_link.kernel_lines(t0, t1, journal) if LOST_RE.search(ln)]


def witness_void(name, res, t0, t1, journal=None):
    """A reader process's result -> the VOID line when it did not open,
    raised recv errors, or the kernel reports frames lost in [t0, t1]
    (epoch), else None: a witness that may have missed frames is not
    judged on (the reviewer's addendum, 2026-11-13: the kernel's lines on
    the line)."""
    import kvaser_link
    if not res.get("opened"):
        why = "the %s reader: %s" % (name, res.get("error"))
    elif res.get("recv_errors"):
        why = "the %s reader: %d recv error(s), first %s" % (
            name, len(res["recv_errors"]), res["recv_errors"][0][1])
    else:
        lost = lost_lines(t0, t1, journal)
        if not lost:
            return None
        why = "the kernel reports %d lost-frame line(s) while the %s witnessed" % (len(lost), name)
    return kvaser_link.void_line(why, t0, t1, journal)


def as_msgs(res):
    """A result's rows as message-like objects (arbitration_id,
    is_extended_id, dlc, data, is_rx, is_error_frame, timestamp, t_host)."""
    import types
    return [types.SimpleNamespace(timestamp=r[0], t_host=r[1], arbitration_id=r[2],
                                  is_extended_id=r[3], dlc=r[4], data=bytes.fromhex(r[5]),
                                  is_rx=r[6], is_error_frame=r[7]) for r in res["rows"]]


# --- self-test -----------------------------------------------------------------

class _Msg:
    def __init__(self, i, is_rx=True):
        self.timestamp, self.arbitration_id, self.is_extended_id = 1000.0 + i, 0x100 + i, False
        self.dlc, self.data, self.is_rx, self.is_error_frame = 1, bytes([i]), is_rx, False


class FakeBus:
    """For the self-test, opened in the child: offers n frames (every third
    an own echo), raises one recv error after the first, then goes quiet."""

    def __init__(self, n=6, fail_open=False, hang=False, delay=0.0):
        if fail_open:
            raise OSError("no such adapter")
        self.n, self.i, self.hang = n, 0, hang
        self.raised = False
        self.t_free = time.monotonic() + delay

    def recv(self, timeout=None):
        if self.hang:
            time.sleep(3600)
        if time.monotonic() < self.t_free:
            time.sleep(timeout or 0)
            return None
        if self.i == 1 and not self.raised:
            self.raised = True
            raise RuntimeError("PCAN_ERROR_QOVERRUN")
        if self.i < self.n:
            self.i += 1
            return _Msg(self.i - 1, is_rx=(self.i - 1) % 3 != 2)
        time.sleep(timeout or 0)
        return None

    def status(self):
        return 0x40

    def shutdown(self):
        pass


def open_fake(**kw):
    return FakeBus(**kw)


def self_test():
    fails = []

    def check(ok, what):
        print("%s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)
    me = os.path.splitext(os.path.basename(__file__))[0]
    r = ProcReader(me, "open_fake", {"n": 6}, name="fake")
    check(r.wait_ready(30), "the child opens its bus and says so")
    time.sleep(0.3)
    res = r.finish()
    ids = [x[2] for x in res["rows"]]
    check(res["opened"] is True and ids == [0x100, 0x101, 0x103, 0x104],
          "every received frame comes back in order, own echoes dropped: %s"
          % [hex(i) for i in ids])
    check(len(res["recv_errors"]) == 1 and "QOVERRUN" in res["recv_errors"][0][1],
          "a recv error is recorded with its text, not swallowed: %s" % res["recv_errors"])
    check(res["status"] == 0x40, "the bus's status() at the end: %r" % res["status"])
    check(len(res["rows"][0]) == 8 and res["rows"][0][5] == "00",
          "rows are (t_dev, t_host, id, ext, dlc, data_hex, is_rx, is_err)")
    t_child = res["rows"][0][1]
    check(abs(time.perf_counter() - t_child) < 30,
          "t_host is perf_counter, the parent's clock: %.3f vs %.3f"
          % (t_child, time.perf_counter()))
    r = ProcReader(me, "open_fake", {"n": 6}, name="fake", keep_echoes=True,
                   count_ids=(0x101, 0x102, 0x104))
    r.wait_ready(30)
    t_end = time.monotonic() + 10
    while r.count() < 2 and time.monotonic() < t_end:
        time.sleep(0.02)
    time.sleep(0.3)                  # the rest of the fake's frames are in
    n_live = r.count()
    r.finish()
    check(n_live == 2, "count(): the received frames with a counted identifier, live, "
                       "echoes not counted (0x102 is an echo): %d" % n_live)
    r = ProcReader(me, "open_fake", {"n": 6}, name="fake", keep_echoes=True)
    r.wait_ready(30)
    time.sleep(0.3)
    res = r.finish()
    check([x[6] for x in res["rows"]].count(False) == 2,
          "keep_echoes keeps them, is_rx False: %s" % [x[6] for x in res["rows"]])
    r = ProcReader(me, "open_fake", {"n": 2, "delay": 0.25}, name="fake")
    r.wait_ready(30)
    res = r.finish()
    check(len(res["rows"]) == 2,
          "frames arriving within DRAIN_S after the stop are still read: %d" % len(res["rows"]))
    r = ProcReader(me, "open_fake", {"fail_open": True}, name="fake")
    r.wait_ready(30)
    res = r.finish()
    check(res["opened"] is False and "no such adapter" in res["error"],
          "an open that fails says why: %s" % res["error"])
    r = ProcReader(me, "open_fake", {"hang": True}, name="fake")
    r.wait_ready(30)
    t0 = time.monotonic()
    res = r.finish(timeout=2.0)
    check(res["opened"] is None and "sent nothing" in res["error"]
          and time.monotonic() - t0 < 20,
          "a hung child is killed and reported: %s" % res["error"])
    planted = ("2026-11-13T10:28:00.6-08:00 madhouse-debian kernel: pcan32: PCAN-USB FD CAN1: "
               "msg[type=1 id=7a9h l=8] lost: err -28 rxqsize=2000\n"
               "2026-11-13T10:28:01.0-08:00 madhouse-debian kernel: gs_usb 1-3.2.3:1.0 canmain: "
               "renamed from can0\n")
    ll = lost_lines(0, 1, lambda s, u: planted)
    check(len(ll) == 1 and "lost: err -28" in ll[0],
          "lost_lines: a planted pcan 'lost' line, and only it: %s" % ll)
    planted2 = planted + ("2026-11-13T10:28:02.0-08:00 madhouse-debian kernel: usb 1-3.2.1: "
                          "error -71\n")
    ok_res = {"opened": True, "rows": [], "recv_errors": [], "status": 0}
    check(witness_void("pcan", ok_res, 0, 1, lambda s, u: "") is None,
          "witness_void: opened, no errors, no lost lines -> judged")
    v = witness_void("pcan", ok_res, 0, 1, lambda s, u: planted2)
    check(v is not None and v.startswith("VOID: the kernel reports 1 lost-frame line")
          and "lost: err -28" in v and "error -71" in v,
          "witness_void: a planted 'lost' line -> VOID naming the kernel's lines: %s" % v)
    v = witness_void("pcan", dict(ok_res, recv_errors=[(1.0, "PCAN_ERROR_QOVERRUN")]), 0, 1,
                     lambda s, u: "")
    check(v is not None and "QOVERRUN" in v, "witness_void: a recv error -> VOID")
    v = witness_void("kvaser", dict(ok_res, opened=False, error="no device"), 0, 1,
                     lambda s, u: "")
    check(v is not None and v.startswith("VOID:") and "no device" in v,
          "witness_void: a reader that did not open -> VOID")
    m = as_msgs({"rows": [(1.5, 2.5, 0x6A2, True, 8, "0001020304050607", False, True)]})[0]
    check((m.timestamp, m.t_host, m.arbitration_id, m.is_extended_id, m.dlc, m.data, m.is_rx,
           m.is_error_frame) == (1.5, 2.5, 0x6A2, True, 8, bytes(range(8)), False, True),
          "as_msgs: every field of a row")
    print("\nrx_proc self-test: %s" % ("all checks OK" if not fails
                                      else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("echoes kept regardless", "            if not is_rx and not keep_echoes:\n                continue",
     "            pass"),
    ("echoes never kept", "            if not is_rx and not keep_echoes:",
     "            if not is_rx:"),
    ("a recv error swallowed",
     "                errs.append((time.perf_counter(), \"%s: %s\" % (type(e).__name__, e)))",
     "                pass"),
    ("no drain after stop", "                end = time.perf_counter() + DRAIN_S",
     "                end = time.perf_counter() - 1"),
    ("a hung child waited for far past the timeout", "        if self._rx.poll(timeout):",
     "        if self._rx.poll(timeout * 20):"),
    ("lost lines unfiltered",
     "    return [ln for ln in kvaser_link.kernel_lines(t0, t1, journal) if LOST_RE.search(ln)]",
     "    return kvaser_link.kernel_lines(t0, t1, journal)"),
    ("count(): echoes counted",
     "            if is_rx and count is not None and m.arbitration_id in count_ids:",
     "            if count is not None and m.arbitration_id in count_ids:"),
    ("count(): every frame counted",
     "            if is_rx and count is not None and m.arbitration_id in count_ids:",
     "            if is_rx and count is not None:"),
    ("witness_void: lost frames ignored", "        if not lost:\n            return None",
     "        return None"),
    ("witness_void: recv errors ignored", "    elif res.get(\"recv_errors\"):", "    elif False:"),
    ("witness_void: a reader that did not open judged", "    if not res.get(\"opened\"):",
     "    if False:"),
    ("the host time not perf_counter",
     "            rows.append((m.timestamp, time.perf_counter(), m.arbitration_id,",
     "            rows.append((m.timestamp, time.time(), m.arbitration_id,"),
]


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if argv == ["--can-fail"]:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    print("usage: rx_proc.py --self-test | --can-fail")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
