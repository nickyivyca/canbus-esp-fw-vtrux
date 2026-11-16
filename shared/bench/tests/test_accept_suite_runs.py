"""Tests of the acceptance runner's suite runs (vtrux_bench/accept/nested.py
and its group in compile.py): ACCEPT-SPEC A7, A6 for their records, and the
first-run rule of A12 (the charge-interposer reviewer, 2026-11-01). No
hardware. Each test names what it checks.

    python -B shared/bench/tests/test_accept_suite_runs.py            every test
    python -B shared/bench/tests/test_accept_suite_runs.py --can-fail  the
        control first (every test unchanged, which must pass), then each test
        again with a rule it guards switched off (nested.RULES; every rule has
        one). The rules are switched off in this process; the nested run is a
        subprocess, so a switched-off rule reaches it through VTRUX_TEST_OFF,
        which only this test's own wrapper (nested_off.py) reads.

A small git repo holds a project suite (proj/suite.py) with one passing case
and one that fails the first time it runs and passes after (a marker file),
so a re-run of flagged cases has something to re-run. Everything is in this
run's own folder under the worktree's scratch/ (tests/_scratch.py), removed
at the end with vtrux_bench/scratch.py.
"""

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
import fake_adapter as FA                            # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import compile as C          # noqa: E402
from vtrux_bench.accept import listfile as L         # noqa: E402
from vtrux_bench.accept import nested as NS          # noqa: E402
from vtrux_bench.accept import record as R           # noqa: E402
from vtrux_bench.accept import settings as ST        # noqa: E402
from vtrux_bench.accept import trees as T            # noqa: E402
from vtrux_bench.run import engine as E              # noqa: E402

BASE = _scratch.run_dir("accept_suite_runs")
N = [0]
MAC = "02:00:00:00:00:01"                    # locally administered, no real board's
OFF_ENV = "VTRUX_TEST_OFF"

SUITE = '''import os, sys
from vtrux_bench.run.model import Check, Group, Suite

FLAKY = ("import os, sys; p = os.path.join(r'{flake_dir}', 'seen'); "
         "ok = os.path.exists(p); open(p, 'w').close(); sys.exit(0 if ok else 1)")
TREE = ("import os, sys; sys.exit(0 if os.path.samefile(r'{fake_tree}', os.getcwd()) "
        "else 1)")
EMULATED = Suite(name="proj-emu", role="tester (test-suite)", groups=[
    Group("a", [Check("ok", ["-c", "pass"], python=True),
                Check("flaky", ["-c", FLAKY], python=True),
                Check("tree", ["-c", TREE], python=True)])])
'''

LIST = '''
[step]
id = "s1"
project = "proj"
ordered_by = "reviewer"
ordered_on = "2026-11-01"
spec = ["9.1"]
verification = "9.1 Emulation"

[trees.base]
project = "{C1}"

[[suite_run]]
id = "emu"
tree = "base"
suite = "emulated"
rerun_flagged = {RERUN}
'''

# A wrapper for the nested run that switches off, in the subprocess, the
# rules named in VTRUX_TEST_OFF; it is used only by these tests.
WRAPPER = '''import os, sys
from vtrux_bench.accept import nested
for k in filter(None, os.environ.get("%s", "").split(",")):
    nested.RULES[k] = False
sys.exit(nested.main())
''' % OFF_ENV


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class Fixture:
    """Runs the list's suite run to the end; holds what it made."""

    def __init__(self, rerun=True, placement=None):
        N[0] += 1
        self.dir = os.path.join(BASE, "f%d" % N[0])
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        write(self.repo, "proj/suite.py", SUITE)
        write(self.repo, "proj/check.py", "pass\n")
        write(self.repo, "shared/comp/tool.py", "pass\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "C1")
        c1 = git(self.repo, "rev-parse", "HEAD")
        FA.PINS.clear()
        write(self.repo, "proj/acceptance/s.toml",
              LIST.format(C1=c1, RERUN="true" if rerun else "false"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "list")
        alist = L.load(self.repo, "HEAD:proj/acceptance/s.toml".replace(
            "HEAD", git(self.repo, "rev-parse", "HEAD")), FA)
        self.rec = R.start(os.path.join(self.dir, "runs"), "s1", out=lambda s: None,
                           ceiling=BASE)
        self.made = S.Made()
        work = os.path.join(self.dir, "work")
        trees = T.make_trees(self.repo, alist, FA, work, self.made, say=self.rec.say)
        flake = os.path.join(self.dir, "flake")
        os.makedirs(flake)
        machine = os.path.join(self.dir, "machine.json")
        m = dict(hosts={"local": {"python": [sys.executable, "-B"]}}, bench_mac=MAC,
                 flake_dir=flake, runs_root=os.path.join(self.dir, "elsewhere"),
                 bench_holder=os.path.join(self.dir, "bench-holder.md"))
        if placement:
            m["placement"] = placement
        with open(machine, "w", encoding="utf-8") as f:
            json.dump(m, f)
        path, s = ST.write_settings(machine, FA, {}, trees, self.rec, self.made, work)
        base = C.machine_env(FA)
        off = ",".join(k for k, v in NS.RULES.items() if not v)
        if off:
            base[OFF_ENV] = off
        per_run = {"emu": ST.write_tree_settings(s, FA, "emu", trees["base"], self.rec,
                                                 self.made, work)}
        suite = C.compile_suite(alist, trees, FA, s, base, paths={
            "settings": path, "masked": os.path.join(self.rec.path, "settings.masked.json"),
            "record_id": self.rec.id, "suite_runs": per_run})
        chk = suite.groups[0].checks[0]
        wrapper = os.path.join(self.dir, "nested_off.py")
        with open(wrapper, "w", encoding="utf-8") as f:
            f.write(WRAPPER)
        i = chk.argv.index("-m")
        chk.argv[i:i + 2] = [wrapper]                  # same arguments, through the wrapper
        self.chain = C.run_suite(suite, s, self.rec, adapter=FA)
        self.rid = "%s-emu" % self.rec.id
        self.runs = os.path.join(self.rec.path, "runs")
        with open(os.path.join(self.chain["_dir"], "emu", "run", "nested.json"),
                  encoding="utf-8") as f:
            self.nested = json.load(f)

    def record(self, rid):
        with open(os.path.join(self.runs, rid, "record.json"), encoding="utf-8") as f:
            text = f.read()
        return text, json.loads(text)

    def verdict(self):
        return {r["id"]: r["verdict"] for r in self.chain["results"]}["emu/run"]


# ---- the tests --------------------------------------------------------------
def test_nested_record():
    """A7: a suite run is a nested chain run with its own record inside the
    acceptance record (runs/<record id>-<suite run id>/), of the whole
    suite, with settings of its own naming its tree (the adapter's
    tree_settings: case a/tree passes only then); A6: that record holds
    the masked settings, and so does the record's copy of them."""
    f = Fixture(rerun=False)
    text, rec = f.record(f.rid)
    assert MAC not in text and "<MAC>" in text, "the nested record holds the real settings"
    assert rec["subset"] is False, rec["subset"]
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"a/ok": "PASS", "a/flaky": "FAIL", "a/tree": "PASS"}, v
    with open(os.path.join(f.rec.path, "settings.emu.masked.json"), encoding="utf-8") as fh:
        m = fh.read()
    assert "fake_tree" in m and MAC not in m, m
    assert sorted(os.listdir(f.runs)) == [f.rid], os.listdir(f.runs)


def test_rerun_new_record():
    """A7: a re-run of the flagged cases is a new nested record selecting
    only them; the first record is left as it closed."""
    f = Fixture(rerun=True)
    _t, first = f.record(f.rid)
    ids = sorted(r["id"] for r in first["results"])
    assert ids == ["a/flaky", "a/ok", "a/tree"], "the first record changed: %s" % ids
    _t2, second = f.record(f.rid + "-rerun")
    assert second["subset"] is True and second["subset_cases"] == ["a/flaky"], (
        second["subset"], second["subset_cases"])
    assert {r["id"]: r["verdict"] for r in second["results"]} == {"a/flaky": "PASS"}


def test_first_run_decides():
    """A12 (the reviewer's ruling (a), 2026-11-01): the first, whole run
    decides: a case that failed and passed on its re-run leaves the suite
    run FAIL. Each flagged case is listed with its first and re-run
    verdicts side by side, in nested.json and on the console."""
    f = Fixture(rerun=True)
    assert f.verdict() == "FAIL", f.verdict()
    assert f.nested["flagged"] == [{"check": "a/flaky", "first": "FAIL", "rerun": "PASS"}], \
        f.nested["flagged"]
    with open(os.path.join(f.chain["_dir"], "emu", "run", "check.log"), encoding="utf-8") as fh:
        log = fh.read()
    assert any("a/flaky" in ln and "first FAIL" in ln and "re-run PASS" in ln
               for ln in log.splitlines()), log[-800:]


def _user_bench_slice():
    """True on Linux with the user's systemd and a bench.slice, as on
    madhouse-debian."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", "show", "bench.slice", "-p",
                            "LoadState"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return "LoadState=loaded" in r.stdout


def test_nested_placement():
    """A7 with the chain runner's placement (the charge-interposer
    reviewer, 2026-11-12, after its run 2548): a suite run started by the
    acceptance runner, in the environment it gives a suite run
    (compile.machine_env), places its group in the settings' slice and
    reads it back: the nested record's "placement" event for group a says
    bench.slice, ok, and every case runs. Before compile.USER_BUS_VARS, the
    nested runner's systemd-run --user could not reach the user manager,
    the read-back was None and a/placement FAILed. Linux with the user's
    bench.slice only; elsewhere it checks nothing and says so."""
    if not _user_bench_slice():
        print("      test_nested_placement: not on Linux with a user bench.slice; "
              "nothing checked")
        return
    # only the nested suite's group "a" is placed, by name: the outer chain
    # check that starts the suite run ("emu") is not, as in a real
    # acceptance (a placed check is given the user bus by the chain runner
    # itself, which would hide the gap)
    f = Fixture(rerun=False, placement={"groups": {"a": "bench.slice"}})
    _t, rec = f.record(f.rid)
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert "a/placement" not in v, v
    assert v == {"a/ok": "PASS", "a/flaky": "FAIL", "a/tree": "PASS"}, v
    with open(os.path.join(f.runs, f.rid, "progress.jsonl"), encoding="utf-8") as fh:
        ev = [json.loads(ln) for ln in fh if '"placement"' in ln]
    grp = [e for e in ev if e.get("group") == "a"]
    assert len(grp) == 1 and grp[0]["ok"] is True and \
        grp[0]["read_back"] == "bench.slice", grp
    assert {r["slice"] for r in rec["results"]} == {"bench.slice"}, rec["results"]


def test_nested_from_hold():
    """A7 under the bench lock, with shared/bench SPEC section 6's
    --from-hold (user, 2026-11-16; the charge-interposer reviewer: the
    acceptance takes the lock through the chain runner, A1, so it passes
    --from-hold through): while the role holds the bench by hand under a
    session's PID, a suite run whose first run needs the bench is refused
    without it, and with it starts from that hold (the release's reads "not
    read: handed to run <id>", the take for the run under the nested run's
    own PID). A suite run that needs no bench takes no lock and is given
    nothing; the hold stays the session's. nested.json's from_hold records
    the PID and whether the run was given it. Run in this process, so the
    made-up canmain check (_canmain_fixture) applies."""
    import _canmain_fixture
    from vtrux_bench import bench_lock as BL
    from vtrux_bench.run.lock import LockError
    from vtrux_bench.run.model import Check, Group, Suite
    role = "tester (gen-inhibit)"
    N[0] += 1
    d = os.path.join(BASE, "fh%d" % N[0])
    os.makedirs(os.path.join(d, "runs"))
    log = os.path.join(d, "bench-log.jsonl")
    BL.init(log, role)
    settings = dict(runs_root=os.path.join(d, "runs"),
                    hosts={"local": {"python": [sys.executable, "-B"]}},
                    bench_lock_log=log, canmain_check=_canmain_fixture.install(d))
    bench = Suite(name="proj-bench", role=role, groups=[
        Group("b", [Check("ok", ["-c", "pass"], python=True)], needs_lock=True)])
    emu = Suite(name="proj-emu", role=role, groups=[
        Group("a", [Check("ok", ["-c", "pass"], python=True)])])
    quiet = lambda s: None                                  # noqa: E731

    def events():
        with open(log, encoding="utf-8") as f:
            return [json.loads(x) for x in f.read().splitlines()]
    sess = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(300)"])
    try:
        BL.take(log, role, sess.pid)
        n = len(events())
        try:
            NS.run(bench, settings, dict(settings), "acc1-bench", out=quiet)
        except LockError:
            pass
        else:
            raise AssertionError("a bench suite run ran under the session's hold "
                                 "without --from-hold")
        assert len(events()) == n, "the refused run appended"
        res = NS.run(bench, settings, dict(settings), "acc2-bench", out=quiet,
                     from_hold=sess.pid)
        assert res["exit"] == 0 and res["from_hold"] == {"pid": sess.pid,
                                                         "given": True}, res
        rid = res["first"]["run_id"]
        new = events()[n:]
        assert [(e["event"], e.get("run"), e.get("pid")) for e in new[:2]] == [
            ("release", None, None), ("take", rid, os.getpid())], new
        assert new[0]["boards"] == {b: "not read: handed to run %s" % rid
                                    for b in BL.BOARDS}, new[0]
        BL.take(log, role, sess.pid)
        res = NS.run(emu, settings, dict(settings), "acc3-emu", out=quiet,
                     from_hold=sess.pid)
        assert res["exit"] == 0 and res["from_hold"] == {"pid": sess.pid,
                                                         "given": False}, res
        h = BL.read_state(log)["holder"]
        assert (h["who"], h["pid"], h["run"]) == (role, sess.pid, None), h
    finally:
        sess.kill()
        sess.wait()


# ---- the hold passed from run to run (the gen-inhibit reviewer, 2026-11-16) --
ROLE = "tester (gen-inhibit)"
OTHER = "tester (charge-interposer)"
FLAKY_AT = ("import os, sys; p = os.path.join(r'%s', 'seen'); "
            "ok = os.path.exists(p); open(p, 'w').close(); sys.exit(0 if ok else 1)")


def _lock_env(tag):
    """A scratch bench log initialised for ROLE, and run settings using it
    with the made-up canmain check (this process only)."""
    import _canmain_fixture
    from vtrux_bench import bench_lock as BL
    N[0] += 1
    d = os.path.join(BASE, "%s%d" % (tag, N[0]))
    os.makedirs(os.path.join(d, "runs"))
    log = os.path.join(d, "bench-log.jsonl")
    BL.init(log, ROLE)
    return d, log, dict(runs_root=os.path.join(d, "runs"),
                        hosts={"local": {"python": [sys.executable, "-B"]}},
                        bench_lock_log=log, canmain_check=_canmain_fixture.install(d))


def _hold_suite(name, d, bench_flaky=False, logic_flaky=False, slow_bench=False):
    """A suite of ROLE: group b needs the bench, group a does not; each may
    hold a case that fails the first time it runs (a marker in a folder of
    its own under d) and passes after. slow_bench: b's first case takes
    1.5 s, so group a's results (another lane) are in before the bench lane
    ends and releases."""
    from vtrux_bench.run.model import Check, Group, Suite

    def flaky(tag):
        p = os.path.join(d, "%s-%s" % (name, tag))
        os.makedirs(p)
        return Check("flaky", ["-c", FLAKY_AT % p], python=True)
    b = [Check("ok", ["-c", "import time; time.sleep(1.5)" if slow_bench else "pass"],
               python=True)] + ([flaky("b")] if bench_flaky else [])
    a = [Check("ok", ["-c", "pass"], python=True)] + ([flaky("a")] if logic_flaky else [])
    return Suite(name=name, role=ROLE, groups=[Group("b", b, needs_lock=True),
                                               Group("a", a)])


def _events(log):
    with open(log, encoding="utf-8") as f:
        return [json.loads(x) for x in f.read().splitlines()]


def _holder(log):
    from vtrux_bench import bench_lock as BL
    h = BL.read_state(log)["holder"]
    return None if h is None else (h["who"], h["pid"], h["run"])


def _sleeper():
    return subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(300)"])


def test_nested_keep_lock():
    """--keep-lock under --from-hold (the gen-inhibit reviewer, 2026-11-16:
    the bench is never free between two suite runs that need it): while
    another role waits for the bench (bench_lock wait, polling every
    0.05 s), two bench suite runs in turn under one session's hold, the
    first with --from-hold and --keep-lock, the second with --from-hold
    alone. The log shows the first run's handed release and take, its end
    release and the take under the session's PID in one step, the second
    run's handed release and take, and its end release; only then the
    waiter's withdraw and take. Each release but the last is followed at
    once by a take of the role, so the lock was never free. nested.json's
    keep_lock records whom each handed the lock to. --keep-lock without
    --from-hold, or with another PID, is refused with nothing appended."""
    import threading
    from vtrux_bench import bench_lock as BL
    from vtrux_bench.run.model import SuiteError
    d, log, settings = _lock_env("kl")
    quiet = lambda s: None                                  # noqa: E731
    sess, other = _sleeper(), _sleeper()
    stop = threading.Event()
    got = {}
    waiter = threading.Thread(target=lambda: got.update(r=BL.wait(
        log, OTHER, other.pid, "after the acceptance", poll=0.05, timeout=120,
        me=os.getpid(), stopped=stop.is_set, out=quiet)))
    try:
        BL.take(log, ROLE, sess.pid)
        n = len(_events(log))
        for fh, kl in ((None, sess.pid), (sess.pid, other.pid)):
            try:
                NS.run(_hold_suite("proj-bench-x%d" % kl, d), settings, dict(settings),
                       "acc-x", out=quiet, from_hold=fh, keep_lock=kl)
            except SuiteError:
                pass
            else:
                raise AssertionError("--keep-lock %r with --from-hold %r was not refused"
                                     % (kl, fh))
            assert len(_events(log)) == n, "a refused --keep-lock appended"
        waiter.start()
        end = time.monotonic() + 10
        while not [q for q in BL.read_state(log)["queue"] if q["who"] == OTHER]:
            assert time.monotonic() < end, "the waiter's request was not queued"
            time.sleep(0.02)
        n = len(_events(log))
        r1 = NS.run(_hold_suite("proj-bench-1", d), settings, dict(settings), "acc-b1",
                    out=quiet, from_hold=sess.pid, keep_lock=sess.pid)
        assert r1["exit"] == 0 and r1["keep_lock"] == {
            "pid": sess.pid, "first": sess.pid, "rerun": None}, r1
        assert _holder(log) == (ROLE, sess.pid, None), _holder(log)
        r2 = NS.run(_hold_suite("proj-bench-2", d), settings, dict(settings), "acc-b2",
                    out=quiet, from_hold=sess.pid)
        assert r2["exit"] == 0 and r2["keep_lock"] == {
            "pid": None, "first": None, "rerun": None}, r2
        waiter.join(30)
        assert got.get("r", (None,))[0] == 0, "the waiter did not take: %r" % (got,)
        ev = _events(log)[n:]
        rows = [(e["event"], e.get("who"), e.get("pid"), e.get("run")) for e in ev]
        ids = (r1["first"]["run_id"], r2["first"]["run_id"])
        me = os.getpid()
        want = [("release", ROLE, None, None), ("take", ROLE, me, ids[0]),
                ("release", ROLE, None, None), ("take", ROLE, sess.pid, None),
                ("release", ROLE, None, None), ("take", ROLE, me, ids[1]),
                ("release", ROLE, None, None),
                ("withdraw", OTHER, None, None), ("take", OTHER, other.pid, None)]
        assert [(a, b, c if a == "take" else None, r if a == "take" else None)
                for a, b, c, r in rows] == want, rows
        assert ev[0]["boards"] == BL.handed_reads(ids[0]) and \
            ev[4]["boards"] == BL.handed_reads(ids[1]), (ev[0], ev[4])
    finally:
        stop.set()
        if waiter.is_alive() or waiter.ident is not None:
            waiter.join(10)
        for p in (sess, other):
            p.kill()
            p.wait()


def test_nested_rerun_hold():
    """The re-run of flagged cases under a hold (the gen-inhibit reviewer,
    2026-11-16: it does the same as the first run). A flaky bench case,
    --from-hold, no --keep-lock: the first run hands the lock back to the
    session at its end, the re-run starts from that hold and releases the
    bench. With --keep-lock the re-run hands it back too. Nothing flagged:
    the first run releases (no re-run, so nothing to keep it for). Only a
    case in a group that needs no bench flagged: the first run releases,
    and the re-run takes no lock. nested.json's keep_lock says whom each
    run handed the lock to."""
    d, log, settings = _lock_env("rh")
    quiet = lambda s: None                                  # noqa: E731
    sess = _sleeper()
    from vtrux_bench import bench_lock as BL
    try:
        P = sess.pid
        BL.take(log, ROLE, P)
        n = len(_events(log))
        res = NS.run(_hold_suite("proj-a", d, bench_flaky=True), settings, dict(settings),
                     "acc-a", rerun=True, out=quiet, from_hold=P)
        assert res["flagged"] == [{"check": "b/flaky", "first": "FAIL", "rerun": "PASS"}], res
        assert res["keep_lock"] == {"pid": None, "first": P, "rerun": None}, res
        ids = (res["first"]["run_id"], res["rerun"]["run_id"])
        rows = [(e["event"], e.get("pid"), e.get("run")) for e in _events(log)[n:]]
        me = os.getpid()
        assert [(a, p if a == "take" else None, r if a == "take" else None)
                for a, p, r in rows] == [
            ("release", None, None), ("take", me, ids[0]), ("release", None, None),
            ("take", P, None), ("release", None, None), ("take", me, ids[1]),
            ("release", None, None)], rows
        assert _holder(log) is None, _holder(log)
        BL.take(log, ROLE, P)
        res = NS.run(_hold_suite("proj-b", d, bench_flaky=True), settings, dict(settings),
                     "acc-b", rerun=True, out=quiet, from_hold=P, keep_lock=P)
        assert res["keep_lock"] == {"pid": P, "first": P, "rerun": P}, res
        assert _holder(log) == (ROLE, P, None), _holder(log)
        res = NS.run(_hold_suite("proj-c", d), settings, dict(settings), "acc-c",
                     rerun=True, out=quiet, from_hold=P)
        assert res["rerun"] is None and res["keep_lock"] == {
            "pid": None, "first": None, "rerun": None}, res
        assert _holder(log) is None, "nothing flagged, yet the hold was kept: %r" % (
            _holder(log),)
        BL.take(log, ROLE, P)
        res = NS.run(_hold_suite("proj-d", d, logic_flaky=True, slow_bench=True), settings,
                     dict(settings), "acc-d", rerun=True, out=quiet, from_hold=P)
        assert res["flagged"] == [{"check": "a/flaky", "first": "FAIL", "rerun": "PASS"}], res
        with open(os.path.join(res["first"]["dir"], "progress.jsonl"), encoding="utf-8") as fh:
            prog = [json.loads(x) for x in fh.read().splitlines()]
        i_flaky = [i for i, x in enumerate(prog) if x.get("type") == "result"
                   and x.get("check") == "a/flaky"]
        i_rel = [i for i, x in enumerate(prog) if x.get("kind") == "lock-release"]
        assert i_flaky and i_rel and i_flaky[0] < i_rel[0], (
            "a/flaky's result was not in before the release, so this case checks "
            "nothing", i_flaky, i_rel)
        assert res["keep_lock"] == {"pid": None, "first": None, "rerun": None}, res
        assert _holder(log) is None, "a re-run needing no bench kept the hold: %r" % (
            _holder(log),)
    finally:
        sess.kill()
        sess.wait()


TESTS = [test_nested_record, test_rerun_new_record, test_first_run_decides,
         test_nested_placement, test_nested_from_hold, test_nested_keep_lock,
         test_nested_rerun_hold]

MUTANTS = [
    (NS, "from_hold_bench", test_nested_from_hold),
    (NS, "from_hold_bench_only", test_nested_from_hold),
    (NS, "keep_lock_passed", test_nested_keep_lock),
    (NS, "keep_same_pid", test_nested_keep_lock),
    (NS, "keep_for_rerun", test_nested_rerun_hold),
    (NS, "keep_only_bench_rerun", test_nested_rerun_hold),
    (NS, "rerun_from_hold", test_nested_rerun_hold),
    (E, "keep_lock_if", test_nested_rerun_hold),
    (C, "user_bus", test_nested_placement),
    (NS, "nested_masked", test_nested_record),
    (NS, "rerun_new_record", test_rerun_new_record),
    (NS, "rerun_only_flagged", test_rerun_new_record),
    (NS, "first_decides", test_first_run_decides),
    (ST, "tree_settings", test_nested_record),
]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    can_fail = "--can-fail" in argv
    bad = 0
    try:
        if not can_fail:
            for t in TESTS:
                out = outcome_of(t)
                print("%s  %-26s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        missing = sorted(set(NS.RULES) - {k for m, k, _t in MUTANTS if m is NS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        here = _user_bench_slice()
        for mod, k, t in MUTANTS:
            if t is test_nested_placement and not here:
                print("NOT RUN (other env)  %s off: %s needs Linux with a user "
                      "bench.slice" % (k, t.__name__), flush=True)
                caught += 1
                continue
            mod.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                mod.RULES[k] = True
            print("%s  %-18s off: %-26s %s" % (
                "ok  " if out else "FAIL", k, t.__name__,
                ("fails (" + out[:110] + ")") if out else
                "STILL PASSES -- the test checks nothing"), flush=True)
            caught += out is not None
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 1 if bad or caught < len(MUTANTS) else 0
    finally:
        S.remove(BASE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
