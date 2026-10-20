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

import json
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


# ---- fixtures -----------------------------------------------------------
class Env:
    def __init__(self, name):
        self.root = os.path.join(SCRATCH, name)
        shutil.rmtree(self.root, ignore_errors=True)
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
                                                        ["0x10000", p]]},
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
    assert tb.flashes == ["w2", "jammer", "w1", "x2app"], tb.flashes
    assert tb.identify(ims["x2app"], None, None) == "X2-APP"
    assert tb.identify(ims["w1"], None, None) == "WC-1"
    assert set(verdicts(rec).values()) == {"PASS"}, verdicts(rec)
    bs = open(os.path.join(eng.run_dir, "board_state.txt")).read()
    assert "wican:" in bs and "x2:" in bs, bs


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
    st = env.settings(wallclock_patterns=[marker, r"vtrux_bench\.run"])
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
         test_image_schedule, test_identify_error, test_identify_after,
         test_void_resume,
         test_two_boards, test_stop_pause,
         test_throttle, test_after, test_wallclock_guard, test_requires,
         test_redact_scope, test_gate, test_skip, test_timeout,
         test_resume, test_compare, test_esptool_flasher, test_remote_command]


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
    "test_image_schedule": _rule("image_schedule"),
    "test_identify_error": _rule("identify_error"),
    "test_identify_after": _rule("identify_after"),
    "test_void_resume": _patch(R.Recorder, "amend", lambda self, r: None),
    "test_two_boards": _rule("restore"),
    "test_stop_pause": _rule("stop_pause"),
    "test_throttle": _rule("throttle"),
    "test_after": _rule("after"),
    "test_wallclock_guard": _rule("wallclock_guard"),
    "test_requires": _rule("requires"),
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
