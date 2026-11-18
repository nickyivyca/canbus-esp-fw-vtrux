"""An acceptance's hold on the bench, from its first bench part to its last
(ACCEPT-SPEC A18; shared/bench SPEC sections 3, 6 and 8; user, 2026-11-17).

A bench part is a [[suite_run]] whose nested run needs the bench (asked
before the chain, compile.probe; one that cannot be told counts as needing
it). Hand items and flasher runs (A9) would be bench parts too; the runner
runs neither yet, so the parts are suite runs. run.py makes one Hold for
the parts it found, and compile.run_suite runs the chain with
engine_class(), whose run_group asks before() before a bench part's group
starts and tells after() when it has ended.

  - The runner's own hold (no --from-hold): nothing before the first bench
    part holds the lock. At the first part the runner waits for the bench
    as `wait` does (bench_lock.wait, own=True): a request under the
    acceptance runner's own PID, then, when the lock is free and its
    request is the oldest live one, a take for the role with no run under
    that PID, in one locked step. No timeout. A STOP in the chain's run
    folder, or SIGTERM or SIGHUP to the runner, withdraws the request at
    the waiter's next locked step; the bench parts are NOT-RUN, "bench not
    taken: <why>"; after a signal the runner then exits by it, as it would
    have. A log that refuses the request leaves the bench parts NOT-RUN
    with the refusal.
  - Between parts the hold passes through the nested runs: every part is
    given --from-hold PID, every one but the last --keep-lock PID (run.py's
    _keep_lock, nested.py). Before each part after the first, the lock
    must be held by the role, with no run, under PID; otherwise that part
    and every later bench part are NOT-RUN, naming the part that did not
    hand the hold back, and none is started only to fail at the nested
    from-hold refusal. This holds under --from-hold too (the
    charge-interposer reviewer, 2026-11-17).
  - finish(), after the chain and on every way out: a request still queued
    is withdrawn, and a hold that is still the runner's own (role, no run,
    the runner's PID) is released before the record's close, with each
    board's read from the last part's end (the board reads of its nested
    record's release) or "not read: <why>". Under --from-hold the session's
    hold stays with the session, as before A18 (ruling (a), the
    charge-interposer reviewer, 2026-11-17).
  - A pause inside a part is the nested run's own (SPEC section 6): it
    frees the bench, a waiter may take it, and the part resumes under its
    own plain take; its --keep-lock hands the hold back at its end as
    usual. The Hold does nothing at a pause.
  - The record: bench_hold.json and console lines name the request, the
    take that ended the wait, every hand-over and release, with the bench
    log's own lines and times (lines()).

RULES: each is switched off by a mutant in tests/test_accept_hold.py, which
must then fail.
"""

import json
import os
import signal
import threading

from .. import bench_lock as BL

RULES = {k: True for k in ("no_timeout", "stop_withdraws", "signal_withdraws",
                           "refused_not_run", "hold_back_check", "release_left",
                           "release_own_only", "boards_from_last", "record_events")}

NOT_TAKEN = "bench not taken"
SIGNALS = ("SIGTERM", "SIGHUP")
MUTANT_TIMEOUT_S = 1.0          # no_timeout off: the wait gives up after this


class Hold:
    def __init__(self, log, role, parts, from_hold=None, say=print, record_id=None,
                 poll=None):
        """log: the bench log; role: the bench parts' role; parts: their
        suite run ids in chain order; from_hold: the session's PID, or None
        for the runner's own hold."""
        self.log, self.role, self.parts = log, role, list(parts)
        self.own = from_hold is None
        self.pid = os.getpid() if self.own else from_hold
        self.say, self.record_id = say, record_id
        self.poll = poll if poll is not None else BL.WAIT_POLL_S
        self.taken = not self.own       # under --from-hold, held from the start
        self.not_run = None             # why the remaining parts are NOT-RUN
        self.ended = []                 # the parts that ran, in order
        self.took_over = {}             # part: whether its nested run took the hold
        self.took_why = {}              # part: why not
        self.last = None                # {"part", "boards"} or {"part", "why"}
        self.release_line = None
        self.release_error = None
        self.stop = threading.Event()
        self.waiting = False
        self.wait_over = threading.Event()
        self.wait_over.set()
        self.signal = None
        self.old = {}
        self.offset = None              # the log's line count where the hold began
        self.finished = False
        if not self.own:
            self.offset = self._count()

    # ---- the log -------------------------------------------------------
    def _count(self):
        with open(self.log, encoding="utf-8") as f:
            return len(f.read().splitlines())

    def _state(self):
        return BL.read_state(self.log)

    def holds(self, st):
        """The lock held by the role, with no run, under the hold's PID."""
        h = st["holder"]
        return (h is not None and h["who"] == self.role and h["run"] is None
                and h["pid"] == self.pid)

    def lines(self):
        """The log's lines of the hold, from its start: every line of the
        role, and another role's take or release before the hold's last
        line (a waiter's take during a part's pause, SPEC section 3)."""
        if self.offset is None:
            return []
        with open(self.log, encoding="utf-8") as f:
            evs = [json.loads(x) for x in f.read().splitlines()[self.offset:]]
        mine = [i for i, e in enumerate(evs) if e.get("who") == self.role]
        if not mine:
            return []
        end = mine[-1]
        return [e for i, e in enumerate(evs[:end + 1])
                if e.get("who") == self.role or e.get("event") in ("take", "release")]

    # ---- signals: the runner's exit while it waits ---------------------
    def install(self):
        """SIGTERM and SIGHUP while the runner waits stop the wait (its
        request withdrawn), then end the runner by the same signal. Main
        thread only (Python's rule); elsewhere nothing is installed."""
        if not (self.own and RULES["signal_withdraws"]) or \
                threading.current_thread() is not threading.main_thread():
            return
        for name in SIGNALS:
            sig = getattr(signal, name, None)
            if sig is not None:
                self.old[sig] = signal.signal(sig, self._on_signal)

    def uninstall(self):
        for sig, h in self.old.items():
            signal.signal(sig, h)
        self.old = {}

    def _on_signal(self, sig, frame):
        if self.waiting and self.signal is None:
            self.signal = sig
            self.stop.set()
            return
        old = self.old.get(sig, signal.SIG_DFL)
        signal.signal(sig, old if old is not None else signal.SIG_DFL)
        os.kill(os.getpid(), sig)

    # ---- the chain's calls ---------------------------------------------
    def before(self, part, stop_text):
        """Before a bench part's group: None to run it, else why it is
        NOT-RUN."""
        if self.not_run:
            return self.not_run
        first = part == self.parts[0]
        if first and self.own and not self.taken:
            return self._wait(part, stop_text)
        if not first and RULES["hold_back_check"]:
            st = self._state()
            prev = self.ended[-1] if self.ended else self.parts[self.parts.index(part) - 1]
            took = self.took_over.get(prev)
            if not took or not self.holds(st):
                self.not_run = ("the hold was not handed back by suite run %s (%s; the lock: "
                                "%s)" % (prev, "it did not take the hold over: %s"
                                         % self.took_why.get(prev, "it did not run")
                                         if not took else "it did not come back",
                                         BL.holder_text(st)))
                self.say("bench hold: %s; the bench parts from %s on are NOT-RUN"
                         % (self.not_run, part))
                return self.not_run
        return None

    def _wait(self, part, stop_text):
        stopped = (lambda: self.stop.is_set() or (RULES["stop_withdraws"]
                                                  and stop_text() is not None))
        if stop_text() is not None:
            self.not_run = "%s: STOP before the first bench part" % NOT_TAKEN
            return self.not_run
        self.offset = self._count()
        self.say("bench hold: waiting for the bench at suite run %s, as bench_lock wait "
                 "does, under the acceptance runner's pid %d, with no timeout (A18)"
                 % (part, self.pid))
        self.wait_over.clear()
        self.waiting = True
        try:
            rc, outcome, lines = BL.wait(
                self.log, self.role, self.pid,
                "acceptance %s: its bench parts (%s), from %s to %s"
                % (self.record_id, ", ".join(self.parts), self.parts[0], self.parts[-1]),
                poll=self.poll, timeout=None if RULES["no_timeout"] else MUTANT_TIMEOUT_S,
                me=self.pid, stopped=stopped, out=lambda ln: self.say("bench hold: " + ln),
                own=True)
        except BL.Refused as e:
            if not RULES["refused_not_run"]:
                raise
            self.not_run = "%s: the bench log refused the request: %s" % (NOT_TAKEN, e)
            self.say("bench hold: %s" % self.not_run)
            return self.not_run
        finally:
            self.waiting = False
            self.wait_over.set()
        for ln in lines:
            self.say("bench hold: " + ln)
        if rc == 0:
            self.taken = True
            return None
        why = outcome
        if outcome == "stopped":
            why = ("stopped by %s" % signal.Signals(self.signal).name if self.signal
                   else "stopped (STOP: %s)" % stop_text())
        self.not_run = "%s: %s" % (NOT_TAKEN, why)
        self.say("bench hold: %s; the bench parts are NOT-RUN" % self.not_run)
        if self.signal is not None:
            os.kill(os.getpid(), self.signal)       # the runner's exit, as asked
        return self.not_run

    def after(self, part, outdir):
        """A bench part's group has ended: its end's board reads, from its
        nested record, for a release the runner may still have to make."""
        self.ended.append(part)
        end = dict(part_end(part, outdir), part=part)
        self.took_over[part] = bool(end.get("took"))
        if not end.get("took"):
            self.took_why[part] = end.get("why") or "no take"
        if "boards" not in end and self.last and self.last.get("boards"):
            # the part did not take the bench (stopped before it started,
            # say): while the hold is still ours, nothing has touched the
            # boards since the last part's end, so its reads stand
            try:
                if self.holds(self._state()):
                    return
            except BL.Refused:
                pass
        self.last = end

    # ---- the end ---------------------------------------------------------
    def finish(self):
        """After the chain, and on any way out (idempotent): withdraw a
        request still queued, and release a hold that is still the runner's
        own. Returns the record's bench_hold section."""
        if self.finished:
            return self.summary()
        self.finished = True
        if self.own:
            if not self.wait_over.is_set():
                self.stop.set()
                self.wait_over.wait(max(10.0, 5 * self.poll))
            try:
                st = self._state()
                if any(q["who"] == self.role and q.get("pid") == self.pid
                       for q in st["queue"]):
                    _st, line = BL.write(self.log, {
                        "event": "withdraw", "who": self.role,
                        "note": "acceptance %s ended while waiting" % self.record_id})
                    self.say("bench hold: " + line)
            except BL.Refused as e:
                self.say("bench hold: the request could not be withdrawn: %s" % e)
        if RULES["release_left"] and (self.own or not RULES["release_own_only"]):
            try:
                st = self._state()
                if self.holds(st):
                    boards = self.boards()
                    _st, line = BL.release(
                        self.log, self.role, boards,
                        note="acceptance %s: its hold, left at its end, released before "
                        "its close (A18)" % self.record_id)
                    self.release_line = line
                    self.say("bench hold: released before the close: " + line)
            except BL.Refused as e:
                self.release_error = str(e)
                self.say("bench hold: LOCK NOT RELEASED: %s" % e)
        return self.summary()

    def boards(self):
        """Each board's read from the last part's end, or not read: why."""
        last = self.last
        if RULES["boards_from_last"] and last and last.get("boards"):
            return dict(last["boards"])
        if last is None:
            why = "no bench part ran under the hold"
        elif last.get("why"):
            why = last["why"]
        else:
            why = "suite run %s left no board reads" % last["part"]
        return {b: "%s: %s" % (BL.NOT_READ, why) for b in BL.BOARDS}

    def summary(self):
        out = {"mode": "the runner's own" if self.own else "--from-hold",
               "pid": self.pid, "role": self.role, "parts": self.parts,
               "not_run": self.not_run, "ended": self.ended,
               "release_before_close": self.release_line,
               "release_error": self.release_error}
        if RULES["record_events"]:
            try:
                out["lines"] = self.lines()
            except (OSError, ValueError) as e:
                out["lines"] = None
                out["lines_error"] = repr(e)
        return out

    # ---- the chain runner with the hold ----------------------------------
    def engine_class(self, base):
        hold = self
        from .compile import group_name
        groups = {group_name(p): p for p in self.parts}

        class HoldEngine(base):
            def run_group(self, g, items, carried):
                part = groups.get(g.name)
                why = hold.before(part, self.stop_text) if part is not None else None
                if why:
                    for c in items:
                        c.skip = why
                try:
                    return super().run_group(g, items, carried)
                finally:
                    if part is not None and not why:
                        hold.after(part, os.path.join(self.run_dir, g.name, "run"))
        return HoldEngine


def part_end(part, outdir):
    """{"took": True, "boards": {...}} from the release of the last nested
    run of the part that took the lock; {"took": True, "why": ...} when it
    took the lock and has no board reads to give (lost, or its release did
    not go through); {"why": ...} when it never took the hold over."""
    try:
        with open(os.path.join(outdir, "nested.json"), encoding="utf-8") as f:
            n = json.load(f)
    except (OSError, ValueError) as e:
        return {"why": "suite run %s left no nested record (%s)" % (
            part, "it did not start or did not take over the hold"
            if isinstance(e, FileNotFoundError) else repr(e))}
    lock = None
    for k in ("rerun", "first"):
        r = n.get(k) if isinstance(n, dict) else None
        if not isinstance(r, dict) or not r.get("dir"):
            continue
        try:
            with open(os.path.join(r["dir"], "record.json"), encoding="utf-8") as f:
                cand = (json.load(f).get("lock") or {})
        except (OSError, ValueError, AttributeError):
            continue
        if cand.get("took"):
            lock = cand
            break
    if lock is None:
        return {"why": "suite run %s's nested record shows no take" % part}
    if lock.get("lost"):
        return {"took": True, "why": "suite run %s lost the lock (%s)" % (part, lock["lost"])}
    rel = lock.get("release") or {}
    if not rel.get("ok") or not isinstance(rel.get("boards"), dict):
        return {"took": True, "why": "suite run %s's release did not go through (%s)" % (
            part, rel.get("error") or "none recorded")}
    return {"took": True, "boards": rel["boards"]}
