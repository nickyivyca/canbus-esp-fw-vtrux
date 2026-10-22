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
  pycache  no __pycache__ in charge-interposer/ or shared/bench/ makes the
           clone dirty (a dirty clone cannot be resumed)

    py -3.14 charge-interposer/bench_suite_test.py [--can-fail]

--can-fail runs each test against a mutant of what it tests (a judge that
passes everything, a suite with its requires dropped, ...) and requires the
test to fail; exit 0 only if every mutant is caught.
"""

import os
import re
import shutil
import subprocess
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
    noread = ("noread_ack_01: premise NOT met\n"
              "  VOID noread_ack_01: VOID: no hole in the board's t= sequence\n"
              "  VOID noread_ack_02: VOID: no hole in the board's t= sequence\n")
    assert _v(j(_run(noread, 0))) == [Verdict.VOID], \
        "rc 0 with every verdict VOID is VOID, not PASS (a25e noread-boots)"
    assert _v(j(_run(noread, 3))) == [Verdict.VOID], \
        "bench_usb_host's rc 3 for an all-VOID case is VOID"
    assert _v(j(_run(noread + "  PASS noread_ack_03: boot window 12209 us\n",
                     0))) == [Verdict.PASS], "a VOID beside a PASS stays PASS"


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
            "x2_mac": "02:00:00:00:00:01",
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
    # 171: vport-listen-wrong-rate split out of vport-listen (2026-10-21)
    # 177: st.bench-topology and the five cf.* can-fails (2026-10-22)
    assert len(seen) == 177, "checks across the suites: %d" % len(seen)
    # build-check runs in a git checkout of HEAD, shipped as a bundle
    # (user ruling 2026-10-19), never in the shipped copy
    mb = [g for g in su["emulated"].groups if g.name == "m-build"][0]
    bc = [c for c in mb.checks if c.id == "build-check"][0]
    assert bc.argv[0] == "firmware/test/build_check_in_clone.py", bc.argv
    head = subprocess.run(["git", "-C", os.path.dirname(HERE), "rev-parse",
                           "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    a = bc.argv
    assert a[a.index("--commit") + 1] == head, (a, head)
    assert a[a.index("--bundle") + 1] == "{work}/gitb/repo.bundle", a
    assert a[a.index("--shipped") + 1] == "{work}/code", a
    bundle = os.path.join(mb.ship["gitb"], "repo.bundle")
    r = subprocess.run(["git", "-C", os.path.dirname(HERE), "bundle",
                        "list-heads", bundle], capture_output=True, text=True)
    assert r.returncode == 0 and head in r.stdout, (bundle, r.stdout,
                                                    r.stderr)
    # the product digest (user 2026-10-20): emulated and bench use the
    # component table's split; the harness suite keeps the commit
    assert su["emulated"].product_files is B.product_files
    assert su["bench"].product_files is B.product_files
    assert su["harness"].product_files is None
    repo = os.path.dirname(HERE)
    pf = set(B.product_files(repo))
    for must in ("charge-interposer/machine.py",
                 "charge-interposer/firmware/platformio.ini",
                 "charge-interposer/firmware/build_identity.py",
                 "charge-interposer/firmware/src/main.cpp",
                 "charge-interposer/firmware/src/machine.h"):
        assert must in pf, must
    for never in ("run_scenario.py", "vehicle_sim.py", "charger_sim.py",
                  "interposer_sim.py", "pack.py", "protocol.py", "bus.py",
                  "replay.py", "regress.py", "bench_suite.py"):
        assert "charge-interposer/" + never not in pf, never
    assert not [p for p in pf if "/test/" in p or p.endswith(".md")], pf
    # the same files source_digest hashes, plus machine.py
    import build_identity as BI
    fw = os.path.join(repo, "charge-interposer", "firmware")
    n_src = sum(1 for d, _s, fs in os.walk(os.path.join(fw, "src"))
                for f in fs if f.endswith(BI.SRC_SUFFIXES))
    n_extra = sum(1 for x in BI.EXTRA_INPUTS
                  if os.path.exists(os.path.join(fw, x)))
    assert len(pf) == n_src + n_extra + 1, (len(pf), n_src, n_extra)
    # every X2 image names the X2's chip, so a port that reaches another
    # board writes nothing (methodology, 2026-10-20)
    for n, im in su["bench"].images.items():
        assert im.board == "x2" and (im.params or {}).get("chip") == {
            "type": "ESP32-S3", "mac": s["x2_mac"]}, (n, im.params)
    # the public repo holds no MAC: none in the suite's source
    src = open(os.path.join(HERE, "bench_suite.py"), encoding="utf-8").read()
    macs = re.findall(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b", src)
    assert not macs, "MAC literal(s) in bench_suite.py: %s" % macs
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


# The trees a run imports Python from. Bytecode written there must not make
# the clone dirty, or --resume refuses it (2026-10-20: 20d3's first resume,
# refused for shared/bench/vtrux_bench/__pycache__).
PYCACHE_TREES = ("charge-interposer", "shared/bench")
_PYCACHE_DROP = ()      # .gitignore files the check leaves out (mutant hook)


def _pycache_unignored():
    """(paths git would report, folders checked): one __pycache__/*.pyc per
    folder holding a .py file in PYCACHE_TREES, asked of a scratch repo
    holding only this repo's .gitignore files on those paths."""
    repo = os.path.dirname(HERE)
    probe = os.path.join(TMP, "pycache-probe")
    shutil.rmtree(probe, ignore_errors=True)
    os.makedirs(probe)
    subprocess.run(["git", "init", "-q", probe], check=True)
    ignores, paths = {".gitignore"}, []
    for tree in PYCACHE_TREES:
        parts = tree.split("/")
        for i in range(1, len(parts)):
            ignores.add("/".join(parts[:i] + [".gitignore"]))
        for d, dirs, files in os.walk(os.path.join(repo, *parts)):
            dirs[:] = [x for x in dirs
                       if x not in (".pio", "__pycache__", ".git")]
            rel = os.path.relpath(d, repo).replace(os.sep, "/")
            if ".gitignore" in files:
                ignores.add(rel + "/.gitignore")
            if any(f.endswith(".py") for f in files):
                paths.append(rel + "/__pycache__/m.cpython-314.pyc")
    for g in sorted(ignores):
        src = os.path.join(repo, *g.split("/"))
        if g in _PYCACHE_DROP or not os.path.isfile(src):
            continue
        dst = os.path.join(probe, *g.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    # NUL-separated: in text mode Windows would turn "\n" into "\r\n" and
    # no path would match.
    r = subprocess.run(["git", "-C", probe, "check-ignore", "--stdin", "-z"],
                       input="\0".join(paths) + "\0", capture_output=True,
                       text=True)
    if r.returncode not in (0, 1):
        raise AssertionError("git check-ignore failed: %s" % r.stderr)
    ignored = set(x for x in r.stdout.split("\0") if x)
    shutil.rmtree(probe, ignore_errors=True)
    return [p for p in paths if p not in ignored], len(paths)


def test_pycache_ignored():
    missing, n = _pycache_unignored()
    assert n >= 5, "only %d Python folders found: the walk is wrong" % n
    assert not missing, "bytecode git would report: %s" % missing[:4]


# Runs a script up to its argument parsing and no further: parse_args is
# replaced by one that parses, prints ARGV-OK and exits; a bad line exits 2
# with argparse's error. Serial, CAN and USB opens raise, so no line of the
# script can reach the bench even if it does work before parsing.
_ARGV_PROBE = r"""
import argparse, runpy, sys
def _forbidden(*a, **k):
    raise SystemExit("ARGV-PROBE: hardware opened before parse_args")
for mod, names in (("serial", ("Serial", "serial_for_url")),
                   ("can", ("Bus",)), ("can.interface", ("Bus",)),
                   ("usb.core", ("find",))):
    try:
        m = __import__(mod, fromlist=["x"])
    except Exception:
        continue
    for n in names:
        setattr(m, n, _forbidden)
_orig = argparse.ArgumentParser.parse_args
_orig_known = argparse.ArgumentParser.parse_known_args
def _done(r):
    print("ARGV-OK")
    sys.stdout.flush()
    raise SystemExit(0)
_inside = []
def _pa(self, args=None, namespace=None):
    _inside.append(1)
    _done(_orig(self, args, namespace))
def _pk(self, args=None, namespace=None):
    if _inside:
        return _orig_known(self, args, namespace)
    _done(_orig_known(self, args, namespace))
argparse.ArgumentParser.parse_args = _pa
argparse.ArgumentParser.parse_known_args = _pk
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
# Scripts the probe cannot check, because they take no arguments through
# argparse (it would run them whole): each is run with no arguments by its
# check, which test_bench_argv also asserts, and every name here must be
# one the walk met without argparse (no stale entries).
NO_ARGPARSE = {"reset_check.py", "keepalive_probe.py"}


def _artifacts():
    root = os.environ.get("REVERSE_IT_DIR")
    assert root, "REVERSE_IT_DIR is not set: the bench scripts live there"
    art = os.path.join(root, "projects", "vtrux", "notes", "artifacts",
                       "interposer-firmware")
    assert os.path.isfile(os.path.join(art, "bench_flight_silence.py")), art
    return art


def _fill(a, s, outdir):
    vals = dict(s, serial_port="COM99", pcan_channel="PCAN_USBBUS1",
                outdir=outdir, wican_state="argv probe", reverse_it=".")
    return re.sub(r"\{(\w+)\}", lambda m: str(vals.get(m.group(1), "X")), a)


def _bench_argv_problems():
    """[(check id, problem)] for every python check of the bench suite whose
    argument line its own script's parser refuses, the number probed, and
    the script names met without argparse."""
    s = _settings()
    s["interposer_artifacts"] = _artifacts()
    outdir = os.path.join(TMP, "argv-out")
    os.makedirs(outdir, exist_ok=True)
    bad, n, plain = [], 0, set()
    for g in B.bench(s).groups:
        for c in g.checks:
            if c.skip or not c.python or not c.argv:
                continue
            argv = [_fill(str(a), s, outdir) for a in c.argv]
            script = argv[0]
            name = os.path.basename(script)
            if not os.path.isfile(script):
                bad.append((c.id, "no script %s" % script))
                continue
            with open(script, encoding="utf-8", errors="replace") as f:
                src = f.read()
            if "parse_args" not in src and "parse_known_args" not in src:
                plain.add(name)
                if name not in NO_ARGPARSE:
                    bad.append((c.id, "%s has no argparse and is not listed"
                                % name))
                elif len(argv) > 1:
                    bad.append((c.id, "%s takes no arguments, given %s"
                                % (name, argv[1:])))
                continue
            n += 1
            err = _probe(argv)
            if err:
                bad.append((c.id, err))
    return bad, n, plain


def _probe(argv):
    """None if argv[0]'s parser accepts argv[1:], else the error line."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-B", "-c", _ARGV_PROBE] + argv,
                       capture_output=True, text=True, timeout=120, env=env,
                       cwd=os.path.dirname(argv[0]))
    if r.returncode == 0 and "ARGV-OK" in r.stdout:
        return None
    lines = (r.stderr + r.stdout).strip().splitlines() or ["rc %d" % r.returncode]
    return lines[-1][:160]


def _probe_controls():
    """The probe must refuse the a25e flight-silence line, accept the fixed
    one, and stop a script that opens a serial port before parsing."""
    fs = os.path.join(_artifacts(), "bench_flight_silence.py")
    bad = _probe([fs, "--port", "COM99", "--pcan", "PCAN_USBBUS1"])
    assert bad and "unrecognized arguments" in bad, \
        "the probe accepted the a25e line: %s" % bad
    good = _probe([fs, "--port", "COM99", "--pcan", "--out", "x.txt"])
    assert good is None, "the probe refused the fixed line: %s" % good
    d = os.path.join(TMP, "argv-control")
    os.makedirs(d, exist_ok=True)
    early = os.path.join(d, "opens_first.py")
    with open(early, "w") as f:
        f.write("import argparse, serial\nserial.Serial('COM99')\n"
                "argparse.ArgumentParser().parse_args()\n")
    hw = _probe([early])
    assert hw and "hardware opened before parse_args" in hw, \
        "the probe let a serial open through: %s" % hw


USB_BASELINE = {"noread-boots": "baseline-boots", "close-boots": "baseline-boots",
                "bridge": "bridge"}


def _opt(argv, name):
    argv = [str(a) for a in argv]
    return argv[argv.index(name) + 1] if name in argv else None


def test_usb_baselines():
    """Every bench_usb_host case that scores against a baseline names, by
    --baseline-dir, the folder of an EARLIER check of its group that writes
    that baseline: baseline-boots with the same --ack / --noack for the
    boot cases, bridge --com3 absent for cycles (a25e: none did)."""
    s, su = _all()
    n = 0
    for g in su["bench"].groups:
        seen = {}
        for c in g.checks:
            argv = [str(a) for a in c.argv]
            if not argv or not argv[0].endswith("bench_usb_host.py"):
                continue
            case = _opt(argv, "--case")
            mode = "--ack" if "--ack" in argv else "--noack" \
                if "--noack" in argv else _opt(argv, "--com3")
            seen[c.id] = (case, mode)
            needs = case in ("noread-boots", "close-boots") or \
                (case == "bridge" and mode == "cycles")
            bdir = _opt(argv, "--baseline-dir")
            if not needs:
                assert bdir is None, "%s needs no baseline dir" % c.id
                continue
            n += 1
            assert bdir, "%s has no --baseline-dir" % c.id
            m = re.match(r"^\{outdir\}/\.\./([\w.-]+)$", bdir)
            assert m, "%s: --baseline-dir %s is not {outdir}/../<check>" \
                % (c.id, bdir)
            src = m.group(1)
            assert src in seen, "%s reads %s, not an earlier check of %s" \
                % (c.id, src, g.name)
            want = ("baseline-boots", mode) if case != "bridge" \
                else ("bridge", "absent")
            assert seen[src] == want, "%s reads %s, which is %s, not %s" \
                % (c.id, src, seen[src], want)
    assert n == 4, "%d dependent usb-host cases found, 4 expected" % n


# The stop each bench_usb_host case needs before its premise can be met, as
# in the 2026-10-14 runs that met it (reverse-it interposer-firmware
# runs/bench_usbhost_control_20261014T0528/CODE.txt: five 240 s stops never
# filled the host side -> 480 s for the not-reading boots, 600 s for the
# bridge cycles). Only these two cases stop reading.
USB_STOPS = {("noread-boots", "--ack"): "480", ("noread-boots", "--noack"): "480",
             ("bridge", "cycles"): "600"}


def test_usb_stops():
    """Each bench_usb_host case that stops reading passes the stop the
    2026-10-14 control showed it needs (4cdb: both noread cases VOID at the
    script's 240 s floor), and no other case passes one."""
    s, su = _all()
    found = set()
    for g in su["bench"].groups:
        for c in g.checks:
            argv = [str(a) for a in c.argv]
            if not argv or not argv[0].endswith("bench_usb_host.py"):
                continue
            case = _opt(argv, "--case")
            mode = "--ack" if "--ack" in argv else "--noack" \
                if "--noack" in argv else _opt(argv, "--com3")
            want = USB_STOPS.get((case, mode))
            got = _opt(argv, "--stop-s")
            assert got == want, "%s: --stop-s %s, want %s" % (c.id, got, want)
            if want:
                found.add((case, mode))
    assert found == set(USB_STOPS), "stop cases missing: %s" % sorted(
        set(USB_STOPS) - found)


def test_script_coverage():
    """Every bench driver in reverse-it interposer-firmware with a
    --self-test is in SELF_TESTS, every script whose --can-fail runs
    script_can_fail is in CAN_FAILS, and the harness suite runs each (4cdb:
    bench_topology's self-test and all five can-fails were run by hand
    only)."""
    art = _artifacts()
    st, cf = set(), set()
    for n in sorted(os.listdir(art)):
        if not n.endswith(".py"):
            continue
        src = open(os.path.join(art, n), encoding="utf-8",
                   errors="replace").read()
        if n.startswith("bench_") and '"--self-test"' in src:
            st.add(n[:-3])
        if "script_can_fail.run(" in src and n != "script_can_fail.py":
            cf.add(n[:-3])
    assert st == set(B.SELF_TESTS), "self-tests: found %s, listed %s" % (
        sorted(st ^ set(B.SELF_TESTS)), "SELF_TESTS")
    assert cf == set(B.CAN_FAILS), "can-fails: found %s, listed %s" % (
        sorted(cf ^ set(B.CAN_FAILS)), "CAN_FAILS")
    s, su = _all()
    runs = set()
    for g in su["harness"].groups:
        for c in g.checks:
            argv = [str(a) for a in c.argv]
            if c.skip or not argv or not argv[0].endswith(".py"):
                continue
            name = os.path.basename(argv[0])[:-3]
            runs |= {(name, m) for m in ("--self-test", "--can-fail")
                     if m in argv[1:]}
    for n in st:
        assert (n, "--self-test") in runs, "harness never runs %s --self-test" % n
    for n in cf:
        assert (n, "--can-fail") in runs, "harness never runs %s --can-fail" % n


def test_reopen_skip():
    """innomaker-rate-runs-reopen stays listed with its argument line, and is
    a skip naming the 2026-10-14 harness defect, so it shows NOT-RUN with
    that reference (reviewer, 2026-10-22; a25e and 4cdb crashed on it)."""
    s, su = _all()
    c = next(c for g in su["bench"].groups for c in g.checks
             if c.id == "innomaker-rate-runs-reopen")
    argv = [str(a) for a in c.argv]
    assert argv[0].endswith("bench_innomaker_clock.py") and \
        _opt(argv, "--between") == "reopen", "the reopen line is gone"
    assert c.skip and "Step 4 no-hands sheet item 1" in c.skip and \
        "Access denied" in c.skip, "not a skip naming the defect: %r" % c.skip


def test_vport_split():
    """vport-listen's two wrong-rate arms run as their own check, which is a
    registered KNOWN FAIL (reviewer, 2026-10-21); the rest stay judged in
    vport-listen; between them every listen arm runs exactly once."""
    sys.path.insert(0, _artifacts())
    import bench_vport as V
    s, su = _all()
    checks = {c.id: c for g in su["bench"].groups for c in g.checks}
    arms = {}
    for cid in ("vport-listen", "vport-listen-wrong-rate"):
        c = checks[cid]
        assert _opt(c.argv, "--steps") == "listen", cid
        arms[cid] = V.listen_arm_keys(_opt(c.argv, "--listen-arms") or "all")
    assert arms["vport-listen-wrong-rate"] == ["wrong-250k", "wrong-1m"]
    assert not set(arms["vport-listen"]) & {"wrong-250k", "wrong-1m"}, \
        "a known-fail arm left in the judged check"
    allarms = arms["vport-listen"] + arms["vport-listen-wrong-rate"]
    assert sorted(allarms) == sorted(V.LISTEN_ARMS), \
        "listen arms lost or doubled: %s" % allarms
    kf = {k.id for k in su["bench"].known_fails}
    assert "b-vport/vport-listen-wrong-rate" in kf
    assert "b-vport/vport-listen" not in kf


def test_bench_argv():
    _probe_controls()
    bad, n, plain = _bench_argv_problems()
    assert n >= 40, "only %d checks probed: the walk is wrong" % n
    assert plain == NO_ARGPARSE, "scripts without argparse: met %s, listed %s" \
        % (sorted(plain), sorted(NO_ARGPARSE))
    assert not bad, "argument lines their scripts refuse: %s" % bad[:4]


TESTS = [test_script_judge, test_recorded_judge, test_scenario_judge,
         test_corrupt_pin_judge, test_c12_judge, test_host_bridge_judge,
         test_suites, test_mutant_wiring, test_identify, test_pycache_ignored,
         test_usb_baselines, test_usb_stops, test_reopen_skip,
         test_script_coverage,
         test_vport_split, test_bench_argv]


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


def _plain_build_check(s):
    """The e7d4 form: build_check.py run in the shipped copy."""
    for g in s.groups:
        for c in g.checks:
            if c.id == "build-check":
                c.argv = ["firmware/test/build_check.py"]


def _harness_files(root):
    return B.product_files(root) + ["charge-interposer/run_scenario.py"]


def _no_machine(root):
    return [p for p in B.product_files(root) if not p.endswith("machine.py")]


def _product_in_harness(s):
    s.product_files = B.product_files


def _no_chip(s):
    for im in s.images.values():
        im.params.pop("chip", None)


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
        if mod == "self":
            return sys.modules[__name__], name
        return getattr(B, mod), name
    return B, attr


_ORIG = {n: getattr(B, n) for n in ("script_judge", "recorded_judge",
                                    "scenario_judge", "c12_judge",
                                    "corrupt_pin_judge",
                                    "host_bridge_judge", "emulated", "bench",
                                    "harness", "product_files")}
_ORIG["bench_identify.PRODUCT_RE"] = B.bench_identify.PRODUCT_RE
_ORIG["bench_identify.identify"] = B.bench_identify.identify
_ORIG["self._PYCACHE_DROP"] = _PYCACHE_DROP
_ORIG["self.NO_ARGPARSE"] = NO_ARGPARSE
_ORIG["self._ARGV_PROBE"] = _ARGV_PROBE
_ORIG["SELF_TESTS"] = B.SELF_TESTS
_ORIG["CAN_FAILS"] = B.CAN_FAILS


def _drop_harness_check(cid):
    def fn(s):
        for g in s.groups:
            g.checks = [c for c in g.checks if c.id != cid]
    return fn


def _rc0_pass(run):
    """The pre-a25e judge: exit code 0 is PASS whatever was printed."""
    if run.rc == 0:
        return Result(Verdict.PASS, detail="rc=0")
    return _ORIG["script_judge"](run)


def _check(s, cid):
    return next(c for g in s.groups for c in g.checks if c.id == cid)


def _drop_baseline_dir(cid):
    def fn(s):
        c = _check(s, cid)
        i = c.argv.index("--baseline-dir")
        del c.argv[i:i + 2]
    return fn


def _baseline_from(cid, src):
    def fn(s):
        c = _check(s, cid)
        c.argv[c.argv.index("--baseline-dir") + 1] = "{outdir}/../" + src
    return fn


def _set_stop(cid, value):
    """value None drops --stop-s; otherwise sets it (adding it if absent)."""
    def fn(s):
        c = _check(s, cid)
        if "--stop-s" in c.argv:
            i = c.argv.index("--stop-s")
            del c.argv[i:i + 2]
        if value is not None:
            c.argv[1:1] = ["--stop-s", value]
    return fn


def _unskip(cid):
    def fn(s):
        _check(s, cid).skip = None
    return fn


def _skip_reason(cid, reason):
    def fn(s):
        _check(s, cid).skip = reason
    return fn


def _wrong_rate_back(s):
    c = _check(s, "vport-listen")
    c.argv[c.argv.index("--listen-arms") + 1] = "all"


def _arm_lost(s):
    c = _check(s, "vport-listen")
    c.argv[c.argv.index("--listen-arms") + 1] = "no-ack,idle,flood-7F4,flood-649"


def _known_fail_dropped(s):
    s.known_fails = [k for k in s.known_fails
                     if k.id != "b-vport/vport-listen-wrong-rate"]


def _flight_silence_a25e(s):
    """The a25e line: a value after the --pcan flag."""
    for g in s.groups:
        for c in g.checks:
            if c.id == "flight-silence":
                i = c.argv.index("--pcan")
                c.argv.insert(i + 1, "{pcan_channel}")

# test -> [(module attribute, replacement, what the mutant does)]
MUTANTS = {
    "test_script_judge": [
        ("script_judge", _pass_all, "passes everything"),
        ("script_judge", _rc0_pass,
         "rc 0 is PASS whatever it printed (a25e noread-boots)")],
    "test_usb_baselines": [
        ("bench", _wrap_suite("bench", _drop_baseline_dir("usb-host-close-ack")),
         "close-ack without --baseline-dir (a25e)"),
        ("bench", _wrap_suite("bench",
                              _drop_baseline_dir("usb-host-bridge-cycles")),
         "bridge-cycles without --baseline-dir (a25e)"),
        ("bench", _wrap_suite("bench", _baseline_from(
            "usb-host-noread-noack", "usb-host-baseline-ack")),
         "noread-noack reads the ACKed baseline"),
        ("bench", _wrap_suite("bench", _baseline_from(
            "usb-host-noread-ack", "usb-host-close-ack")),
         "noread-ack reads a later check's folder")],
    "test_usb_stops": [
        ("bench", _wrap_suite("bench", _set_stop("usb-host-noread-ack", None)),
         "noread-ack at the script's 240 s floor (4cdb)"),
        ("bench", _wrap_suite("bench",
                              _set_stop("usb-host-noread-noack", "240")),
         "noread-noack stops 240 s"),
        ("bench", _wrap_suite("bench",
                              _set_stop("usb-host-bridge-cycles", "480")),
         "bridge-cycles stops 480 s"),
        ("bench", _wrap_suite("bench",
                              _set_stop("usb-host-close-ack", "480")),
         "a stop on a case that never stops reading")],
    "test_script_coverage": [
        ("SELF_TESTS", [n for n in B.SELF_TESTS if n != "bench_topology"],
         "bench_topology's self-test unlisted (4cdb)"),
        ("CAN_FAILS", [n for n in B.CAN_FAILS if n != "bench_switch_loss"],
         "bench_switch_loss's can-fail unlisted"),
        ("CAN_FAILS", B.CAN_FAILS + ["bench_c12"],
         "a listed can-fail the script does not have"),
        ("harness", _wrap_suite("harness", _drop_harness_check(
            "cf.bench-usb-host")), "listed, but the harness never runs it")],
    "test_reopen_skip": [
        ("bench", _wrap_suite("bench", _unskip("innomaker-rate-runs-reopen")),
         "the reopen case runs again (a25e, 4cdb)"),
        ("bench", _wrap_suite("bench", _skip_reason(
            "innomaker-rate-runs-reopen", "reviewer: later")),
         "a skip that does not name the 2026-10-14 defect")],
    "test_vport_split": [
        ("bench", _wrap_suite("bench", _wrong_rate_back),
         "the wrong-rate arms back in vport-listen"),
        ("bench", _wrap_suite("bench", _arm_lost),
         "vport-listen loses its stall arm"),
        ("bench", _wrap_suite("bench", _known_fail_dropped),
         "the wrong-rate check not a known fail")],
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
         "a skip with no spec item or ruling"),
        ("emulated", _wrap_suite("emulated", _plain_build_check),
         "build-check in the shipped copy (e7d4)"),
        ("bench", _wrap_suite("bench", _no_chip),
         "X2 images name no chip"),
        ("product_files", _harness_files, "run_scenario.py counted as product"),
        ("product_files", _no_machine, "machine.py left out of the product"),
        ("harness", _wrap_suite("harness", _product_in_harness),
         "the harness suite gated on the product digest")],
    "test_pycache_ignored": [
        ("self._PYCACHE_DROP", ("shared/bench/.gitignore",),
         "shared/bench without its .gitignore (the 20d3 resume refusal)"),
        ("self._PYCACHE_DROP", ("charge-interposer/.gitignore",),
         "charge-interposer without its .gitignore")],
    "test_bench_argv": [
        ("bench", _wrap_suite("bench", _flight_silence_a25e),
         "flight-silence gives --pcan a value (the a25e line)"),
        ("self.NO_ARGPARSE", {"reset_check.py", "keepalive_probe.py",
                              "bench_topology.py"},
         "a stale NO_ARGPARSE entry"),
        ("self.NO_ARGPARSE", {"reset_check.py"},
         "a script without argparse left unlisted"),
        ("self._ARGV_PROBE", _ARGV_PROBE.replace(
            "_done(_orig(self, args, namespace))", "_done(None)"),
         "the probe never parses"),
        ("self._ARGV_PROBE", _ARGV_PROBE.replace(
            "        setattr(m, n, _forbidden)", "        pass"),
         "the probe leaves serial, CAN and USB open"),
        ("self._ARGV_PROBE", _ARGV_PROBE.replace(
            "    if _inside:\n        return _orig_known(self, args, namespace)\n",
            ""),
         "parse_args's inner parse_known_args ends the probe (extras unchecked)")],
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
