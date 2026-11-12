#!/usr/bin/env python3
"""Test of canchg_mode.py: the bench run puts canchg in libusb mode at its
start and back on gs_usb at its end, through canctl, and checks each in
sysfs. No hardware and no real canctl: a fake canctl (a script this test
writes) acts on a made-up sysfs holding canmain (bound, netdev canmain) and
canchg, both 1d50:606f.

    python3.13 -B charge-interposer/canchg_mode_test.py [--can-fail]     (madhouse)
    py -3.14 -B charge-interposer/canchg_mode_test.py [--can-fail]       (Windows)

Each case names what it checks:
  1. off-linux      off Linux, start() and end() are ok, say so and run no
                    canctl
  2. start          canchg bound: start() runs `canctl mode canchg libusb`
                    (exactly that) once, and is ok with canchg unbound and
                    canmain untouched; its log is in logdir
  3. start-canctl   canctl exiting 2 (doing nothing) or 3 (even with canchg
                    unbound): not ok, the detail naming canctl, its rc and
                    what the rc means
  4. start-recheck  canctl exiting 0 but changing nothing (the canctl
                    installed before 2026-11-12 did that when an unbind did
                    not take), or VTRUX_CANCHG_DEVICE unset: not ok, the
                    detail naming the re-check and not canctl
  5. end            canchg unbound: end() runs `canctl mode canchg
                    socketcan --wait 10` once and is ok with interface 0 on
                    gs_usb and netdev canchg present
The fake canctl refuses (exit 2) what the real one refuses: anything but
`mode canchg libusb|socketcan`, with `--wait 1..99` only after socketcan
(canctl's header, 2026-11-12).
  6. end-check      canctl exiting 0 but changing nothing, or binding
                    interface 0 to another driver (usbfs, a pyusb claim), or
                    binding with no netdev ever: not ok, "not bound again";
                    canctl exiting 3: not ok, naming canctl and its rc
  7. end-poll       the netdev appearing only after two polls: ok, within
                    BIND_WAIT_S; it never appearing: not ok once BIND_WAIT_S
                    has passed on the (fake) clock, not before
  8. hooks          hooks(): on the X2, the start step runs before the hook
                    given and the end step after it; the end step runs, and
                    the result is not ok, when the hook given fails or
                    raises; a failed step makes the result not ok with the
                    hook given ok; on the WiCAN the hook given runs alone,
                    its result as it returned it, and no canctl runs
--can-fail runs the cases on the unedited canchg_mode.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail. The edits include skipping the end step, the end
step only after a hook that succeeded, and each check dropped.

Charge-interposer tester, 2026-11-12 (the charge-interposer reviewer's
canchg-mode batch: a fake canctl, a control and a can-fail for a skipped
end step).
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "canchg_mode.py")
SER_MAIN, SER_CHG = "00AA00BB1111222233334444", "00CC00DD5555666677778888"
CHG = ("1-3.3", SER_CHG)
MAIN = ("1-3.2.3", SER_MAIN)
ENV = {"VTRUX_CANCHG_DEVICE": "1d50:606f:%s" % SER_CHG}
END_CALL = "canctl mode canchg socketcan --wait 10"      # BIND_WAIT_S 10

FAKE_CANCTL = r'''
import json, os, sys
ctl, args = sys.argv[1], sys.argv[2:]
with open(ctl, encoding="utf-8") as f:
    c = json.load(f)
with open(c["log"], "a", encoding="utf-8") as f:
    f.write("canctl " + " ".join(args) + "\n")
ok = len(args) == 3 or (len(args) == 5 and args[2] == "socketcan" and args[3] == "--wait"
                        and args[4].isdigit() and 1 <= int(args[4]) <= 99)
if not ok or args[:2] != ["mode", "canchg"] or args[2] not in ("libusb", "socketcan"):
    print("canctl: refused")       # as the real one, --wait only with socketcan, 1..99 s
    sys.exit(2)
if c.get("act", True):
    drv = os.path.join(c["intf"], "driver")
    net = os.path.join(c["net"], "canchg")
    if args[2] == "libusb":
        if os.path.lexists(drv):
            os.unlink(drv)
        if os.path.isdir(net):
            os.rmdir(net)
    else:
        if not os.path.lexists(drv):
            os.symlink(os.path.join(c["drivers"], c.get("driver", "gs_usb")), drv)
        if c.get("netdev", True) and not os.path.isdir(net):
            os.makedirs(net)
print("canchg: mode %s" % args[2])
sys.exit(c.get("rc", 0))
'''


def load(path):
    spec = importlib.util.spec_from_file_location("canchg_mode", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Bench:
    """A made-up sysfs (USB devices, drivers, net) and a fake canctl on it,
    patched into the module M."""

    def __init__(self, M, work, chg_bound, **ctl):
        self.M, self.work = M, work
        sysd = os.path.join(work, "sys")
        self.root = os.path.join(sysd, "bus", "usb", "devices")
        self.drivers = os.path.join(sysd, "bus", "usb", "drivers")
        self.net = os.path.join(sysd, "class", "net")
        for d in (self.root, self.net, os.path.join(self.drivers, "gs_usb"),
                  os.path.join(self.drivers, "usbfs")):
            os.makedirs(d, exist_ok=True)
        for (name, serial), bound in ((MAIN, True), (CHG, chg_bound)):
            self._device(name, serial, bound)
        self.log = os.path.join(work, "calls.txt")
        open(self.log, "w").close()
        self.ctl = os.path.join(work, "canctl.json")
        self.set(**ctl)
        script = os.path.join(work, "fake_canctl.py")
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write(FAKE_CANCTL)
        self.logdir = os.path.join(work, "logdir")
        os.makedirs(self.logdir)
        M.CANCTL = [sys.executable, "-B", script, self.ctl]
        M.SYSFS_USB, M.SYSFS_NET = self.root, self.net
        self.clock = [0.0]
        self.sleeps = []
        M.monotonic = lambda: self.clock[0]
        M.sleep = self._sleep
        self.on_sleep = None

    def _device(self, name, serial, bound):
        d = os.path.join(self.root, name)
        os.makedirs(d)
        for k, v in (("idVendor", "1d50"), ("idProduct", "606f"), ("serial", serial),
                     ("busnum", "1"), ("devnum", "38" if serial == SER_CHG else "34")):
            with open(os.path.join(d, k), "w", encoding="ascii") as f:
                f.write("%s\n" % v)
        for i in ("1.0", "1.1"):
            os.makedirs(os.path.join(self.root, "%s:%s" % (name, i)))
        if bound:
            os.symlink(os.path.join(self.drivers, "gs_usb"),
                       os.path.join(self.root, name + ":1.0", "driver"))
            os.makedirs(os.path.join(self.net, "canchg" if serial == SER_CHG else "canmain"))

    def set(self, **ctl):
        c = dict(log=self.log, intf=os.path.join(self.root, CHG[0] + ":1.0"), net=self.net,
                 drivers=self.drivers)
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

    def driver(self, dev):
        p = os.path.join(self.root, dev[0] + ":1.0", "driver")
        return os.path.basename(os.path.realpath(p)) if os.path.lexists(p) else None

    def netdev(self, name):
        return os.path.isdir(os.path.join(self.net, name))


def _sub(work, name):
    d = os.path.join(work, name)
    os.makedirs(d)
    return d


def case_off_linux(M, work):
    b = Bench(M, work, chg_bound=True)
    for fn in (M.start, M.end):
        out = fn(ENV, b.logdir, "win32")
        assert out["ok"] is True and "off Linux" in out["detail"], out
    assert b.calls() == [], b.calls()
    assert b.driver(CHG) == "gs_usb", b.driver(CHG)


def case_start(M, work):
    b = Bench(M, work, chg_bound=True)
    out = M.start(ENV, b.logdir, "linux")
    assert out["ok"] is True, out
    assert b.calls() == ["canctl mode canchg libusb"], b.calls()
    assert b.driver(CHG) is None and not b.netdev("canchg"), (b.driver(CHG), b.netdev("canchg"))
    assert b.driver(MAIN) == "gs_usb" and b.netdev("canmain")
    assert out["found"]["bus"] == 1 and out["found"]["address"] == 38, out
    logs = os.listdir(b.logdir)
    assert len(logs) == 1 and logs[0].startswith("canchg_start_"), logs


def case_start_canctl(M, work):
    b = Bench(M, _sub(work, "rc2"), chg_bound=True, rc=2, act=False)
    out = M.start(ENV, b.logdir, "linux")
    assert out["ok"] is False and "canctl mode canchg libusb failed (rc 2, refused)" in out["detail"], out
    b = Bench(M, _sub(work, "rc3"), chg_bound=True, rc=3)
    out = M.start(ENV, b.logdir, "linux")
    assert b.driver(CHG) is None, b.driver(CHG)
    assert out["ok"] is False and "canctl mode canchg libusb failed (rc 3, did not take)" in out["detail"], out


def case_start_recheck(M, work):
    b = Bench(M, _sub(work, "noop"), chg_bound=True, act=False)
    out = M.start(ENV, b.logdir, "linux")
    assert b.calls() == ["canctl mode canchg libusb"], b.calls()
    assert out["ok"] is False and "re-check" in out["detail"] and "bound to the kernel" in \
        out["detail"] and "failed (rc" not in out["detail"], out
    b = Bench(M, _sub(work, "unset"), chg_bound=True)
    out = M.start({}, b.logdir, "linux")
    assert out["ok"] is False and "re-check" in out["detail"] and \
        "VTRUX_CANCHG_DEVICE is not set" in out["detail"], out


def case_end(M, work):
    b = Bench(M, work, chg_bound=False)
    assert b.driver(CHG) is None and not b.netdev("canchg")
    out = M.end(ENV, b.logdir, "linux")
    assert out["ok"] is True, out
    assert b.calls() == [END_CALL], b.calls()
    assert b.driver(CHG) == "gs_usb" and b.netdev("canchg"), (b.driver(CHG), b.netdev("canchg"))
    f = out["found"]
    assert f["canctl_rc"] == 0 and f["bound"] is True and f["netdev"] is True, f
    logs = os.listdir(b.logdir)
    assert len(logs) == 1 and logs[0].startswith("canchg_end_"), logs


def case_end_check(M, work):
    for name, ctl in (("noop", dict(act=False)), ("usbfs", dict(driver="usbfs")),
                      ("nonet", dict(netdev=False))):
        b = Bench(M, _sub(work, name), chg_bound=False, **ctl)
        out = M.end(ENV, b.logdir, "linux")
        assert out["ok"] is False and "not bound again" in out["detail"], (name, out)
        assert b.calls() == [END_CALL], (name, b.calls())
    b = Bench(M, _sub(work, "rc3"), chg_bound=False, rc=3, act=False)
    out = M.end(ENV, b.logdir, "linux")
    assert out["ok"] is False and "canctl mode canchg socketcan failed (rc 3, did not take)" in \
        out["detail"], out


def case_end_poll(M, work):
    b = Bench(M, _sub(work, "late"), chg_bound=False, netdev=False)

    def late(n):
        if n == 2:
            os.makedirs(os.path.join(b.net, "canchg"))
    b.on_sleep = late
    out = M.end(ENV, b.logdir, "linux")
    assert out["ok"] is True and len(b.sleeps) == 2, (out, b.sleeps)
    b = Bench(M, _sub(work, "never"), chg_bound=False, netdev=False)
    out = M.end(ENV, b.logdir, "linux")
    assert out["ok"] is False and "netdev canchg absent" in out["detail"], out
    assert b.clock[0] >= M.BIND_WAIT_S and b.clock[0] - M.POLL_S < M.BIND_WAIT_S, \
        (b.clock[0], b.sleeps)


def case_hooks(M, work):
    b = Bench(M, _sub(work, "x2"), chg_bound=True)

    def inner(step, ok=True, boom=False):
        def hook(board, img, ctx, logdir):
            with open(b.log, "a", encoding="utf-8") as f:
                f.write("inner %s %s\n" % (step, board))
            if boom:
                raise RuntimeError("inner %s raised" % step)
            return dict(ok=ok, detail="inner %s %s" % (step, board), found={"k": step})
        return hook

    s, e = M.hooks(inner("start"), inner("end"), ENV, platform="linux")
    out = s("x2", None, None, b.logdir)
    assert out["ok"] is True and b.calls() == ["canctl mode canchg libusb", "inner start x2"], \
        (out, b.calls())
    assert out["found"]["k"] == "start" and out["found"]["canchg"]["canctl_rc"] == 0, out
    out = e("x2", None, None, b.logdir)
    assert out["ok"] is True and b.calls()[2:] == ["inner end x2", END_CALL], \
        (out, b.calls())
    assert b.driver(CHG) == "gs_usb" and b.netdev("canchg")
    # the hook given fails, or raises: the end step still runs
    for name, hk in (("fails", inner("end", ok=False)), ("raises", inner("end", boom=True))):
        b = Bench(M, _sub(work, name), chg_bound=False)
        _s, e = M.hooks(inner("start"), hk, ENV, platform="linux")
        out = e("x2", None, None, b.logdir)
        assert b.calls() == ["inner end x2", END_CALL], (name, b.calls())
        assert out["ok"] is False and b.driver(CHG) == "gs_usb", (name, out)
        if name == "raises":
            assert "inner end raised" in out["detail"], out
    # a failed step with the hook given ok
    b = Bench(M, _sub(work, "stepfails"), chg_bound=True, act=False)
    s, e = M.hooks(inner("start"), inner("end"), ENV, platform="linux")
    out = s("x2", None, None, b.logdir)
    assert out["ok"] is False and "re-check" in out["detail"] and \
        "inner start x2" in out["detail"], out
    b = Bench(M, _sub(work, "endfails"), chg_bound=False, act=False)
    s, e = M.hooks(inner("start"), inner("end"), ENV, platform="linux")
    out = e("x2", None, None, b.logdir)
    assert out["ok"] is False and "not bound again" in out["detail"], out
    # the WiCAN: the hook given alone, as it returned it
    b = Bench(M, _sub(work, "wican"), chg_bound=True)
    s, e = M.hooks(inner("start"), inner("end"), ENV, platform="linux")
    for hk, step in ((s, "start"), (e, "end")):
        out = hk("wican", None, None, b.logdir)
        assert out == dict(ok=True, detail="inner %s wican" % step, found={"k": step}), out
    assert b.calls() == ["inner start wican", "inner end wican"], b.calls()
    assert b.driver(CHG) == "gs_usb"


CASES = [case_off_linux, case_start, case_start_canctl, case_start_recheck, case_end,
         case_end_check, case_end_poll, case_hooks]

MUTANTS = [
    ("the end step skipped (hooks)",
     "        return _both(end(environ, logdir, platform), theirs)",
     "        return theirs"),
    ("the end step only after a hook that succeeded",
     "        return _both(end(environ, logdir, platform), theirs)",
     "        if not theirs.get(\"ok\"):\n            return theirs\n"
     "        return _both(end(environ, logdir, platform), theirs)"),
    ("a raising hook not caught",
     "    except Exception as e:                              # noqa: BLE001\n"
     "        return dict(ok=False, detail=\"raised %r\" % (e,))",
     "    except ZeroDivisionError:\n        raise"),
    ("the end step before the hook given",
     "        theirs = _call(on_end, b, img, ctx, logdir)\n"
     "        return _both(end(environ, logdir, platform), theirs)",
     "        mine = end(environ, logdir, platform)\n"
     "        return _both(mine, _call(on_end, b, img, ctx, logdir))"),
    ("the start step after the hook given",
     "        mine = start(environ, logdir, platform)\n"
     "        return _both(mine, _call(on_start, b, img, ctx, logdir))",
     "        theirs = _call(on_start, b, img, ctx, logdir)\n"
     "        return _both(start(environ, logdir, platform), theirs)"),
    ("the step on every board", "        if b != board:\n            return on_start(",
     "        if False:\n            return on_start("),
    ("a failed step ignored in the result",
     "    return dict(ok=bool(mine.get(\"ok\")) and bool(theirs.get(\"ok\")),",
     "    return dict(ok=bool(theirs.get(\"ok\")),"),
    ("the hook given's failure ignored in the result",
     "    return dict(ok=bool(mine.get(\"ok\")) and bool(theirs.get(\"ok\")),",
     "    return dict(ok=bool(mine.get(\"ok\")),"),
    ("start: canctl's exit code ignored",
     "    if rc != 0:\n        return dict(ok=False, detail=\"canchg: canctl mode canchg libusb",
     "    if False:\n        return dict(ok=False, detail=\"canchg: canctl mode canchg libusb"),
    ("start: the re-check dropped",
     "        t = adapter_fixes.canchg_target(0, \"linux\", environ, SYSFS_USB)",
     "        t = {\"bus\": 1, \"address\": 38}"),
    ("start: the wrong mode", "    rc, last = _canctl(\"libusb\", logdir, \"start\")",
     "    rc, last = _canctl(\"socketcan\", logdir, \"start\")"),
    ("start: canctl run off Linux",
     "        return dict(ok=True, detail=\"canchg: no mode step off Linux\")\n"
     "    rc, last = _canctl(\"libusb\"",
     "        pass\n    rc, last = _canctl(\"libusb\""),
    ("end: canctl run off Linux",
     "        return dict(ok=True, detail=\"canchg: no mode step off Linux\")\n"
     "    rc, last = _canctl(\"socketcan\"",
     "        pass\n    rc, last = _canctl(\"socketcan\""),
    ("end: canctl's exit code ignored",
     "    if rc != 0:\n        return dict(ok=False, detail=\"canchg: canctl mode canchg socketcan",
     "    if False:\n        return dict(ok=False, detail=\"canchg: canctl mode canchg socketcan"),
    ("end: the bound check dropped", "    if not (bound and netdev):", "    if False:"),
    ("end: the netdev not checked", "    if not (bound and netdev):", "    if not bound:"),
    ("end: any driver counts as gs_usb", "    bound = driver == GS_USB_DRIVER",
     "    bound = driver is not None"),
    ("end: no poll", "        if (bound and netdev) or monotonic() >= t_end:",
     "        if True:"),
    ("end: polls without end", "        if (bound and netdev) or monotonic() >= t_end:",
     "        if (bound and netdev) or monotonic() >= t_end + 100:"),
    ("end: the serial not compared", "        if ids == (vid, pid, serial):",
     "        if ids[:2] == (vid, pid):"),
    ("end: the wrong mode",
     "    rc, last = _canctl(\"socketcan\", logdir, \"end\", [\"--wait\", \"%d\" % BIND_WAIT_S])",
     "    rc, last = _canctl(\"libusb\", logdir, \"end\", [\"--wait\", \"%d\" % BIND_WAIT_S])"),
    ("end: --wait not passed",
     "    rc, last = _canctl(\"socketcan\", logdir, \"end\", [\"--wait\", \"%d\" % BIND_WAIT_S])",
     "    rc, last = _canctl(\"socketcan\", logdir, \"end\")"),
    ("start: --wait passed with libusb (canctl refuses it)",
     "    rc, last = _canctl(\"libusb\", logdir, \"start\")",
     "    rc, last = _canctl(\"libusb\", logdir, \"start\", [\"--wait\", \"%d\" % BIND_WAIT_S])"),
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
    with scratch_dir.run("canchg-mode-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\ncanchg_mode_test: %s" % ("all checks OK" if not bad
                                             else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited canchg_mode.py", flush=True)
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
            p = os.path.join(d, "canchg_mode.py")
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
