"""The chain runner's own tests (the harness tests the harness). No hardware.

    python -B shared/bench/tests/test_run_core.py            every test
    python -B shared/bench/tests/test_run_core.py --can-fail  every test again
        with the rule it guards switched off (a mutant), each of which must
        FAIL -- a test that still passes with its rule removed checks nothing
    python -B shared/bench/tests/test_run_core.py NAME...     some tests

Each test builds a small suite of fake checks (python -c one-liners), a
fake bench-holder.md and, where flashing is involved, fake_esptool.py
standing in for esptool. Scratch goes to the machine temp folder
(<tempdir>/vtrux-bench-tests/), never into a synced folder.
"""

import glob
import json
import re
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vtrux_bench.run import engine as E            # noqa: E402
from vtrux_bench.run import record as R            # noqa: E402
from vtrux_bench.run import procs as P             # noqa: E402
from vtrux_bench.run import model as M             # noqa: E402
from vtrux_bench.run.lock import LockError         # noqa: E402
from vtrux_bench.run.model import (Check, Figure, Group, Image, KnownFail,  # noqa: E402
                                   Result, Suite, SuiteError, Tol, Verdict)

PY = sys.executable
SCRATCH = os.path.join(tempfile.gettempdir(), "vtrux-bench-tests")
ROLE = "tester (test-suite)"
# fake_esptool's chip: a locally administered MAC, no real board's
FAKE_CHIP = {"type": "ESP32-S3", "mac": "02:00:00:00:00:01"}


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
                 esptool={"argv": [PY, os.path.join(HERE, "fake_esptool.py")],
                          "port": "FAKE0", "baud": 115200},
                 pause_poll_s=0.2, scratch=self.root)
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
    return Suite(name=kw.pop("name", "t"), role=ROLE, groups=groups, **kw)


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
               Group("logic", [chk("free", "pass")])])
    eng, rec = run(env, s)
    v = verdicts(rec)
    assert v["b1/steal"] == "PASS" and v["b2/after"] == "NOT-RUN", v
    assert v["logic/free"] == "PASS", v
    # a logic-only run needs no lock at all
    env.set_holder("-- (free)")
    run(env, suite([Group("logic", [chk("free", "pass")])]))


def test_flash_gate():
    """Auto-reset check fails before the flash: nothing is written, the
    group is NOT-RUN, the rest of the bench stops, and no restore flash."""
    env = Env("flashgate")
    os.environ["FAKE_ESPTOOL_MODE"] = "chipid-fail"
    a, b = env.image("A", "IMG-A"), env.image("B", "IMG-B")
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="B"),
               Group("g2", [chk("y", "pass")], needs_lock=True, image="A")],
              images={"A": a, "B": b}, standing_image="A",
              identify=env.identify)
    eng, rec = run(env, s)
    calls = env.esptool_calls()
    assert not any("write-flash" in c for c in calls), calls
    v = verdicts(rec)
    assert v == {"g1/x": "NOT-RUN", "g2/y": "NOT-RUN"}, v
    assert len([c for c in calls if "chip-id" in c]) == 1, calls
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
    assert len([c for c in calls if "write-flash" in c]) == 1, calls
    assert verdicts(rec) == {"g1/x": "NOT-RUN", "g2/y": "NOT-RUN"}


def test_chip_mismatch():
    """A chip-id that reads another chip type or MAC than the image names
    writes nothing, also at the restore, and the board's NOT ON STANDING
    IMAGE line names the mismatch (methodology, 2026-10-20)."""
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
                  images={"A": a, "B": b}, standing_image="A",
                  identify=env.identify)
        eng, rec = run(env, s)
        calls = env.esptool_calls()
        assert not any("write-flash" in c for c in calls), (name, calls)
        assert verdicts(rec) == {"g1/x": "NOT-RUN", "g2/y": "NOT-RUN"}, verdicts(rec)
        off = rec["not_on_standing"]
        assert len(off) == 1 and "chip-id mismatch" in off[0], (name, off)
    # the same check passes on the right chip (the control)
    env = Env("chipok")
    a = env.image("A", "IMG-A")
    a.params["chip"] = dict(FAKE_CHIP)
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="A")],
              images={"A": a}, standing_image="A", identify=env.identify)
    eng, rec = run(env, s)
    assert verdicts(rec) == {"g1/x": "PASS"}, verdicts(rec)
    assert len([c for c in env.esptool_calls() if "write-flash" in c]) == 1


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
                  images={"A": a}, standing_image="A", identify=env.identify)
        eng, rec = run(env, s)
        assert not env.esptool_calls(), (name, env.esptool_calls())
        assert verdicts(rec) == {"g1/x": "NOT-RUN"}, verdicts(rec)
        ev = [e for e in rec["events"] if e["kind"] == "flash"]
        assert ev and ev[0]["phase"] == "chip-unnamed" and "names no chip" in \
            ev[0]["detail"], (name, ev)


def test_image_schedule():
    """Flash only when the board is not already on the image; restore the
    standing image at the end."""
    env = Env("schedule")
    a, b = env.image("A", "IMG-A"), env.image("B", "IMG-B")
    with open(env.board, "w") as f:
        f.write("IMG-A")
    s = suite([Group("g1", [chk("x", "pass")], needs_lock=True, image="A"),
               Group("g2", [chk("y", "pass")], needs_lock=True, image="A"),
               Group("g3", [chk("z", "pass")], needs_lock=True, image="B")],
              images={"A": a, "B": b}, standing_image="A",
              identify=env.identify)
    eng, rec = run(env, s)
    writes = [c for c in env.esptool_calls() if "write-flash" in c]
    assert len(writes) == 2, writes          # to B for g3, back to A
    assert writes[0].endswith("B.bin") and writes[1].endswith("A.bin"), writes
    assert open(env.board).read() == "IMG-A"
    assert set(verdicts(rec).values()) == {"PASS"}
    assert any(g["name"] == "restore" and g["status"].startswith("restored")
               for g in rec["groups"]), rec["groups"]
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
    assert not [c for c in env.esptool_calls() if "write-flash" in c], \
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
        self.set(image.board, image.expect)
        return dict(ok=True, phase="done", detail="fake", recoverable=True)

    def identify(self, image, ctx, logdir):
        p = self.path(image.board)
        return open(p).read() if os.path.exists(p) else None


def test_two_boards():
    """Two boards: each flashed only when its image changes, and each put
    back on its own standing image at the end."""
    env = Env("twoboards")
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
              standing_images={"x2": "x2app", "wican": "w1"})
    eng, rec = run(env, s)
    # at the start each board is put on its first image (x2: jammer),
    # then flashed only on change, then restored
    assert tb.flashes == ["jammer", "w2", "w1", "x2app"], tb.flashes
    assert tb.identify(ims["x2app"], None, None) == "X2-APP"
    assert tb.identify(ims["w1"], None, None) == "WC-1"
    assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)
    bs = open(os.path.join(eng.run_dir, "board_state.txt")).read()
    assert "wican:" in bs and "x2:" in bs, bs
    assert "NOT ON STANDING" not in bs and not rec["not_on_standing"], bs


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
              images=ims, flashers={"fake": tb.flasher}, identify=tb.identify,
              standing_images={"x2": "x2app", "wican": "w1"})
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


class GarbledJammer(TwoBoards):
    """Flashing `bad` leaves the board reading something no identify
    knows; with garble_restore the standing image reads wrong too."""
    def __init__(self, env, bad, garble_restore=False):
        TwoBoards.__init__(self, env)
        self.bad = set(bad)
        self.garble_restore = garble_restore
        self.bad_seen = False

    def flasher(self, image, params, settings, logdir):
        self.flashes.append(image.name)
        garble = image.name in self.bad or (
            self.garble_restore and self.bad_seen)
        self.bad_seen = self.bad_seen or image.name in self.bad
        self.set(image.board, "VN-garbage" if garble else image.expect)
        return dict(ok=True, phase="done", detail="fake", recoverable=True)


def _jammer_suite(tb):
    ims = {"x2app": Image("x2app", "fake", expect="X2-APP", board="x2"),
           "jammer": Image("jammer", "fake", expect="JAM", board="x2")}
    return suite([Group("a", [chk("x", "pass")], needs_lock=True,
                        image="x2app"),
                  Group("j", [chk("y", "pass")], needs_lock=True,
                        image="jammer"),
                  Group("k", [chk("z", "pass")], needs_lock=True,
                        image="x2app")],
                 images=ims, flashers={"fake": tb.flasher},
                 identify=tb.identify, standing_images={"x2": "x2app"})


def test_restore_after_stop():
    """An identify that reads wrong after a flash stops the bench, and the
    end-of-run restore is still made, once; a restore that reads wrong as
    well flashes nothing further (user 2026-10-20)."""
    env = Env("restorestop")
    tb = GarbledJammer(env, bad=["jammer"])
    tb.set("x2", "X2-APP")
    eng, rec = run(env, _jammer_suite(tb))
    v = verdicts(rec)
    assert v == {"a/x": "PASS", "j/y": "NOT-RUN", "k/z": "NOT-RUN"}, v
    assert tb.flashes == ["jammer", "x2app"], tb.flashes
    assert open(tb.path("x2")).read() == "X2-APP"
    assert not rec["not_on_standing"], rec["not_on_standing"]
    env = Env("restorestop2")
    tb = GarbledJammer(env, bad=["jammer"], garble_restore=True)
    tb.set("x2", "X2-APP")
    eng, rec = run(env, _jammer_suite(tb))
    assert tb.flashes == ["jammer", "x2app"], tb.flashes   # one restore
    assert open(tb.path("x2")).read() == "VN-garbage"


def test_standing_line():
    """A board left off its standing image opens board_state.txt, index.txt
    and the record with NOT ON STANDING IMAGE, with the board and reason."""
    env = Env("standingline")
    tb = GarbledJammer(env, bad=["jammer"], garble_restore=True)
    tb.set("x2", "X2-APP")
    eng, rec = run(env, _jammer_suite(tb))
    first = open(os.path.join(eng.run_dir, "board_state.txt")).readline()
    assert first.startswith("NOT ON STANDING IMAGE: x2 on unknown"), first
    assert "RESTORE FAILED" in first and "VN-garbage" in first, first
    idx = open(os.path.join(eng.run_dir, "index.txt")).readline()
    assert idx == first, (idx, first)
    assert rec["not_on_standing"] == [first.rstrip("\n")], rec["not_on_standing"]


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
        ims = {"x2app": Image("x2app", "fake", expect="X2-APP", board="x2"),
               "w1": Image("w1", "fake", expect="WC-1", board="wican"),
               "w2": Image("w2", "fake", expect="WC-2", board="wican")}
        groups = groups or [
            Group("a", [chk("x", "pass")], needs_lock=True, image=["w2", "x2app"]),
            Group("b", [chk("y", "pass")], needs_lock=True, image="w1")]
        return suite(groups, images=ims, flashers={"fake": self.flasher},
                     identify=self.identify, on_start=self.hook("on_start"),
                     on_end=self.hook("on_end"),
                     standing_images={"x2": "x2app", "wican": "w1"}, **kw)


def test_board_hooks():
    """on_start runs for every board before the start pass touches it,
    with the first image the run needs there; on_end runs for every board
    after the restore, with the image the run believes it is on; both are
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
    # the start pass put the WiCAN on w2, the restore put it back on w1,
    # and the end hook sees w1
    assert [x for x in tb.calls if x[0] == "on_end"] == [
        ("on_end", "wican", "w1"), ("on_end", "x2", "x2app")], tb.calls
    assert tb.calls.index(("flash", "wican", "w1")) < tb.calls.index(
        ("on_end", "wican", "w1")), tb.calls


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


def test_throttle():
    """A build-class check runs at idle priority with one job while a
    wall-clock test runs on its host (here: the bench lock is held)."""
    env = Env("throttle")
    probe = (
        "import os, sys, json\n"
        "if sys.platform == 'win32':\n"
        "    import ctypes\n"
        "    k = ctypes.windll.kernel32\n"
        "    k.GetCurrentProcess.restype = ctypes.c_void_p\n"
        "    k.GetPriorityClass.argtypes = [ctypes.c_void_p]\n"
        "    pr = k.GetPriorityClass(k.GetCurrentProcess())\n"
        "    idle = pr == 0x40\n"
        "else:\n"
        "    idle = os.getpriority(os.PRIO_PROCESS, 0) >= 19\n"
        "json.dump({{'idle': idle, 'jobs': os.environ.get('PLATFORMIO_RUN_JOBS'),"
        " 'arg': sys.argv[1]}}, open(os.path.join(sys.argv[2], 'p.json'), 'w'))\n")

    def judge(run):
        d = json.load(open(os.path.join(run.outdir, "p.json")))
        ok = d["idle"] and d["jobs"] == "1" and d["arg"] == "1"
        return Result(Verdict.PASS if ok else Verdict.FAIL, detail=repr(d))

    s = suite([Group("g", [Check("build", ["-c", probe, "{jobs}", "{outdir}"], python=True,
                                 cls="build", judge=judge)])])
    env.set_holder("tester (the-other-project)")
    eng, rec = run(env, s)
    assert verdicts(rec) == {"g/build": "PASS"}, rec["results"]
    r = rec["results"][0]
    assert r["throttled"] and "another role" in r["throttle_reason"], r
    # under its own lock, with nothing timing-sensitive running: full speed
    env.set_holder(ROLE + " (test)")
    eng, rec = run(env, s)
    assert verdicts(rec) == {"g/build": "FAIL"}, "throttled by its own lock"
    # ...but a wall-clock process on the host throttles it again
    marker = "vtrux_bench_wallclock_marker_%d" % os.getpid()
    pid = spawn_foreign(marker)
    try:
        st = env.settings(wallclock_patterns=[marker])
        eng, rec = run(env, s, settings=st)
        assert verdicts(rec) == {"g/build": "PASS"}, rec["results"]
        assert "process" in rec["results"][0]["throttle_reason"], rec["results"]
    finally:
        kill_pid(pid)
    env.set_holder("-- (free)")
    eng, rec = run(env, s)
    assert verdicts(rec) == {"g/build": "FAIL"}, "not throttled when idle"


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


def test_wallclock_guard():
    """A wall-clock check does not start while another wall-clock test
    runs on its host; the runner's own processes do not count."""
    env = Env("wcguard")
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
    pid = spawn_foreign(marker)
    try:
        eng, rec = run(env, s, settings=st)
        r = rec["results"][0]
        assert r["verdict"] == "NOT-RUN" and "another wall-clock" in r["detail"], r
    finally:
        kill_pid(pid)
    env.set_holder("tester (the-other-project)")
    eng, rec = run(env, suite([Group("g", [Check("sim", pc("pass"), python=True,
                                                 cls="wallclock")])]))
    assert verdicts(rec) == {"g/sim": "NOT-RUN"}, rec["results"]


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


def test_remote_command():
    """The remote line: cd, env, coreutils timeout, nice when throttled."""
    line = P.remote_command(["python3", "x.py", "a b"], "/w", {"K": "v w"},
                            100, True)
    for part in ("cd /w &&", "PLATFORMIO_RUN_JOBS=1", "K='v w'",
                 "timeout --kill-after=30 100", "nice -n 19", "'a b'"):
        assert part in line, (part, line)
    line = P.remote_command(["x"], None, {}, 100, False)
    assert "nice" not in line and "PLATFORMIO" not in line, line
    line = P.remote_command(["x"], "/w", {"K": "v"}, 100, False, "/o/.pid")
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


TESTS = [test_validate, test_bare_shell, test_basic_verdicts, test_never_overwrite,
         test_known_fail, test_lock_guard, test_flash_gate,
         test_chip_mismatch, test_chip_required, test_image_schedule,
         test_identify_error, test_identify_after,
         test_void_resume,
         test_two_boards, test_start_identify, test_restore_after_stop,
         test_standing_line, test_board_hooks, test_start_hook_stops,
         test_release_line, test_end_hooks_crash, test_stop_pause,
         test_throttle, test_after, test_wallclock_guard, test_requires,
         test_requires_whole, test_subset_mark, test_subset_not_whole,
         test_product_digest, test_digest_bridge,
         test_resume_code,
         test_redact_scope, test_gate, test_skip, test_timeout,
         test_resume, test_compare, test_esptool_flasher, test_remote_command,
         test_remote_stop]


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


def _reusing_run_dir(runs_root, suite_name, run_id=None):
    path = os.path.join(runs_root, run_id or "x")
    os.makedirs(path, exist_ok=True)
    for f in ("progress.jsonl", "record.json", "index.txt"):
        try:
            os.remove(os.path.join(path, f))
        except OSError:
            pass
    return run_id or "x", path


def _write_over(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


MUTANTS = {
    "test_validate": _patch(E, "validate", lambda *a, **k: None),
    "test_bare_shell": _patch(M, "RULES", {"bare_shell": False}),
    "test_basic_verdicts": _rule("no_verdict"),
    "test_never_overwrite": _both(_patch(E, "new_run_dir", _reusing_run_dir),
                                  _patch(R, "write_new", _write_over)),
    "test_known_fail": _rule("known_fail"),
    "test_lock_guard": _rule("lock_guard"),
    "test_flash_gate": _rule("flash_gate"),
    "test_chip_mismatch": _patch(
        __import__("vtrux_bench.run.flash", fromlist=["RULES"]), "RULES",
        {"chip_match": False, "chip_required": True}),
    "test_chip_required": _patch(
        __import__("vtrux_bench.run.flash", fromlist=["RULES"]), "RULES",
        {"chip_match": True, "chip_required": False}),
    "test_image_schedule": _rule("image_schedule"),
    "test_identify_error": _rule("identify_error"),
    "test_identify_after": _rule("identify_after"),
    "test_void_resume": _patch(R.Recorder, "amend", lambda self, r: None),
    "test_two_boards": _rule("restore"),
    "test_start_identify": _rule("start_identify"),
    "test_restore_after_stop": _rule("restore_after_stop"),
    "test_standing_line": _rule("standing_line"),
    "test_board_hooks": _rule("board_hooks"),
    "test_start_hook_stops": _rule("start_hook_stops"),
    "test_release_line": _rule("release_line"),
    "test_end_hooks_crash": _rule("end_crash_boards"),
    "test_stop_pause": _rule("stop_pause"),
    "test_throttle": _rule("throttle"),
    "test_after": _rule("after"),
    "test_wallclock_guard": _rule("wallclock_guard"),
    "test_requires": _rule("requires"),
    "test_requires_whole": _rule("requires_whole"),
    "test_subset_mark": _rule("subset_mark"),
    "test_subset_not_whole": _rule("subset_not_whole"),
    "test_product_digest": _rule("product_digest"),
    "test_digest_bridge": _rule("digest_bridge"),
    "test_resume_code": _rule("resume_code"),
    "test_redact_scope": _rule("redact_scope"),
    "test_gate": _rule("gate"),
    "test_skip": _rule("skip"),
    "test_timeout": _rule("timeout"),
    "test_resume": _patch(E.Engine, "_carried", lambda self: {}),
    "test_compare": _patch(R, "_within", lambda a, b, t: True),
    "test_esptool_flasher": _patch(
        __import__("vtrux_bench.run.flash", fromlist=["md5_of"]), "md5_of",
        lambda p: "0" * 32),
    "test_remote_command": _patch(P, "THROTTLE_ENV", {}),
    "test_remote_stop": _patch(P, "RULES", {"remote_stop": False}),
}


def main(argv):
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    tests = [t for t in TESTS if not names or t.__name__ in names]
    bad = 0
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
                             "mutants caught" if can_fail else "passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
