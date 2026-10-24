"""session_compact.py -- a terminal Claude Code session compacts its own context, then resumes.

Plan: $REVERSE_IT_DIR/tools/plans/session-compact.md. Procedure: SKILL.md beside this file.

    <python> <this file's absolute path> --compact "<what to keep>" --resume "<next assigned step>" [--check]

0. Refuses, typing nothing, if this file is untracked or differs from HEAD in its git worktree.
1. Validates both messages (mandatory; one line of printable ASCII, at most 600 characters;
   the resume message cannot start with / ! or #; neither may end in a backslash).
2. Resolves the target: only the Claude Code process this tool runs under ($CLAUDE_PID, which
   must be in this process's own ancestry). Linux: through $TMUX_PANE only. Windows: through
   that process's own console only. No window, focus or mouse automation, and no option to
   name another target.
3. Takes a per-session lock, types "/compact <msg>" and Enter (held by Claude Code until the
   turn ends), starts a detached waiter and returns.
4. The waiter watches the session transcript for a manual compact_boundary written after the
   request, then types the resume message and Enter. If none arrives before the timeout it
   types nothing. The waiter records itself in the lock; a later call clears a lock whose
   owner is no longer running, or one older than the timeout plus 5 minutes.

Every call is logged to scratch/session-compact/session_compact_<hostname>.log.jsonl in the
worktree (gitignored).
Output is ASCII only.
"""
import argparse
import datetime
import glob
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

MAX_LEN = 600
TIMEOUT_S = 3600
POLL_S = 2.0
ENTER_DELAY_S = 0.3
SETTLE_S = 1.5
RESUME_FORBIDDEN_START = ("/", "!", "#")
IS_WIN = os.name == "nt"

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(tempfile.gettempdir(), "session-compact")


class Refused(Exception):
    pass


def git(*args):
    return subprocess.run(["git", "-C", HERE] + list(args), capture_output=True, text=True, timeout=30)


def repo_root():
    try:
        r = git("rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


ROOT = repo_root()
LOG_NAME = "session_compact_%s.log.jsonl" % socket.gethostname()
LOG_PATH = (os.path.join(ROOT, "scratch", "session-compact", LOG_NAME) if ROOT
            else os.path.join(STATE_DIR, LOG_NAME))


def self_check():
    """Raise Refused unless this file is tracked and identical to HEAD."""
    me = os.path.basename(os.path.abspath(__file__))
    try:
        tracked = git("ls-files", "--error-unmatch", "--", me)
        if tracked.returncode != 0:
            raise Refused("this tool's file is not tracked by git: %s" % os.path.abspath(__file__))
        changed = git("diff", "--quiet", "HEAD", "--", me)
    except (OSError, subprocess.SubprocessError) as e:
        raise Refused("cannot run git to check this tool's file: %s" % e)
    if changed.returncode == 1:
        raise Refused("this tool's file differs from HEAD: %s" % os.path.abspath(__file__))
    if changed.returncode != 0:
        raise Refused("git diff on this tool's file failed: %s" % changed.stderr.strip())


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def log(record):
    record = dict(record, ts=now_utc().isoformat(timespec="seconds"), host=socket.gethostname())
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="ascii", errors="replace") as f:
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- validation

def validate(name, msg, resume):
    if msg is None or not msg.strip():
        raise Refused("%s message is missing or empty" % name)
    if msg != msg.strip():
        raise Refused("%s message has leading or trailing whitespace" % name)
    if len(msg) > MAX_LEN:
        raise Refused("%s message is %d characters; the limit is %d" % (name, len(msg), MAX_LEN))
    bad = [c for c in msg if not (0x20 <= ord(c) <= 0x7E)]
    if bad:
        raise Refused("%s message must be one line of printable ASCII (found %r)" % (name, bad[0]))
    if msg.endswith("\\"):
        raise Refused("%s message must not end in a backslash (line continuation)" % name)
    if resume and msg.startswith(RESUME_FORBIDDEN_START):
        raise Refused("resume message must not start with / ! or # (it must be plain text)")


# ---------------------------------------------------------------- processes

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    class KEY_EVENT_RECORD(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                    ("UnicodeChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]

    class _EVENT(ctypes.Union):
        _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]

    class INPUT_RECORD(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]


def process_table():
    """pid -> (ppid, image name)."""
    table = {}
    if IS_WIN:
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            table[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
        return table
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % d) as f:
                stat = f.read()
        except OSError:
            continue
        name = stat[stat.find("(") + 1:stat.rfind(")")]
        ppid = int(stat[stat.rfind(")") + 2:].split()[1])
        table[int(d)] = (ppid, name)
    return table


def ancestors(pid, table):
    chain, seen = [], set()
    while pid in table and pid not in seen and pid > 0:
        seen.add(pid)
        chain.append(pid)
        pid = table[pid][0]
    return chain


def alive(pid):
    if IS_WIN:
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259
    return os.path.exists("/proc/%d" % pid)


def resolve_target():
    table = process_table()
    mine = ancestors(os.getpid(), table)[1:]
    env = os.environ.get("CLAUDE_PID", "")
    if env.isdigit():
        pid = int(env)
        if pid not in mine:
            raise Refused("CLAUDE_PID %d is not an ancestor of this process" % pid)
        return pid, "CLAUDE_PID"
    for pid in mine:
        if table[pid][1].lower() in ("claude", "claude.exe"):
            return pid, "ancestor " + table[pid][1]
    raise Refused("no Claude Code process found among this process's ancestors")


# ---------------------------------------------------------------- input backends

def tmux(*args):
    return subprocess.run(["tmux"] + list(args), capture_output=True, text=True, timeout=10)


def check_backend(target):
    """Raise Refused unless keys can reach the target's own terminal input."""
    if IS_WIN:
        attach(target)
        k32.FreeConsole()
        return {"backend": "windows-console"}
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        raise Refused("not inside tmux ($TMUX_PANE unset); on Linux only tmux sessions are supported")
    r = tmux("display-message", "-p", "-t", pane, "#{pane_pid}")
    if r.returncode != 0 or not r.stdout.strip().isdigit():
        raise Refused("tmux pane %s not found: %s" % (pane, r.stderr.strip()))
    pane_pid = int(r.stdout.strip())
    if pane_pid not in ancestors(target, process_table()):
        raise Refused("tmux pane %s (pid %d) does not contain process %d" % (pane, pane_pid, target))
    return {"backend": "tmux", "pane": pane}


def attach(target):
    k32.FreeConsole()
    if not k32.AttachConsole(wintypes.DWORD(target)):
        raise Refused("AttachConsole(%d) failed, error %d" % (target, ctypes.get_last_error()))
    h = k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
    if h in (None, ctypes.c_void_p(-1).value):
        k32.FreeConsole()
        raise Refused("cannot open the console input of %d, error %d" % (target, ctypes.get_last_error()))
    k32.CloseHandle(h)


def win_write(target, text, enter):
    attach(target)
    try:
        h = k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
        recs = []
        keys = [(c, None) for c in text] + ([("\r", 0x0D)] if enter else [])
        for ch, vk in keys:
            if vk is None:
                vks = u32.VkKeyScanW(wintypes.WCHAR(ch))
                vk = vks & 0xFF if vks != -1 else 0
                state = 0x0010 if vks != -1 and (vks >> 8) & 1 else 0
            else:
                state = 0
            scan = u32.MapVirtualKeyW(vk, 0) if vk else 0
            for down in (True, False):
                r = INPUT_RECORD()
                r.EventType = 0x0001
                r.Event.KeyEvent = KEY_EVENT_RECORD(down, 1, vk, scan, ch, state)
                recs.append(r)
        arr = (INPUT_RECORD * len(recs))(*recs)
        written = wintypes.DWORD()
        ok = k32.WriteConsoleInputW(h, arr, len(recs), ctypes.byref(written))
        k32.CloseHandle(h)
        if not ok or written.value != len(recs):
            raise Refused("WriteConsoleInputW wrote %d of %d records, error %d"
                          % (written.value, len(recs), ctypes.get_last_error()))
    finally:
        k32.FreeConsole()


def type_line(target, backend, text):
    """Type text, pause, then press Enter as a separate key."""
    if IS_WIN:
        win_write(target, text, False)
        time.sleep(ENTER_DELAY_S)
        win_write(target, "", True)
        return
    pane = backend["pane"]
    r = tmux("send-keys", "-t", pane, "-l", text)
    if r.returncode != 0:
        raise Refused("tmux send-keys failed: %s" % r.stderr.strip())
    time.sleep(ENTER_DELAY_S)
    r = tmux("send-keys", "-t", pane, "Enter")
    if r.returncode != 0:
        raise Refused("tmux send-keys Enter failed: %s" % r.stderr.strip())


# ---------------------------------------------------------------- transcript and lock

def find_transcript(session_id):
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    hits = glob.glob(os.path.join(base, "projects", "*", session_id + ".jsonl"))
    if len(hits) != 1:
        raise Refused("expected one transcript for session %s under %s, found %d"
                      % (session_id, base, len(hits)))
    return hits[0]


def lock_path(session_id):
    return os.path.join(STATE_DIR, session_id + ".lock")


def stale_lock(path):
    """Why an existing lock is stale, or None if it still counts as held.

    The lock names its owner: the calling tool until the waiter starts, then the waiter. A dead
    owner frees it at once. Age is the backstop for a reused PID; an empty or unreadable lock
    (caught mid-write) is judged by age only.
    """
    if time.time() - os.path.getmtime(path) > TIMEOUT_S + 300:
        return "older than %d s" % (TIMEOUT_S + 300)
    try:
        with open(path, encoding="ascii") as f:
            owner = json.load(f)
        pid = int(owner.get("waiter") or owner["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not alive(pid):
        return "owner pid %d is not running" % pid
    return None


def take_lock(session_id):
    """Create the session's lock; return a note if a stale one was cleared first."""
    os.makedirs(STATE_DIR, exist_ok=True)
    path = lock_path(session_id)
    cleared = None
    if os.path.exists(path):
        cleared = stale_lock(path)
        if cleared:
            os.remove(path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Refused("a self-compact request for this session is already pending (%s)" % path)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"pid": os.getpid(), "ts": now_utc().isoformat()}))
    return cleared


def claim_lock(session_id):
    """The waiter records itself as the lock's owner, so a waiter that dies frees the lock."""
    with open(lock_path(session_id), "w", encoding="ascii") as f:
        f.write(json.dumps({"pid": os.getpid(), "waiter": os.getpid(), "ts": now_utc().isoformat()}))


def drop_lock(session_id):
    try:
        os.remove(lock_path(session_id))
    except OSError:
        pass


def boundary_found(path, offset, tail, session_id, since):
    """Read new transcript bytes; return (found, offset, tail)."""
    size = os.path.getsize(path)
    if size < offset:
        offset, tail = 0, b""
    if size == offset:
        return False, offset, tail
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(size - offset)
    offset = size
    lines = (tail + data).split(b"\n")
    tail = lines.pop()
    for raw in lines:
        if b"compact_boundary" not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        meta = rec.get("compactMetadata") or {}
        if (rec.get("type") == "system" and rec.get("subtype") == "compact_boundary"
                and rec.get("sessionId") == session_id and meta.get("trigger") == "manual"):
            ts = rec.get("timestamp", "").replace("Z", "+00:00")
            try:
                if datetime.datetime.fromisoformat(ts) >= since:
                    return True, offset, tail
            except ValueError:
                continue
    return False, offset, tail


# ---------------------------------------------------------------- detached waiter

def spawn_waiter(request_path):
    args = [sys.executable, os.path.abspath(__file__), "--_wait", request_path]
    common = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    if not IS_WIN:
        return subprocess.Popen(args, start_new_session=True, **common).pid
    base = 0x00000008 | 0x00000200
    try:
        return subprocess.Popen(args, creationflags=base | 0x01000000, **common).pid
    except OSError:
        return subprocess.Popen(args, creationflags=base, **common).pid


def wait_and_resume(request_path):
    with open(request_path, encoding="ascii") as f:
        req = json.load(f)
    sid, target = req["session_id"], req["target"]
    since = datetime.datetime.fromisoformat(req["since"])
    deadline = time.time() + req["timeout"]
    offset, tail = req["offset"], b""
    base = {"session": sid, "target": target, "resume": req["resume"]}
    try:
        claim_lock(sid)
        while time.time() < deadline:
            if not alive(target):
                log(dict(base, outcome="target-gone", note="nothing typed"))
                return
            found, offset, tail = boundary_found(req["transcript"], offset, tail, sid, since)
            if found:
                time.sleep(SETTLE_S)
                if not alive(target):
                    log(dict(base, outcome="target-gone", note="after compaction; nothing typed"))
                    return
                backend = check_backend(target)
                type_line(target, backend, req["resume"])
                log(dict(base, outcome="resumed"))
                return
            time.sleep(POLL_S)
        log(dict(base, outcome="timeout", note="no manual compaction seen; nothing typed"))
    except Exception as e:
        log(dict(base, outcome="error", note=str(e)))
    finally:
        drop_lock(sid)
        try:
            os.remove(request_path)
        except OSError:
            pass


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Compact this Claude Code session, then resume it.")
    ap.add_argument("--compact", help="instructions for /compact (what the summary must keep)")
    ap.add_argument("--resume", help="plain-text message typed after the compaction (the next assigned step)")
    ap.add_argument("--check", action="store_true", help="validate and resolve the target; type nothing")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S,
                    help="seconds to wait for the compaction (at most %d)" % TIMEOUT_S)
    ap.add_argument("--_wait", help=argparse.SUPPRESS)
    a = ap.parse_args()

    if a._wait:
        wait_and_resume(a._wait)
        return 0

    result = {"compact": a.compact, "resume": a.resume, "check": a.check}
    try:
        self_check()
        validate("compact", a.compact, False)
        validate("resume", a.resume, True)
        if not 1 <= a.timeout <= TIMEOUT_S:
            raise Refused("--timeout must be 1..%d" % TIMEOUT_S)
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        if not sid:
            raise Refused("CLAUDE_CODE_SESSION_ID is unset; run this as a Claude Code tool call")
        target, how = resolve_target()
        transcript = find_transcript(sid)
        backend = check_backend(target)
        result.update(session=sid, target=target, target_from=how, transcript=transcript, **backend)
        if a.check:
            result["outcome"] = "check-ok"
            print(json.dumps(result, ensure_ascii=True, indent=1))
            return 0

        cleared = take_lock(sid)
        if cleared:
            result["stale_lock_cleared"] = cleared
        try:
            since = now_utc()
            req = {"session_id": sid, "target": target, "transcript": transcript,
                   "offset": os.path.getsize(transcript), "since": since.isoformat(),
                   "timeout": a.timeout, "resume": a.resume}
            request_path = os.path.join(STATE_DIR, sid + ".request.json")
            with open(request_path, "w", encoding="ascii") as f:
                json.dump(req, f)
            type_line(target, backend, "/compact " + a.compact)
            result["waiter"] = spawn_waiter(request_path)
        except Exception:
            drop_lock(sid)
            raise
        result["outcome"] = "queued"
        log({k: result.get(k) for k in ("session", "target", "backend", "compact", "resume", "waiter",
                                        "stale_lock_cleared", "outcome")})
        print(json.dumps(result, ensure_ascii=True, indent=1))
        print("Compaction queued; it runs when this turn ends, then the resume message is typed. End the turn now.")
        return 0
    except Refused as e:
        result.update(outcome="refused", reason=str(e))
        if not a.check:
            log({"compact": a.compact, "resume": a.resume, "outcome": "refused", "reason": str(e)})
        print(json.dumps(result, ensure_ascii=True, indent=1))
        return 2


if __name__ == "__main__":
    sys.exit(main())
