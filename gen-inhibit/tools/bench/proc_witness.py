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
import collections
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


# ---------------------------------------------------------------------------------------------------
# ProcThread: any tool's thread witness, its loop unchanged, run in a forked child (gen-inhibit
# reviewer's answer (5) to message 43ffe9bb, 2026-11-19: the other busy-wait tools' witnesses).
# ---------------------------------------------------------------------------------------------------

# The witness load (gen-inhibit reviewer's answer (b) to message 1bcd2aa9, 2026-11-19): a thread witness
# counts every frame its loop reads (self.recv: board frames, error frames, every kind), and the most it
# read in any LOAD_WINDOW_S; over LOAD_LIMIT its judgements are VOID "witness load". The six tools' tables
# keep their rate under the limit (test_busywait_witnesses' rate guard); this guards what no table holds:
# the boards' own frames, and the error frames of a burst or a jam. The basis (the reviewer's answer (1) to
# f29f2626, 2026-11-19): each tool's witness under its own pacer at its own rate read every frame with the
# wire fed at 2400 frames/s, for each of the three pacers (test_busywait_witnesses, part feed), and the
# limit is half the lowest feed so shown.
LOAD_WINDOW_S = 1.0
LOAD_LIMIT = 1200


def load_reason(w):
    """'witness load: ...' when a thread witness read more than LOAD_LIMIT frames in any LOAD_WINDOW_S
    so far, else None. Only a ThreadWitness (or a thread twin) is judged on it: a child's reads are
    counted in the child, where no pacer shares its process."""
    if not getattr(w, "_thread_mode", False):
        return None
    ld = (getattr(w, "proc_info", None) or {}).get("load")
    if not ld or ld["peak"] <= ld["limit"]:
        return None
    return ("witness load: %d frames read in %g s (at %.1f s), over %d (half the 2400/s feed a thread "
            "witness read in full under each pacer)" % (ld["peak"], ld["window_s"], ld["peak_at_s"], ld["limit"]))


def void_reason(w, running=True):
    """Why what a ProcThread read cannot be judged, or None: the child's error (its run raised, it
    ended without its end message, or it did not end and was killed); with running, also the child
    no longer running when the reader still needs it (a witness that stopped reading mid-run is a
    short witness, never passed off as a whole one). A ThreadWitness the same, for its thread: the
    loop raised, or it is no longer running; and its load over LOAD_LIMIT (load_reason), so a
    judgement made after the load went over is VOID, as one made after the loop died. None for
    anything not started (a test's fake)."""
    if not isinstance(w, ProcThread):
        return None
    if getattr(w, "_thread_mode", False):
        if getattr(w, "_twin_th", None) is None:
            return None
        err = w.proc_info.get("error")
        if err:
            return "the witness thread failed: %s" % err
        load = load_reason(w)
        if load:
            return load
        if running and not w.is_alive():
            return "the witness thread is not running"
        return None
    if getattr(w, "_proc", None) is None:
        return None
    err = w.proc_info.get("error")
    if err:
        return "the witness child failed: %s" % err
    if running and not w.is_alive():
        return "the witness child is not running (exit code %s)" % (w.proc_info.get("exitcode"),)
    return None


def void_line(w):
    """(reason, the line a tool prints for its suite judge) at a run's end, or (None, None): the
    witness's child or thread failed (void_reason, running=False)."""
    reason = void_reason(w, running=False)
    return reason, ("witness VOID: %s" % reason if reason else None)


def void_verdict(reason):
    return "VOID: %s, not judged" % reason


class WitnessFailed(Exception):
    """A judgement reached a witness whose child failed or stopped (require())."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def require(w, running=True):
    """Raises WitnessFailed with void_reason(w, running), if there is one."""
    why = void_reason(w, running)
    if why:
        raise WitnessFailed(why)


def event():
    """An Event shared with a forked child: the stop flag a ProcThread's run() reads in the child and
    the parent sets. A threading.Event would be the child's own copy, and set() in the parent would
    never reach it."""
    return multiprocessing.get_context("fork").Event()


def _thread_child(owner, conn, wake, sync_req, batch_s):
    """In the child: owner's run() (or its target) in a thread; every batch_s, or at once when the
    parent asks (sync) or the run ends, the new items of each shipped list, the shipped values that
    changed and the bus's stamp_stats() that changed, then ("end", stamp_stats, error)."""
    lists = owner._shipped_lists()
    sent = dict((n, 0) for n in lists)
    lock = owner._snapshot_lock()
    bus = owner._stats_bus()
    done, err = threading.Event(), []

    def runner():
        try:
            (owner._target or owner.run)()
        except BaseException as e:      # noqa: BLE001  kept and sent, never a silent short witness
            import traceback
            traceback.print_exc()       # as a thread's death prints it
            err.append("%s: %s" % (type(e).__name__, e))
        finally:
            done.set()
            wake.set()

    def stats():
        if bus is None:
            return None
        try:
            return bus.stamp_stats()
        except Exception as e:          # noqa: BLE001
            return {"stamp_stats_error": "%s: %s" % (type(e).__name__, e)}

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    last_vals, last_stats, served = None, None, 0
    while True:
        wake.wait(batch_s)
        wake.clear()                    # cleared before the request is read: a later request re-wakes
        finished = done.is_set()
        req = sync_req.value
        if lock is not None:
            lock.acquire()
        try:
            out = {}
            for n, lst in lists.items():
                k = len(lst)
                if k > sent[n]:
                    out[n] = lst[sent[n]:k]
                    sent[n] = k
            vals = dict((n, getattr(owner, n, None)) for n in owner.VALUES)
        finally:
            if lock is not None:
                lock.release()
        st = stats()
        if out or vals != last_vals or st != last_stats:
            conn.send(("batch", out, vals, st))
            last_vals, last_stats = vals, st
        if req > served:
            conn.send(("sync", req))
            served = req
        if finished:
            break
    conn.send(("end", stats(), err[0] if err else None))
    conn.close()


class ProcThread(object):
    """threading.Thread's interface as the bench tools use it (start, join, is_alive, daemon, name),
    with run() in a forked child process, so a busy-wait pacer in the tool's process cannot starve
    the reads (run cb08: a thread witness kept 1.3-30 % of an e4 replay).

    A tool's witness class changes its base from threading.Thread to ProcThread, makes its stop flag
    with self.event() (or proc_witness.event()), and names what the parent reads:
      LISTS   the list attributes run() appends to; the child sends their new items in batches and
              a receiver thread in the parent extends the parent's lists, in order;
      VALUES  other attributes run() sets (a count, an error text); sent when they change;
      BUS     the attribute holding the bus run() reads (None: no bus); its stamp_stats() is the
              child's, sent with each batch, since the stamp check's after-check counts are kept
              by the bus object that read the frames: after start(), the parent's attribute is a
              view of its bus whose stamp_stats() answers with the child's latest;
      BUS_LISTS  list attributes of that bus which its reads append to (kvaser_bus.pcan_bus's
              vtrux_states, the bus states read on past): shipped as LISTS are, into the
              parent's bus object (made when it has none).
    The class's lock attribute, if it has one, is held in the child while a batch is taken and in
    the parent while it is applied, so lists appended together stay the same length.

    A function form: ProcThread(target=fn, lists={"name": the_list, ...}) runs fn in the child and
    ships those lists (the closure's objects, copied by the fork) back into the same objects.

    Live readers see the frames BATCH_S late at most; sync() asks the child for everything read so
    far and waits for it, for a reader that marks or slices the frames while the run goes on.
    stop: whatever run() checks must be an event(); join() waits for the child's last batch and
    its end. info records the child's pid, the parent's threads at the fork, the batches, the
    syncs, the child's exit code and an error its run raised (printed in the child as well, as a
    thread's death would be)."""

    LISTS = ()
    VALUES = ()
    BUS = "bus"
    BUS_LISTS = ()

    def __init__(self, daemon=True, target=None, lists=None, name=None, batch_s=BATCH_S):
        self.daemon = daemon
        self.name = name or type(self).__name__
        self.batch_s = batch_s
        self._target = target
        self._fn_lists = dict(lists or {})
        self._ctx = multiprocessing.get_context("fork")
        self._wake = self._ctx.Event()
        self._sync_req = self._ctx.Value("i", 0, lock=False)
        self._sync_lock = threading.Lock()
        self._synced = 0
        self._cond = threading.Condition()
        self._proc = self._recv_th = self._conn = None
        self.child_stats = None
        self.child_stats_final = False
        self.proc_info = {"how": "a forked child process runs the witness loop; batches every %.3f s" % batch_s,
                          "pid": None, "parent_threads_at_fork": None, "batches": 0, "syncs": 0,
                          "exitcode": None, "error": None}

    def event(self):
        return self._ctx.Event()

    def run(self):              # the subclass's loop
        raise NotImplementedError

    def recv(self, timeout=None):
        """The witness loop's read: the BUS attribute's recv(timeout), each frame it gives counted
        (_count_read) for the load guard (load_reason). A loop reads through this, never its bus's
        recv directly (test_busywait_witnesses' load scan)."""
        m = getattr(self, self.BUS).recv(timeout=timeout)
        if m is not None:
            self._count_read(time.perf_counter())
        return m

    def _count_read(self, t):
        """One frame read at t (perf_counter): the frames read in the last LOAD_WINDOW_S, and the
        most there have been, kept in proc_info["load"]."""
        win = self.__dict__.get("_load_win")
        if win is None:
            win = self._load_win = collections.deque()
            info = getattr(self, "proc_info", None)
            if info is None:
                info = self.proc_info = {}
            info["load"] = {"window_s": LOAD_WINDOW_S, "limit": LOAD_LIMIT, "reads": 0, "peak": 0,
                            "peak_at_s": 0.0, "first_read": t}
        ld = self.proc_info["load"]
        win.append(t)
        while t - win[0] >= LOAD_WINDOW_S:
            win.popleft()
        ld["reads"] += 1
        if len(win) > ld["peak"]:
            ld["peak"], ld["peak_at_s"] = len(win), round(t - ld["first_read"], 3)

    def _raw_bus(self):
        b = getattr(self, self.BUS, None) if self.BUS else None
        return b._bus if isinstance(b, _ThreadBusView) else b

    def _shipped_lists(self):
        out = dict((n, getattr(self, n)) for n in self.LISTS)
        out.update(self._fn_lists)
        bus = self._raw_bus()
        if bus is not None:
            for n in self.BUS_LISTS:
                if not isinstance(getattr(bus, n, None), list):
                    setattr(bus, n, [])
                out["bus." + n] = getattr(bus, n)
        return out

    def _snapshot_lock(self):
        lk = getattr(self, "lock", None) if self._target is None else None
        return lk if hasattr(lk, "acquire") else None

    def _stats_bus(self):
        b = getattr(self, self.BUS, None) if self.BUS else None
        return b if hasattr(b, "stamp_stats") else None

    def start(self):
        if self._proc is not None:
            raise RuntimeError("a ProcThread can be started once")
        parent, child = self._ctx.Pipe(duplex=False)
        for f in (sys.stdout, sys.stderr):      # the child's exit flushes what it inherited
            try:
                f.flush()
            except Exception:       # noqa: BLE001
                pass
        self.proc_info["parent_threads_at_fork"] = threading.active_count()
        self._proc = self._ctx.Process(target=_thread_child,
                                       args=(self, child, self._wake, self._sync_req, self.batch_s),
                                       daemon=self.daemon)
        self._proc.start()
        child.close()
        self._conn = parent
        self.proc_info["pid"] = self._proc.pid
        bus = self._stats_bus()
        if bus is not None:                     # the parent's view; the child kept the bus itself
            setattr(self, self.BUS, _ThreadBusView(bus, self))
        self._recv_th = threading.Thread(target=self._receive, daemon=True)
        self._recv_th.start()

    def _apply(self, out, vals):
        lists = self._shipped_lists()
        lock = self._snapshot_lock()
        if lock is not None:
            lock.acquire()
        try:
            for n, items in out.items():
                lists[n].extend(items)
            for n, v in vals.items():
                setattr(self, n, v)
        finally:
            if lock is not None:
                lock.release()

    def _receive(self):
        conn = self._conn
        try:
            while True:
                try:
                    msg = conn.recv()
                except (EOFError, OSError) as e:
                    if self.proc_info["error"] is None and not self.child_stats_final:
                        self.proc_info["error"] = ("the witness child ended without its end message (%s)"
                                                   % type(e).__name__)
                    return
                if msg[0] == "batch":
                    self._apply(msg[1], msg[2])
                    if msg[3] is not None:
                        self.child_stats = msg[3]
                    self.proc_info["batches"] += 1
                elif msg[0] == "sync":
                    with self._cond:
                        self._synced = max(self._synced, msg[1])
                        self._cond.notify_all()
                elif msg[0] == "end":
                    if msg[1] is not None:
                        self.child_stats = msg[1]
                    self.child_stats_final = True
                    self.proc_info["error"] = self.proc_info["error"] or msg[2]
                    return
        finally:
            with self._cond:
                self._synced = 1 << 30          # nothing more will come: every sync is answered
                self._cond.notify_all()

    def sync(self, timeout=2.0):
        """Waits until every item the child had read when asked is in the parent's lists. True when
        it is (or the child has ended, so all it read is in); False after timeout s. True at once
        when nothing was started (or the object was made without __init__, as a test's is)."""
        if getattr(self, "_proc", None) is None:
            return True
        with self._sync_lock:
            n = self._sync_req.value + 1
            self._sync_req.value = n
        self._wake.set()
        self.proc_info["syncs"] += 1
        with self._cond:
            return self._cond.wait_for(lambda: self._synced >= n, timeout)

    def is_alive(self):
        return self._proc is not None and self._proc.is_alive()

    def join(self, timeout=None):
        """Waits for the child's last batch and its end (the stop event set first), up to timeout s;
        a child still running then is killed and that recorded."""
        t_end = None if timeout is None else time.monotonic() + timeout
        left = lambda: None if t_end is None else max(0.0, t_end - time.monotonic())
        if self._recv_th is not None:
            self._recv_th.join(left())
        if self._proc is not None:
            self._proc.join(left())
            if self._proc.is_alive():
                self.proc_info["error"] = (self.proc_info["error"]
                                           or "the witness child did not end in %s s: killed" % timeout)
                self._proc.kill()
                self._proc.join(1.0)
                if self._recv_th is not None:
                    self._recv_th.join(1.0)
            self.proc_info["exitcode"] = self._proc.exitcode


class _InThread(object):
    """ProcThread's interface with the loop in a thread of this process, its death recorded: an
    exception the loop raises is printed (as a thread's death is) and kept in proc_info["error"],
    so void_reason() names it and the arm is VOID, never a short witness passed off as a whole one
    (gen-inhibit reviewer, 2026-11-19: run cb08's B12 judge read a dead witness's 0 error frames as
    a PASS). Nothing is shipped: the lists are the thread's own."""
    _thread_mode = True

    def event(self):
        return threading.Event()

    def _run_recorded(self):
        try:
            (self._target or self.run)()
        except BaseException as e:      # noqa: BLE001  kept, never a silent short witness
            import traceback
            traceback.print_exc()
            self.proc_info["error"] = "%s: %s" % (type(e).__name__, e)

    def start(self):
        if getattr(self, "_twin_th", None) is not None:
            raise RuntimeError("a witness can be started once")
        self.proc_info["how"] = "a thread of this process runs the witness loop"
        self._twin_th = threading.Thread(target=self._run_recorded, daemon=self.daemon)
        self._twin_th.start()

    def join(self, timeout=None):
        if getattr(self, "_twin_th", None) is not None:
            self._twin_th.join(timeout)
            if self._twin_th.is_alive():
                self.proc_info["error"] = (self.proc_info["error"]
                                           or "the witness thread did not end in %s s" % timeout)

    def is_alive(self):
        return getattr(self, "_twin_th", None) is not None and self._twin_th.is_alive()

    def sync(self, timeout=2.0):
        return True


class ThreadWitness(_InThread, ProcThread):
    """A witness loop in a thread of the tool's process, as the six tools whose pacers sleep to 2 ms
    before each frame keep theirs (gen-inhibit reviewer's ruling (i), 2026-11-19: at the slice's
    454 frames/s such a pacer did not starve a thread witness; test_busywait_witnesses keeps that
    as a regression check). Same interface as ProcThread (event(), sync(), proc_info, LISTS ...),
    so a tool moves between the two by its base class; a loop that dies VOIDs as a child does."""


def thread_twin(cls):
    """cls (a ProcThread subclass, or ProcThread itself for the function form) with its loop in a
    thread of this process: for tests (the can-fail, the same loop starved in a thread where the
    child is not; and a test whose fake bus shares state with the test's own process)."""
    if getattr(cls, "_thread_mode", False):
        return cls
    twin = type(cls.__name__ + "InThread", (_InThread, cls), {})
    twin.__qualname__ = twin.__name__
    return twin


class _ThreadBusView(object):
    """A ProcThread's bus in the parent: stamp_stats() answers with the child's latest, once one
    has come; everything else is the bus's own (shutdown, status, send)."""

    def __init__(self, bus, owner):
        self._bus, self._owner = bus, owner

    def stamp_stats(self):
        if self._owner.child_stats is not None:
            return self._owner.child_stats
        return self._bus.stamp_stats()

    def __getattr__(self, name):
        return getattr(self._bus, name)
