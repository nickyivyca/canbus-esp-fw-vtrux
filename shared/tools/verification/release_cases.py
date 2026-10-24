#!/usr/bin/env python3
"""Real release builds against SPEC R5, R8 to R11, R13, R14 (with R3), R16 (override_path) and R22.

    (from an exported ESP-IDF shell, with its Python)
    python -B release_cases.py --root <short scratch dir> --out <run folder> --base <commit>
                               [--cases a,b,...] [--jobs N] [--fixture <repo>]
    python -B release_cases.py --self-test      # the record and second-build judges against fakes

The project is gen-inhibit at --base, cloned into --root, with a fixture commit on
top that no gen-inhibit branch has: vtrux_build.json ({"project": "gen-inhibit",
"shared_components": []}), CONFIG_APP_REPRODUCIBLE_BUILD on, and the tracked files as
`idf.py reconfigure` rewrites them. What it rewrote is listed in fixture.txt. Two
more branches sit on it: fx-r11 (the build deletes its own .bin last) and fx-r16
(a shared component taken from a local folder). --fixture uses a repo already
made that way instead (another host, for R5 across host OSes). REVERSE_IT_DIR is a
stand-in folder under --root, so no record reaches the real firmware/builds/.

Each case runs the release build as a user would (`python -B -m vtrux_build
release`, the tooling from this checkout's shared/tools), then judges what it did
from outside: the exit code, the record folder before and after, the build folder,
git. A refusal case judges only that the build stopped, wrote no record, and got as
far as the case needs. It never judges the wording. A case whose trigger did not
happen (a watcher that never fired, a launcher CMake did not take) is VOID, never
a pass.

Before each case: the bench lock must be free, the "No builds" line clear, and no
simulator running (AGENTS.md "Builds during wall-clock tests"). Otherwise it stops.

Gen-inhibit tester, 2026-10-24 (C6f verification, part A real builds).
"""
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)
import independent as I  # noqa: E402

GIT_ID = {"GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "tester@invalid", "GIT_COMMITTER_NAME": "tester",
          "GIT_COMMITTER_EMAIL": "tester@invalid", "GIT_AUTHOR_DATE": "2026-10-24T00:00:00+0000",
          "GIT_COMMITTER_DATE": "2026-10-24T00:00:00+0000"}
REPRO_OFF = "# CONFIG_APP_REPRODUCIBLE_BUILD is not set"
REPRO_ON = "CONFIG_APP_REPRODUCIBLE_BUILD=y"
RESULTS = []


# ---------- judges (pure; --self-test runs them on fakes) ----------

def judge_record(rec, files, facts):
    """SPEC R13 on one record. rec: the parsed .json; files: {name: bytes} of the record
    folder's files for it; facts: what the tester read itself. Returns failing items."""
    bad = []
    h = rec.get("image_hash")
    names = {h + ".bin", h + ".elf", h + ".json"} if h else set()
    if not h or set(files) != names:
        bad.append("files: %s, want %s" % (sorted(files), sorted(names)))
        return bad
    b, e = files[h + ".bin"], files[h + ".elf"]

    def want(label, got, exp):
        if got != exp:
            bad.append("%s: %r, want %r" % (label, got, exp))
    want("the .bin is the image the build named (R11)", hashlib.sha256(b).hexdigest(), facts["app_bin_sha256"])
    want("record name = image hash = the ELF's sha256 (R1)", h, hashlib.sha256(e).hexdigest())
    want("image hash = the descriptor's ELF hash", h, I.image_hash(b))
    want("code hash = the tester's R2", rec.get("code_hash"), I.code_hash(b))
    want("image size", rec.get("image_size"), len(b))
    want("commit", rec.get("commit"), facts["commit"])
    want("project", rec.get("project"), facts["project"])
    want("host OS system", (rec.get("host_os") or {}).get("system"), facts["system"])
    want("toolchain version", (rec.get("toolchain") or {}).get("version"), facts["toolchain"])
    want("IDF version in the image", (rec.get("idf_version") or {}).get("image"), facts["idf_image"])
    want("IDF version of the project", (rec.get("idf_version") or {}).get("project"), facts["idf_describe"])
    want("dependencies.lock sha256, line endings as LF", rec.get("dependencies_lock_sha256_lf"), facts["lock_sha256"])
    comps = rec.get("components") or {}
    for name, d in facts["component_digests"].items():
        want("component %s digest" % name, (comps.get(name) or {}).get("digest"), d)
    want("shared components", rec.get("shared_components"), facts["shared_components"])
    t = rec.get("tooling") or {}
    for k in ("tag", "commit", "dirty", "digest"):
        want("tooling %s" % k, t.get(k), facts["tooling"][k])
    return bad


def judge_again(rc, before, after):
    """SPEC R13 (never edited) for a second release build of the same image: it refuses, or
    it skips; either way the record folder keeps every file's bytes and mtime."""
    if before != after:
        return "FINDING: the record folder changed (%s)" % ", ".join(
            sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)))
    return None if rc is not None else "no exit code"


def self_test():
    ok = True
    b = I.build()
    e = b"an elf"
    h = hashlib.sha256(e).hexdigest()
    b = I.with_field(b, "elf_sha256", bytes.fromhex(h))
    rec = {"image_hash": h, "code_hash": I.code_hash(b), "image_size": len(b), "commit": "c" * 40, "project": "p",
           "host_os": {"system": "Windows"}, "toolchain": {"version": "gcc 14"},
           "idf_version": {"image": "v5.4.1", "project": "v5.4.1"}, "dependencies_lock_sha256_lf": "l" * 64,
           "components": {"main": {"digest": "d" * 64}}, "shared_components": {},
           "tooling": {"tag": None, "commit": "t" * 40, "dirty": False, "digest": "g" * 64}}
    files = {h + ".bin": b, h + ".elf": e, h + ".json": b"{}"}
    facts = {"app_bin_sha256": hashlib.sha256(b).hexdigest(), "commit": "c" * 40, "project": "p",
             "system": "Windows", "toolchain": "gcc 14", "idf_image": "v5.4.1", "idf_describe": "v5.4.1",
             "lock_sha256": "l" * 64, "component_digests": {"main": "d" * 64}, "shared_components": {},
             "tooling": {"tag": None, "commit": "t" * 40, "dirty": False, "digest": "g" * 64}}
    r = judge_record(rec, files, facts)
    print("  %s faithful record passes %s" % ("ok  " if not r else "FAIL", r))
    ok &= not r
    other = I.with_byte(b, I.layout(b)[0][1][0], 0x5A)

    def broken(label, rec2=None, files2=None, facts2=None):
        r = judge_record(rec2 or rec, files2 or files, facts2 or facts)
        print("  %s caught: %s" % ("ok  " if r else "MISS", label))
        return bool(r)
    for label, k, v in (("wrong code hash", "code_hash", "0" * 64), ("wrong size", "image_size", 1),
                        ("wrong commit", "commit", "d" * 40), ("wrong project", "project", "q"),
                        ("wrong host", "host_os", {"system": "Linux"}), ("wrong toolchain", "toolchain", {"version": "x"}),
                        ("wrong IDF in image", "idf_version", {"image": "v5.3", "project": "v5.4.1"}),
                        ("wrong IDF of project", "idf_version", {"image": "v5.4.1", "project": "v5.3"}),
                        ("wrong lock hash", "dependencies_lock_sha256_lf", "m" * 64),
                        ("wrong component digest", "components", {"main": {"digest": "e" * 64}}),
                        ("a component missing", "components", {}),
                        ("wrong shared components", "shared_components", {"x": {}}),
                        ("tooling dirty", "tooling", dict(rec["tooling"], dirty=True)),
                        ("tooling digest", "tooling", dict(rec["tooling"], digest="h" * 64))):
        ok &= broken(label, rec2=dict(rec, **{k: v}))
    old = {k: v for k, v in rec.items() if k != "dependencies_lock_sha256_lf"}
    ok &= broken("lock hash only under the old field name", rec2=dict(old, dependencies_lock_sha256="l" * 64))
    lf, crlf = b"a: 1\nb: 2\n", b"a: 1\r\nb: 2\r\n"
    for label, x, y, same in (("CRLF and LF give one hash", crlf, lf, True),
                              ("a changed byte gives another", lf, lf.replace(b"2", b"3"), False),
                              ("a lone CR is kept", b"a: 1\rb: 2\n", b"a: 1b: 2\n", False)):
        good = (sha256_lf_bytes(x) == sha256_lf_bytes(y)) == same
        print("  %s LF-normalised lock hash, %s" % ("ok  " if good else "FAIL", label))
        ok &= good
    ok &= sha256_lf_bytes(lf) == hashlib.sha256(lf).hexdigest()
    ok &= broken("named by something other than the ELF's sha256",
                 rec2=dict(rec, image_hash="a" * 64), files2={"a" * 64 + x: files[h + x] for x in (".bin", ".elf", ".json")})
    ok &= broken("the .elf missing", files2={k: v for k, v in files.items() if not k.endswith(".elf")})
    ok &= broken("an extra file", files2=dict(files, **{h + ".txt": b""}))
    ok &= broken("a .bin other than the one the build named", files2=dict(files, **{h + ".bin": other}),
                 rec2=dict(rec, code_hash=I.code_hash(other), image_size=len(other)))
    s1 = {"x.json": (1, 2, "a")}
    for label, after, want_bad in (("unchanged", dict(s1), False), ("mtime changed", {"x.json": (1, 3, "a")}, True),
                                   ("bytes changed", {"x.json": (1, 2, "b")}, True),
                                   ("a file added", dict(s1, **{"y.json": (1, 2, "c")}), True)):
        r = judge_again(0, s1, after)
        good = bool(r) == want_bad
        print("  %s second build, %s: %s" % ("ok  " if good else "FAIL", label, r))
        ok &= good
    for label, args, want in (("refused", (1, False), "PASS"), ("built", (0, False), "FAIL"),
                              ("refused, record folder changed", (1, True), "FAIL"),
                              ("built, trigger missing", (0, False, False), "VOID"),
                              ("refused, trigger missing", (1, False, False), "VOID"),
                              ("refused, after-condition missing", (1, False, True, False), "VOID")):
        v = verdict(*args)[0]
        print("  %s refusal verdict, %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    stats = "cache_miss\t5\ndirect_cache_hit\t2\npreprocessed_cache_hit\t1\ncalled_for_link\t9\nfiles_in_cache\t40\n"
    for label, text, want in (("hits and misses", stats, 8), ("nothing cached", stats.replace("5", "0").replace(
            "2", "0").replace("1", "0"), 0), ("no output", "", 0)):
        n = ccache_compiles(text)
        print("  %s ccache compiles, %s: %d" % ("ok  " if n == want else "FAIL", label, n))
        ok &= n == want
    d = I.scratch("release-cases-selftest")
    os.makedirs(os.path.join(d, "CMakeFiles", "3.30.2"))
    open(os.path.join(d, "CMakeFiles", "3.30.2", "CMakeCCompiler.cmake"), "w").write(
        'set(CMAKE_C_COMPILER_ARG1 "")\nset(CMAKE_C_COMPILER "C:/m/riscv32-esp-elf-gcc.exe")\n')
    for label, build, want in (("found", d, "C:/m/riscv32-esp-elf-gcc.exe"), ("no build folder", d + "_none", None)):
        c = chosen_compiler(build)
        print("  %s chosen compiler, %s: %s" % ("ok  " if c == want else "FAIL", label, c))
        ok &= c == want
    print("\nself-test: %s" % ("every broken input caught, the faithful one passed" if ok else "FAILED"))
    return 0 if ok else 1


# ---------- plumbing ----------

def run(cmd, cwd=None, env=None, log=None, check=False, timeout=7200):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = p.stdout.decode("utf-8", "replace")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write("$ %s  (in %s)\n%s\n[exit %d, %.0f s]\n\n" % (" ".join(cmd), cwd or os.getcwd(), out, p.returncode,
                                                                  time.time() - t0))
    if check and p.returncode:
        raise RuntimeError("%s: exit %d\n%s" % (" ".join(cmd), p.returncode, out[-3000:]))
    return p.returncode, out


def git(repo, *args, env=None):
    e = dict(os.environ, **GIT_ID)
    e.update(env or {})
    return run(["git", "-C", repo] + list(args), env=e, check=True)[1].strip()


def sha256_file(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sha256_lf_bytes(b):
    """SPEC R13: the lock's hash with its line endings normalised to LF (CRLF -> LF)."""
    return hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest()


def snapshot(folder):
    out = {}
    if os.path.isdir(folder):
        for n in sorted(os.listdir(folder)):
            p = os.path.join(folder, n)
            st = os.stat(p)
            out[n] = (st.st_size, st.st_mtime_ns, sha256_file(p))
    return out


def rmtree(p):
    def onerr(func, path, _exc):
        os.chmod(path, 0o700)
        func(path)
    if os.path.exists(p):
        shutil.rmtree(p, onerror=onerr)


def bench_clear(log):
    """AGENTS.md: before a build, the bench lock, the "No builds" line, and running simulators."""
    ri = os.environ.get("REAL_REVERSE_IT_DIR") or ""
    why = []
    bench = os.path.join(os.path.dirname(os.path.dirname(TOOLS)), "shared", "bench")
    if platform.system() != "Windows":
        pass                # the bench and its lock are on NICKY-XPS; a build elsewhere does not touch it
    elif os.path.isdir(os.path.join(bench, "vtrux_bench")):
        _rc, out = run([sys.executable, "-B", "-m", "vtrux_bench.bench_lock", "status"], cwd=bench, env=dict(os.environ))
        if "the lock is free" not in out:
            why.append("bench lock: " + " | ".join(out.strip().splitlines()[:2]))
    else:
        why.append("no shared/bench here to read the lock with")
    holder = os.path.join(ri, "firmware", "bench-holder.md")
    if ri and os.path.exists(holder):
        m = re.search(r"\*\*No builds:\*\*\s*(\S+)", open(holder, encoding="utf-8").read())
        if not m or m.group(1) != "--":
            why.append("No builds line: %s" % (m.group(1) if m else "not found"))
    elif platform.system() == "Windows":
        why.append("REAL_REVERSE_IT_DIR unset or no bench-holder.md: cannot read the No builds line")
    sims = []
    if platform.system() == "Windows":
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.ProcessId -ne $PID } | "
              "ForEach-Object { $_.CommandLine }")
        _rc, out = run(["powershell", "-NoProfile", "-Command", ps])
    else:
        _rc, out = run(["ps", "-eo", "args"])
    for line in out.splitlines():
        if re.search(r"charger_sim|charge-interposer[\\/].*sim|tools[\\/]gen_inhibit[\\/]gi_", line):
            sims.append(line.strip()[:120])
    if sims:
        why.append("simulators running: %s" % sims)
    with open(log, "a", encoding="utf-8") as f:
        f.write("bench check: %s\n\n" % ("clear" if not why else "; ".join(why)))
    return why


class Ctx:
    def __init__(self, a):
        self.root, self.out, self.jobs, self.base = os.path.abspath(a.root), os.path.abspath(a.out), a.jobs, a.base
        self.ri = os.path.join(self.root, "ri")
        self.p1 = os.path.join(self.root, "a")
        self.p2 = os.path.join(self.root, "path_b_longer", "deeper")
        self.mini = os.path.join(self.root, "mini")
        self.src = a.fixture or os.path.join(self.root, "src")
        self.made_fixture = not a.fixture
        self.log = os.path.join(self.out, "driver.log")
        self.shared = {}

    def records(self, project="gen-inhibit"):
        return os.path.join(self.ri, "firmware", "builds", project)


def idf_env(**extra):
    e = dict(os.environ)
    e.pop("REAL_REVERSE_IT_DIR", None)
    e.update(extra)
    return e


def idf(ctx, proj, *args, log=None):
    return run([sys.executable, os.path.join(os.environ["IDF_PATH"], "tools", "idf.py"), "--no-ccache"] + list(args),
               cwd=proj, env=idf_env(), log=log or ctx.log)


def release(ctx, proj, log, jobs=None, check_only=False, env=None, watcher=None):
    e = idf_env(PYTHONPATH=TOOLS, REVERSE_IT_DIR=ctx.ri)
    e.update(env or {})
    for k, v in list(e.items()):
        if v is None:
            del e[k]
    cmd = [sys.executable, "-B", "-m", "vtrux_build", "release", "--project", proj]
    if check_only:
        cmd.append("--check-only")
    if jobs:
        cmd += ["--jobs", str(jobs)]
    with open(log, "a", encoding="utf-8") as f:
        f.write("env: REVERSE_IT_DIR=%s IDF_CCACHE_ENABLE=%s %s\n" % (
            e.get("REVERSE_IT_DIR", "(unset)"), e.get("IDF_CCACHE_ENABLE", "(unset)"),
            " ".join("%s=%s" % (k, v) for k, v in (env or {}).items())))
    th = None
    if watcher:
        th = threading.Thread(target=watcher, daemon=True)
        th.start()
    rc, out = run(cmd, cwd=proj, env=e, log=log)
    return rc, out


def reset(repo, branch, target=None):
    """branch at target (default: origin's copy of the fixture branch it tracks), clean;
    ignored files (build/, managed_components/) are kept."""
    git(repo, "checkout", "-q", "-f", "-B", branch, target or "origin/" + {"other": "fixture"}.get(branch, branch))
    git(repo, "clean", "-q", "-fd")


def report(name, verdict, detail, log):
    RESULTS.append({"case": name, "verdict": verdict, "detail": detail})
    line = "%-28s %-8s %s" % (name, verdict, detail)
    print(line, flush=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write("RESULT " + line + "\n\n")


# ---------- the fixture ----------

def set_repro(proj):
    p = os.path.join(proj, "sdkconfig")
    lines = open(p, newline="").read().replace("\r\n", "\n").split("\n")      # IDF writes CRLF on Windows
    n = lines.count(REPRO_OFF)
    if n != 1 and REPRO_ON not in lines:
        raise RuntimeError("sdkconfig: %d '%s' lines" % (n, REPRO_OFF))
    with open(p, "w", newline="") as f:
        f.write("\n".join(REPRO_ON if x == REPRO_OFF else x for x in lines))


def normalise(ctx, repo, label, fx):
    """Commit the tracked files as a non-release reconfigure rewrites them, and say what changed."""
    rc, _ = idf(ctx, repo, "reconfigure")
    if rc:
        raise RuntimeError("fixture reconfigure failed (%s); see driver.log" % label)
    st = git(repo, "status", "--porcelain")
    fx.write("%s: reconfigure rewrote: %s\n" % (label, st.replace("\n", "; ") or "nothing"))
    if st:
        fx.write(git(repo, "diff", "--stat") + "\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fixture: tracked files as reconfigure writes them")
    rmtree(os.path.join(repo, "build"))


def make_fixture(ctx):
    me = HERE
    while not os.path.exists(os.path.join(me, ".git")):
        me = os.path.dirname(me)
    run(["git", "clone", "-q", "--no-checkout", me, ctx.src], check=True)
    with open(os.path.join(ctx.out, "fixture.txt"), "w") as fx:
        base = git(ctx.src, "rev-parse", ctx.base + "^{commit}")
        fx.write("base: %s (%s)\n" % (base, ctx.base))
        git(ctx.src, "checkout", "-q", "-b", "fixture", base)
        with open(os.path.join(ctx.src, "vtrux_build.json"), "w", newline="") as f:
            f.write('{"project": "gen-inhibit", "shared_components": []}\n')
        set_repro(ctx.src)
        git(ctx.src, "add", "vtrux_build.json", "sdkconfig")
        git(ctx.src, "commit", "-q", "-m", "fixture: vtrux_build.json; CONFIG_APP_REPRODUCIBLE_BUILD on")
        normalise(ctx, ctx.src, "fixture", fx)
        fx.write("fixture: %s\n" % git(ctx.src, "rev-parse", "HEAD"))

        git(ctx.src, "checkout", "-q", "-b", "fx-r11", "fixture")
        with open(os.path.join(ctx.src, "CMakeLists.txt"), "a", newline="") as f:
            f.write("\n# fixture fx-r11: the build deletes its own .bin last (SPEC R11 case)\n"
                    "add_custom_target(fx_r11_rm_bin ALL COMMAND ${CMAKE_COMMAND} -E remove "
                    "${CMAKE_BINARY_DIR}/${CMAKE_PROJECT_NAME}.bin)\n"
                    "add_dependencies(fx_r11_rm_bin app)\n")
        git(ctx.src, "commit", "-q", "-am", "fixture fx-r11: the build deletes its .bin")
        normalise(ctx, ctx.src, "fx-r11", fx)
        fx.write("fx-r11: %s\n" % git(ctx.src, "rev-parse", "HEAD"))

        git(ctx.src, "checkout", "-q", "-b", "fx-r16", "fixture")
        d = os.path.join(ctx.src, "fx_shared", "vtrux_fx_shared")
        os.makedirs(d)
        with open(os.path.join(d, "CMakeLists.txt"), "w", newline="") as f:
            f.write('idf_component_register(SRCS "fx_shared.c")\n')
        with open(os.path.join(d, "fx_shared.c"), "w", newline="") as f:
            f.write("int vtrux_fx_shared(void) { return 1; }\n")
        with open(os.path.join(d, "idf_component.yml"), "w", newline="") as f:
            f.write('version: "1.0.0"\n')
        y = os.path.join(ctx.src, "main", "idf_component.yml")
        s = open(y, newline="").read()
        s = s.replace("dependencies:\n", "dependencies:\n  vtrux/vtrux_fx_shared:\n"
                      "    version: \"1.0.0\"\n    override_path: \"../fx_shared/vtrux_fx_shared\"\n", 1)
        with open(y, "w", newline="") as f:
            f.write(s)
        with open(os.path.join(ctx.src, "vtrux_build.json"), "w", newline="") as f:
            f.write('{"project": "gen-inhibit", "shared_components": ["vtrux/vtrux_fx_shared"]}\n')
        git(ctx.src, "add", "-A")
        git(ctx.src, "commit", "-q", "-m", "fixture fx-r16: a shared component from a local folder")
        normalise(ctx, ctx.src, "fx-r16", fx)
        fx.write("fx-r16: %s\n" % git(ctx.src, "rev-parse", "HEAD"))
        git(ctx.src, "checkout", "-q", "fixture")


def make_mini(ctx):
    rmtree(ctx.mini)
    shutil.copytree(os.path.join(os.environ["IDF_PATH"], "examples", "get-started", "hello_world"), ctx.mini)
    for x in ("pytest_hello_world.py", "sdkconfig.ci"):
        if os.path.exists(os.path.join(ctx.mini, x)):
            os.remove(os.path.join(ctx.mini, x))
    with open(os.path.join(ctx.mini, ".gitignore"), "w", newline="") as f:
        f.write("build/\nmanaged_components/\n")
    with open(os.path.join(ctx.mini, "vtrux_build.json"), "w", newline="") as f:
        f.write('{"project": "vtrux-mini-test", "shared_components": []}\n')
    # a manifest, so the reconfigure writes a dependencies.lock: the tooling refuses a project
    # without one (whether SPEC allows that is with the reviewer, 2026-10-24)
    with open(os.path.join(ctx.mini, "main", "idf_component.yml"), "w", newline="") as f:
        f.write('dependencies:\n  idf:\n    version: ">=5.0"\n')
    run(["git", "init", "-q", "-b", "main", ctx.mini], check=True)
    rc, _ = idf(ctx, ctx.mini, "set-target", "esp32c3")
    if rc:
        raise RuntimeError("mini: set-target failed")
    set_repro(ctx.mini)
    rmtree(os.path.join(ctx.mini, "build"))
    git(ctx.mini, "add", "-A")
    git(ctx.mini, "commit", "-q", "-m", "mini: hello_world, esp32c3, reproducible build on")
    with open(os.path.join(ctx.out, "fixture.txt"), "a") as fx:
        normalise(ctx, ctx.mini, "mini", fx)
        fx.write("mini: %s\n" % git(ctx.mini, "rev-parse", "HEAD"))


def clones(ctx):
    if not os.path.exists(ctx.p1):
        run(["git", "clone", "-q", "-b", "fixture", ctx.src, ctx.p1], check=True)
    if not os.path.exists(ctx.p2):
        os.makedirs(os.path.dirname(ctx.p2), exist_ok=True)
        run(["git", "clone", "-q", "--no-checkout", ctx.src, ctx.p2], check=True)
        git(ctx.p2, "checkout", "-q", "-b", "other", "origin/fixture")


# ---------- what the tester reads itself ----------

def facts_for(ctx, proj, project, shared):
    bd = os.path.join(proj, "build")
    pd = json.load(open(os.path.join(bd, "project_description.json")))
    b = open(os.path.join(bd, pd["app_bin"]), "rb").read()
    import vtrux_build.digest as D
    import vtrux_build.builddir as B
    comps = B.BuildDir(bd).components()
    pick = {n: D.component_digest(d) for n, d in comps.items() if n in ("main", "espressif__mdns", "mdns")}
    gcc = run([pd.get("c_compiler") or "riscv32-esp-elf-gcc", "--version"])[1].splitlines()[0].strip()
    idf_describe = run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip()
    tools_wt = git(TOOLS, "rev-parse", "--show-toplevel")
    return {"app_bin_sha256": hashlib.sha256(b).hexdigest(), "commit": git(proj, "rev-parse", "HEAD"),
            "project": project, "system": platform.system(), "toolchain": gcc,
            "idf_image": I.field(b, "idf_ver").rstrip(b"\0").decode(), "idf_describe": idf_describe,
            "lock_sha256": (sha256_lf_bytes(open(os.path.join(proj, "dependencies.lock"), "rb").read())
                            if os.path.exists(os.path.join(proj, "dependencies.lock")) else None),
            "component_digests": pick, "shared_components": shared,
            "tooling": {"tag": None, "commit": git(TOOLS, "rev-parse", "HEAD"),
                        "dirty": bool(git(tools_wt, "status", "--porcelain")), "digest": D.component_digest(TOOLS)}}


def own_checks(proj):
    """R9 and R10 read from the build, by the tester: CCACHE_ENABLE, ccache in any compile
    rule, and each managed component's .component_hash against dependencies.lock."""
    bad = []
    bd = os.path.join(proj, "build")
    for d, _dirs, files in os.walk(bd):
        if "CMakeCache.txt" in files:
            m = re.search(r"^CCACHE_ENABLE:[A-Z]+=(.*)$", open(os.path.join(d, "CMakeCache.txt")).read(), re.M)
            if m and m.group(1).strip().upper() not in ("0", "OFF", "FALSE", "NO", "N"):
                bad.append("%s: CCACHE_ENABLE=%s" % (os.path.relpath(d, proj), m.group(1)))
        for n in files:
            if n.endswith(".ninja"):
                if re.search(r"ccache", open(os.path.join(d, n), errors="replace").read(), re.I):
                    bad.append("ccache named in %s" % os.path.relpath(os.path.join(d, n), proj))
    lp = os.path.join(proj, "dependencies.lock")
    lock = open(lp).read() if os.path.exists(lp) else ""
    mc = os.path.join(proj, "managed_components")
    for name in (os.listdir(mc) if os.path.isdir(mc) else []):
        ch = os.path.join(mc, name, ".component_hash")
        if os.path.exists(ch):
            h = open(ch).read().strip()
            if "component_hash: " + h not in lock:
                bad.append("%s/.component_hash %s not in dependencies.lock" % (name, h[:12]))
    return bad


# ---------- the cases ----------

def verdict(rc, changed, trigger=True, extra_ok=True, note=""):
    """A refusal case's verdict. trigger: the case's stimulus happened (else VOID, whatever the
    exit code); extra_ok: what else the case needs after a refusal (else VOID)."""
    if not trigger:
        return "VOID", "the trigger did not happen (exit %d)" % rc + ("; " + note if note else "")
    if rc == 0:
        return "FAIL", "built and exited 0; SPEC says refuse" + (" (%s)" % note if note else "")
    if changed:
        return "FAIL", "exit %d, but the record folder changed" % rc
    if not extra_ok:
        return "VOID", note
    return "PASS", "refused (exit %d), no record written%s" % (rc, "; " + note if note else "")


def refusal(ctx, name, proj, rc, before, project="gen-inhibit", trigger=True, extra_ok=True, note=""):
    v, detail = verdict(rc, before != snapshot(ctx.records(project)), trigger, extra_ok, note)
    report(name, v, detail, ctx.log)


def c_pre_unset_ri(ctx, log):
    reset(ctx.p1, "fixture")
    rmtree(os.path.join(ctx.p1, "build"))
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, env={"REVERSE_IT_DIR": None})
    built = os.path.exists(os.path.join(ctx.p1, "build"))
    refusal(ctx, "pre_unset_ri", ctx.p1, rc, before, extra_ok=not built,
            note="R13: REVERSE_IT_DIR unset; build/ %s" % ("CREATED: not stopped before building" if built else "never created"))
    if built:
        RESULTS[-1]["verdict"] = "FAIL"


def c_pre_overwrite(ctx, log):
    reset(ctx.p1, "fixture")
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, check_only=True, env={"IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS": "0"})
    refusal(ctx, "pre_overwrite", ctx.p1, rc, before, note="R10: the overwrite setting set, to 0")


def c_pre_untracked(ctx, log):
    reset(ctx.p1, "fixture")
    open(os.path.join(ctx.p1, "untracked_probe.txt"), "w").write("x\n")
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, check_only=True)
    refusal(ctx, "pre_untracked", ctx.p1, rc, before, note="R8: an untracked file")
    reset(ctx.p1, "fixture")


def c_pre_modified(ctx, log):
    reset(ctx.p1, "fixture")
    with open(os.path.join(ctx.p1, "README.md"), "a") as f:
        f.write("\nprobe\n")
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, check_only=True)
    refusal(ctx, "pre_modified", ctx.p1, rc, before, note="R8: a tracked file modified (README.md)")
    reset(ctx.p1, "fixture")


def check_success(ctx, name, proj, rc, before, project="gen-inhibit", shared=None):
    after = snapshot(ctx.records(project))
    new = sorted(set(after) - set(before))
    if rc:
        report(name, "FAIL", "exit %d; SPEC says this build is a release build" % rc, ctx.log)
        return None
    note = ""
    if not new:
        # the same build recorded by an earlier run (the fixture's commits are deterministic):
        # R13 then writes nothing new, so judge the record whose commit is this HEAD
        head = git(proj, "rev-parse", "HEAD")
        for n in after:
            if n.endswith(".json") and json.load(open(os.path.join(ctx.records(project), n))).get("commit") == head:
                new = [n[:-5] + x for x in (".bin", ".elf", ".json")]
                note = " (recorded by an earlier run; none new)"
    if len(new) != 3:
        report(name, "FAIL", "exit 0 but new record files %s, want 3" % new, ctx.log)
        return None
    jn = [n for n in new if n.endswith(".json")][0]
    rec = json.load(open(os.path.join(ctx.records(project), jn)))
    files = {n: open(os.path.join(ctx.records(project), n), "rb").read() for n in new}
    bad = judge_record(rec, files, facts_for(ctx, proj, project, shared or {}))
    bad += own_checks(proj)
    report(name, "PASS" if not bad else "FAIL", "record %s; code %s%s%s" % (
        rec["image_hash"][:16], rec["code_hash"][:16], note, "" if not bad else "; " + "; ".join(bad)), ctx.log)
    return rec


def c_a_release(ctx, log):
    reset(ctx.p1, "fixture")
    rmtree(os.path.join(ctx.p1, "build"))
    before = snapshot(ctx.records())
    with open(log, "a") as f:
        f.write("IDF_CCACHE_ENABLE in the exported environment: %s\n" % os.environ.get("IDF_CCACHE_ENABLE", "(unset)"))
    rc, _ = release(ctx, ctx.p1, log)       # idf.py build path
    ctx.shared["a"] = check_success(ctx, "a_release", ctx.p1, rc, before)


def c_a_again(ctx, log):
    reset(ctx.p1, "fixture")
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs)
    r = judge_again(rc, before, snapshot(ctx.records()))
    report("a_again", "PASS" if not r else ("FINDING" if r.startswith("FINDING") else "FAIL"),
           r or "exit %d; %s; the record folder's bytes and mtimes unchanged" % (rc, "skipped" if rc == 0 else "refused"),
           ctx.log)


def need_a(ctx, name):
    a = ctx.shared.get("a")
    if not a:
        rec_dir = ctx.records()
        js = [n for n in os.listdir(rec_dir) if n.endswith(".json")] if os.path.isdir(rec_dir) else []
        for n in js:
            r = json.load(open(os.path.join(rec_dir, n)))
            if r.get("commit") == git(ctx.src, "rev-parse", "fixture"):
                ctx.shared["a"] = a = r
    if not a:
        report(name, "VOID", "no a_release record to compare with", ctx.log)
    return a


def differing(a_bin, b_bin):
    """Which descriptor fields differ, and how many bytes differ after R2's blanking outside
    project_name (the name stamp)."""
    out = [f for f in I.FIELDS if I.field(a_bin, f) != I.field(b_bin, f)]
    if len(a_bin) != len(b_bin):
        return "fields %s; sizes differ" % out

    def blank(d):
        x = bytearray(d)
        for o, n in I.blanks(d) + [I.FIELDS["project_name"]]:
            x[o:o + n] = bytes(n)
        return x
    x, y = blank(a_bin), blank(b_bin)
    return "fields differing %s; bytes differing outside them and R2's blanks: %d" % (
        out, sum(1 for i in range(len(x)) if x[i] != y[i]))


def rec_bin(ctx, rec):
    return open(os.path.join(ctx.records(), rec["image_hash"] + ".bin"), "rb").read()


def c_r5_p2_same_commit(ctx, log):
    a = need_a(ctx, "r5_p2_same_commit")
    if not a:
        return
    reset(ctx.p2, "other")
    rmtree(os.path.join(ctx.p2, "build"))
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p2, log, jobs=ctx.jobs)
    after = snapshot(ctx.records())
    if rc:
        report("r5_p2_same_commit", "FAIL", "exit %d" % rc, ctx.log)
        return
    img = None
    for n in sorted(set(after) - set(before)) or [a["image_hash"] + ".json"]:
        if n.endswith(".json"):
            img = n[:-5]
    ok = img == a["image_hash"]
    report("r5_p2_same_commit", "PASS" if ok else "FAIL",
           "same commit, branch 'other', clone path %s: image hash %s, a_release's %s%s" % (
               os.path.relpath(ctx.p2, ctx.root), (img or "?")[:16], a["image_hash"][:16],
               " (the same record, so none new)" if ok and after == before else ""), ctx.log)


def c_r5_p2_new_commit(ctx, log):
    a = need_a(ctx, "r5_p2_new_commit")
    if not a:
        return
    reset(ctx.p2, "other")
    git(ctx.p2, "commit", "-q", "--allow-empty", "-m", "fixture: same files, another commit")
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p2, log, jobs=ctx.jobs)
    rec = check_success(ctx, "r5_p2_new_commit_record", ctx.p2, rc, before)
    git(ctx.p2, "reset", "-q", "--hard", "origin/fixture")
    if not rec:
        report("r5_p2_new_commit", "VOID", "no record", ctx.log)
        return
    same_code = rec["code_hash"] == a["code_hash"]
    report("r5_p2_new_commit", "PASS" if same_code and rec["image_hash"] != a["image_hash"] else "FAIL",
           "identical files, another commit: code hash %s vs a_release %s (%s); image hash %s vs %s; %s" % (
               rec["code_hash"][:16], a["code_hash"][:16], "same" if same_code else "DIFFERENT",
               rec["image_hash"][:16], a["image_hash"][:16], differing(rec_bin(ctx, a), rec_bin(ctx, rec))), ctx.log)


def c_bench_dirty(ctx, log):
    a = need_a(ctx, "bench_dirty")
    if not a:
        return
    reset(ctx.p1, "fixture")
    with open(os.path.join(ctx.p1, "README.md"), "a") as f:
        f.write("\nbench build probe\n")
    e = idf_env()
    rc, _ = run([sys.executable, os.path.join(os.environ["IDF_PATH"], "tools", "idf.py"), "build"], cwd=ctx.p1, env=e, log=log)
    reset(ctx.p1, "fixture")
    if rc:
        report("bench_dirty", "FAIL", "the bench build (idf.py build, tracked README.md modified) failed: exit %d" % rc, ctx.log)
        return
    bd = os.path.join(ctx.p1, "build")
    pd = json.load(open(os.path.join(bd, "project_description.json")))
    b = open(os.path.join(bd, pd["app_bin"]), "rb").read()
    same = I.code_hash(b) == a["code_hash"]
    report("bench_dirty", "PASS" if same else "FAIL",
           "R14/R3: bench build in a modified tree (%s, version %s): code hash %s vs a_release %s (%s); %s" % (
               pd["app_bin"], I.field(b, "version").rstrip(b"\0").decode(), I.code_hash(b)[:16], a["code_hash"][:16],
               "same" if same else "DIFFERENT", differing(rec_bin(ctx, a), b)), ctx.log)


def mdns_dir(ctx):
    mc = os.path.join(ctx.p1, "managed_components")
    for n in os.listdir(mc) if os.path.isdir(mc) else []:
        if "mdns" in n:
            return os.path.join(mc, n)
    return None


def c_r10(ctx, log, name, mutate, note):
    reset(ctx.p1, "fixture")
    if not mdns_dir(ctx):
        idf(ctx, ctx.p1, "reconfigure", log=log)       # fetch it again (a non-release reconfigure)
    d = mdns_dir(ctx)
    if not d:
        report(name, "VOID", "no managed mdns component (run a_release first)", ctx.log)
        return
    what = mutate(d)
    if what is None:
        report(name, "VOID", note + ": precondition missing", ctx.log)
        return
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs)
    refusal(ctx, name, ctx.p1, rc, before, note=note + ": " + what)
    rmtree(d)


def mut_file(d):
    for root, _dirs, files in os.walk(d):
        for n in sorted(files):
            if n.endswith(".c"):
                with open(os.path.join(root, n), "a", newline="") as f:
                    f.write("\n/* tester probe */\n")
                return os.path.relpath(os.path.join(root, n), d)
    return None


def mut_hash(d):
    p = os.path.join(d, ".component_hash")
    if not os.path.exists(p):
        return None
    h = open(p).read().strip()
    open(p, "w").write(("0" if h[0] != "0" else "1") + h[1:])
    return ".component_hash first digit changed"


def mut_extra(d):
    if not os.path.exists(os.path.join(d, "CHECKSUMS.json")):
        return None
    open(os.path.join(d, "tester_extra.txt"), "w").write("x\n")
    return "tester_extra.txt added (not in CHECKSUMS.json)"


def c_r9(ctx, log, name, var):
    reset(ctx.p1, "fixture")
    rmtree(os.path.join(ctx.p1, "build"))
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs, env={var: "ccache"})
    took = False
    cache = os.path.join(ctx.p1, "build", "CMakeCache.txt")
    if os.path.exists(cache):
        took = bool(re.search(r"^%s:[A-Z]+=.*ccache" % re.escape(var), open(cache).read(), re.M))
    for d, _x, files in os.walk(os.path.join(ctx.p1, "build")):
        for n in files:
            if n.endswith(".ninja") and "ccache" in open(os.path.join(d, n), errors="replace").read():
                took = True
    refusal(ctx, name, ctx.p1, rc, before, trigger=took,
            note="R9: %s=ccache in the environment; %s" % (var, "CMake took it" if took else "CMake did not take it"))


def c_r9_masquerade(ctx, log, case="r9_masquerade", hardlink=False):
    """ccache in the compiler's place on PATH: a link named after the compiler (Linux), or a copy
    of ccache.exe under that name (Windows, where ccache's docs masquerade by copying). With
    hardlink, a hard link to ccache under that name on either host: the same file, not a copy."""
    reset(ctx.p1, "fixture")
    rmtree(os.path.join(ctx.p1, "build"))
    cc = shutil.which("ccache")
    if not cc:
        report(case, "VOID", "R9: no ccache on this host to masquerade with", ctx.log)
        return
    masq = os.path.join(ctx.root, "masq")
    rmtree(masq)
    os.makedirs(masq)
    win = platform.system() == "Windows"
    how = "a hard link" if hardlink else "a copy" if win else "a link"
    for name in ("riscv32-esp-elf-gcc", "riscv32-esp-elf-g++"):
        dst = os.path.join(masq, name + (".exe" if win else ""))
        if hardlink:
            try:
                os.link(cc, dst)
            except OSError as e:
                report(case, "VOID", "R9: cannot hard-link ccache here (%s)" % e, ctx.log)
                rmtree(masq)
                return
            if not os.path.samefile(cc, dst):
                report(case, "VOID", "R9: the hard link is not ccache's own file", ctx.log)
                rmtree(masq)
                return
        elif win:
            shutil.copyfile(cc, dst)
        else:
            os.symlink(cc, dst)
    # Its own cache folder, so that what it cached is this build's alone.
    cdir = os.path.join(ctx.root, "masq_cache")
    rmtree(cdir)
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs,
                    env={"PATH": masq + os.pathsep + os.environ["PATH"], "CCACHE_DIR": cdir})
    chosen = chosen_compiler(os.path.join(ctx.p1, "build"))
    took = bool(chosen) and os.path.normcase(os.path.abspath(chosen)).startswith(os.path.normcase(masq))
    stats = subprocess.run([cc, "--print-stats"], env=dict(os.environ, CCACHE_DIR=cdir),
                           capture_output=True, text=True).stdout
    cached = ccache_compiles(stats)
    note = "R9: ccache as %s named riscv32-esp-elf-gcc first on PATH; CMake chose %s; ccache cached %d compiles" % (
        how, chosen or "nothing found", cached)
    # A build that exits 0 with nothing cached may have run ccache disabled: not a finding either way.
    refusal(ctx, case, ctx.p1, rc, before, trigger=took and (rc != 0 or cached > 0), note=note)
    rmtree(masq)
    rmtree(cdir)


def chosen_compiler(build):
    """The C compiler CMake chose, from CMakeFiles/<version>/CMakeCCompiler.cmake."""
    for p in glob.glob(os.path.join(build, "CMakeFiles", "*", "CMakeCCompiler.cmake")):
        m = re.search(r'^set\(CMAKE_C_COMPILER "([^"]*)"\)', open(p).read(), re.M)
        if m:
            return m.group(1)
    return None


def ccache_compiles(stats):
    """Compiles ccache handled through its cache (hits and misses), from --print-stats."""
    n = dict(l.split("\t", 1) for l in stats.splitlines() if "\t" in l)
    return sum(int(n.get(k, 0)) for k in ("direct_cache_hit", "preprocessed_cache_hit", "cache_miss"))


def c_pre_staged(ctx, log):
    reset(ctx.p1, "fixture")
    open(os.path.join(ctx.p1, "staged_probe.txt"), "w").write("x\n")
    git(ctx.p1, "add", "staged_probe.txt")
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, check_only=True)
    refusal(ctx, "pre_staged", ctx.p1, rc, before, note="R8: a new file staged, not committed")
    git(ctx.p1, "reset", "-q")
    reset(ctx.p1, "fixture")


def c_pre_deleted(ctx, log):
    reset(ctx.p1, "fixture")
    os.remove(os.path.join(ctx.p1, "main", "main.c"))
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, check_only=True)
    refusal(ctx, "pre_deleted", ctx.p1, rc, before, note="R8: a tracked file deleted (main/main.c)")
    reset(ctx.p1, "fixture")


def c_r8_rewrites(ctx, log):
    """The fixture's first commit, before its sdkconfig was committed as the reconfigure writes
    it: the release build's own reconfigure rewrites a tracked file."""
    reset(ctx.p1, "fx-r8", "origin/fixture~1")
    rmtree(os.path.join(ctx.p1, "build"))
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs)
    st = git(ctx.p1, "status", "--porcelain")
    compiled = ninja_lines(ctx.p1)
    refusal(ctx, "r8_reconfigure_rewrites", ctx.p1, rc, before, extra_ok="sdkconfig" in st and compiled == 0,
            note="R8: committed sdkconfig the reconfigure rewrites; after: status '%s', ninja log lines %d" % (
                st.replace("\n", "; "), compiled))
    reset(ctx.p1, "fixture")
    rmtree(os.path.join(ctx.p1, "build"))


def c_r10_no_lock(ctx, log):
    """A project that downloads nothing and has no manifest, so no dependencies.lock: refused
    (SPEC R10, 0d2431b)."""
    if not os.path.exists(os.path.join(ctx.mini, ".git")):
        make_mini(ctx)
    if not git(ctx.mini, "branch", "--list", "nolock"):
        git(ctx.mini, "checkout", "-q", "-b", "nolock", "main")
        git(ctx.mini, "rm", "-q", os.path.join("main", "idf_component.yml"), "dependencies.lock")
        git(ctx.mini, "commit", "-q", "-m", "mini nolock: no manifest, no dependencies.lock")
    reset(ctx.mini, "nolock", "nolock")
    rmtree(os.path.join(ctx.mini, "build"))
    before = snapshot(ctx.records("vtrux-mini-test"))
    rc, out = release(ctx, ctx.mini, log, jobs=ctx.jobs)
    reconfigured = os.path.exists(os.path.join(ctx.mini, "build", "CMakeCache.txt"))
    no_lock = not os.path.exists(os.path.join(ctx.mini, "dependencies.lock"))
    refusal(ctx, "r10_no_lock", ctx.mini, rc, before, project="vtrux-mini-test",
            extra_ok=reconfigured and no_lock and ninja_lines(ctx.mini) == 0,
            note="R10: no manifest; after the reconfigure: dependencies.lock %s, ninja log lines %d" % (
                "absent" if no_lock else "PRESENT", ninja_lines(ctx.mini)))
    reset(ctx.mini, "main", "main")


def watch_then(cond, act, fired, timeout=3000):
    def w():
        t0 = time.time()
        while time.time() - t0 < timeout:
            if cond():
                act()
                fired.append(time.time())
                return
            time.sleep(0.2)
    return w


def ninja_lines(proj):
    p = os.path.join(proj, "build", ".ninja_log")
    try:
        with open(p, "rb") as f:
            return f.read().count(b"\n")
    except OSError:
        return 0


def c_r8_reconf(ctx, log):
    reset(ctx.p1, "fixture")
    rmtree(os.path.join(ctx.p1, "build"))
    fired = []
    cache = os.path.join(ctx.p1, "build", "CMakeCache.txt")
    w = watch_then(lambda: os.path.exists(cache),
                   lambda: open(os.path.join(ctx.p1, "r8_probe.txt"), "w").write("x\n"), fired)
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs, watcher=w)
    compiled = ninja_lines(ctx.p1)
    ok = bool(fired) and compiled == 0
    refusal(ctx, "r8_during_reconfigure", ctx.p1, rc, before, extra_ok=ok,
            note="R8: an untracked file made during the reconfigure; watcher %s; ninja log lines after: %d" % (
                "fired" if fired else "NEVER FIRED", compiled))
    if fired and compiled and RESULTS[-1]["verdict"] != "FAIL":
        RESULTS[-1]["verdict"] = "FAIL"
        RESULTS[-1]["detail"] += " (compiled before refusing)"
    reset(ctx.p1, "fixture")


def c_r8_build(ctx, log, name, act, note):
    reset(ctx.p1, "fixture")
    rmtree(os.path.join(ctx.p1, "build"))
    fired = []
    w = watch_then(lambda: ninja_lines(ctx.p1) > 100, act, fired)
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs, watcher=w)
    refusal(ctx, name, ctx.p1, rc, before, trigger=bool(fired),
            note="%s; watcher %s at ninja log line > 100" % (note, "fired" if fired else "NEVER FIRED"))
    reset(ctx.p1, "fixture")


def c_r11(ctx, log):
    reset(ctx.p1, "fx-r11")
    rmtree(os.path.join(ctx.p1, "build"))
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs)
    pdp = os.path.join(ctx.p1, "build", "project_description.json")
    gone = None
    if os.path.exists(pdp):
        pd = json.load(open(pdp))
        gone = not os.path.exists(os.path.join(ctx.p1, "build", pd.get("app_bin", "?")))
    ninja_failed = "FAILED:" in open(log, encoding="utf-8", errors="replace").read()
    refusal(ctx, "r11_bin_missing", ctx.p1, rc, before, extra_ok=bool(gone) and not ninja_failed,
            note="R11: the build deletes the .bin project_description.json names; after: %s; ninja %s" % (
                "missing" if gone else "PRESENT or no description",
                "FAILED (refused for another reason)" if ninja_failed else "succeeded"))
    reset(ctx.p1, "fixture")


def c_r16(ctx, log):
    reset(ctx.p1, "fx-r16")
    before = snapshot(ctx.records())
    rc, _ = release(ctx, ctx.p1, log, jobs=ctx.jobs)
    lock = open(os.path.join(ctx.p1, "dependencies.lock")).read()
    local = "vtrux/vtrux_fx_shared" in lock and "type: local" in lock
    refusal(ctx, "r16_override_path", ctx.p1, rc, before, trigger=local,
            note="R16: shared component vtrux/vtrux_fx_shared by override_path; lock source local: %s" % local)
    reset(ctx.p1, "fixture")
    rmtree(os.path.join(ctx.p1, "build"))


def c_r22_mini(ctx, log):
    if not os.path.exists(os.path.join(ctx.mini, ".git")):
        make_mini(ctx)
    reset(ctx.mini, "main", "main")
    rmtree(os.path.join(ctx.mini, "build"))
    before = snapshot(ctx.records("vtrux-mini-test"))
    rc, _ = release(ctx, ctx.mini, log, jobs=ctx.jobs)
    check_success(ctx, "r22_mini", ctx.mini, rc, before, project="vtrux-mini-test")


def c_r13_lock_crlf(ctx, log):
    """SPEC R13 (5ec6f53): the lock's hash is taken with its line endings as LF, so one lock gives
    one hash in any checkout. The mini project is built twice, its committed lock (LF in git) on
    disk first as CRLF and then as LF, each into an empty record folder (the earlier one is moved
    aside, since the same image is never recorded twice). Both records must give the sha256 of
    the committed lock."""
    name = "r13_lock_crlf"
    if not os.path.exists(os.path.join(ctx.mini, ".git")):
        make_mini(ctx)
    reset(ctx.mini, "main", "main")
    lp = os.path.join(ctx.mini, "dependencies.lock")
    blob = subprocess.run(["git", "-C", ctx.mini, "show", "HEAD:dependencies.lock"], capture_output=True).stdout
    if not blob or b"\r" in blob:
        report(name, "VOID", "the committed lock is missing or already has CR bytes", ctx.log)
        return
    want = hashlib.sha256(blob).hexdigest()
    got = {}
    for ending, data in (("CRLF", blob.replace(b"\n", b"\r\n")), ("LF", blob)):
        recs = ctx.records("vtrux-mini-test")
        if os.path.exists(recs):
            os.rename(recs, "%s.aside-%d" % (recs, time.time_ns()))
        rmtree(os.path.join(ctx.mini, "build"))
        with open(lp, "wb") as f:
            f.write(data)
        st = git(ctx.mini, "status", "--porcelain")
        if st:
            report("%s (%s)" % (name, ending), "VOID", "git status shows the lock changed: %s" % st, ctx.log)
            continue
        before = snapshot(recs)
        rc, _ = release(ctx, ctx.mini, log, jobs=ctx.jobs)
        crlf_after = b"\r\n" in open(lp, "rb").read()
        with open(log, "a") as f:
            f.write("lock on disk before the build: %s; after: %s\n" % (ending, "CRLF" if crlf_after else "LF"))
        rec = check_success(ctx, "%s (%s)" % (name, ending), ctx.mini, rc, before, project="vtrux-mini-test")
        if rec:
            got[ending] = (rec.get("dependencies_lock_sha256_lf"), crlf_after == (ending == "CRLF"))
    if set(got) != {"CRLF", "LF"}:
        report(name, "VOID", "needs both builds recorded; got %s" % sorted(got), ctx.log)
    elif not all(kept for _h, kept in got.values()):
        report(name, "VOID", "a build changed the lock's line endings on disk", ctx.log)
    elif all(h == want for h, _k in got.values()):
        report(name, "PASS", "CRLF and LF on disk: both records give the committed lock's sha256 %s" % want[:16], ctx.log)
    else:
        report(name, "FAIL", "records give %s (CRLF on disk) and %s (LF); the committed lock's sha256 is %s" % (
            str(got["CRLF"][0])[:16], str(got["LF"][0])[:16], want[:16]), ctx.log)
    reset(ctx.mini, "main", "main")


CASES = [
    ("pre_unset_ri", c_pre_unset_ri),
    ("pre_overwrite", c_pre_overwrite),
    ("pre_untracked", c_pre_untracked),
    ("pre_modified", c_pre_modified),
    ("pre_staged", c_pre_staged),
    ("pre_deleted", c_pre_deleted),
    ("a_release", c_a_release),
    ("a_again", c_a_again),
    ("r5_p2_same_commit", c_r5_p2_same_commit),
    ("r5_p2_new_commit", c_r5_p2_new_commit),
    ("bench_dirty", c_bench_dirty),
    ("r10_file_modified", lambda c, l: c_r10(c, l, "r10_file_modified", mut_file, "R10: a managed component file changed")),
    ("r10_component_hash", lambda c, l: c_r10(c, l, "r10_component_hash", mut_hash, "R10: .component_hash changed")),
    ("r10_extra_file", lambda c, l: c_r10(c, l, "r10_extra_file", mut_extra, "R10: a file CHECKSUMS.json does not list")),
    ("r9_launcher_c", lambda c, l: c_r9(c, l, "r9_launcher_c", "CMAKE_C_COMPILER_LAUNCHER")),
    ("r9_launcher_cxx", lambda c, l: c_r9(c, l, "r9_launcher_cxx", "CMAKE_CXX_COMPILER_LAUNCHER")),
    ("r9_masquerade", c_r9_masquerade),
    ("r9_masquerade_hardlink", lambda c, l: c_r9_masquerade(c, l, "r9_masquerade_hardlink", hardlink=True)),
    ("r8_reconfigure_rewrites", c_r8_rewrites),
    ("r10_no_lock", c_r10_no_lock),
    ("r8_during_reconfigure", c_r8_reconf),
    ("r8_build_untracked", lambda c, l: c_r8_build(c, l, "r8_build_untracked",
                                                   lambda: open(os.path.join(c.p1, "r8_probe.txt"), "w").write("x\n"),
                                                   "R8: an untracked file made during the build")),
    ("r8_build_head", lambda c, l: c_r8_build(c, l, "r8_build_head",
                                              lambda: git(c.p1, "commit", "-q", "--allow-empty", "-m", "moved"),
                                              "R8: HEAD moved (an empty commit) during the build")),
    ("r11_bin_missing", c_r11),
    ("r16_override_path", c_r16),
    ("r22_mini", c_r22_mini),
    ("r13_lock_crlf", c_r13_lock_crlf),
]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--base", default="gen-inhibit/main")
    ap.add_argument("--fixture")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.root and a.out) or "IDF_PATH" not in os.environ:
        ap.error("--root and --out, from an exported ESP-IDF shell")
    ctx = Ctx(a)
    os.makedirs(ctx.out, exist_ok=True)
    os.makedirs(os.path.join(ctx.ri, "firmware"), exist_ok=True)
    names = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s  host %s  %s  python %s\nIDF %s  tooling %s (%s)\ncases %s\n\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S"), platform.node(), platform.platform(), platform.python_version(),
            run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip(),
            git(TOOLS, "rev-parse", "HEAD"), git(TOOLS, "status", "--porcelain") or "clean", names))
    if not os.path.exists(ctx.src):
        if not ctx.made_fixture:
            ap.error("--fixture %s does not exist" % ctx.src)
        make_fixture(ctx)
    clones(ctx)
    for n, fn in CASES:
        if n not in names:
            continue
        why = bench_clear(ctx.log)
        if why:
            report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "case_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    with open(os.path.join(ctx.out, "results.json"), "a") as f:
        for r in RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RESULTS if r["verdict"] != "PASS"]
    print("\n%d case(s), %d not PASS: %s" % (len(RESULTS), len(bad), ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
