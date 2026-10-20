"""Tests the HARNESS: bench_suite.py's judges and the shape of its three
suites (tester, 2026-10-18). No hardware, no runner, no ssh.

  judges   each judge on synthetic output in the format its script prints
           (taken from the 2026-10-18 trial runs), driven to every verdict
           it can give
  suites   emulated / bench / harness validate; bench requires emulated;
           no hardware or lock outside bench; no harness test (mutation,
           can-fail, self-test) in emulated or bench; no wall-clock check
           in harness; no check id in two suites; every known FAIL names a
           check of its own suite; every skip names its spec item or ruling

    py -3.14 charge-interposer/bench_suite_test.py [--can-fail]

--can-fail runs each test against a mutant of what it tests (a judge that
passes everything, a suite with its requires dropped, ...) and requires the
test to fail; exit 0 only if every mutant is caught.
"""

import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bench_suite as B                                   # noqa: E402
from vtrux_bench.run import Result, Verdict               # noqa: E402
from vtrux_bench.run.model import CheckRun, validate      # noqa: E402

TMP = os.path.join(tempfile.gettempdir(), "interposer-tester",
                   "bench-suite-test")


class _Chk:
    def __init__(self, cid):
        self.id = cid


def _run(log, rc=0, cid="c", files=None, ctx=None):
    """A CheckRun with `log` as its output and `files` in its outdir."""
    d = os.path.join(TMP, "run_%d" % _run.n)
    _run.n += 1
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    lp = os.path.join(d, "check.log")
    with open(lp, "w", encoding="utf-8") as f:
        f.write(log)
    for name, text in (files or {}).items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(text)
    return CheckRun(check=_Chk(cid), full_id="g/" + cid, rc=rc,
                    timed_out=False, log_path=lp, outdir=d, duration_s=1.0,
                    ctx=ctx or {})


_run.n = 0


def _v(r):
    rs = r if isinstance(r, list) else [r]
    return [x.verdict for x in rs]


def _by(rs):
    return {x.sub: x.verdict for x in rs}


# ---- judges -------------------------------------------------------------
def test_script_judge():
    j = B.script_judge
    assert _v(j(_run("all ok\n", 0))) == [Verdict.PASS]
    assert _v(j(_run("FAIL  the thing\n", 1))) == [Verdict.FAIL]
    assert _v(j(_run("VOID  premise not met\n", 1))) == [Verdict.VOID]
    assert _v(j(_run("VOID  a\nFAIL  b\n", 1))) == [Verdict.FAIL], \
        "a FAIL beside a VOID is a FAIL"
    assert _v(j(_run("", 3))) == [Verdict.FAIL], "rc 3, no lines: FAIL"
    assert _v(j(_run("", None))) == [Verdict.ERROR], "killed: ERROR"


def test_recorded_judge():
    j = B.recorded_judge
    r = j(_run("measured 12\n", 0))
    assert _v(r) == [Verdict.PASS] and "recorded" in r.detail
    assert _v(j(_run("FAIL  x\n", 1))) == [Verdict.FAIL]


def test_scenario_judge():
    j = B.scenario_judge
    assert _v(j(_run("...\n  -> PASS\n", 0))) == [Verdict.PASS]
    assert _v(j(_run("  -> PASS\n", 1))) == [Verdict.FAIL], \
        "-> PASS with a non-zero exit is not a pass"
    assert _v(j(_run("FAIL  x\n  -> FAIL\n", 1))) == [Verdict.FAIL]
    assert _v(j(_run("  -> FAIL\n  -> PASS\n", 0))) == [Verdict.PASS], \
        "the LAST verdict line counts"
    assert _v(j(_run("  -> PASS\n  -> FAIL\n", 0))) == [Verdict.FAIL]
    assert _v(j(_run("no verdict\n", 0))) == [Verdict.ERROR]


PIN_OK = (
    "unit_monitor_6h05: 525609 steps\n"
    "  FAIL  trace and golden do NOT match their pins\n"
    "unit_override_6h05: 525610 steps\n"
    "  FAIL  trace and golden do NOT match their pins\n"
    "  ok    unit_monitor_6h05 on machine.cpp: IDENTICAL\n"
    "  ok    unit_override_6h05 on machine.cpp: IDENTICAL\n\n2 FAILED\n")


def test_corrupt_pin_judge():
    j = B.corrupt_pin_judge
    assert _v(j(_run(PIN_OK, 1))) == [Verdict.PASS]
    assert _v(j(_run(PIN_OK.replace("  FAIL  trace and golden do NOT match",
                                    "  ok    trace and golden match", 1), 1))) \
        == [Verdict.FAIL], "one corrupted pin not caught"
    assert _v(j(_run(PIN_OK.replace("FAIL  trace and golden do NOT match",
                                    "ok    trace and golden match"), 0))) \
        == [Verdict.FAIL], "neither caught, exit 0"
    assert _v(j(_run(PIN_OK.replace("on machine.cpp: IDENTICAL\n\n",
                                    "on machine.cpp: IDENTICAL\n"
                                    "  FAIL  unit_override_6h05 on machine.cpp:"
                                    " DIFFERS\n\n"), 1))) == [Verdict.FAIL], \
        "a replay that differs as well"
    assert _v(j(_run(PIN_OK, None))) == [Verdict.ERROR]


C12_HEAD = ("C1-reset  CANSTAT  0x80\n\n"
            "--- against the fake (mcp2515_conformance_fake.txt): %d line(s) "
            "differ ---\n")
C12_LINES = [
    "  C4-txflags-on-request    TXB0CTRL.dirtied         fake 0x70       "
    "silicon 0x00       ASSUMED",
    "  C9-no-ack                TEC.at-2ms               fake 64         "
    "silicon (absent)   DEFERRED",
    "  C9-no-ack                wire.delivered           fake (none)     "
    "silicon (absent)   DATASHEET  <-- a DATASHEET line",
]
C12_BAD = ("  C3-loopback              wire.delivered           fake 1          "
           "silicon 0          DATASHEET  <-- a DATASHEET line")


def test_c12_judge():
    j = B.c12_judge
    ok = C12_HEAD % 3 + "\n".join(C12_LINES) + "\n"
    r = j(_run("", 1, cid="c12-all", files={"c12-all.txt": ok}))
    assert _v(r) == [Verdict.PASS], r
    assert r.figures["lines_differ"].value == 3
    assert r.figures["datasheet_differ_outside_c9"].value == 0
    bad = C12_HEAD % 4 + "\n".join(C12_LINES + [C12_BAD]) + "\n"
    r = j(_run("", 1, cid="c12-all", files={"c12-all.txt": bad}))
    assert _v(r) == [Verdict.FAIL] and "C3-loopback" in r.detail, r
    # a DATASHEET line after the block's blank line is not in the block
    tail = ok + "\nC3-x  <-- a DATASHEET line\n"
    r = j(_run("", 1, cid="c12-all", files={"c12-all.txt": tail}))
    assert _v(r) == [Verdict.PASS], r
    r = j(_run("", 1, cid="c12-all"))
    assert _v(r) == [Verdict.ERROR], "no transcript: ERROR"
    r = j(_run("FAIL  x\n", 1, cid="c12-all",
               files={"c12-all.txt": "no block here\n"}))
    assert _v(r) == [Verdict.FAIL], "no differ block: the exit code decides"


def _code_tree(tests):
    d = os.path.join(TMP, "code")
    hb = os.path.join(d, "firmware", "test", "host_bridge")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(hb)
    for t in tests:
        open(os.path.join(hb, t + ".cpp"), "w").close()
    return d


def test_host_bridge_judge():
    j = B.host_bridge_judge
    code = _code_tree(["test_l2_bridge", "test_conformance", "test_event_layout",
                       "test_canport_contract"])
    l2 = ["--- l2: c%d ---\ntest_l2_bridge[c%d]: all checks OK" % (i, i)
          for i in range(33)]
    log = "\n".join([
        "capture pin: l3_stimulus.txt matches its pin",
        "test_canport_contract: all checks OK",
        "test_conformance: all checks OK",
        "test_event_layout DID NOT BUILD"] + l2 + [
        "--- l2: bad ---",
        "  ok  setup",
        "FAIL  it clears bridge_ok (spec 8.2)",
        "1 failure(s) in bad",
        "--- l2: hung ---",
        "  ok  setup"]) + "\n"
    rs = _by(j(_run(log, 1, ctx={"interposer_code": code})))
    assert rs["capture-pin"] == Verdict.PASS
    assert rs["l2.c0"] == rs["l2.c32"] == Verdict.PASS
    assert rs["l2.bad"] == Verdict.FAIL
    assert rs["l2.hung"] == Verdict.ERROR, "opened, never reported: ERROR"
    assert rs["test_conformance"] == Verdict.PASS
    assert rs["test_canport_contract"] == Verdict.PASS
    assert rs["test_event_layout"] == Verdict.ERROR
    assert "test_l2_bridge" not in rs
    assert "l2.population" not in rs, "35 cases opened: no population FAIL"
    # a test with no line at all, a missing pin, too few L2 cases
    code = _code_tree(["test_l2_bridge", "test_conformance", "test_new"])
    log2 = "test_conformance: all checks OK\n" + "\n".join(l2[:5]) + "\n"
    rs = _by(j(_run(log2, 1, ctx={"interposer_code": code})))
    assert rs["test_new"] == Verdict.FAIL, "silent test: FAIL"
    assert rs["capture-pin"] == Verdict.FAIL
    assert rs["l2.population"] == Verdict.FAIL


# ---- suites -------------------------------------------------------------
def _settings():
    """Enough settings to build all three suites, from this clone: the
    manifest's 36edf3e55552 rows for the Arduino images (their bytes are
    checked only at flash time) and stand-in ELFs for the IDF images."""
    d = os.path.join(TMP, "settings")
    shutil.rmtree(d, ignore_errors=True)
    art = os.path.join(d, "art")
    os.makedirs(art)
    open(os.path.join(art, "x_mutations.sh"), "w").close()
    for name in ("c12_bench", "vport_bench"):
        os.makedirs(os.path.join(d, "images", "idf", name))
        with open(os.path.join(d, "images", "idf", name, name + ".elf"),
                  "wb") as f:
            f.write(name.encode())
    return {"interposer_code": HERE, "interposer_artifacts": art,
            "images_dir": os.path.join(d, "images"),
            "images_src": "36edf3e55552", "boot_app0": "boot_app0.bin",
            "hosts": {"local": {}, "madhouse": {}}}


def _all():
    s = _settings()
    return s, {"emulated": B.emulated(s), "bench": B.bench(s),
               "harness": B.harness(s)}


HARNESS_MARKS = ("mutation", "--can-fail", "--self-test", "canfail",
                 "--guard-test", "--corrupt-pin")
# A harness check that needs the board cannot run without the lock, so it
# stays in the bench suite, named here.
BENCH_HARNESS = {"b-bridge/mcp-reset-canfail"}


def _argv(c):
    return " ".join(str(a) for a in c.argv)


def test_suites():
    s, su = _all()
    for k, x in su.items():
        validate(x, s, builtin_flashers=("esptool",))
    assert su["emulated"].name == "charge-interposer-emulated"
    assert su["bench"].requires == su["emulated"].name, \
        "the bench must require the emulated suite"
    assert su["emulated"].requires is None and su["harness"].requires is None
    for k in ("emulated", "harness"):
        assert not su[k].images, "%s flashes nothing" % k
        assert not any(g.needs_lock for g in su[k].groups), \
            "%s takes no lock" % k
    for g in su["bench"].groups:
        live = [c for c in g.checks if not c.skip]
        assert g.needs_lock or not live, \
            "bench group %s runs checks without the lock" % g.name
    for k in ("emulated", "bench"):
        for g in su[k].groups:
            for c in g.checks:
                if c.skip:
                    continue
                hit = [m for m in HARNESS_MARKS if m in _argv(c)]
                assert not hit or (k == "bench" and "%s/%s" % (
                    g.name, c.id) in BENCH_HARNESS), \
                    "%s/%s/%s is a harness test (%s)" % (k, g.name, c.id, hit)
    for g in su["harness"].groups:
        for c in g.checks:
            assert c.cls != "wallclock", \
                "harness check %s/%s is wall-clock" % (g.name, c.id)
    seen = {}
    for k, x in su.items():
        for g in x.groups:
            for c in g.checks:
                key = _argv(c) if not c.skip else "skip:" + c.id
                assert key not in seen, "%s in %s and %s" % (key, seen.get(key), k)
                seen[key] = k
        ids = {"%s/%s" % (g.name, c.id) for g in x.groups for c in g.checks}
        for kf in x.known_fails:
            top = "/".join(kf.id.split("/")[:2])
            assert top in ids, "%s: known FAIL %s names no check" % (k, kf.id)
        for g in x.groups:
            for c in g.checks:
                if c.skip:
                    assert any(w in c.skip for w in ("spec", "reviewer",
                                                     "plan", "ruling")), \
                        "skip %s/%s names no spec item or ruling" % (g.name, c.id)
    assert len(seen) == 166, "checks across the suites: %d" % len(seen)
    e_sims = [c for g in su["emulated"].groups for c in g.checks
              if c.id.startswith("sim.")]
    assert len(e_sims) == 27 and all("--time-scale 1 " in _argv(c) + " "
                                     for c in e_sims), "27 sims at 1x"


def test_mutant_wiring():
    """The corrupt-pin check uses its judge; every other mutant script
    the exit-code judge."""
    s, su = _all()
    mut = {c.id: c for g in su["harness"].groups if g.name == "m-mutants"
           for c in g.checks}
    assert mut["monitor-6h-diff-corrupt-pin"].judge is B.corrupt_pin_judge
    assert all(c.judge is B.script_judge for k, c in mut.items()
               if k != "monitor-6h-diff-corrupt-pin")


# The two HELLO? replies the bridge image gave in the 00:30 trial
# (runs/charge-interposer_20261018T003009_7077/_bench/b-bridge/).
REPLY_DIAG = ("=0 ovf=0 txf=0 silent=0 fw=9 schema=4\ndiag st=PASSTHROUGH "
              "trip=0 flags=0xc0 ilim=- vmax=0 chgmax=0 soc=0.0 evap=0 chg=0 "
              "mod=0 synth=0 ovf=0 txf=0 silent=0 fw=9 schema=4\n")
REPLY_LOSS = (" boot=61522us switch=300/300us(last/max) drained=0\n  LOSS "
              "veh(rx=0 tx=0 err=184 off=0 peak=0) chg(rx=0 tx=0 err=0 off=0 "
              "ring=0/0) bridge=0 emit=0\n       bridged frames that missed "
              "the vehicle: 0 (clears bridge_ok for that interval)\n")


def test_identify():
    """bench_identify: the product's serial lines are recognised on any
    line, and a product image is never sent the SLCAN probe."""
    I = B.bench_identify
    for name, text in (("diag reply", REPLY_DIAG), ("loss reply", REPLY_LOSS),
                       ("diag line not first", "x\ndiag st=MONITOR trip=0\n")):
        assert I.PRODUCT_RE.search(text), "%s not seen as product" % name
    for text in ("@HELLO c12_bench version=1 elf_sha256=" + "a" * 64 + "\n",
                 "V0101\rNF001\r", "JAMMER ERR unknown_command\n",
                 "INFO mcp2515 ready\n"):
        assert not I.PRODUCT_RE.search(text), "%r seen as product" % text
    calls = []

    def fake(r7, rh, rs):
        def f7(settings, logdir, tag):
            calls.append("7f7")
            if isinstance(r7, Exception):
                raise r7
            return r7

        def fh(settings, logdir, tag):
            calls.append("hello")
            return rh if rh is not None else (None, False)

        def fs(settings, logdir, tag):
            calls.append("slcan")
            return rs
        return f7, fh, fs

    saved = (I.read_7f7, I.read_hello, I.read_slcan)
    try:
        cases = [
            (("756d6714", False), None, None, "756d6714", ["7f7"]),
            ((None, True), None, None, "product-without-0x7F7", ["7f7"]),
            ((None, False), (None, True), None, "product-without-0x7F7",
             ["7f7", "hello"]),
            ((None, False), ("c12_bench:x", False), None, "c12_bench:x",
             ["7f7", "hello"]),
            ((None, False), (None, False), "slcan:V0101:NF001",
             "slcan:V0101:NF001", ["7f7", "hello", "slcan"]),
        ]
        img = B.Image("x", "esptool")
        for r7, rh, rs, want, seq in cases:
            del calls[:]
            I.read_7f7, I.read_hello, I.read_slcan = fake(r7, rh, rs)
            got = I.identify(img, {}, TMP, {})
            assert got == want and calls == seq, (r7, rh, got, calls)
        del calls[:]
        I.read_7f7, I.read_hello, I.read_slcan = fake(
            I.IdentifyError("no capture"), None, None)
        try:
            I.identify(img, {}, TMP, {})
        except I.IdentifyError:
            pass
        else:
            raise AssertionError("a failed boot capture did not raise")
        assert calls == ["7f7"], calls
    finally:
        I.read_7f7, I.read_hello, I.read_slcan = saved


TESTS = [test_script_judge, test_recorded_judge, test_scenario_judge,
         test_corrupt_pin_judge, test_c12_judge, test_host_bridge_judge,
         test_suites, test_mutant_wiring, test_identify]


# ---- can-fail -----------------------------------------------------------
def _pass_all(run):
    return Result(Verdict.PASS, detail="mutant")


def _hb_unreported_pass(run):
    out = _ORIG["host_bridge_judge"](run)
    return [Result(Verdict.PASS, x.sub, detail=x.detail)
            if x.verdict == Verdict.ERROR else x for x in out]


def _scen_ignores_rc(run):
    r = _ORIG["scenario_judge"](run)
    if r.verdict == Verdict.FAIL and "-> PASS" in r.detail:
        r.verdict = Verdict.PASS
    return r


def _c12_counts_c9(run):
    import re
    out = os.path.join(run.outdir, "%s.txt" % run.check.id)
    if not os.path.exists(out):
        return Result(Verdict.ERROR)
    text = open(out, encoding="utf-8").read()
    if re.search(r"<-- a DATASHEET line", text):
        return Result(Verdict.FAIL, detail="any DATASHEET line")
    return Result(Verdict.PASS)


def _wrap_suite(name, fn):
    def factory(settings):
        s = _ORIG[name](settings)
        fn(s)
        return s
    return factory


def _drop_requires(s):
    s.requires = None


def _sim_into_harness(s):
    s.groups[0].checks.append(B.Check("sim.extra", ["run_scenario.py", "x"],
                                      python=True, cls="wallclock"))


def _mutant_into_emulated(s):
    s.groups[0].checks.append(B.Check("x-mutations", ["bash", "x_mutations.sh"]))


def _bare_skip(s):
    s.groups[-1].checks.append(B.Check("later", ["-"], skip="later"))


def _plain_judges(s):
    for g in s.groups:
        for c in g.checks:
            if c.judge is _ORIG["corrupt_pin_judge"]:
                c.judge = B.script_judge


def _identify_ignores_product(image, ctx, logdir, settings):
    I = B.bench_identify
    got, _p = I.read_7f7(settings, logdir, "t")
    if got is not None:
        return got
    got, _p = I.read_hello(settings, logdir, "t")
    if got is not None:
        return got
    return I.read_slcan(settings, logdir, "t")


import re as _re                                          # noqa: E402
_OLD_PRODUCT_RE = _re.compile(r"serial>.*\b(MODE|spec 2\.3)|^diag st=|"
                              r"\bfw=\d+ schema=\d+")
_ORIG_SCRIPT = B.script_judge
def _target(attr):
    """(module, name) for a mutant attribute, "mod.name" or a name in
    bench_suite."""
    if "." in attr:
        mod, name = attr.split(".", 1)
        return getattr(B, mod), name
    return B, attr


_ORIG = {n: getattr(B, n) for n in ("script_judge", "recorded_judge",
                                    "scenario_judge", "c12_judge",
                                    "corrupt_pin_judge",
                                    "host_bridge_judge", "emulated", "bench",
                                    "harness")}
_ORIG["bench_identify.PRODUCT_RE"] = B.bench_identify.PRODUCT_RE
_ORIG["bench_identify.identify"] = B.bench_identify.identify

# test -> [(module attribute, replacement, what the mutant does)]
MUTANTS = {
    "test_script_judge": [("script_judge", _pass_all, "passes everything")],
    "test_recorded_judge": [("recorded_judge", _pass_all, "passes everything")],
    "test_scenario_judge": [
        ("scenario_judge", _pass_all, "passes everything"),
        ("scenario_judge", _scen_ignores_rc, "ignores the exit code")],
    "test_corrupt_pin_judge": [
        ("corrupt_pin_judge", _pass_all, "passes everything"),
        ("corrupt_pin_judge", _ORIG_SCRIPT, "reads exit 1 as FAIL")],
    "test_mutant_wiring": [
        ("harness", _wrap_suite("harness", _plain_judges),
         "every mutant on the exit-code judge"),
        ],
    "test_identify": [
        ("bench_identify.PRODUCT_RE", _OLD_PRODUCT_RE,
         "PRODUCT_RE without re.M (the 00:30 form)"),
        ("bench_identify.identify", _identify_ignores_product,
         "identify goes on to SLCAN after a product reply")],
    "test_c12_judge": [
        ("c12_judge", _pass_all, "passes everything"),
        ("c12_judge", _c12_counts_c9, "fails on C9's DATASHEET line")],
    "test_host_bridge_judge": [
        ("host_bridge_judge", _hb_unreported_pass,
         "an unreported L2 case passes")],
    "test_suites": [
        ("bench", _wrap_suite("bench", _drop_requires), "bench drops requires"),
        ("harness", _wrap_suite("harness", _sim_into_harness),
         "a wall-clock sim in harness"),
        ("emulated", _wrap_suite("emulated", _mutant_into_emulated),
         "a mutation script in emulated"),
        ("bench", _wrap_suite("bench", _bare_skip),
         "a skip with no spec item or ruling")],
}


def main():
    os.makedirs(TMP, exist_ok=True)
    can_fail = "--can-fail" in sys.argv[1:]
    bad = 0
    for t in TESTS:
        if not can_fail:
            try:
                t()
                print("ok    %s" % t.__name__)
            except Exception:
                bad += 1
                print("FAIL  %s\n%s" % (t.__name__, traceback.format_exc()[-800:]))
            continue
        for attr, repl, what in MUTANTS[t.__name__]:
            mod, name = _target(attr)
            setattr(mod, name, repl)
            try:
                t()
                bad += 1
                print("FAIL  %s survived: %s" % (t.__name__, what))
            except Exception as e:
                print("ok    %s caught: %s (%s: %s)" % (
                    t.__name__, what, type(e).__name__, str(e)[:80]))
            finally:
                setattr(mod, name, _ORIG[attr])
    n = sum(len(v) for v in MUTANTS.values()) if can_fail else len(TESTS)
    print("\n%d of %d %s" % (n - bad, n, "mutants caught" if can_fail
                              else "passed"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
