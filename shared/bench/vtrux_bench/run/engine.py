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
  restore         at the end, every board the run flashed is put back on
                  its standing image (unless a flash failure stopped the
                  bench, or STOP says norestore)
"""

import fnmatch
import hashlib
import io
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
from .record import Recorder, load_progress, new_run_dir, stamp, write_record, write_new

RULES = {k: True for k in (
    "lock_guard", "flash_gate", "image_schedule", "identify_after",
    "no_verdict", "known_fail", "stop_pause", "throttle", "redact_scope",
    "gate", "timeout", "skip", "restore")}

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
        self.no_more_flash = set()
        self.bench_stopped = None
        self.failed_images = set()
        self.executed_checks = set()
        self.groups_out = []
        self.shipped = set()
        self._ship_lock = threading.Lock()
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
        for e in load_progress(self.resume):
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
            try:
                h = lockmod.holder(self.bench_holder)
                if not lockmod.is_free(h):
                    return True, "bench lock held: %s" % h[:80]
            except Exception as e:
                return True, "bench-holder unreadable (%s): assume running" % e
        pats = self.settings.get("wallclock_patterns") or []
        if pats:
            ssh_host = None if host == "local" else self.host_cfg(host).get("ssh")
            try:
                lines = procs.process_lines(ssh_host)
            except Exception as e:
                return True, "process list failed (%s): assume running" % e
            hits = procs.matching(lines, pats)
            if hits:
                return True, "process: %s" % hits[0][:100]
        return False, "no wall-clock test on %s" % host

    # ---- images ---------------------------------------------------------
    def identify(self, image, logdir):
        try:
            got = self.suite.identify(image, self.base_ctx(), logdir)
        except Exception as e:
            got = None
            self.rec.event("identify-error", image=image.name, error=repr(e))
        self.board_read[image.board] = got
        self.rec.event("identify", board=image.board, expected_image=image.name,
                       expected=image.expect, read=got)
        return got

    def _flash_failed(self, name, why, recoverable=True):
        if RULES["flash_gate"]:
            if recoverable is False:
                self.no_more_flash.add(self.suite.images[name].board)
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
            return all(r["verdict"] == Verdict.PASS for r in rs)
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
                rc, to = procs.run_remote(cfg["ssh"], argv, cwd, env,
                                          timeout_s, log_path, throttled,
                                          stop_now)
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
        finally:
            self.groups_out.append(dict(
                name=g.name, lane=g.lane, host=g.host, needs_lock=g.needs_lock,
                image=g.image, started=started, ended=stamp(),
                wall_s=round(time.monotonic() - t0, 1), status=status,
                checks=len(items)))

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
        if self.bench_stopped and "flash" in self.bench_stopped:
            self.rec.event("restore-skipped", reason=self.bench_stopped)
            return
        st = self.stop_text()
        if st is not None and "norestore" in st.lower():
            self.rec.event("restore-skipped", reason="STOP says norestore")
            return
        t0 = time.monotonic()
        started = stamp()
        statuses = []
        ok, h = self.lock_ours()
        for board, si in todo:
            if not ok:
                statuses.append("%s: RESTORE NOT RUN, lock not ours (%s)"
                                % (board, h))
                continue
            if board in self.no_more_flash:
                statuses.append("%s: RESTORE NOT RUN, its auto-reset check "
                                "failed" % board)
                continue
            logdir = os.path.join(self.run_dir, "_bench", "restore")
            os.makedirs(logdir, exist_ok=True)
            self.failed_images.discard(si)
            good, why = self.ensure_image(si, logdir)
            statuses.append(("restored: " if good else "RESTORE FAILED: ") + why)
        status = "; ".join(statuses)
        self.rec.event("restore", status=status)
        self.groups_out.append(dict(
            name="restore", lane="bench", host="local", needs_lock=True,
            image=[si for _, si in todo], started=started, ended=stamp(),
            wall_s=round(time.monotonic() - t0, 1), status=status, checks=0))

    def _lane(self, lane, work, carried):
        try:
            for g, items in work:
                self.run_group(g, items, carried)
            if lane == "bench":
                self.restore()
        except Exception as e:
            self.rec.event("lane-crash", lane=lane, error=repr(e),
                           tb=traceback.format_exc()[-2000:])
            done = {r["check"] for r in self.rec.results}
            for g, items in work:
                for c in items:
                    fid = "%s/%s" % (g.name, c.id)
                    if fid not in done:
                        self.rec.result(id=fid, check=fid, group=g.name,
                                        verdict=Verdict.ERROR,
                                        detail="lane %s crashed: %r" % (lane, e))

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
        header = dict(
            suite=self.suite.name, role=self.suite.role, started=stamp(),
            host=socket.gethostname(),
            selection=dict(groups=self.sel_groups, checks=self.sel_checks),
            resume_from=self.resume, settings=self.settings,
            plan=self.plan_lines())
        code = {}
        if self.suite.code_info:
            try:
                code = dict(self.suite.code_info(self.base_ctx()))
            except Exception as e:
                code = {"error": repr(e)}
        code.setdefault("code_hash", None)   # pending a user ruling
        header["code"] = code
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
        self.rec.event("start", run_id=self.run_id)
        lanes = {}
        for g, items in sel:
            lanes.setdefault(g.lane, []).append((g, items))
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
        if self.needs_bench():
            lines = ["Boards at the end of run %s (for the release message "
                     "and the board sections, bench-holder rule 4):"
                     % self.run_id]
            for board in sorted(set(self.board_read) | set(self.board_image)):
                lines.append("  %s: last identify read %s; image %s; flashed in "
                             "this run: %s" % (
                                 board, self.board_read.get(board),
                                 self.board_image.get(board) or "unknown",
                                 board in self.board_touched))
            lines.append("  bench stopped: %s" % self.bench_stopped)
            write_new(os.path.join(self.run_dir, "board_state.txt"),
                      "\n".join(lines) + "\n")
        self.rec.event("end")
        rec = write_record(self.rec, header, self.groups_out, unmatched)
        self.rec.close()
        return rec


def exit_code(record):
    ok = (Verdict.PASS, Verdict.KNOWN_FAIL, Verdict.NOT_APPLICABLE)
    bad = [r for r in record["results"] if r["verdict"] not in ok
           or r.get("known_fail_passed")]
    return 0 if not bad and not record.get("unmatched_known_fails") else 1
