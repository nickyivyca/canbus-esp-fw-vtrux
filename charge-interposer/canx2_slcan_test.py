#!/usr/bin/env python3
"""Test of canx2_slcan.py: before a bench run identifies or flashes the X2,
it detaches canx2 from the kernel's SLCAN if it is attached, through canctl,
and checks the state afterwards. No hardware and no real canctl: a fake
canctl (a script this test writes) acts on a made-up sysfs net folder and a
made-up /proc, where slcand processes are folders with a cmdline file.

    python3.13 -B charge-interposer/canx2_slcan_test.py [--can-fail]     (madhouse)
    py -3.14 -B charge-interposer/canx2_slcan_test.py [--can-fail]       (Windows)

Each case names what it checks:
  1. off-linux      off Linux, start() is ok, says so and runs no canctl,
                    even with canx2 attached
  2. not-attached   no netdev canx2 and no slcand for it (an slcand for
                    another role, and other processes, in /proc): ok, "not
                    attached", no canctl
  3. detach         netdev canx2 and `slcand /dev/ttyACM1 canx2`: start()
                    runs `canctl slcan detach canx2` (exactly that) once
                    and is ok with both gone; the detail says it was
                    attached and detached; its log is in logdir; the other
                    role's slcand is untouched
  4. half-attached  only the netdev, or only the slcand: either counts as
                    attached and canctl runs
  5. canctl-rc      canctl exiting 2 (doing nothing) or 3 (even with both
                    gone): not ok, naming canctl, its rc and what it means
  6. still-attached canctl exiting 0 but leaving the netdev, or the slcand:
                    not ok, "still attached", not naming a canctl failure
  7. poll           the slcand going only after two polls: ok; never going:
                    not ok once DETACH_WAIT_S has passed on the (fake)
                    clock, not before
  8. hooks          hooks(): on the X2 the step runs before the start hook
                    given, and the result is not ok when either fails or the
                    hook given raises; a failed step (still attached, or
                    canctl exiting 2) returns without running the hook given
                    at all, saying so; the end hook is the one given,
                    unchanged; on the WiCAN the start hook given runs alone,
                    its result as it returned it, and no canctl runs
The fake canctl refuses (exit 2) anything but `slcan detach canx2`, and,
as the real one does, refuses when no slcand runs for the role.
--can-fail runs the cases on the unedited canx2_slcan.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail.

Charge-interposer tester, 2026-11-13 (the charge-interposer reviewer's
run-start batch).
"""

import importlib.util
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "canx2_slcan.py")
CALL = "canctl slcan detach canx2"

FAKE_CANCTL = r'''
import json, os, shutil, sys
ctl, args = sys.argv[1], sys.argv[2:]
with open(ctl, encoding="utf-8") as f:
    c = json.load(f)
with open(c["log"], "a", encoding="utf-8") as f:
    f.write("canctl " + " ".join(args) + "\n")
if args != ["slcan", "detach", "canx2"]:
    print("canctl: refused")
    sys.exit(2)
if c.get("act", True):
    net = os.path.join(c["net"], "canx2")
    if c.get("drop_net", True) and os.path.isdir(net):
        os.rmdir(net)
    for pid in c.get("kill", []):
        shutil.rmtree(os.path.join(c["proc"], str(pid)), ignore_errors=False)
print("canx2 detached")
sys.exit(c.get("rc", 0))
'''

PID_X2, PID_OTHER, PID_SHELL = 4242, 4343, 17


def load(path):
    spec = importlib.util.spec_from_file_location("canx2_slcan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Bench:
    """A made-up net folder and /proc, and a fake canctl on them, patched
    into the module M. netdev: netdev canx2 present; slcand: an slcand for
    canx2 running. Always present: an slcand for another role, a shell
    naming canx2 in its command, a candump on canx2 (not slcand, so not
    attached) and a non-numeric /proc entry."""

    def __init__(self, M, work, netdev=True, slcand=True, **ctl):
        self.M, self.work = M, work
        self.net = os.path.join(work, "sys", "class", "net")
        self.proc = os.path.join(work, "proc")
        os.makedirs(os.path.join(self.net, "eno1"))
        os.makedirs(os.path.join(self.proc, "self"))
        if netdev:
            os.makedirs(os.path.join(self.net, "canx2"))
        if slcand:
            self.process(PID_X2, ["/usr/bin/slcand", "/dev/ttyACM1", "canx2"])
        self.process(PID_OTHER, ["/usr/bin/slcand", "/dev/ttyACM5", "canother"])
        self.process(PID_SHELL, ["bash", "-c", "echo canx2"])
        self.process(PID_SHELL + 1, ["/usr/bin/candump", "canx2"])
        self.log = os.path.join(work, "calls.txt")
        open(self.log, "w").close()
        self.ctl = os.path.join(work, "canctl.json")
        ctl.setdefault("kill", [PID_X2] if slcand else [])
        self.set(**ctl)
        script = os.path.join(work, "fake_canctl.py")
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write(FAKE_CANCTL)
        self.logdir = os.path.join(work, "logdir")
        os.makedirs(self.logdir)
        M.CANCTL = [sys.executable, "-B", script, self.ctl]
        M.SYSFS_NET, M.PROC = self.net, self.proc
        self.clock = [0.0]
        self.sleeps = []
        M.monotonic = lambda: self.clock[0]
        M.sleep = self._sleep
        self.on_sleep = None

    def process(self, pid, argv):
        d = os.path.join(self.proc, str(pid))
        os.makedirs(d)
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"\0".join(a.encode() for a in argv) + b"\0")

    def set(self, **ctl):
        c = dict(log=self.log, net=self.net, proc=self.proc)
        c.update(ctl)
        with open(self.ctl, "w", encoding="utf-8") as f:
            json.dump(c, f)

    def _sleep(self, s):
        self.sleeps.append(s)
        self.clock[0] += s
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))

    def calls(self):
        with open(self.log, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]

    def netdev(self):
        return os.path.isdir(os.path.join(self.net, "canx2"))

    def alive(self, pid):
        return os.path.isdir(os.path.join(self.proc, str(pid)))


def _sub(work, name):
    d = os.path.join(work, name)
    os.makedirs(d)
    return d


def case_off_linux(M, work):
    b = Bench(M, work)
    out = M.start(b.logdir, "win32")
    assert out["ok"] is True and "off Linux" in out["detail"], out
    assert b.calls() == [] and b.netdev() and b.alive(PID_X2), b.calls()


def case_not_attached(M, work):
    b = Bench(M, work, netdev=False, slcand=False)
    out = M.start(b.logdir, "linux")
    assert out["ok"] is True and "not attached" in out["detail"], out
    assert out["found"]["attached"] is False, out
    assert b.calls() == [], b.calls()
    assert b.alive(PID_OTHER)


def case_detach(M, work):
    b = Bench(M, work)
    out = M.start(b.logdir, "linux")
    assert out["ok"] is True, out
    assert b.calls() == [CALL], b.calls()
    assert not b.netdev() and not b.alive(PID_X2), (b.netdev(), b.alive(PID_X2))
    assert b.alive(PID_OTHER)
    d = out["detail"]
    assert "was attached" in d and "netdev canx2 present" in d and \
        "slcand pid %d" % PID_X2 in d and "detached" in d, d
    f = out["found"]
    assert f["attached"] is True and f["canctl_rc"] == 0 and \
        "netdev canx2 absent" in f["after"] and "no slcand" in f["after"], f
    logs = os.listdir(b.logdir)
    assert len(logs) == 1 and logs[0].startswith("canx2_detach_"), logs


def case_half_attached(M, work):
    for name, kw in (("netdev-only", dict(slcand=False)),
                     ("slcand-only", dict(netdev=False))):
        b = Bench(M, _sub(work, name), **kw)
        out = M.start(b.logdir, "linux")
        assert b.calls() == [CALL], (name, b.calls())
        assert out["ok"] is True and out["found"]["attached"] is True, (name, out)
        assert not b.netdev() and not b.alive(PID_X2), name


def case_canctl_rc(M, work):
    b = Bench(M, _sub(work, "rc2"), rc=2, act=False)
    out = M.start(b.logdir, "linux")
    assert out["ok"] is False and "canctl slcan detach canx2 failed (rc 2, refused)" in \
        out["detail"], out
    b = Bench(M, _sub(work, "rc3"), rc=3)
    out = M.start(b.logdir, "linux")
    assert not b.netdev() and not b.alive(PID_X2)
    assert out["ok"] is False and "failed (rc 3, did not take)" in out["detail"], out


def case_still_attached(M, work):
    for name, ctl in (("noop", dict(act=False)), ("net-left", dict(drop_net=False)),
                      ("slcand-left", dict(kill=[]))):
        b = Bench(M, _sub(work, name), **ctl)
        out = M.start(b.logdir, "linux")
        assert b.calls() == [CALL], (name, b.calls())
        assert out["ok"] is False and "still attached" in out["detail"] and \
            "failed (rc" not in out["detail"], (name, out)


def case_poll(M, work):
    b = Bench(M, _sub(work, "late"), kill=[])

    def late(n):
        if n == 2:
            shutil.rmtree(os.path.join(b.proc, str(PID_X2)))
    b.on_sleep = late
    out = M.start(b.logdir, "linux")
    assert out["ok"] is True and len(b.sleeps) == 2, (out, b.sleeps)
    b = Bench(M, _sub(work, "never"), kill=[])
    out = M.start(b.logdir, "linux")
    assert out["ok"] is False and "slcand pid %d" % PID_X2 in out["found"]["after"], out
    assert b.clock[0] >= M.DETACH_WAIT_S and b.clock[0] - M.POLL_S < M.DETACH_WAIT_S, \
        (b.clock[0], b.sleeps)


def case_hooks(M, work):
    b = Bench(M, _sub(work, "x2"))

    def inner(step, ok=True, boom=False):
        def hook(board, img, ctx, logdir):
            with open(b.log, "a", encoding="utf-8") as f:
                f.write("inner %s %s\n" % (step, board))
            if boom:
                raise RuntimeError("inner %s raised" % step)
            return dict(ok=ok, detail="inner %s %s" % (step, board), found={"k": step})
        return hook

    end = inner("end")
    s, e = M.hooks(inner("start"), end, platform="linux")
    assert e is end, e
    out = s("x2", None, None, b.logdir)
    assert out["ok"] is True and b.calls() == [CALL, "inner start x2"], (out, b.calls())
    assert out["found"]["k"] == "start" and out["found"]["canx2"]["canctl_rc"] == 0, out
    for name, hk in (("fails", inner("start", ok=False)), ("raises", inner("start", boom=True))):
        b = Bench(M, _sub(work, name))
        s, _e = M.hooks(hk, end, platform="linux")
        out = s("x2", None, None, b.logdir)
        assert b.calls() == [CALL, "inner start x2"], (name, b.calls())
        assert out["ok"] is False, (name, out)
        if name == "raises":
            assert "inner start raised" in out["detail"], out
    # a failed step: the hook given is not run at all, and the result says so
    for name, ctl in (("stepfails", dict(act=False)), ("steprc", dict(rc=2, act=False))):
        b = Bench(M, _sub(work, name), **ctl)
        s, _e = M.hooks(inner("start"), end, platform="linux")
        out = s("x2", None, None, b.logdir)
        assert b.calls() == [CALL], (name, b.calls())
        assert out["ok"] is False and "was not run" in out["detail"] and \
            "inner start" not in out["detail"], (name, out)
        assert out["found"]["canx2"]["attached"] is True, (name, out)
    b = Bench(M, _sub(work, "wican"))
    s, _e = M.hooks(inner("start"), end, platform="linux")
    out = s("wican", None, None, b.logdir)
    assert out == dict(ok=True, detail="inner start wican", found={"k": "start"}), out
    assert b.calls() == ["inner start wican"] and b.netdev(), b.calls()


CASES = [case_off_linux, case_not_attached, case_detach, case_half_attached,
         case_canctl_rc, case_still_attached, case_poll, case_hooks]

MUTANTS = [
    ("the hook given run after a failed step",
     "        if not mine.get(\"ok\"):\n            # the reviewer's",
     "        if False:\n            # the reviewer's"),
    ("the step skipped (hooks)",
     "        mine = start(logdir, platform)\n",
     "        mine = dict(ok=True, found=None)\n"),
    ("the step after the hook given",
     "        mine = start(logdir, platform)\n",
     "        _call(on_start, b, img, ctx, logdir)\n"
     "        mine = start(logdir, platform)\n"),
    ("a failed step's early return reported ok",
     "            return dict(ok=False, detail=\"%s; the rest of the X2's start hook \"",
     "            return dict(ok=True, detail=\"%s; the rest of the X2's start hook \""),
    ("the step on every board", "        if b != board:\n            return on_start(",
     "        if False:\n            return on_start("),
    ("a raising hook not caught",
     "    except Exception as e:                              # noqa: BLE001\n"
     "        return dict(ok=False, detail=\"raised %r\" % (e,))",
     "    except ZeroDivisionError:\n        raise"),
    ("the hook given's failure ignored in the result",
     "    return dict(ok=bool(mine.get(\"ok\")) and bool(theirs.get(\"ok\")),",
     "    return dict(ok=bool(mine.get(\"ok\")),"),
    ("canctl run off Linux",
     "        return dict(ok=True, detail=\"canx2: no slcan step off Linux\")",
     "        pass"),
    ("canctl's exit code ignored", "    if rc != 0:\n        return dict(ok=False, detail=\"canx2: attached",
     "    if False:\n        return dict(ok=False, detail=\"canx2: attached"),
    ("the state after not checked", "    if netdev or pids:\n        return dict(ok=False",
     "    if False:\n        return dict(ok=False"),
    ("only the netdev checked after", "    if netdev or pids:\n        return dict(ok=False",
     "    if netdev:\n        return dict(ok=False"),
    ("only the netdev counts as attached", "    if not netdev and not pids:",
     "    if not netdev:"),
    ("only slcand counts as attached", "    if not netdev and not pids:",
     "    if not pids:"),
    ("canctl run even when not attached", "    if not netdev and not pids:\n",
     "    if False:\n"),
    ("any slcand counts", "        if len(argv) >= 2 and os.path.basename(argv[0]) == SLCAND and argv[-1] == ROLE:",
     "        if len(argv) >= 2 and os.path.basename(argv[0]) == SLCAND:"),
    ("any process naming the role counts",
     "        if len(argv) >= 2 and os.path.basename(argv[0]) == SLCAND and argv[-1] == ROLE:",
     "        if ROLE in argv:"),
    ("no poll", "        if not (netdev or pids) or monotonic() >= t_end:", "        if True:"),
    ("polls without end", "        if not (netdev or pids) or monotonic() >= t_end:",
     "        if not (netdev or pids) or monotonic() >= t_end + 100:"),
    ("the wrong canctl command", "    argv = list(CANCTL) + [\"slcan\", \"detach\", ROLE]",
     "    argv = list(CANCTL) + [\"down\", ROLE]"),
]


def run_cases(path, base):
    M = load(path)
    bad = []
    for c in CASES:
        work = os.path.join(base, c.__name__)
        os.makedirs(work)
        try:
            c(M, work)
            print("ok    %s" % c.__name__, flush=True)
        except Exception as e:                          # noqa: BLE001
            print("FAIL  %s  %s: %s" % (c.__name__, type(e).__name__, str(e)[:300]), flush=True)
            bad.append(c.__name__)
    return bad


def main(argv):
    with scratch_dir.run("canx2-slcan-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\ncanx2_slcan_test: %s" % ("all checks OK" if not bad
                                             else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited canx2_slcan.py", flush=True)
        if run_cases(MODULE, os.path.join(base, "control")):
            print("\nREFUSED: the control fails")
            return 1
        with open(MODULE, encoding="utf-8") as f:
            src = f.read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)), flush=True)
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            p = os.path.join(d, "canx2_slcan.py")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_cases(p, os.path.join(d, "t"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad
                                      else "STILL PASSES -- nothing checks it"), flush=True)
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
