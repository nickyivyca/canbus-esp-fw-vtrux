#!/usr/bin/env python3
"""Tests for suite_gen_inhibit.py: both suites load and validate on the pinned core,
cover the baseline index once each, keep the SSID out of what the record keeps, and
their hooks say "could not look" (raise) rather than "not this image" (None) when a
tool fails. No hardware: every tool the hooks run is stood in for.

    PYTHONPATH=<core 1c7bed2>/shared/bench GI_BASELINE_DIR=<.../runs/baseline_e3d9437> \
        py -3.14 -B test_suite_gen_inhibit.py

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
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vtrux_bench.run import cli as CLI                       # noqa: E402
from vtrux_bench.run.engine import Engine, fmt               # noqa: E402
from vtrux_bench.run.model import CheckRun, validate         # noqa: E402
import suite_gen_inhibit as SG                               # noqa: E402

SSID = "WiCAN_00aa11bb22cc"        # made up for the test; the real one is never here
BASE = os.path.join(tempfile.gettempdir(), "gen-inhibit-suite-test", str(os.getpid()))
fails = []
oks = 0


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
    shutil.rmtree(BASE, ignore_errors=True)
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
        "interposer_code": os.path.join(BASE, "ic"), "x2_images": x2, "gi_fw": HERE, "gi_components": "x",
        "gi_host_ssh": "hosttest", "gi_host_root": "~/rt", "bash": shutil.which("bash"),
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
        test_ssid(settings, bench, emu)
        test_redact(settings)
        test_bins()
        test_adapter()
        test_identify(settings, bench)
        test_flashers(settings, bench)
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
