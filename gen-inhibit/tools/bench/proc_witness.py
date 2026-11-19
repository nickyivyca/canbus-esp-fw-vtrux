"""The witness out of the replay's process (gen-inhibit reviewer's F1, 2026-11-19).

Run gen-inhibit-bench_20261119T032517_cb08 on madhouse-debian: every e4 arm's Kvaser witness kept
1.3-30 % of its replay (B3's off arm 1: 14,090 of 82,555 queued). The witness was a thread in the
process whose replay loop paces each frame with a busy-wait (`while perf() < due: pass`), and the
witness thread took one recv per frame. The reviewer's ruling: take the witness out of the replay's
process, and leave the pacers as they are.

ProcWitness has the interface of e4_rig_calibration.Witness, the thread it replaces (name_, bus,
frames, raw, rx, stop.set(), start(), join()), so settle_clock, bus_errors and the arm's readers take
it unchanged:
  - start() forks a child process, which inherits the bus the parent opened (its socket and its
    stamp check) and reads it with one recv per frame, as the thread did, in a process of its own;
  - the child sends what it read to the parent in batches every BATCH_S, and a receiver thread in
    the parent extends frames, raw and rx, so a reader in the parent (e4's wait_for_quiet) sees
    the frames while the arm runs, BATCH_S late at most, plus the parent's scheduling;
  - stop.set() asks the child to stop; join() waits for the child to read what is still queued on
    the bus, send its last batch and its bus's stamp_stats(), and exit;
  - bus is the parent's bus, but stamp_stats() answers with the child's: the stamp check's
    after-check counts (hw_zero, hw_backward) are kept by the bus object that read the frames.
The parent opens and closes the bus as before and never reads it while the child runs.

info records how the witness ran: the child's pid, how many threads the parent had when it forked
(the fork copies only the forking thread), the batches, and the child's end (its exit code, and an
error it raised, which also goes in the frames' place: no frame, the error kept, never a short
witness passed off as a whole one).
"""
import multiprocessing
import sys
import threading
import time

import witness_clock as WC  # noqa: E402  the stamp a Kvaser witness keeps (raw_stamp, receipt)

BATCH_S = 0.05


def _child(bus, conn, stop_ev, batch_s):
    """In the child: read every frame bus.recv gives until stop_ev is set and the bus has no more,
    sending batches of (raw, rx, frame) as they come, then ("end", stamp_stats or None, error)."""
    lock = threading.Lock()
    batch, done, err = [], threading.Event(), []

    def reader():
        recv = bus.recv
        try:
            while True:
                m = recv(timeout=0.2 if not stop_ev.is_set() else 0.0)
                if m is None:
                    if stop_ev.is_set():
                        break
                    continue
                raw, rx = WC.raw_stamp(m), WC.receipt(m)
                item = (raw, rx, (raw if raw is not None else rx, m.arbitration_id, bytes(m.data),
                                  bool(m.is_error_frame), bool(m.is_extended_id)))
                with lock:
                    batch.append(item)
        except BaseException as e:      # noqa: BLE001  kept and sent, never a silent short witness
            err.append("%s: %s" % (type(e).__name__, e))
        finally:
            done.set()

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    while True:
        finished = done.wait(batch_s)
        with lock:
            out = batch[:]
            del batch[:]
        if out:
            conn.send(("frames", out))
        if finished:
            break
    try:
        stats = bus.stamp_stats() if hasattr(bus, "stamp_stats") else None
    except Exception as e:              # noqa: BLE001
        stats, err = None, err + ["stamp_stats: %s: %s" % (type(e).__name__, e)]
    conn.send(("end", stats, err[0] if err else None))
    conn.close()


class _Stop(object):
    """The thread witness's stop Event, as far as its callers use it: set() and is_set()."""

    def __init__(self, ev):
        self._ev = ev

    def set(self):
        self._ev.set()

    def is_set(self):
        return self._ev.is_set()


class _BusView(object):
    """The parent's bus, with stamp_stats() answered by the child's bus once it has ended."""

    def __init__(self, bus, owner):
        self._bus, self._owner = bus, owner

    def stamp_stats(self):
        if self._owner.child_stats is not None:
            return self._owner.child_stats
        return self._bus.stamp_stats()

    def __getattr__(self, name):
        return getattr(self._bus, name)


class ProcWitness(object):
    def __init__(self, name, bus, batch_s=BATCH_S):
        self.name_ = name
        self.real_bus = bus
        self.bus = _BusView(bus, self)
        self.frames, self.raw, self.rx = [], [], []
        self.child_stats = None
        self.batch_s = batch_s
        self._ctx = multiprocessing.get_context("fork")
        self._ev = self._ctx.Event()
        self.stop = _Stop(self._ev)
        self._proc = self._recv_th = self._conn = None
        self.info = {"how": "a forked child process reads the bus; batches every %.3f s" % batch_s,
                     "pid": None, "parent_threads_at_fork": None, "batches": 0, "exitcode": None,
                     "error": None}

    def start(self):
        parent, child = self._ctx.Pipe(duplex=False)
        # the child's exit flushes the stdio it inherited: anything still buffered here would print twice
        for f in (sys.stdout, sys.stderr):
            try:
                f.flush()
            except Exception:       # noqa: BLE001  a closed or odd stream: nothing to print twice
                pass
        self.info["parent_threads_at_fork"] = threading.active_count()
        self._proc = self._ctx.Process(target=_child, args=(self.real_bus, child, self._ev, self.batch_s),
                                       daemon=True)
        self._proc.start()
        child.close()
        self._conn = parent
        self.info["pid"] = self._proc.pid
        self._recv_th = threading.Thread(target=self._receive, daemon=True)
        self._recv_th.start()

    def _receive(self):
        conn = self._conn
        while True:
            try:
                msg = conn.recv()
            except (EOFError, OSError) as e:
                if self.info["error"] is None and self.child_stats is None:
                    self.info["error"] = "the witness child ended without its end message (%s)" % type(e).__name__
                return
            if msg[0] == "frames":
                raws, rxs, frs = zip(*msg[1])
                self.raw.extend(raws)
                self.rx.extend(rxs)
                self.frames.extend(frs)
                self.info["batches"] += 1
            elif msg[0] == "end":
                self.child_stats, self.info["error"] = msg[1], msg[2]
                return

    def is_alive(self):
        return self._proc is not None and self._proc.is_alive()

    def join(self, timeout=None):
        """Waits for the child's last batch and its end (stop.set() first), up to timeout s."""
        t_end = None if timeout is None else time.monotonic() + timeout
        left = lambda: None if t_end is None else max(0.0, t_end - time.monotonic())
        if self._recv_th is not None:
            self._recv_th.join(left())
        if self._proc is not None:
            self._proc.join(left())
            if self._proc.is_alive():
                self.info["error"] = self.info["error"] or "the witness child did not end in %s s: killed" % timeout
                self._proc.kill()
                self._proc.join(1.0)
            self.info["exitcode"] = self._proc.exitcode
        if self._conn is not None:
            self._conn.close()
        return None
