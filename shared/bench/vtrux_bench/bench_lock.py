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
    take      pid (required), run (optional)
    release   boards: {board: TEXT} for each of BOARDS, TEXT the image read
    pause     off the wire and when, or "not read: <why>"; pause also run
    request   note (required): what for and how long
    clear     reason (required)
    lost      run (required)

The state is the replay of the whole log, every event checked again against
the rules (sections 3 and 8), unknown fields included. A line that does not
parse or breaks them makes every command refuse (fail closed). There is no
second state file.

The board state is known only after a release or pause whose every board
was read off the wire (TEXT not "not read..."); it is unknown before any,
after one with an unread board, and after a clear or a lost.
"""

import argparse
import contextlib
import datetime
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
}
LOG_NAME = os.path.join("firmware", "bench-log.jsonl")
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
    def lock(f):
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

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
        elif not _text(ev, key):
            raise Refused("%s needs %s" % (kind, key))
    run = ev.get("run")
    if run is not None and (not isinstance(run, str) or not RUN_RE.match(run)):
        raise Refused("run id %r is not [A-Za-z0-9._-]{1,80}" % (run,))
    note = ev.get("note")
    if note is not None and not isinstance(note, str):
        raise Refused("note is not text")


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
        impl.lock(f)
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


def write(path, ev):
    """Check ev against the log's state and append it, under the lock.
    Returns (state after, the line appended). A refusal names the holder."""
    ev = dict(ev, ts=now())
    ev = {k: v for k, v in ev.items() if v is not None}
    with locked(path) as f:
        state = _read(f)
        try:
            if ev["event"] == "clear":
                check_holder_gone(state)
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
    a = p.parse_args(argv)
    try:
        path = a.log or default_log()
        if a.cmd == "init":
            print(init(path, a.who))
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
