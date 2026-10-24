#!/usr/bin/env python3
"""Tests for suite_gen_inhibit.py: both suites load and validate on the pinned core,
cover the baseline index once each, keep the SSID out of what the record keeps, and
their hooks say "could not look" (raise) rather than "not this image" (None) when a
tool fails. No hardware: every tool the hooks run is stood in for.

    PYTHONPATH=<core b9896a0>/shared/bench GI_BASELINE_DIR=<.../runs/baseline_e3d9437> \
        py -3.14 -B test_suite_gen_inhibit.py

The board steps run the real wican_reboot.py from this folder's bench/ (the suite's
HARNESS, 2026-10-22) against its test's fake WiCAN (no hardware either).

Prints "ok ..." or "FAIL ..." per check and PASS or FAIL at the end; exit 0 only on
PASS. Gen-inhibit tester, 2026-10-19.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vtrux_bench.run import cli as CLI                       # noqa: E402
from vtrux_bench.run.engine import Engine, fmt               # noqa: E402
from vtrux_bench.run.model import CheckRun, validate         # noqa: E402
import suite_gen_inhibit as SG                               # noqa: E402

SSID = "WiCAN_" + "0f" * 6   # made up for the test, and built so this file has no SSID-form literal (test_publish_scan.py)
BASE = os.path.join(tempfile.gettempdir(), "gen-inhibit-suite-test", str(os.getpid()))
fails = []
oks = 0


def rmtree(path):
    """Remove a tree, including git's read-only object files (Windows), or raise.
    ignore_errors would leave the tree behind, and a later run with the same pid finds it."""
    import stat

    def again(fn, p, exc):
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    if os.path.exists(path):
        shutil.rmtree(path, onexc=again)


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:
        return e
    return None


def fake_bin(path, version, fill):
    b = bytearray(0x200)
    b[0] = 0xE9
    b[0x20:0x24] = b"\x32\x54\xcd\xab"
    b[0x30:0x30 + len(version)] = version.encode()
    b[0xB0:0xD0] = bytes([fill]) * 32
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b)
    return bytes([fill]).hex() * 32


def fixture():
    rmtree(BASE)
    os.makedirs(BASE)
    shas = {}
    for k, (name, d) in enumerate(sorted(SG.WICAN_BUILDS.items())):
        shas[name] = fake_bin(os.path.join(BASE, "images", "build-tester-" + d, "wican-fw_obd_t.bin"),
                              "e3d9437", 0x10 + k)
    shas["wican_prod"] = fake_bin(os.path.join(BASE, "prod", "wican-fw_obd_p.bin"), "e3d9437", 0x70)
    bh = os.path.join(BASE, "bench-holder.md")
    with open(bh, "w", encoding="utf-8") as f:
        f.write("# Bench holder\n\n## WiCAN\nSoftAP `%s`, page at 192.168.80.1\n\n"
                "## Lock\n\n**Holder:** -- (free)\n" % SSID)
    x2 = {"x2_app": {"files": [["0x0", os.path.join(BASE, "x2", "app.bin")]], "md5": {}, "expect": "756d6714"},
          "x2_slcan": {"files": [["0x0", os.path.join(BASE, "x2", "slcan.bin")]], "md5": {},
                       "expect": "slcan:V0101:NF001"}}
    settings = {
        "reverse_it": BASE.replace("\\", "/"), "gi_tools": HERE.replace("\\", "/"), "gi_commit": "e3d9437",
        "gi_images": os.path.join(BASE, "images"), "gi_prod_dir": os.path.join(BASE, "prod"),
        "interposer_code": os.path.join(BASE, "ic"), "x2_images": x2, "gi_fw": HERE, "gi_components": "x", "gi_checks_clone": HERE,
        "gi_host_ssh": "hosttest", "gi_host_root": "~/rt", "bash": shutil.which("bash"),
        "gi_can1_termination": "fixed passive resistors (test)",
        "bench_holder": bh, "runs_root": os.path.join(BASE, "runs"),
        "hosts": {"local": {"python": [sys.executable, "-B"]}},
        "esptool": {"argv": [sys.executable, "-c", "pass"], "port": "COM3"},
    }
    return settings, shas


def index_checks():
    d = os.environ.get("GI_BASELINE_DIR")
    if not d:
        return None
    with open(os.path.join(d, "suite_index_e3d9437.json"), encoding="utf-8") as f:
        return {c["id"]: c for c in json.load(f)["checks"]}


def covered(suite):
    """{index id: [where]}: judged ids of every check, and the ids of skipped checks."""
    out = {}
    for g in suite.groups:
        for c in g.checks:
            ids = list(getattr(c.judge, "ids", ())) or ([c.id] if c.skip else [])
            for i in ids:
                out.setdefault(i, []).append("%s/%s" % (g.name, c.id))
    return out


# ------------------------------------------------------------------ the suites

def test_suites(settings, shas):
    bench = SG.bench(settings)
    emu = SG.emulated(settings)
    eng = raises(lambda: Engine(bench, settings))
    check("bench: the core accepts it (validate)", eng is None, repr(eng))
    e2 = raises(lambda: Engine(emu, settings))
    check("emulated: the core accepts it (validate)", e2 is None, repr(e2))
    check("bench: every group holds the lock", all(g.needs_lock for g in bench.groups),
          [g.name for g in bench.groups if not g.needs_lock])
    check("bench: every group puts a WiCAN and an X2 image on",
          all(sorted(bench.images[n].board for n in g.image_list) == ["wican", "x2"] for g in bench.groups))
    check("emulated: no group holds the lock or carries an image",
          not any(g.needs_lock or g.image_list for g in emu.groups) and not emu.images)
    check("bench: standing images are _man and the X2's own",
          bench.standings() == {"wican": "wican_man", "x2": "x2_app"}, bench.standings())
    want = dict(shas)
    got = {n: im.expect for n, im in bench.images.items() if im.board == "wican"}
    check("bench: each WiCAN image expects its own bin's ELF sha", got == want, (got, want))
    check("bench: the X2 images expect what the settings name",
          bench.images["x2_app"].expect == "756d6714" and bench.images["x2_slcan"].expect == "slcan:V0101:NF001")
    for g in bench.groups:
        for c in g.checks:
            if not c.skip:
                check("bench: %s/%s declares check.log, so it is redacted" % (g.name, c.id), "check.log" in c.outputs)

    idx = index_checks()
    if idx is None:
        check("GI_BASELINE_DIR is set (the index is needed)", False, "not set")
        return bench, emu
    cb, ce = covered(bench), covered(emu)
    for cid, c in sorted(idx.items()):
        mine = cb if c["part"] == "B" else ce
        other = ce if c["part"] == "B" else cb
        check("index %s (part %s) is judged or skipped exactly once, in its own suite" % (cid, c["part"]),
              len(mine.get(cid, [])) == 1 and cid not in other, (mine.get(cid), other.get(cid)))
    extra = sorted(set(cb) | set(ce) - set(idx))
    check("no suite judges an id the index does not have", not (set(cb) | set(ce)) - set(idx), extra)
    for i, where in sorted(cb.items()):
        g = where[0].split("/")[0]
        check("bench: %s's B27-style skip only where the index says NOT-RUN" % i,
              not any(c.skip for gg in bench.groups if gg.name == g for c in gg.checks if c.id == i)
              or idx.get(i, {}).get("verdict") == "NOT-RUN")

    nn = [c for gg in bench.groups if gg.name == "man_held" for c in gg.checks if c.id == "NONODE"]
    check("bench: NONODE runs in man_held, with the X2 held", len(nn) == 1 and
          [gg.image_list for gg in bench.groups if gg.name == "man_held"] == [["wican_man", "x2_held"]])
    check("bench: NONODE takes the termination from the settings and _man's ELF sha", nn and
          nn[0].env.get("GI_TERMINATION") == "{gi_can1_termination}" and
          nn[0].env.get("GI_MAN_SHA") == bench.images["wican_man"].expect and nn[0].argv[-1] == "NONODE",
          nn and nn[0].env)
    s3 = {k: v for k, v in settings.items() if k != "gi_can1_termination"}
    check("bench: no gi_can1_termination refuses the suite", raises(lambda: SG.bench(s3)) is not None)

    rd = SG.reads(bench)
    check("reads: B13's desk step reads B13's arms and both B2 halves",
          rd.get("man_held/B13") == ["man_app/B13arms", "man_app/B2app", "man_held/B2held"], rd.get("man_held/B13"))
    check("reads: a check with no desk inputs reads nothing", rd.get("man_app/B1") == [], rd.get("man_app/B1"))
    check("reads: the eight desk checks, and only they, read folders", len([f for f, v in rd.items() if v]) == 8,
          sorted(f for f, v in rd.items() if v))

    # desk inputs: every {run_dir}/<group>/<check> names a check that runs before it
    order = []
    for g in bench.groups:
        for c in g.checks:
            fid = "%s/%s" % (g.name, c.id)
            for a in c.argv:
                if a.startswith("{run_dir}/"):
                    ref = a[len("{run_dir}/"):]
                    check("bench: %s reads %s, which runs before it" % (fid, ref), ref in order, order[-3:])
            order.append(fid)
    return bench, emu


def test_ssid(settings, bench, emu):
    ctx = {k: v for k, v in settings.items() if isinstance(v, str)}
    ctx.update(run_dir="R", run_id="I", suite="S", outdir="O", check_id="C", group="G", jobs="1")
    for s in (bench, emu):
        for g in s.groups:
            for c in g.checks:
                e = raises(lambda: [fmt(a, ctx) for a in c.argv] + [fmt(v, ctx) for v in c.env.values()]
                           + [fmt(c.cwd, ctx)])
                check("%s: %s/%s's argv, env and cwd format from the settings" % (s.name, g.name, c.id),
                      e is None, repr(e))
    raw = json.dumps([[c.argv, c.cwd] for s in (bench, emu) for g in s.groups for c in g.checks])
    home = os.path.expanduser("~").replace("\\", "/")
    check("no argv or cwd carries a local path (they come from the settings)",
          BASE.replace("\\", "/") not in raw.replace("\\\\", "/") and home not in raw.replace("\\\\", "/"))
    check("every bash check runs bash by the full path in the settings, never a bare bash",
          all(c.argv[0] == "{bash}" for s in (bench, emu) for g in s.groups for c in g.checks
              if not c.skip and c.argv[0] != "powershell"), [c.argv[0] for g in bench.groups for c in g.checks][:3])
    check("no argv names the host H runs on (gi_host_ssh does)",
          "madhouse" not in raw and "{gi_host_ssh}" in raw)
    blob = json.dumps(settings)
    check("the SSID is not in the settings (the record keeps them)", SSID not in blob)
    argvs = json.dumps([c.argv for s in (bench, emu) for g in s.groups for c in g.checks])
    check("the SSID is in no check's argv (plan prints them)", SSID not in argvs)
    ci = json.dumps(bench.code_info({}), default=str)
    check("the SSID is not in code_info", SSID not in ci)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sp = os.path.join(BASE, "settings.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(settings, f)
        rc = CLI.main(["plan", "--suite", os.path.join(HERE, "suite_gen_inhibit.py") + ":bench", "--settings", sp])
    text = out.getvalue()
    check("plan of the bench suite exits 0 and lists every group",
          rc == 0 and all("group %s " % g.name in text for g in bench.groups), (rc, text[-300:]))
    check("plan's output does not carry the SSID", SSID not in text)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = CLI.main(["plan", "--suite", os.path.join(HERE, "suite_gen_inhibit.py") + ":emulated",
                       "--settings", sp, "--group", "host"])
    check("plan of the emulated suite, one group, exits 0", rc == 0 and "group host " in out.getvalue()
          and "build_checks" not in out.getvalue(), out.getvalue()[-200:])
    bad = dict(settings, bench_holder=os.path.join(BASE, "nofile.md"))
    check("no bench-holder file: the bench suite does not load", raises(lambda: SG.bench(bad)) is not None)
    with open(os.path.join(BASE, "bh2.md"), "w", encoding="utf-8") as f:
        f.write("no SoftAP here\n")
    check("a bench-holder with no SSID: the bench suite does not load",
          raises(lambda: SG.bench(dict(settings, bench_holder=os.path.join(BASE, "bh2.md"))), ValueError) is not None)


def test_redact(settings):
    hooks = SG.make_hooks(settings, SSID)
    d = os.path.join(BASE, "redact")
    os.makedirs(d)
    paths = {}
    for n, body in (("a.txt", "join %s ok\n" % SSID), ("b.json", '{"ssid": "%s"}' % SSID),
                    ("check.log", "x %s y %s\n" % (SSID, SSID)), ("dump.bin", "bin %s\n" % SSID),
                    ("c.txt", "nothing\n")):
        paths[n] = os.path.join(d, n)
        with open(paths[n], "w", encoding="utf-8", newline="") as f:
            f.write(body)
    hooks["redact"](list(paths.values()) + [os.path.join(d, "missing.txt")])
    rd = {n: open(p, encoding="utf-8", newline="").read() for n, p in paths.items()}
    check("redact: the SSID is gone from .txt, .json and check.log",
          all(SSID not in rd[n] and "<GI_WICAN_SSID>" in rd[n] for n in ("a.txt", "b.json", "check.log")), rd)
    check("redact: every occurrence, not the first", rd["check.log"].count("<GI_WICAN_SSID>") == 2)
    check("redact: a binary (a flash dump) is left byte for byte", rd["dump.bin"] == "bin %s\n" % SSID)
    check("redact: a file without the SSID is unchanged", rd["c.txt"] == "nothing\n")


def test_bins():
    p = os.path.join(BASE, "bins", "x.bin")
    sha = fake_bin(p, "abc1234", 0x5A)
    check("elf_sha256_of_bin reads the descriptor's ELF sha", SG.elf_sha256_of_bin(p) == sha)
    check("app_version reads the descriptor's version", SG.app_version(p) == "abc1234")
    import bench_flash as BF
    check("elf_sha256_of_bin agrees with bench_flash.image_elf_sha256 (the OTA judge's own)",
          BF.image_elf_sha256(p) == sha, BF.image_elf_sha256(p))
    with open(p, "r+b") as f:
        f.seek(0x20)
        f.write(b"\0\0\0\0")
    check("a bin without the descriptor magic is refused", raises(lambda: SG.elf_sha256_of_bin(p), ValueError))
    os.makedirs(os.path.join(BASE, "bins", "two"))
    for n in ("wican-fw_obd_a.bin", "wican-fw_obd_b.bin"):
        open(os.path.join(BASE, "bins", "two", n), "wb").close()
    check("two images in one build folder are refused",
          raises(lambda: SG.image_bin(os.path.join(BASE, "bins", "two")), ValueError))
    check("no image in a build folder is refused",
          raises(lambda: SG.image_bin(os.path.join(BASE, "bins")), ValueError))


# ------------------------------------------------------------------ the adapter

def test_adapter():
    idx_dir = os.environ.get("GI_BASELINE_DIR")
    empty = os.path.join(BASE, "empty")
    os.makedirs(empty)
    j = SG.judged("B1")

    def cr(outdir, rc):
        return CheckRun(check=None, full_id="g/B1", rc=rc, timed_out=False, log_path=os.path.join(outdir, "check.log"),
                        outdir=outdir, duration_s=0.0, ctx={})
    r = j(cr(empty, 0))
    check("adapter: no files is ERROR, named by the judge", [(x.verdict, x.sub) for x in r] == [("ERROR", "B1")],
          [(x.verdict, x.sub) for x in r])
    r = j(cr(empty, 3))
    check("adapter: a non-zero exit adds ERROR sub=rc",
          ("ERROR", "rc") in [(x.verdict, x.sub) for x in r], [(x.verdict, x.sub) for x in r])
    if idx_dir:
        bench_dir = os.path.join(idx_dir, "bench")
        r = j(cr(bench_dir, 0))
        check("adapter: B1 on the e3d9437 files is PASS with its figures",
              [x.verdict for x in r] == ["PASS"] and r[0].figures, [(x.verdict, x.detail[:80]) for x in r])
        r = j(cr(bench_dir, 1))
        check("adapter: the same files with exit 1 are not all PASS",
              any(x.verdict == "ERROR" and x.sub == "rc" for x in r))
        r = SG.judged("B5", "B6", "B7", "B8")(cr(bench_dir, 0))
        check("adapter: several judges give one result each, in order",
              [x.sub for x in r] == ["B5", "B6", "B7", "B8"], [x.sub for x in r])


# ------------------------------------------------------------------ hooks with stand-in tools

class Tools:
    """Stands in for SG._run: each call looks up what the named tool 'does'."""

    def __init__(self):
        self.calls = []
        self.do = {}

    def __call__(self, argv, logpath, env_extra, cwd, timeout):
        k = next(i for i, a in enumerate(argv) if a.endswith(".py"))    # the tool, after the python
        tool = os.path.basename(argv[k])
        sub = argv[k + 1] if k + 1 < len(argv) else ""
        self.calls.append((tool, sub, list(argv)))
        fn = self.do.get((tool, sub)) or self.do.get(tool)
        out, rc, files = fn(argv, env_extra) if fn else ("", 1, {})
        with open(logpath, "w", encoding="utf-8") as f:
            f.write(out)
        for name, body in files.items():
            folder = env_extra.get("GI_RUNS_DIR") or os.path.dirname(logpath)
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                f.write(body if isinstance(body, str) else json.dumps(body))
        return rc

    def named(self, tool, sub=None):
        return [c for c in self.calls if c[0] == tool and (sub is None or c[1] == sub)]


def with_tools(fn):
    saved = SG._run, SG.wican_page, SG._sleep
    t = Tools()
    SG._run, SG._sleep = t, (lambda s: None)
    try:
        return fn(t)
    finally:
        SG._run, SG.wican_page, SG._sleep = saved


def fake_module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


N = [0]


def logdir():
    N[0] += 1
    d = os.path.join(BASE, "logs", "l%d" % N[0])
    os.makedirs(d)
    return d


def test_identify(settings, bench):
    hooks = SG.make_hooks(settings, SSID)
    ident = hooks["identify"]
    wm, xa, xj, xh = (bench.images[n] for n in ("wican_man", "x2_app", "x2_jammer", "x2_held"))
    joined = []
    fake_module("bench_flash", join_ap=lambda s, timeout=40.0: joined.append(s),
                image_elf_sha256=__import__("bench_flash").image_elf_sha256)

    def bi(result):
        calls = []

        def identify(image, ctx, logdir_, settings_):
            calls.append(image.name)
            if isinstance(result, Exception):
                raise result
            return result
        fake_module("bench_identify", identify=identify)
        return calls

    def pins(verdict_line, rec_extra="", rc=0):
        return lambda argv, env: ("pins: TX (GPIO7) %s\n" % verdict_line, rc,
                                  {"x2_hold_pins_T%d.json" % len(argv): '{"esptool": "%s"}' % rec_extra})

    # the WiCAN
    def t1(t):
        SG.wican_page = lambda: {"elf_sha256": "ab" * 32}
        return ident(wm, {}, logdir())
    check("identify WiCAN: the page's elf_sha256", with_tools(t1) == "ab" * 32)

    def t2(t):
        def down():
            raise OSError("no route")
        SG.wican_page = down
        joined.clear()
        return raises(lambda: ident(wm, {}, logdir()), SG.IdentifyError), list(joined)
    e, j = with_tools(t2)
    check("identify WiCAN: an unreadable page RAISES (could not look), after joining the AP",
          e is not None and j == [SSID], (e, j))

    def t3(t):
        SG.wican_page = lambda: {"mode": 0}
        return raises(lambda: ident(wm, {}, logdir()), SG.IdentifyError)
    check("identify WiCAN: a page without elf_sha256 RAISES, never None", with_tools(t3) is not None)

    def t3c(t):
        SG.wican_page = lambda: {"elf_sha256": "deadbeef"}
        return raises(lambda: ident(wm, {}, logdir()), SG.IdentifyError)
    check("identify WiCAN: an elf_sha256 that is not 64 hex RAISES", with_tools(t3c) is not None)

    def t3b(t):
        n = [0]

        def flaky():
            n[0] += 1
            if n[0] == 1:
                raise OSError("first")
            return {"elf_sha256": "cd" * 32}
        SG.wican_page = flaky
        return ident(wm, {}, logdir())
    check("identify WiCAN: read again after joining the AP", with_tools(t3b) == "cd" * 32)

    # the X2
    def t4(t):
        calls = bi("756d6714")
        t.do[("x2_hold.py", "pins")] = pins("not driven, level unreadable")
        return ident(xh, {}, logdir()), calls, t.named("x2_jammer.py")
    got, calls, jam = with_tools(t4)
    check("identify X2: held in ROM is read by the pins, and nothing resets it",
          got == "held:not driven" and calls == [] and jam == [], (got, calls, jam))

    def t5(t):
        calls = bi("756d6714")
        t.do[("x2_hold.py", "pins")] = pins("unknown -- a register was not read", rc=1)
        t.do[("x2_jammer.py", "id")] = lambda a, e: ("", 0, {"x2_jammer_id_T1.json": {"banner_ok": False}})
        return ident(xa, {}, logdir()), calls, t.named("x2_jammer.py")
    got, calls, jam = with_tools(t5)
    check("identify X2: a running image goes to bench_identify, and no ID is sent",
          got == "756d6714" and calls == ["x2_app"] and jam == [], (got, calls, jam))

    def t6(t):
        bi(None)
        t.do[("x2_hold.py", "pins")] = pins("unknown", rc=1)
        t.do[("x2_jammer.py", "id")] = lambda a, e: ("", 0, {"x2_jammer_id_T1.json": {"banner_ok": True}})
        return ident(xa, {}, logdir())
    check("identify X2: the jammer's banner when bench_identify knows nothing", with_tools(t6) == "jammer:banner")

    def t7(t):
        bi(None)
        t.do[("x2_hold.py", "pins")] = pins("unknown", rc=1)
        t.do[("x2_jammer.py", "id")] = lambda a, e: ("", 0, {"x2_jammer_id_T1.json": {"banner_ok": False}})
        return ident(xj, {}, logdir())
    check("identify X2: no banner either is None (on none of the images)", with_tools(t7) is None)

    def t8(t):
        bi(None)
        t.do[("x2_hold.py", "pins")] = pins("unknown", rc=1)
        t.do[("x2_jammer.py", "id")] = lambda a, e: ("Traceback\n", 1, {})
        return raises(lambda: ident(xj, {}, logdir()), SG.IdentifyError)
    check("identify X2: a jammer id that wrote no record RAISES", with_tools(t8) is not None)

    def t9(t):
        bi(None)
        t.do[("x2_hold.py", "pins")] = pins("unknown", rc=1)
        t.do[("x2_jammer.py", "id")] = lambda a, e: ("", 1, {})
        d = logdir()
        with open(os.path.join(d, "x2_jammer_id_OLD.json"), "w") as f:
            json.dump({"banner_ok": True}, f)
        return raises(lambda: ident(xj, {}, d), SG.IdentifyError)
    check("identify X2: an older jammer record does not stand in for this call's", with_tools(t9) is not None)

    def t10(t):
        calls = bi("756d6714")
        t.do[("x2_hold.py", "pins")] = pins("unknown", rec_extra="could not open port 'COM3': PermissionError", rc=1)
        return raises(lambda: ident(xa, {}, logdir()), SG.IdentifyError), calls
    e, calls = with_tools(t10)
    check("identify X2: a port that would not open RAISES, before anything resets the board",
          e is not None and calls == [], (e, calls))

    def t11(t):
        calls = bi("756d6714")
        t.do[("x2_hold.py", "pins")] = lambda a, e: ("Traceback\n", 1, {})
        d = logdir()
        with open(os.path.join(d, "x2_hold_pins_OLD.json"), "w") as f:
            f.write("{}")
        return raises(lambda: ident(xa, {}, d), SG.IdentifyError), calls
    e, calls = with_tools(t11)
    check("identify X2: pins that wrote no record of its own RAISES", e is not None and calls == [], (e, calls))

    def t12(t):
        bi(RuntimeError("boot capture failed"))
        t.do[("x2_hold.py", "pins")] = pins("unknown", rc=1)
        return raises(lambda: ident(xa, {}, logdir())), t.named("x2_jammer.py")
    e, jam = with_tools(t12)
    check("identify X2: bench_identify's own failure passes up (the core stops), no ID sent",
          e is not None and jam == [], (e, jam))

    def t13(t):
        bi("756d6714")
        t.do[("x2_hold.py", "pins")] = lambda a, e: ("pins: TX (GPIO7) not driven %s\n" % SSID, 0,
                                                     {"x2_hold_pins_T.json": '{"ssid": "%s"}' % SSID})
        d = logdir()
        ident(xh, {}, d)
        return all(SSID not in open(os.path.join(d, n), encoding="utf-8").read() for n in os.listdir(d))
    check("identify X2: the SSID is redacted from the tool's log and its new records", with_tools(t13))


def test_flashers(settings, bench):
    hooks = SG.make_hooks(settings, SSID)
    wm, xh, xj = (bench.images[n] for n in ("wican_man", "x2_held", "x2_jammer"))

    m2 = bench.images["wican_man2"]
    TOOK, A1 = "...\nVERDICT: (c) TOOK -- page reads the image\n", "VERDICT: (a1) did NOT take: reset at 131072\n"

    def ota(outs, page=m2.expect):
        """outs: each upload's (stdout, rc), in order; page: what the WiCAN reads after
        the first attempt fails (a sha, or an exception to raise)."""
        def t(tt):
            seq = list(outs)
            tt.do["bench_flash.py"] = lambda a, e: (seq.pop(0) + ({},)) if seq else ("", 1, {})

            def pg():
                if isinstance(page, Exception):
                    raise page
                return {"elf_sha256": page}
            SG.wican_page = pg
            try:            # the suite's own flasher, which knows its images; it must never raise
                r = bench.flashers["ota"](wm, wm.params, settings, logdir())
            except Exception as e:
                r = {"ok": None, "recoverable": None, "detail": "RAISED %r" % e}
            bins = [c[2][c[2].index("--bin") + 1] for c in tt.named("bench_flash.py")]
            return r, bins
        return with_tools(t)
    target, cur = wm.params["bin"], m2.params["bin"]
    r, bins = ota([(TOOK, 0)])
    check("ota: (c) TOOK with exit 0 is ok, one upload", r["ok"] and bins == [target], (r, bins))
    r, bins = ota([(A1, 1), (TOOK, 0), (TOOK, 0)])
    check("ota: a failed upload, the running build restored over itself, then the image again",
          r["ok"] and bins == [target, cur, target], (r, bins))
    check("ota: the first attempt's failure line is kept", "attempt1 wican_man: VERDICT: (a1)" in r["detail"], r)
    r, bins = ota([(A1, 1), (TOOK, 0), (A1, 1)])
    check("ota: a second failure in a row is not ok (the core stops the run), the board restorable",
          not r["ok"] and r["recoverable"] is True and bins == [target, cur, target]
          and r["detail"].count("VERDICT: (a1)") == 2, (r, bins))
    r, bins = ota([(A1, 1), (A1, 1)])
    check("ota: a failed restore is not ok, no third upload, no more WiCAN flashes",
          not r["ok"] and r["recoverable"] is False and bins == [target, cur], (r, bins))
    r, bins = ota([(A1, 1)], page="ee" * 32)
    check("ota: the WiCAN on a build this suite does not have: no restore, no more flashes",
          not r["ok"] and r["recoverable"] is False and bins == [target], (r, bins))
    r, bins = ota([(A1, 1)], page=OSError("no route"))
    check("ota: the page unreadable after a failure: no restore on an unknown state",
          not r["ok"] and r["recoverable"] is False and bins == [target], (r, bins))
    r, bins = ota([("VERDICT: (c) TOOK\n", 2), (TOOK, 0), ("VERDICT: (c) TOOK\n", 2)])
    check("ota: TOOK with a non-zero exit is not ok", not r["ok"] and len(bins) == 3, (r, bins))
    r, bins = ota([("", 0), (TOOK, 0), ("", 0)])
    check("ota: no VERDICT line is not ok", not r["ok"] and "no VERDICT" in r["detail"], r)
    r, bins = ota([(A1, 1), (TOOK, 0), (TOOK, 0)], page=wm.expect)
    check("ota: the board already on the image after a failure: that image restored, then tried again",
          r["ok"] and bins == [target, target, target], (r, bins))

    def hold(out, rc):
        def t(tt):
            tt.do[("x2_hold.py", "hold")] = lambda a, e: (out, rc, {})
            return hooks["x2_hold"](xh, xh.params, settings, logdir())
        return with_tools(t)
    check("x2_hold: not driven, exit 0 is ok", hold("hold: TX (GPIO7) not driven, level unreadable\n", 0)["ok"])
    check("x2_hold: driven is not ok", not hold("hold: TX (GPIO7) driven -- out_sel 1\n", 0)["ok"])
    check("x2_hold: unknown, exit 1 is not ok", not hold("hold: TX (GPIO7) unknown\n", 1)["ok"])

    def jam(dump_verdict, dump_rc=0, flash_rc=0, records=1):
        def t(tt):
            files = {("x2_flash_custody_dump_T%d.json" % k): {"verdict": dump_verdict} for k in range(records)}
            tt.do[("x2_flash_custody.py", "dump")] = lambda a, e: ("", dump_rc, files)
            tt.do[("x2_flash_custody.py", "flash-jammer")] = lambda a, e: ("", flash_rc, {})
            r = hooks["x2_jammer"](xj, xj.params, settings, logdir())
            return r, tt.named("x2_flash_custody.py", "flash-jammer")
        return with_tools(t)
    r, fl = jam("PASS")
    check("x2_jammer: a PASS dump, then the jammer, from that dump's record",
          r["ok"] and len(fl) == 1 and "x2_flash_custody_dump_T0.json" in fl[0][2][fl[0][2].index("--dump-record") + 1]
          and SG.JAMMER_MD5 in fl[0][2], (r, fl))
    r, fl = jam("FAIL")
    check("x2_jammer: a FAIL dump flashes nothing", not r["ok"] and fl == [], (r, fl))
    r, fl = jam("PASS", dump_rc=1)
    check("x2_jammer: a dump exiting non-zero flashes nothing", not r["ok"] and fl == [], (r, fl))
    r, fl = jam("PASS", records=0)
    check("x2_jammer: no dump record flashes nothing", not r["ok"] and fl == [], (r, fl))
    r, fl = jam("PASS", records=2)
    check("x2_jammer: two dump records (which one?) flashes nothing", not r["ok"] and fl == [], (r, fl))
    r, fl = jam("PASS", flash_rc=1)
    check("x2_jammer: a failed jammer write stops further X2 flashes", not r["ok"] and r["recoverable"] is False, r)


def reboot_tool(verdict="PASS", rc=0, write=True, extra=None, seen=None):
    """Stands in for wican_reboot.py: writes the record where --out says."""
    def fn(argv, env):
        if seen is not None:
            seen.append("wican_reboot")
        if write:
            rec = dict(verdict=verdict, detail="%s stand-in" % verdict, found_image="wican_man",
                       found_sha="ab" * 32, differs_from_first=False, came_up_armed=False, disarmed=None,
                       before={"mode": 3, "reasons": {"abort_latched": True}})
            rec.update(extra or {})
            with open(argv[argv.index("--out") + 1], "w", encoding="utf-8") as f:
                json.dump(rec, f)
        return "%s stand-in\n" % verdict, rc, {}
    return fn


def opts(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag and i + 1 < len(argv)]


def test_board_hooks(settings, bench, emu):
    """The core's on_start / on_end (port plan step 4; user 2026-10-20): the WiCAN's
    run wican_reboot.py and pass only on its PASS; the X2's do nothing."""
    check("board hooks: the bench suite has on_start and on_end",
          callable(bench.on_start) and callable(bench.on_end))
    check("board hooks: the emulated suite has none (no hardware)", emu.on_start is None and emu.on_end is None)
    wm, rt, xa = bench.images["wican_man"], bench.images["wican_rt"], bench.images["x2_app"]
    wican = {n: im.expect for n, im in bench.images.items() if im.board == "wican"}
    order = []
    fake_module("bench_flash", join_ap=lambda s, timeout=40.0: order.append("join"),
                image_elf_sha256=__import__("bench_flash").image_elf_sha256)

    def page_up():
        order.append("page")
        return {"elf_sha256": "ab" * 32}

    def call(which, board, image, tool=None, page=page_up):
        def t(tt):
            SG.wican_page = page
            if tool is not None:
                tt.do["wican_reboot.py"] = tool
            d = logdir()
            try:
                out = getattr(bench, which)(board, image, {}, d)
            except Exception as e:          # a step that raises is a failed check here, not a crash
                out = dict(ok="raised", detail=repr(e), found={})
            return out, tt.named("wican_reboot.py"), d
        return with_tools(t)

    out, calls, _ = call("on_start", "x2", xa, reboot_tool())
    check("board hooks: the X2's start step does nothing and passes",
          out["ok"] is True and calls == [], (out, calls))
    out, calls, _ = call("on_end", "x2", None, reboot_tool())
    check("board hooks: the X2's end step does nothing and passes", out["ok"] is True and calls == [], (out, calls))

    out, calls, d = call("on_start", "wican", rt, reboot_tool())
    argv = calls[0][2] if calls else []
    known = dict(kv.split("=", 1) for kv in opts(argv, "--known"))
    check("board hooks: the WiCAN's start runs wican_reboot.py once, from the harness folder",
          len(calls) == 1 and os.path.normpath(argv[len(SG._py(settings))]) ==
          os.path.normpath(os.path.join(SG.harness_dir(settings), "wican_reboot.py")), argv)
    check("board hooks: start names the run's first image (--first)",
          opts(argv, "--first") == ["wican_rt"] and not opts(argv, "--want") and "--want-unknown" not in argv, argv)
    check("board hooks: --known is every WiCAN image with its sha, and no X2 image",
          known == wican and len(known) >= 4, (sorted(known), sorted(wican)))
    check("board hooks: start passes on the tool's PASS, and keeps what it found",
          out["ok"] is True and out["found"]["before_mode"] == 3 and out["found"]["before_latched"] is True
          and out["found"]["found_image"] == "wican_man" and out["found"]["record"]
          and os.path.exists(os.path.join(d, out["found"]["record"])), out)
    check("board hooks: the record and the tool's log are in the hook's log folder",
          sorted(os.path.splitext(n)[1] for n in os.listdir(d) if n.startswith("wican_reboot_start_"))
          == [".json", ".txt"], os.listdir(d))

    out, calls, _ = call("on_end", "wican", wm, reboot_tool())
    argv = calls[0][2] if calls else []
    check("board hooks: the end names the image the core believes (--want)",
          opts(argv, "--want") == ["wican_man"] and not opts(argv, "--first") and out["ok"] is True, (argv, out))
    out, calls, _ = call("on_end", "wican", None, reboot_tool())
    argv = calls[0][2] if calls else []
    check("board hooks: an end where the core does not know the image says so (--want-unknown)",
          "--want-unknown" in argv and not opts(argv, "--want") and out["ok"] is True, (argv, out))

    out, _, _ = call("on_start", "wican", wm, reboot_tool("FAIL", 1, extra={"detail": "FAIL start wican, found X"}))
    check("board hooks: the tool's FAIL fails the step, with its detail",
          out["ok"] is False and out["detail"] == "FAIL start wican, found X", out)
    out, _, _ = call("on_start", "wican", wm, reboot_tool("FAIL", 0))
    check("board hooks: exit 0 with a FAIL record still fails", out["ok"] is False, out)
    out, _, _ = call("on_end", "wican", wm, reboot_tool("PASS", 1))
    check("board hooks: a PASS record with a non-zero exit fails", out["ok"] is False, out)
    out, _, _ = call("on_start", "wican", wm, reboot_tool(write=False, rc=1))
    check("board hooks: no record fails, saying so", out["ok"] is False and "no record" in out["detail"], out)

    out, _, d = call("on_start", "wican", wm, reboot_tool(extra={"note": "on " + SSID}))
    check("board hooks: the SSID is out of the step's record and log",
          out["ok"] and all(SSID not in open(os.path.join(d, n), encoding="utf-8").read() for n in os.listdir(d)),
          os.listdir(d))

    def down():
        order.append("page")
        raise OSError("no route")
    order.clear()
    out, calls, _ = call("on_start", "wican", wm, reboot_tool(seen=order), page=down)
    check("board hooks: an unreadable page joins the AP before the reboot tool runs",
          "join" in order and "wican_reboot" in order and order.index("join") < order.index("wican_reboot"), order)
    check("board hooks: a page still unreadable is left to the tool (it runs and decides)",
          len(calls) == 1 and out["ok"] is True, (calls, out))


def test_board_hooks_e2e(settings, bench):
    """Reviewer condition 5: a killed run leaves the WiCAN armed and latched; the next
    start's hook reboots it, disarms what comes up armed, passes, and says what it
    found. The real wican_reboot.py against test_wican_reboot.py's fake device."""
    # The harness is bench/ of the settings' gi_tools (this folder here, 2026-10-22), which
    # the hooks run in place: no copy into the fake reverse-it root, which held it before.
    src = SG.harness_dir(settings)
    if not os.path.exists(os.path.join(src, "test_wican_reboot.py")):
        check("board hooks e2e: the suite's harness folder holds test_wican_reboot.py", False, src)
        return
    sys.path.insert(0, src)
    try:
        import test_wican_reboot as TW
    finally:
        sys.path.remove(src)
    srv = TW.http.server.ThreadingHTTPServer(("127.0.0.1", 0), TW.Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    saved_host, saved_page = os.environ.get("GI_WICAN_HOST"), SG.wican_page
    os.environ["GI_WICAN_HOST"] = "127.0.0.1:%d" % srv.server_address[1]
    man, rt = bench.images["wican_man"], bench.images["wican_rt"]

    def case(which, image, dev):
        TW.Fake.dev = dev
        SG.wican_page = lambda: {"elf_sha256": dev.sha}
        d = logdir()
        try:
            out = getattr(bench, which)("wican", image, {}, d)
        except Exception as e:
            out = dict(ok="raised", detail=repr(e), found={})
        text = "".join(open(os.path.join(d, n), encoding="utf-8").read() for n in os.listdir(d))
        return out, list(dev.paths), text
    try:
        out, paths, text = case("on_start", man, TW.Dev(sha=man.expect, mode=3, latched=True))
        f = out.get("found") or {}
        check("board hooks e2e: armed and latched by a killed run, the start reboots and passes",
              out["ok"] is True and "POST /system_reboot" in paths, (out, paths))
        check("board hooks e2e: and says what it found (mode 3, latched, on wican_man, the first image)",
              f.get("before_mode") == 3 and f.get("before_latched") is True and f.get("found_image") == "wican_man"
              and f.get("differs_from_first") is False and "before: mode 3, latched True" in out["detail"], out)
        check("board hooks e2e: no other page field reaches the step's files",
              "FAKE_SSID_VALUE" not in text and "FAKE_PASS_VALUE" not in text)

        out, paths, _ = case("on_start", rt, TW.Dev(sha=man.expect, mode=2, boot_mode=2))
        f = out.get("found") or {}
        check("board hooks e2e: an image that comes up armed is disarmed, passes, and says so",
              out["ok"] is True and f.get("came_up_armed") is True and f.get("disarmed") is True
              and "POST /gen_inhibit_set?mode=0" in paths, (out, paths))
        check("board hooks e2e: a suite image other than the run's first is marked",
              f.get("differs_from_first") is True and "not the run's first image wican_rt" in out["detail"], out)

        odd = "cd" * 32
        out, _, _ = case("on_start", man, TW.Dev(sha=odd))
        check("board hooks e2e: an unknown image refuses the start, naming the hash",
              out["ok"] is False and odd in out["detail"], out)
        out, _, _ = case("on_start", man, TW.Dev(sha=man.expect, after_latched=True))
        check("board hooks e2e: a latch that survives the reboot fails the start", out["ok"] is False, out)

        out, _, _ = case("on_end", man, TW.Dev(sha=man.expect, mode=2, boot_mode=2))
        f = out.get("found") or {}
        check("board hooks e2e: the release reboots, disarms and confirms the image the core believes",
              out["ok"] is True and f.get("came_up_armed") is True and f.get("disarmed") is True, out)
        out, _, _ = case("on_end", man, TW.Dev(sha=rt.expect))
        check("board hooks e2e: a release on another image is not confirmed", out["ok"] is False, out)
        out, _, _ = case("on_end", None, TW.Dev(sha=man.expect))
        check("board hooks e2e: a release the core cannot name is not confirmed", out["ok"] is False, out)
    finally:
        srv.shutdown()
        srv.server_close()
        SG.wican_page = saved_page
        if saved_host is None:
            os.environ.pop("GI_WICAN_HOST", None)
        else:
            os.environ["GI_WICAN_HOST"] = saved_host


def git(repo, *a):
    import subprocess
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(a),
                          capture_output=True, text=True, check=True).stdout.strip()


def test_code_info(settings):
    """commit and dirty, read off the trees: what the core's Suite.requires compares."""
    from vtrux_bench.run.engine import _code_ident
    bench0 = SG.bench(settings)
    ci = bench0.code_info({})
    check("code: trees that are not git repos are dirty (no code identity)", ci["dirty"] is True, ci["trees"])
    check("code: the bench suite requires the emulated suite on the same code",
          bench0.requires == "gen-inhibit-emulated", bench0.requires)

    repo = os.path.join(BASE, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    with open(os.path.join(repo, ".gitignore"), "w") as f:
        f.write("*.bin\n")
    with open(os.path.join(repo, "src.c"), "w") as f:
        f.write("int x;\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "c")
    h = git(repo, "rev-parse", "--short=7", "HEAD")

    def build(version_of=lambda n: h):
        for k, (name, d) in enumerate(sorted(SG.WICAN_BUILDS.items())):
            fake_bin(os.path.join(repo, "build-tester-" + d, "wican-fw_obd_t.bin"), version_of(name), 0x10 + k)
        fake_bin(os.path.join(repo, "prod", "wican-fw_obd_p.bin"), version_of("wican_prod"), 0x70)
    build()
    s2 = dict(settings, gi_images=repo, gi_prod_dir=os.path.join(repo, "prod"), gi_fw=repo, gi_commit=h)
    ci = SG.bench(s2).code_info({})
    ce = SG.emulated(s2).code_info({})
    check("code: clean trees at gi_commit, every image naming it: not dirty",
          ci["dirty"] is False and ce["dirty"] is False, (ci["trees"], ce["gi_fw"]))
    check("code: each image records its commit and clean state",
          ci["images"]["wican_man"]["commit_clean"] == "%s clean=True" % h, ci["images"]["wican_man"])
    check("code: the core reads the same code for both suites",
          _code_ident(ci) == _code_ident(ce) == "commit:%s" % h, (_code_ident(ci), _code_ident(ce)))
    check("code: gi_commit that is not HEAD is dirty",
          SG.bench(dict(s2, gi_commit="0000000")).code_info({})["dirty"] is True
          and SG.emulated(dict(s2, gi_commit="0000000")).code_info({})["dirty"] is True)
    build(lambda n: "abcdef0" if n == "wican_rxq" else h)
    ci = SG.bench(s2).code_info({})
    check("code: one image built from another commit is dirty",
          ci["dirty"] is True and ci["images"]["wican_rxq"]["commit_clean"].endswith("clean=False"),
          ci["images"]["wican_rxq"])
    build()
    with open(os.path.join(repo, "src.c"), "a") as f:
        f.write("int y;\n")
    check("code: an uncommitted change is dirty, for both suites",
          SG.bench(s2).code_info({})["dirty"] is True and SG.emulated(s2).code_info({})["dirty"] is True)


FAKE_SSH = r"""#!/usr/bin/env bash
# The host-group test's ssh: logs the host and the command; runs a copy (tar xf) here
# with HOME set to the fake host's home, and answers anything else as a passing check.
while [ "${1#-}" != "$1" ]; do [ "$1" = "-o" ] && shift; shift; done
printf 'HOST %s CMD %s\n' "$1" "$2" >> "$SSH_LOG"
case "$2" in
  *"tar xf"*) h=$FAKE_HOME; command -v cygpath > /dev/null && h=$(cygpath -u "$h")   # a POSIX home, as the host has
              HOME="$h" bash -c "$2" ;;
  *) echo "pages seen: fake"; echo "all emitted diag frames decode against the DBC"; echo "exit 0" ;;
esac
"""


def test_host_group(settings, emu):
    """H and B15host run this clone's harness (host-run commit, 2026-10-22): H the repo's
    baseline_host_run.sh with RROOT from gi_host_root; B15host copies its files to the
    host and runs them there, under a fake ssh; a copy that fails stops the check."""
    import subprocess
    host = {c.id: c for g in emu.groups if g.name == "host" for c in g.checks}
    h, b = host.get("H"), host.get("B15host")
    check("the host group has H and B15host", h is not None and b is not None, sorted(host))
    if h is None or b is None:
        return
    check("H runs the repo's baseline_host_run.sh (the suite's HARNESS)",
          h.argv[:2] == ["{bash}", SG.HARNESS + "/baseline_host_run.sh"], h.argv)
    check("H passes gi_host_root as RROOT", h.env.get("RROOT") == "{gi_host_root}", h.env)
    raw = json.dumps([c.argv for c in host.values()])
    check("no host check names the SeaDrive harness", "notes/artifacts/gen-inhibit" not in raw, raw[:300])

    d = os.path.join(BASE, "hostgroup")
    fbin, fhome, out = (os.path.join(d, x) for x in ("bin", "home", "out"))
    for p in (fbin, fhome, out):
        os.makedirs(p)
    with open(os.path.join(fbin, "ssh"), "w", newline="\n") as f:
        f.write(FAKE_SSH)
    os.chmod(os.path.join(fbin, "ssh"), 0o755)      # Linux needs it; Git Bash goes by the #! line

    def run_b15(gi_tools, tag):
        log = os.path.join(d, "ssh_%s.log" % tag)
        ctx = {k: v for k, v in settings.items() if isinstance(v, str)}
        ctx.update(gi_tools=gi_tools.replace("\\", "/"), outdir=out.replace("\\", "/"), run_dir="R", run_id="I",
                   suite="S", check_id="B15host", group="host", jobs="1")
        argv = [fmt(a, ctx) for a in b.argv]
        e = dict(os.environ, PATH=fbin + os.pathsep + os.environ.get("PATH", ""), SSH_LOG=log, FAKE_HOME=fhome)
        r = subprocess.run(argv, capture_output=True, text=True, env=e, stdin=subprocess.DEVNULL, timeout=120)
        lines = open(log, encoding="utf-8").read().splitlines() if os.path.exists(log) else []
        return r.returncode, lines, r.stdout + r.stderr

    rc, lines, txt = run_b15(HERE, "good")
    res = os.path.join(out, "B15_diag_decode_check_host_e3d9437.txt")
    txt += open(res, encoding="utf-8", errors="replace").read() if os.path.exists(res) else ""   # the check's own output
    dest = os.path.join(fhome, "gi_bench_e3d9437")
    same = all(os.path.isfile(os.path.join(dest, f)) and
               open(os.path.join(dest, f), "rb").read() == open(os.path.join(HERE, "bench", f), "rb").read()
               for f in SG.B15_FILES)
    check("B15host copies %s from bench/ to the host's ~/gi_bench_<commit>" % ", ".join(SG.B15_FILES),
          rc == 0 and same, (rc, txt[-300:], os.listdir(dest) if os.path.isdir(dest) else None))
    # the copy is enough on its own: the check's one local import, and the pins it reads
    p = subprocess.run([sys.executable, "-B", "-c", "import gi_paths; print('pins', len(gi_paths.load_pins()))"],
                       cwd=dest if os.path.isdir(dest) else d, capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=""))
    check("the copy holds gi_paths and the pins it loads", p.returncode == 0 and p.stdout.startswith("pins "),
          (p.stdout + p.stderr)[-300:])
    run = [l for l in lines if "tar xf" not in l]
    want = ('HOST hosttest CMD cd ~/rt && REVERSE_IT_DIR="$PWD" python3 -B ~/gi_bench_e3d9437/diag_decode_check.py '
            '~/gi_baseline_e3d9437/test/gen_inhibit_host 2>&1; echo "exit $?"')
    check("B15host runs the copied check on gi_host_ssh, from gi_host_root, with REVERSE_IT_DIR set there",
          run == [want], run)
    check("both of B15host's ssh calls go to gi_host_ssh",
          len(lines) == 2 and all(l.startswith("HOST hosttest CMD ") for l in lines), lines)
    check("B15host's output file holds the host's answer",
          os.path.exists(res) and "exit 0" in open(res, encoding="utf-8").read(), txt[-300:])

    partial = os.path.join(d, "partial_tools")
    os.makedirs(os.path.join(partial, "bench"))
    for f in SG.B15_FILES[:-1]:
        shutil.copy(os.path.join(HERE, "bench", f), os.path.join(partial, "bench"))
    rc, lines, txt = run_b15(partial, "partial")
    check("a file B15host cannot copy fails the check, and the check does not run",
          rc != 0 and not [l for l in lines if "tar xf" not in l], (rc, lines, txt[-300:]))


def test_example(bench, emu):
    """The settings example names every key the suites need and every placeholder."""
    import re
    with open(os.path.join(HERE, "suite_gen_inhibit.settings.example.json"), encoding="utf-8") as f:
        ex = json.load(f)
    src = open(os.path.join(HERE, "suite_gen_inhibit.py"), encoding="utf-8").read()
    needed = set(re.findall(r'need\(settings, "(\w+)"\)', src))
    run_keys = {"run_dir", "run_id", "suite", "outdir", "check_id", "group", "jobs", "work"}
    for s in (bench, emu):
        for g in s.groups:
            for c in g.checks:
                for a in list(c.argv) + list(c.env.values()) + [c.cwd or ""]:
                    needed |= set(re.findall(r"\{(\w+)\}", a)) - run_keys
    needed |= {"x2_images", "hosts", "esptool", "runs_root", "bench_holder"}
    check("the settings example has every key the suites need: %s" % ",".join(sorted(needed)),
          needed <= set(ex), sorted(needed - set(ex)))
    check("the settings example has no WiCAN SSID", not re.search(r"WiCAN_[0-9a-fA-F]{4}", json.dumps(ex)))


def main():
    try:
        settings, shas = fixture()
        bench, emu = test_suites(settings, shas)
        test_example(bench, emu)
        test_host_group(settings, emu)
        test_code_info(settings)
        test_ssid(settings, bench, emu)
        test_redact(settings)
        test_bins()
        test_adapter()
        test_identify(settings, bench)
        test_flashers(settings, bench)
        test_board_hooks(settings, bench, emu)
        test_board_hooks_e2e(settings, bench)
    finally:
        rmtree(BASE)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
