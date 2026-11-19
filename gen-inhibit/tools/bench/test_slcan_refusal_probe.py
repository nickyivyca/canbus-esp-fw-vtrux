"""Harness test for slcan_refusal_probe (spec 3.2): its judges, and how a command whose send
raised and an arm that is not run are recorded (gen-inhibit reviewer, 2026-11-19, after run
cb08 judged the ws arm's ModuleNotFoundError "-> PASS").

    python3.13 -B gen-inhibit/tools/bench/test_slcan_refusal_probe.py [--mutants]   (from the repo root)

--mutants applies each edit in MUTANTS to a copy of slcan_refusal_probe.py in the worktree's
scratch/ and requires a FAIL line from this test on each (a crash is not a kill).
"""
import importlib.util
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

SCRIPT = os.environ.get("SLCAN_PROBE") or os.path.join(HERE, "slcan_refusal_probe.py")
_spec = importlib.util.spec_from_file_location("slcan_refusal_probe", SCRIPT)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def st(**kw):
    s = {"mode": 3, "bus_on": True, "drv_state": "running", "inhibit_live": True, "self_off": "",
         "disabled": False, "arm_block": "", "abort_reason": "", "tx_ok": 100, "keys": ["mode"]}
    s.update(kw)
    return s


def closed_port():
    """A localhost TCP port with nothing listening (bound, then closed)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    off = dict(mode=0, bus_on=False, drv_state=None, inhibit_live=False, tx_ok=0)
    checks = [
        ("armed, nothing changes, device keeps answering -> PASS",
         P.judge("armed", st(), st(tx_ok=150), {0x051})["verdict"] == "PASS"),
        ("armed, a close forces mode OFF -> FAIL",
         P.judge("armed", st(), st(mode=0, inhibit_live=False, tx_ok=150), set())["verdict"] == "FAIL"),
        ("armed, the device stops answering -> FAIL",
         P.judge("armed", st(), st(tx_ok=100), {0x051})["verdict"] == "FAIL"),
        ("the injected frame on the wire -> FAIL",
         P.judge("armed", st(), st(tx_ok=150), {0x6A6})["verdict"] == "FAIL"),
        ("OFF, an SLCAN open installs the driver -> FAIL",
         P.judge("off", st(**off), st(**dict(off, drv_state="running", bus_on=True)), set())["verdict"] == "FAIL"),
        ("OFF, a WiCAN frame appears -> FAIL", P.judge("off", st(**off), st(**off), {0x7F1})["verdict"] == "FAIL"),
        ("OFF, nothing changes -> PASS", P.judge("off", st(**off), st(**off), {0x123})["verdict"] == "PASS"),
        ("OFF: the X2's pages (0x7F4-0x7F7) and the replayed VCM's 0x051 are not the WiCAN's",
         P.judge("off", st(**off), st(**off), {0x051, 0x7F4, 0x7F5, 0x7F6, 0x7F7})["verdict"] == "PASS"),
        ("a page key that appears after the command is reported",
         P.judge("off", st(**off), st(**dict(off, keys=["mode", "slcan_refused"])), set())["new_page_keys"]
         == ["slcan_refused"]),
        ("an unread page is UNREAD, never PASS", P.judge("off", None, st(**off), set())["verdict"] == "UNREAD"),
    ]
    C = P.judge_counted
    r0 = dict(refused_slcan=4, refused_can_send=1)
    checks += [
        ("counted: refused_slcan +1 -> PASS",
         C(st(**r0), st(**dict(r0, refused_slcan=5)))["counted"] == "PASS"),
        ("counted: refused_can_send +1 instead (wrong path booked) -> FAIL",
         C(st(**r0), st(**dict(r0, refused_can_send=2)))["counted"].startswith("FAIL")),
        ("counted: both +1 (counted twice) -> FAIL",
         C(st(**r0), st(**dict(r0, refused_slcan=5, refused_can_send=2)))["counted"].startswith("FAIL")),
        ("counted: refused_slcan +2 for one command -> FAIL",
         C(st(**r0), st(**dict(r0, refused_slcan=6)))["counted"].startswith("FAIL")),
        ("counted: neither rises -> FAIL (silently dropped)", C(st(**r0), st(**r0))["counted"].startswith("FAIL")),
        ("counted: one down, one up (sum 0) -> FAIL",
         C(st(**r0), st(**dict(r0, refused_slcan=5, refused_can_send=0)))["counted"].startswith("FAIL")),
        ("counted: a counter going down by a reboot is FAIL, even if the other rose by 2",
         C(st(**r0), st(**dict(r0, refused_slcan=6, refused_can_send=0)))["counted"].startswith("FAIL")
         and "went down" in C(st(**r0), st(**dict(r0, refused_slcan=6, refused_can_send=0)))["counted"]),
        ("counted: the delta per counter is recorded",
         C(st(**r0), st(**dict(r0, refused_slcan=6)))["refused_delta"]
         == {"refused_slcan": 2, "refused_can_send": 0}),
        ("counted: a page without the counters (old build) -> UNRESOLVED, never PASS",
         C(st(), st())["counted"].startswith("UNRESOLVED")),
        ("counted: one counter missing after -> UNRESOLVED",
         C(st(**r0), st(refused_slcan=5))["counted"].startswith("UNRESOLVED")),
        ("counted: unread page -> UNREAD", C(None, st(**r0))["counted"] == "UNREAD"),
        ("state(): the counters are copied from the page when present, absent when not",
         P.state({"mode": 0, "refused_slcan": 3, "refused_can_send": 0}).get("refused_slcan") == 3
         and "refused_slcan" not in P.state({"mode": 0})),
    ]

    # A send that raised is not a reply (run cb08: the ws arm's ModuleNotFoundError read "-> PASS").
    ok, reply = P.channel_send("tcp", "127.0.0.1", closed_port(), "O\r", timeout=0.2)
    checks.append(("send: a refused connection is not sent, and says why",
                   ok is False and reply.startswith("ERROR ConnectionRefusedError")))
    saved = sys.modules.get("websockets.sync.client", "absent")
    sys.modules["websockets.sync.client"] = None     # the import raises ImportError
    try:
        ok_ws, reply_ws = P.channel_send("ws", "127.0.0.1", 0, "O\r")
    finally:
        if saved == "absent":
            sys.modules.pop("websockets.sync.client", None)
        else:
            sys.modules["websockets.sync.client"] = saved
    checks.append(("send: a websocket library that will not import is not sent, and says why",
                   ok_ws is False and reply_ws.startswith("ERROR ") and "Error" in reply_ws))
    good = st(**dict(off, **dict(r0, refused_slcan=5)))
    err = P.command_result("off", "ws", "O", False, "ERROR ModuleNotFoundError: x", st(**dict(off, **r0)),
                           st(**dict(off, **r0)), [], 0)
    checks += [
        ("command: a send that raised is ERROR, and neither judge runs",
         err["verdict"] == "ERROR" and err["counted"] == "ERROR" and "refused_delta" not in err),
        ("command: a send that raised gives its error as the reason", err["why"] == ["ERROR ModuleNotFoundError: x"]),
        ("command: a sent command is judged on the page and counted",
         P.command_result("off", "tcp", "O", True, "no reply", st(**dict(off, **r0)), good, [], 0)["verdict"]
         == "PASS"
         and P.command_result("off", "tcp", "O", True, "no reply", st(**dict(off, **r0)), good, [], 0)["counted"]
         == "PASS"),
        ("command: a sent command whose page changed still FAILs",
         P.command_result("off", "tcp", "O", True, "no reply", st(**dict(off, **r0)),
                          st(**dict(off, refused_slcan=5, refused_can_send=1, bus_on=True)), [], 0)["verdict"]
         == "FAIL"),
    ]
    el = P.line(err)
    checks += [
        ("line: an ERROR command's line reads -> ERROR and counted ERROR, never PASS",
         "-> ERROR" in el and "| counted ERROR" in el and "PASS" not in el),
        ("line: an arm not run reads NOT-RUN with its reason, and no -> verdict",
         P.line({"cond": "armed", "channel": "ws", "not_run": "websockets missing"})
         == "  armed ws  NOT-RUN websockets missing"),
    ]
    res = [err, {"cond": "armed", "channel": "ws", "not_run": "websockets missing"},
           P.command_result("off", "tcp", "O", True, "no reply", st(**dict(off, **r0)), good, [], 0)]
    s = P.summary(res)
    checks += [
        ("summary: commands count the commands sent or raised, not the arms not run", s["commands"] == 2),
        ("summary: an ERROR is counted as one", s["error"] == 1 and s["fail"] == 0),
        ("summary: each arm not run is named with its reason", s["not_run"] == ["armed/ws: websockets missing"]),
    ]
    saved_find = importlib.util.find_spec
    try:
        importlib.util.find_spec = lambda name, *a: None if name == "websockets" else saved_find(name, *a)
        miss = P.ws_missing()
        importlib.util.find_spec = lambda name, *a: object() if name == "websockets" else saved_find(name, *a)
        have = P.ws_missing()
    finally:
        importlib.util.find_spec = saved_find
    checks.append(("ws_missing: true exactly when websockets cannot be found", miss is True and have is False))
    slcan_tcp = {"protocol": "slcan", "port_type": "tcp", "port": "3333"}
    saved_ws = P.ws_missing
    try:
        P.ws_missing = lambda: True
        ws_gone = P.not_run_reason("ws", slcan_tcp)
        P.ws_missing = lambda: False
        ws_there = P.not_run_reason("ws", slcan_tcp)
    finally:
        P.ws_missing = saved_ws
    checks += [
        ("not run: the websocket arm without websockets", ws_gone == "websockets missing"),
        ("not run: the websocket arm runs with websockets", ws_there is None),
        ("not run: the tcp arm runs when the device speaks slcan over TCP", P.not_run_reason("tcp", slcan_tcp) is None),
        ("not run: the tcp arm on another protocol, naming it",
         P.not_run_reason("tcp", dict(slcan_tcp, protocol="realdash66")) == "protocol/port_type is realdash66/tcp"),
        ("not run: the tcp arm on another port type",
         P.not_run_reason("tcp", dict(slcan_tcp, port_type="udp")) == "protocol/port_type is slcan/udp"),
    ]

    fails = []
    for label, ok in checks:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


MUTANTS = [
    ("a send that raised reads as sent, as in run cb08",
     '        return False, "ERROR %s: %s" % (type(e).__name__, e)',
     '        return True, "ERROR %s: %s" % (type(e).__name__, e)'),
    ("a send that raised is judged on the page", "    if not sent:\n", "    if False:\n"),
    ("a send that raised is counted as FAIL, not ERROR",
     '"new_page_keys": [], "counted": "ERROR"}', '"new_page_keys": [], "counted": "FAIL"}'),
    ("an ERROR command's line hides the verdict",
     'r["reply"][:14], r["verdict"], "; ".join(r["why"]),', 'r["reply"][:14], "PASS", "; ".join(r["why"]),'),
    ("the ws arm runs without websockets", '    if ch == "ws" and ws_missing():\n', '    if False:\n'),
    ("the tcp arm runs on any protocol",
     '    if ch == "tcp" and (cfg.get("protocol") != "slcan" or cfg.get("port_type") != "tcp"):\n',
     '    if False:\n'),
    ("ws_missing never true",'    return importlib.util.find_spec("websockets") is None',
     "    return False"),
    ("the summary drops the errors", '"error": sum(1 for r in results if r.get("verdict") == "ERROR"),',
     '"error": 0,'),
    ("the summary drops the arms not run",
     '"not_run": ["%s/%s: %s" % (r["cond"], r["channel"], r["not_run"]) for r in results if "not_run" in r],',
     '"not_run": [],'),
    ("an arm not run reads as a command line",
     '        return "  %-5s %-3s NOT-RUN %s" % (r["cond"], r["channel"], r["not_run"])',
     '        return "  %-5s %-3s NOT-RUN %s -> PASS  | counted PASS" % (r["cond"], r["channel"], r["not_run"])'),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read()
    base = gi_paths.scratch("mutants-slcan_refusal_probe")
    killed = errors = 0
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print("ERROR    control: the unmutated probe fails this test (exit %d)" % r.returncode)
        return 1
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, "slcan_refusal_probe.py")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, SLCAN_PROBE=path), timeout=120)
        fl = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        dead = bool(fl) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fl),
                                                   ("; " + fl[0][:110]) if fl else (r.stderr.strip()[-120:] if r.stderr else "")))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
