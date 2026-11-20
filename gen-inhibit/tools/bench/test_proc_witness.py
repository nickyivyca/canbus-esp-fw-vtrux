#!/usr/bin/env python3
"""Harness test for proc_witness.ProcWitness, the witness out of the replay's process (gen-inhibit
reviewer's F1, 2026-11-19), with its can-fail.

    python3.13 -B gen-inhibit/tools/bench/test_proc_witness.py [--mutants]

No hardware. The bus is a datagram socketpair: a feeder process (the wire) sends frames at the e4
replay rate, RATE per s for SECONDS s, non-blocking, so a frame the reader does not take in time is
dropped as a CAN raw socket drops it; the witness reads the other end through a bus object with
recv(timeout), as python-can's. Meanwhile the test's own process paces at RATE with a busy-wait, as
e4_device_calibration's replay loop does (`while perf() < due: pass`, the pacer the reviewer said to
leave as it is).

  every frame   ProcWitness reads every frame the feeder sent (the bound: all of them; the feeder
                counts what it put on the socket), in order, each with its raw stamp and receipt
                time, frames/raw/rx the same length.
  can-fail      e4_rig_calibration.Witness, the thread ProcWitness replaces, under the same pacer
                and the same feed, does NOT read every frame: the case the check is for (run cb08:
                1.3-30 % of each replay). If it ever does, the case no longer tells the two apart
                and the test FAILs, saying so.
  live          frames reach the parent while the child runs (e4's wait_for_quiet reads them then).
  stats         bus.stamp_stats() answers with the child's bus's, once it has ended; the parent's
                bus answers before.
  error         a recv that raises in the child is kept in info["error"], the frames before it kept.
  end           join() returns after stop.set(), the child gone, its exit code 0.

--mutants applies each edit in MUTANTS to a copy of proc_witness.py in the worktree's scratch/ and
requires a FAIL line on each. Runs in build.slice (AGENTS.md); about 40 s.
"""
import importlib.util
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders

SCRIPT = os.environ.get("PROC_WITNESS") or os.path.join(HERE, "proc_witness.py")
_spec = importlib.util.spec_from_file_location("proc_witness", SCRIPT)
PW = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PW)

RATE = 2400.0       # frames/s: e4's replays run at 2356-2445 (B26's 82464 frames in 35 s)
SECONDS = 5.0

fails, oks = [], 0


def check(name, ok, detail=""):
    global oks
    if ok:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


class Msg(object):
    def __init__(self, k, t):
        self.arbitration_id = 0x100 + k % 0x500
        self.data = struct.pack("<II", k, k ^ 0x5A5A5A5A)
        self.is_error_frame, self.is_extended_id = False, False
        self.timestamp = t          # raw_stamp's stamp (no hw_timestamp attribute: CANlib's form)
        self.host_timestamp = t     # receipt's


class SockBus(object):
    """One end of the socketpair, read as python-can's recv(timeout) reads a bus."""

    def __init__(self, sock, stats=None, raise_after=None):
        self.sock, self.stats, self.raise_after, self.n = sock, stats, raise_after, 0

    def recv(self, timeout=None):
        if self.raise_after is not None and self.n >= self.raise_after:
            raise OSError("the adapter went away (test)")
        try:
            self.sock.settimeout(timeout if timeout else None)
            if not timeout:
                self.sock.setblocking(False)
            d = self.sock.recv(64)
        except (socket.timeout, BlockingIOError):
            return None
        self.n += 1
        k = struct.unpack("<I", d[:4])[0]
        return Msg(k, time.time())

    def stamp_stats(self):
        return dict(self.stats or {}, read_here=self.n)


def feeder(sock, n, rate, sent_w):
    """In a child: n frames at rate, non-blocking (a full buffer drops the frame); writes how many
    the socket took to sent_w."""
    sock.setblocking(False)
    took = 0
    t0 = time.perf_counter()
    for k in range(n):
        due = t0 + k / rate
        while time.perf_counter() < due:
            time.sleep(0)
        try:
            sock.send(struct.pack("<II", k, 0) + b"\0" * 8)
            took += 1
        except BlockingIOError:
            pass
    os.write(sent_w, struct.pack("<I", took))
    os._exit(0)


def run(make_witness, live_probe=None):
    """Feeds RATE x SECONDS frames while this process busy-waits at RATE; returns (frames the
    socket took, the witness after join, frames seen in the parent mid-run)."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    a.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    w = make_witness(SockBus(a, stats={"state": "hardware", "after_check": {"hw_zero": 0}}))
    w.start()
    time.sleep(0.3)
    r, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:
        a.close()
        os.close(r)
        feeder(b, int(RATE * SECONDS), RATE, wfd)
    b.close()
    os.close(wfd)
    perf = time.perf_counter
    base, k, mid = perf(), 0, None
    while perf() - base < SECONDS + 0.3:      # the replay's pacer, as e4's: a busy-wait per frame
        due = base + k / RATE
        while perf() < due:
            pass
        k += 1
        if mid is None and perf() - base > SECONDS / 2:
            mid = len(w.frames)
    os.waitpid(pid, 0)
    took = struct.unpack("<I", os.read(r, 4))[0]
    os.close(r)
    time.sleep(0.5)
    w.stop.set()
    w.join(timeout=10.0)
    a.close()
    return took, w, mid


def main():
    import e4_rig_calibration as R  # noqa: E402  the thread witness ProcWitness replaces

    # The wire carries every frame the feeder sends: a reader too slow fills the socket and the frame
    # is lost to it (here the send is refused and counted out of `took`; on CAN the raw socket drops
    # it). So the bound is every frame sent, int(RATE * SECONDS).
    wire = int(RATE * SECONDS)
    took, w, mid = run(lambda bus: PW.ProcWitness("kvaser", bus))
    n = len(w.frames)
    ks = [struct.unpack("<I", f[2][:4])[0] for f in w.frames]
    check("every frame: ProcWitness read every frame on the wire, under a busy-wait pacer at %d/s" % RATE,
          took == wire and n == wire, "read %d, the socket took %d, of %d sent" % (n, took, wire))
    check("every frame: in the order sent, none twice", ks == sorted(set(ks)), ks[:5])
    check("every frame: raw stamp and receipt time beside each frame",
          len(w.raw) == len(w.rx) == n and all(x is not None for x in w.raw[:100]))
    check("live: frames reached the parent while the child read (mid-run)", mid is not None and mid > 0, mid)
    check("stats: stamp_stats answers with the child's bus (its reads), once it ended",
          (w.bus.stamp_stats() or {}).get("read_here") == took, w.bus.stamp_stats())
    check("end: the child gone after join, exit code 0, no error",
          not w.is_alive() and w.info["exitcode"] == 0 and w.info["error"] is None, w.info)
    check("info: the child's pid and the parent's threads at the fork recorded",
          isinstance(w.info["pid"], int) and isinstance(w.info["parent_threads_at_fork"], int), w.info)

    took_t, wt, _ = run(lambda bus: R.Witness("kvaser", bus))
    nt = len(wt.frames)
    check("can-fail: the thread witness under the same pacer misses frames on the wire (the case this check is for)",
          nt < wire, "the thread read %d of %d: this case no longer tells them apart" % (nt, wire))
    print("     thread witness: %d of %d sent (%.1f %%); ProcWitness: %d of %d"
          % (nt, wire, 100.0 * nt / wire, n, wire))

    # stop with frames still queued: the child reads them all before it ends (a short tail lost
    # at the end of an arm would be the frames right before the device's read)
    class SlowBus(SockBus):
        def recv(self, timeout=None):
            time.sleep(0.002)
            return SockBus.recv(self, timeout)
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    a.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    b.setblocking(False)            # a full queue refuses the send, never blocks the test
    queued = 0
    for k in range(300):
        try:
            b.send(struct.pack("<II", k, 0) + b"\0" * 8)
            queued += 1
        except OSError:
            break
    w = PW.ProcWitness("kvaser", SlowBus(a))
    w.start()
    w.stop.set()
    w.join(timeout=10.0)
    check("stop: the frames still queued when stop is set are read before the child ends",
          queued > 50 and len(w.frames) == queued, "read %d of %d queued" % (len(w.frames), queued))
    a.close()
    b.close()

    # a recv that raises in the child: kept, with the frames before it
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    for k in range(10):
        b.send(struct.pack("<II", k, 0) + b"\0" * 8)
    w = PW.ProcWitness("kvaser", SockBus(a, raise_after=4))
    before = w.bus.stamp_stats()
    w.start()
    time.sleep(0.5)
    w.stop.set()
    w.join(timeout=5.0)
    check("error: a recv that raises is kept in info, the frames before it kept, not a short witness passed off",
          len(w.frames) == 4 and "went away" in (w.info["error"] or ""), (len(w.frames), w.info))
    check("stats: before the child ends, the parent's bus answers", before == {"read_here": 0}, before)
    a.close()
    b.close()
    proc_thread_cases()
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


def proc_thread_cases():
    """ProcThread, a tool's own witness loop in a forked child (2026-11-19): what the parent gets back."""
    class W(PW.ProcThread):
        LISTS = ("frames", "rx")
        VALUES = ("count", "note")
        BUS_LISTS = ("states",)

        def __init__(self, bus, batch_s=PW.BATCH_S, ignore_stop=False):
            super().__init__(daemon=True, batch_s=batch_s)
            self.bus, self.frames, self.rx, self.count, self.note = bus, [], [], 0, None
            self.lock = threading.Lock()
            self.stop = self.event()
            self.ignore_stop = ignore_stop

        def run(self):
            while self.ignore_stop or not self.stop.is_set():
                m = self.bus.recv(timeout=0.05)
                if m is None:
                    continue
                with self.lock:
                    self.frames.append(struct.unpack("<I", m.data[:4])[0])
                    self.rx.append(m.host_timestamp)
                    self.count += 1
                self.note = "seen %d" % self.count
                if self.count % 10 == 0:
                    self.bus.states.append(self.count)

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    for k in range(100):
        b.send(struct.pack("<II", k, 0) + b"\0" * 8)
    bus = SockBus(a)
    bus.states = []
    w = W(bus, batch_s=30.0)        # no batch on its own within the test: only sync() brings frames
    w.start()
    time.sleep(0.5)
    before = len(w.frames)
    synced = w.sync(timeout=5.0)
    check("ProcThread sync: the frames come only when asked, then all the child had read",
          before == 0 and synced and w.frames == list(range(100)), (before, synced, len(w.frames)))
    check("ProcThread VALUES: the child's count and note", (w.count, w.note) == (100, "seen 100"), (w.count, w.note))
    check("ProcThread lock: lists appended together arrive the same length", len(w.rx) == len(w.frames))
    check("ProcThread BUS: stamp_stats answers with the child's bus while it runs",
          (w.bus.stamp_stats() or {}).get("read_here") == 100 and bus.n == 0, (w.bus.stamp_stats(), bus.n))
    check("ProcThread BUS_LISTS: the child's bus list lands in the parent's bus object",
          bus.states == list(range(10, 101, 10)), bus.states)
    check("ProcThread: void_reason None while the child runs whole", PW.void_reason(w) is None, PW.void_reason(w))
    w.stop.set()
    w.join(timeout=10.0)
    check("ProcThread end: the child gone, exit code 0, its last stats kept, no void",
          not w.is_alive() and w.proc_info["exitcode"] == 0 and w.child_stats_final
          and PW.void_line(w) == (None, None), (w.proc_info, PW.void_line(w)))
    check("ProcThread: void_reason with running names a child no longer running",
          (PW.void_reason(w, running=True) or "").startswith("the witness child is not running"),
          PW.void_reason(w, running=True))
    a.close()
    b.close()

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    bus = SockBus(a)
    bus.states = []
    w = W(bus, ignore_stop=True)
    w.start()
    w.stop.set()
    w.join(timeout=0.5)
    check("ProcThread join: a child that does not end in time is killed, and that is the void reason",
          not w.is_alive() and "did not end" in (PW.void_reason(w, running=False) or ""), w.proc_info)
    a.close()
    b.close()

    got = []

    def fn():
        got.append(1)
        raise OSError("gone (test)")
    w = PW.ProcThread(target=fn, lists={"got": got})
    w.start()
    w.join(timeout=5.0)
    check("ProcThread function form: the list comes back into the same object, the error kept",
          got == [1] and "gone" in (w.proc_info["error"] or ""), (got, w.proc_info))

    tw = PW.ThreadWitness(target=fn, lists={})
    tw.start()
    tw.join(timeout=5.0)
    check("ThreadWitness: a loop that raises in its thread is kept and named",
          (PW.void_reason(tw, running=False) or "").startswith("the witness thread failed: OSError: gone"),
          tw.proc_info)


MUTANTS = [
    ("the witness reads in the parent's process, as before",
     '        self._ctx = multiprocessing.get_context("fork")\n        self._ev = self._ctx.Event()\n',
     '        self._ctx = multiprocessing.get_context("fork")\n        self._ev = self._ctx.Event()\n'
     '        self._reads_in_parent = True\n'),
    ("the child stops at the first empty read, leaving the queue unread",
     "                m = recv(timeout=0.2 if not stop_ev.is_set() else 0.0)\n",
     "                m = recv(timeout=0.2) if not stop_ev.is_set() else None\n"),
    ("the last batch not sent", "        if out:\n            conn.send((\"frames\", out))\n        if finished:\n            break\n",
     "        if out and not finished:\n            conn.send((\"frames\", out))\n        if finished:\n            break\n"),
    ("stamp_stats from the parent's bus",
     "    def stamp_stats(self):\n        if self._owner.child_stats is not None:\n            return self._owner.child_stats\n"
     "        return self._bus.stamp_stats()\n\n    def __getattr__(self, name):\n        return getattr(self._bus, name)\n"
     "\n\nclass ProcWitness",
     "    def stamp_stats(self):\n        if False:\n            return self._owner.child_stats\n"
     "        return self._bus.stamp_stats()\n\n    def __getattr__(self, name):\n        return getattr(self._bus, name)\n"
     "\n\nclass ProcWitness"),
    ("an error in the child dropped",
     "never a silent short witness\n            err.append(\"%s: %s\" % (type(e).__name__, e))\n",
     "never a silent short witness\n            pass\n"),
    ("frames only at the end, not while the child runs", "        finished = done.wait(batch_s)\n",
     "        finished = done.wait(3600)\n"),
    # ProcThread (2026-11-19)
    ("ProcThread: a sync never answered", "            conn.send((\"sync\", req))\n", "            pass\n"),
    ("ProcThread: VALUES not sent", "            vals = dict((n, getattr(owner, n, None)) for n in owner.VALUES)\n",
     "            vals = {}\n"),
    ("ProcThread: BUS_LISTS not shipped", "                out[\"bus.\" + n] = getattr(bus, n)\n", "                pass\n"),
    ("ProcThread: stamp_stats from the parent's bus",
     "the bus's own (shutdown, status, send).\"\"\"\n\n    def __init__(self, bus, owner):\n"
     "        self._bus, self._owner = bus, owner\n\n    def stamp_stats(self):\n"
     "        if self._owner.child_stats is not None:\n",
     "the bus's own (shutdown, status, send).\"\"\"\n\n    def __init__(self, bus, owner):\n"
     "        self._bus, self._owner = bus, owner\n\n    def stamp_stats(self):\n"
     "        if False:\n"),
    ("ProcThread: a child that does not end is left running", "                self._proc.kill()\n                self._proc.join(1.0)\n"
     "                if self._recv_th is not None:\n", "                if self._recv_th is not None:\n"),
    ("ProcThread: the function form's error dropped",
     "            traceback.print_exc()       # as a thread's death prints it\n            err.append(",
     "            traceback.print_exc()       # as a thread's death prints it\n            (lambda x: None)("),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read()
    base = gi_paths.scratch("mutants-proc_witness")
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        mut = src.replace(old, new)
        if n == 1:      # run the reader in a thread of the parent: the class's start() replaced
            mut += ('\n\ndef _thread_start(self):\n    parent, child = self._ctx.Pipe(duplex=False)\n'
                    '    self._conn = parent\n    self.info["pid"] = None\n'
                    '    t = threading.Thread(target=_child, args=(self.real_bus, child, self._ev, self.batch_s), daemon=True)\n'
                    '    t.start()\n    self._recv_th = threading.Thread(target=self._receive, daemon=True)\n'
                    '    self._recv_th.start()\n    self._proc = None\n\n\nProcWitness.start = _thread_start\n')
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, "proc_witness.py")
        open(path, "w", encoding="utf-8", newline="").write(mut)
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, PROC_WITNESS=path), timeout=300)
        fl = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        # killed: a check FAILed and the test ran to its summary (a crash is not a kill; since 2026-11-19
        # the ProcThread cases' children raise on purpose, so a traceback on stderr is expected)
        dead = bool(fl) and bool(re.search(r"^FAIL, \d+ ok, \d+ FAIL$", r.stdout, re.M))
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fl),
                                                   ("; " + fl[0][:110]) if fl else (r.stderr.strip()[-160:] if r.stderr else "")))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
