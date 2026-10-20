"""The chain runner: executes a Suite.

Lanes run in parallel threads: "bench" (every group that needs the bench,
one after another, then the restore) and one "logic:<host>" lane per host
for the rest. Within a lane, groups and their checks run in order.

The bench lock (bench-holder rule 2, user 2026-10-17): a chained run holds
the lock from start to finish. The tester takes it and messages the other
tester before starting, and releases it after. The runner never edits
bench-holder.md; it refuses to start a run that includes a bench group
unless the suite's role holds the lock, and re-reads the Holder line before
every bench group. Board changes during the run are in the run's own log
(progress.jsonl, record.json, board_state.txt).

Rules the engine enforces (each has a test in tests/test_run_core.py that
fails when the rule is switched off through RULES):
  lock_guard      no bench group runs unless the suite's role holds the lock
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
  stop_pause      STOP ends the lane (the rest NOT-RUN), PAUSE waits
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
                  every one after the restore, on every run however it
                  ended (not after the lock is lost); both are recorded
                  (user 2026-10-20: the WiCAN is rebooted at the start and
                  at each release, so a killed run cannot leave it armed)
  start_hook_stops  an on_start that fails (or raises) stops the bench
                  before anything is identified or flashed: every group
                  that needs the lock is NOT-RUN
  release_line    an on_end that fails (or raises) opens board_state.txt,
                  index.txt and the record with "RELEASE NOT CONFIRMED: ..."
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

from . import lock as lockmod
from . import procs
from .flash import BUILTIN as BUILTIN_FLASHERS
from .model import Check, CheckRun, Result, SuiteError, Verdict, validate
from .record import (Recorder, compare, load_progress, new_run_dir, stamp,
                     write_record, write_new)

RULES = {k: True for k in (
    "lock_guard", "flash_gate", "image_schedule", "identify_after",
    "no_verdict", "known_fail", "stop_pause", "throttle", "redact_scope",
    "gate", "timeout", "skip", "restore", "identify_error", "after",
    "wallclock_guard", "requires", "requires_whole", "resume_code",
    "subset_mark", "subset_not_whole", "start_identify", "restore_after_stop",
    "standing_line", "product_digest", "digest_bridge", "board_hooks",
    "start_hook_stops", "release_line", "carry_finished", "carry_check_end")}

IDENTIFY_ERROR = "<identify failed>"

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
                 groups=None, checks=None, resume=None, out=print):
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
        self.kf = {k.id: k.ref for k in suite.known_fails}
        self._wc_lock = threading.Lock()
        self.active_wallclock = {}
        # Per board (Image.board): what it is on, what it last read, whether
        # this run flashed it, and boards whose auto-reset check failed.
        self.board_image = {}
        self.board_read = {}
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
        """(ok, holder text). Ours = the suite's role holds the lock."""
        if not RULES["lock_guard"]:
            return True, "rule off"
        if not self.bench_holder:
            return False, "settings.bench_holder is not set"
        try:
            h = lockmod.holder(self.bench_holder)
        except (OSError, lockmod.LockError) as e:
            return False, "bench-holder unreadable: %s" % e
        return lockmod.held_by(h, self.suite.role), h

    def wallclock_running(self, host):
        with self._wc_lock:
            if self.active_wallclock.get(host, 0) > 0:
                return True, "this run has a wall-clock check on %s" % host
        if host == "local" and self.bench_holder:
            # Another role's lock means its wall-clock test may be running.
            # Under this suite's own role, nothing of the other project can
            # run, so this run's own wall-clock checks (above) and the
            # process scan (below) decide.
            try:
                h = lockmod.holder(self.bench_holder)
                if not lockmod.is_free(h) and not lockmod.held_by(
                        h, self.suite.role):
                    return True, "bench lock held by another role: %s" % h[:80]
            except Exception as e:
                return True, "bench-holder unreadable (%s): assume running" % e
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
        if host == "local" and self.bench_holder:
            try:
                h = lockmod.holder(self.bench_holder)
                if not lockmod.is_free(h) and not lockmod.held_by(
                        h, self.suite.role):
                    return True, "bench lock held by another role: %s" % h[:80]
            except Exception as e:
                return True, "bench-holder unreadable (%s)" % e
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
                if self.bench_stopped:
                    status = skip_all("bench stopped (%s)" % self.bench_stopped)
                    return
                ok, h = self.lock_ours()
                if not ok:
                    self.bench_stopped = "lock not held by %s: %s" % (
                        self.suite.role, h)
                    self.rec.event("lock-lost", group=g.name, holder=h)
                    status = skip_all(self.bench_stopped)
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
                st = self.stop_text()
                if st is not None:
                    status = "stopped (%s)" % st
                    self._not_run(g, c, "STOP: %s" % st)
                    continue
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
        self.rec.event(which.replace("_", "-"), board=board,
                       image=image.name if image else None, result=out)
        return out

    def _call_hook(self, which, board, image, logdir):
        """One board hook; anything but {"ok": bool, ...} is a failure."""
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
        """Suite.on_end for every board the lane used, after the restore,
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
        ok, h = self.lock_ours()
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
                    for board in self._first_images(work):
                        if board not in self.hook_out["on_end"]:
                            self._record_hook("on_end", board, None, dict(
                                ok=False, detail="end hooks crashed: %r" % (e,)))

    def _lane_body(self, lane, work, carried):
        try:
            if lane == "bench":
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
        if self.needs_bench():
            ok, h = self.lock_ours()
            if not ok:
                raise lockmod.LockError(
                    "refusing to start: the run includes bench groups and the "
                    "bench lock is not held by %s (Holder: %s). Take it and "
                    "message the other tester first (bench-holder rule 2)."
                    % (self.suite.role, h))
        self.run_id, self.run_dir = new_run_dir(self.runs_root, self.suite.name,
                                                self.run_id_req)
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
        for ln in unconfirmed + off:
            self.out(ln)
        if self.needs_bench():
            lines = unconfirmed + off + [
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
