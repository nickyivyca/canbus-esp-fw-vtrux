"""The bench lock as an append-only event log (reverse-it firmware/bench-log.jsonl).

The specification is reverse-it firmware/bench-lock-spec.md (user,
2026-10-21); section numbers below are its. NOT YET THE LOCK: until the
cutover (section 7) the Holder line in bench-holder.md is the lock
(vtrux_bench.run.lock), and the runner does not read this log yet.

    python -m vtrux_bench.bench_lock [--log PATH] init --who W
    python -m vtrux_bench.bench_lock [--log PATH] status [--json]
    python -m vtrux_bench.bench_lock [--log PATH] take --who W --pid N [--run ID]
    python -m vtrux_bench.bench_lock [--log PATH] release --who W --board wican=TEXT --board x2=TEXT
    python -m vtrux_bench.bench_lock [--log PATH] pause --who W --board wican=TEXT --board x2=TEXT
    python -m vtrux_bench.bench_lock [--log PATH] request --who W --note TEXT
    python -m vtrux_bench.bench_lock [--log PATH] withdraw --who W
    python -m vtrux_bench.bench_lock [--log PATH] clear --who W --reason TEXT
    python -m vtrux_bench.bench_lock [--log PATH] repair --who W --reason TEXT

`lost` has no command: only a runner writes it, through lost() (section 3).
A runner takes the lock for its run through take() with its own PID
(section 8). --log defaults to $REVERSE_IT_DIR/firmware/bench-log.jsonl.
Exit 0 done, 2 refused, with the reason on stderr.

The bench host and environment (section 4). `init` writes the log's first
line, recording this host and its execution environment (detect_env). Every
later command, the runner's API calls included, refuses unless it runs on
that host in that environment. Nothing else here names a host or an OS: the
PID check and the file lock belong to the environment (ENVS), and only
native Windows is implemented; WSL and anything else are refused.

The log (section 2). One compact ASCII JSON object per line, written only
here. Every line has ts (local time with its UTC offset, to the second),
who (a tester or implementor role, ROLE_RE) and event; any line may carry
run and note. The other fields, per event (FIELDS):

    init      host, env
    take      pid (required, running in the bench environment), run
              (optional)
    release   boards: {board: TEXT} for each of BOARDS, TEXT the image read
    pause     off the wire and when, or "not read: <why>"; pause also run
    request   note (required): what for and how long
    clear     reason (required)
    lost      run (required)
    repair    damaged: {name, sha256, kept, bad: [{line, error}]};
              dropped_runs: [run id]; reason (required)

The state is the replay of the whole log, every event checked again against
the rules (sections 3 and 8), unknown fields included. A line that does not
parse or breaks them makes every command refuse (fail closed). There is no
second state file.

The board state is known only after a release or pause whose every board
was read off the wire (TEXT not "not read..."); it is unknown before any,
after one with an unread board, and after a clear, a lost or a repair.

repair (section 3) is the only way out of a log that does not replay. On
the bench host and environment, by a role that may take, and refused if the
log replays: it keeps the good lines up to the first bad one (truncated
there, never skipped past), refuses while a holder it can find is alive
(the kept lines' holder, and the pid of every take it can parse among the
dropped lines; unsure counts as alive), renames the damaged log aside as
bench-log.<ts>.damaged.jsonl and writes the new log: the kept lines (or,
when line 1 is the bad one, a fresh init for this host and environment),
then the repair event. Its replay frees the lock, ends any held or paused
run, leaves the boards unknown and counts dropped_runs as used. init and
repair are never appended by write().
"""

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import socket
import sys

ROLE_RE = re.compile(r"(tester|implementor) \((gen-inhibit|charge-interposer)\)\Z")
RUN_RE = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")
BOARDS = ("wican", "x2")
NOT_READ = "not read"
COMMON = ("ts", "who", "event", "run", "note")
FIELDS = {
    "init": ("host", "env"),
    "take": ("pid",),
    "release": ("boards",),
    "pause": ("boards",),
    "request": (),
    "withdraw": (),
    "clear": ("reason",),
    "lost": (),
    "repair": ("damaged", "dropped_runs", "reason"),
}
REQUIRED = {
    "init": ("host", "env"),
    "take": ("pid",),
    "release": ("boards",),
    "pause": ("run", "boards"),
    "request": ("note",),
    "withdraw": (),
    "clear": ("reason",),
    "lost": ("run",),
    "repair": ("damaged", "dropped_runs", "reason"),
}
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
DAMAGED_RE = re.compile(r"bench-log\.\d{8}T\d{6}\.damaged\.jsonl\Z")
LOG_NAME = os.path.join("firmware", "bench-log.jsonl")
LOCK_WAIT_S = 10.0
UNKNOWN = "BOARD STATE UNKNOWN"


class Refused(Exception):
    """A command the rules do not allow, or a log that breaks them."""


# ---- the environment (section 4) ---------------------------------------

class WindowsEnv:
    """Native Windows Python: kernel32 for PIDs, msvcrt for the file lock."""

    @staticmethod
    def pid_alive(pid):
        """True unless the process is certainly gone. Unsure or access
        denied counts as alive, so `clear` refuses (section 8)."""
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() != 87   # 87: no such process
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259                # STILL_ACTIVE
        finally:
            k.CloseHandle(h)

    @staticmethod
    def lock(f, wait_s):
        """Wait up to wait_s for the lock; Refused if another writer keeps
        it (msvcrt's own LK_LOCK would raise OSError after ~10 s)."""
        import msvcrt
        import time
        deadline = time.monotonic() + wait_s
        while True:
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise Refused("the log is locked by another writer (waited "
                                  "%g s); try again" % wait_s)
                time.sleep(0.1)

    @staticmethod
    def unlock(f):
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


ENVS = {"windows": WindowsEnv}


def detect_env(os_name=None, platform=None, proc_version=None, environ=None):
    """The execution environment, detected explicitly: never from the host
    name, which inside WSL is the Windows one."""
    os_name = os.name if os_name is None else os_name
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if os_name == "nt":
        return "windows"
    if platform.startswith("linux"):
        if proc_version is None:
            try:
                with open("/proc/version", encoding="utf-8",
                          errors="replace") as f:
                    proc_version = f.read()
            except OSError:
                proc_version = ""
        if ("microsoft" in proc_version.lower() or "WSL_DISTRO_NAME" in environ
                or "WSL_INTEROP" in environ):
            return "wsl"
        return "linux"
    return platform


def this_env():
    return detect_env()


def this_host():
    return socket.gethostname()


def env_impl(env):
    impl = ENVS.get(env)
    if impl is None:
        raise Refused("bench_lock is not implemented in the %r environment "
                      "(implemented: %s)" % (env, ", ".join(sorted(ENVS))))
    return impl


def bench_impl(state):
    """This process's environment, if it is the bench's (init's host and
    env). Refused otherwise: a PID or a lock from another environment is
    never trusted."""
    host, env = this_host(), this_env()
    want = state["bench"]
    if host.upper() != want["host"].upper() or env != want["env"]:
        raise Refused("the bench is on host %s in the %s environment; this "
                      "is host %s in the %s environment"
                      % (want["host"], want["env"], host, env))
    return env_impl(env)


# ---- the rules (sections 3 and 8) --------------------------------------

def new_state():
    return {
        "bench": None,          # {host, env} from init
        "holder": None,         # {who, run, pid, ts}
        "paused": {},           # who -> {run, ts}
        "queue": [],            # [{who, note, ts}] in order
        "runs": set(),          # every run id the log has named
        "takers": {},           # run id -> the role that took it
        "boards": {"known": False, "why": "no release recorded yet"},
        "events": 0,
    }


def _text(ev, key):
    v = ev.get(key)
    return v if isinstance(v, str) and v.strip() else None


def check_fields(ev):
    """The shape of one event, whatever the state."""
    if not isinstance(ev, dict):
        raise Refused("not a JSON object")
    kind = ev.get("event")
    if kind not in FIELDS:
        raise Refused("unknown event %r" % (kind,))
    extra = sorted(set(ev) - set(COMMON) - set(FIELDS[kind]))
    if extra:
        raise Refused("%s with unknown fields %s" % (kind, ", ".join(extra)))
    who = ev.get("who")
    if not isinstance(who, str) or not ROLE_RE.match(who):
        raise Refused("who %r is not a tester or implementor role like "
                      "'tester (charge-interposer)'" % (who,))
    if not _text(ev, "ts"):
        raise Refused("%s without ts" % kind)
    for key in REQUIRED[kind]:
        if key == "pid":
            if (not isinstance(ev.get("pid"), int) or isinstance(ev["pid"], bool)
                    or ev["pid"] <= 0):
                raise Refused("take needs a positive integer pid")
        elif key == "boards":
            b = ev.get("boards")
            if not isinstance(b, dict) or sorted(b) != sorted(BOARDS):
                raise Refused("%s needs one read for each board %s, got %s"
                              % (kind, ", ".join(BOARDS),
                                 ", ".join(sorted(b)) if isinstance(b, dict)
                                 else "none"))
            for name in BOARDS:
                if not _text(b, name):
                    raise Refused("%s: the %s read is empty" % (kind, name))
        elif key == "damaged":
            _check_damaged(ev.get("damaged"))
        elif key == "dropped_runs":
            d = ev.get("dropped_runs")
            if not isinstance(d, list) or not all(
                    isinstance(r, str) and RUN_RE.match(r) for r in d):
                raise Refused("repair needs dropped_runs, a list of run ids")
        elif not _text(ev, key):
            raise Refused("%s needs %s" % (kind, key))
    run = ev.get("run")
    if run is not None and (not isinstance(run, str) or not RUN_RE.match(run)):
        raise Refused("run id %r is not [A-Za-z0-9._-]{1,80}" % (run,))
    note = ev.get("note")
    if note is not None and not isinstance(note, str):
        raise Refused("note is not text")


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check_damaged(d):
    """repair's record of the damaged log: its new name and sha256, the
    number of lines kept, and each bad line's number and error."""
    if not isinstance(d, dict) or sorted(d) != ["bad", "kept", "name", "sha256"]:
        raise Refused("repair needs damaged with name, sha256, kept and bad")
    if not isinstance(d["name"], str) or not DAMAGED_RE.match(d["name"]):
        raise Refused("damaged name %r is not bench-log.<ts>.damaged.jsonl"
                      % (d["name"],))
    if not isinstance(d["sha256"], str) or not SHA_RE.match(d["sha256"]):
        raise Refused("damaged sha256 is not 64 hex digits")
    if not _int(d["kept"]) or d["kept"] < 0:
        raise Refused("damaged kept is not a line count")
    bad = d["bad"]
    if not isinstance(bad, list) or not bad:
        raise Refused("damaged bad lists no bad line")
    last = d["kept"]
    for i, b in enumerate(bad):
        if not (isinstance(b, dict) and sorted(b) == ["error", "line"]
                and _int(b["line"]) and _text(b, "error")):
            raise Refused("damaged bad entry %r is not {line, error}" % (b,))
        if i == 0 and b["line"] != d["kept"] + 1:
            raise Refused("the first bad line is %d, not the line after the "
                          "%d kept" % (b["line"], d["kept"]))
        if b["line"] <= last and i:
            raise Refused("damaged bad lines out of order")
        last = b["line"]


def _all_read(boards):
    return all(not boards[n].strip().lower().startswith(NOT_READ)
               for n in BOARDS)


def apply(state, ev):
    """Check one event against the state and apply it. Raises Refused."""
    check_fields(ev)
    kind, who, run = ev["event"], ev["who"], ev.get("run")
    if kind == "init":
        if state["events"]:
            raise Refused("init after the log's first line")
        state["bench"] = {"host": ev["host"], "env": ev["env"]}
        state["events"] += 1
        return state
    h = state["holder"]
    if kind == "take":
        if h is not None:
            raise Refused("the lock is not free")
        mine = state["paused"].get(who)
        if mine is not None and run != mine["run"]:
            raise Refused("%s has run %s paused: resume it (take --run %s), "
                          "or take and release it to end it"
                          % (who, mine["run"], mine["run"]))
        resuming = mine is not None and run == mine["run"]
        if run is not None and not resuming and run in state["runs"]:
            raise Refused("run id %s was used before: a run id is used once"
                          % run)
        if resuming:
            del state["paused"][who]
        if run is not None:
            state["runs"].add(run)
            state["takers"][run] = who
        state["holder"] = {"who": who, "run": run, "pid": ev["pid"],
                           "ts": ev["ts"]}
        state["queue"] = [q for q in state["queue"] if q["who"] != who]
    elif kind in ("release", "pause"):
        if h is None or h["who"] != who:
            raise Refused("%s by %s, who does not hold the lock" % (kind, who))
        if kind == "pause":
            if run != h["run"]:
                raise Refused("pause names run %s, but the lock is held for "
                              "run %s" % (run, h["run"]))
            state["paused"][who] = {"run": run, "ts": ev["ts"]}
        state["holder"] = None
        b = dict(ev["boards"])
        if _all_read(b):
            state["boards"] = {"known": True, "read": b, "who": who,
                               "ts": ev["ts"]}
        else:
            state["boards"] = {"known": False, "read": b, "why": "%s by %s at "
                               "%s did not read every board off the wire"
                               % (kind, who, ev["ts"])}
    elif kind == "request":
        if h is not None and h["who"] == who:
            raise Refused("%s holds the lock: nothing to request" % who)
        if any(q["who"] == who for q in state["queue"]):
            raise Refused("%s already has a request queued" % who)
        state["queue"].append({"who": who, "note": ev["note"], "ts": ev["ts"]})
    elif kind == "withdraw":
        if not any(q["who"] == who for q in state["queue"]):
            raise Refused("%s has no request queued" % who)
        state["queue"] = [q for q in state["queue"] if q["who"] != who]
    elif kind == "clear":
        if h is None:
            raise Refused("clear, but the lock is free")
        state["holder"] = None
        state["boards"] = {"known": False, "why": "lock cleared by %s at %s "
                           "(holder %s, run %s, pid %d): %s" % (
                               who, ev["ts"], h["who"], h["run"] or "--",
                               h["pid"], ev["reason"])}
    elif kind == "lost":
        if h is not None and h["run"] == run:
            raise Refused("lost names run %s, which holds the lock" % run)
        if state["takers"].get(run) != who:
            raise Refused("lost for run %s by %s, which did not take it"
                          % (run, who))
        state["paused"] = {w: p for w, p in state["paused"].items()
                           if p["run"] != run}
        state["boards"] = {"known": False, "why": "run %s lost the lock at "
                           "%s, end restore skipped" % (run, ev["ts"])}
    elif kind == "repair":
        # legal after the kept lines of a log that did not replay; repair()
        # is its only writer and refuses a log that replays (section 3)
        state["holder"] = None
        state["paused"] = {}
        state["runs"] |= set(ev["dropped_runs"])
        d = ev["damaged"]
        state["boards"] = {"known": False, "why": "log repaired by %s at %s "
                           "(%s, %d line(s) kept, first bad line %d): %s" % (
                               who, ev["ts"], d["name"], d["kept"],
                               d["bad"][0]["line"], ev["reason"])}
    state["events"] += 1
    return state


def replay(lines):
    state = new_state()
    for n, ln in enumerate(lines, 1):
        if not ln.strip():
            raise Refused("line %d is blank" % n)
        try:
            ev = json.loads(ln)
        except ValueError as e:
            raise Refused("line %d is not JSON: %s" % (n, e))
        try:
            apply(state, ev)
        except Refused as e:
            raise Refused("line %d breaks the rules: %s" % (n, e))
    if state["bench"] is None:
        raise Refused("the log is empty: it has no init line")
    return state


def split_lines(data):
    """The log's lines. A last line without its newline is a torn write."""
    text = data.decode("utf-8")
    if text and not text.endswith("\n"):
        raise Refused("the last line has no newline (a torn write?)")
    return text.split("\n")[:-1] if text else []


# ---- the file ----------------------------------------------------------

def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def line_of(ev):
    return json.dumps(ev, ensure_ascii=True, separators=(",", ":"))


@contextlib.contextmanager
def locked(path):
    """The log opened for append, under this environment's exclusive lock.
    Refused before the file is touched in an unimplemented environment."""
    impl = env_impl(this_env())
    if not os.path.isfile(path):
        raise Refused("no bench log at %s (init creates it)" % path)
    f = open(path, "a+b")
    try:
        impl.lock(f, LOCK_WAIT_S)
        try:
            yield f
        finally:
            impl.unlock(f)
    finally:
        f.close()


def _read(f):
    f.seek(0)
    state = replay(split_lines(f.read()))
    bench_impl(state)
    return state


def read_state(path):
    with locked(path) as f:
        return _read(f)


def holder_text(state):
    h = state["holder"]
    if h is None:
        return "the lock is free"
    return "held by %s%s, pid %d, since %s" % (
        h["who"], ", run %s" % h["run"] if h["run"] else "", h["pid"], h["ts"])


def check_holder_gone(state):
    """clear's condition (sections 3 and 4): the holder's pid is not running,
    asked in the bench environment and nowhere else. Raises Refused."""
    h = state["holder"]
    if h is None:
        raise Refused("clear, but the lock is free")
    if bench_impl(state).pid_alive(h["pid"]):
        raise Refused("the holder's pid %d is still running in the bench "
                      "environment: clear is for a holder that is gone"
                      % h["pid"])


def check_pid_alive(state, pid):
    """take's pid must be running in the bench environment: a mistyped pid
    would make the lock clearable at once. Unsure counts as alive."""
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        if not bench_impl(state).pid_alive(pid):
            raise Refused("pid %d is not running in the bench environment: "
                          "give the long-lived holder's" % pid)


def write(path, ev):
    """Check ev against the log's state and append it, under the lock.
    Returns (state after, the line appended). A refusal names the holder."""
    ev = dict(ev, ts=now())
    ev = {k: v for k, v in ev.items() if v is not None}
    if ev.get("event") in ("init", "repair"):
        raise Refused("%s is never appended: init creates the log, repair "
                      "replaces a damaged one" % ev["event"])
    with locked(path) as f:
        state = _read(f)
        try:
            if ev["event"] == "clear":
                check_holder_gone(state)
            if ev["event"] == "take":
                check_pid_alive(state, ev.get("pid"))
            apply(state, ev)
        except Refused as e:
            raise Refused("%s (%s)" % (e, holder_text(state)))
        line = line_of(ev)
        f.seek(0, os.SEEK_END)
        f.write((line + "\n").encode("ascii"))
        f.flush()
        os.fsync(f.fileno())
    return state, line


def init(path, who):
    """Create the log with its first line, naming this host and its
    environment as the bench's. Refused in an unimplemented environment."""
    env = this_env()
    env_impl(env)
    if os.path.exists(path):
        raise Refused("%s exists already" % path)
    if not os.path.isdir(os.path.dirname(os.path.abspath(path))):
        raise Refused("no folder for %s" % path)
    ev = {"ts": now(), "who": who, "event": "init", "host": this_host(),
          "env": env}
    apply(new_state(), ev)
    line = line_of(ev)
    with open(path, "xb") as f:
        f.write((line + "\n").encode("ascii"))
        f.flush()
        os.fsync(f.fileno())
    return line


def take(path, who, pid, run=None, note=None):
    """The runner's take at start: its own PID and run id (section 8)."""
    return write(path, {"event": "take", "who": who, "pid": pid, "run": run,
                        "note": note})


def lost(path, who, run, note=None):
    """The runner's record that its run lost the lock (section 6)."""
    return write(path, {"event": "lost", "who": who, "run": run, "note": note})


def scan(data):
    """A damaged log's bytes -> (kept, state, bad, dropped): the good lines
    (text) up to the first bad one, the state they replay to, each bad
    line as {line, error} (the first bad line, then every later line that is
    bad on its own), and the dropped lines' text (None where undecodable).
    Raises Refused if the log replays cleanly."""
    pieces = data.split(b"\n")
    raw = pieces[:-1] + ([pieces[-1]] if pieces[-1] else [])
    torn = bool(pieces[-1])
    state, kept, bad, dropped = new_state(), [], [], []
    for n, b in enumerate(raw, 1):
        err, text, ev = None, None, None
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError as e:
            err = "not UTF-8: %s" % e
        if err is None and torn and n == len(raw):
            err = "no newline (a torn write?)"
        if err is None and not text.strip():
            err = "blank"
        if err is None:
            try:
                ev = json.loads(text)
            except ValueError as e:
                err = "not JSON: %s" % e
        if err is None and bad:
            try:
                check_fields(ev)
            except Refused as e:
                err = str(e)
        elif err is None and n == 1 and (not isinstance(ev, dict)
                                         or ev.get("event") != "init"):
            err = "line 1 is not the init"
        elif err is None:
            try:
                apply(state, ev)
            except Refused as e:
                err = "breaks the rules: %s" % e
        if bad or err is not None:
            dropped.append(text)
            if err is not None:
                bad.append({"line": n, "error": err})
        else:
            kept.append(text)
    if not raw:
        bad.append({"line": 1, "error": "the log is empty: it has no init "
                    "line"})
    elif not bad:
        raise Refused("the log replays: nothing to repair")
    return kept, state, bad, dropped


def dropped_takes_and_runs(dropped):
    """From the dropped lines: the pid of every take that still parses, and
    every run id that still parses (sections 3, 8)."""
    pids, runs = [], set()
    for text in dropped:
        try:
            ev = json.loads(text) if text is not None else None
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        r = ev.get("run")
        if isinstance(r, str) and RUN_RE.match(r):
            runs.add(r)
        p = ev.get("pid")
        if ev.get("event") == "take" and _int(p) and p > 0:
            pids.append(p)
    return pids, sorted(runs)


def check_repair_holders(state, impl, dropped_pids):
    """repair's PID fail-safe (section 3): refused while the kept lines'
    holder, or the pid of any take among the dropped lines, is alive in the
    bench environment. Unsure counts as alive (pid_alive)."""
    h = state["holder"]
    alive = []
    if h is not None and impl.pid_alive(h["pid"]):
        alive.append("the kept lines' holder %s, pid %d" % (h["who"], h["pid"]))
    for p in dropped_pids:
        if impl.pid_alive(p):
            alive.append("pid %d of a take among the dropped lines" % p)
    if alive:
        raise Refused("a holder may still be running in the bench "
                      "environment: %s; repair is for a log whose holders "
                      "are gone" % "; ".join(alive))


def repair(path, who, reason):
    """Replace a log that does not replay (section 3). Returns (state of the
    new log, the repair line, the damaged log's new path). who and reason
    are checked with the repair line itself, by the new log's replay, before
    anything is renamed; locked() refuses an unimplemented environment."""
    env = this_env()
    folder = os.path.dirname(os.path.abspath(path))
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    name = "bench-log.%s.damaged.jsonl" % stamp
    aside = os.path.join(folder, name)
    tmp = path + ".repair-tmp"
    with locked(path) as f:
        f.seek(0)
        data = f.read()
        kept, state, bad, dropped = scan(data)
        if state["bench"] is not None:
            impl = bench_impl(state)
            head = []
        else:
            # line 1 was bad: a fresh init for this host and environment
            impl = env_impl(env)
            head = [line_of({"ts": now(), "who": who, "event": "init",
                             "host": this_host(), "env": env})]
        pids, runs = dropped_takes_and_runs(dropped)
        check_repair_holders(state, impl, pids)
        ev = {"ts": now(), "who": who, "event": "repair",
              "damaged": {"name": name,
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "kept": len(kept), "bad": bad},
              "dropped_runs": runs, "reason": reason}
        line = line_of(ev)
        new = head + kept + [line]
        after = replay(new)         # the new log must replay, or nothing moves
        with open(tmp, "xb") as t:
            t.write("".join(l + "\n" for l in new).encode("ascii"))
            t.flush()
            os.fsync(t.fileno())
    # every writer refuses the damaged log, so nothing appends between the
    # unlock and the renames; Windows cannot rename a file held open
    os.rename(path, aside)
    if sha256_file(aside) != ev["damaged"]["sha256"]:
        os.rename(aside, path)
        os.remove(tmp)
        raise Refused("the log changed during the repair; nothing replaced")
    os.rename(tmp, path)
    return after, line, aside


def sha256_file(path):
    with open(path, "rb") as g:
        return hashlib.sha256(g.read()).hexdigest()


def held_by_run(state, who, run):
    """For the runner (section 6): held by its own role and its own run."""
    h = state["holder"]
    return (h is not None and run is not None and h["who"] == who
            and h["run"] == run)


# ---- status and the command line (section 5) ---------------------------

def status_lines(state):
    b0 = state["bench"]
    out = ["bench: host %s, %s environment" % (b0["host"], b0["env"]),
           "holder: %s" % holder_text(state)]
    h = state["holder"]
    if h is not None and not bench_impl(state).pid_alive(h["pid"]):
        out.append("  its pid %d is NOT RUNNING in the bench environment"
                   % h["pid"])
    for who, p in sorted(state["paused"].items()):
        out.append("paused: run %s of %s, since %s" % (p["run"], who, p["ts"]))
    for i, q in enumerate(state["queue"], 1):
        out.append("queue %d: %s, %s, since %s" % (i, q["who"], q["note"],
                                                   q["ts"]))
    b = state["boards"]
    if b["known"]:
        out.append("boards, as released by %s at %s:" % (b["who"], b["ts"]))
    else:
        out.append("%s: %s" % (UNKNOWN, b["why"]))
    out.extend("  %s: %s" % (n, b["read"][n]) for n in BOARDS if "read" in b)
    return out


def status_json(state):
    s = dict(state)
    s["runs"] = sorted(s["runs"])
    h = s["holder"]
    if h is not None:
        s["holder"] = dict(h, alive=bench_impl(state).pid_alive(h["pid"]))
    return s


def parse_boards(items):
    """--board NAME=TEXT, repeated, into {NAME: TEXT}. Raises Refused."""
    out = {}
    for it in items or ():
        name, sep, text = it.partition("=")
        if not sep:
            raise Refused("--board %r is not NAME=TEXT" % it)
        if name in out:
            raise Refused("--board %s given twice" % name)
        out[name] = text
    return out


def default_log():
    root = os.environ.get("REVERSE_IT_DIR")
    if not root:
        raise Refused("REVERSE_IT_DIR is not set, and no --log given")
    return os.path.join(root, LOG_NAME)


def main(argv=None):
    p = argparse.ArgumentParser(prog="bench_lock")
    p.add_argument("--log")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    for name in ("init", "take", "release", "pause", "request", "withdraw",
                 "clear"):
        s = sub.add_parser(name)
        s.add_argument("--who", required=True)
        if name == "take":
            s.add_argument("--pid", type=int, required=True,
                           help="the long-lived process holding the bench "
                           "(the Claude Code session's), never this command's")
            s.add_argument("--run")
            s.add_argument("--note")
        elif name in ("release", "pause"):
            s.add_argument("--board", action="append", metavar="NAME=TEXT",
                           help="one each for %s: the image read off the "
                           "wire and when, or 'not read: <why>'"
                           % ", ".join(BOARDS))
            s.add_argument("--note")
        elif name == "request":
            s.add_argument("--note", required=True,
                           help="what for, and how long")
        elif name == "clear":
            s.add_argument("--reason", required=True)
    s = sub.add_parser("repair", help="replace a log that does not replay")
    s.add_argument("--who", required=True)
    s.add_argument("--reason", required=True)
    a = p.parse_args(argv)
    try:
        path = a.log or default_log()
        if a.cmd == "init":
            print(init(path, a.who))
            return 0
        if a.cmd == "repair":
            st, line, aside = repair(path, a.who, a.reason)
            print("the damaged log is kept as %s" % aside)
            print(line)
            print("\n".join(status_lines(st)))
            return 0
        if a.cmd == "status":
            st = read_state(path)
            if a.json:
                print(json.dumps(status_json(st), indent=1, sort_keys=True))
            else:
                print("\n".join(status_lines(st)))
            return 0
        ev = {"event": a.cmd, "who": a.who}
        for key in ("pid", "run", "note", "reason"):
            ev[key] = getattr(a, key, None)
        if a.cmd == "take" and a.pid == os.getpid():
            raise Refused("--pid is this command's own PID, which exits at "
                          "once: give the long-lived holder's")
        if a.cmd in ("release", "pause"):
            ev["boards"] = parse_boards(a.board)
        if a.cmd == "pause":
            st = read_state(path)
            ev["run"] = st["holder"]["run"] if st["holder"] else None
        st, line = write(path, ev)
        print(line)
        print("\n".join(status_lines(st)))
        return 0
    except Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
