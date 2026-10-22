"""Tests of the bench-lock event log (vtrux_bench.bench_lock). No hardware.

    python -B shared/bench/tests/test_bench_lock.py            every test
    python -B shared/bench/tests/test_bench_lock.py --can-fail  every test
        again under each of its mutants (a broken copy of the rule it
        guards), each of which must make it FAIL
    python -B shared/bench/tests/test_bench_lock.py NAME...     some tests

Written from the specification, reverse-it firmware/bench-lock-spec.md
(user, 2026-10-21). Each test names the spec sections it checks (SPEC), and
each mutant breaks one rule of those sections.

Runs in native Windows Python only, the one environment bench_lock
implements (section 4); the logs it makes name this host as the bench.
Other hosts and environments are simulated by patching this_host and
this_env, or by writing an init line by hand.

A mutant is a text edit of bench_lock.py's source, executed into the module
in place of the original; an edit whose text is not found exactly once is
reported NOT APPLIED and counts as a failure, never a silent no-op. Logs go
to <tempdir>/vtrux-bench-tests/bench_lock/, never the real
firmware/bench-log.jsonl.
"""

import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vtrux_bench import bench_lock as B             # noqa: E402

SCRATCH = os.path.join(tempfile.gettempdir(), "vtrux-bench-tests", "bench_lock")
SRC = os.path.join(os.path.dirname(HERE), "vtrux_bench", "bench_lock.py")
CI = "tester (charge-interposer)"
GI = "tester (gen-inhibit)"
GIM = "implementor (gen-inhibit)"
ME = os.getpid()
BD = {"wican": "_man 25f50ac0, read over HTTP 02:00",
      "x2": "756d6714, read off the wire 02:02"}
WSL_VERSION = ("Linux version 5.15.153.1-microsoft-standard-WSL2 "
               "(root@1234) (gcc 11.2.0) #1 SMP")


class Fail(Exception):
    pass


def check(cond, what):
    if not cond:
        raise Fail(what)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except B.Refused as e:
        return str(e)
    raise Fail("%s%r was not refused" % (fn.__name__, a[1:2]))


def folder(name):
    d = os.path.join(SCRATCH, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    return os.path.join(d, "bench-log.jsonl")


def fresh(name):
    p = folder(name)
    B.init(p, CI)
    return p


def hand_log(name, lines_):
    p = folder(name)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(l + "\n" for l in lines_))
    return p


def init_line(host=None, env="windows"):
    return json.dumps({"ts": "2026-10-21T03:00:00-07:00", "who": CI,
                       "event": "init", "host": host or socket.gethostname(),
                       "env": env})


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def alive(pid):
    return B.ENVS["windows"].pid_alive(pid)


def ev(kind, who, **k):
    return dict(k, event=kind, who=who)


def w(p, kind, who, **k):
    return B.write(p, ev(kind, who, **k))[0]


def lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = B.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class patched:
    """Patch bench_lock attributes for a with-block."""

    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = getattr(B, k)
            setattr(B, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(B, k, v)


# ---- the tests ----------------------------------------------------------

def test_take_release():
    """SPEC 3 take, release; 5 a refusal names the holder."""
    p = fresh("take_release")
    st = w(p, "take", CI, pid=ME, run="r1")
    check(st["holder"]["who"] == CI and st["holder"]["run"] == "r1", "take")
    msg = refused(B.write, p, ev("take", GI, pid=ME))
    check(CI in msg and "r1" in msg, "the refusal names the holder: %s" % msg)
    refused(B.write, p, ev("take", CI, pid=ME, run="r2"))
    refused(B.write, p, ev("release", GI, boards=BD))
    refused(B.write, p, ev("release", CI))
    st = w(p, "release", CI, boards=BD)
    check(st["holder"] is None, "release frees the lock")
    st = w(p, "take", GI, pid=ME)
    check(st["holder"]["who"] == GI and st["holder"]["run"] is None,
          "the other role takes a free lock, for no run")
    check(len(lines(p)) == 4, "init and three lines, refusals wrote none")


def test_boards():
    """SPEC 3 release, pause (board state known only if every board was
    read); 5 BOARD STATE UNKNOWN; 8 board reads; Ruled: every release reads
    both boards."""
    p = fresh("boards")
    st = B.read_state(p)
    check(not st["boards"]["known"], "unknown before any release")
    w(p, "take", CI, pid=ME, run="r1")
    for bad in ({"x2": BD["x2"]}, dict(BD, truck="?"), dict(BD, wican=" "),
                "x2 756d6714", None):
        refused(B.write, p, ev("release", CI, boards=bad))
        refused(B.write, p, ev("pause", CI, run="r1", boards=bad))
    st = w(p, "release", CI, boards=dict(BD, wican="not read: off the AP"))
    check(not st["boards"]["known"], "a release with an unread board")
    check(any(l.startswith(B.UNKNOWN) for l in B.status_lines(st)),
          "status says %s" % B.UNKNOWN)
    w(p, "take", CI, pid=ME, run="r2")
    st = w(p, "pause", CI, run="r2", boards=dict(BD, x2="Not read: COM3 busy"))
    check(not st["boards"]["known"], "a pause with an unread board")
    w(p, "take", CI, pid=ME, run="r2")
    st = w(p, "release", CI, boards=BD)
    check(st["boards"]["known"] and st["boards"]["read"] == BD,
          "a release with every board read")
    out = B.status_lines(st)
    check(not any(B.UNKNOWN in l for l in out)
          and any(BD["x2"] in l for l in out), "status shows the reads")
    refused(B.parse_boards, ["x2=a", "x2=b"])
    refused(B.parse_boards, ["x2"])
    check(B.parse_boards(["wican=not read: off AP", "x2=a=b"]) ==
          {"wican": "not read: off AP", "x2": "a=b"}, "parse_boards")


def test_pause_resume():
    """SPEC 3 pause, take after pause; 8 a role with a paused run."""
    p = fresh("pause_resume")
    w(p, "take", CI, pid=ME)
    refused(B.write, p, ev("pause", CI, run=None, boards=BD))
    w(p, "release", CI, boards=BD)
    w(p, "take", CI, pid=ME, run="r1")
    refused(B.write, p, ev("pause", CI, run="r9", boards=BD))
    refused(B.write, p, ev("pause", GI, run="r1", boards=BD))
    st = w(p, "pause", CI, run="r1", boards=BD)
    check(st["holder"] is None and st["paused"][CI]["run"] == "r1",
          "pause frees the lock and keeps the run")
    check(not B.held_by_run(st, CI, "r1"), "a paused run does not hold it")
    refused(B.write, p, ev("take", GI, pid=ME, run="r1"))
    w(p, "take", GI, pid=ME, run="g1")
    w(p, "release", GI, boards=BD)
    refused(B.write, p, ev("take", CI, pid=ME, run="r2"))
    refused(B.write, p, ev("take", CI, pid=ME))
    st = w(p, "take", CI, pid=ME, run="r1")
    check(B.held_by_run(st, CI, "r1") and not st["paused"], "resume")


def test_run_once():
    """SPEC 8 run ids are used once."""
    p = fresh("run_once")
    w(p, "take", CI, pid=ME, run="r1")
    w(p, "release", CI, boards=BD)
    refused(B.write, p, ev("take", CI, pid=ME, run="r1"))
    refused(B.write, p, ev("take", GI, pid=ME, run="r1"))


def test_held_by_run():
    """SPEC 6 its own role and its own run id."""
    st = B.new_state()
    check(not B.held_by_run(st, CI, "r1"), "free")
    st["holder"] = {"who": CI, "run": None, "pid": ME, "ts": "t"}
    check(not B.held_by_run(st, CI, "r1"), "a take for no run")
    check(not B.held_by_run(st, CI, None), "no run asked, none held")
    st["holder"]["run"] = "r1"
    check(B.held_by_run(st, CI, "r1"), "its own role and run")
    check(not B.held_by_run(st, GI, "r1"), "another role, the same run id")
    check(not B.held_by_run(st, CI, "r10"), "a longer id")
    check(not B.held_by_run(st, CI, "r"), "a prefix")
    st["holder"]["run"] = "r10"
    check(not B.held_by_run(st, CI, "r1"), "an id that prefixes the holder's")
    st["holder"]["who"] = "tester (charge-interposer)x"
    st["holder"]["run"] = "r1"
    check(not B.held_by_run(st, CI, "r1"), "a role that the asker prefixes")


def test_queue():
    """SPEC 3 request, withdraw, take ignores the queue; 8 request."""
    p = fresh("queue")
    w(p, "take", CI, pid=ME, run="r1")
    refused(B.write, p, ev("request", CI, note="more, 1 h"))
    refused(B.write, p, ev("request", GI))
    w(p, "request", GI, note="B6 session 3, 35 min")
    refused(B.write, p, ev("request", GI, note="again"))
    st = w(p, "request", GIM, note="bring-up, 2 h")
    check([q["who"] for q in st["queue"]] == [GI, GIM], "queue order")
    refused(B.write, p, ev("withdraw", CI))
    w(p, "pause", CI, run="r1", boards=BD)
    st = w(p, "take", GIM, pid=ME)
    check(st["holder"]["who"] == GIM, "take does not consult the queue")
    check([q["who"] for q in st["queue"]] == [GI],
          "take removes the taker's own request only")
    st = w(p, "withdraw", GI)
    check(st["queue"] == [], "withdraw")


def test_clear():
    """SPEC 3 clear; 4 clear checks the pid in the bench environment;
    8 PID checks fail safe, clear ends the cleared holder's run."""
    p = fresh("clear")
    w(p, "take", CI, pid=ME, run="r1")
    refused(B.write, p, ev("clear", GI, reason="crashed?"))
    check(len(lines(p)) == 2, "a refused clear wrote nothing")
    p = fresh("clear2")
    gone = dead_pid()
    check(not alive(gone), "a finished child is not alive")
    check(alive(ME), "this process is alive")
    w(p, "take", CI, pid=gone, run="r1")
    refused(B.write, p, ev("clear", GI))
    st = w(p, "clear", GI, reason="session crashed")
    check(st["holder"] is None and not st["boards"]["known"],
          "clear frees the lock and the boards are unknown")
    last = json.loads(lines(p)[-1])
    check(last["reason"] == "session crashed" and str(gone) in
          st["boards"]["why"], "clear records the reason and the holder")
    refused(B.write, p, ev("take", CI, pid=ME, run="r1"))
    refused(B.write, p, ev("clear", GI, reason="again"))


def test_pid_wrong_env():
    """SPEC 4 clear checks the pid in the bench environment and no other:
    a PID from another environment is never trusted."""
    st = B.replay([init_line(env="wsl"),
                   json.dumps({"ts": "t", "who": CI, "event": "take",
                               "pid": dead_pid(), "run": "r1"})])
    msg = refused(B.check_holder_gone, st)
    check("environment" in msg, "refused for the environment: %s" % msg)
    st["bench"]["env"] = "windows"
    B.check_holder_gone(st)


def test_lost():
    """SPEC 3 lost; 8 lost."""
    p = fresh("lost")
    refused(B.lost, p, CI, "r1")
    B.take(p, CI, ME, "r1")
    refused(B.lost, p, CI, "r1")
    w(p, "pause", CI, run="r1", boards=BD)
    refused(B.lost, p, GI, "r1")
    st = B.lost(p, CI, "r1")[0]
    check(not st["boards"]["known"] and "r1" in st["boards"]["why"],
          "lost makes the boards unknown")
    check(not st["paused"], "a lost run is no longer paused")
    st = B.take(p, CI, ME, "r2")[0]
    check(B.held_by_run(st, CI, "r2"), "the role takes a new run after lost")


def test_replay_fails_closed():
    """SPEC 2 a bad line makes every command refuse; 3 events and their
    fields; 8 lines (torn)."""
    p = fresh("replay")
    w(p, "take", CI, pid=ME, run="r1")
    w(p, "release", CI, boards=BD)
    good = lines(p)
    check(B.read_state(p)["events"] == 3, "the good log replays")
    init, take, rel = good
    t = json.loads(take)
    bad = {
        "a second take while held": [init, take, take],
        "a blank line": [init, take, "", rel],
        "not JSON": [init, take, "{take"],
        "an unknown event": [init, json.dumps(dict(t, event="steal"))],
        "a reviewer as who": [init, json.dumps(dict(t, who="reviewer (x)"))],
        "a take without pid": [init, json.dumps({k: v for k, v in t.items()
                                                 if k != "pid"})],
        "a release by another role": [init, take, json.dumps(dict(
            json.loads(rel), who=GI))],
        "a run id with a space": [init, json.dumps(dict(t, run="r 1"))],
        "an unknown field": [init, json.dumps(dict(t, host="X"))],
        "no init line": [take, rel],
        "a second init": [init, take, init],
        "nothing at all": [],
    }
    for what, ls in bad.items():
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(l + "\n" for l in ls))
        refused(B.read_state, p)
        refused(B.write, p, ev("request", GI, note="x"))
        rc, _o, e = cli("--log", p, "status")
        check(rc == 2 and "REFUSED" in e, "status on %s: rc %d" % (what, rc))
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(init + "\n" + take + "\n" + rel)
    msg = refused(B.read_state, p)
    check("newline" in msg, "a torn last line: %s" % msg)


def test_init():
    """SPEC 3 init; 4 init records host and env; 8 creating the log."""
    p = folder("init")
    refused(B.write, p, ev("take", CI, pid=ME))
    refused(B.read_state, p)
    check(not os.path.exists(p), "a missing log is not created by a write")
    B.init(p, CI)
    first = json.loads(lines(p)[0])
    check(first["event"] == "init" and first["who"] == CI
          and first["host"] == socket.gethostname() and first["env"] == "windows",
          "init names the host and environment: %s" % first)
    st = B.read_state(p)
    check(st["holder"] is None and st["bench"]["env"] == "windows",
          "a new log is free")
    refused(B.init, p, CI)
    check(len(lines(p)) == 1, "a second init wrote nothing")
    refused(B.init, os.path.join(os.path.dirname(p), "no-such", "x.jsonl"), CI)
    q = folder("init_reviewer")
    refused(B.init, q, "reviewer (charge-interposer)")
    check(not os.path.exists(q), "a refused init creates nothing")


def test_detect_env():
    """SPEC 4 the environment is detected explicitly; WSL and anything not
    implemented are refused."""
    check(B.detect_env("nt", "win32", "", {}) == "windows", "windows")
    check(B.detect_env("posix", "linux", WSL_VERSION, {}) == "wsl",
          "WSL by /proc/version")
    check(B.detect_env("posix", "linux", "Linux version 6.1.0-26-amd64", {
        "WSL_DISTRO_NAME": "Debian"}) == "wsl", "WSL by its environment")
    check(B.detect_env("posix", "linux", "Linux version 6.1.0-26-amd64", {})
          == "linux", "native Linux")
    check(B.detect_env("posix", "darwin", "", {}) == "darwin", "other")
    for env in ("wsl", "linux", "darwin"):
        refused(B.env_impl, env)
    check(B.env_impl("windows") is B.WindowsEnv, "windows implemented")


def test_env_refusal():
    """SPEC 4 every command refuses off the bench host or environment,
    status included (8); WSL is refused before the log is touched."""
    p = fresh("env")
    before = lines(p)
    with patched(this_env=lambda: "wsl"):
        refused(B.read_state, p)
        msg = refused(B.read_state, p + ".missing")
        check("not implemented" in msg,
              "WSL refused before the file is looked at: %s" % msg)
        refused(B.write, p, ev("take", CI, pid=ME))
        q = folder("env_wsl_init")
        refused(B.init, q, CI)
        check(not os.path.exists(q), "no log made under WSL")
        rc, _o, e = cli("--log", p, "status")
        check(rc == 2 and "wsl" in e, "status under WSL: %s" % e.strip())
    with patched(this_host=lambda: "SOME-OTHER-HOST"):
        refused(B.write, p, ev("take", CI, pid=ME))
        rc, _o, e = cli("--log", p, "status")
        check(rc == 2 and "SOME-OTHER-HOST" in e, "status off the host: %s"
              % e.strip())
    check(lines(p) == before, "nothing written off the bench")


def test_env_mismatch():
    """SPEC 4 a log whose init names another environment or host refuses
    every command, whatever this environment is."""
    for name, il in (("mismatch_env", init_line(env="linux")),
                     ("mismatch_host", init_line(host="MADHOUSE-DEBIAN"))):
        p = hand_log(name, [il])
        refused(B.read_state, p)
        refused(B.write, p, ev("take", CI, pid=ME))
        rc, _o, e = cli("--log", p, "status")
        check(rc == 2 and "bench is on" in e, "%s status: %s" % (name, e))
        check(lines(p) == [il], "%s: nothing written" % name)
    p = hand_log("match", [init_line()])
    check(B.read_state(p)["holder"] is None, "the matching log is readable")


def test_file_lock():
    """SPEC 2 read, check and append in one locked step."""
    p = fresh("file_lock")
    done = []

    def writer():
        try:
            B.write(p, ev("take", CI, pid=ME, run="r1"))
            done.append("ok")
        except Exception as e:
            done.append(repr(e))

    with B.locked(p):
        t = threading.Thread(target=writer)
        t.start()
        time.sleep(1.5)
        check(t.is_alive() and not done,
              "a write went through while another writer held the lock")
    t.join(15)
    check(done == ["ok"], "the waiting write finished after the lock: %s" % done)
    check(len(lines(p)) == 2, "init and one line")


def test_lines():
    """SPEC 2 every line has ts (with offset), who, event; 8 compact ASCII."""
    p = fresh("lines")
    w(p, "take", CI, pid=ME, run="r1", note="caf\u00e9, \u2192 bench")
    for ln in lines(p):
        obj = json.loads(ln)
        check(ln == json.dumps(obj, ensure_ascii=True, separators=(",", ":")),
              "compact: %s" % ln)
        check(re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d\Z",
                       obj["ts"]), "ts with its offset: %s" % obj["ts"])
        check(obj["who"] == CI and obj["event"] in ("init", "take"),
              "who and event")
    with open(p, "rb") as f:
        raw = f.read()
    check(all(b < 128 for b in raw) and raw.endswith(b"\n"), "ASCII, \\n-ended")
    check(json.loads(lines(p)[1])["note"] == "caf\u00e9, \u2192 bench",
          "the note survives")


def test_cli():
    """SPEC 5 command line; 4 never the command's own PID; 3 no lost
    command; 8 defaults and exit codes."""
    p = folder("cli")
    rc, out, _e = cli("--log", p, "init", "--who", CI)
    check(rc == 0 and out.strip() == lines(p)[0], "init prints its line")
    rc, out, _e = cli("--log", p, "status")
    check(rc == 0 and "free" in out, "status free: %s" % out)
    rc, _o, e = cli("--log", p, "take", "--who", CI, "--pid", str(ME))
    check(rc == 2 and "own PID" in e and len(lines(p)) == 1,
          "--pid of the command itself: %s" % e)
    rc, out, _e = cli("--log", p, "take", "--who", CI, "--pid", "1",
                      "--run", "r1")
    check(rc == 0 and out.splitlines()[0] == lines(p)[-1],
          "take prints the line it appended: %s" % out)
    rc, _o, e = cli("--log", p, "take", "--who", GI, "--pid", "1")
    check(rc == 2 and CI in e and len(lines(p)) == 2,
          "second take: %d %s" % (rc, e))
    rc, _o, e = cli("--log", p, "pause", "--who", CI, "--board", "x2=ok")
    check(rc == 2 and "wican" in e, "pause without the wican read: %s" % e)
    rc, out, _e = cli("--log", p, "pause", "--who", CI, "--board",
                      "wican=_man 02:00", "--board", "x2=756d6714 02:02")
    check(rc == 0 and "paused: run r1" in out and "x2: 756d6714 02:02" in out,
          "pause: %s" % out)
    rc, out, _e = cli("--log", p, "request", "--who", GI, "--note", "B6, 1 h")
    check(rc == 0 and "B6, 1 h" in out, "request: %s" % out)
    rc, out, _e = cli("--log", p, "status", "--json")
    s = json.loads(out)
    check(rc == 0 and s["holder"] is None and s["paused"][CI]["run"] == "r1",
          "status --json")
    err = io.StringIO()
    try:
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = B.main(["--log", p, "lost", "--help"])
    except SystemExit as e:
        rc = e.code
    check(rc == 2 and "invalid choice" in err.getvalue() and len(lines(p)) == 4,
          "no lost command: %r %s" % (rc, err.getvalue()[-80:]))
    env = os.environ.pop("REVERSE_IT_DIR", None)
    try:
        rc, _o, e = cli("status")
    finally:
        if env is not None:
            os.environ["REVERSE_IT_DIR"] = env
    check(rc == 2 and "REVERSE_IT_DIR" in e, "no log and no REVERSE_IT_DIR")


TESTS = [test_take_release, test_boards, test_pause_resume, test_run_once,
         test_held_by_run, test_queue, test_clear, test_pid_wrong_env,
         test_lost, test_replay_fails_closed, test_init, test_detect_env,
         test_env_refusal, test_env_mismatch, test_file_lock, test_lines,
         test_cli]


# ---- mutants ------------------------------------------------------------

with open(SRC, encoding="utf-8") as _f:
    ORIGINAL = _f.read()


def _load(text):
    exec(compile(text, SRC, "exec"), B.__dict__)


def _edit(old, new):
    """A source edit applied by on() and undone by off()."""
    def on():
        n = ORIGINAL.count(old)
        if n != 1:
            raise RuntimeError("mutant text found %d times: %r" % (n, old[:60]))
        _load(ORIGINAL.replace(old, new))

    def off():
        _load(ORIGINAL)
    return on, off


MUTANTS = {
    "test_take_release": [
        ("take ignores the holder",
         _edit("        if h is not None:\n            raise Refused(\"the lock is not free\")",
               "        if False:\n            raise Refused(\"the lock is not free\")")),
        ("anyone releases",
         _edit("        if h is None or h[\"who\"] != who:", "        if h is None:")),
        ("refusal hides the holder",
         _edit("            raise Refused(\"%s (%s)\" % (e, holder_text(state)))",
               "            raise")),
        ("refusal still appends",
         _edit("            apply(state, ev)\n        except Refused as e:\n"
               "            raise Refused(\"%s (%s)\"",
               "            try:\n                apply(state, ev)\n"
               "            except Refused:\n                pass\n"
               "        except Refused as e:\n"
               "            raise Refused(\"%s (%s)\"")),
    ],
    "test_boards": [
        ("release without boards",
         _edit("    \"release\": (\"boards\",),\n    \"pause\": (\"run\", \"boards\"),",
               "    \"release\": (),\n    \"pause\": (\"run\", \"boards\"),")),
        ("pause without boards",
         _edit("    \"pause\": (\"run\", \"boards\"),", "    \"pause\": (\"run\",),")),
        ("any set of boards",
         _edit("not isinstance(b, dict) or sorted(b) != sorted(BOARDS)",
               "not isinstance(b, dict)")),
        ("empty board read",
         _edit("                if not _text(b, name):", "                if False:")),
        ("not read counts as read",
         _edit("    return all(not boards[n].strip().lower().startswith(NOT_READ)",
               "    return all(True")),
        ("case-sensitive not read",
         _edit("boards[n].strip().lower().startswith(NOT_READ)",
               "boards[n].strip().startswith(NOT_READ)")),
        ("status hides unknown",
         _edit("        out.append(\"%s: %s\" % (UNKNOWN, b[\"why\"]))",
               "        out.append(\"boards: %s\" % b[\"why\"])")),
        ("board given twice",
         _edit("        if name in out:\n", "        if False:\n")),
        ("board without =",
         _edit("        if not sep:\n", "        if False:\n")),
    ],
    "test_pause_resume": [
        ("pause without a run",
         _edit("    \"pause\": (\"run\", \"boards\"),", "    \"pause\": (\"boards\",),")),
        ("pause names any run",
         _edit("            if run != h[\"run\"]:", "            if False:")),
        ("paused run not kept",
         _edit("            state[\"paused\"][who] = {\"run\": run, \"ts\": ev[\"ts\"]}",
               "            pass")),
        ("paused role takes a new run",
         _edit("        if mine is not None and run != mine[\"run\"]:",
               "        if False:")),
    ],
    "test_run_once": [
        ("run ids reusable",
         _edit("        if run is not None and not resuming and run in state[\"runs\"]:",
               "        if False:")),
    ],
    "test_held_by_run": [
        ("run prefix match",
         _edit("            and h[\"run\"] == run)", "            and h[\"run\"].startswith(run))")),
        ("role not checked",
         _edit("    return (h is not None and run is not None and h[\"who\"] == who\n",
               "    return (h is not None and run is not None\n")),
        ("role prefix match",
         _edit("and h[\"who\"] == who\n", "and h[\"who\"].startswith(who)\n")),
        ("any holder",
         _edit("            and h[\"run\"] == run)", "            or True)")),
    ],
    "test_queue": [
        ("holder may request",
         _edit("        if h is not None and h[\"who\"] == who:\n            raise Refused(\"%s holds",
               "        if False:\n            raise Refused(\"%s holds")),
        ("duplicate requests",
         _edit("        if any(q[\"who\"] == who for q in state[\"queue\"]):\n            raise Refused(\"%s already",
               "        if False:\n            raise Refused(\"%s already")),
        ("take keeps the request",
         _edit("        state[\"queue\"] = [q for q in state[\"queue\"] if q[\"who\"] != who]\n    elif kind in",
               "        pass\n    elif kind in")),
        ("take clears every request",
         _edit("        state[\"queue\"] = [q for q in state[\"queue\"] if q[\"who\"] != who]\n    elif kind in",
               "        state[\"queue\"] = []\n    elif kind in")),
        ("take honours the queue",
         _edit("        mine = state[\"paused\"].get(who)\n",
               "        if state[\"queue\"] and state[\"queue\"][0][\"who\"] != who:\n"
               "            raise Refused(\"not first in the queue\")\n"
               "        mine = state[\"paused\"].get(who)\n")),
        ("request without note",
         _edit("    \"request\": (\"note\",),", "    \"request\": (),")),
    ],
    "test_clear": [
        ("clear a live holder",
         _edit("    if bench_impl(state).pid_alive(h[\"pid\"]):", "    if False:")),
        ("no clear check",
         _edit("            if ev[\"event\"] == \"clear\":\n                check_holder_gone(state)\n",
               "")),
        ("pid_alive always False",
         _edit("        k = ctypes.WinDLL(\"kernel32\", use_last_error=True)\n",
               "        return False\n")),
        ("pid_alive always True",
         _edit("        k = ctypes.WinDLL(\"kernel32\", use_last_error=True)\n",
               "        return True\n")),
        ("clear without reason",
         _edit("    \"clear\": (\"reason\",),\n    \"lost\": (\"run\",),",
               "    \"clear\": (),\n    \"lost\": (\"run\",),")),
        ("clear keeps the boards",
         _edit("        state[\"boards\"] = {\"known\": False, \"why\": \"lock cleared by",
               "        state[\"boards\"] = {\"known\": True, \"why\": \"lock cleared by")),
        ("cleared run resumable",
         _edit("            state[\"runs\"].add(run)\n", "            pass\n")),
    ],
    "test_pid_wrong_env": [
        ("PID checked in this environment",
         _edit("    if bench_impl(state).pid_alive(h[\"pid\"]):",
               "    if env_impl(this_env()).pid_alive(h[\"pid\"]):")),
    ],
    "test_lost": [
        ("lost by any role",
         _edit("        if state[\"takers\"].get(run) != who:", "        if False:")),
        ("lost for the holder's run",
         _edit("        if h is not None and h[\"run\"] == run:\n            raise Refused(\"lost names",
               "        if False:\n            raise Refused(\"lost names")),
        ("lost keeps it paused",
         _edit("        state[\"paused\"] = {w: p for w, p in state[\"paused\"].items()\n                           if p[\"run\"] != run}",
               "        pass")),
        ("lost keeps the boards",
         _edit("        state[\"boards\"] = {\"known\": False, \"why\": \"run %s lost",
               "        state[\"boards\"] = {\"known\": True, \"why\": \"run %s lost")),
    ],
    "test_replay_fails_closed": [
        ("blank lines skipped",
         _edit("        if not ln.strip():\n            raise Refused(\"line %d is blank\" % n)",
               "        if not ln.strip():\n            continue")),
        ("bad lines skipped",
         _edit("        except Refused as e:\n            raise Refused(\"line %d breaks",
               "        except Refused as e:\n            continue\n            raise Refused(\"line %d breaks")),
        ("unparsed lines skipped",
         _edit("        except ValueError as e:\n            raise Refused(\"line %d is not JSON",
               "        except ValueError as e:\n            continue\n            raise Refused(\"line %d is not JSON")),
        ("torn line accepted",
         _edit("    if text and not text.endswith(\"\\n\"):", "    if False:")),
        ("any who",
         _edit("    if not isinstance(who, str) or not ROLE_RE.match(who):",
               "    if not isinstance(who, str):")),
        ("any run id",
         _edit("    if run is not None and (not isinstance(run, str) or not RUN_RE.match(run)):",
               "    if run is not None and not isinstance(run, str):")),
        ("take without pid",
         _edit("    \"take\": (\"pid\",),\n    \"release\": (\"boards\",),\n    \"pause\": (\"run\"",
               "    \"take\": (),\n    \"release\": (\"boards\",),\n    \"pause\": (\"run\"")),
        ("unknown fields allowed",
         _edit("    if extra:\n", "    if False:\n")),
        ("init anywhere",
         _edit("        if state[\"events\"]:\n", "        if False:\n")),
        ("empty log accepted",
         _edit("    if state[\"bench\"] is None:\n        raise Refused(\"the log is empty",
               "    if False:\n        raise Refused(\"the log is empty")),
    ],
    "test_init": [
        ("write creates the log",
         _edit("    if not os.path.isfile(path):\n        raise Refused(\"no bench log",
               "    if False:\n        raise Refused(\"no bench log")),
        ("init overwrites",
         _edit("    if os.path.exists(path):\n        raise Refused(\"%s exists",
               "    if False:\n        raise Refused(\"%s exists")),
        ("init without env",
         _edit("    ev = {\"ts\": now(), \"who\": who, \"event\": \"init\", \"host\": this_host(),\n          \"env\": env}",
               "    ev = {\"ts\": now(), \"who\": who, \"event\": \"init\", \"host\": this_host(),\n          \"env\": \"?\"}")),
        ("init unchecked",
         _edit("    apply(new_state(), ev)\n", "")),
    ],
    "test_detect_env": [
        ("WSL not detected",
         _edit("            return \"wsl\"\n", "            return \"linux\"\n")),
        ("WSL only by /proc/version",
         _edit("        if (\"microsoft\" in proc_version.lower() or \"WSL_DISTRO_NAME\" in environ",
               "        if (\"microsoft\" in proc_version.lower()")),
        ("WSL taken as Windows",
         _edit("            return \"wsl\"\n", "            return \"windows\"\n")),
        ("every environment implemented",
         _edit("    impl = ENVS.get(env)\n", "    impl = ENVS.get(env, WindowsEnv)\n")),
    ],
    "test_env_refusal": [
        ("WSL refusal skipped",
         _edit("    impl = env_impl(this_env())\n    if not os.path.isfile(path):",
               "    impl = env_impl(\"windows\")\n    if not os.path.isfile(path):")),
        ("init in any environment",
         _edit("    env = this_env()\n    env_impl(env)\n", "    env = this_env()\n")),
        ("host not compared",
         _edit("    if host.upper() != want[\"host\"].upper() or env != want[\"env\"]:",
               "    if env != want[\"env\"]:")),
        ("status skips the bench check",
         _edit("    state = replay(split_lines(f.read()))\n    bench_impl(state)\n",
               "    state = replay(split_lines(f.read()))\n")),
    ],
    "test_env_mismatch": [
        ("env not compared with init",
         _edit("    if host.upper() != want[\"host\"].upper() or env != want[\"env\"]:",
               "    if host.upper() != want[\"host\"].upper():")),
        ("host not compared with init",
         _edit("    if host.upper() != want[\"host\"].upper() or env != want[\"env\"]:",
               "    if env != want[\"env\"]:")),
    ],
    "test_file_lock": [
        ("no OS lock",
         _edit("        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)",
               "        pass")),
    ],
    "test_lines": [
        ("spaced JSON",
         _edit("ensure_ascii=True, separators=(\",\", \":\"))", "ensure_ascii=True)")),
        ("non-ASCII written",
         _edit("    return json.dumps(ev, ensure_ascii=True,", "    return json.dumps(ev, ensure_ascii=False,")),
        ("ts without offset",
         _edit("    return datetime.datetime.now().astimezone().isoformat(timespec=\"seconds\")",
               "    return datetime.datetime.now().isoformat(timespec=\"seconds\")")),
    ],
    "test_cli": [
        ("own PID accepted",
         _edit("        if a.cmd == \"take\" and a.pid == os.getpid():", "        if False:")),
        ("pause names no run",
         _edit("            ev[\"run\"] = st[\"holder\"][\"run\"] if st[\"holder\"] else None",
               "            ev[\"run\"] = None")),
        ("refusal exits 0",
         _edit("        return 2\n", "        return 0\n")),
        ("no appended line printed",
         _edit("        print(line)\n", "")),
        ("a lost command",
         _edit("    for name in (\"init\", \"take\", \"release\", \"pause\", \"request\", \"withdraw\",\n                 \"clear\"):",
               "    for name in (\"init\", \"take\", \"release\", \"pause\", \"request\", \"withdraw\",\n                 \"clear\", \"lost\"):")),
    ],
}


def _run(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    if B.detect_env() != "windows":
        print("these tests run in native Windows Python only (section 4)")
        return 1
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    tests = [t for t in TESTS if not names or t.__name__ in names]
    bad = total = 0
    for t in tests:
        if not can_fail:
            total += 1
            outcome = _run(t)
            print("%s  %-26s %s" % ("ok  " if outcome is None else "FAIL",
                                    t.__name__, outcome or ""))
            bad += outcome is not None
            continue
        for label, (on, off) in MUTANTS[t.__name__]:
            total += 1
            try:
                on()
            except RuntimeError as e:
                print("FAIL  %-26s %-30s NOT APPLIED: %s"
                      % (t.__name__, label, e))
                bad += 1
                continue
            try:
                outcome = _run(t)
            finally:
                off()
            ok = outcome is not None
            print("%s  %-26s %-30s %s" % (
                "ok  " if ok else "FAIL", t.__name__, label,
                ("fails (" + outcome[:70] + ")") if ok else
                "STILL PASSES -- the test checks nothing"))
            bad += not ok
    print("\n%d of %d %s" % (total - bad, total,
                             "mutants caught" if can_fail else "passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
