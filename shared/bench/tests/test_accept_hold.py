"""Tests of an acceptance's hold on the bench (vtrux_bench/accept/hold.py
and run.py): ACCEPT-SPEC A18, shared/bench SPEC sections 3, 6 and 8 (user,
2026-11-17). No hardware: a bench log of its own per test, the fixture
project of tests/test_accept_run.py, and sleeping child processes standing
in for the other roles' sessions and waiters.

    python -B shared/bench/tests/test_accept_hold.py            every test
    python -B shared/bench/tests/test_accept_hold.py --can-fail  the control
        first (every test unchanged, which must pass), then each test again
        with a rule it guards switched off (hold.RULES, and run.RULES'
        hold rules); each must then fail
    python -B shared/bench/tests/test_accept_hold.py NAME...     some tests

The fixture's bench suite (role "tester (gen-inhibit)") has two bench
groups, b and c; b pauses its run once (a PAUSE file in its run folder)
when the settings' pause_dir is set. Each test's acceptance runs in this
process (test_accept_run.Run), so the acceptance runner's PID is this
test's, except test_hold_signal's, which runs it in a child process.
Everything is in this run's own folder under the worktree's scratch/
(tests/_scratch.py), removed at the end.
"""

import glob
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_accept_run as TAR                        # noqa: E402
from vtrux_bench import bench_lock as BL             # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import hold as HO            # noqa: E402
from vtrux_bench.accept import run as RUN            # noqa: E402

ROLE = "tester (gen-inhibit)"            # the fixture's bench suite's role
OTHER = "tester (charge-interposer)"     # another role, waiting or holding
BL.WAIT_POLL_S = 0.1                     # the hold's poll here (default 2 s)
NOT_READ = {b: "not read: a test's hold" for b in BL.BOARDS}

# group b of the bench suite pauses its own run once, when pause_dir is set,
# writing its run folder into pause_dir/once for the test to find
PAUSE = ("import os; m = r'{pause_dir}'; d = r'{run_dir}'; "
         "f = os.path.join(m, 'once') if m else ''; "
         "(m and not os.path.exists(f)) and (open(f, 'w').write(d), "
         "open(os.path.join(d, 'PAUSE'), 'w').close())")
_OLD = ("BENCH = Suite(name='proj-bench', role='tester (gen-inhibit)', groups=[\n"
        "    Group('b', [Check('ok', ['-c', 'pass'], python=True)], needs_lock=True)])\n")
assert TAR.FILES["proj/suite.py"].count(_OLD) == 1
TAR.FILES["proj/suite.py"] = TAR.FILES["proj/suite.py"].replace(_OLD, (
    "PAUSE = %r\n"
    "BENCH = Suite(name='proj-bench', role='tester (gen-inhibit)', groups=[\n"
    "    Group('b', [Check('ok', ['-c', PAUSE], python=True)], needs_lock=True),\n"
    "    Group('c', [Check('ok', ['-c', 'pass'], python=True)], needs_lock=True)])\n")
    % PAUSE)

SR = '\n[[suite_run]]\nid = "%s"\ntree = "base"\nsuite = "%s"\n'
# the list's base has suite run emu (no bench); these follow it
BENCHES = SR % ("bench1", "bench") + SR % ("bench2", "bench") + \
    'rerun_flagged = true\n' + SR % ("emu2", "emulated")
BETWEEN = SR % ("bench1", "bench") + SR % ("emu2", "emulated") + SR % ("bench2", "bench")
SLEEP = [sys.executable, "-B", "-c", "import time; time.sleep(900)"]


# ---- helpers -----------------------------------------------------------------
def bench(name, canmain=True, pause=False):
    """A folder with a bench log of its own (initialised by ROLE) and the
    machine settings that name it: canmain_check probe "absent" on an
    interface and serial no machine has (or none: a nested bench run then
    refuses to start), pause_poll_s 0.1, pause_dir set or empty."""
    TAR.N[0] += 1
    d = os.path.join(TAR.BASE, "%s%d" % (name, TAR.N[0]))
    os.makedirs(d)
    log = os.path.join(d, "bench-log.jsonl")
    BL.init(log, ROLE)
    m = dict(bench_lock_log=log, pause_poll_s=0.1, pause_dir="")
    if pause:
        m["pause_dir"] = os.path.join(d, "pause")
        os.makedirs(m["pause_dir"])
    if canmain:
        serial = os.path.join(d, "canmain_serial.md")
        with open(serial, "w", encoding="utf-8") as f:
            f.write("- canmain serial: `TESTNOSUCHCANMAIN01`\n")
        m["canmain_check"] = {"probe": "absent", "iface": "cmnosuch0",
                              "reason": "made-up interface and serial, on no machine",
                              "serial_file": serial}
    return d, log, m


def events(log, n=1):
    with open(log, encoding="utf-8") as f:
        return [json.loads(x) for x in f.read().splitlines()[n:]]


def watch(log, pred, timeout=120.0, stop=None):
    """The first event (after init) for which pred holds, waiting for it;
    None on timeout or stop."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and not (stop and stop.is_set()):
        for e in events(log):
            if pred(e):
                return e
        time.sleep(0.02)
    return None


def is_take(e, who, pid=None, run=None):
    return (e["event"] == "take" and e["who"] == who
            and (pid is None or e.get("pid") == pid)
            and (run is None or bool(e.get("run")) == run))


def chain_dirs():
    return glob.glob(os.path.join(TAR.BASE, "r*", "runs", "accept_*", "chain", "*", ""))


def stop_chain(text):
    """A STOP file in the running acceptance's chain run folder."""
    for d in chain_dirs():
        if not os.path.exists(os.path.join(d, "STOP")):
            with open(os.path.join(d, "STOP"), "w", encoding="utf-8") as f:
                f.write(text)


def results(r, group):
    """The acceptance's chain record's results of a group."""
    found = []
    for d in glob.glob(os.path.join(r.rec, "chain", "*", "record.json")):
        with open(d, encoding="utf-8") as f:
            res = [x for x in json.load(f)["results"] if x["group"] == group]
        if res:
            found.append((d, res))
    assert len(found) == 1, (group, os.listdir(os.path.join(r.rec, "chain")))
    return found[0][1]


def hold_json(r):
    with open(os.path.join(r.rec, "bench_hold.json"), encoding="utf-8") as f:
        return json.load(f)


def rows(evs, P):
    """(event, who, pid shown as "P" for the acceptance runner's or "x"
    for another, whether it has a run)."""
    def shown(e):
        if e["event"] not in ("take", "request") or e.get("pid") is None:
            return None
        return "P" if e["pid"] == P else "x"
    return [(e["event"], e["who"], shown(e), bool(e.get("run"))) for e in evs]


class Procs:
    """Sleeping children standing in for sessions and waiters."""

    def __init__(self, n):
        self.p = [subprocess.Popen(SLEEP) for _ in range(n)]

    def __enter__(self):
        return self.p

    def __exit__(self, *a):
        for p in self.p:
            p.kill()
            p.wait()


def tail(r):
    return r.rec and r.read("console.txt")[-2500:]


# A18's sequence for two bench parts under the runner's own hold: the
# request, the take that ends the wait, bench1 from the hold, its end
# handing the hold back, bench2 from the hold, its end releasing
OWN_TWO = [("request", ROLE, "P", False), ("withdraw", ROLE, None, False),
           ("take", ROLE, "P", False),
           ("release", ROLE, None, False), ("take", ROLE, "x", True),
           ("release", ROLE, None, False), ("take", ROLE, "P", False),
           ("release", ROLE, None, False), ("take", ROLE, "x", True),
           ("release", ROLE, None, False)]


# ---- the tests ----------------------------------------------------------------
def test_hold_queued():
    """A18 (and SPEC sections 3, 6): with the bench log and no --from-hold,
    the list runs emu, bench1, bench2 and emu2, and nobody holds the bench.
    Nothing holds the lock until bench1: the role's first line in the log
    is the acceptance's request, under the acceptance runner's PID, after
    emu's nested run has ended. Then the take that ends the wait (no run,
    that PID), bench1 from it, the hold handed back, bench2 from it, and
    bench2's release. Another role that queues once the hold is taken
    (bench_lock wait) takes the bench only after that last release. The
    record's bench_hold.json and README name the request, the take, every
    hand-over and the release, each with its log time."""
    d, log, m = bench("hq")
    P = os.getpid()
    stop, got = threading.Event(), {}
    with Procs(2) as (other, me):
        def waiter():
            if watch(log, lambda e: is_take(e, ROLE, P, False), 120, stop) is None:
                return
            got["r"] = BL.wait(log, OTHER, other.pid, "after the acceptance", poll=0.05,
                               timeout=300, me=me.pid, stopped=stop.is_set,
                               out=lambda s: None)
        t = threading.Thread(target=waiter)
        t.start()
        try:
            r = TAR.Run(extra=BENCHES, machine=m)
            assert r.rc == 0, (r.rc, tail(r))
            t.join(60)
            assert got.get("r", (None,))[0] == 0, "the waiter did not take: %r" % (got,)
        finally:
            stop.set()
            t.join(10)
        evs = events(log)
        rw = rows(evs, P)
        mine = [x for x in rw if x[1] == ROLE]
        assert mine == OWN_TWO, mine
        assert rw[-2:] == [("withdraw", OTHER, None, False), ("take", OTHER, "x", False)], rw
        req = [e for e in evs if e["who"] == ROLE][0]
        # emu's nested run ended before the request
        with open(os.path.join(glob.glob(os.path.join(r.rec, "chain", "*", "emu", "run",
                                                      "nested.json"))[0]),
                  encoding="utf-8") as f:
            emu_dir = json.load(f)["first"]["dir"]
        with open(os.path.join(emu_dir, "record.json"), encoding="utf-8") as f:
            emu = json.load(f)
        ended = emu.get("ended") or emu.get("finished")
        assert ended and str(ended)[:19] <= req["ts"][:19], (ended, req["ts"])
        h = hold_json(r)
        assert h["mode"] == "the runner's own" and h["pid"] == P and \
            h["parts"] == ["bench1", "bench2"] and h["not_run"] is None, h
        assert [(e["event"], e["who"]) for e in h["lines"] if e["who"] == ROLE] == \
            [(a, b) for a, b, _c, _d in OWN_TWO], h["lines"]
        assert all(e.get("ts") for e in h["lines"]), h["lines"]
        readme = r.read("README.md")
        assert "## The bench hold (A18)" in readme and req["ts"] in readme, readme[-1500:]
        assert "bench hold: waiting for the bench at suite run bench1" in r.read("console.txt")


def test_hold_no_timeout():
    """A18: the wait at the first bench part has no timeout. Another role
    holds the bench when the acceptance reaches bench1 and releases it
    2.5 s after the acceptance's request is queued; the acceptance takes it
    then, and passes."""
    d, log, m = bench("nt")
    P = os.getpid()
    stop = threading.Event()
    with Procs(1) as (holder,):
        BL.take(log, OTHER, holder.pid)

        def release_later():
            if watch(log, lambda e: e["event"] == "request" and e["who"] == ROLE,
                     120, stop) is None:
                return
            time.sleep(2.5)
            BL.release(log, OTHER, NOT_READ, note="the other role done")
        t = threading.Thread(target=release_later)
        t.start()
        try:
            r = TAR.Run(extra=BENCHES, machine=m)
        finally:
            stop.set()
            t.join(10)
        assert r.rc == 0, (r.rc, tail(r))
        rw = rows(events(log), P)
        rel = rw.index(("release", OTHER, None, False))
        assert ("take", ROLE, "P", False) in rw[rel:], rw
        assert ("withdraw", ROLE, None, False) in rw[rel:], rw


def test_hold_stop_waiting():
    """A18: a STOP in the acceptance's chain run folder while it waits
    withdraws its request; bench1 and bench2 are NOT-RUN, "bench not
    taken", the role never takes, and the record is closed (ACCEPTANCE
    FAIL). The other role holds the bench throughout, and lets go after
    20 s only so that a mutant that keeps waiting cannot hang the test."""
    d, log, m = bench("sw")
    stop = threading.Event()
    with Procs(1) as (holder,):
        BL.take(log, OTHER, holder.pid)

        def stopper():
            if watch(log, lambda e: e["event"] == "request" and e["who"] == ROLE,
                     120, stop) is None:
                return
            time.sleep(0.3)
            stop_chain("test: stop while waiting")
            if not stop.wait(20):
                BL.release(log, OTHER, NOT_READ, note="the test lets go")
        t = threading.Thread(target=stopper)
        t.start()
        try:
            r = TAR.Run(extra=BENCHES, machine=m)
        finally:
            stop.set()
            t.join(30)
        assert r.rc == 1 and os.path.exists(os.path.join(r.rec, "SHA256SUMS")), (r.rc, tail(r))
        mine = [(e["event"], e.get("note", "")) for e in events(log) if e["who"] == ROLE]
        assert [x[0] for x in mine] == ["request", "withdraw"], mine
        assert "stopped" in mine[1][1], mine
        for g in ("bench1", "bench2"):
            res = results(r, g)
            assert res and all(x["verdict"] == "NOT-RUN" and "bench not taken: stopped (STOP"
                               in x["detail"] for x in res), (g, res)


def child():
    """test_hold_signal's child: one acceptance under the parent's settings
    and rules, printing its scratch folder first."""
    a = json.loads(os.environ["VTRUX_HOLD_CHILD"])
    for k in a["off"]:
        HO.RULES[k] = False
    print("BASE " + TAR.BASE, flush=True)
    r = TAR.Run(extra=BENCHES, machine=a["machine"])
    print("RC %s" % r.rc, flush=True)


def test_hold_signal():
    """A18: the runner's exit while it waits withdraws its request. The
    acceptance runs in a child process; while it waits (another role holds
    the bench), SIGTERM ends it by that signal, and the log shows its
    request withdrawn and no take by its role."""
    if not hasattr(signal, "SIGHUP"):
        print("NOT RUN (other env): POSIX signals")
        return
    d, log, m = bench("sg")
    off = [k for k, v in HO.RULES.items() if not v]
    with Procs(1) as (holder,):
        BL.take(log, OTHER, holder.pid)
        env = dict(os.environ, VTRUX_HOLD_CHILD=json.dumps({"machine": m, "off": off}))
        c = subprocess.Popen([sys.executable, "-B", "-c",
                              "import sys; sys.path[:0] = [%r]; import test_accept_hold "
                              "as T; T.child()" % HERE], env=env, cwd=HERE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        base = None
        try:
            e = watch(log, lambda e: e["event"] == "request" and e["who"] == ROLE, 180)
            assert e is not None and e.get("pid") == c.pid, e
            time.sleep(0.3)
            c.send_signal(signal.SIGTERM)
            out = c.communicate(timeout=60)[0]
        finally:
            if c.poll() is None:
                c.kill()
                out = c.communicate()[0]
            for ln in (out or "").splitlines():
                if ln.startswith("BASE "):
                    base = ln[5:]
            if base and os.path.isdir(base):
                S.remove(base)
        assert c.returncode == -signal.SIGTERM, (c.returncode, (out or "")[-1500:])
        mine = [(e["event"], e.get("note", "")) for e in events(log) if e["who"] == ROLE]
        assert [x[0] for x in mine] == ["request", "withdraw"], mine
        assert BL.read_state(log)["holder"]["who"] == OTHER


def test_hold_refused():
    """A18: a request the log refuses (the role already has a request
    queued under a running pid, another of its waits) leaves bench1 and
    bench2 NOT-RUN with the refusal; the role never takes, and the record is
    closed (ACCEPTANCE FAIL)."""
    d, log, m = bench("rf")
    with Procs(1) as (w,):
        BL.write(log, {"event": "request", "who": ROLE, "pid": w.pid,
                       "note": "another wait of this role"})
        n = len(events(log, 0))
        r = TAR.Run(extra=BENCHES, machine=m)
        assert r.rc == 1 and os.path.exists(os.path.join(r.rec, "SHA256SUMS")), (r.rc, tail(r))
        assert events(log, n) == [], events(log, n)
        for g in ("bench1", "bench2"):
            res = results(r, g)
            assert res and all(x["verdict"] == "NOT-RUN" and "bench not taken: the bench log "
                               "refused the request" in x["detail"] for x in res), (g, res)


def test_hold_not_handed_back():
    """A18: bench1 does not take over the runner's own hold (its nested run
    refuses to start: the settings have no canmain_check). bench2 is then
    NOT-RUN, naming bench1, not started; and the runner releases its hold
    before the close, each board "not read: <why>", the why naming bench1.
    The lock is free at the end."""
    d, log, m = bench("nh", canmain=False)
    P = os.getpid()
    r = TAR.Run(extra=BENCHES, machine=m)
    assert r.rc == 1 and os.path.exists(os.path.join(r.rec, "SHA256SUMS")), (r.rc, tail(r))
    res = results(r, "bench2")
    assert res and all(x["verdict"] == "NOT-RUN" and "the hold was not handed back by suite "
                       "run bench1" in x["detail"] for x in res), res
    evs = [e for e in events(log) if e["who"] == ROLE]
    assert rows(evs, P) == OWN_TWO[:3] + [("release", ROLE, None, False)], rows(evs, P)
    last = evs[-1]
    assert all(v.startswith("not read: suite run bench1 left no nested record")
               for v in last["boards"].values()), last
    assert "left at its end" in last.get("note", ""), last
    assert BL.read_state(log)["holder"] is None
    assert hold_json(r)["release_before_close"], hold_json(r)


def test_hold_from_hold_not_handed_back():
    """A18 with --from-hold (the charge-interposer reviewer, 2026-11-17,
    ruling (a)): the session's hold is the hold, and the runner does not
    queue. bench1 does not take it over (no canmain_check), so bench2 is
    NOT-RUN, naming bench1, not started; and the session's hold stays with
    the session: nothing is appended to the log at all."""
    d, log, m = bench("fh", canmain=False)
    with Procs(1) as (sess,):
        BL.take(log, ROLE, sess.pid)
        n = len(events(log, 0))
        r = TAR.Run(extra=BENCHES, machine=m, from_hold=sess.pid)
        assert r.rc == 1 and os.path.exists(os.path.join(r.rec, "SHA256SUMS")), (r.rc, tail(r))
        res = results(r, "bench2")
        assert res and all(x["verdict"] == "NOT-RUN" and "the hold was not handed back by "
                           "suite run bench1" in x["detail"] for x in res), res
        assert events(log, n) == [], events(log, n)
        h = BL.read_state(log)["holder"]
        assert h["who"] == ROLE and h["pid"] == sess.pid and h["run"] is None, h
        assert hold_json(r)["mode"] == "--from-hold", hold_json(r)


def test_hold_stop_between():
    """A18: a STOP between bench parts (the list runs bench1, emu2, bench2;
    the STOP once bench1 has handed the hold back) leaves bench2 NOT-RUN,
    and the runner releases its hold before the close with each board's
    read from bench1's end (its release's board reads). The lock is free
    at the end."""
    d, log, m = bench("sb")
    P = os.getpid()
    stop = threading.Event()

    def stopper():
        end = time.monotonic() + 180
        while time.monotonic() < end and not stop.is_set():
            # the hold's take, and bench1's end handing it back
            if len([e for e in events(log) if is_take(e, ROLE, P, False)]) >= 2:
                stop_chain("test: stop between parts")
                return
            time.sleep(0.02)
    t = threading.Thread(target=stopper)
    t.start()
    try:
        r = TAR.Run(extra=BETWEEN, machine=m)
    finally:
        stop.set()
        t.join(10)
    assert r.rc == 1 and os.path.exists(os.path.join(r.rec, "SHA256SUMS")), (r.rc, tail(r))
    res = results(r, "bench2")
    assert res and all(x["verdict"] == "NOT-RUN" for x in res), res
    evs = [e for e in events(log) if e["who"] == ROLE]
    assert rows(evs, P) == OWN_TWO[:7] + [("release", ROLE, None, False)], rows(evs, P)
    bench1_end = evs[5]
    assert evs[-1]["boards"] == bench1_end["boards"], (evs[-1], bench1_end)
    assert "left at its end" in evs[-1].get("note", ""), evs[-1]
    assert BL.read_state(log)["holder"] is None


def test_hold_pause():
    """A18 with a pause (the gen-inhibit reviewer, 2026-11-17): bench1 pauses
    its run between its groups b and c, which frees the bench
    (free_paused). Another role, queued once the hold was taken, takes it
    then, as SPEC section 3 allows. The PAUSE file goes while that role
    still holds the bench: bench1's resume is a plain take, which retries
    (its record's resume-waiting) and does not queue (the role's only
    request is the acceptance's first). Once the other role releases,
    bench1 takes the bench back under its own PID and run, and its
    --keep-lock at its end puts the hold back under the acceptance
    runner's PID; bench2 starts from it, and releases at its end."""
    d, log, m = bench("pz", pause=True)
    P = os.getpid()
    stop, got = threading.Event(), {}
    with Procs(2) as (other, me):
        once = os.path.join(m["pause_dir"], "once")

        def unpause():
            with open(once, encoding="utf-8") as f:
                p = os.path.join(f.read(), "PAUSE")
            if os.path.exists(p):
                os.remove(p)

        def driver():
            try:
                if watch(log, lambda e: is_take(e, ROLE, P, False), 60, stop) is None:
                    return
                w = threading.Thread(target=lambda: got.update(r=BL.wait(
                    log, OTHER, other.pid, "during a pause", poll=0.05, timeout=300,
                    me=me.pid, stopped=stop.is_set, out=lambda s: None)))
                w.start()
                if watch(log, lambda e: is_take(e, OTHER), 60, stop) is None:
                    return
                unpause()
                time.sleep(1.0)                 # bench1's resume retries meanwhile
                BL.release(log, OTHER, NOT_READ, note="the other role done")
                w.join(10)
            finally:
                # never leave the run paused: a mutant must fail, not hang
                while not stop.is_set():
                    if os.path.exists(once):
                        unpause()
                    time.sleep(0.1)
        t = threading.Thread(target=driver)
        t.start()
        try:
            r = TAR.Run(extra=BENCHES, machine=m)
        finally:
            stop.set()
            t.join(30)
        assert r.rc == 0, (r.rc, tail(r))
        assert got.get("r", (None,))[0] == 0, got
        evs = events(log)
        rw = rows(evs, P)

        def at(row, after=0):
            return rw.index(row, after)
        fp = at(("free_paused", ROLE, None, True))
        ot = at(("take", OTHER, "x", False), fp)
        orl = at(("release", OTHER, None, False), ot)
        res = at(("take", ROLE, "x", True), orl)
        assert evs[res].get("note") == "resume", evs[res]
        first = [e for e in evs if is_take(e, ROLE, run=True)][0]
        assert (evs[res]["pid"], evs[res]["run"]) == (first["pid"], first["run"]), \
            (evs[res], first)
        back = at(("take", ROLE, "P", False), res)
        assert rw[back - 1] == ("release", ROLE, None, False), rw
        b2 = at(("take", ROLE, "x", True), back)
        assert "-bench2" in evs[b2]["run"], evs[b2]
        assert rw[-1] == ("release", ROLE, None, False), rw
        assert [x for x in rw if x[0] == "request" and x[1] == ROLE] == \
            [("request", ROLE, "P", False)], rw
        with open(glob.glob(os.path.join(r.rec, "chain", "*", "bench1", "run",
                                         "nested.json"))[0], encoding="utf-8") as f:
            b1 = json.load(f)["first"]["dir"]
        with open(os.path.join(b1, "progress.jsonl"), encoding="utf-8") as f:
            prog = f.read()
        assert '"resume-waiting"' in prog, prog[-1500:]


TESTS = [test_hold_queued, test_hold_no_timeout, test_hold_stop_waiting, test_hold_signal,
         test_hold_refused, test_hold_not_handed_back, test_hold_from_hold_not_handed_back,
         test_hold_stop_between, test_hold_pause]

# (module's RULES, rule, test)
MUTANTS = [
    (HO, "no_timeout", test_hold_no_timeout),
    (HO, "stop_withdraws", test_hold_stop_waiting),
    (HO, "signal_withdraws", test_hold_signal),
    (HO, "refused_not_run", test_hold_refused),
    (HO, "hold_back_check", test_hold_not_handed_back),
    (HO, "hold_back_check", test_hold_from_hold_not_handed_back),
    (HO, "release_left", test_hold_not_handed_back),
    (HO, "release_left", test_hold_stop_between),
    (HO, "release_own_only", test_hold_from_hold_not_handed_back),
    (HO, "boards_from_last", test_hold_stop_between),
    (HO, "record_events", test_hold_queued),
    (RUN, "hold_queued", test_hold_queued),
    (RUN, "hold_queued", test_hold_pause),
    (RUN, "from_hold_no_queue", test_hold_from_hold_not_handed_back),
    (RUN, "hold_recorded", test_hold_queued),
    (RUN, "keep_lock_chain", test_hold_pause),
]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    tests = [t for t in TESTS if not names or t.__name__ in names]
    bad = 0
    try:
        if not can_fail:
            for t in tests:
                out = outcome_of(t)
                print("%s  %-36s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(tests) - bad, len(tests)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        missing = sorted(set(HO.RULES) - {k for mod, k, _t in MUTANTS if mod is HO})
        missing += sorted(set(TAR.HOLD_RULES) - {k for mod, k, _t in MUTANTS if mod is RUN})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for mod, k, t in MUTANTS:
            mod.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                mod.RULES[k] = True
            print("%s  %-20s off: %-36s %s" % (
                "ok  " if out else "FAIL", k, t.__name__,
                ("fails (" + out[:110] + ")") if out else
                "STILL PASSES -- the test checks nothing"), flush=True)
            caught += out is not None
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 1 if bad or caught < len(MUTANTS) else 0
    finally:
        S.remove(TAR.BASE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
