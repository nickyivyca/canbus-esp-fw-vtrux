#!/usr/bin/env python3
"""Test of canchg_mode.py: the bench run puts canchg in libusb mode at its
start and back on gs_usb at its end, through can-tools' bench-role layer
(bench_layer.to_libusb and to_socketcan), and judges what the layer read
back. No hardware and no real canctl: a stand-in layer (FakeLayer) keeps
canchg's mode, driver and netdev, records each call, and can be made to
fail (a canctl that exits 2 or 3, a read-back that never settles, the bench
guard refusing) or to return a state other than the one asked for. The
layer itself, canctl and its read-back from canctl's role table, is
can-tools' (tests/test_bench_layer.py there).

    python3.13 -B charge-interposer/canchg_mode_test.py [--can-fail]

Each case names what it checks:
  1. off-linux      off Linux, start() and end() are ok, say so, and the
                    layer is not asked
  2. start          canchg on gs_usb: start() asks the layer for
                    to_libusb("canchg") (exactly that) once, and is ok with
                    the bus and address read back; its log is in logdir;
                    with no stand-in, the layer is adapter_roles.layer()'s
  3. start-fails    the layer raising (canctl refused, did not take, the
                    guard): not ok, the detail naming to_libusb and what the
                    layer said
  4. start-state    the layer returning canchg in another mode: not ok
  5. end            canchg in libusb: end() asks for to_socketcan("canchg",
                    wait_s=10) once and is ok with netdev canchg read back
  6. end-fails      the layer raising: not ok, "not back on gs_usb", naming
                    to_socketcan and what the layer said; the layer
                    returning another driver (usbfs), no netdev, or libusb
                    mode: not ok
  7. hooks          hooks(): on the X2, the start step runs before the hook
                    given and the end step after it; the end step runs, and
                    the result is not ok, when the hook given fails or
                    raises; a failed step makes the result not ok with the
                    hook given ok; on the WiCAN the hook given runs alone,
                    its result as it returned it, and the layer is not asked
--can-fail runs the cases on the unedited canchg_mode.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail. The edits include skipping the end step, the end
step only after a hook that succeeded, and each check dropped.

Charge-interposer tester, 2026-11-12 (the charge-interposer reviewer's
canchg-mode batch: a fake canctl, a control and a can-fail for a skipped
end step); 2026-11-19, the reviewer's bench-layer item: the steps are the
layer's to_libusb and to_socketcan, and the stand-in layer replaces the fake
canctl and sysfs.
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "canchg_mode.py")
START_CALL = "to_libusb canchg"
END_CALL = "to_socketcan canchg wait_s=10.0"            # BIND_WAIT_S 10


def load(path):
    spec = importlib.util.spec_from_file_location("canchg_mode", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _NS(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


class LayerError(RuntimeError):
    """The stand-in layer's failure (bench_layer.BenchLayerError's place)."""


class FakeLayer:
    """A stand-in for canbench.bench_layer: canchg's mode, driver and netdev,
    each call recorded in .calls (shared with the hooks' own lines).
    fail={step: text}: that step raises LayerError(text), changing nothing;
    lie={step: {field: value}}: that step returns a state with those fields
    changed (the read-back the module must judge)."""

    def __init__(self, calls, mode="socketcan", fail=None, lie=None):
        self.calls, self.fail, self.lie = calls, dict(fail or {}), dict(lie or {})
        self.set(mode)

    def set(self, mode):
        self.mode = mode
        self.driver = "gs_usb" if mode == "socketcan" else None
        self.netdev = "canchg" if mode == "socketcan" else None

    def _res(self, step, role):
        f = dict(mode=self.mode, driver=self.driver, netdev=self.netdev)
        f.update(self.lie.get(step, {}))
        return _NS(role=_NS(role=role, kind="gs_usb", driver=f["driver"]), mode=f["mode"],
                   netdev=f["netdev"], busnum=1, devnum=38)

    def to_libusb(self, role, wait_s=None):
        self.calls.append("to_libusb %s" % role + ("" if wait_s is None else " wait_s=%s"
                                                     % wait_s))
        if "to_libusb" in self.fail:
            raise LayerError(self.fail["to_libusb"])
        self.set("libusb")
        return self._res("to_libusb", role)

    def to_socketcan(self, role, wait_s=None):
        self.calls.append("to_socketcan %s wait_s=%s" % (role, wait_s))
        if "to_socketcan" in self.fail:
            raise LayerError(self.fail["to_socketcan"])
        self.set("socketcan")
        return self._res("to_socketcan", role)


class Bench:
    """A stand-in layer patched into the module M, and a log folder."""

    def __init__(self, M, work, chg_bound=True, **kw):
        self.log = []
        self.layer = M.LAYER = FakeLayer(self.log, "socketcan" if chg_bound else "libusb",
                                         **kw)
        self.logdir = os.path.join(work, "logdir")
        os.makedirs(self.logdir)

    def calls(self):
        return list(self.log)


def _sub(work, name):
    d = os.path.join(work, name)
    os.makedirs(d)
    return d


def case_off_linux(M, work):
    b = Bench(M, work, chg_bound=True)
    for fn in (M.start, M.end):
        out = fn(b.logdir, "win32")
        assert out["ok"] is True and "off Linux" in out["detail"], out
    assert b.calls() == [], b.calls()
    assert b.layer.mode == "socketcan"


def case_start(M, work):
    b = Bench(M, work, chg_bound=True)
    out = M.start(b.logdir, "linux")
    assert out["ok"] is True, out
    assert b.calls() == [START_CALL], b.calls()
    assert b.layer.mode == "libusb"
    assert out["found"]["bus"] == 1 and out["found"]["address"] == 38, out
    assert out["found"]["mode"] == "libusb", out
    logs = os.listdir(b.logdir)
    assert len(logs) == 1 and logs[0].startswith("canchg_start_"), logs
    with open(os.path.join(b.logdir, logs[0]), encoding="utf-8") as f:
        assert "libusb mode" in f.read()
    # with no stand-in, the layer is adapter_roles.layer()'s (the guard set there)
    M.LAYER = None
    saved = sys.modules.get("adapter_roles")
    sys.modules["adapter_roles"] = type("AR", (), {"layer": staticmethod(lambda: b.layer)})
    try:
        out = M.end(b.logdir, "linux")
    finally:
        if saved is None:
            del sys.modules["adapter_roles"]
        else:
            sys.modules["adapter_roles"] = saved
    assert out["ok"] and b.calls()[-1] == END_CALL, (out, b.calls())


def case_start_fails(M, work):
    for name, text in (("rc2", "canctl mode canchg libusb failed (rc 2, refused): x"),
                       ("rc3", "canctl mode canchg libusb failed (rc 3, did not take): x"),
                       ("guard", "the bench lock is free: the bench is used only under the lock")):
        b = Bench(M, _sub(work, name), chg_bound=True, fail={"to_libusb": text})
        out = M.start(b.logdir, "linux")
        assert out["ok"] is False and "bench_layer.to_libusb canchg failed" in out["detail"] \
            and text in out["detail"], (name, out)
        assert b.calls() == [START_CALL], (name, b.calls())
        assert os.listdir(b.logdir), (name, "no log")


def case_start_state(M, work):
    b = Bench(M, work, chg_bound=True, lie={"to_libusb": {"mode": "socketcan"}})
    out = M.start(b.logdir, "linux")
    assert out["ok"] is False and "not libusb" in out["detail"], out


def case_end(M, work):
    b = Bench(M, work, chg_bound=False)
    out = M.end(b.logdir, "linux")
    assert out["ok"] is True, out
    assert b.calls() == [END_CALL], b.calls()
    assert b.layer.mode == "socketcan"
    f = out["found"]
    assert f["mode"] == "socketcan" and f["driver"] == "gs_usb" and f["netdev"] == "canchg", f
    logs = os.listdir(b.logdir)
    assert len(logs) == 1 and logs[0].startswith("canchg_end_"), logs


def case_end_fails(M, work):
    for name, kw in (("rc3", dict(fail={"to_socketcan": "canctl mode canchg socketcan --wait "
                                                        "10 failed (rc 3, did not take): x"})),
                     ("unsettled", dict(fail={"to_socketcan": "canctl mode canchg socketcan "
                                                              "exited 0 but canchg is not "
                                                              "settled in socketcan after 10 s"})),
                     ("guard", dict(fail={"to_socketcan": "the bench lock is free"}))):
        b = Bench(M, _sub(work, name), chg_bound=False, **kw)
        out = M.end(b.logdir, "linux")
        assert out["ok"] is False and "not back on gs_usb" in out["detail"] and \
            "bench_layer.to_socketcan canchg failed" in out["detail"] and \
            kw["fail"]["to_socketcan"] in out["detail"], (name, out)
        assert b.calls() == [END_CALL], (name, b.calls())
    for name, lie in (("usbfs", {"driver": "usbfs"}), ("nonet", {"netdev": None}),
                      ("libusb", {"mode": "libusb"})):
        b = Bench(M, _sub(work, name), chg_bound=False, lie={"to_socketcan": lie})
        out = M.end(b.logdir, "linux")
        assert out["ok"] is False and "not back on gs_usb" in out["detail"], (name, out)


def case_hooks(M, work):
    b = Bench(M, _sub(work, "x2"), chg_bound=True)

    def inner(bench, step, ok=True, boom=False):
        def hook(board, img, ctx, logdir):
            bench.log.append("inner %s %s" % (step, board))
            if boom:
                raise RuntimeError("inner %s raised" % step)
            return dict(ok=ok, detail="inner %s %s" % (step, board), found={"k": step})
        return hook

    s, e = M.hooks(inner(b, "start"), inner(b, "end"), platform="linux")
    out = s("x2", None, None, b.logdir)
    assert out["ok"] is True and b.calls() == [START_CALL, "inner start x2"], (out, b.calls())
    assert out["found"]["k"] == "start" and out["found"]["canchg"]["mode"] == "libusb", out
    out = e("x2", None, None, b.logdir)
    assert out["ok"] is True and b.calls()[2:] == ["inner end x2", END_CALL], (out, b.calls())
    assert b.layer.mode == "socketcan"
    # the hook given fails, or raises: the end step still runs
    for name, boom, ok in (("fails", False, False), ("raises", True, True)):
        b = Bench(M, _sub(work, name), chg_bound=False)
        _s, e = M.hooks(inner(b, "start"), inner(b, "end", ok=ok, boom=boom), platform="linux")
        out = e("x2", None, None, b.logdir)
        assert b.calls() == ["inner end x2", END_CALL], (name, b.calls())
        assert out["ok"] is False and b.layer.mode == "socketcan", (name, out)
        if name == "raises":
            assert "inner end raised" in out["detail"], out
    # a failed step with the hook given ok
    b = Bench(M, _sub(work, "stepfails"), chg_bound=True, fail={"to_libusb": "refused"})
    s, e = M.hooks(inner(b, "start"), inner(b, "end"), platform="linux")
    out = s("x2", None, None, b.logdir)
    assert out["ok"] is False and "to_libusb canchg failed" in out["detail"] and \
        "inner start x2" in out["detail"], out
    b = Bench(M, _sub(work, "endfails"), chg_bound=False, fail={"to_socketcan": "refused"})
    s, e = M.hooks(inner(b, "start"), inner(b, "end"), platform="linux")
    out = e("x2", None, None, b.logdir)
    assert out["ok"] is False and "not back on gs_usb" in out["detail"], out
    # the WiCAN: the hook given alone, as it returned it
    b = Bench(M, _sub(work, "wican"), chg_bound=True)
    s, e = M.hooks(inner(b, "start"), inner(b, "end"), platform="linux")
    for hk, step in ((s, "start"), (e, "end")):
        out = hk("wican", None, None, b.logdir)
        assert out == dict(ok=True, detail="inner %s wican" % step, found={"k": step}), out
    assert b.calls() == ["inner start wican", "inner end wican"], b.calls()
    assert b.layer.mode == "socketcan"


CASES = [case_off_linux, case_start, case_start_fails, case_start_state, case_end,
         case_end_fails, case_hooks]

MUTANTS = [
    ("the end step skipped (hooks)",
     "        return _both(end(logdir, platform), theirs)",
     "        return theirs"),
    ("the end step only after a hook that succeeded",
     "        return _both(end(logdir, platform), theirs)",
     "        if not theirs.get(\"ok\"):\n            return theirs\n"
     "        return _both(end(logdir, platform), theirs)"),
    ("a raising hook not caught",
     "    except Exception as e:                              # noqa: BLE001\n"
     "        return dict(ok=False, detail=\"raised %r\" % (e,))",
     "    except ZeroDivisionError:\n        raise"),
    ("the end step before the hook given",
     "        theirs = _call(on_end, b, img, ctx, logdir)\n"
     "        return _both(end(logdir, platform), theirs)",
     "        mine = end(logdir, platform)\n"
     "        return _both(mine, _call(on_end, b, img, ctx, logdir))"),
    ("the start step after the hook given",
     "        mine = start(logdir, platform)\n"
     "        return _both(mine, _call(on_start, b, img, ctx, logdir))",
     "        theirs = _call(on_start, b, img, ctx, logdir)\n"
     "        return _both(start(logdir, platform), theirs)"),
    ("the step on every board", "        if b != board:\n            return on_start(",
     "        if False:\n            return on_start("),
    ("a failed step ignored in the result",
     "    return dict(ok=bool(mine.get(\"ok\")) and bool(theirs.get(\"ok\")),",
     "    return dict(ok=bool(theirs.get(\"ok\")),"),
    ("the hook given's failure ignored in the result",
     "    return dict(ok=bool(mine.get(\"ok\")) and bool(theirs.get(\"ok\")),",
     "    return dict(ok=bool(mine.get(\"ok\")),"),
    ("start: the layer's failure taken as ok",
     "        out = dict(ok=False, detail=\"canchg: bench_layer.to_libusb %s failed: %s: %s\"",
     "        out = dict(ok=True, detail=\"canchg: bench_layer.to_libusb %s failed: %s: %s\""),
    ("start: the mode read back not compared", "    if f[\"mode\"] != \"libusb\":",
     "    if False:"),
    ("start: the wrong mode", "        res = _layer().to_libusb(ROLE)",
     "        res = _layer().to_socketcan(ROLE)"),
    ("start: the layer asked off Linux",
     "        return dict(ok=True, detail=\"canchg: no mode step off Linux\")\n    try:\n"
     "        res = _layer().to_libusb(ROLE)",
     "        pass\n    try:\n        res = _layer().to_libusb(ROLE)"),
    ("end: the layer asked off Linux",
     "        return dict(ok=True, detail=\"canchg: no mode step off Linux\")\n    try:\n"
     "        res = _layer().to_socketcan(ROLE, wait_s=BIND_WAIT_S)",
     "        pass\n    try:\n        res = _layer().to_socketcan(ROLE, wait_s=BIND_WAIT_S)"),
    ("end: the layer's failure taken as ok",
     "        out = dict(ok=False, detail=\"canchg: not back on gs_usb: bench_layer.to_socketcan %s \"\n"
     "                   \"failed",
     "        out = dict(ok=True, detail=\"canchg: not back on gs_usb: bench_layer.to_socketcan %s \"\n"
     "                   \"failed"),
    ("end: the read-back not judged",
     "    if f[\"mode\"] != \"socketcan\" or f[\"driver\"] != \"gs_usb\" or not f[\"netdev\"]:",
     "    if False:"),
    ("end: any driver counts as gs_usb",
     "    if f[\"mode\"] != \"socketcan\" or f[\"driver\"] != \"gs_usb\" or not f[\"netdev\"]:",
     "    if f[\"mode\"] != \"socketcan\" or not f[\"netdev\"]:"),
    ("end: no netdev accepted",
     "    if f[\"mode\"] != \"socketcan\" or f[\"driver\"] != \"gs_usb\" or not f[\"netdev\"]:",
     "    if f[\"mode\"] != \"socketcan\" or f[\"driver\"] != \"gs_usb\":"),
    ("end: the wrong mode", "        res = _layer().to_socketcan(ROLE, wait_s=BIND_WAIT_S)",
     "        res = _layer().to_libusb(ROLE, wait_s=BIND_WAIT_S)"),
    ("end: the wait not passed", "        res = _layer().to_socketcan(ROLE, wait_s=BIND_WAIT_S)",
     "        res = _layer().to_socketcan(ROLE)"),
    ("the layer not adapter_roles'", "    import adapter_roles\n    return adapter_roles.layer()",
     "    raise RuntimeError(\"no layer\")"),
    ("no log written", "        with open(os.path.join(logdir, \"canchg_%s_%s.txt\"",
     "        with open(os.devnull + (\"\" if 1 else \"canchg_%s_%s.txt\""),
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
