"""The chain runner: executes a Suite.

Lanes run in parallel threads: "bench" (every group that needs the bench,
one after another, then the restore) and one "logic:<host>" lane per host
for the rest. Within a lane, groups and their checks run in order.

The bench lock has two sources, chosen by the settings (reverse-it
firmware/bench-lock-spec.md, sections 6 and 7):
  - bench_lock_log unset (before the cutover): the Holder line of
    bench-holder.md (settings bench_holder) is the lock (bench-holder rule
    2, user 2026-10-17). The tester takes it and messages the other tester
    before starting, and releases it after; the runner never edits the
    file. A pause is manual (user 2026-10-22): the PAUSE file stops the run
    at a case boundary and the role edits the Holder line by hand; the
    paused run waits until the PAUSE file is gone and the Holder is its
    role's again. Between cases with no pause under way, a Holder that is
    not its role's is a lost lock.
  - bench_lock_log set: the event log of vtrux_bench.bench_lock is the
    lock. The runner forms its run id, takes the lock under its own PID and
    that id, and only then makes the run folder (section 8). It goes on
    only while the lock is held by its role, its run id and its PID. At a
    case boundary with a PAUSE file it identifies its boards, runs the end
    hooks, writes free_paused with those reads and waits, opening nothing;
    once the file is gone it takes the lock back under its PID and run id,
    waiting while another role holds it, then runs the start hooks again
    and re-identifies (flashing back) every board its remaining cases use
    before the next case (section 6). At its run's end it releases with
    its board reads, or with
    --keep-lock PID releases and takes again for its role, with no run,
    under PID, in one locked step.
In both, the lock is checked before every bench case, a case in progress
always runs to its end, and any state but the run's own lock is lost: the
record and index.txt open with "LOCK LOST: <state>", every remaining case is
NOT-RUN, the end restore and the end hooks' board actions are skipped, and
nothing is released (the log gets `lost`, unless it does not replay). Board
changes during the run are in the run's own log (progress.jsonl,
record.json, board_state.txt).

Rules the engine enforces (each has a test in tests/test_run_core.py that
fails when the rule is switched off through RULES):
  lock_guard      no bench group runs unless the suite's role holds the
                  lock: refused at the start, checked at each group's start
  lock_each_case  the lock is checked again before every bench case, not
                  only at its group's start, in both sources (section 6)
  pause_own_lock  a pause ends only on the run's own lock: the Holder line
                  its role's again, or the runner's own take of its paused
                  run; never on the PAUSE file alone (sections 6, 7)
  lost_record     a lost lock appends `lost` to the log (unless the log does
                  not replay) and opens the record and index.txt with
                  "LOCK LOST: <state>"
  take_first      (log) the lock is taken under the runner's PID and run id
                  before the run folder exists: a refused take leaves none
  folder_release  (log) a take whose run folder then cannot be made is
                  released ("not read: run folder not created") before the
                  runner raises: no lock is left under an exited runner
  self_pause      (log) a PAUSE file makes the runner write free_paused with
                  its board reads at the next case boundary, then wait
  release_end     (log) at the run's end the runner releases with its board
                  reads, or "not read: <why>"; never after a LOCK LOST
  keep_lock_one_step  (log) --keep-lock PID appends the release and the hand
                  take under PID in one locked step, nothing in between
  own_pid         (log) the run goes on only while the lock is held under
                  its runner's own PID: a hand take --run <id> under a
                  session's PID is a lost lock (section 6)
  pause_hooks     (log) at a free_paused the end hooks run as at the end
                  release (minus the restore), and their reads go in it
  resume_identify (log) after the resume take, before the next case, the
                  start hooks run again and every board the remaining cases
                  use is identified against its expected image and flashed
                  back if it differs
  resume_stop     (log) a board the resume cannot put back is a stop, not a
                  lost lock (user, Q6 (i)): the lock stays the run's, the
                  rest is NOT-RUN, the record and index.txt open with
                  "STOPPED: <board> is <read>, expected <image>, cannot
                  flash", and the end release carries the identities read
  resume_stop_kept  (log) the end restore skips the board the resume could
                  not put back, which is left as found for the
                  investigation; its restore note is "restore skipped:
                  <STOPPED line>". Other boards restore as normal and the
                  end hooks still run
  stop_no_resume  (log) STOP during a pause: the runner takes the lock back
                  for the restore and the release, but runs no start hooks
                  and re-identifies or flashes nothing before the lane ends
  flash_gate      a failed flash (or failed auto-reset check) stops what
                  needs that image; no retry; after a failed auto-reset
                  check no further flash at all
  image_schedule  flash only when the board is not already on the image
                  (also in the start pass below)
  start_identify  at the start of the bench lane, before the first group,
                  every board the run uses (test gear included) is
                  identified against the first image the run needs on it,
                  flashed if it reads anything else, and identified again
                  (user 2026-10-20: correctness rests on this, not on what
                  the last run left)
  restore_after_stop  the end-of-run restore is one best-effort attempt
                  per board, also after a flash or identify failure stopped
                  the bench; only the flash gate (a failed auto-reset
                  check), a lost lock or STOP norestore prevent it
  standing_line   a board the run used that ends off its standing image
                  opens board_state.txt with "NOT ON STANDING IMAGE: ..."
                  (also in record.json and index.txt)
  identify_after  a group whose board image changed under it is VOID
  no_verdict      a check that returns no verdict is an ERROR
  known_fail      registry FAILs become KNOWN-FAIL; a registry PASS is
                  flagged; registry entries never produced are listed
  stop_pause      STOP ends the lane (the rest NOT-RUN), PAUSE waits; a
                  pause under way ends on the run's own lock even with STOP
  throttle        build-class checks run throttled while a wall-clock test
                  runs on their host
  redact_scope    the redaction hook sees only declared outputs
  gate            a gate check that does not PASS makes the rest of its
                  group NOT-RUN
  timeout         a check past its timeout is killed (whole tree), ERROR
  skip            a check with a skip reason is recorded NOT-RUN with it,
                  and never executed
  after           a group with `after` waits for those groups, and with
                  after_must_pass runs only if they all passed
  wallclock_guard a wall-clock check starts only when no other wall-clock
                  test runs on its host (another role's lock, a matching
                  process that is not this run's own, or another of this
                  run's wall-clock checks): one long simulation at a time
  requires        a suite with `requires` starts only when a run of that
                  suite on the same code matched its reference
  product_digest  "the same code" (Suite.requires, --resume) is the product
                  digest over Suite.product_files when the suite gives them,
                  not the commit: a harness-only commit keeps it (user
                  2026-10-20). The harness identity is recorded, not gated
  digest_bridge   a record with no product digest (older runs) is given
                  the digest of its commit's tree, from git, if that tree
                  was clean
  requires_whole  the run Suite.requires accepts must have selected the
                  whole suite, with no NOT-RUN but declared skips
  subset_mark     a run with a --group or --check selection is marked a
                  subset run in its record (subset: true, and subset_cases:
                  the ids of the cases selected); a whole run, subset: false
  subset_not_whole  requires_whole refuses a run marked subset, or one
                  whose selection is not empty, whatever the other says
                  (user 2026-10-20: a subset run never satisfies a
                  whole-run requirement)
  resume_code     --resume refuses a run of another suite or other code
  identify_error  an identify hook that fails (raises) stops the bench; it
                  never leads to a flash
  restore         at the end, every board the run flashed is put back on
                  its standing image (unless a flash failure stopped the
                  bench, or STOP says norestore)
  board_hooks     Suite.on_start runs for every board the bench lane uses
                  before the start pass touches it, and Suite.on_end for
                  every one after the restore (straight after a lane
                  crash, which skips the restore), on every run however it
                  ended (not after the lock is lost); both are recorded
                  (user 2026-10-20: the WiCAN is rebooted at the start and
                  at each release, so a killed run cannot leave it armed)
  start_hook_stops  an on_start that fails (or raises) stops the bench
                  before anything is identified or flashed: every group
                  that needs the lock is NOT-RUN
  release_line    an on_end that fails (or raises) opens board_state.txt,
                  index.txt and the record with "RELEASE NOT CONFIRMED: ..."
  end_crash_boards  if the end hooks themselves crash, every board on_end
                  was owed to (the lane's, and every board the run touched)
                  is recorded as a failed on_end
  carry_check_end --resume carries a check only if its check-end event
                  (written after all its results) is in progress.jsonl: a
                  check killed between two results runs again
  carry_finished  --resume carries a check of a group that uses a board only
                  if the group's group-end event (written after
                  identify-after and its amends) is there too: a board group
                  killed mid-way carries nothing; a board-less one keeps its
                  finished checks (user 2026-10-20, option A refined)
"""

import fnmatch
import glob
import hashlib
import io
import json
import os
import socket
import subprocess
import tarfile
import threading
import time
import traceback

from .. import bench_lock as BL
from . import lock as lockmod
from . import procs
from .flash import BUILTIN as BUILTIN_FLASHERS
from .model import Check, CheckRun, Result, SuiteError, Verdict, validate
from .record import (Recorder, compare, load_progress, make_run_id,
                     new_run_dir, stamp, write_record, write_new)

RULES = {k: True for k in (
    "lock_guard", "lock_each_case", "pause_own_lock", "lost_record",
    "take_first", "folder_release", "self_pause", "release_end",
    "keep_lock_one_step",
    "own_pid", "pause_hooks", "resume_identify", "resume_stop",
    "resume_stop_kept", "stop_no_resume",
    "flash_gate", "image_schedule", "identify_after",
    "no_verdict", "known_fail", "stop_pause", "throttle", "redact_scope",
    "gate", "timeout", "skip", "restore", "identify_error", "after",
    "wallclock_guard", "requires", "requires_whole", "resume_code",
    "subset_mark", "subset_not_whole", "start_identify", "restore_after_stop",
    "standing_line", "product_digest", "digest_bridge", "board_hooks",
    "start_hook_stops", "release_line", "end_crash_boards", "carry_finished",
    "carry_check_end")}

IDENTIFY_ERROR = "<identify failed>"


def read_text(r):
    """An identify read as words, for the STOPPED line."""
    if r == IDENTIFY_ERROR:
        return "unidentified (the identify failed)"
    return "on no image identify knows" if r is None else r


SHIP_EXCLUDE = {"__pycache__", ".pio", ".git", "build"}
SSH = ("ssh", "-o", "BatchMode=yes")


class _Strict(dict):
    def __missing__(self, k):
        raise SuiteError("no setting {%s} for formatting" % k)


def fmt(s, ctx):
    if not isinstance(s, str):
        return s
    return s.format_map(_Strict(ctx))


def fmt_any(v, ctx):
    if isinstance(v, str):
        return fmt(v, ctx)
    if isinstance(v, list):
        return [fmt_any(x, ctx) for x in v]
    if isinstance(v, dict):
        return {k: fmt_any(x, ctx) for k, x in v.items()}
    return v


class Engine:
    def __init__(self, suite, settings, runs_root=None, run_id=None,
                 groups=None, checks=None, resume=None, out=print,
                 keep_lock=None):
        self.suite = suite
        self.settings = settings
        self.out = out
        self.flashers = dict(BUILTIN_FLASHERS)
        self.flashers.update(suite.flashers)
        validate(suite, settings, self.flashers)
        self.runs_root = runs_root or settings.get("runs_root")
        self.run_id_req = run_id
        self.sel_groups = list(groups or [])
        self.sel_checks = list(checks or [])
        self.resume = resume
        self.bench_holder = settings.get("bench_holder")
        # The lock's source (sections 6, 7): the log when the settings name
        # it, else the Holder line. took: this run holds a take in the log.
        self.lock_log = settings.get("bench_lock_log")
        self.keep_lock = keep_lock
        self.took = False
        self.lock_lost = None
        self.resume_stopped = None
        self.resume_stopped_board = None
        self.lock_release = None
        self.bench_work = []
        self.kf = {k.id: k.ref for k in suite.known_fails}
        self._wc_lock = threading.Lock()
        self.active_wallclock = {}
        # Per board (Image.board): what it is on, what it last read, whether
        # this run flashed it, and boards whose auto-reset check failed.
        self.board_image = {}
        self.board_read = {}
        self.board_read_ts = {}
        self.hook_ts = {}
        self.board_touched = set()
        self.restore_note = {}
        self.hook_out = {"on_start": {}, "on_end": {}}
        self.requires_note = None
        self._bridged = {}
        self.no_more_flash = set()
        self.no_flash_why = {}
        self.bench_stopped = None
        self.failed_images = set()
        self.executed_checks = set()
        self.groups_out = []
        self.shipped = set()
        self._ship_lock = threading.Lock()
        self.group_done = {}
        self.images_used = {}
        names = [x.name for x in suite.groups]
        for g in self.sel_groups:
            if g not in names:
                raise SuiteError("no group %r in suite %s" % (g, suite.name))

    # ---- selection ------------------------------------------------------
    def selected(self):
        """[(group, [checks])] after --group / --check."""
        out = []
        for g in self.suite.groups:
            if self.sel_groups and g.name not in self.sel_groups:
                continue
            items = [c for c in g.checks
                     if not self.sel_checks or any(
                         fnmatch.fnmatchcase("%s/%s" % (g.name, c.id), p)
                         for p in self.sel_checks)]
            if items:
                out.append((g, items))
        return out

    def needs_bench(self):
        return any(g.needs_lock for g, _ in self.selected())

    def _carried(self):
        if not self.resume:
            return {}
        by = {}
        prog = load_progress(self.resume)
        # Only what finished (user 2026-10-20, option A with the per-check
        # refinement): a check needs its check-end (all its results written);
        # a check in a group that uses a board also needs the group's
        # group-end (written after identify-after and amends). A board-less
        # group killed mid-way keeps its finished checks. A run from before
        # these events carries nothing.
        ev = lambda kind, key: {e.get(key) for e in prog          # noqa: E731
                                if e.get("type") == "event" and e.get("kind") == kind}
        groups_ended, checks_ended = ev("group-end", "group"), ev("check-end", "check")
        board_groups = {g.name for g in self.suite.groups if g.image_list}
        for e in prog:
            if e.get("type") == "result" and (
                    (RULES["carry_check_end"] and e.get("check") not in checks_ended)
                    or (RULES["carry_finished"] and e.get("group") in board_groups
                        and e.get("group") not in groups_ended)):
                continue
            if e.get("type") == "result":
                by.setdefault(e["check"], []).append(e)
            elif e.get("type") == "amend":
                for r in by.get(e["check"], []):
                    if r["id"] == e["id"]:
                        r["verdict"] = e["verdict"]
                        r["detail"] = e["detail"]
        prev = os.path.basename(os.path.normpath(self.resume))
        return {cid: (prev, rs) for cid, rs in by.items()
                if rs and all(r["verdict"] in Verdict.TERMINAL for r in rs)}

    # ---- context --------------------------------------------------------
    def base_ctx(self):
        ctx = {k: v for k, v in self.settings.items() if isinstance(v, str)}
        ctx.update(run_dir=self.run_dir, run_id=self.run_id,
                   suite=self.suite.name)
        return ctx

    def host_cfg(self, host):
        return (self.settings.get("hosts") or {}).get(host) or {}

    def remote_work(self, host):
        root = self.host_cfg(host).get("workroot")
        if not root:
            raise SuiteError("hosts.%s.workroot is not set" % host)
        return "%s/%s" % (root.rstrip("/"), self.run_id)

    # ---- STOP / PAUSE ---------------------------------------------------
    def stop_text(self):
        p = os.path.join(self.run_dir, "STOP")
        if not RULES["stop_pause"] or not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read().strip() or "STOP"
        except OSError:
            return "STOP"

    def wait_pause(self):
        if not RULES["stop_pause"]:
            return
        p = os.path.join(self.run_dir, "PAUSE")
        logged = False
        while os.path.exists(p) and self.stop_text() is None:
            if not logged:
                self.rec.event("pause", note="PAUSE file present; waiting")
                logged = True
            time.sleep(float(self.settings.get("pause_poll_s", 10)))
        if logged:
            self.rec.event("resume", note="PAUSE removed")

    # ---- lock and throttle ----------------------------------------------
    def lock_ours(self):
        """(ok, holder text). Ours = the suite's role holds the lock; under
        the log, its role and this run's id, not paused (section 6)."""
        if not RULES["lock_guard"]:
            return True, "rule off"
        if self.lock_log:
            st, why = self._log_state()
            if st is None:
                return False, "the bench log does not replay: %s" % why
            return self.runner_holds(st), self._log_text(st)
        if not self.bench_holder:
            return False, "settings.bench_holder is not set"
        try:
            h = lockmod.holder(self.bench_holder)
        except (OSError, lockmod.LockError) as e:
            return False, "bench-holder unreadable: %s" % e
        return lockmod.held_by(h, self.suite.role), h

    def _log_state(self):
        """(state, None), or (None, why) when the log does not replay."""
        try:
            return BL.read_state(self.lock_log), None
        except BL.Refused as e:
            return None, str(e)

    @staticmethod
    def _log_text(st):
        out = BL.holder_text(st)
        for who, p in sorted(st["paused"].items()):
            out += "; run %s of %s paused since %s" % (p["run"], who, p["ts"])
        return out

    def _other_holder(self):
        """The holder's text when another role holds the bench lock, else
        None. Raises if the lock cannot be read."""
        if self.lock_log:
            st = BL.read_state(self.lock_log)
            h = st["holder"]
            if h is not None and h["who"] != self.suite.role:
                return BL.holder_text(st)
            return None
        h = lockmod.holder(self.bench_holder)
        if not lockmod.is_free(h) and not lockmod.held_by(h, self.suite.role):
            return h
        return None

    def _pause_path(self):
        return os.path.join(self.run_dir, "PAUSE")

    def _poll(self):
        time.sleep(float(self.settings.get("pause_poll_s", 10)))

    def case_gate(self):
        """Before a bench case or group (section 6; the lock-gap fix in both
        sources). None to go on, after any pause it waited through; else the
        LOCK LOST line, the run having lost the lock (lost())."""
        if self.lock_lost:
            return self.lock_lost
        if self.lock_log:
            st, why = self._log_state()
            if st is None:
                return self.lost("the bench log does not replay: %s" % why,
                                 append=False)
            if RULES["lock_guard"] and not self.runner_holds(st):
                return self.lost(self._log_text(st))
            if RULES["stop_pause"] and os.path.exists(self._pause_path()):
                if RULES["self_pause"]:
                    return self.pause_log()
                self.wait_pause()
            return None
        if RULES["stop_pause"] and os.path.exists(self._pause_path()):
            # a pause under way (manual before the cutover, section 7): the
            # Holder may already be another role's, which is not a loss
            self.pause_holder()
        ok, h = self.lock_ours()
        if not ok:
            return self.lost("not held by %s: %s" % (self.suite.role, h))
        return None

    def pause_holder(self):
        """Before the cutover (section 7): wait until the PAUSE file is gone
        (or STOP asks to end) and the Holder is this role's again."""
        self.rec.event("pause", note="PAUSE file present; waiting until it is "
                       "removed and the Holder is %s" % self.suite.role)
        while True:
            if not os.path.exists(self._pause_path()) or \
                    self.stop_text() is not None:
                if not RULES["pause_own_lock"]:
                    break
                ok, _h = self.lock_ours()
                if ok:
                    break
            self._poll()
        self.rec.event("resume", note="PAUSE removed and the lock is ours")

    def pause_log(self):
        """Section 6: free the bench with free_paused and this run's board
        reads, wait (opening nothing) until the PAUSE file is gone or STOP
        asks to end, then take the lock back under this runner's PID and run
        id, waiting while another role holds it. None once the lock is ours
        again; the LOCK LOST line if the run cannot get it back."""
        boards = self.pause_reads()
        try:
            _st, line = BL.free_paused(self.lock_log, self.suite.role,
                                       self.run_id, boards,
                                       note="PAUSE file in the run folder")
        except BL.Refused as e:
            return self.lost("free_paused refused: %s" % e)
        self.rec.event("free-paused", line=line, boards=boards)
        while os.path.exists(self._pause_path()) and self.stop_text() is None:
            self._poll()
        if not RULES["pause_own_lock"]:
            self.rec.event("resume", note="PAUSE removed")
            return None
        waiting = None
        while True:
            try:
                _st, line = BL.take(self.lock_log, self.suite.role, os.getpid(),
                                    run=self.run_id, note="resume")
                self.rec.event("resume", line=line)
                if RULES["stop_no_resume"] and self.stop_text() is not None:
                    # the take is for the restore and the release; STOP
                    # ends the lane before any case, so no hooks or flashes
                    self.rec.event("resume-skipped", note="STOP during the "
                                   "pause: no start hooks, no re-identify")
                    return None
                return self.resume_check()
            except BL.Refused as e:
                st, why = self._log_state()
                if st is None:
                    return self.lost("the bench log does not replay: %s" % why,
                                     append=False)
                mine = st["paused"].get(self.suite.role)
                if not mine or mine["run"] != self.run_id:
                    return self.lost("run %s is no longer paused: %s"
                                     % (self.run_id, self._log_text(st)))
                if waiting != str(e):
                    waiting = str(e)
                    self.rec.event("resume-waiting", reason=waiting)
            self._poll()

    def lost(self, state, append=True):
        """The run lost the lock (section 6): every remaining case NOT-RUN,
        no end restore, no end-hook board actions, no release. Appends
        `lost` to the log unless the log does not replay. Returns the LOCK
        LOST line."""
        if self.lock_lost:
            return self.lock_lost
        self.lock_lost = "LOCK LOST: %s" % state
        self.bench_stopped = self.lock_lost
        self.rec.event("lock-lost", state=state)
        if self.lock_log and append and self.took and RULES["lost_record"]:
            try:
                _st, line = BL.lost(self.lock_log, self.suite.role,
                                    self.run_id, note=state[:300])
                self.rec.event("lost-appended", line=line)
            except BL.Refused as e:
                self.rec.event("lost-refused", error=str(e))
        return self.lock_lost

    def runner_holds(self, st):
        """Section 6: held by this suite's role, this run's id and this
        runner's PID, not paused. A hand take --run <id> under a session's
        PID is not the runner's lock."""
        if not BL.held_by_run(st, self.suite.role,
                              getattr(self, "run_id", None)):
            return False
        return not RULES["own_pid"] or st["holder"]["pid"] == os.getpid()

    def pause_reads(self):
        """free_paused's board reads (section 6): what the end release does,
        minus the restore. Each board this run has an image for is
        identified, and the end hooks run for every board the lane uses;
        each read is that identify or hook read, or "not read: <why>"."""
        work = self.bench_work
        first = self._first_images(work)
        logdir = os.path.join(self.run_dir, "_bench", "pause")
        os.makedirs(logdir, exist_ok=True)
        for board, name in first.items():
            name = self.board_image.get(board) or name
            self.identify(self.suite.images[name], logdir)
        hooks = {}
        if (self.suite.on_end is not None and RULES["board_hooks"]
                and RULES["pause_hooks"]):
            boards = list(first) + sorted(self.board_touched - set(first))
            for board in boards:
                name = self.board_image.get(board)
                hooks[board] = self._call_hook(
                    "on_end", board, self.suite.images.get(name) if name
                    else None, logdir, record=False)
            self.rec.event("pause-end-hooks", hooks=hooks)
        return self.board_reads(hooks=hooks, why_unread="paused between "
                                "cases")

    def _expected_images(self):
        """{board: image} the remaining cases use: the current group's
        image on each of its boards, then the first image a later group
        needs on any other board."""
        names = [g.name for g, _items in self.bench_work]
        cur = getattr(self, "current_group", None)
        start = names.index(cur) if cur in names else 0
        return self._first_images(self.bench_work[start:])

    def resume_check(self):
        """Section 6, after the resume take and before the next case: the
        start hooks again, then every board the remaining cases use is
        identified against the image expected on it, as at run start, and
        flashed and identified again if it differs. A board it cannot put
        right is a stop, not a lost lock (user, Q6 (i)): the lock stays the
        run's, the rest is NOT-RUN, the record opens with the STOPPED line
        and the end release carries the identities read. None to go on,
        else the LOCK LOST line."""
        t0 = time.monotonic()
        started = stamp()
        logdir = os.path.join(self.run_dir, "_bench", "resume")
        os.makedirs(logdir, exist_ok=True)
        expected = self._expected_images()
        statuses = []
        if (self.suite.on_start is not None and RULES["board_hooks"]):
            hooks = {}
            for board, name in expected.items():
                out = self._call_hook("on_start", board,
                                      self.suite.images[name], logdir,
                                      record=False)
                hooks[board] = out
                statuses.append("%s: %s%s" % (board, "" if out["ok"] else
                                              "START HOOK FAILED: ",
                                              out["detail"]))
                if not out["ok"] and RULES["start_hook_stops"]:
                    self.bench_stopped = "on_start for %s failed at the " \
                        "resume: %s" % (board, out["detail"])
                    break
            self.rec.event("resume-start-hooks", hooks=hooks)
        lost = None
        if RULES["resume_identify"] and not self.bench_stopped:
            for board, name in expected.items():
                ok, why = self.ensure_image(name, logdir)
                statuses.append(("" if ok else "RESUME FAILED: ") + why)
                if not ok and RULES["resume_stop"]:
                    im = self.suite.images[name]
                    self.resume_stopped = (
                        "STOPPED: %s is %s, expected %s, cannot flash"
                        % (board, read_text(self.board_read.get(board)),
                           im.expect))
                    self.resume_stopped_board = board
                    self.bench_stopped = "%s (%s)" % (self.resume_stopped, why)
                    self.rec.event("resume-stopped", board=board, image=name,
                                   why=why, line=self.resume_stopped)
                    break
                if not ok:
                    lost = self.lost("at the resume, %s could not be put back "
                                     "on %s: %s" % (board, name, why))
                    break
        reads = {b: self.board_read.get(b) for b in expected}
        status = "; ".join(statuses)
        self.rec.event("resume-identify", status=status, identities=reads)
        self.groups_out.append(dict(
            name="resume", lane="bench", host="local", needs_lock=True,
            image=list(expected.values()), started=started, ended=stamp(),
            wall_s=round(time.monotonic() - t0, 1), status=status, checks=0))
        return lost

    def board_reads(self, hooks=None, why_unread=None):
        """{board: TEXT} for each of bench_lock.BOARDS (section 8): what
        this run read off the wire and when, or "not read: <why>". hooks:
        {board: end-hook result with its time "t"}. An end hook that failed
        leaves its board not read; an identify that failed or recognised
        nothing counts as no read."""
        out = {}
        rid = self.run_id
        hooks = hooks or {}
        for name in BL.BOARDS:
            hook = hooks.get(name)
            if hook is not None and not hook.get("ok"):
                out[name] = "not read: the end hook failed (%s); run %s" % (
                    hook.get("detail"), rid)
                continue
            parts, unread = [], None
            if name in self.board_read:
                r, ts = self.board_read[name], self.board_read_ts.get(name)
                if r == IDENTIFY_ERROR:
                    unread = "identify failed at %s" % ts
                elif r is None:
                    unread = "identify at %s recognised no image it knows" % ts
                else:
                    parts.append("%s, identified off the wire %s" % (r, ts))
            if hook is not None:
                parts.append("end hook %s: %s" % (
                    hook.get("t"), hook.get("detail") or "ok"))
            if parts:
                out[name] = "; ".join(parts) + "; run %s" % rid
            else:
                out[name] = "not read: %s; run %s" % (
                    unread or why_unread or "run has no image or end hook for "
                    "the %s" % name, rid)
        return out

    def release_end(self):
        """At the run's end, after the restore and the end hooks (section
        6): release with the board reads, or with --keep-lock PID release
        and take for this role under PID in one step. Nothing after a LOCK
        LOST."""
        if not (self.lock_log and self.took and RULES["release_end"]) or \
                self.lock_lost:
            return
        boards = self.board_reads(hooks={
            b: dict(o, t=self.hook_ts.get(("on_end", b)))
            for b, o in self.hook_out["on_end"].items()})
        note = "end of run %s" % self.run_id
        try:
            if self.keep_lock is None:
                _st, line = BL.release(self.lock_log, self.suite.role, boards,
                                       note=note)
                lines = [line]
            elif RULES["keep_lock_one_step"]:
                _st, lines = BL.release_and_take(
                    self.lock_log, self.suite.role, boards, self.keep_lock,
                    note=note)
            else:
                lines = [BL.release(self.lock_log, self.suite.role, boards,
                                    note=note)[1],
                         BL.take(self.lock_log, self.suite.role,
                                 self.keep_lock, note=note)[1]]
            self.lock_release = dict(ok=True, lines=lines, boards=boards)
            self.rec.event("lock-release", lines=lines)
        except BL.Refused as e:
            self.lock_release = dict(ok=False, error=str(e), boards=boards)
            self.rec.event("lock-release-refused", error=str(e))

    def lock_lines(self):
        """The lines that open the record, index.txt and board_state.txt
        about the lock."""
        out = []
        if self.resume_stopped:
            out.append(self.resume_stopped)
        if self.lock_lost and RULES["lost_record"]:
            out.append(self.lock_lost)
        r = self.lock_release
        if r is not None and not r["ok"]:
            out.append("LOCK NOT RELEASED: %s" % r["error"])
        return out

    def _take_at_start(self):
        """Section 8 under the log: the run id first, then the take under
        this runner's PID and that id, then the folder. A refused take
        means no folder and no run."""
        if self.keep_lock is not None:
            kp = self.keep_lock
            if not isinstance(kp, int) or isinstance(kp, bool) or kp <= 0 \
                    or kp == os.getpid():
                raise lockmod.LockError(
                    "refusing to start: --keep-lock %r is not the long-lived "
                    "process of the session taking over (never the runner's "
                    "own)" % (kp,))
            try:
                BL.check_pid_alive(BL.read_state(self.lock_log), kp)
            except BL.Refused as e:
                raise lockmod.LockError("refusing to start: --keep-lock: %s"
                                        % e)
        rid = make_run_id(self.suite.name, self.run_id_req)
        if os.path.exists(os.path.join(self.runs_root, rid)):
            raise FileExistsError(os.path.join(self.runs_root, rid))
        if not RULES["take_first"]:
            self.run_id, self.run_dir = new_run_dir(self.runs_root,
                                                    self.suite.name, rid)
        try:
            BL.take(self.lock_log, self.suite.role, os.getpid(), run=rid,
                    note="run of suite %s" % self.suite.name)
        except BL.Refused as e:
            raise lockmod.LockError(
                "refusing to start: the bench log refused the take for run "
                "%s: %s" % (rid, e))
        self.took = True
        if RULES["take_first"]:
            try:
                self.run_id, self.run_dir = new_run_dir(self.runs_root,
                                                        self.suite.name, rid)
            except Exception as e:
                if RULES["folder_release"]:
                    self._release_unstarted(rid, e)
                raise

    def _release_unstarted(self, rid, err):
        """The take succeeded but the run folder could not be made: release
        before raising, so a runner that exits leaves no lock held."""
        why = "not read: run folder not created (%s); run %s" % (err, rid)
        try:
            BL.release(self.lock_log, self.suite.role,
                       {b: why for b in BL.BOARDS},
                       note="run %s never started" % rid)
        except BL.Refused as e:
            raise lockmod.LockError(
                "run folder for %s not created (%s), and the release was "
                "refused: %s" % (rid, err, e))
        self.took = False

    def wallclock_running(self, host):
        with self._wc_lock:
            if self.active_wallclock.get(host, 0) > 0:
                return True, "this run has a wall-clock check on %s" % host
        if host == "local" and (self.bench_holder or self.lock_log):
            # Another role's lock means its wall-clock test may be running.
            # Under this suite's own role, nothing of the other project can
            # run, so this run's own wall-clock checks (above) and the
            # process scan (below) decide.
            try:
                h = self._other_holder()
                if h:
                    return True, "bench lock held by another role: %s" % h[:80]
            except Exception as e:
                return True, "bench lock unreadable (%s): assume running" % e
        hit = self.other_wallclock_process(host)
        if hit:
            return True, hit
        return False, "no wall-clock test on %s" % host

    def other_wallclock_process(self, host):
        """A matching process on the host that is not this runner, its
        ancestors or anything it started (locally by process tree; on a
        remote host, by this run's id in the command line). None if none."""
        pats = self.settings.get("wallclock_patterns") or []
        if not pats:
            return None
        ssh_host = None if host == "local" else self.host_cfg(host).get("ssh")
        try:
            table = procs.process_table(ssh_host)
        except Exception as e:
            return "process list failed (%s): assume running" % e
        if host == "local":
            mine = procs.own_tree(table, os.getpid())
            lines = [a for p, _pp, a in table if p not in mine]
        else:
            rid = getattr(self, "run_id", None) or "\0"
            lines = [a for _p, _pp, a in table if rid not in a]
        hits = procs.matching(lines, pats)
        return ("process: %s" % hits[0][:100]) if hits else None

    def other_wallclock(self, host):
        """Is another wall-clock test running on `host`? (busy, why)"""
        with self._wc_lock:
            if self.active_wallclock.get(host, 0) > 0:
                return True, ("this run already has a wall-clock check on %s"
                              % host)
        if host == "local" and (self.bench_holder or self.lock_log):
            try:
                h = self._other_holder()
                if h:
                    return True, "bench lock held by another role: %s" % h[:80]
            except Exception as e:
                return True, "bench lock unreadable (%s)" % e
        hit = self.other_wallclock_process(host)
        if hit:
            return True, hit
        return False, None

    # ---- images ---------------------------------------------------------
    def identify(self, image, logdir):
        """The board's identity, or None if it is on no image identify
        knows. An identify hook that RAISES could not look, which is not
        the same as "not this image": it returns IDENTIFY_ERROR, and the
        caller must not flash on it."""
        try:
            got = self.suite.identify(image, self.base_ctx(), logdir)
        except Exception as e:
            got = IDENTIFY_ERROR
            self.rec.event("identify-error", board=image.board,
                           image=image.name, error=repr(e))
        self.board_read[image.board] = got
        self.board_read_ts[image.board] = stamp()
        self.rec.event("identify", board=image.board, expected_image=image.name,
                       expected=image.expect, read=got)
        return got

    def _flash_failed(self, name, why, recoverable=True):
        if RULES["flash_gate"]:
            if recoverable is False:
                self.no_more_flash.add(self.suite.images[name].board)
                self.no_flash_why.setdefault(self.suite.images[name].board, why)
            self.failed_images.add(name)
            if self.suite.on_flash_failure == "stop-bench":
                self.bench_stopped = why

    def ensure_image(self, name, logdir):
        """Returns (ok, reason)."""
        im = self.suite.images[name]
        if name in self.failed_images:
            return False, "image %s failed to flash earlier in this run" % name
        board = im.board
        got = self.identify(im, logdir)
        self._note_image(im, got, flashed=False)
        if got == IDENTIFY_ERROR and RULES["identify_error"]:
            why = ("could not identify %s (the identify hook failed): no "
                   "flash on an unknown state" % board)
            self.bench_stopped = why
            return False, why
        if got == im.expect and RULES["image_schedule"]:
            self.board_image[board] = name
            return True, "%s already on %s (read %s)" % (board, name, got)
        self.board_image[board] = None
        if board in self.no_more_flash and RULES["flash_gate"]:
            return False, ("no flash of %s allowed: its auto-reset check "
                           "failed earlier" % board)
        params = fmt_any(im.params, self.base_ctx())
        self.board_touched.add(board)
        self.rec.event("flash-start", board=board, image=name, read_before=got)
        res = self.flashers[im.flasher](im, params, self.settings, logdir)
        self.rec.event("flash", image=name, **res)
        self._note_image(im, None, flashed=True, md5=res.get("md5"))
        if not res.get("ok"):
            why = "flash of %s failed (%s): %s" % (name, res.get("phase"),
                                                  res.get("detail"))
            self._flash_failed(name, why, res.get("recoverable"))
            return False, why
        got2 = self.identify(im, logdir)
        if got2 != im.expect:
            why = "flashed %s but identify read %s" % (name, got2)
            self._flash_failed(name, why)
            return False, why
        self.board_image[board] = name
        return True, "flashed %s to %s, read %s" % (board, name, got2)

    def _note_image(self, im, read, flashed, md5=None):
        """Every image this run used, for the record (user ruling
        2026-10-17: log the code hash of each). code_hash stays null until
        the image library's code-hash tool exists; the identity read off
        the board and the file digests stand in."""
        e = self.images_used.setdefault(im.name, dict(
            board=im.board, expect=im.expect, flasher=im.flasher, note=im.note,
            code_hash=None, md5=(im.params or {}).get("md5") or {},
            flashed=False, reads=[]))
        if read is not None:
            e["reads"].append(read)
        if flashed:
            e["flashed"] = True
            if md5:
                e["md5"] = md5

    # ---- remote ---------------------------------------------------------
    def _ship(self, g):
        if not g.ship:
            return
        host = g.host
        ssh_host = self.host_cfg(host).get("ssh")
        work = self.remote_work(host)
        for name, local_dir in sorted(g.ship.items()):
            with self._ship_lock:
                if (host, name) in self.shipped:
                    continue
                buf = io.BytesIO()
                manifest = []
                with tarfile.open(fileobj=buf, mode="w") as tf:
                    for root, dirs, files in os.walk(local_dir):
                        dirs[:] = sorted(d for d in dirs if d not in SHIP_EXCLUDE)
                        for fn in sorted(files):
                            p = os.path.join(root, fn)
                            rel = os.path.relpath(p, local_dir).replace("\\", "/")
                            tf.add(p, arcname=rel)
                            with open(p, "rb") as f:
                                manifest.append("%s  %s" % (
                                    hashlib.sha256(f.read()).hexdigest(), rel))
                data = buf.getvalue()
                line = "mkdir -p %s/%s && tar -x -C %s/%s" % (work, name, work,
                                                            name)
                r = subprocess.run(list(SSH) + [ssh_host, line], input=data,
                                   capture_output=True, timeout=600)
                if r.returncode != 0:
                    raise RuntimeError("shipping %s to %s failed: %s" % (
                        name, host, r.stderr[-300:]))
                write_new(os.path.join(self.run_dir, "ship_%s_%s.txt" % (
                    host, name)), "from %s to %s:%s/%s\n%d files\n%s\n" % (
                        local_dir, host, work, name, len(manifest),
                        "\n".join(manifest)))
                self.rec.event("ship", host=host, name=name, files=len(manifest))
                self.shipped.add((host, name))

    def _fetch(self, host, remote_dir, local_dir):
        ssh_host = self.host_cfg(host).get("ssh")
        r = subprocess.run(list(SSH) + [
            ssh_host, "if [ -d %s ]; then tar -C %s -cf - .; fi"
            % (remote_dir, remote_dir)], capture_output=True, timeout=600)
        if not r.stdout:
            return 0
        n = 0
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
            for m in tf.getmembers():
                nm = m.name
                if nm.startswith(("/", "..")) or "/../" in nm or \
                        not (m.isfile() or m.isdir()):
                    continue
                tf.extract(m, local_dir)
                n += 1
        return n

    # ---- one check ------------------------------------------------------
    def _not_run(self, g, c, reason):
        fid = "%s/%s" % (g.name, c.id)
        self.rec.result(id=fid, check=fid, group=g.name,
                        verdict=Verdict.NOT_RUN, detail=reason)

    def run_check(self, g, c, carried):
        fid = "%s/%s" % (g.name, c.id)
        if fid in carried:
            prev, rs = carried[fid]
            for r in rs:
                r2 = {k: v for k, v in r.items() if k not in ("type", "t")}
                r2["carried_from"] = prev
                self.rec.result(**r2)
            self.rec.event("check-end", check=fid, group=g.name, results=len(rs))
            return all(r["verdict"] == Verdict.PASS for r in rs)
        if c.cls == "wallclock" and RULES["wallclock_guard"]:
            wait_s = float(self.settings.get("wallclock_wait_s", 0))
            t_end = time.monotonic() + wait_s
            while True:
                busy, why = self.other_wallclock(g.host)
                if not busy or time.monotonic() >= t_end or \
                        self.stop_text() is not None:
                    break
                time.sleep(float(self.settings.get("wallclock_poll_s", 60)))
            if busy:
                self.rec.event("wallclock-refused", check=fid, reason=why)
                self._not_run(g, c, "refused: another wall-clock test on %s "
                              "(%s)" % (g.host, why))
                return False
        self.executed_checks.add(fid)
        outdir = os.path.join(self.run_dir, g.name, c.id)
        os.makedirs(outdir)
        log_path = os.path.join(outdir, "check.log")
        host = g.host
        cfg = self.host_cfg(host)
        throttled, why = False, None
        if c.cls == "build" and RULES["throttle"]:
            throttled, why = self.wallclock_running(host)
        ctx = self.base_ctx()
        ctx.update(check_id=c.id, group=g.name,
                   jobs="1" if throttled else str(os.cpu_count() or 1))
        if host == "local":
            ctx["outdir"] = outdir
        else:
            self._ship(g)
            work = self.remote_work(host)
            ctx.update(work=work, outdir="%s/out/%s/%s" % (work, g.name, c.id))
        argv = [fmt(a, ctx) for a in c.argv]
        if c.python:
            py = cfg.get("python")
            if not py:
                raise SuiteError("hosts.%s.python is not set" % host)
            argv = list(py) + argv
        env = dict(cfg.get("env") or {})
        env.update({k: fmt(v, ctx) for k, v in c.env.items()})
        cwd = fmt(c.cwd, ctx) if c.cwd else None
        timeout_s = c.timeout_s if RULES["timeout"] else 10 ** 9
        if c.cls == "wallclock":
            with self._wc_lock:
                self.active_wallclock[host] = self.active_wallclock.get(host, 0) + 1
        stop_now = lambda: (self.stop_text() or "").lower().startswith("now")
        t0 = time.monotonic()
        try:
            if host == "local":
                rc, to = procs.run_local(argv, cwd, env, timeout_s, log_path,
                                         throttled, stop_now)
            else:
                procs.run_remote(cfg["ssh"], ["mkdir", "-p", ctx["outdir"]],
                                 None, None, 60, log_path)
                rc, to = procs.run_remote(
                    cfg["ssh"], argv, cwd, env, timeout_s, log_path,
                    throttled, stop_now,
                    pidfile=ctx["outdir"] + "/.runner.pid")
                self._fetch(host, ctx["outdir"], outdir)
        finally:
            if c.cls == "wallclock":
                with self._wc_lock:
                    self.active_wallclock[host] -= 1
        dur = time.monotonic() - t0
        if g.cleanup:
            cl = [fmt(a, ctx) for a in g.cleanup]
            if host == "local":
                procs.run_local(cl, cwd, env, 300, log_path)
            else:
                procs.run_remote(cfg["ssh"], cl, cwd, env, 300, log_path)
        run = CheckRun(check=c, full_id=fid, rc=rc, timed_out=to,
                       log_path=log_path, outdir=outdir, duration_s=dur,
                       ctx=ctx)
        try:
            res = c.judge(run)
        except Exception as e:
            res = [Result(Verdict.ERROR, detail="judge raised %r" % (e,))]
        if isinstance(res, Result):
            res = [res]
        res = list(res or [])
        if not res and RULES["no_verdict"]:
            res = [Result(Verdict.ERROR, detail="no verdict from the judge")]
        if to:
            res = [r if r.verdict != Verdict.PASS else
                   Result(Verdict.ERROR, r.sub, r.figures,
                          "timed out after %.0f s (judge said PASS)" % timeout_s)
                   for r in res]
            res.append(Result(Verdict.ERROR, "timeout",
                              detail="timed out after %.0f s; tree killed"
                              % timeout_s))
        if self.suite.redact and c.outputs:
            paths = [os.path.join(outdir, o) for o in c.outputs]
            if not RULES["redact_scope"]:
                paths = [os.path.join(outdir, p) for p in os.listdir(outdir)]
            self.suite.redact([p for p in paths if os.path.isfile(p)])
        all_pass = True
        for r in res:
            v = r.verdict
            if v not in Verdict.JUDGE:
                r = Result(Verdict.ERROR, r.sub, r.figures,
                           "judge returned verdict %r" % (v,))
                v = r.verdict
            rid = fid + ("/" + r.sub if r.sub else "")
            extra = {}
            if RULES["known_fail"] and rid in self.kf:
                extra["known_fail"] = self.kf[rid]
                if v == Verdict.FAIL:
                    v = Verdict.KNOWN_FAIL
                elif v == Verdict.PASS:
                    extra["known_fail_passed"] = True
            if v != Verdict.PASS:
                all_pass = False
            self.rec.result(
                id=rid, check=fid, group=g.name, verdict=v, detail=r.detail,
                figures={k: vars(f) for k, f in r.figures.items()},
                tolerances={k: vars(t) for k, t in c.tolerances.items()},
                rc=rc, duration_s=round(dur, 1), throttled=throttled,
                throttle_reason=why,
                log=os.path.relpath(log_path, self.run_dir).replace("\\", "/"),
                **extra)
        # Written once every result of the check is: --resume carries a check
        # only with it (a kill between two results leaves none).
        self.rec.event("check-end", check=fid, group=g.name, results=len(res))
        return all_pass

    # ---- groups and lanes -----------------------------------------------
    def run_group(self, g, items, carried):
        t0 = time.monotonic()
        started = stamp()
        status = "ran"
        n_before = len(self.rec.results)
        logdir = os.path.join(self.run_dir, "_bench", g.name)

        def skip_all(reason):
            for c in items:
                self._not_run(g, c, reason)
            return "not run: %s" % reason

        try:
            waits = [d for d in g.after if d in self.group_done] \
                if RULES["after"] else []
            for d in waits:
                if not self.group_done[d].is_set():
                    self.rec.event("waiting", group=g.name, after=d)
                while not self.group_done[d].wait(2.0):
                    if self.stop_text() is not None:
                        break
            if self.lock_lost:
                status = skip_all(self.lock_lost)
                return
            if self.stop_text() is not None and waits:
                status = skip_all("STOP while waiting for %s" % ", ".join(waits))
                return
            if g.after_must_pass and waits:
                ok_v = (Verdict.PASS, Verdict.KNOWN_FAIL, Verdict.NOT_APPLICABLE)
                bad = [r["id"] for r in self.rec.results
                       if r["group"] in waits and r["verdict"] not in ok_v]
                if bad:
                    status = skip_all("after %s: %d result(s) not PASS (%s)" % (
                        ", ".join(waits), len(bad), ", ".join(bad[:3])))
                    return
            if g.needs_lock:
                self.current_group = g.name
                if self.bench_stopped:
                    status = skip_all("bench stopped (%s)" % self.bench_stopped)
                    return
                lost = self.case_gate()
                if lost:
                    status = skip_all(lost)
                    return
                if self.bench_stopped:          # a resume's start hook
                    status = skip_all("bench stopped (%s)" % self.bench_stopped)
                    return
            for name in g.image_list:
                os.makedirs(logdir, exist_ok=True)
                ok, why = self.ensure_image(name, logdir)
                self.rec.event("image", group=g.name, ok=ok, reason=why)
                if not ok:
                    status = skip_all(why)
                    return
            gate_reason = None
            for c in items:
                if c.skip and RULES["skip"]:
                    self._not_run(g, c, "skipped: %s" % c.skip)
                    continue
                if gate_reason:
                    self._not_run(g, c, gate_reason)
                    continue
                if self.lock_lost:
                    status = self.lock_lost
                    self._not_run(g, c, self.lock_lost)
                    continue
                st = self.stop_text()
                if st is not None:
                    status = "stopped (%s)" % st
                    self._not_run(g, c, "STOP: %s" % st)
                    continue
                if g.needs_lock and RULES["lock_each_case"]:
                    lost = self.case_gate()
                    if lost:
                        status = lost
                        self._not_run(g, c, lost)
                        continue
                    if self.bench_stopped:      # a resume's start hook
                        status = "bench stopped (%s)" % self.bench_stopped
                        self._not_run(g, c, status)
                        continue
                    st = self.stop_text()       # a pause STOP ended
                    if st is not None:
                        status = "stopped (%s)" % st
                        self._not_run(g, c, "STOP: %s" % st)
                        continue
                else:
                    self.wait_pause()
                passed = self.run_check(g, c, carried)
                if c.gate and not passed and RULES["gate"]:
                    gate_reason = "gate %s/%s did not PASS" % (g.name, c.id)
            changed = []
            for name in g.image_list:
                im = self.suite.images[name]
                if self.board_image.get(im.board) != name:
                    continue
                got = self.identify(im, logdir)
                if got != im.expect:
                    self.board_image[im.board] = None
                    changed.append((im, got))
            if changed and RULES["identify_after"]:
                why = "; ".join("%s read %s after the group, expected %s" % (
                    im.board, got, im.expect) for im, got in changed)
                status = "VOID: " + why
                for r in self.rec.results[n_before:]:
                    if r["group"] == g.name and not r.get("carried_from") \
                            and r["verdict"] != Verdict.NOT_RUN:
                        r["judged"] = r["verdict"]
                        r["verdict"] = Verdict.VOID
                        r["detail"] = "%s; judged %s: %s" % (
                            why, r["judged"], r.get("detail", ""))
                        self.rec.amend(r)
                self.rec.event("void-group", group=g.name, reason=why)
            # Written last: every result and amend of the group precedes it, so
            # --resume knows the group finished (identify-after included).
            self.rec.event("group-end", group=g.name, status=status)
        finally:
            self.groups_out.append(dict(
                name=g.name, lane=g.lane, host=g.host, needs_lock=g.needs_lock,
                image=g.image, started=started, ended=stamp(),
                wall_s=round(time.monotonic() - t0, 1), status=status,
                checks=len(items)))
            if g.name in self.group_done:
                self.group_done[g.name].set()

    def restore(self):
        """Every board this run flashed goes back to its standing image."""
        if not RULES["restore"]:
            return
        todo = []
        for board in sorted(self.board_touched):
            si = self.suite.standing_for(board)
            if si and self.board_image.get(board) != si:
                todo.append((board, si))
        if not todo:
            return
        if not self.lock_lost:
            ok, h = self.lock_ours()
            if not ok:
                self.lost("at the end restore, not held by %s: %s"
                          % (self.suite.role, h))
        if self.lock_lost:
            self.rec.event("restore-skipped", reason=self.lock_lost)
            for board, _si in todo:
                self.restore_note[board] = "restore skipped: %s" % self.lock_lost
            return
        if (self.bench_stopped and "flash" in self.bench_stopped
                and not RULES["restore_after_stop"]):
            self.rec.event("restore-skipped", reason=self.bench_stopped)
            for board, _si in todo:
                self.restore_note[board] = "restore skipped: %s" % self.bench_stopped
            return
        st = self.stop_text()
        if st is not None and "norestore" in st.lower():
            self.rec.event("restore-skipped", reason="STOP says norestore")
            for board, _si in todo:
                self.restore_note[board] = "restore skipped: STOP says norestore"
            return
        t0 = time.monotonic()
        started = stamp()
        statuses = []
        ok, h = self.lock_ours()
        for board, si in todo:
            if not ok:
                statuses.append("%s: RESTORE NOT RUN, lock not ours (%s)"
                                % (board, h))
                self.restore_note[board] = statuses[-1]
                continue
            if (board == self.resume_stopped_board
                    and RULES["resume_stop_kept"]):
                statuses.append("%s: restore skipped: %s"
                                % (board, self.resume_stopped))
                self.restore_note[board] = ("restore skipped: %s"
                                            % self.resume_stopped)
                continue
            if board in self.no_more_flash:
                statuses.append("%s: RESTORE NOT RUN, no further flash "
                                "allowed (%s)" % (
                                    board, self.no_flash_why.get(
                                        board, "its auto-reset check failed")))
                self.restore_note[board] = statuses[-1]
                continue
            logdir = os.path.join(self.run_dir, "_bench", "restore")
            os.makedirs(logdir, exist_ok=True)
            self.failed_images.discard(si)
            good, why = self.ensure_image(si, logdir)
            statuses.append(("restored: " if good else "RESTORE FAILED: ") + why)
            self.restore_note[board] = statuses[-1]
        status = "; ".join(statuses)
        self.rec.event("restore", status=status)
        self.groups_out.append(dict(
            name="restore", lane="bench", host="local", needs_lock=True,
            image=[si for _, si in todo], started=started, ended=stamp(),
            wall_s=round(time.monotonic() - t0, 1), status=status, checks=0))

    # ---- board hooks ----------------------------------------------------
    def _first_images(self, work):
        """{board: the first image the lane needs on it}, in lane order."""
        first = {}
        for g, _items in work:
            for name in g.image_list:
                first.setdefault(self.suite.images[name].board, name)
        return first

    def _record_hook(self, which, board, image, out):
        self.hook_out[which][board] = out
        self.hook_ts[(which, board)] = stamp()
        self.rec.event(which.replace("_", "-"), board=board,
                       image=image.name if image else None, result=out)
        return out

    def _call_hook(self, which, board, image, logdir, record=True):
        """One board hook; anything but {"ok": bool, ...} is a failure.
        record=False (a pause's or resume's hooks): returned, and kept out
        of the run's start and end hook results."""
        try:
            out = getattr(self.suite, which)(board, image, self.base_ctx(),
                                             logdir)
            if not isinstance(out, dict) or not isinstance(out.get("ok"), bool):
                out = dict(ok=False, detail="%s returned %r, not a dict with "
                           "ok" % (which, out))
        except Exception as e:
            out = dict(ok=False, detail="%s raised %r" % (which, e))
        out = dict(out)
        out["detail"] = str(out.get("detail", ""))
        if not record:
            out["t"] = stamp()
            return out
        return self._record_hook(which, board, image, out)

    def start_hooks(self, work):
        """Suite.on_start for every board the lane uses, before the start
        pass identifies or flashes anything; a failure stops the bench."""
        if self.suite.on_start is None or not RULES["board_hooks"]:
            return
        first = self._first_images(work)
        if not first:
            return
        t0 = time.monotonic()
        started = stamp()
        logdir = os.path.join(self.run_dir, "_bench", "on_start")
        os.makedirs(logdir, exist_ok=True)
        statuses = []
        for board, name in first.items():
            if self.bench_stopped:
                statuses.append("%s: not run, bench stopped (%s)"
                                % (board, self.bench_stopped))
                continue
            out = self._call_hook("on_start", board, self.suite.images[name],
                                  logdir)
            statuses.append("%s: %s%s" % (board, "" if out["ok"] else
                                          "START HOOK FAILED: ", out["detail"]))
            if not out["ok"] and RULES["start_hook_stops"]:
                self.bench_stopped = "on_start for %s failed: %s" % (
                    board, out["detail"])
        status = "; ".join(statuses)
        self.groups_out.append(dict(
            name="on-start", lane="bench", host="local", needs_lock=True,
            image=list(first.values()), started=started, ended=stamp(),
            wall_s=round(time.monotonic() - t0, 1), status=status, checks=0))

    def end_hooks(self, work):
        """Suite.on_end for every board the lane used, after the restore (or
        straight after a lane crash, which skips the restore),
        however the run ended; not while another role holds the lock."""
        if self.suite.on_end is None or not RULES["board_hooks"]:
            return
        boards = list(self._first_images(work))
        boards += sorted(self.board_touched - set(boards))
        if not boards:
            return
        t0 = time.monotonic()
        started = stamp()
        logdir = os.path.join(self.run_dir, "_bench", "on_end")
        os.makedirs(logdir, exist_ok=True)
        if not self.lock_lost:
            ok, h = self.lock_ours()
            if not ok:
                self.lost("at the end hooks, not held by %s: %s"
                          % (self.suite.role, h))
        ok, h = (False, self.lock_lost) if self.lock_lost else (True, None)
        statuses = []
        for board in boards:
            name = self.board_image.get(board)
            image = self.suite.images.get(name) if name else None
            if not ok:
                out = self._record_hook("on_end", board, image, dict(
                    ok=False, detail="not run: the lock is not ours (%s)" % h))
            else:
                out = self._call_hook("on_end", board, image, logdir)
            statuses.append("%s: %s%s" % (board, "" if out["ok"] else
                                          "END HOOK FAILED: ", out["detail"]))
        self.groups_out.append(dict(
            name="on-end", lane="bench", host="local", needs_lock=True,
            image=[self.board_image.get(b) for b in boards], started=started,
            ended=stamp(), wall_s=round(time.monotonic() - t0, 1),
            status="; ".join(statuses), checks=0))

    def release_lines(self):
        """One "RELEASE NOT CONFIRMED" line per board whose on_end failed."""
        return ["RELEASE NOT CONFIRMED: %s: %s" % (board, out["detail"])
                for board, out in sorted(self.hook_out["on_end"].items())
                if not out.get("ok")]

    def start_identify(self, work):
        """Identify every board the lane uses against the first image the
        run needs on it, before the first group; flash it if it reads
        anything else (ensure_image). A failure is handled as any flash
        failure (the flash gate, on_flash_failure)."""
        first = self._first_images(work)
        if not first:
            return
        t0 = time.monotonic()
        started = stamp()
        logdir = os.path.join(self.run_dir, "_bench", "start")
        os.makedirs(logdir, exist_ok=True)
        statuses = []
        for board, name in first.items():
            if self.bench_stopped:
                statuses.append("%s: not flashed, bench stopped (%s)"
                                % (board, self.bench_stopped))
                continue
            ok, why = self.ensure_image(name, logdir)
            statuses.append(("" if ok else "START FAILED: ") + why)
        status = "; ".join(statuses)
        self.rec.event("start-identify", status=status)
        self.groups_out.append(dict(
            name="start-identify", lane="bench", host="local", needs_lock=True,
            image=list(first.values()), started=started, ended=stamp(),
            wall_s=round(time.monotonic() - t0, 1), status=status, checks=0))

    def off_standing(self):
        """Lines for every board the run used that ends off its standing
        image: [] when all are on it (or have none)."""
        used = set(self.board_touched)
        for g, _items in self.selected():
            for name in g.image_list:
                used.add(self.suite.images[name].board)
        out = []
        for board in sorted(used):
            si = self.suite.standing_for(board)
            if si and self.board_image.get(board) != si:
                out.append("NOT ON STANDING IMAGE: %s on %s (standing %s), "
                           "reason: %s" % (
                               board, self.board_image.get(board) or
                               "unknown (last read %r)" % (
                                   self.board_read.get(board),),
                               si, self.restore_note.get(board) or
                               self.bench_stopped or "restore not reached"))
        return out

    def _lane(self, lane, work, carried):
        try:
            self._lane_body(lane, work, carried)
        finally:
            if lane == "bench":
                try:
                    self.end_hooks(work)
                except Exception as e:
                    self.rec.event("end-hooks-crash", error=repr(e),
                                   tb=traceback.format_exc()[-2000:])
                    boards = list(self._first_images(work))
                    if RULES["end_crash_boards"]:
                        boards += sorted(self.board_touched - set(boards))
                    for board in boards:
                        if board not in self.hook_out["on_end"]:
                            self._record_hook("on_end", board, None, dict(
                                ok=False, detail="end hooks crashed: %r" % (e,)))
                try:
                    self.release_end()
                except Exception as e:
                    self.lock_release = dict(ok=False, error="the release "
                                             "crashed: %r" % (e,), boards=None)
                    self.rec.event("lock-release-crash", error=repr(e),
                                   tb=traceback.format_exc()[-2000:])

    def _lane_body(self, lane, work, carried):
        try:
            if lane == "bench":
                self.bench_work = work
                self.start_hooks(work)
            if lane == "bench" and RULES["start_identify"]:
                self.start_identify(work)
            for g, items in work:
                self.run_group(g, items, carried)
            if lane == "bench":
                self.restore()
        except Exception as e:
            self.rec.event("lane-crash", lane=lane, error=repr(e),
                           tb=traceback.format_exc()[-2000:])
            for g, _ in work:
                if g.name in self.group_done:
                    self.group_done[g.name].set()
            done = {r["check"] for r in self.rec.results}
            for g, items in work:
                for c in items:
                    fid = "%s/%s" % (g.name, c.id)
                    if fid not in done:
                        self.rec.result(id=fid, check=fid, group=g.name,
                                        verdict=Verdict.ERROR,
                                        detail="lane %s crashed: %r" % (lane, e))

    # ---- the precondition ---------------------------------------------
    def requirement_met(self):
        """Suite.requires names a suite (e.g. the emulated one). Met when
        the newest finished run of it under runs_root is on the same code as
        this run, and its results match the reference run named in
        settings.references[<that suite>] (no differences by compare); with
        no reference yet (the first baseline), when none of its results is
        FAIL, ERROR or VOID and no known FAIL passed."""
        need = self.suite.requires
        code = self._code()
        ident = _code_ident(code)
        if ident is None:
            return False, ("this run's code is not identifiable (%s): a dirty "
                           "tree has no code identity" % code)
        cands = []
        for rec_path in glob.glob(os.path.join(self.runs_root, "*", "record.json")):
            try:
                with open(rec_path, encoding="utf-8") as f:
                    r = json.load(f)
            except (OSError, ValueError):
                continue
            if r.get("suite") == need:
                cands.append((r.get("ended", ""), rec_path, r))
        if not cands:
            return False, "no finished run of %s under %s" % (need, self.runs_root)
        cands.sort()
        _ended, path, r = cands[-1]
        rid, bridged = self._record_ident(r.get("code") or {}, code)
        if rid != ident:
            return False, ("the newest %s run (%s) is on other code: %s, not "
                           "%s%s" % (need, r.get("run_id"), rid, ident, bridged))
        self.requires_note = "%s on %s%s" % (r.get("run_id"), ident, bridged)
        if RULES["requires_whole"]:
            sel = r.get("selection") or {}
            if RULES["subset_not_whole"] and (
                    r.get("subset") or sel.get("groups") or sel.get("checks")):
                return False, ("the newest %s run (%s) was a partial, subset "
                               "run (subset %s, --group %s --check %s, %s "
                               "case(s)): the whole suite must have run"
                               % (need, r.get("run_id"), r.get("subset"),
                                  sel.get("groups"), sel.get("checks"),
                                  len(r.get("subset_cases") or [])))
            nr = [x["id"] for x in r["results"]
                  if x["verdict"] == Verdict.NOT_RUN
                  and not str(x.get("detail", "")).startswith("skipped: ")]
            if nr:
                return False, ("the newest %s run (%s) has %d check(s) NOT-RUN "
                               "that are not declared skips (%s)"
                               % (need, r.get("run_id"), len(nr),
                                  ", ".join(nr[:3])))
        ref = (self.settings.get("references") or {}).get(need)
        if ref:
            with open(os.path.join(ref, "record.json"), encoding="utf-8") as f:
                diffs = compare(json.load(f), r)
            if diffs:
                return False, ("the newest %s run (%s) differs from its "
                               "reference: %s" % (need, r.get("run_id"),
                                                  "; ".join(diffs[:3])))
            return True, "%s matched its reference" % self.requires_note
        bad = [x["id"] for x in r["results"]
               if x["verdict"] in (Verdict.FAIL, Verdict.ERROR, Verdict.VOID)
               or x.get("known_fail_passed")]
        if bad:
            return False, ("the newest %s run (%s) has %d result(s) FAIL, ERROR "
                           "or VOID (%s) and there is no reference to compare "
                           "with" % (need, r.get("run_id"), len(bad),
                                     ", ".join(bad[:3])))
        return True, "%s is clean (no reference yet)" % self.requires_note

    def resume_matches(self):
        """--resume carries verdicts, so the run it carries them from must be
        of this suite and on this code, as its start event records them."""
        start = next((e for e in load_progress(self.resume)
                      if e.get("type") == "event" and e.get("kind") == "start"),
                     None)
        if not start or "suite" not in start or "code" not in start:
            return False, ("%s records no suite and code at its start (a run "
                           "from before resume checked them)" % self.resume)
        if start["suite"] != self.suite.name:
            return False, ("%s is a run of %s, not %s"
                           % (self.resume, start["suite"], self.suite.name))
        now = dict(self._code())
        now.setdefault("code_hash", None)
        then = dict(start["code"])
        if now.get("product_digest") and not then.get("product_digest"):
            rid, _b = self._record_ident(then, now)
            if rid and rid.startswith("product:"):
                then["product_digest"] = rid[len("product:"):]
        if not _same_code(then, now):
            return False, ("%s ran on other code: %s, not %s"
                           % (self.resume, _code_ident(then)
                              or then, _code_ident(now) or now))
        return True, ""

    def _record_ident(self, rcode, now):
        """(identity of a recorded run's code, note). A record with no
        product digest, when this run has one, gets the digest of its
        commit's tree from git (bridge), if that tree was clean."""
        rid = _code_ident(rcode)
        if (not RULES["digest_bridge"] or rcode.get("product_digest")
                or not now.get("product_digest") or not now.get("repo")):
            return rid, ""
        commit = rcode.get("commit")
        if not commit or rcode.get("dirty") is not False:
            return rid, " (no bridge: the record's tree was not clean)"
        try:
            d = self._digest_at(now["repo"], commit)
        except Exception as e:
            return rid, " (no bridge: %r)" % (e,)
        return "product:%s" % d, (" (product digest bridged from commit %s)"
                                  % commit[:12])

    def _digest_at(self, repo, commit):
        """The product digest of the tree at `commit`, read from git
        objects (git archive), never from a working tree."""
        if commit in self._bridged:
            return self._bridged[commit]
        import io
        import shutil
        import tarfile
        import tempfile
        tar = subprocess.run(["git", "-C", repo, "archive", commit],
                             capture_output=True, check=True).stdout
        work = os.path.join(tempfile.gettempdir(), "vtrux-bench", "digest",
                            commit[:12])
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
            tf.extractall(work, filter="data")
        d = product_digest(work, self.suite.product_files(work))
        self._bridged[commit] = d
        return d

    def _code(self):
        if not self.suite.code_info:
            return {}
        try:
            code = dict(self.suite.code_info(self.base_ctx() if hasattr(
                self, "run_dir") else {}))
        except Exception as e:
            return {"error": repr(e)}
        return self._with_digest(code)

    def _with_digest(self, code):
        """Adds product_digest (and the file count) when the suite gives
        product_files and its code_info gives the repo root."""
        if (RULES["product_digest"] and self.suite.product_files
                and code.get("repo")):
            try:
                files = self.suite.product_files(code["repo"])
                code["product_digest"] = product_digest(code["repo"], files)
                code["product_files"] = len(set(files))
            except Exception as e:
                code["product_digest_error"] = repr(e)
        return code

    # ---- the run --------------------------------------------------------
    def plan_lines(self):
        lines = []
        lanes = {}
        for g, items in self.selected():
            lanes.setdefault(g.lane, []).append((g, items))
        for lane, work in lanes.items():
            lines.append("lane %s:" % lane)
            for g, items in work:
                lines.append("  group %s  host=%s bench=%s image=%s" % (
                    g.name, g.host, "yes" if g.needs_lock else "no",
                    ",".join(g.image_list) or "-"))
                for c in items:
                    lines.append("    %-9s %s/%s  %s%s" % (
                        c.cls, g.name, c.id, "[python] " if c.python else "",
                        " ".join(c.argv)))
            if lane == "bench" and self.suite.standings():
                lines.append("  then restore each flashed board: %s" % ", ".join(
                    "%s -> %s" % kv for kv in sorted(self.suite.standings().items())))
        return lines

    def run(self):
        if not self.runs_root:
            raise SuiteError("settings.runs_root is not set")
        if self.suite.requires and RULES["requires"]:
            ok, why = self.requirement_met()
            if not ok:
                raise SuiteError("refusing to start: %s" % why)
        if self.resume and RULES["resume_code"]:
            ok, why = self.resume_matches()
            if not ok:
                raise SuiteError("refusing to resume: %s" % why)
        if self.keep_lock is not None and not (self.lock_log
                                               and self.needs_bench()):
            raise lockmod.LockError(
                "refusing to start: --keep-lock hands over the bench log's "
                "lock, and %s" % ("this run includes no bench group"
                                  if self.lock_log else
                                  "the settings name no bench_lock_log: "
                                  "before the cutover the Holder line is "
                                  "edited by hand"))
        if self.needs_bench() and self.lock_log:
            self._take_at_start()
        elif self.needs_bench():
            ok, h = self.lock_ours()
            if not ok:
                raise lockmod.LockError(
                    "refusing to start: the run includes bench groups and the "
                    "bench lock is not held by %s (Holder: %s). Take it and "
                    "message the other tester first (bench-holder rule 2)."
                    % (self.suite.role, h))
        if not self.took:
            self.run_id, self.run_dir = new_run_dir(
                self.runs_root, self.suite.name, self.run_id_req)
        self.rec = Recorder(self.run_dir, self.run_id)
        carried = self._carried()
        sel = self.selected()
        subset = bool(self.sel_groups or self.sel_checks) and RULES["subset_mark"]
        cases = (["%s/%s" % (g.name, c.id) for g, items in sel for c in items]
                 if subset else None)
        header = dict(
            suite=self.suite.name, role=self.suite.role, started=stamp(),
            host=socket.gethostname(),
            selection=dict(groups=self.sel_groups, checks=self.sel_checks),
            subset=subset, subset_cases=cases,
            resume_from=self.resume, settings=self.settings,
            plan=self.plan_lines())
        code = self._code()
        code.setdefault("code_hash", None)   # pending a user ruling
        header["code"] = code
        header["requires_met"] = self.requires_note
        clocks = {"local": time.time()}
        for host in sorted({g.host for g, _ in sel if g.host != "local"}):
            try:
                r = subprocess.run(list(SSH) + [self.host_cfg(host)["ssh"],
                                                "date +%s.%N"],
                                   capture_output=True, text=True, timeout=60)
                clocks[host] = float(r.stdout.strip())
            except Exception as e:
                clocks[host] = "unreadable: %r" % (e,)
        header["host_clocks_epoch"] = clocks
        self.rec.event("start", run_id=self.run_id, suite=self.suite.name,
                       code=code, subset=subset, subset_cases=cases)
        lanes = {}
        for g, items in sel:
            lanes.setdefault(g.lane, []).append((g, items))
            self.group_done[g.name] = threading.Event()
        threads = [threading.Thread(target=self._lane, args=(ln, w, carried),
                                    name=ln) for ln, w in lanes.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        produced = {r["id"] for r in self.rec.results
                    if r["verdict"] != Verdict.NOT_RUN}
        ran = self.executed_checks | {r["check"] for r in self.rec.results
                                      if r.get("carried_from")}
        unmatched = []
        if RULES["known_fail"]:
            for kid in sorted(self.kf):
                if "/".join(kid.split("/")[:2]) in ran and kid not in produced:
                    unmatched.append(kid)
        off = self.off_standing() if RULES["standing_line"] else []
        header["not_on_standing"] = off
        unconfirmed = self.release_lines() if RULES["release_line"] else []
        header["release_not_confirmed"] = unconfirmed
        header["board_hooks"] = self.hook_out
        locklines = self.lock_lines()
        header["lock_lines"] = locklines
        header["lock"] = dict(
            source="log" if self.lock_log else "holder",
            path=self.lock_log or self.bench_holder, took=self.took,
            keep_lock=self.keep_lock, lost=self.lock_lost,
            release=self.lock_release)
        for ln in locklines + unconfirmed + off:
            self.out(ln)
        if self.needs_bench():
            lines = locklines + unconfirmed + off + [
                "Boards at the end of run %s (for the release message and the "
                "board sections, bench-holder rule 4):" % self.run_id]
            for board in sorted(set(self.board_read) | set(self.board_image)
                                | set(self.hook_out["on_start"])
                                | set(self.hook_out["on_end"])):
                lines.append("  %s: last identify read %s; image %s; flashed in "
                             "this run: %s" % (
                                 board, self.board_read.get(board),
                                 self.board_image.get(board) or "unknown",
                                 board in self.board_touched))
                for which in ("on_start", "on_end"):
                    o = self.hook_out[which].get(board)
                    if o is not None:
                        lines.append("    %s: %s, %s" % (
                            which, "ok" if o.get("ok") else "FAILED",
                            o.get("detail")))
            lines.append("  bench stopped: %s" % self.bench_stopped)
            r = self.lock_release
            if r is not None and r["ok"]:
                lines.append("Lock released by the runner (bench log):")
                lines.extend("  %s" % ln for ln in r["lines"])
            write_new(os.path.join(self.run_dir, "board_state.txt"),
                      "\n".join(lines) + "\n")
        self.rec.event("end")
        header["images_used"] = self.images_used
        header["harness"] = dict(
            gated=False, commit=code.get("commit"), dirty=code.get("dirty"),
            artifacts_sha256=code.get("artifacts_sha256"),
            ship_lists={os.path.basename(p): _sha256_file(p) for p in sorted(
                glob.glob(os.path.join(self.run_dir, "ship_*.txt")))})
        rec = write_record(self.rec, header, self.groups_out, unmatched)
        self.rec.close()
        return rec


def product_digest(root, rels):
    """sha256 over the product files, sorted by repo-relative path: each
    path, a NUL, its bytes, a NUL."""
    h = hashlib.sha256()
    for rel in sorted(set(r.replace(os.sep, "/") for r in rels)):
        h.update(rel.encode("utf-8") + b"\0")
        with open(os.path.join(root, rel), "rb") as f:
            h.update(f.read())
        h.update(b"\0")
    return h.hexdigest()


def _sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _code_ident(code):
    """What "the same code" means for Suite.requires and --resume: the
    product digest where the suite gives one (user 2026-10-20); else the
    code hash once it is logged; else the commit, and only from a clean
    tree."""
    if RULES["product_digest"] and code.get("product_digest"):
        return "product:%s" % code["product_digest"]
    if code.get("code_hash"):
        return "code_hash:%s" % code["code_hash"]
    if code.get("commit") and code.get("dirty") is False:
        return "commit:%s" % code["commit"]
    return None


def _same_code(a, b):
    """The same code for --resume: the same identity where either side has
    one; otherwise the same recorded code_info."""
    ia, ib = _code_ident(a), _code_ident(b)
    if ia or ib:
        return ia == ib
    strip = lambda d: {k: v for k, v in d.items() if k != "error"}  # noqa: E731
    return strip(a) == strip(b)


def exit_code(record):
    ok = (Verdict.PASS, Verdict.KNOWN_FAIL, Verdict.NOT_APPLICABLE)
    bad = [r for r in record["results"] if r["verdict"] not in ok
           or r.get("known_fail_passed")]
    return 0 if not bad and not record.get("unmatched_known_fails") else 1
