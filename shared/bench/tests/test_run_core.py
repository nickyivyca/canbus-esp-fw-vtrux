"""The chain runner's own tests (the harness tests the harness). No hardware.

    python -B shared/bench/tests/test_run_core.py            every test
    python -B shared/bench/tests/test_run_core.py --can-fail  every test
        unmutated first (the control, which must pass, or no mutant is
        counted), then again with the rule it guards switched off (a
        mutant), each of which must FAIL -- a test that still passes with
        its rule removed checks nothing
    python -B shared/bench/tests/test_run_core.py NAME...     some tests

Each test builds a small suite of fake checks (python -c one-liners), a
fake bench-holder.md and, where flashing is involved, fake_esptool.py
standing in for esptool. Scratch goes to this run's own folder,
<worktree>/scratch/vtrux-bench-tests/run_core/<time>_<pid>/ (_scratch.py), never
into a synced folder or the system temp folder.
"""

import glob
import json
import re
import os
import shutil
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vtrux_bench.run import engine as E            # noqa: E402
import _scratch                                    # noqa: E402
from vtrux_bench.run import record as R            # noqa: E402
from vtrux_bench.run import procs as P             # noqa: E402
from vtrux_bench.run import model as M             # noqa: E402
from vtrux_bench.run.lock import LockError         # noqa: E402
from vtrux_bench.run.model import (Check, Figure, Group, Image, KnownFail,  # noqa: E402
                                   Result, Suite, SuiteError, Tol, Verdict)

from vtrux_bench.run import canmain as CM         # noqa: E402

PY = sys.executable
SCRATCH = _scratch.run_dir("run_core")  # this run's own folder in the worktree's scratch/ (_scratch.py)

# The canmain check (SPEC section 8) runs for real in every bench run here,
# on made-up inputs: a sysfs tree with one USB root hub and no canmain, and
# a Plug and Play listing with one root hub, so its probe "absent" passes in
# either environment until a test plants canmain.
CM_ROOT = os.path.join(SCRATCH, "_canmain_sys")
CM_SERIAL = "TESTCANMAIN01"
CM_FILE = os.path.join(SCRATCH, "_canmain_serial.md")
os.makedirs(os.path.join(CM_ROOT, "bus", "usb", "devices", "usb1"))
os.makedirs(os.path.join(CM_ROOT, "class", "net"))
with open(os.path.join(CM_ROOT, "bus", "usb", "devices", "usb1", "serial"),
          "w") as _f:
    _f.write("0000:00:14.0\n")
with open(CM_FILE, "w") as _f:
    _f.write("# made up\n\n- canmain serial: `%s`\n" % CM_SERIAL)
CM_IDS = ["USB\\ROOT_HUB30\\4&2D0A1B&0&0"]
CM.SYS_ROOT = CM_ROOT
CM.pnp_ids = lambda: list(CM_IDS)
CM_SETTING = {"probe": "absent", "reason": "made-up bench, no canmain",
              "iface": "canmain", "serial_file": CM_FILE}
ROLE = "tester (test-suite)"
# fake_esptool's chip: a locally administered MAC, no real board's
FAKE_CHIP = {"type": "ESP32-S3", "mac": "02:00:00:00:00:01"}

# The esptool flasher finds its port by the board's USB IDs (DEVICES-SPEC
# S1-S3, flash.device_port): a made-up /dev/serial/by-id/ and sysfs
# (_serial_fixture.py) with one X2-like board, the flasher told it is on
# Linux. Where symbolic links cannot be made (Windows without the right),
# the flasher is told it is on Windows and keeps its port setting, and the
# three device tests fail, saying why.
from vtrux_bench.run import flash as FL           # noqa: E402
from _serial_fixture import FakeSerial            # noqa: E402
FAKE_DEVICE = {"vid": "303a", "pid": "1001", "serial": "TESTX2SERIAL01",
               "interface": 0}
try:
    SD_FS = FakeSerial(os.path.join(SCRATCH, "_serial"))
    SD_LINK = SD_FS.add("ttyACM9", "303a", "1001", serial="TESTX2SERIAL01",
                        interface=0, interfaces=3)
    SD_FS.install()
    FL.this_env = lambda: "linux"
    ESPTOOL_PORT = {"device": FAKE_DEVICE}
    SD_WHY = None
except OSError as _e:
    SD_LINK = None
    FL.this_env = lambda: "windows"
    ESPTOOL_PORT = {"port": "FAKE0"}
    SD_WHY = "no made-up by-id folder (symbolic links: %s)" % _e


# ---- fixtures -----------------------------------------------------------
def _rmtree(path):
    """rmtree that also removes read-only files (git objects on Windows)."""
    import stat

    def force(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if os.path.exists(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=force)
        else:
            shutil.rmtree(path, onerror=force)


class Env:
    def __init__(self, name):
        self.root = os.path.join(SCRATCH, name)
        _rmtree(self.root)
        os.makedirs(self.root)
        self.runs = os.path.join(self.root, "runs")
        self.holder = os.path.join(self.root, "bench-holder.md")
        self.board = os.path.join(self.root, "board.bin")
        self.esplog = os.path.join(self.root, "esptool.log")
        self.set_holder(ROLE + " (test)")
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]
        os.environ["FAKE_ESPTOOL_LOG"] = self.esplog
        os.environ["FAKE_BOARD"] = self.board
        os.environ["FAKE_ESPTOOL_MODE"] = "ok"

    def set_holder(self, h):
        with open(self.holder, "w", encoding="utf-8") as f:
            f.write("# Bench holder\n\n## Lock\n\n**Holder:** %s\n**Loaned to:** "
                    "--\n**Since:** x\n**For:** y\n\n## Board state\n" % h)

    def settings(self, **kw):
        s = dict(runs_root=self.runs, bench_holder=self.holder,
                 hosts={"local": {"python": [PY]}},
                 esptool=dict({"argv": [PY, os.path.join(HERE, "fake_esptool.py")],
                               "baud": 115200}, **json.loads(json.dumps(ESPTOOL_PORT))),
                 pause_poll_s=0.2, scratch=self.root,
                 canmain_check=dict(CM_SETTING))
        s.update(kw)
        return s

    def image(self, name, content):
        p = os.path.join(self.root, "%s.bin" % name)
        with open(p, "w") as f:
            f.write(content)
        boot = os.path.join(self.root, "boot.bin")
        with open(boot, "w") as f:
            f.write("BOOT")
        return Image(name, "esptool", params={"files": [["0x0", boot],
                                                        ["0x10000", p]],
                                              "chip": dict(FAKE_CHIP)},
                     expect=content)

    def identify(self, image, ctx, logdir):
        if not os.path.exists(self.board):
            return None
        with open(self.board) as f:
            return f.read()

    def esptool_calls(self):
        if not os.path.exists(self.esplog):
            return []
        with open(self.esplog) as f:
            return [ln.strip() for ln in f if ln.strip()]


def pc(code):
    """argv for a python one-liner check."""
    return ["-c", code]


def chk(cid, code, **kw):
    return Check(cid, pc(code), python=True, timeout_s=kw.pop("timeout_s", 60),
                 **kw)


def run(env, suite, settings=None, **kw):
    eng = E.Engine(suite, settings or env.settings(), out=lambda *a: None, **kw)
    rec = eng.run()
    return eng, rec


def verdicts(rec):
    return {r["id"]: r["verdict"] for r in rec["results"]}


def suite(groups, **kw):
    return Suite(name=kw.pop("name", "t"), role=kw.pop("role", ROLE),
                 groups=groups, **kw)


# ---- tests --------------------------------------------------------------
def test_validate():
    """A suite that cannot run as written is refused before anything runs."""
    env = Env("validate")
    bad = [
        suite([Group("g", [chk("a", "pass"), chk("a", "pass")])]),
        suite([Group("g", [chk("a", "pass")], image="nope", needs_lock=True)]),
        suite([Group("g", [chk("a", "pass")], image="i")],
              images={"i": env.image("i", "X")}, identify=env.identify),
        suite([Group("g", [chk("a", "pass", outputs=["*.log"])])]),
        suite([Group("g", [chk("a b", "pass")])]),
    ]
    for s in bad:
        try:
            E.Engine(s, env.settings())
        except SuiteError:
            continue
        raise AssertionError("accepted an invalid suite: %r" % s.groups[0])


def test_basic_verdicts():
    """PASS, FAIL and a judge that returns nothing (ERROR, never PASS)."""
    env = Env("basic")
    s = suite([Group("g", [
        chk("ok", "pass"),
        chk("bad", "import sys; sys.exit(3)"),
        Check("silent", pc("pass"), python=True, judge=lambda run: []),
    ])])
    eng, rec = run(env, s)
    v = verdicts(rec)
    assert v == {"g/ok": "PASS", "g/bad": "FAIL", "g/silent": "ERROR"}, v
    assert os.path.exists(os.path.join(eng.run_dir, "record.json"))
    idx = open(os.path.join(eng.run_dir, "index.txt")).read()
    assert "g/silent" in idx and "ERROR" in idx
    assert E.exit_code(rec) == 1


def test_never_overwrite():
    """A run folder and its record are never reused or overwritten."""
    env = Env("overwrite")
    s = suite([Group("g", [chk("ok", "pass")])])
    run(env, s, run_id="fixed")
    first = open(os.path.join(env.runs, "fixed", "record.json")).read()
    try:
        run(env, s, run_id="fixed")
    except FileExistsError:
        pass
    else:
        raise AssertionError("a second run reused the folder")
    assert open(os.path.join(env.runs, "fixed", "record.json")).read() == first


def test_known_fail():
    """Registry FAIL -> KNOWN-FAIL; registry PASS flagged; unproduced entry
    listed."""
    env = Env("knownfail")
    s = suite([Group("g", [
        chk("kf", "import sys; sys.exit(1)"),
        chk("fixed", "pass"),
        Check("multi", pc("pass"), python=True,
              judge=lambda run: [Result(Verdict.PASS, "a")]),
    ])], known_fails=[KnownFail("g/kf", "spec 8.2, labelled"),
                      KnownFail("g/fixed", "spec 2.3"),
                      KnownFail("g/multi/renamed", "spec x")])
    eng, rec = run(env, s)
    v = verdicts(rec)
    assert v["g/kf"] == "KNOWN-FAIL", v
    fixed = [r for r in rec["results"] if r["id"] == "g/fixed"][0]
    assert fixed["verdict"] == "PASS" and fixed.get("known_fail_passed"), fixed
    assert rec["unmatched_known_fails"] == ["g/multi/renamed"], rec[
        "unmatched_known_fails"]
    idx = open(os.path.join(eng.run_dir, "index.txt")).read()
    assert "KNOWN FAILS THAT NOW PASS" in idx and "NOT PRODUCED" in idx
    assert E.exit_code(rec) == 1


def test_lock_guard():
    """No bench group runs unless the suite's role holds the lock: refused
    at the start, and NOT-RUN if the lock changes hands mid-run."""
    env = Env("lock")
    s = suite([Group("b1", [chk("x", "pass")], needs_lock=True)])
    env.set_holder("tester (someone-else)")
    try:
        run(env, s)
    except LockError:
        pass
    else:
        raise AssertionError("started a bench run without the lock")
    assert not os.path.exists(env.runs) or not os.listdir(env.runs)
    env.set_holder(ROLE + " (test)")
    steal = ("p=%r; open(p,'w').write('## Lock\\n\\n**Holder:** tester (thief)\\n"
             "**Since:** x\\n**For:** y\\n')" % env.holder)
    s = suite([Group("b1", [chk("steal", steal)], needs_lock=True),
               Group("b2", [chk("after", "pass")], needs_lock=True),
               Group("logic", [chk("free", "pass")], after=["b2"])])
    eng, rec = run(env, s)
    v = verdicts(rec)
    assert v["b1/steal"] == "PASS" and v["b2/after"] == "NOT-RUN", v
    # a lost lock stops the run: the logic lane's remaining cases too
    # (section 6, "every remaining case"; reviewer 2026-10-22)
    nr = [r for r in rec["results"] if r["id"] == "logic/free"][0]
    assert nr["verdict"] == "NOT-RUN" and nr["detail"].startswith(
        "LOCK LOST"), nr
    # a logic-only run needs no lock at all
    env.set_holder("-- (free)")
    run(env, suite([Group("logic", [chk("free", "pass")])]))


def test_flash_gate():
    """Auto-reset check fails before the flash: nothing is written, the
    group is NOT-RUN, the rest of the bench stops, and no flash at the end
    either (B is marked not safe to leave idle, A is its idle-safe image)."""
    env = Env("flashgate")
    os.environ["FAKE_ESPTOOL_MODE"] = "chipid-fail"
    a, b = env.image("A", "IMG-A"), env.image("B", "IMG-B")
    b.idle_safe = False
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="B"),
               Group("g2", [chk("y", "pass")], needs_lock=True, image="A")],
              images={"A": a, "B": b}, idle_safe_images={"board": "A"},
              identify=env.identify)
    eng, rec = run(env, s)
    calls = env.esptool_calls()
    assert not any("write_flash" in c for c in calls), calls
    v = verdicts(rec)
    assert v == {"g1/x": "NOT-RUN", "g2/y": "NOT-RUN"}, v
    assert len([c for c in calls if "chip_id" in c]) == 1, calls
    # write fails, after-check passes: no retry of that image
    env = Env("flashgate2")
    os.environ["FAKE_ESPTOOL_MODE"] = "write-fail"
    a, b = env.image("A", "IMG-A"), env.image("B", "IMG-B")
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="B"),
               Group("g2", [chk("y", "pass")], needs_lock=True, image="B")],
              images={"A": a, "B": b}, identify=env.identify,
              on_flash_failure="skip-image-groups")
    eng, rec = run(env, s)
    calls = env.esptool_calls()
    assert len([c for c in calls if "write_flash" in c]) == 1, calls
    assert verdicts(rec) == {"g1/x": "NOT-RUN", "g2/y": "NOT-RUN"}


def test_chip_mismatch():
    """A chip_id that reads another chip type or MAC than the image names
    writes nothing, and the flash's record names the mismatch (methodology,
    2026-10-20)."""
    for name, chip, mac in (("chiptype", "ESP32-C3", "02:00:00:00:00:01"),
                            ("chipmac", "ESP32-S3", "aa:bb:cc:dd:ee:ff")):
        env = Env(name)
        os.environ["FAKE_ESPTOOL_CHIP"] = chip
        os.environ["FAKE_ESPTOOL_MAC"] = mac
        a, b = env.image("A", "IMG-A"), env.image("B", "IMG-B")
        for im in (a, b):
            im.params["chip"] = {"type": "ESP32-S3", "mac": "02:00:00:00:00:01"}
        with open(env.board, "w") as f:
            f.write("IMG-A")
        s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="B"),
                   Group("g2", [chk("y", "pass")], needs_lock=True, image="A")],
                  images={"A": a, "B": b}, identify=env.identify)
        eng, rec = run(env, s)
        calls = env.esptool_calls()
        assert not any("write_flash" in c for c in calls), (name, calls)
        assert verdicts(rec) == {"g1/x": "NOT-RUN", "g2/y": "NOT-RUN"}, verdicts(rec)
        ev = [e for e in rec["events"] if e["kind"] == "flash"]
        assert len(ev) == 1 and ev[0]["phase"] == "chip-mismatch" and \
            "chip_id mismatch" in ev[0]["detail"], (name, ev)
    # the same check passes on the right chip (the control)
    env = Env("chipok")
    a = env.image("A", "IMG-A")
    a.params["chip"] = dict(FAKE_CHIP)
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="A")],
              images={"A": a}, identify=env.identify)
    eng, rec = run(env, s)
    assert verdicts(rec) == {"g1/x": "PASS"}, verdicts(rec)
    assert len([c for c in env.esptool_calls() if "write_flash" in c]) == 1


def test_chip_required():
    """An esptool image that names no chip type or MAC is refused before
    anything touches the board (methodology, 2026-10-20: a check an image
    can skip by naming nothing is no check)."""
    for name, chip in (("nochip", None), ("nomac", {"type": "ESP32-S3"}),
                       ("notype", {"mac": FAKE_CHIP["mac"]})):
        env = Env(name)
        a = env.image("A", "IMG-A")
        if chip is None:
            a.params.pop("chip")
        else:
            a.params["chip"] = chip
        s = suite([Group("g1", [chk("x", "pass")], needs_lock=True,
                         image="A")],
                  images={"A": a}, identify=env.identify)
        eng, rec = run(env, s)
        assert not env.esptool_calls(), (name, env.esptool_calls())
        assert verdicts(rec) == {"g1/x": "NOT-RUN"}, verdicts(rec)
        ev = [e for e in rec["events"] if e["kind"] == "flash"]
        assert ev and ev[0]["phase"] == "chip-unnamed" and "names no chip" in \
            ev[0]["detail"], (name, ev)


def test_image_schedule():
    """Flash only when the board is not already on the image."""
    env = Env("schedule")
    a, b = env.image("A", "IMG-A"), env.image("B", "IMG-B")
    with open(env.board, "w") as f:
        f.write("IMG-A")
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="A"),
               Group("g2", [chk("y", "pass")], needs_lock=True, image="A"),
               Group("g3", [chk("z", "pass")], needs_lock=True, image="B")],
              images={"A": a, "B": b}, identify=env.identify)
    eng, rec = run(env, s)
    writes = [c for c in env.esptool_calls() if "write_flash" in c]
    assert len(writes) == 1 and writes[0].endswith("B.bin"), writes   # for g3
    assert open(env.board).read() == "IMG-B"
    assert set(verdicts(rec).values()) == {"PASS"}
    used = rec["images_used"]
    assert set(used) == {"A", "B"} and used["B"]["flashed"] and         "code_hash" in used["A"] and used["A"]["reads"], used


def test_identify_error():
    """An identify hook that cannot look (raises) stops the bench and
    never leads to a flash."""
    env = Env("iderror")
    a = env.image("A", "IMG-A")

    def broken(image, ctx, logdir):
        raise RuntimeError("capture tool crashed")
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="A"),
               Group("g2", [chk("y", "pass")], needs_lock=True, image="A")],
              images={"A": a}, identify=broken)
    eng, rec = run(env, s)
    assert not [c for c in env.esptool_calls() if "write_flash" in c], \
        env.esptool_calls()
    assert verdicts(rec) == {"g1/x": "NOT-RUN", "g2/y": "NOT-RUN"}, verdicts(rec)


def test_identify_after():
    """A group whose board image changed under it is VOID."""
    env = Env("idafter")
    a = env.image("A", "IMG-A")
    with open(env.board, "w") as f:
        f.write("IMG-A")
    tamper = "open(%r,'w').write('SOMETHING-ELSE')" % env.board
    s = suite([Group("g1", [chk("x", "pass"), chk("tamper", tamper)],
                     needs_lock=True, image="A")],
              images={"A": a}, identify=env.identify)
    eng, rec = run(env, s)
    v = verdicts(rec)
    assert v == {"g1/x": "VOID", "g1/tamper": "VOID"}, v
    assert all(r.get("judged") == "PASS" for r in rec["results"])
    return env, s, eng


def test_void_resume():
    """A group made VOID after the fact is amended in progress.jsonl, so a
    resume runs it again instead of carrying the PASSes it was judged."""
    env, s, eng = test_identify_after()
    with open(env.board, "w") as f:
        f.write("IMG-A")
    eng2, rec2 = run(env, s, resume=eng.run_dir)
    assert not any(r.get("carried_from") for r in rec2["results"]), rec2["results"]


class TwoBoards:
    """Fake flasher and identify for two boards, each a file."""
    def __init__(self, env):
        self.env = env
        self.flashes = []

    def path(self, board):
        return os.path.join(self.env.root, "board_%s.txt" % board)

    def set(self, board, value):
        with open(self.path(board), "w") as f:
            f.write(value)

    def flasher(self, image, params, settings, logdir):
        self.flashes.append(image.name)
        ff = getattr(self, "fail_flash", False)
        if ff is True or (ff and image.board in ff) or \
                image.name in getattr(self, "fail_images", ()):
            return dict(ok=False, phase="write", detail="fake failure",
                        recoverable=True)
        self.set(image.board, image.expect)
        return dict(ok=True, phase="done", detail="fake", recoverable=True)

    def identify(self, image, ctx, logdir):
        p = self.path(image.board)
        return open(p).read() if os.path.exists(p) else None


def test_two_boards():
    """Two boards: each flashed only when its image changes, and none
    restored at the end (user, 2026-11-11): each stays on what its last case
    used, and the end release says so."""
    env, p = log_env("twoboards")
    tb = TwoBoards(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    ims = {"x2app": Image("x2app", "fake", expect="X2-APP", board="x2"),
           "jammer": Image("jammer", "fake", expect="JAM", board="x2"),
           "w1": Image("w1", "fake", expect="WC-1", board="wican"),
           "w2": Image("w2", "fake", expect="WC-2", board="wican")}
    s = suite([Group("a", [chk("x", "pass")], needs_lock=True, image="w1"),
               Group("b", [chk("y", "pass")], needs_lock=True,
                     image=["w2", "jammer"]),
               Group("c", [chk("z", "pass")], needs_lock=True, image="w2")],
              images=ims, flashers={"fake": tb.flasher}, identify=tb.identify,
              role=CI)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    # at the start each board is put on its first image (x2: jammer),
    # then flashed only on change, and nothing at the end
    assert tb.flashes == ["jammer", "w2"], tb.flashes
    assert tb.identify(ims["jammer"], None, None) == "JAM"
    assert tb.identify(ims["w2"], None, None) == "WC-2"
    assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)
    bs = open(os.path.join(eng.run_dir, "board_state.txt")).read()
    assert "wican:" in bs and "x2:" in bs, bs
    assert not rec["idle_unsafe_left"], rec["idle_unsafe_left"]
    rl = log_events(p)[-1]
    assert rl["event"] == "release" and rl["boards"]["x2"].startswith(
        "JAM, identified") and rl["boards"]["wican"].startswith(
        "WC-2, identified"), rl


def test_start_identify():
    """Every board the run uses is identified at the start, before the
    first group, and flashed only if it reads another image; a board needed
    only by a later group is still read first (user 2026-10-20)."""
    env = Env("startidentify")
    tb = TwoBoards(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-OLD")
    ims = {"x2app": Image("x2app", "fake", expect="X2-APP", board="x2"),
           "w1": Image("w1", "fake", expect="WC-1", board="wican")}
    s = suite([Group("a", [chk("x", "pass")], needs_lock=True, image="x2app"),
               Group("b", [chk("y", "pass")], needs_lock=True, image="w1")],
              images=ims, flashers={"fake": tb.flasher}, identify=tb.identify)
    eng, rec = run(env, s)
    assert tb.flashes == ["w1"], tb.flashes      # x2 matched; wican did not
    prog = R.load_progress(eng.run_dir)
    first_check = next(n for n, e in enumerate(prog) if e["type"] == "result")
    reads = [(e["board"], e["read"]) for e in prog[:first_check]
             if e.get("kind") == "identify"]
    assert reads[:3] == [("x2", "X2-APP"), ("wican", "WC-OLD"),
                         ("wican", "WC-1")], reads
    assert any(g["name"] == "start-identify" for g in rec["groups"]), rec["groups"]
    assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)


def test_standing_refused():
    """Standing images are retired (user, 2026-11-11: images are loaded at
    the start of a run, never restored at its end): a suite that still names
    Suite.standing_image or standing_images is refused, saying why. The same
    suite without them is accepted (the control)."""
    env = Env("standing_refused")
    a = env.image("A", "IMG-A")
    g = [Group("g", [chk("x", "pass")], needs_lock=True, image="A")]
    for kw in ({"standing_image": "A"}, {"standing_images": {"board": "A"}}):
        try:
            E.Engine(suite(g, images={"A": a}, identify=env.identify, **kw),
                     env.settings())
        except SuiteError as e:
            assert "retired" in str(e) and "idle_safe" in str(e), (kw, e)
            continue
        raise AssertionError("accepted a suite naming %s" % sorted(kw))
    E.Engine(suite(g, images={"A": a}, identify=env.identify), env.settings())


def test_ota_stops_bench():
    """FLASH-SPEC O11: a flash by the "ota" flasher that is not ok stops the
    bench, so a suite with an "ota" image and on_flash_failure
    "skip-image-groups" is refused, saying why. The same suite with
    "stop-bench" is accepted (the control), and "skip-image-groups" stays
    allowed for a suite with no "ota" image."""
    env = Env("ota_stops_bench")
    a = env.image("A", "IMG-A")
    w = Image("W", "ota", params={"bin": "w.bin", "retry": "none"},
              expect="e" * 64, board="wican")
    g = [Group("g", [chk("x", "pass")], needs_lock=True, image="A")]
    try:
        E.Engine(suite(g, images={"A": a, "W": w}, identify=env.identify,
                       on_flash_failure="skip-image-groups"), env.settings())
    except SuiteError as e:
        assert "ota" in str(e) and "stop-bench" in str(e) and "O11" in str(e), e
    else:
        raise AssertionError("accepted an \"ota\" image with skip-image-groups")
    E.Engine(suite(g, images={"A": a, "W": w}, identify=env.identify,
                   on_flash_failure="stop-bench"), env.settings())
    E.Engine(suite(g, images={"A": a}, identify=env.identify,
                   on_flash_failure="skip-image-groups"), env.settings())


def test_idle_safe_named():
    """An image marked not safe to leave idle needs its board's idle-safe
    image in Suite.idle_safe_images: one of the suite's images, of that
    board, itself idle-safe (user, 2026-11-11). Anything else is refused;
    the right declaration is accepted (the control)."""
    env = Env("idle_safe_named")

    def ims():
        return {"x2app": Image("x2app", "fake", expect="X2-APP", board="x2"),
                "jammer": Image("jammer", "fake", expect="JAM", board="x2",
                                idle_safe=False),
                "w1": Image("w1", "fake", expect="WC-1", board="wican")}
    g = [Group("g", [chk("x", "pass")], needs_lock=True, image="jammer")]
    fl = {"fake": lambda *a: dict(ok=True)}
    for name, idle in (("none", {}), ("unknown", {"x2": "nope"}),
                       ("other board", {"x2": "w1"}),
                       ("itself marked", {"x2": "jammer"})):
        try:
            E.Engine(suite(g, images=ims(), flashers=fl, identify=env.identify,
                           idle_safe_images=idle), env.settings())
        except SuiteError as e:
            assert "idle" in str(e), (name, e)
            continue
        raise AssertionError("accepted idle_safe_images %s (%s)" % (idle, name))
    E.Engine(suite(g, images=ims(), flashers=fl, identify=env.identify,
                   idle_safe_images={"x2": "x2app"}), env.settings())


class Hooked(TwoBoards):
    """TwoBoards with board hooks that log every call next to the flashes
    and identifies, and answer from `answers` ({(which, board): out})."""
    def __init__(self, env, answers=None):
        TwoBoards.__init__(self, env)
        self.calls = []
        self.answers = answers or {}

    def flasher(self, image, params, settings, logdir):
        self.calls.append(("flash", image.board, image.name))
        return TwoBoards.flasher(self, image, params, settings, logdir)

    def identify(self, image, ctx, logdir):
        self.calls.append(("identify", image.board, image.name))
        return TwoBoards.identify(self, image, ctx, logdir)

    def hook(self, which):
        def h(board, image, ctx, logdir):
            assert os.path.isdir(logdir), logdir
            self.calls.append((which, board, image.name if image else None))
            out = self.answers.get((which, board), dict(ok=True, detail="fine"))
            if isinstance(out, Exception):
                raise out
            return out
        return h

    def suite(self, groups=None, **kw):
        # jammer: marked not safe to leave idle, x2app its board's idle-safe
        # image (user, 2026-11-11)
        ims = {"x2app": Image("x2app", "fake", expect="X2-APP", board="x2"),
               "jammer": Image("jammer", "fake", expect="JAM", board="x2",
                               idle_safe=False),
               "w1": Image("w1", "fake", expect="WC-1", board="wican"),
               "w2": Image("w2", "fake", expect="WC-2", board="wican")}
        groups = groups or [
            Group("a", [chk("x", "pass")], needs_lock=True, image=["w2", "x2app"]),
            Group("b", [chk("y", "pass")], needs_lock=True, image="w1")]
        return suite(groups, images=ims, flashers={"fake": self.flasher},
                     identify=self.identify, on_start=self.hook("on_start"),
                     on_end=self.hook("on_end"),
                     idle_safe_images={"x2": "x2app"}, **kw)


def test_board_hooks():
    """on_start runs for every board before the start pass touches it,
    with the first image the run needs there; on_end runs for every board
    after the end pass, with the image the run believes it is on; both are
    in the record and board_state.txt. on_end runs after a STOP too, and
    after a failed start hook (user 2026-10-20)."""
    env = Env("boardhooks")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    eng, rec = run(env, tb.suite())
    c = tb.calls
    assert c[:2] == [("on_start", "wican", "w2"), ("on_start", "x2", "x2app")], c
    assert c[2][0] == "identify" and c[-2:] == [("on_end", "wican", "w1"),
                                               ("on_end", "x2", "x2app")], c
    assert ("flash", "wican", "w2") in c and \
        c.index(("flash", "wican", "w2")) > 1, c
    assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)
    bh = rec["board_hooks"]
    assert sorted(bh["on_start"]) == ["wican", "x2"] and \
        sorted(bh["on_end"]) == ["wican", "x2"], bh
    assert bh["on_end"]["wican"]["ok"] is True, bh
    bs = open(os.path.join(eng.run_dir, "board_state.txt")).read()
    assert "on_start: ok, fine" in bs and "on_end: ok, fine" in bs, bs
    names = [g["name"] for g in rec["groups"]]
    assert names[0] == "on-start" and names[-1] == "on-end", names
    kinds = [e["kind"] for e in R.load_progress(eng.run_dir)
             if e["type"] == "event"]
    assert "on-start" in kinds and "on-end" in kinds, kinds
    # a STOP before the first group: no group runs, the end hooks still do
    env = Env("boardhooks_stop")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    os.makedirs(env.runs)
    s = tb.suite()
    eng = E.Engine(s, env.settings(), out=lambda *a: None)
    orig = eng.start_identify

    def stop_then_identify(work):
        with open(os.path.join(eng.run_dir, "STOP"), "w") as f:
            f.write("test")
        return orig(work)
    eng.start_identify = stop_then_identify
    rec = eng.run()
    assert set(verdicts(rec).values()) == {"NOT-RUN"}, verdicts(rec)
    # the start pass put the WiCAN on w2 and group b's start put it on w1
    # (a group loads its images before its first case sees the STOP); no
    # flash at the end (no restore, user 2026-11-11), and the end hook sees
    # w1, after the end pass read it
    assert tb.flashes == ["w2", "w1"], tb.flashes
    assert [x for x in tb.calls if x[0] == "on_end"] == [
        ("on_end", "wican", "w1"), ("on_end", "x2", "x2app")], tb.calls
    last_id = max(n for n, x in enumerate(tb.calls)
                  if x[:2] == ("identify", "wican"))
    assert tb.calls.index(("flash", "wican", "w1")) < last_id < \
        tb.calls.index(("on_end", "wican", "w1")), tb.calls


def test_start_hook_stops():
    """A start hook that fails, or raises, stops the bench before anything
    is identified or flashed; every bench group is NOT-RUN with the hook's
    reason; the end hooks still run (user 2026-10-20)."""
    for name, answer in (("fails", dict(ok=False, detail="armed and stays so")),
                         ("raises", RuntimeError("no page")),
                         ("garbage", "yes")):
        env = Env("starthook_" + name)
        tb = Hooked(env, {("on_start", "wican"): answer})
        tb.set("x2", "X2-APP")
        tb.set("wican", "WC-OLD")
        eng, rec = run(env, tb.suite())
        v = verdicts(rec)
        assert v == {"a/x": "NOT-RUN", "b/y": "NOT-RUN"}, (name, v)
        assert not [x for x in tb.calls if x[0] in ("flash", "identify")], \
            (name, tb.calls)
        assert ("on_start", "x2", "x2app") not in tb.calls, (name, tb.calls)
        why = rec["results"][0]["detail"]
        assert "on_start for wican failed" in why, (name, why)
        assert {"armed and stays so": "fails", "no page": "raises",
                "not a dict": "garbage"}[next(k for k in (
                    "armed and stays so", "no page", "not a dict")
                    if k in why)] == name, (name, why)
        assert rec["board_hooks"]["on_start"]["wican"]["ok"] is False
        assert [x[:2] for x in tb.calls if x[0] == "on_end"] == [
            ("on_end", "wican"), ("on_end", "x2")], (name, tb.calls)


def test_release_line():
    """An end hook that fails, raises or answers garbage, or one not run
    because the lock is no longer ours, opens board_state.txt, index.txt
    and the record with RELEASE NOT CONFIRMED (user 2026-10-20)."""
    for name, answer in (("fails", dict(ok=False, detail="still latched")),
                         ("raises", RuntimeError("no page")),
                         ("garbage", None)):
        env = Env("release_" + name)
        tb = Hooked(env, {("on_end", "wican"): answer})
        tb.set("x2", "X2-APP")
        tb.set("wican", "WC-1")
        eng, rec = run(env, tb.suite())
        assert set(verdicts(rec).values()) == {"PASS"}, (name, verdicts(rec))
        first = open(os.path.join(eng.run_dir, "board_state.txt")).readline()
        assert first.startswith("RELEASE NOT CONFIRMED: wican: "), (name, first)
        assert {"fails": "still latched", "raises": "no page",
                "garbage": "not a dict"}[name] in first, (name, first)
        idx = open(os.path.join(eng.run_dir, "index.txt")).readline()
        assert idx == first, (name, idx, first)
        assert rec["release_not_confirmed"] == [first.rstrip("\n")], rec
        assert not any("x2" in ln for ln in rec["release_not_confirmed"]), rec
    # the lock lost mid-run: the end hooks are not run, and say so
    env = Env("release_lock")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    steal = ("open(%r, 'w').write('# Bench holder\\n\\n**Holder:** someone "
             "else\\n')" % env.holder)
    s = tb.suite([Group("a", [chk("x", steal)], needs_lock=True,
                        image=["w1", "x2app"])])
    eng, rec = run(env, s)
    assert not [x for x in tb.calls if x[0] == "on_end"], tb.calls
    lines = rec["release_not_confirmed"]
    assert len(lines) == 2 and all("lock is not ours" in ln for ln in lines), lines


def test_end_hooks_crash():
    """If the end hooks themselves crash, every board on_end was owed to is
    recorded as a failed on_end and opens the record: the lane's boards and
    every board the run touched (charge-interposer reviewer, 2026-10-20)."""
    env = Env("endcrash")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    os.makedirs(env.runs)
    eng = E.Engine(tb.suite(), env.settings(), out=lambda *a: None)

    def crash(work):
        eng.board_touched.add("third")
        raise RuntimeError("end pass broke")
    eng.end_hooks = crash
    rec = eng.run()
    oe = rec["board_hooks"]["on_end"]
    assert sorted(oe) == ["third", "wican", "x2"], oe
    assert all(v["ok"] is False and "end hooks crashed" in v["detail"]
               for v in oe.values()), oe
    lines = rec["release_not_confirmed"]
    assert any(ln.startswith("RELEASE NOT CONFIRMED: third: ") for ln in lines), lines


def test_end_identify():
    """SPEC section 6 (user, 2026-11-11): after the last case every board
    the run has an image for is identified, and the end release carries
    those reads. Here the last group uses only the WiCAN, and its check
    changes the X2 under it; the release must say what the X2 reads after
    the last case, not what it read when its own group ended."""
    env, p = log_env("end_identify")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    chg = chk("chg", "open(%r, 'w').write('X2-OTHER')" % tb.path("x2"))
    s = tb.suite([Group("a", [chk("x", "pass")], needs_lock=True,
                        image=["w1", "x2app"]),
                  Group("b", [chg], needs_lock=True, image="w1")], role=CI)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)
    assert not tb.flashes, tb.flashes
    rl = log_events(p)[-1]
    assert rl["event"] == "release" and rl["boards"]["x2"].startswith(
        "X2-OTHER, identified off the wire"), rl
    ep = [e for e in rec["events"] if e["kind"] == "end-pass"]
    assert ep and ep[0]["identities"] == {"wican": "WC-1", "x2": "X2-OTHER"}, ep
    # the end hook is told the X2 is on no image of the suite
    assert ("on_end", "x2", None) in tb.calls, tb.calls


def test_idle_safe_end():
    """The Ruled exception (user, 2026-11-11): a run that would release at
    its end with a board on an image marked not safe to leave idle loads
    that board's idle-safe image first, identified before and after, then
    runs the end hooks and releases with the read made after the load.
    Boards on idle-safe images are left as they are."""
    env, p = log_env("idle_safe_end")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    s = tb.suite([Group("a", [chk("x", "pass")], needs_lock=True,
                        image=["w1", "x2app"]),
                  Group("j", [chk("y", "pass")], needs_lock=True,
                        image=["w2", "jammer"])], role=CI)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)
    # w2 and the jammer for group j; at the end x2app only (w2 is idle-safe)
    assert tb.flashes == ["w2", "jammer", "x2app"], tb.flashes
    c = tb.calls
    fl = c.index(("flash", "x2", "x2app"))
    assert c[fl - 1][:2] == ("identify", "x2") and \
        c[fl + 1][:2] == ("identify", "x2"), c[fl - 2:fl + 3]
    assert c.index(("on_end", "x2", "x2app")) > fl, c
    assert ("on_end", "wican", "w2") in c, c
    assert open(tb.path("x2")).read() == "X2-APP"
    assert open(tb.path("wican")).read() == "WC-2"
    rl = log_events(p)[-1]
    assert rl["event"] == "release" and rl["boards"]["x2"].startswith(
        "X2-APP, identified"), rl
    assert not rec["idle_unsafe_left"], rec["idle_unsafe_left"]


def test_idle_unsafe_line():
    """If the idle-safe load fails, the release (its note), board_state.txt,
    index.txt and the record open with "LEFT ON IDLE-UNSAFE IMAGE: <board>
    on <image>, <why>" (user, 2026-11-11). After a lost lock nothing is
    loaded, and the record and index.txt say the board was left on it."""
    env, p = log_env("idle_unsafe_line")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    tb.fail_images = {"x2app"}       # x2 starts on it, so only the end load
    s = tb.suite([Group("j", [chk("y", "pass")], needs_lock=True,
                        image=["w1", "jammer"])], role=CI)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    want = "LEFT ON IDLE-UNSAFE IMAGE: x2 on jammer, loading the idle-safe " \
        "image x2app failed: "
    assert tb.flashes == ["jammer", "x2app"], tb.flashes
    lines = rec["idle_unsafe_left"]
    assert len(lines) == 1 and lines[0].startswith(want), lines
    for name in ("board_state.txt", "index.txt"):
        assert first_line(eng, name) == lines[0], (name, first_line(eng, name))
    rl = log_events(p)[-1]
    assert rl["event"] == "release" and rl["note"].startswith(lines[0]), rl
    assert rl["boards"]["x2"].startswith("JAM, identified"), rl
    # the lock lost during the jammer's group: no load, the line says why
    env, p = log_env("idle_unsafe_lost")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    s = tb.suite([Group("j", [_steal("log", env, p), chk("n", "pass")],
                        needs_lock=True, image=["w1", "jammer"])], role=CI)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    assert tb.flashes == ["jammer"], tb.flashes
    lines = rec["idle_unsafe_left"]
    assert len(lines) == 1 and lines[0].startswith(
        "LEFT ON IDLE-UNSAFE IMAGE: x2 on jammer, not loaded: LOCK LOST"), lines
    idx = open(os.path.join(eng.run_dir, "index.txt")).read().splitlines()
    assert idx[0].startswith("LOCK LOST") and idx[1] == lines[0], idx[:2]


_REAL_PAUSE_READS = E.Engine.pause_reads
_REAL_END_PASS = E.Engine.end_pass


def _pause_loads_idle_safe(self):
    """Mutant: a pause that loads the idle-safe image, as the end does."""
    logdir = os.path.join(self.run_dir, "_bench", "pause")
    os.makedirs(logdir, exist_ok=True)
    for board in list(self.board_image):
        im = self.suite.images.get(self.board_image.get(board) or "")
        if im is not None and not im.idle_safe:
            self.ensure_image(self.suite.idle_safe_images[board], logdir)
    return _REAL_PAUSE_READS(self)


def _end_pass_restores(self, work):
    """Mutant: the old end restore, each board back on its first image."""
    _REAL_END_PASS(self, work)
    logdir = os.path.join(self.run_dir, "_bench", "end")
    for board, name in self._first_images(work).items():
        self.ensure_image(name, logdir)


def test_idle_pause_untouched():
    """A paused run leaves its boards as they are (user, 2026-11-11): at a
    free_paused nothing is loaded, even on an image not safe to leave idle;
    the idle-safe load is made only at the end release."""
    env, p, tb, eng, rec, seen = _pause_scenario("idle_pause",
                                                 image=("w1", "jammer"))
    assert verdicts(rec) == {"a/first": "PASS", "a/second": "PASS"}, \
        verdicts(rec)
    fp = [e for e in log_events(p) if e["event"] == "free_paused"][0]
    assert fp["boards"]["x2"].startswith("JAM, identified"), fp
    assert tb.flashes == ["jammer", "x2app"], tb.flashes   # start, end
    rl = log_events(p)[-1]
    assert rl["event"] == "release" and rl["boards"]["x2"].startswith(
        "X2-APP, identified"), rl


def test_stop_pause():
    """STOP ends the lane (rest NOT-RUN); PAUSE holds it until removed."""
    env = Env("stop")
    mk = "import os, sys; open(os.path.join(sys.argv[1], '%s'), 'w').close()"
    first = Check("first", ["-c", mk % "STOP", "{run_dir}"], python=True)
    s = suite([Group("g", [first, chk("second", "pass")])])
    eng, rec = run(env, s)
    assert verdicts(rec) == {"g/first": "PASS", "g/second": "NOT-RUN"}, verdicts(rec)
    env = Env("pause")
    first = Check("first", ["-c", mk % "PAUSE", "{run_dir}"], python=True)
    s = suite([Group("g", [first, chk("second", "pass")])])
    eng = E.Engine(s, env.settings(), out=lambda *a: None)
    removed = {}

    def remover():
        for _ in range(200):
            time.sleep(0.1)
            if getattr(eng, "run_dir", None):
                p = os.path.join(eng.run_dir, "PAUSE")
                if os.path.exists(p):
                    time.sleep(1.5)
                    removed["t"] = time.time()
                    os.remove(p)
                    return
    t = threading.Thread(target=remover)
    t.start()
    rec = eng.run()
    t.join()
    second = [r for r in rec["results"] if r["id"] == "g/second"][0]
    assert "t" in removed and second["verdict"] == "PASS"
    kinds = [e["kind"] for e in rec["events"]]
    assert "pause" in kinds and "resume" in kinds, kinds


def test_build_alongside():
    """A build-class check keeps every core ({jobs} is the host's CPU count)
    whatever runs alongside it, and its result says whether a wall-clock
    test did: another role's lock, or a wallclock_patterns process
    (AGENTS.md "Parallel work on the shared machine", point 1; the
    charge-interposer reviewer's batch, 2026-11-12: no more one-job
    throttle)."""
    env = Env("alongside")
    cores = str(os.cpu_count() or 1)
    assert cores != "1", "one CPU: a one-job build cannot be told apart"
    probe = ("import os, sys; open(os.path.join(sys.argv[2], 'jobs.txt'), 'w')"
             ".write(sys.argv[1])")

    def judge(run):
        got = open(os.path.join(run.outdir, "jobs.txt")).read()
        return Result(Verdict.PASS if got == cores else Verdict.FAIL,
                      detail="jobs %s of %s cores" % (got, cores))

    s = suite([Group("g", [Check("build", ["-c", probe, "{jobs}", "{outdir}"],
                                 python=True, cls="build", judge=judge)])])

    def one(settings=None):
        eng, rec = run(env, s, settings=settings)
        assert verdicts(rec) == {"g/build": "PASS"}, rec["results"]
        return rec["results"][0]
    env.set_holder("tester (the-other-project)")
    r = one()
    assert r["alongside_wallclock"] is True and "another role" in \
        r["alongside_reason"], r
    env.set_holder(ROLE + " (test)")             # its own lock, nothing else
    r = one()
    assert r["alongside_wallclock"] is False, r
    marker = "vtrux_bench_wallclock_marker_%d" % os.getpid()
    pid = spawn_foreign(marker)
    try:
        r = one(env.settings(wallclock_patterns=[marker]))
    finally:
        kill_pid(pid)
    assert r["alongside_wallclock"] is True and "process" in \
        r["alongside_reason"], r


def test_after():
    """A group with `after` waits for those groups in another lane; with
    after_must_pass it does not run if they did not all pass."""
    env = Env("after")
    marker = os.path.join(env.root, "built.txt")
    build = ("import time, sys; time.sleep(1.5); open(sys.argv[1], 'w').close()")
    need = "import os, sys; sys.exit(0 if os.path.exists(sys.argv[1]) else 1)"
    s = suite([Group("build", [Check("b", ["-c", build, marker], python=True)]),
               Group("bench", [Check("uses", ["-c", need, marker], python=True)],
                     needs_lock=True, after=["build"])])
    eng, rec = run(env, s)
    assert verdicts(rec) == {"build/b": "PASS", "bench/uses": "PASS"}, verdicts(rec)
    env = Env("after2")
    s = suite([Group("build", [chk("b", "import time, sys; time.sleep(1); sys.exit(1)")]),
               Group("bench", [chk("uses", "pass")], needs_lock=True,
                     after=["build"])])
    eng, rec = run(env, s)
    assert verdicts(rec) == {"build/b": "FAIL", "bench/uses": "NOT-RUN"}, verdicts(rec)
    # a cycle is refused before anything runs
    s = suite([Group("a", [chk("x", "pass")], after=["b"]),
               Group("b", [chk("y", "pass")], needs_lock=True, after=["a"])])
    try:
        E.Engine(s, env.settings())
    except SuiteError:
        pass
    else:
        raise AssertionError("accepted a cycle")


def _events(rec, kind):
    return [e for e in rec["events"] if e["kind"] == kind]


def test_wallclock_records():
    """A wall-clock check runs whatever else is timing-sensitive on its
    host, and records it at its start ("wallclock-others"): a matching
    process that is not the runner's own, another role's lock (AGENTS.md
    "Parallel work on the shared machine", point 3, user 2026-11-12: the
    guard records, it does not refuse). The runner's own processes are
    left out."""
    env = Env("wcrecords")
    marker = "vtrux_bench_other_sim_%d" % os.getpid()
    # The runner's own process matches by its exact command line, read
    # from the process table. A broad pattern ("vtrux_bench.run") also
    # matched other sessions' runners on the same host (2026-10-19).
    own = [a for pid, _pp, a in P.process_table() if pid == os.getpid()]
    assert own and own[0], "this process is not in the process table"
    st = env.settings(wallclock_patterns=[marker, re.escape(own[0])])
    s = suite([Group("g", [Check("sim", pc("pass"), python=True,
                                 cls="wallclock")])])
    eng, rec = run(env, s, settings=st)    # only the runner itself matches
    assert verdicts(rec) == {"g/sim": "PASS"}, rec["results"]
    ev = _events(rec, "wallclock-others")
    assert len(ev) == 1 and ev[0]["processes"] == [] and ev[0]["own"] == 0 \
        and ev[0]["wallclock"] is False and ev[0]["check"] == "g/sim", ev
    pid = spawn_foreign(marker)
    try:
        eng, rec = run(env, s, settings=st)
    finally:
        kill_pid(pid)
    assert verdicts(rec) == {"g/sim": "PASS"}, rec["results"]
    ev = _events(rec, "wallclock-others")
    assert len(ev) == 1 and ev[0]["wallclock"] is True and \
        any(marker in a for a in ev[0]["processes"]), ev
    env.set_holder("tester (the-other-project)")
    eng, rec = run(env, s)
    assert verdicts(rec) == {"g/sim": "PASS"}, rec["results"]
    ev = _events(rec, "wallclock-others")
    assert len(ev) == 1 and ev[0]["wallclock"] is True and \
        "the-other-project" in ev[0]["lock"]["who"], ev


def test_own_wallclock_wait():
    """Two of one run's wall-clock checks on the same host, in two lanes,
    do not overlap: the second waits ("wallclock-wait") and both run (one
    simulator bench per set of ports; the charge-interposer reviewer,
    2026-11-12: a wait, not a refusal)."""
    env = Env("wcownwait")
    spans = os.path.join(env.root, "spans.txt")
    code = ("import sys, time; f, n = sys.argv[1], sys.argv[2]; "
            "open(f, 'a').write('%s start\\n' % n); time.sleep(2); "
            "open(f, 'a').write('%s end\\n' % n)")
    s = suite([Group("b", [Check("sim1", pc(code) + [spans, "1"], python=True,
                                 cls="wallclock")], needs_lock=True),
               Group("l", [Check("sim2", pc(code) + [spans, "2"], python=True,
                                 cls="wallclock")])])
    eng, rec = run(env, s)
    assert verdicts(rec) == {"b/sim1": "PASS", "l/sim2": "PASS"}, rec["results"]
    lines = open(spans).read().split("\n")[:-1]
    # appended as they happen: no overlap means each start is followed by
    # its own end
    assert len(lines) == 4 and lines[0].split()[0] == lines[1].split()[0] and \
        lines[2].split()[0] == lines[3].split()[0] and \
        [ln.split()[1] for ln in lines] == ["start", "end"] * 2, lines
    assert _events(rec, "wallclock-wait"), "the second check never waited"


def test_wallclock_hand_roles():
    """Under bench_lock_log, a hand take (bench_lock.HAND_ROLES: android,
    can-tools; SPEC section 8) is recorded as not another role's wall-clock
    test (others_on, which feeds both the wall-clock check's record and a
    build's alongside_wallclock): with no wallclock_patterns process
    nothing counts, and a matching process does. A tester of the other
    project holding the lock counts, and so does a lock that does not
    replay. Whatever is recorded, the wall-clock check runs (the
    charge-interposer reviewer's batch, 2026-11-12)."""
    env, p = log_env("wc_hand")
    marker = "vtrux_bench_hand_sim_%d" % os.getpid()
    s = suite([Group("g", [Check("sim", pc("pass"), python=True,
                                 cls="wallclock")])], role=CI)
    st = env.settings(bench_lock_log=p, wallclock_patterns=[marker])

    def guard():
        return E.Engine(s, st, out=lambda *a: None).others_on("local")

    o = guard()
    assert o["lock"] is None and o["wallclock"] is False, o     # lock free
    for who in BL.HAND_ROLES:
        BL.take(p, who, os.getpid())
        o = guard()
        assert o["lock"]["who"] == who and o["lock"]["wallclock"] is False \
            and "hand take" in o["lock"]["note"] and o["wallclock"] is False, \
            (who, o)
        eng, rec = run(env, s, settings=st)
        assert verdicts(rec) == {"g/sim": "PASS"}, (who, rec["results"])
        ev = _events(rec, "wallclock-others")
        assert ev and ev[0]["lock"]["wallclock"] is False, (who, ev)
        pid = spawn_foreign(marker)
        try:
            o = guard()
        finally:
            kill_pid(pid)
        assert o["wallclock"] is True and o["processes"] and \
            o["why"].startswith("process: "), (who, o)
        BL.release(p, who, BD, note="test: nothing opened, nothing sent")
    BL.take(p, GI, os.getpid())
    o = guard()
    assert o["lock"]["wallclock"] is True and o["wallclock"] is True and \
        "another role" in o["why"], o
    eng, rec = run(env, s, settings=st)
    assert verdicts(rec) == {"g/sim": "PASS"}, rec["results"]
    BL.release(p, GI, BD, note="test")
    BL.take(p, BL.HAND_ROLES[0], os.getpid())
    with open(p, "a", encoding="ascii") as f:
        f.write("not a log line\n")
    o = guard()
    assert "unreadable" in o["lock"] and o["wallclock"] is True and \
        "unreadable" in o["why"], o


def _fake_systemd_run(root):
    """A systemd-run that starts the command where it is, in no slice."""
    p = os.path.join(root, "fake_systemd_run.py")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("import subprocess, sys\na = sys.argv[1:]\n"
                "sys.exit(subprocess.call(a[a.index('--') + 1:]))\n")
    return [PY, p]


def test_placement_slices():
    """With settings.placement on Linux, each check starts in its class's
    slice: the check itself reads /proc/self/cgroup and finds bench.slice
    (wallclock) or build.slice (build); the record names each group's
    slice, read back with its CPUWeight and MemoryHigh. Off Linux nothing
    is placed and the record says so (AGENTS.md "Parallel work on the
    shared machine", point 1; the charge-interposer reviewer, 2026-11-12).
    The Linux half needs the user systemd and its bench.slice and
    build.slice, as on madhouse-debian."""
    env = Env("placement")
    code = ("import sys; open(sys.argv[1] + '/cg.txt', 'w')"
            ".write(open('/proc/self/cgroup').read())")

    def judge_in(want):
        def judge(run):
            got = P.cgroup_slice(open(os.path.join(run.outdir, "cg.txt")).read())
            return Result(Verdict.PASS if got == want else Verdict.FAIL,
                          detail="in %s, want %s" % (got, want))
        return judge
    pl = {"slices": {"wallclock": "bench.slice", "build": "build.slice",
                     "light": "build.slice"}}
    saved = E.PLATFORM
    try:
        E.PLATFORM = "win32"                     # off Linux: nothing placed
        s = suite([Group("g", [chk("x", "pass")])])
        eng, rec = run(env, s, settings=env.settings(placement=pl))
        assert verdicts(rec) == {"g/x": "PASS"}, rec["results"]
        ev = _events(rec, "placement")
        assert len(ev) == 1 and ev[0]["slice"] is None and \
            ev[0]["reason"] == "off Linux", ev
        assert rec["results"][0]["slice"] is None, rec["results"]
    finally:
        E.PLATFORM = saved
    if not sys.platform.startswith("linux"):
        return
    s = suite([Group("w", [Check("sim", pc(code) + ["{outdir}"], python=True,
                                 cls="wallclock", judge=judge_in("bench.slice"))]),
               Group("b", [Check("make", pc(code) + ["{outdir}"], python=True,
                                 cls="build", judge=judge_in("build.slice"))])])
    eng, rec = run(env, s, settings=env.settings(placement=pl))
    assert verdicts(rec) == {"w/sim": "PASS", "b/make": "PASS"}, rec["results"]
    got = {r["check"]: r["slice"] for r in rec["results"]}
    assert got == {"w/sim": "bench.slice", "b/make": "build.slice"}, got
    ev = {e["group"]: e for e in _events(rec, "placement")}
    for grp, want in (("w", "bench.slice"), ("b", "build.slice")):
        e = ev[grp]
        assert e["ok"] is True and e["slice"] == want and \
            e["read_back"] == want and "CPUWeight" in e and \
            "MemoryHigh" in e, e


def test_placement_readback():
    """A placement that did not take (here a systemd-run that starts the
    command in no slice) FAILs the group's setup, <group>/placement, and
    its checks do not run (the charge-interposer reviewer, 2026-11-12: not
    a silent run)."""
    env = Env("placement_rb")
    pl = {"slices": {"light": "vtrux-test-elsewhere.slice"},
          "systemd_run": _fake_systemd_run(env.root),
          "systemctl": [PY, "-c", "print('CPUWeight=7'); "
                                  "print('MemoryHigh=infinity')"]}
    saved = E.PLATFORM
    try:
        E.PLATFORM = "linux"
        s = suite([Group("g", [chk("x", "pass")])])
        eng, rec = run(env, s, settings=env.settings(placement=pl))
    finally:
        E.PLATFORM = saved
    v = verdicts(rec)
    assert v == {"g/placement": "FAIL", "g/x": "NOT-RUN"}, rec["results"]
    nr = [r for r in rec["results"] if r["id"] == "g/x"][0]
    assert "placement did not take" in nr["detail"], nr
    ev = _events(rec, "placement")
    assert len(ev) == 1 and ev[0]["ok"] is False and \
        ev[0]["slice"] == "vtrux-test-elsewhere.slice" and \
        ev[0]["read_back"] != "vtrux-test-elsewhere.slice", ev


def test_requires():
    """A suite that requires another starts only after a clean run of it
    on the same, identifiable code."""
    env = Env("requires")
    code = {"commit": "abc", "dirty": False}
    emu_ok = suite([Group("e", [chk("x", "pass")])], name="emu",
                   code_info=lambda ctx: dict(code))
    emu_bad = suite([Group("e", [chk("x", "import sys; sys.exit(1)")])],
                    name="emu", code_info=lambda ctx: dict(code))
    bench = suite([Group("b", [chk("y", "pass")])], name="bench",
                  requires="emu", code_info=lambda ctx: dict(code))

    def refused():
        try:
            run(env, bench)
        except SuiteError as e:
            return str(e)
        return None
    assert refused() and "no finished run" in refused()
    run(env, emu_bad)
    assert refused() and "FAIL, ERROR" in refused(), refused()
    time.sleep(1.1)
    run(env, emu_ok)
    assert refused() is None, refused()
    code["commit"] = "def"           # the bench is now on other code
    r = refused()
    assert r and "other code" in r, r
    code["dirty"] = True
    r = refused()
    assert r and "not identifiable" in r, r


def test_requires_whole():
    """The run Suite.requires accepts is a run of the whole suite: not a
    --group or --check selection, and with no NOT-RUN check but declared
    skips (gen-inhibit tester, 2026-10-19)."""
    env = Env("reqwhole")
    code = {"commit": "abc", "dirty": False}
    emu = suite([Group("e", [chk("x", "pass")]),
                 Group("f", [Check("sim", pc("pass"), python=True,
                                   cls="wallclock"),
                             Check("later", ["-"], skip="by ruling")])],
                name="emu", code_info=lambda ctx: dict(code))
    bench = suite([Group("b", [chk("y", "pass")])], name="bench",
                  requires="emu", code_info=lambda ctx: dict(code))

    def refused():
        try:
            run(env, bench)
        except SuiteError as e:
            return str(e)
        return None
    run(env, emu, groups=["e"])               # clean, but one group only
    r = refused()
    assert r and "partial" in r, r
    time.sleep(1.1)
    run(env, emu, checks=["e/*", "f/later"])  # clean, by --check
    r = refused()
    assert r and "partial" in r, r
    time.sleep(1.1)
    env.set_holder("tester (the-other-project)")   # the sim is refused
    run(env, emu)
    env.set_holder("-- (free)")
    r = refused()
    assert r and "NOT-RUN" in r and "f/sim" in r, r
    time.sleep(1.1)
    run(env, emu)                             # whole, one declared skip
    assert refused() is None, refused()


def test_subset_mark():
    """A run with a --group or --check selection is marked a subset run in
    record.json, index.txt and the start event, with the ids of the cases
    it selected; a whole run is marked whole (user 2026-10-20)."""
    env = Env("subsetmark")
    s = suite([Group("e", [chk("x", "pass"), chk("y", "pass")]),
               Group("f", [chk("z", "pass")])])

    def marks(rec):
        d = os.path.join(env.runs, rec["run_id"])
        with open(os.path.join(d, "record.json"), encoding="utf-8") as f:
            disk = json.load(f)
        with open(os.path.join(d, "index.txt"), encoding="utf-8") as f:
            index = f.read()
        start = next(e for e in R.load_progress(d) if e.get("kind") == "start")
        assert (start["subset"], start["subset_cases"]) == (
            disk["subset"], disk["subset_cases"]), (start, disk)
        return disk["subset"], disk["subset_cases"], index
    sub, cases, index = marks(run(env, s)[1])
    assert sub is False and cases is None, (sub, cases)
    assert "whole suite" in index and "SUBSET" not in index, index
    sub, cases, index = marks(run(env, s, groups=["e"])[1])
    assert sub is True and cases == ["e/x", "e/y"], (sub, cases)
    assert "SUBSET RUN" in index and "2 case(s)" in index and "e/y" in index, index
    sub, cases, index = marks(run(env, s, checks=["e/y", "f/*"])[1])
    assert sub is True and cases == ["e/y", "f/z"], (sub, cases)
    head = index.split("\n\n")[0]
    assert "SUBSET RUN" in head and "f/z" in head and "e/x" not in head, index


def test_subset_not_whole():
    """requires_whole refuses a run marked subset, read from the record
    itself, and still refuses an unmarked record whose selection is not
    empty; the whole run is accepted (user 2026-10-20)."""
    env = Env("subsetwhole")
    code = {"commit": "abc", "dirty": False}
    emu = suite([Group("e", [chk("x", "pass")]),
                 Group("f", [chk("z", "pass")])],
                name="emu", code_info=lambda ctx: dict(code))
    bench = suite([Group("b", [chk("y", "pass")])], name="bench",
                  requires="emu", code_info=lambda ctx: dict(code))

    def refused():
        try:
            run(env, bench)
        except SuiteError as e:
            return str(e)
        return None

    def edit(rec, **fields):
        p = os.path.join(env.runs, rec["run_id"], "record.json")
        with open(p, encoding="utf-8") as f:
            r = json.load(f)
        r.update(fields)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(r, f)
    rec = run(env, emu, groups=["e"])[1]
    r = refused()
    assert r and "subset run" in r and "1 case(s)" in r, r
    edit(rec, selection={"groups": [], "checks": []})
    r = refused()
    assert r and "subset True" in r, r       # the marker alone refuses it
    time.sleep(1.1)
    rec = run(env, emu, checks=["f/z"])[1]
    edit(rec, subset=False, subset_cases=None)
    r = refused()
    assert r and "subset run" in r and "'f/z'" in r, r   # the selection alone
    time.sleep(1.1)
    run(env, emu)
    assert refused() is None, refused()


class GitTree:
    """A throwaway git repo: product file p/a.c, harness file h/x.py."""
    def __init__(self, env):
        self.root = os.path.join(env.root, "repo")
        os.makedirs(os.path.join(self.root, "p"))
        os.makedirs(os.path.join(self.root, "h"))
        self.git("init", "-q")
        self.edit("p/a.c", "int a;\n")
        self.edit("h/x.py", "x = 1\n")
        self.commit()

    def git(self, *args):
        import subprocess
        return subprocess.run(["git", "-C", self.root, "-c", "user.name=t",
                               "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
                              + list(args), capture_output=True, text=True,
                              check=True).stdout.strip()

    def edit(self, rel, text):
        with open(os.path.join(self.root, rel), "w", newline="\n") as f:
            f.write(text)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def code_info(self, ctx):
        return {"repo": self.root, "commit": self.git("rev-parse", "HEAD"),
                "dirty": bool(self.git("status", "--porcelain"))}


def _digest_suites(gt):
    files = lambda root: ["p/a.c"]                     # noqa: E731
    emu = suite([Group("e", [chk("x", "pass")])], name="emu",
                code_info=gt.code_info, product_files=files)
    bench = suite([Group("b", [chk("y", "pass")])], name="bench",
                  requires="emu", code_info=gt.code_info, product_files=files)
    return emu, bench


def _refused(env, s):
    try:
        run(env, s)
    except SuiteError as e:
        return str(e)
    return None


def test_product_digest():
    """The bench requires the emulated run on the same PRODUCT digest: a
    harness-only commit keeps it, a product edit changes it (user
    2026-10-20); the harness identity is recorded, not gated."""
    env = Env("productdigest")
    gt = GitTree(env)
    emu, bench = _digest_suites(gt)
    eng, rec = run(env, emu)
    gt.edit("h/x.py", "x = 2\n")
    gt.commit()                                  # harness-only commit
    r = _refused(env, bench)
    assert r is None, r
    assert rec["code"]["product_digest"] and rec["code"]["product_files"] == 1
    brec = json.load(open(sorted(glob.glob(os.path.join(
        env.runs, "bench_*", "record.json")))[-1]))
    assert brec["code"]["commit"] != rec["code"]["commit"]
    assert brec["harness"]["gated"] is False and brec["harness"]["commit"]
    idx = open(os.path.join(env.runs, brec["run_id"], "index.txt")).read()
    assert "product digest (the code identity)" in idx and \
        "harness (recorded, not gated)" in idx, idx
    gt.edit("p/a.c", "int a, b;\n")
    gt.commit()                                  # product commit
    r = _refused(env, bench)
    assert r and "other code" in r, r


def test_digest_bridge():
    """A record with no product digest (older runs) is given its commit's
    digest from git, if its tree was clean; then it counts."""
    env = Env("digestbridge")
    gt = GitTree(env)
    emu, bench = _digest_suites(gt)
    eng, rec = run(env, emu)
    p = os.path.join(env.runs, rec["run_id"], "record.json")
    old = json.load(open(p))
    for k in ("product_digest", "product_files"):
        old["code"].pop(k)
    json.dump(old, open(p, "w"))
    gt.edit("h/x.py", "x = 3\n")
    gt.commit()
    r = _refused(env, bench)
    assert r is None, r
    brec = json.load(open(sorted(glob.glob(os.path.join(
        env.runs, "bench_*", "record.json")))[-1]))
    assert "bridged from commit %s" % rec["code"]["commit"][:12] in \
        brec["requires_met"], brec["requires_met"]
    old["code"]["dirty"] = True                  # a dirty record: no bridge
    json.dump(old, open(p, "w"))
    r = _refused(env, bench)
    assert r and "not clean" in r, r


def _digest_spy(env, spy):
    """Point the engine's digest folder at env's and its product_digest at
    spy; returns the undo."""
    saved = (E.DIGEST_ROOT, E.product_digest)
    E.DIGEST_ROOT = os.path.join(env.root, "digest")

    def undo():
        E.DIGEST_ROOT, E.product_digest = saved
    E.product_digest = spy
    return undo


def test_digest_own_folder():
    """Two digests of one commit at once each extract into a folder of their
    own (before 2026-10-24 every call wiped and refilled one folder per
    commit, so two runners at once could digest each other's half-written
    tree)."""
    env = Env("digestown")
    gt = GitTree(env)
    commit = gt.git("rev-parse", "HEAD")
    emu, _ = _digest_suites(gt)
    engs = [E.Engine(emu, env.settings(), out=lambda *a: None) for _ in range(2)]
    real = E.product_digest
    seen, out, errs = [], [None, None], []
    both_in = threading.Barrier(2, timeout=30)

    def spy(work, files):
        seen.append(work)
        both_in.wait()                           # both calls inside at once
        return real(work, files)

    def call(i):
        try:
            out[i] = engs[i]._digest_at(gt.root, commit)
        except Exception as e:                   # reported below, not lost
            errs.append(repr(e))
    undo = _digest_spy(env, spy)
    try:
        ts = [threading.Thread(target=call, args=(i,)) for i in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
    finally:
        undo()
    assert not errs, errs
    assert len(seen) == 2 and seen[0] != seen[1], seen
    assert out[0] and out[0] == out[1], out


def test_digest_folder_removed():
    """A digest's folder is gone after the call, when it succeeds and when
    the digest fails."""
    env = Env("digestgone")
    gt = GitTree(env)
    commit = gt.git("rev-parse", "HEAD")
    emu, _ = _digest_suites(gt)
    eng = E.Engine(emu, env.settings(), out=lambda *a: None)
    real = E.product_digest
    seen = []

    def spy(work, files):
        seen.append(os.path.isdir(work))
        return real(work, files)

    def boom(work, files):
        seen.append(os.path.isdir(work))
        raise RuntimeError("digest failed")
    undo = _digest_spy(env, spy)
    root = E.DIGEST_ROOT
    try:
        d = eng._digest_at(gt.root, commit)
        assert d and seen == [True], (d, seen)
        assert os.listdir(root) == [], os.listdir(root)
        eng._bridged.clear()
        E.product_digest = boom
        try:
            eng._digest_at(gt.root, commit)
            raise AssertionError("the failing digest was not raised")
        except RuntimeError:
            pass
        assert seen == [True, True], seen
        assert os.listdir(root) == [], os.listdir(root)
    finally:
        undo()


def _under(p, root):
    p, root = (os.path.normcase(os.path.abspath(x)) for x in (p, root))
    return p.startswith(root + os.sep)


def _unlock(root):
    """Everything under root made writable again, so a mutant's leftovers
    can be removed."""
    import stat
    for dp, dns, fns in os.walk(root):
        for n in dns:
            os.chmod(os.path.join(dp, n), stat.S_IRWXU)
        for n in fns:
            os.chmod(os.path.join(dp, n), stat.S_IREAD | stat.S_IWRITE)


def test_digest_in_scratch():
    """With no DIGEST_ROOT, a digest extracts into <the repo's worktree>/
    scratch/vtrux-bench/digest/, found from any folder of the repo, never
    into the system temp folder (before 2026-11-01 it was
    <temp>/vtrux-bench/digest); its folder is gone after."""
    import tempfile
    env = Env("digestscratch")
    gt = GitTree(env)
    commit = gt.git("rev-parse", "HEAD")
    emu, _ = _digest_suites(gt)
    eng = E.Engine(emu, env.settings(), out=lambda *a: None)
    real, seen = E.product_digest, []

    def spy(work, files):
        seen.append(work)
        return real(work, files)
    saved = (E.DIGEST_ROOT, E.product_digest)
    E.DIGEST_ROOT, E.product_digest = None, spy
    try:
        d = eng._digest_at(gt.root, commit)
    finally:
        E.DIGEST_ROOT, E.product_digest = saved
    root = os.path.join(gt.root, "scratch", "vtrux-bench", "digest")
    assert d and len(seen) == 1, (d, seen)
    assert _under(seen[0], root), (seen, root)
    assert not _under(seen[0], tempfile.gettempdir()), seen
    assert os.listdir(root) == [], os.listdir(root)
    sub = E.digest_root(os.path.join(gt.root, "p"))
    assert os.path.normcase(sub) == os.path.normcase(root), (sub, root)


def test_digest_hard_files_removed():
    """A digest's folder is removed though the tree in it holds a read-only
    file in a read-only folder and, on Windows, a path past 260 characters
    (AGENTS.md "Removing scratch": rmtree alone leaves the read-only file,
    and with ignore_errors says nothing)."""
    import stat
    env = Env("digesthard")
    gt = GitTree(env)
    commit = gt.git("rev-parse", "HEAD")
    emu, _ = _digest_suites(gt)
    eng = E.Engine(emu, env.settings(), out=lambda *a: None)
    real = E.product_digest
    made = []

    def spy(work, files):
        hard = os.path.join(work, "ro")
        os.makedirs(hard)
        open(os.path.join(hard, "f"), "w").close()
        os.chmod(os.path.join(hard, "f"), stat.S_IREAD)
        os.chmod(hard, stat.S_IREAD | stat.S_IEXEC)
        made.append(hard)
        if os.name == "nt":
            deep = E.long_path(os.path.join(work, *["d" * 60] * 5))
            os.makedirs(deep)
            open(os.path.join(deep, "f"), "w").close()
            made.append(deep)
        return real(work, files)
    undo = _digest_spy(env, spy)
    root = E.DIGEST_ROOT
    try:
        d = eng._digest_at(gt.root, commit)
        left = os.listdir(root)
    finally:
        undo()
        _unlock(root)
    assert d and made, (d, made)
    assert left == [], left


def test_digest_unremovable_printed():
    """A digest folder that cannot be removed is printed with the reason,
    not passed over in silence, and the digest is still given."""
    import contextlib
    import io
    env = Env("digestprint")
    gt = GitTree(env)
    commit = gt.git("rev-parse", "HEAD")
    emu, _ = _digest_suites(gt)
    eng = E.Engine(emu, env.settings(), out=lambda *a: None)
    undo = _digest_spy(env, E.product_digest)
    root = E.DIGEST_ROOT
    rmtree, err = shutil.rmtree, io.StringIO()
    shutil.rmtree = lambda *a, **k: None         # nothing can be removed
    try:
        with contextlib.redirect_stderr(err):
            d = eng._digest_at(gt.root, commit)
    finally:
        shutil.rmtree = rmtree
        undo()
    left = os.listdir(root)
    assert d and len(left) == 1, (d, left)
    assert "could not remove" in err.getvalue() and left[0] in err.getvalue(), \
        err.getvalue()


def _fixed_mkdtemp(prefix="", dir=None, **_k):
    """mkdtemp's mutant: one folder per prefix, shared by every call."""
    d = os.path.join(dir, prefix + "fixed")
    os.makedirs(d, exist_ok=True)
    return d


def test_resume_code():
    """--resume refuses a run of another suite or on other code, and a run
    whose start records neither."""
    env = Env("resumecode")
    code = {"commit": "abc", "dirty": False}
    s = suite([Group("g", [chk("a", "pass")])], name="emu",
              code_info=lambda ctx: dict(code))
    eng1, _ = run(env, s)

    def refused(st, **kw):
        try:
            run(env, st, resume=eng1.run_dir, **kw)
        except SuiteError as e:
            return str(e)
        return None
    assert refused(s) is None, refused(s)
    code["commit"] = "def"
    r = refused(s)
    assert r and "other code" in r, r
    code["commit"] = "abc"
    other = suite([Group("g", [chk("a", "pass")])], name="bench",
                  code_info=lambda ctx: dict(code))
    r = refused(other)
    assert r and "is a run of emu" in r, r
    p = os.path.join(eng1.run_dir, "progress.jsonl")
    lines = open(p).readlines()
    lines = [ln.replace('"suite": "emu", ', "") if '"start"' in ln else ln
             for ln in lines]
    open(p, "w").writelines(lines)
    r = refused(s)
    assert r and "records no suite" in r, r


def test_redact_scope():
    """The redaction hook sees exactly the declared outputs."""
    env = Env("redact")
    seen = []
    write = ("import os, sys; d=sys.argv[1]; open(os.path.join(d,'a.txt'),'w')"
             ".write('SSID'); open(os.path.join(d,'b.txt'),'w').write('SSID')")
    w = Check("w", ["-c", write, "{outdir}"], python=True, outputs=["a.txt"])
    s = suite([Group("g", [w])],
              redact=lambda paths: seen.extend(os.path.basename(p) for p in paths))
    run(env, s)
    assert seen == ["a.txt"], seen


def test_gate():
    """A gate that does not PASS makes the rest of its group NOT-RUN, and
    only its group."""
    env = Env("gate")
    s = suite([Group("g", [chk("premise", "import sys; sys.exit(1)", gate=True),
                           chk("judged", "pass")]),
               Group("h", [chk("other", "pass")])])
    eng, rec = run(env, s)
    v = verdicts(rec)
    assert v == {"g/premise": "FAIL", "g/judged": "NOT-RUN",
                 "h/other": "PASS"}, v


def test_skip():
    """A check with a skip reason is listed NOT-RUN with it and never runs."""
    env = Env("skip")
    marker = os.path.join(env.root, "ran.txt")
    s = suite([Group("g", [chk("hands", "open(%r,'w').close()" % marker,
                               skip="needs hands: power cycle"),
                           chk("ok", "pass")])])
    eng, rec = run(env, s)
    hands = [r for r in rec["results"] if r["id"] == "g/hands"][0]
    assert hands["verdict"] == "NOT-RUN" and "needs hands" in hands["detail"], hands
    assert not os.path.exists(marker), "a skipped check ran"


def test_missing_setting():
    """A check whose argv or env names a setting the run does not have is an
    ERROR naming it, with a check.log; the other checks of its group and of
    its lane still run and keep their own verdicts (ACCEPT-SPEC A12: the
    acceptance runner's {build_<id>} after a failed build, 2026-11-04)."""
    env = Env("missing_setting")
    s = suite([Group("g", [chk("before", "pass"),
                           Check("argv", pc("import sys; sys.exit(0)") + ["{no_such_build}"],
                                 python=True, timeout_s=60),
                           chk("after", "import sys; sys.exit(1)"),
                           Check("env", pc("pass"), python=True, timeout_s=60,
                                 env={"X": "{no_such_env}"})]),
               Group("h", [chk("other", "pass")])])
    eng, rec = run(env, s)
    v = verdicts(rec)
    assert v == {"g/before": "PASS", "g/argv": "ERROR", "g/after": "FAIL",
                 "g/env": "ERROR", "h/other": "PASS"}, v
    by = {r["id"]: r for r in rec["results"]}
    assert "{no_such_build}" in by["g/argv"]["detail"], by["g/argv"]
    assert "{no_such_env}" in by["g/env"]["detail"], by["g/env"]
    log = os.path.join(eng.run_dir, by["g/argv"]["log"])
    with open(log, encoding="utf-8") as f:
        assert "{no_such_build}" in f.read()
    assert "crashed" not in json.dumps(rec["results"]), rec["results"]


def test_base_env():
    """A local check with base_env starts from that environment, not the
    runner's own: a variable only in the runner's environment does not reach
    it, base_env's and env's do. A check without base_env inherits the
    runner's environment as before (ACCEPT-SPEC A7: an acceptance check's
    environment is never the operator's shell)."""
    env = Env("base_env")
    os.environ["VTRUX_OPERATOR_ONLY"] = "1"
    try:
        base = {k: os.environ[k] for k in ("SYSTEMROOT", "PATH", "TEMP", "TMP")
                if k in os.environ}
        base["FROM_BASE"] = "b"
        clean = ("import os, sys; sys.exit(0 if 'VTRUX_OPERATOR_ONLY' not in os.environ "
                 "and os.environ.get('FROM_BASE') == 'b' and "
                 "os.environ.get('FROM_ENV') == 'e' else 3)")
        inherits = ("import os, sys; sys.exit(0 if os.environ.get('VTRUX_OPERATOR_ONLY') "
                    "== '1' else 3)")
        s = suite([Group("g", [chk("clean", clean, base_env=base, env={"FROM_ENV": "e"}),
                               chk("inherits", inherits)])])
        eng, rec = run(env, s)
    finally:
        del os.environ["VTRUX_OPERATOR_ONLY"]
    v = verdicts(rec)
    assert v == {"g/clean": "PASS", "g/inherits": "PASS"}, v


def test_record_settings():
    """With record_settings (the command line's --record-settings FILE) the
    run's record holds that -- a masked copy -- in place of the settings it
    ran with, so no value of the real settings reaches record.json; without
    it the settings are recorded as before (ACCEPT-SPEC A6: every record an
    acceptance run writes holds the masked settings; reviewer, 2026-11-01)."""
    from vtrux_bench.run import cli
    env = Env("record_settings")
    secret = "02:00:00:00:00:7e"
    st = env.settings(bench_mac=secret)
    masked = dict(st, bench_mac="<MAC>")
    s = suite([Group("g", [chk("ok", "pass")])])
    eng, rec = run(env, s, settings=st, record_settings=masked)
    with open(os.path.join(eng.run_dir, "record.json"), encoding="utf-8") as f:
        text = f.read()
    assert secret not in text and rec["settings"]["bench_mac"] == "<MAC>", rec["settings"]
    eng2, rec2 = run(env, s, settings=st)
    assert rec2["settings"]["bench_mac"] == secret, "the default changed"
    # the command line
    sp, mp = os.path.join(env.root, "s.json"), os.path.join(env.root, "m.json")
    for p, d in ((sp, st), (mp, masked)):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
    suite_py = os.path.join(env.root, "suite_rs.py")
    with open(suite_py, "w", encoding="utf-8") as f:
        f.write("import sys\nfrom vtrux_bench.run.model import Check, Group, Suite\n"
                "S = Suite(name='rs', role=%r, groups=[Group('g', [Check('ok', "
                "['-c', 'pass'], python=True)])])\n" % ROLE)
    before = set(os.listdir(env.runs))
    rc = cli.main(["run", "--suite", suite_py + ":S", "--settings", sp,
                   "--record-settings", mp])
    assert rc == 0, rc
    new = [d for d in os.listdir(env.runs) if d not in before]
    assert len(new) == 1, new
    with open(os.path.join(env.runs, new[0], "record.json"), encoding="utf-8") as f:
        text = f.read()
    assert secret not in text and "<MAC>" in text, "the command line recorded the real settings"


def test_timeout():
    """Past its timeout a check is killed, child processes included: ERROR."""
    env = Env("timeout")
    pidf = os.path.join(env.root, "child.pid")
    code = ("import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)']); "
            "open(%r, 'w').write(str(c.pid)); time.sleep(8)" % pidf)
    s = suite([Group("g", [chk("slow", code, timeout_s=2)])])
    t0 = time.time()
    eng, rec = run(env, s)
    assert time.time() - t0 < 7, "the check was not cut short"
    v = verdicts(rec)
    assert v.get("g/slow/timeout") == "ERROR", v
    pid = int(open(pidf).read())
    time.sleep(0.5)
    assert not _alive(pid), "child %d survived the timeout" % pid


def spawn_foreign(marker, seconds=60):
    """A process that looks like another session's: started through an
    intermediate that exits at once, so it is not this runner's descendant.
    Returns its pid."""
    import subprocess
    pidf = os.path.join(SCRATCH, "foreign_%s.pid" % marker)
    code = ("import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(%d)', %r]); open(%r, 'w').write(str(p.pid))"
            % (seconds, marker, pidf))
    subprocess.run([PY, "-c", code], check=True)
    return int(open(pidf).read())


def kill_pid(pid):
    import subprocess
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _alive(pid):
    if sys.platform == "win32":
        import subprocess
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                           capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


def test_resume():
    """--resume carries terminal results and re-runs only the rest."""
    env = Env("resume")
    marker = os.path.join(env.root, "count.txt")
    bump = "open(%r,'a').write('x')" % marker
    s = suite([Group("g", [chk("done", bump), chk("again", "import sys; sys.exit(0)")])])
    eng1, rec1 = run(env, s)
    # make 'again' look unfinished in the first run's progress
    p = os.path.join(eng1.run_dir, "progress.jsonl")
    lines = [ln for ln in open(p) if '"id": "g/again"' not in ln]
    open(p, "w").writelines(lines)
    eng2, rec2 = run(env, s, resume=eng1.run_dir)
    assert open(marker).read() == "x", "a carried check ran again"
    done = [r for r in rec2["results"] if r["id"] == "g/done"][0]
    assert done.get("carried_from") == eng1.run_id
    assert verdicts(rec2)["g/again"] == "PASS"


def _killed_copy(env, eng, keep):
    """A copy of a finished run's folder whose progress.jsonl stops where a
    kill would have stopped it: the first `keep` lines, the rest never
    written. Returns the copy's path."""
    dst = os.path.join(env.root, "killed_%d_%s" % (keep, os.path.basename(eng.run_dir)))
    os.makedirs(dst)
    lines = open(os.path.join(eng.run_dir, "progress.jsonl")).readlines()
    with open(os.path.join(dst, "progress.jsonl"), "w") as f:
        f.writelines(lines[:keep])
    return dst


def _line_of(eng, pred):
    for n, ln in enumerate(open(os.path.join(eng.run_dir, "progress.jsonl"))):
        if pred(json.loads(ln)):
            return n
    raise AssertionError("no such line")


class Killed:
    """One finished run of a suite with two board-less groups (lone; free:
    two, multi) and one board group (board: three, multi3), each multi
    check judged as two results; resume() resumes a copy cut where a kill
    would have cut it and says what was carried and what ran again."""
    def __init__(self, name):
        self.env = Env(name)
        self.tb = TwoBoards(self.env)
        self.tb.set("wican", "WC-1")
        ids = ("one", "two", "multi", "three", "multi3")
        self.marks = {k: os.path.join(self.env.root, "%s.txt" % k) for k in ids}
        bump = lambda k: pc("open(%r,'a').write('x')" % self.marks[k])   # noqa: E731
        two = lambda run: [Result(Verdict.PASS, "a"), Result(Verdict.PASS, "b")]  # noqa: E731
        self.s = suite(
            [Group("lone", [Check("one", bump("one"), python=True)]),
             Group("free", [Check("two", bump("two"), python=True),
                            Check("multi", bump("multi"), python=True, judge=two)]),
             # after=: the bench lane runs beside the logic lane; waiting
             # puts the board group's lines after the others' in progress
             Group("board", [Check("three", bump("three"), python=True),
                             Check("multi3", bump("multi3"), python=True, judge=two)],
                   needs_lock=True, image="w1", after=["free"])],
            images={"w1": Image("w1", "fake", expect="WC-1", board="wican")},
            flashers={"fake": self.tb.flasher}, identify=self.tb.identify)
        self.eng, rec = run(self.env, self.s)
        assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)
        assert self.ran() == {k: 1 for k in ids}, self.ran()

    def ran(self):
        return {k: len(open(p).read()) if os.path.exists(p) else 0
                for k, p in self.marks.items()}

    def resume(self, pred, after=0):
        """Cut `after` lines past the first progress line matching pred."""
        before = self.ran()
        cut = _line_of(self.eng, pred) + 1 + after
        _e, rec = run(self.env, self.s, resume=_killed_copy(self.env, self.eng, cut))
        assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)
        carried = {r["check"].split("/")[1] for r in rec["results"] if r.get("carried_from")}
        again = {k for k, n in self.ran().items() if n > before[k]}
        return carried, again


def _is_check_end(cid):
    return lambda e: e.get("kind") == "check-end" and e.get("check") == cid


def test_carry_finished():
    """--resume and a run killed inside a group (user 2026-10-20, option A
    refined): a finished check of a board-less group is carried; a finished
    check of a group that uses a board is not, until that group's group-end
    (identify-after) is written; a group that ended before the kill is."""
    k = Killed("carryfinished")
    carried, again = k.resume(_is_check_end("free/two"))
    assert carried == {"one", "two"} and again == {"multi", "three", "multi3"}, \
        ("board-less group killed after a finished check", carried, again)
    carried, again = k.resume(_is_check_end("board/three"))
    assert "three" not in carried and "three" in again, \
        ("a finished check of an unfinished board group was carried", carried, again)
    assert carried == {"one", "two", "multi"}, carried
    carried, again = k.resume(lambda e: e.get("kind") == "group-end"
                              and e.get("group") == "board")
    assert carried == {"one", "two", "multi", "three", "multi3"} and not again, \
        ("a board group that ended", carried, again)


def test_carry_check_end():
    """--resume and a kill between two results of one check: the check runs
    again, in a board-less group and in a board group alike (user
    2026-10-20)."""
    k = Killed("carrycheckend")
    carried, again = k.resume(lambda e: e.get("id") == "free/multi/a")
    assert "multi" not in carried and "multi" in again, \
        ("a half-written check of a board-less group was carried", carried, again)
    assert carried == {"one", "two"}, carried
    carried, again = k.resume(lambda e: e.get("id") == "board/multi3/a")
    assert "multi3" not in carried and "multi3" in again, \
        ("a half-written check of a board group was carried", carried, again)


def test_compare():
    """compare reports changed verdicts and figures outside tolerance."""
    env = Env("compare")
    vals = {"v": 10.0}

    def judge(run):
        return Result(Verdict.PASS, figures={"tec": Figure(vals["v"], "")})
    s = suite([Group("g", [Check("m", pc("pass"), python=True, judge=judge,
                                 tolerances={"tec": Tol(abs=1.0)})])])
    _, a = run(env, s)
    vals["v"] = 10.5
    _, b = run(env, s)
    assert R.compare(a, b) == [], R.compare(a, b)
    vals["v"] = 12.0
    _, c = run(env, s)
    d = R.compare(a, c)
    assert len(d) == 1 and "FIGURE g/m tec" in d[0], d


def test_esptool_flasher():
    """The esptool flasher: md5 mismatch writes nothing and runs no
    esptool; success counts the verified writes; a failed after-check
    forbids any further flash."""
    from vtrux_bench.run.flash import esptool_flash
    env = Env("esptool")
    im = env.image("A", "IMG-A")
    st = env.settings()
    params = dict(im.params, md5={im.params["files"][1][1]: "0" * 32})
    logd = os.path.join(env.root, "log1")
    os.makedirs(logd)
    r = esptool_flash(im, params, st, logd)
    assert not r["ok"] and r["phase"] == "files" and not env.esptool_calls(), r
    logd = os.path.join(env.root, "log2")
    os.makedirs(logd)
    r = esptool_flash(im, im.params, st, logd)
    assert r["ok"] and "2 writes verified" in r["detail"], r
    os.environ["FAKE_ESPTOOL_MODE"] = "after-fail"
    logd = os.path.join(env.root, "log3")
    os.makedirs(logd)
    r = esptool_flash(im, im.params, st, logd)
    assert not r["ok"] and r["recoverable"] is False, r


def test_esptool_commands():
    """esptool is called with the underscore command names, chip_id and
    write_flash, which esptool v4 (ESP-IDF v5.4.x's, the fake's default
    form) requires: it refuses a dashed name as an invalid choice. Every
    call names one, and none names a dashed one (gen-inhibit reviewer,
    2026-11-11)."""
    env = Env("esptool_commands")
    im = env.image("A", "IMG-A")
    logd = os.path.join(env.root, "log")
    os.makedirs(logd)
    r = FL.esptool_flash(im, im.params, env.settings(), logd)
    assert r["ok"] and "2 writes verified" in r["detail"], r
    calls = env.esptool_calls()
    names = [[w for w in c.split() if w.replace("-", "_") in
              ("chip_id", "write_flash")] for c in calls]
    assert names == [["chip_id"], ["write_flash"], ["chip_id"]], calls


def _esptool_output(text):
    """esptool's own lines in a run_local log: without the command and
    cwd lines (`$ ...`, `# ...`) run_local writes first, or blank lines."""
    return [ln for ln in text.splitlines()
            if ln.strip() and not ln.startswith(("$ ", "# "))]


def test_esptool_v5_output():
    """On esptool v5, which prints deprecation warnings on stdout ahead of
    its output for the underscore names (v5.0.2, NICKY-XPS, 2026-11-11),
    the flasher still reads the chip type and MAC and counts the verified
    writes: a flash on the right chip succeeds, and on another MAC writes
    nothing. The warnings really are first in the log the flasher read."""
    env = Env("esptool_v5")
    os.environ["FAKE_ESPTOOL_FORM"] = "v5"
    im = env.image("A", "IMG-A")
    logd = os.path.join(env.root, "log1")
    os.makedirs(logd)
    r = FL.esptool_flash(im, im.params, env.settings(), logd)
    assert r["ok"] and "2 writes verified" in r["detail"], r
    with open(os.path.join(logd, "autoreset_before.txt")) as f:
        text = f.read()
    assert "Warning: Deprecated" in _esptool_output(text)[0] and \
        "Chip type:" in text, text
    env = Env("esptool_v5_mac")
    os.environ["FAKE_ESPTOOL_FORM"] = "v5"
    os.environ["FAKE_ESPTOOL_MAC"] = "02:00:00:00:00:02"
    im = env.image("A", "IMG-A")
    logd = os.path.join(env.root, "log2")
    os.makedirs(logd)
    r = FL.esptool_flash(im, im.params, env.settings(), logd)
    assert not r["ok"] and r["phase"] == "chip-mismatch" and \
        "MAC 02:00:00:00:00:02, expected 02:00:00:00:00:01" in r["detail"], r
    assert not [c for c in env.esptool_calls() if "write_flash" in c], \
        env.esptool_calls()


def _device_env(name):
    assert SD_LINK, SD_WHY
    env = Env(name)
    return env, env.image("A", "IMG-A")


def test_esptool_device():
    """DEVICES-SPEC S1-S3 on Linux: the flasher finds the board's port by
    its USB IDs before any esptool call, gives esptool the by-id link, and
    keeps the port and the IDs matched in its result and in
    serial_device.txt; a device that matches nothing runs no esptool and
    writes nothing, saying so."""
    env, im = _device_env("esptool_device")
    logd = os.path.join(env.root, "log1")
    os.makedirs(logd)
    r = FL.esptool_flash(im, im.params, env.settings(), logd)
    assert r["ok"] and r["port"] == SD_LINK, r
    assert r["device"] == {"port": SD_LINK, "vid": "303a", "pid": "1001",
                           "serial": "TESTX2SERIAL01", "interface": 0,
                           "usb": "1-1", "tty": "ttyACM9"}, r
    calls = env.esptool_calls()
    assert calls and all(("--port %s " % SD_LINK) in c for c in calls), calls
    with open(os.path.join(logd, "serial_device.txt")) as f:
        text = f.read()
    assert "port: %s\n" % SD_LINK in text and \
        "serial: TESTX2SERIAL01\n" in text and "interface: 0\n" in text, text
    env, im = _device_env("esptool_device_none")
    logd = os.path.join(env.root, "log2")
    os.makedirs(logd)
    st = env.settings()
    st["esptool"]["device"] = dict(FAKE_DEVICE, serial="TESTX2SERIAL09")
    r = FL.esptool_flash(im, im.params, st, logd)
    assert not r["ok"] and r["phase"] == "device" and r["recoverable"] and \
        "no device matches" in r["detail"] and "nothing run" in \
        r["detail"] and not env.esptool_calls(), (r, env.esptool_calls())


def test_esptool_port_refused():
    """On Linux a port setting (a tty name) is refused, in the image or the
    settings, beside a device or alone, and nothing is run (DEVICES-SPEC
    S1: no COM or tty name in settings)."""
    for where, extra in (("settings", {}), ("image", {"port": "/dev/ttyACM9"})):
        for with_device in (True, False):
            env, im = _device_env("port_refused_%s_%s" % (where, with_device))
            logd = os.path.join(env.root, "log")
            os.makedirs(logd)
            st = env.settings()
            if where == "settings":
                st["esptool"]["port"] = SD_LINK
            if not with_device:
                st["esptool"].pop("device")
            r = FL.esptool_flash(im, dict(im.params, **extra), st, logd)
            assert not r["ok"] and r["phase"] == "device" and \
                "a port setting" in r["detail"] and \
                not env.esptool_calls(), (where, with_device, r)


def test_esptool_windows_port():
    """On Windows the flasher keeps its COM port setting (existing tooling;
    user, 2026-11-11), and the lookup is never asked: a port is used as
    given, with no device recorded; no port refuses; a device alone is
    refused as Linux only."""
    assert FL.device_port({}, {"port": "COM9"}, env="windows") == ("COM9", None)
    assert FL.device_port({"port": "COM8"}, {"port": "COM9",
                                             "device": dict(FAKE_DEVICE)},
                          env="windows") == ("COM8", None)
    for params, et, want in (({}, {}, "no port"),
                             ({}, {"device": dict(FAKE_DEVICE)}, "Linux only")):
        try:
            got = FL.device_port(params, et, env="windows")
        except FL.SD.Refused as e:
            assert want in str(e), (et, str(e))
        else:
            raise AssertionError("not refused: %r -> %r" % (et, got))


def test_record_device():
    """The run record keeps the port resolved and the IDs matched
    (DEVICES-SPEC S3): record.json's images_used has them under `ports`
    for each flash, and the flash event carries them."""
    env, a = _device_env("record_device")
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="A")],
              images={"A": a}, identify=env.identify)
    eng, rec = run(env, s)
    assert verdicts(rec) == {"g1/x": "PASS"}, verdicts(rec)
    ports = rec["images_used"]["A"].get("ports")
    assert ports and len(ports) == 1 and ports[0]["port"] == SD_LINK and \
        ports[0]["device"]["serial"] == "TESTX2SERIAL01" and \
        ports[0]["device"]["interface"] == 0, rec["images_used"]["A"]
    ev = [e for e in rec["events"] if e["kind"] == "flash"]
    assert len(ev) == 1 and ev[0]["port"] == SD_LINK, ev


def test_remote_command():
    """The remote line: cd, env, coreutils timeout; no nice and no one-job
    environment, since builds keep every core (2026-11-12)."""
    line = P.remote_command(["python3", "x.py", "a b"], "/w", {"K": "v w"},
                            100)
    for part in ("cd /w &&", "K='v w'", "timeout --kill-after=30 100",
                 "'a b'"):
        assert part in line, (part, line)
    assert "nice" not in line and "PLATFORMIO" not in line and \
        "-j1" not in line, line
    line = P.remote_command(["x"], "/w", {"K": "v"}, 100, "/o/.pid")
    assert line.startswith("cd /w && echo $$ > /o/.pid && exec env K=v "
                           "timeout "), line


def test_remote_stop():
    """STOP now on a remote check kills its process group on the remote
    host too: the remote line records timeout's pid in the check's outdir,
    and after the local ssh is killed the runner sends the group kill for
    that file (2026-10-19: a stopped run's sim ran on on madhouse)."""
    env = Env("remotestop")
    log = os.path.join(env.root, "ssh.jsonl")
    os.environ["FAKE_SSH_LOG"] = log
    fake = (PY, os.path.join(HERE, "fake_ssh.py"))
    real_remote, real_fetch = P.run_remote, E.Engine._fetch
    P.run_remote = lambda *a, **k: real_remote(*a, ssh=fake, **k)
    E.Engine._fetch = lambda self, *a: 0
    st = env.settings(hosts={"local": {"python": [PY]},
                             "far": {"ssh": "farhost", "workroot": "/w",
                                     "python": ["python3"]}})
    s = suite([Group("g", [Check("long", ["run-forever"]),
                           Check("after", ["never"])], host="far")])
    eng = E.Engine(s, st, out=lambda *a: None)

    def stopper():
        for _ in range(300):
            time.sleep(0.1)
            if getattr(eng, "run_dir", None) and os.path.exists(log) and \
                    "run-forever" in open(log).read():
                with open(os.path.join(eng.run_dir, "STOP"), "w") as f:
                    f.write("now")
                return
    t = threading.Thread(target=stopper)
    t.start()
    try:
        rec = eng.run()
    finally:
        t.join()
        P.run_remote, E.Engine._fetch = real_remote, real_fetch
    v = verdicts(rec)
    assert v["g/long"] != "PASS" and v["g/after"] == "NOT-RUN", v
    calls = [json.loads(ln) for ln in open(log)]
    pidfile = "/w/%s/out/g/long/.runner.pid" % eng.run_id
    main = [i for i, c in enumerate(calls) if "run-forever" in c["line"]]
    assert len(main) == 1, calls
    assert ("echo $$ > %s && exec" % pidfile) in calls[main[0]]["line"], \
        calls[main[0]]
    kills = [c for c in calls[main[0] + 1:]
             if c["line"] == P.remote_kill_line(pidfile)]
    assert kills and kills[0]["host"] == "farhost", \
        "no group kill after the stop: %s" % calls[main[0] + 1:]


def test_bare_shell():
    """On Windows a local check whose argv[0] is a bare bash or sh is
    refused (CreateProcess would find WSL's bash first); a full path or a
    settings placeholder passes, as does a bare bash on a remote host or
    off Windows."""
    env = Env("bareshell")
    saved = M.WINDOWS
    st = env.settings(hosts={"local": {"python": [PY]},
                             "far": {"ssh": "nowhere"}})
    try:
        M.WINDOWS = True
        for a0 in ("bash", "BASH.EXE", "sh"):
            s = suite([Group("g", [Check("c", [a0, "x.sh"])])])
            try:
                E.Engine(s, st)
            except SuiteError as e:
                assert "System32" in str(e), e
            else:
                raise AssertionError("accepted a bare %s" % a0)
        for argv in (["C:/Program Files/Git/bin/bash.exe", "x.sh"],
                     ["{bash}", "x.sh"]):
            E.Engine(suite([Group("g", [Check("c", argv)])]), st)
        E.Engine(suite([Group("g", [Check("c", ["bash", "x.sh"])],
                              host="far")]), st)
        M.WINDOWS = False
        E.Engine(suite([Group("g", [Check("c", ["bash", "x.sh"])])]), st)
    finally:
        M.WINDOWS = saved


# ---- the bench lock: both sources (bench-lock-spec sections 6, 7, 8) ----
# Written from reverse-it firmware/bench-lock-spec.md (sha256 1f1d5551...,
# user rulings of 2026-10-22). The log tests use a real bench_lock log in
# the scratch folder (never firmware/bench-log.jsonl), the suite's role a
# bench_lock role, and this test process as the runner (its PID is the
# runner's).
from vtrux_bench import bench_lock as BL          # noqa: E402

CI = "tester (charge-interposer)"
GI = "tester (gen-inhibit)"
BENCH = os.path.dirname(HERE)
BD = {"wican": "WC-1 read by hand", "x2": "X2-APP read by hand"}


def log_env(name):
    env = Env(name)
    p = os.path.join(env.root, "bench-log.jsonl")
    BL.init(p, GI)
    return env, p


def log_events(p):
    """The log's events. On Windows a read during another thread's locked
    write is refused (PermissionError); the lock is brief, so retry for up
    to 10 s rather than kill a scenario thread (2026-10-22: one died, and
    its run waited on its PAUSE file for ever)."""
    end = time.monotonic() + 10
    while True:
        try:
            with open(p, encoding="utf-8") as f:
                return [json.loads(l) for l in f.read().splitlines()]
        except PermissionError:
            if time.monotonic() > end:
                raise
            time.sleep(0.05)


def bl_code(p, body):
    """A python -c check that acts on the log as another session would."""
    code = ("import os, sys; sys.path.insert(0, %r); "
            "from vtrux_bench import bench_lock as B; p = %r; %s"
            % (BENCH, p, body))
    return code.replace("{", "{{").replace("}", "}}")   # the runner formats argv


def first_line(eng, name):
    with open(os.path.join(eng.run_dir, name)) as f:
        return f.readline().rstrip("\n")


def _steal(mode, env, p):
    """A check that takes the lock away between cases: in the Holder line,
    or by a hand release of the run's lock and another role's take."""
    if mode == "holder":
        return chk("steal", "open(%r, 'w').write('## Lock\\n\\n**Holder:** "
                   "tester (thief)\\n')" % env.holder)
    return chk("steal", bl_code(p, "B.release(p, %r, %r); B.take(p, %r, %d)"
                                % (CI, BD, GI, os.getpid())))


def test_lock_each_case():
    """Section 6 (and the lock-gap fix of section 7, in both sources): the
    lock is checked before every bench case, not only at its group's
    start; a case already running runs to its end."""
    for mode in ("holder", "log"):
        env, p = log_env("each_case_" + mode)
        s = suite([Group("g", [_steal(mode, env, p), chk("next", "pass")],
                         needs_lock=True)], role=CI)
        env.set_holder(CI)
        st = env.settings(bench_lock_log=p) if mode == "log" else \
            env.settings()
        eng, rec = run(env, s, settings=st)
        v = verdicts(rec)
        assert v["g/steal"] == "PASS", (mode, v)
        nxt = [r for r in rec["results"] if r["id"] == "g/next"][0]
        assert nxt["verdict"] == "NOT-RUN" and nxt["detail"].startswith(
            "LOCK LOST"), (mode, nxt)
        if mode == "log":
            evs = log_events(p)
            assert evs[-1]["event"] == "lost" and evs[-1]["who"] == CI and \
                evs[-1]["run"] == eng.run_id, evs[-1]


def test_lost_record():
    """Section 6: a lost lock opens the record and index.txt with "LOCK
    LOST: <state>"; the log gets `lost` (not when it does not replay);
    every remaining case is NOT-RUN; the end pass, the end hooks' board
    actions and the release are skipped."""
    env, p = log_env("lost_holder")
    env.set_holder(CI)
    s = suite([Group("g", [_steal("holder", env, p), chk("n", "pass")],
                     needs_lock=True)], role=CI)
    eng, rec = run(env, s)
    for name in ("index.txt", "board_state.txt"):
        assert first_line(eng, name).startswith("LOCK LOST: not held by "
                                                "%s" % CI), name
    assert rec["lock_lines"][0].startswith("LOCK LOST: "), rec["lock_lines"]
    # a log that does not replay: no lost line can be appended
    env, p = log_env("lost_garbage")
    s = suite([Group("g", [chk("spoil", "open(%r, 'a').write('garbage\\n')" % p),
                           chk("n", "pass")], needs_lock=True)], role=CI)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    assert verdicts(rec)["g/n"] == "NOT-RUN", verdicts(rec)
    idx = first_line(eng, "index.txt")
    assert idx.startswith("LOCK LOST: the bench log does not replay"), idx
    with open(p) as f:
        assert f.read().endswith("garbage\n"), "appended to a broken log"
    # with boards: lost after a flash -> no end pass, no end hooks, no release
    env, p = log_env("lost_boards")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    s = tb.suite([Group("a", [_steal("log", env, p), chk("n", "pass")],
                        needs_lock=True, image=["w2", "x2app"])], role=CI)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    assert tb.flashes == ["w2"], tb.flashes
    assert not [c for c in tb.calls if c[0] == "on_end"], tb.calls
    evs = log_events(p)
    assert [e["event"] for e in evs[-3:]] == ["release", "take", "lost"], evs
    assert not any(e["event"] == "release" and e["who"] == CI and
                   e.get("note", "").startswith("end of run") for e in evs)
    assert rec["lock"]["lost"].startswith("LOCK LOST"), rec["lock"]


def _pause_scenario(name, mode="log", during=None, fail_flash=False,
                    second=None, image=("w1", "x2app"), keep_lock=None,
                    gate_delay=0):
    """check 1 writes PAUSE. Another role takes the bench while the run is
    paused (log: once free_paused is in the log; holder: by the Holder
    line, once the run's record shows its pause), `during` runs, the PAUSE
    file goes, and the other role hands the bench back 1.2 s later. check 2
    passes only if the lock is the run's own when it starts.

    Before 2026-11-05 the holder-mode role took the bench as soon as the
    PAUSE file existed. When the runner reached its next case boundary more
    than about 0.1 s after check 1 wrote the file (a loaded machine), the
    role had already taken the bench and removed the file, so the runner
    saw no pause under way and another role's Holder: a lost lock, as SPEC
    section 7 says, and test_pause_own_lock failed in mode "holder". A
    person waits for the run to stop before editing the Holder line; so
    does the role now. `gate_delay` makes the runner that slow on purpose:
    it sleeps that long before each case gate."""
    env, p = log_env(name)
    env.set_holder(CI)
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    mk = "import os, sys; open(os.path.join(sys.argv[1], 'PAUSE'), 'w').close()"
    if second is None and mode == "log":
        second = Check("second", ["-c", (
            "import json, sys; e = json.loads(open(sys.argv[1]).read()"
            ".splitlines()[-1]); sys.exit(0 if (e['event'], e['who'], "
            "e.get('run'), e['pid']) == ('take', %r, sys.argv[2], %d) else 1)"
            % (CI, os.getpid())), p, "{run_id}"], python=True)
    elif second is None:
        second = Check("second", ["-c", (
            "import sys; t = open(sys.argv[1]).read(); "
            "sys.exit(0 if '**Holder:** %s' in t else 1)" % CI),
            env.holder], python=True)
    s = tb.suite([Group("a", [Check("first", ["-c", mk, "{run_dir}"],
                                    python=True), second],
                        needs_lock=True, image=list(image))], role=CI)
    st = env.settings(bench_lock_log=p) if mode == "log" else env.settings()
    eng = E.Engine(s, st, out=lambda *a: None, keep_lock=keep_lock)
    if gate_delay:
        real_gate = eng.case_gate

        def slow_gate():
            time.sleep(gate_delay)
            return real_gate()
        eng.case_gate = slow_gate
    seen = {}

    def other():
        """The other role. If it fails, it still frees the run (STOP, no
        PAUSE, GI's lock released) and the scenario raises its error,
        rather than leave eng.run() waiting for ever."""
        try:
            other_steps()
        except Exception:
            seen["error"] = traceback.format_exc()
            rd = getattr(eng, "run_dir", None)
            if rd:
                open(os.path.join(rd, "STOP"), "w").close()
                if os.path.exists(os.path.join(rd, "PAUSE")):
                    os.remove(os.path.join(rd, "PAUSE"))
            try:
                h = BL.read_state(p)["holder"]
                if h is not None and h["who"] == GI:
                    BL.release(p, GI, BD)
            except Exception:
                pass

    def other_steps():
        for _ in range(150):
            time.sleep(0.1)
            if not getattr(eng, "run_dir", None):
                continue
            if mode == "log":
                ready = any(e["event"] == "free_paused" for e in log_events(p))
            else:
                r = getattr(eng, "rec", None)
                ready = r is not None and any(
                    e["kind"] == "pause" for e in list(r.events))
            if ready:
                break
        else:
            seen["timeout"] = True
        if mode == "log" and not seen.get("timeout"):
            BL.take(p, GI, os.getpid(), note="between")
        elif mode == "holder":
            env.set_holder(GI)
        if during:
            during(tb, eng, seen)
        if os.path.exists(os.path.join(eng.run_dir, "PAUSE")):
            os.remove(os.path.join(eng.run_dir, "PAUSE"))
        time.sleep(1.2)
        seen["back"] = time.time()
        if mode == "log" and not seen.get("timeout"):
            BL.release(p, GI, BD)
        elif mode == "holder":
            env.set_holder(CI)
    t = threading.Thread(target=other)
    t.start()
    rec = eng.run()
    t.join()
    assert "error" not in seen, "the other role failed:\n%s" % seen["error"]
    return env, p, tb, eng, rec, seen


def test_pause_own_lock():
    """Sections 6 and 7: a pause ends only on the run's own lock. Before
    the cutover, the run waits until the PAUSE file is gone and the Holder
    is its role's again; under the log, the runner takes the lock back
    under its PID and run id, waiting while another role holds it. Others'
    takes and releases in between are not a loss. Each mode runs twice,
    the second time with the runner 0.5 s slow to reach each case gate, the
    timing that made mode "holder" fail on a loaded machine before
    2026-11-05 (_pause_scenario)."""
    for mode in ("holder", "log"):
        for delay in (0, 0.5):
            env, p, tb, eng, rec, seen = _pause_scenario(
                "ownlock_%s_%s" % (mode, delay), mode, gate_delay=delay)
            v = verdicts(rec)
            assert v == {"a/first": "PASS", "a/second": "PASS"}, \
                (mode, delay, v)
            assert not rec["lock"]["lost"], (mode, delay, rec["lock"])
            res = [e for e in rec["events"] if e["kind"] == "resume"]
            assert res and res[0]["t"] >= R.stamp(
                __import__("datetime").datetime.fromtimestamp(
                    int(seen["back"])).astimezone()), (mode, delay, res, seen)


def test_self_pause():
    """Section 6 under the log: at the case boundary after a PAUSE file the
    runner writes free_paused for its run with its board reads, and frees
    the bench; the resume take is its own (PID and run id); the record
    notes the resume."""
    env, p, tb, eng, rec, seen = _pause_scenario("selfpause")
    assert not seen.get("timeout"), "no free_paused within 15 s"
    evs = log_events(p)
    got = [(e["event"], e["who"]) for e in evs]
    assert got == [("init", GI), ("take", CI), ("free_paused", CI),
                   ("take", GI), ("release", GI), ("take", CI),
                   ("release", CI)], got
    fp, back = evs[2], evs[5]
    assert fp["run"] == eng.run_id and back["run"] == eng.run_id and \
        back["pid"] == os.getpid() == evs[1]["pid"], (fp, back)
    assert fp["boards"]["x2"].startswith("X2-APP, identified off the wire"), fp
    kinds = [e["kind"] for e in rec["events"]]
    assert "free-paused" in kinds and "resume" in kinds, kinds


def test_pause_hooks():
    """Section 6: at a free_paused the runner does what it does at its end
    release: the end hooks run (the WiCAN release reboot), and each board's
    read is that hook or identify read."""
    env, p, tb, eng, rec, seen = _pause_scenario("pausehooks")
    fp = [e for e in log_events(p) if e["event"] == "free_paused"][0]
    ends = [c for c in tb.calls if c[0] == "on_end"]
    assert len(ends) == 4, ends          # two at the pause, two at the end
    for b in ("wican", "x2"):
        assert "end hook" in fp["boards"][b] and "fine" in fp["boards"][b], fp
    assert not tb.flashes, "flashed at a pause: %s" % tb.flashes


def test_resume_identify():
    """Section 6: after the resume take, before the next case, the start
    hooks run again and every board the remaining cases use is identified
    against its expected image; one that differs is flashed and identified
    again (one that cannot be flashed: test_resume_stop). The record notes
    the identities read at the resume."""
    def change(tb, eng, seen):
        tb.set("x2", "OTHER")
    env, p, tb, eng, rec, seen = _pause_scenario("resume_id", during=change)
    assert verdicts(rec) == {"a/first": "PASS", "a/second": "PASS"}, \
        verdicts(rec)
    assert tb.flashes == ["x2app"], tb.flashes
    starts = [c for c in tb.calls if c[0] == "on_start"]
    assert len(starts) == 4, starts
    ri = [e for e in rec["events"] if e["kind"] == "resume-identify"][0]
    assert ri["identities"] == {"wican": "WC-1", "x2": "X2-APP"}, ri
    assert "resume" in [g["name"] for g in rec["groups"]]


def test_resume_stop():
    """Section 6 (user, Q6 (i)): a board the resume cannot put back is a
    stop, not a lost lock. The runner keeps the lock (no `lost`, no
    lost-refused), records every remaining case NOT-RUN, opens its record
    and index.txt with "STOPPED: <board> is <image read>, expected
    <image> (<its expect value>), cannot flash", and releases with the
    identities it read."""
    def change_nofl(tb, eng, seen):
        tb.set("x2", "OTHER")
        tb.fail_flash = True
    env, p, tb, eng, rec, seen = _pause_scenario("resume_stop",
                                                 during=change_nofl)
    line = "STOPPED: x2 is OTHER, expected x2app (X2-APP), cannot flash"
    sec = [r for r in rec["results"] if r["id"] == "a/second"][0]
    assert sec["verdict"] == "NOT-RUN" and line in sec["detail"], sec
    assert first_line(eng, "index.txt") == line, first_line(eng, "index.txt")
    assert rec["lock_lines"][0] == line, rec["lock_lines"]
    assert not rec["lock"]["lost"], rec["lock"]
    kinds = [e["kind"] for e in rec["events"]]
    assert "lost-refused" not in kinds and "lock-lost" not in kinds, kinds
    evs = log_events(p)
    assert not [e for e in evs if e["event"] == "lost"], evs
    rl = evs[-1]
    assert rl["event"] == "release" and rl["who"] == CI, rl
    assert rl["boards"]["x2"].startswith("OTHER, identified off the wire"), rl
    assert BL.read_state(p)["holder"] is None
    # with --keep-lock the stop still hands the lock to the session
    keeper = spawn_foreign("vtrux_resume_stop_%d" % os.getpid())
    try:
        env, p, tb, eng, rec, seen = _pause_scenario(
            "resume_stop_keep", during=change_nofl, keep_lock=keeper)
        assert rec["lock_lines"][0] == line, rec["lock_lines"]
        rl, tk = log_events(p)[-2:]
        assert rl["event"] == "release" and rl["who"] == CI and \
            rl["boards"]["x2"].startswith("OTHER"), rl
        assert tk["event"] == "take" and tk["who"] == CI and \
            tk["pid"] == keeper and "run" not in tk, tk
        assert BL.read_state(p)["holder"]["pid"] == keeper
    finally:
        kill_pid(keeper)


def test_resume_stop_kept():
    """Section 6's stop bullet (gen-inhibit reviewer, 2026-10-22 and
    2026-11-11): the end pass leaves the board the resume could not put back
    untouched, as found for the investigation -- no identify, and no
    idle-safe load even though it was expected on an image not safe to leave
    idle -- so the release carries the identity read at the resume. It gets
    a LEFT ON IDLE-UNSAFE IMAGE line naming the stop. The other board (the
    WiCAN, on w2) goes through the end pass as normal, and the end hooks
    run for both."""
    def change_x2(tb, eng, seen):
        tb.set("x2", "OTHER")
        tb.fail_flash = {"x2"}
    env, p, tb, eng, rec, seen = _pause_scenario(
        "resume_stop_kept", during=change_x2, image=("w2", "jammer"))
    stop = "STOPPED: x2 is OTHER, expected jammer (JAM), cannot flash"
    assert verdicts(rec) == {"a/first": "PASS", "a/second": "NOT-RUN"}, \
        verdicts(rec)
    # w2 and the jammer at the start, the failed jammer at the resume; no
    # x2app at the end
    assert tb.flashes == ["w2", "jammer", "jammer"], tb.flashes
    c = tb.calls
    last_fl = max(n for n, x in enumerate(c) if x[:2] == ("flash", "x2"))
    assert not [x for x in c[last_fl:] if x[:2] == ("identify", "x2")], c
    assert [x for x in c[last_fl:] if x[:2] == ("identify", "wican")], c
    assert open(tb.path("x2")).read() == "OTHER"
    want = ("LEFT ON IDLE-UNSAFE IMAGE: x2 on jammer, not loaded, left as "
            "found for the investigation (%s)" % stop)
    assert rec["idle_unsafe_left"] == [want], rec["idle_unsafe_left"]
    idx = open(os.path.join(eng.run_dir, "index.txt")).read().splitlines()
    assert idx[:2] == [stop, want], idx[:2]
    assert c[-2:] == [("on_end", "wican", "w2"), ("on_end", "x2", None)], \
        c[-2:]
    rl = log_events(p)[-1]
    assert rl["event"] == "release" and rl["who"] == CI and \
        rl["note"].startswith(want), rl
    assert rl["boards"]["x2"].startswith("OTHER, identified off the wire"), rl


def test_stop_no_resume():
    """STOP during a pause (gen-inhibit reviewer, 2026-10-22): the runner
    takes the lock back for the end pass and the release, but runs no start
    hooks and identifies or flashes nothing for cases that will not run."""
    def stop(tb, eng, seen):
        tb.set("x2", "OTHER")
        with open(os.path.join(eng.run_dir, "STOP"), "w") as f:
            f.write("STOP (test)")
    env, p, tb, eng, rec, seen = _pause_scenario("stop_no_resume",
                                                 during=stop)
    v = verdicts(rec)
    # the X2 changed under the group, so its finished case is VOID
    assert v["a/first"] == "VOID" and v["a/second"] == "NOT-RUN", v
    starts = [c for c in tb.calls if c[0] == "on_start"]
    assert len(starts) == 2, "start hooks at the resume: %s" % starts
    assert tb.flashes == [], "flashed after STOP: %s" % tb.flashes
    evs = log_events(p)
    assert (evs[-2]["event"], evs[-1]["event"]) == ("take", "release") and \
        evs[-1]["who"] == CI, evs[-2:]
    assert not rec["lock"]["lost"], rec["lock"]


def test_own_pid():
    """Section 6: the run goes on only while the lock is held by its role,
    its run id and its PID; a hand take --run <id> under a session's PID
    is a lost lock."""
    env, p = log_env("own_pid")
    hand = chk("hand", bl_code(p, (
        "r = sys.argv[1]; B.free_paused(p, %r, r, %r); "
        "B.take(p, %r, os.getpid(), run=r)" % (CI, BD, CI))))
    hand.argv.append("{run_id}")
    s = suite([Group("g", [hand, chk("n", "pass")], needs_lock=True)],
              role=CI)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    n = [r for r in rec["results"] if r["id"] == "g/n"][0]
    assert n["verdict"] == "NOT-RUN" and n["detail"].startswith("LOCK LOST"), n
    evs = log_events(p)
    assert evs[-1]["event"] == "take" and evs[-1]["pid"] != os.getpid(), \
        "the runner released or wrote after the hand take: %s" % evs[-1]


def test_take_first():
    """Section 8: the runner takes the lock under its own PID and its run
    id, formed first, before the run folder exists; a refused take leaves
    no folder. A role that may not take is refused the same way."""
    env, p = log_env("takefirst")
    BL.take(p, GI, os.getpid())
    s = suite([Group("g", [chk("x", "pass")], needs_lock=True)], role=CI)
    for st_suite in (s, suite([Group("g", [chk("x", "pass")],
                                     needs_lock=True)])):
        try:
            run(env, st_suite, settings=env.settings(bench_lock_log=p))
        except LockError:
            pass
        else:
            raise AssertionError("started without the log's take")
        assert not os.path.exists(env.runs) or not os.listdir(env.runs), \
            os.listdir(env.runs)
    BL.release(p, GI, BD)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    tk = log_events(p)[-2]
    assert tk["event"] == "take" and tk["who"] == CI and \
        tk["pid"] == os.getpid() and tk["run"] == eng.run_id, tk


def test_folder_release():
    """Section 6: a take whose run folder then cannot be made is released
    before the runner raises, each board "not read: run folder not
    created"; no lock is left under a runner that exits."""
    env, p = log_env("folder_release")
    s = suite([Group("g", [chk("x", "pass")], needs_lock=True)], role=CI)
    real = E.new_run_dir

    def broken(runs_root, suite_name, run_id=None):
        raise OSError("disk full (test)")
    E.new_run_dir = broken
    try:
        run(env, s, settings=env.settings(bench_lock_log=p))
    except OSError:
        pass
    else:
        raise AssertionError("ran without a run folder")
    finally:
        E.new_run_dir = real
    evs = log_events(p)
    assert [e["event"] for e in evs[-2:]] == ["take", "release"], evs
    rl = evs[-1]
    assert rl["who"] == CI and all(
        v.startswith("not read: run folder not created (disk full (test))")
        and evs[-2]["run"] in v for v in rl["boards"].values()), rl
    assert BL.read_state(p)["holder"] is None


def test_release_end():
    """Section 6: at its run's end, after the end pass and the end hooks,
    the runner releases, each board's read the one it made or "not read:
    <why>"."""
    env, p = log_env("release_end")
    tb = Hooked(env)
    tb.set("x2", "X2-APP")
    tb.set("wican", "WC-1")
    eng, rec = run(env, tb.suite(role=CI),
                   settings=env.settings(bench_lock_log=p))
    rl = log_events(p)[-1]
    assert rl["event"] == "release" and rl["who"] == CI, rl
    x2 = rl["boards"]["x2"]
    assert x2.startswith("X2-APP, identified off the wire") and \
        "end hook" in x2 and eng.run_id in x2, x2
    assert rl["boards"]["wican"].startswith("WC-1, identified"), rl
    st = BL.read_state(p)
    assert st["holder"] is None and st["boards"]["known"], st
    bs = open(os.path.join(eng.run_dir, "board_state.txt")).read()
    assert "Lock released by the runner" in bs, bs
    # a suite with no image or hook for the WiCAN: "not read", so unknown
    env, p = log_env("release_unread")
    tb = TwoBoards(env)
    tb.set("x2", "X2-APP")
    s = suite([Group("a", [chk("x", "pass")], needs_lock=True,
                     image="x2app")], role=CI,
              images={"x2app": Image("x2app", "fake", expect="X2-APP",
                                     board="x2")},
              flashers={"fake": tb.flasher}, identify=tb.identify)
    eng, rec = run(env, s, settings=env.settings(bench_lock_log=p))
    rl = log_events(p)[-1]
    assert rl["event"] == "release" and rl["boards"]["wican"].startswith(
        "not read: run has no image or end hook for the wican"), rl
    assert not BL.read_state(p)["boards"]["known"]


def test_keep_lock():
    """Section 6: with --keep-lock PID the runner releases and takes again
    for its role, with no run, under PID, in one locked step. Refused at
    start without a log, for the runner's own PID or a dead one."""
    env, p = log_env("keeplock")
    s = suite([Group("g", [chk("x", "pass")], needs_lock=True)], role=CI)
    keeper = spawn_foreign("vtrux_keep_lock_%d" % os.getpid())
    calls = []
    real = BL.write_all

    def spy(path, evs):
        calls.append([e["event"] for e in evs])
        return real(path, evs)
    BL.write_all = spy
    try:
        eng, rec = run(env, s, settings=env.settings(bench_lock_log=p),
                       keep_lock=keeper)
    finally:
        BL.write_all = real
    try:
        evs = log_events(p)
        rl, tk = evs[-2], evs[-1]
        assert rl["event"] == "release" and tk["event"] == "take" and \
            tk["who"] == CI and tk["pid"] == keeper and "run" not in tk, evs
        assert ["release", "take"] in calls, calls
        st = BL.read_state(p)
        assert st["holder"]["pid"] == keeper and st["holder"]["run"] is None
        for kp, settings in ((keeper, env.settings()),
                             (os.getpid(), env.settings(bench_lock_log=p)),
                             (dead_child(), env.settings(bench_lock_log=p))):
            before = os.listdir(env.runs)
            try:
                run(env, s, settings=settings, keep_lock=kp)
            except LockError:
                pass
            else:
                raise AssertionError("--keep-lock %r accepted" % kp)
            assert os.listdir(env.runs) == before, "a folder was made"
    finally:
        kill_pid(keeper)


def dead_child():
    import subprocess
    c = subprocess.Popen([PY, "-c", "pass"])
    c.wait()
    return c.pid


def test_canmain_check():
    """SPEC section 8: a run checks CAN1 at its start, beside listening
    first, and refuses unless canmain is down. The setting names the probe;
    no setting refuses; "absent" checks that no adapter answering to
    canmain is present; the reason is printed in the start line. A refusal
    comes before the take: nothing in the log, no run folder. A run with
    no bench group needs no setting."""
    env, p = log_env("canmain")
    s = suite([Group("g", [chk("a", "pass")], needs_lock=True)], role=CI)
    base = env.settings(bench_lock_log=p)
    n0 = len(log_events(p))

    def refused(st, want):
        try:
            E.Engine(s, st, out=lambda *a: None).run()
        except LockError as e:
            msg = str(e)
        else:
            raise AssertionError("ran without a canmain check: %s" % want)
        assert "canmain check" in msg and want in msg, (want, msg)
        assert len(log_events(p)) == n0, "the refusal wrote to the log"
        assert not os.path.exists(env.runs) or not os.listdir(env.runs), \
            "the refusal made a run folder"

    refused({k: v for k, v in base.items() if k != "canmain_check"},
            "no canmain_check")
    refused(dict(base, canmain_check=dict(CM_SETTING, probe="down")),
            "is not one of")
    refused(dict(base, canmain_check=dict(CM_SETTING, reason="")),
            "needs reason")
    dev = os.path.join(CM_ROOT, "bus", "usb", "devices", "1-4")
    os.makedirs(dev)
    with open(os.path.join(dev, "serial"), "w") as f:
        f.write(CM_SERIAL + "\n")
    CM_IDS.append("USB\\VID_1D50&PID_606F\\" + CM_SERIAL)
    try:
        refused(base, "is present" if E.BL.this_env() == "linux"
                else "carry canmain's serial")
    finally:
        shutil.rmtree(dev)
        CM_IDS.pop()
    out = []
    eng = E.Engine(s, base, out=out.append)
    rec = eng.run()
    assert verdicts(rec) == {"g/a": "PASS"}, verdicts(rec)
    line = out[0] if out else ""
    assert line.startswith("canmain check: probe absent (made-up bench, no "
                           "canmain): canmain is down"), out
    assert CM_SERIAL not in line, "the serial is printed: %s" % line
    assert rec["canmain_check"] == line, rec.get("canmain_check")
    plan = E.Engine(s, base).plan_lines()
    assert any("canmain check, probe absent" in ln for ln in plan), plan
    free = suite([Group("h", [chk("b", "pass")])], role=CI)
    eng, rec = run(env, free, settings={k: v for k, v in base.items()
                                        if k != "canmain_check"})
    assert verdicts(rec) == {"h/b": "PASS"} and rec["canmain_check"] is None, \
        rec.get("canmain_check")


TESTS = [test_validate, test_bare_shell, test_basic_verdicts, test_never_overwrite,
         test_known_fail, test_lock_guard, test_flash_gate,
         test_chip_mismatch, test_chip_required, test_image_schedule,
         test_identify_error, test_identify_after,
         test_void_resume,
         test_two_boards, test_start_identify, test_standing_refused,
         test_ota_stops_bench, test_idle_safe_named, test_board_hooks, test_start_hook_stops,
         test_release_line, test_end_hooks_crash, test_end_identify,
         test_idle_safe_end, test_idle_unsafe_line, test_idle_pause_untouched,
         test_stop_pause,
         test_build_alongside, test_after, test_wallclock_records,
         test_own_wallclock_wait, test_wallclock_hand_roles,
         test_placement_slices, test_placement_readback, test_requires,
         test_requires_whole, test_subset_mark, test_subset_not_whole,
         test_product_digest, test_digest_bridge,
         test_digest_own_folder, test_digest_folder_removed,
         test_digest_in_scratch, test_digest_hard_files_removed,
         test_digest_unremovable_printed,
         test_resume_code,
         test_redact_scope, test_gate, test_skip, test_missing_setting, test_base_env,
         test_record_settings,
         test_timeout,
         test_resume, test_carry_finished, test_carry_check_end, test_compare,
         test_esptool_flasher, test_esptool_commands, test_esptool_v5_output,
         test_esptool_device, test_esptool_port_refused,
         test_esptool_windows_port, test_record_device,
         test_remote_command,
         test_remote_stop,
         test_lock_each_case, test_lost_record, test_pause_own_lock,
         test_self_pause, test_pause_hooks, test_resume_identify,
         test_resume_stop, test_resume_stop_kept, test_stop_no_resume,
         test_own_pid, test_take_first, test_folder_release, test_release_end,
         test_keep_lock, test_canmain_check]


# ---- mutants: each test's rule switched off -----------------------------
def _rule(name):
    def on():
        E.RULES[name] = False

    def off():
        E.RULES[name] = True
    return on, off


def _patch(mod, attr, value):
    saved = {}

    def on():
        saved["v"] = getattr(mod, attr)
        setattr(mod, attr, value)

    def off():
        setattr(mod, attr, saved["v"])
    return on, off


def _both(*pairs):
    return (lambda: [p[0]() for p in pairs], lambda: [p[1]() for p in pairs])


def _chip_read_first_line(text, _orig=FL.chip_read):
    """The chip_id output read from esptool's first line of output only (the
    first line after run_local's own), as a parser that expects the output
    to start with the chip would."""
    out = _esptool_output(text)
    return _orig(out[0] if out else "")


def _reusing_run_dir(runs_root, suite_name, run_id=None):
    path = os.path.join(runs_root, run_id or "x")
    os.makedirs(path, exist_ok=True)
    for f in ("progress.jsonl", "record.json", "index.txt"):
        try:
            os.remove(os.path.join(path, f))
        except OSError:
            pass
    return run_id or "x", path


def _refusing_run_check(self, g, c, carried, _orig=E.Engine.run_check):
    """The old guard: a wall-clock check NOT-RUN while anything else may be
    timing-sensitive on its host."""
    if c.cls == "wallclock":
        o = self.others_on(g.host)
        if o["wallclock"]:
            self._not_run(g, c, "refused: another wall-clock test on %s (%s)"
                          % (g.host, o["why"]))
            return False
    return _orig(self, g, c, carried)


def _niced_remote_command(*a, _orig=P.remote_command, **k):
    return _orig(*a, **k).replace("timeout --kill", "nice -n 19 timeout --kill")


def _write_over(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


MUTANTS = {
    "test_validate": _patch(E, "validate", lambda *a, **k: None),
    "test_bare_shell": _patch(M, "RULES", dict(M.RULES, bare_shell=False)),
    "test_basic_verdicts": _rule("no_verdict"),
    "test_never_overwrite": _both(_patch(E, "new_run_dir", _reusing_run_dir),
                                  _patch(R, "write_new", _write_over)),
    "test_known_fail": _rule("known_fail"),
    "test_lock_guard": _rule("lock_guard"),
    "test_flash_gate": _rule("flash_gate"),
    "test_chip_mismatch": _patch(FL, "RULES", dict(FL.RULES, chip_match=False)),
    "test_chip_required": _patch(FL, "RULES", dict(FL.RULES, chip_required=False)),
    # the port taken from the settings as written, not found by the IDs
    "test_esptool_device": _patch(FL, "RULES", dict(FL.RULES, resolve_first=False)),
    "test_esptool_port_refused": _patch(FL, "RULES", dict(FL.RULES, port_refused=False)),
    # Windows goes through the Linux lookup
    "test_esptool_windows_port": _patch(FL, "RULES", dict(FL.RULES, windows_port=False)),
    "test_record_device": _rule("record_device"),
    "test_image_schedule": _rule("image_schedule"),
    "test_identify_error": _rule("identify_error"),
    "test_identify_after": _rule("identify_after"),
    "test_void_resume": _patch(R.Recorder, "amend", lambda self, r: None),
    # no restore: the mutant puts the old end restore back
    "test_two_boards": _patch(E.Engine, "end_pass", _end_pass_restores),
    "test_start_identify": _rule("start_identify"),
    "test_standing_refused": _patch(M, "RULES", dict(M.RULES,
                                                     standing_refused=False)),
    "test_ota_stops_bench": _patch(M, "RULES", dict(M.RULES,
                                                    ota_stops_bench=False)),
    "test_idle_safe_named": _patch(M, "RULES", dict(M.RULES,
                                                    idle_safe_named=False)),
    "test_board_hooks": _rule("board_hooks"),
    "test_start_hook_stops": _rule("start_hook_stops"),
    "test_release_line": _rule("release_line"),
    "test_end_hooks_crash": _rule("end_crash_boards"),
    "test_end_identify": _rule("end_identify"),
    "test_idle_safe_end": _rule("idle_safe_end"),
    "test_idle_unsafe_line": _rule("idle_unsafe_line"),
    # a paused run leaves its boards: the mutant loads idle-safe at a pause
    "test_idle_pause_untouched": _patch(E.Engine, "pause_reads",
                                        _pause_loads_idle_safe),
    "test_stop_pause": _rule("stop_pause"),
    # a build back at one job while a wall-clock test runs alongside
    "test_build_alongside": _patch(
        E.Engine, "build_jobs",
        lambda self, alongside: "1" if alongside else str(os.cpu_count() or 1)),
    "test_after": _rule("after"),
    # the guard refusing again
    "test_wallclock_records": _patch(E.Engine, "run_check", _refusing_run_check),
    "test_own_wallclock_wait": _rule("own_wallclock_wait"),
    # the HAND_ROLES skip removed: a hand take counts again
    "test_wallclock_hand_roles": _rule("hand_roles_wallclock"),
    # every check in build.slice, whatever its class or platform
    "test_placement_slices": _patch(E.Engine, "placement_for",
                                    lambda self, g, c: "build.slice"),
    # the placement not read back
    "test_placement_readback": _rule("placement_readback"),
    "test_requires": _rule("requires"),
    "test_requires_whole": _rule("requires_whole"),
    "test_subset_mark": _rule("subset_mark"),
    "test_subset_not_whole": _rule("subset_not_whole"),
    "test_product_digest": _rule("product_digest"),
    "test_digest_bridge": _rule("digest_bridge"),
    "test_digest_own_folder": _patch(__import__("tempfile"), "mkdtemp", _fixed_mkdtemp),
    "test_digest_folder_removed": _patch(shutil, "rmtree", lambda *a, **k: None),
    "test_digest_in_scratch": _patch(E, "digest_root",
                                     lambda repo: os.path.join(repo, "digest")),
    "test_digest_hard_files_removed": _patch(
        E, "remove_tree", lambda p: shutil.rmtree(p, ignore_errors=True)),
    "test_digest_unremovable_printed": _patch(
        E, "remove_tree", lambda p: shutil.rmtree(p, ignore_errors=True)),
    "test_resume_code": _rule("resume_code"),
    "test_redact_scope": _rule("redact_scope"),
    "test_gate": _rule("gate"),
    "test_skip": _rule("skip"),
    "test_missing_setting": _rule("missing_setting"),
    "test_base_env": _rule("base_env"),
    "test_record_settings": _rule("record_settings"),
    "test_timeout": _rule("timeout"),
    "test_resume": _patch(E.Engine, "_carried", lambda self: {}),
    "test_carry_finished": _rule("carry_finished"),
    "test_carry_check_end": _rule("carry_check_end"),
    "test_compare": _patch(R, "_within", lambda a, b, t: True),
    "test_esptool_flasher": _patch(
        __import__("vtrux_bench.run.flash", fromlist=["md5_of"]), "md5_of",
        lambda p: "0" * 32),
    # the dashed names back, as before 2026-11-11
    "test_esptool_commands": _patch(FL, "COMMANDS", {"chip_id": "chip-id",
                                                     "write_flash": "write-flash"}),
    "test_esptool_v5_output": _patch(FL, "chip_read", _chip_read_first_line),
    # the remote line niced again
    "test_remote_command": _patch(P, "remote_command", _niced_remote_command),
    "test_remote_stop": _patch(P, "RULES", {"remote_stop": False}),
    "test_lock_each_case": _rule("lock_each_case"),
    "test_lost_record": _rule("lost_record"),
    "test_pause_own_lock": _rule("pause_own_lock"),
    "test_self_pause": _rule("self_pause"),
    "test_pause_hooks": _rule("pause_hooks"),
    "test_resume_identify": _rule("resume_identify"),
    "test_resume_stop": _rule("resume_stop"),
    # the mutant gives the stopped board the end pass (identify, then load)
    "test_resume_stop_kept": _rule("resume_stop_kept"),
    "test_stop_no_resume": _rule("stop_no_resume"),
    "test_own_pid": _rule("own_pid"),
    "test_take_first": _rule("take_first"),
    "test_folder_release": _rule("folder_release"),
    "test_release_end": _rule("release_end"),
    "test_keep_lock": _rule("keep_lock_one_step"),
    "test_canmain_check": _rule("canmain_check"),
}


def main(argv):
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    tests = [t for t in TESTS if not names or t.__name__ in names]
    bad = 0
    if can_fail:
        print("-- control: every test, unmutated", flush=True)
        control_failed = []
        for t in tests:
            try:
                t()
                outcome = None
            except Exception as e:
                outcome = "%s: %s" % (type(e).__name__, str(e)[:300])
            print("%s  %-24s %s" % ("ok  " if outcome is None else "FAIL",
                                    t.__name__, outcome or ""), flush=True)
            if outcome is not None:
                control_failed.append(t.__name__)
        if control_failed:
            print("\nREFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing; no mutant is counted" % ", ".join(control_failed))
            return 1
        print("-- mutants", flush=True)
    for t in tests:
        if can_fail:
            on, off = MUTANTS[t.__name__]
            on()
        try:
            t()
            outcome = None
        except Exception as e:
            outcome = "%s: %s" % (type(e).__name__, str(e)[:300])
        finally:
            if can_fail:
                off()
        if can_fail:
            ok = outcome is not None
            print("%s  %-24s with its rule removed: %s" % (
                "ok  " if ok else "FAIL", t.__name__,
                ("fails (" + outcome[:110] + ")") if ok else
                "STILL PASSES -- the test checks nothing"))
        else:
            ok = outcome is None
            print("%s  %-24s %s" % ("ok  " if ok else "FAIL", t.__name__,
                                    "" if ok else outcome))
        bad += 0 if ok else 1
    print("\n%d of %d %s" % (len(tests) - bad, len(tests),
                             "mutants caught; the control passes" if can_fail
                             else "passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
