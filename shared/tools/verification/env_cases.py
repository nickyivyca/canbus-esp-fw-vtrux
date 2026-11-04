#!/usr/bin/env python3
"""SPEC R24's "no default", R31, R32 and R33 (shared/main ceac030), against the tooling at this
checkout's HEAD.

    (from an exported ESP-IDF shell, with its Python; --root made by release_cases.py)
    python -B env_cases.py --root <release_cases' --root> --out <run folder> [--cases a,b] [--jobs N]
                           [--other-idf <file>]
    python -B env_cases.py --dump-env <file>    # in a shell exported for the OTHER ESP-IDF install
    python -B env_cases.py --self-test          # the judges, and the R32 cases against fake tooling

Like scope_cases.py, it runs in release_cases.py's --root: the fixture repo tags the tooling
under test, and the cases use release_cases' minimal project (hello_world on esp32c3, made if
missing), on branches of their own, with the tooling run from the pinned checkout (R23).

R32 (the IDF environment check). check-env with the caller's exported environment passes, and
with a trailing separator on IDF_TOOLS_PATH. Each of these is refused, naming the variable:
IDF_PATH unset and empty (empty refused as unset: the gen-inhibit reviewer, 2026-11-03, R32 to
read "set and not empty"); IDF_TOOLS_PATH unset (never replaced by ~/.espressif: R24, R32) and empty;
IDF_PYTHON_ENV_PATH and ESP_ROM_ELF_DIR unset, outside IDF_TOOLS_PATH in a folder whose name
starts like it, and outside through "..", and IDF_PYTHON_ENV_PATH in a sibling ".espressif"
(the other install's, on a machine with two). Before each, a child process run with the same
environment prints what it sees of the four variables; a case whose planted state does not show
there is VOID. A refusal that is a Python traceback is a FAIL, not a refusal. Then R32 before a
release build and a dev build (R31): IDF_TOOLS_PATH unset, and ESP_ROM_ELF_DIR outside, must be
refused naming the variable, with no record written and no build folder made (nothing handed to
ESP-IDF).

R24. A release build passes the caller's IDF_TOOLS_PATH as it is: the configure prints
$ENV{IDF_TOOLS_PATH} and must print the caller's value (r33_release_eq's build).

R31 (dev builds). The minimal project's branch r31 lists two variants in vtrux_build.json,
each with one -D option. A variant not listed is refused, the output naming each listed one,
and no build folder made; a project that lists none has no build command (refused, no build
folder). `build <variant> --jobs 3` builds in build/<variant> only (its CMakeCache.txt holds
that variant's option and not the other's; no build/CMakeCache.txt; the app_bin its
project_description.json names exists), and every ninja the tooling starts (its descendants
with no ninja or cmake between: the bootloader's ninja runs under cmake and is not the
tooling's) runs with -j 3, read from the process table while the build runs; without --jobs,
none has a -j. A case where no such ninja was seen is VOID. "Nothing is added to the caller's
environment": the configure writes `cmake -E environment` to a file, and the dev build's must
equal a plain `idf.py reconfigure` run with the same environment, name for name and value for
value.

R33 (exact IDF version). The can-fail, with --other-idf (the other install's exported
variables, written by --dump-env in a shell exported for it): a copy of the minimal project
naming this IDF's version, as "M.m.p" and as "==M.m.p", must configure under this install and
must fail to configure under the other; VOID unless the other install configures the same
project naming its own version. Each form is run twice: with no dependencies.lock in the copy
(_fresh: the Component Manager solves the manifest), and with the lock this install wrote for
that same manifest (_locked: as a project commits it, R10; on 2026-11-04 a copy carrying the
minimal project's lock configured under v5.4.1, where a changed manifest was refused, so the
two states are judged apart). A copy whose lock state is not the one planted is VOID. And a
release build is refused for a manifest naming ">=5.4.0", "*", ">=M.m.p,<M.m.(p+1)" or no IDF;
one naming "==M.m.p" builds and is recorded (release_cases' r22_mini is the bare form's); its
branch commits the lock the reconfigure writes for its manifest, as release_cases' fixture does.

Each case judges from outside: exit codes, output names (only that the refusal names the
variable R32 requires it to), the record folder, the build folder, the process table. It never
judges other wording. Before each case: the bench lock, the "No builds" line, simulators
(release_cases.bench_clear).

Gen-inhibit tester, 2026-11-03 (gen-inhibit port step 1d).
"""
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
import release_cases as R  # noqa: E402

R32_VARS = ("IDF_PATH", "IDF_TOOLS_PATH", "IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR")
OTHER_KEYS = ("PATH",) + R32_VARS + ("python",)
MINI_PROJECT = "vtrux-mini-test"
VARIANTS = {"r31-alpha": {"R31_OPT_A": "alpha"}, "r31-beta": {"R31_OPT_B": "beta"}}
UNKNOWN = "r31-nosuch"
JOBS = 3
PROBE = ("import json, os; print('R32PROBE ' + json.dumps({v: os.environ.get(v) for v in %r}))" % (R32_VARS,))
ENV_DUMP = 'execute_process(COMMAND "${CMAKE_COMMAND}" -E environment OUTPUT_FILE "${CMAKE_BINARY_DIR}/r31_env.txt")\n'
TOOLS_PROBE = 'message(STATUS "R24 tools [$ENV{IDF_TOOLS_PATH}]")\n'
TOOLS_SEEN = re.compile(r"R24 tools \[(.*?)\]")


# ---------- the tooling's command line (SPEC R31, R32) ----------

def check_env_cmd(proj):
    return [sys.executable, "-B", "-m", "vtrux_build", "check-env"]


def build_cmd(proj, variant, jobs=None):
    return [sys.executable, "-B", "-m", "vtrux_build", "build", variant, "--project", proj] + (
        ["--jobs", str(jobs)] if jobs else [])


def variants_json(variants):
    """vtrux_build.json's "variants": {variant: {option: value}} (R31: each names its -D options)."""
    return {"variants": variants}


# ---------- judges (pure; --self-test runs them on fakes) ----------

def names(out, var):
    """out names var as a whole name (IDF_PATH is not named by IDF_TOOLS_PATH)."""
    return re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(var), out) is not None


def crashed(out):
    return "Traceback (most recent call last)" in out


def judge_check_env(rc, out, want, trigger, note=""):
    """R32's check on one environment. want: the variable the refusal must name, None for a pass."""
    if not trigger:
        return "VOID", "the planted environment did not show in the probe child (%s)" % note
    if crashed(out):
        return "FAIL", "a Python traceback (exit %d), not a refusal" % rc
    if want is None:
        return ("PASS", "accepted (exit 0)") if rc == 0 else ("FAIL", "exit %d on an environment R32 accepts" % rc)
    if rc == 0:
        return "FAIL", "accepted (exit 0); R32 says refuse naming %s" % want
    if not names(out, want):
        return "FAIL", "refused (exit %d) without naming %s" % (rc, want)
    return "PASS", "refused (exit %d), naming %s" % (rc, want)


def judge_pre_idf(rc, out, want, trigger, built, changed, note=""):
    """R32 before a release or dev build: refused naming want, no record, nothing given to ESP-IDF
    (no build folder made)."""
    v, d = judge_check_env(rc, out, want, trigger, note)
    if v != "PASS":
        return v, d
    if changed:
        return "FAIL", d + ", but the record folder changed"
    if built:
        return "FAIL", d + ", but a build folder was made: ESP-IDF ran first"
    return "PASS", d + "; no build folder, no record"


def inside(p, root):
    """The tester's reading of "inside": p, normalised, is root or under it (case-insensitive on
    Windows). Used only to show the cases' paths are what they claim."""
    a, b = os.path.normcase(os.path.normpath(p)), os.path.normcase(os.path.normpath(root))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def r32_cases(base):
    """[(case, {var: value or None to unset}, the variable the refusal names or None for a pass)]
    from the caller's four variables in base."""
    t = base["IDF_TOOLS_PATH"].rstrip("\\/")
    py = os.path.relpath(base["IDF_PYTHON_ENV_PATH"], t)
    rom = os.path.relpath(base["ESP_ROM_ELF_DIR"], t)
    prefix = t + "-r32"                                         # its name starts like the tools folder's
    up = os.path.join(t, "..", os.path.basename(t) + "-r32")    # under it by text, outside it normalised
    other = os.path.join(os.path.dirname(t), ".espressif")
    if os.path.normcase(other) == os.path.normcase(t):
        other = t + "-other"
    return [
        ("r32_control", {}, None),
        ("r32_tools_trailing_sep", {"IDF_TOOLS_PATH": t + os.sep}, None),
        ("r32_idf_path_unset", {"IDF_PATH": None}, "IDF_PATH"),
        ("r32_idf_path_empty", {"IDF_PATH": ""}, "IDF_PATH"),     # refused as unset (reviewer, 2026-11-03)
        ("r32_tools_unset", {"IDF_TOOLS_PATH": None}, "IDF_TOOLS_PATH"),
        ("r32_tools_empty", {"IDF_TOOLS_PATH": ""}, "IDF_TOOLS_PATH"),
        ("r32_pyenv_unset", {"IDF_PYTHON_ENV_PATH": None}, "IDF_PYTHON_ENV_PATH"),
        ("r32_pyenv_prefix", {"IDF_PYTHON_ENV_PATH": os.path.join(prefix, py)}, "IDF_PYTHON_ENV_PATH"),
        ("r32_pyenv_dotdot", {"IDF_PYTHON_ENV_PATH": os.path.join(up, py)}, "IDF_PYTHON_ENV_PATH"),
        ("r32_pyenv_other", {"IDF_PYTHON_ENV_PATH": os.path.join(other, py)}, "IDF_PYTHON_ENV_PATH"),
        ("r32_rom_unset", {"ESP_ROM_ELF_DIR": None}, "ESP_ROM_ELF_DIR"),
        ("r32_rom_prefix", {"ESP_ROM_ELF_DIR": os.path.join(prefix, rom)}, "ESP_ROM_ELF_DIR"),
        ("r32_rom_dotdot", {"ESP_ROM_ELF_DIR": os.path.join(up, rom)}, "ESP_ROM_ELF_DIR"),
    ]


def applied(base, change):
    e = dict(base)
    for k, v in change.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


def probe_ok(out, env):
    """The probe child saw the four variables as env has them."""
    m = re.findall(r"^R32PROBE (.*)$", out, re.M)
    return bool(m) and json.loads(m[-1]) == {v: env.get(v) for v in R32_VARS}


def jobs_of(cmd):
    return re.findall(r"(?:^|\s)-j\s*(\d+)(?=\s|$)", cmd or "")


def tooling_ninjas(procs, root):
    """The ninja processes the tooling (pid root) started: its descendants with no ninja or cmake
    between. Returns (those, how many ninjas could not be traced to root or elsewhere)."""
    by = {p["pid"]: p for p in procs}
    mine, lost = [], 0
    for p in procs:
        if not re.match(r"ninja(\.exe)?$", os.path.basename(p["name"] or ""), re.I):
            continue
        q, seen, verdict = p, set(), None
        while verdict is None:
            if q["ppid"] == root:
                verdict = True
            elif q["ppid"] in seen or q["ppid"] not in by:
                verdict = "lost"
            else:
                seen.add(q["ppid"])
                q = by[q["ppid"]]
                if re.match(r"(ninja|cmake)(\.exe)?$", os.path.basename(q["name"] or ""), re.I):
                    verdict = False
        if verdict is True:
            mine.append(p)
        elif verdict == "lost":
            lost += 1
    return mine, lost


def judge_jobs(procs, root, want):
    """R31: with --jobs N every ninja the tooling starts runs with -j N; without it, none has -j."""
    mine, lost = tooling_ninjas(procs, root)
    if not mine:
        return "VOID", "no ninja started by the tooling was seen (%d ninja(s) not traced)" % lost
    js = [jobs_of(p["cmd"]) for p in mine]
    if want is None:
        bad = [p["cmd"] for p, j in zip(mine, js) if j]
        if bad:
            return "FAIL", "no --jobs, but the tooling's ninja ran with -j: %s" % bad[0]
        return "PASS", "%d ninja run(s) by the tooling, none with -j" % len(mine)
    bad = [p["cmd"] for p, j in zip(mine, js) if j != [str(want)]]
    if bad:
        return "FAIL", "--jobs %d, but the tooling's ninja ran as: %s" % (want, bad[0])
    return "PASS", "%d ninja run(s) by the tooling, each with -j %d" % (len(mine), want)


def cache_value(cache_text, name):
    m = re.search(r"^%s:[A-Za-z]+=(.*)$" % re.escape(name), cache_text, re.M)
    return None if m is None else m.group(1).strip()


def judge_variant(proj, variant, variants):
    """R31: the variant built in build/<variant> only, configured with its own options."""
    bad = []
    bd = os.path.join(proj, "build")
    vd = os.path.join(bd, variant)
    cache = os.path.join(vd, "CMakeCache.txt")
    if not os.path.exists(cache):
        return "FAIL", "no build/%s/CMakeCache.txt" % variant
    text = open(cache, errors="replace").read()
    for k, v in variants[variant].items():
        if cache_value(text, k) != v:
            bad.append("build/%s's cache has %s=%s, want %s" % (variant, k, cache_value(text, k), v))
    for other, opts in variants.items():
        if other == variant:
            continue
        if os.path.exists(os.path.join(bd, other)):
            bad.append("build/%s made too" % other)
        for k in opts:
            if cache_value(text, k) is not None:
                bad.append("build/%s's cache has %s, %s's option" % (variant, k, other))
    if os.path.exists(os.path.join(bd, "CMakeCache.txt")):
        bad.append("build/CMakeCache.txt made: configured in build/, not build/%s" % variant)
    try:
        pd = json.load(open(os.path.join(vd, "project_description.json")))
        if not os.path.isfile(os.path.join(vd, pd["app_bin"])):
            bad.append("the app_bin build/%s's project_description.json names is missing" % variant)
    except (OSError, KeyError, ValueError) as e:
        bad.append("build/%s/project_description.json: %s" % (variant, e))
    return ("FAIL", "; ".join(bad)) if bad else ("PASS", "built in build/%s with its options only" % variant)


def parse_env(text, windows):
    out = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if "=" in line[1:]:
            k, v = line.split("=", 1) if not line.startswith("=") else ("=" + line[1:].split("=", 1)[0],
                                                                       line[1:].split("=", 1)[1])
            out[k.upper() if windows else k] = v
    return out


def judge_env_diff(plain, tool):
    """R31: nothing added to the caller's environment. plain/tool: what the configure saw, run by
    idf.py directly and by the tooling, from the same caller's environment."""
    added = sorted(set(tool) - set(plain))
    changed = sorted(k for k in set(tool) & set(plain) if tool[k] != plain[k])
    dropped = sorted(set(plain) - set(tool))
    if not plain or not tool:
        return "VOID", "no environment read (%d plain, %d tooling)" % (len(plain), len(tool))
    if added or changed:
        return "FAIL", "added %s; changed %s" % (added, changed)
    return "PASS", "the same %d names and values%s" % (len(tool), "; dropped %s" % dropped if dropped else "")


def judge_r33(rc_this, rc_other, rc_control, form, other_v):
    if rc_this:
        return "VOID", "the configure under this install failed (exit %d): the manifest does not configure" % rc_this
    if rc_control:
        return "VOID", "the other install (v%s) did not configure the project naming its own version (exit %d)" % (
            other_v, rc_control)
    if rc_other == 0:
        return "FAIL", "a configure under v%s of a manifest naming %s went through; R33 says it fails" % (other_v, form)
    return "PASS", "configured here; under v%s refused (exit %d); v%s's own version configures there" % (
        other_v, rc_other, other_v)


# ---------- the process table, read while a build runs ----------

PS_LOOP = r"""
$seen = @{}
while ($true) {
  foreach ($p in Get-CimInstance Win32_Process) {
    $k = "$($p.ProcessId)|$($p.CreationDate)"
    if (-not $seen.ContainsKey($k)) {
      $seen[$k] = 1
      $o = [ordered]@{pid = [int]$p.ProcessId; ppid = [int]$p.ParentProcessId; name = [string]$p.Name; cmd = [string]$p.CommandLine}
      [Console]::Out.WriteLine(($o | ConvertTo-Json -Compress))
    }
  }
  [Console]::Out.WriteLine('SWEEP')
  [Console]::Out.Flush()
  Start-Sleep -Milliseconds 150
}
"""


class Watch:
    """Every process seen while it runs: {pid, ppid, name, cmd}."""

    def __init__(self):
        self.procs, self._stop, self._swept = [], threading.Event(), threading.Event()
        self._p = self._t = None

    def start(self):
        if platform.system() == "Windows":
            self._p = subprocess.Popen(["powershell", "-NoProfile", "-Command", PS_LOOP], stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, errors="replace")
            self._t = threading.Thread(target=self._read, daemon=True)
        else:
            self._t = threading.Thread(target=self._proc, daemon=True)
        self._t.start()
        if not self._swept.wait(120):
            raise RuntimeError("the process watcher did not start")
        return self

    def _read(self):
        for line in self._p.stdout:
            line = line.strip()
            if line == "SWEEP":
                self._swept.set()
            elif line.startswith("{"):
                try:
                    self.procs.append(json.loads(line))
                except ValueError:
                    pass

    def _proc(self):
        seen = set()
        while not self._stop.is_set():
            for d in os.listdir("/proc"):
                if not d.isdigit():
                    continue
                try:
                    stat = open("/proc/%s/stat" % d).read()
                    cmd = open("/proc/%s/cmdline" % d, "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
                except OSError:
                    continue
                name = stat[stat.index("(") + 1:stat.rindex(")")]
                ppid = int(stat[stat.rindex(")") + 2:].split()[1])
                k = (int(d), stat.split()[21] if len(stat.split()) > 21 else "")
                if k not in seen:
                    seen.add(k)
                    self.procs.append({"pid": int(d), "ppid": ppid, "name": name, "cmd": cmd.strip()})
            self._swept.set()
            time.sleep(0.15)

    def stop(self):
        self._stop.set()
        if self._p:
            time.sleep(0.5)       # one more sweep after the build ends
            self._p.kill()
            self._p.wait()
        self._t.join(10)
        return self.procs


# ---------- running things ----------

def probe(env, log):
    _rc, out = R.run([sys.executable, "-c", PROBE], env=env, log=log)
    return probe_ok(out, env)


def tool_env(tools, change=None, extra=None):
    e = R.idf_env(PYTHONPATH=tools, **(extra or {}))
    return applied(e, change or {})


def run_pid(cmd, cwd, env, log, watch=False):
    """cmd run to the end: (rc, out, its pid, the processes seen while it ran, if watched)."""
    w = Watch().start() if watch else None
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    raw = p.communicate()[0]
    procs = w.stop() if w else []
    out = raw.decode("utf-8", "replace").replace("\r\n", "\n")
    with open(log, "a", encoding="utf-8") as f:
        f.write("$ %s  (in %s)\n%s\n[exit %d, %.0f s; %d processes seen]\n\n" % (
            " ".join(cmd), cwd, out, p.returncode, time.time() - t0, len(procs)))
    return p.returncode, out, p.pid, procs


def mini(ctx):
    if not os.path.exists(os.path.join(ctx.mini, ".git")):
        R.make_mini(ctx)
    return ctx.mini


def mini_branch(ctx, branch, files, msg, base="main"):
    """A mini branch: base with files {rel: whole text, or (None, text to append)} committed."""
    mini(ctx)
    R.reset(ctx.mini, "main", "main")
    R.git(ctx.mini, "checkout", "-q", "-f", "-B", branch, base)
    for rel, text in files.items():
        p = os.path.join(ctx.mini, rel)
        if isinstance(text, tuple):
            text = open(p, newline="").read() + text[1]
        with open(p, "w", newline="") as f:
            f.write(text)
    R.git(ctx.mini, "add", "-A")
    R.git(ctx.mini, "commit", "-q", "-m", msg)


def vb_text(ctx, variants=None):
    data = json.loads(R.git(ctx.mini, "show", "main:vtrux_build.json"))
    if variants is not None:
        data.update(variants_json(variants))
    return json.dumps(data) + "\n"


def r31_branch(ctx):
    if not R.ref(ctx.mini, "refs/heads/r31"):
        mini_branch(ctx, "r31", {"vtrux_build.json": vb_text(ctx, VARIANTS), "CMakeLists.txt": (None, ENV_DUMP)},
                    "r31 cases: two variants; the configure writes its environment")
    R.reset(ctx.mini, "r31", "r31")
    R.rmtree(os.path.join(ctx.mini, "build"))


# ---------- the cases ----------

def c_check_env(ctx, log, name, change, want):
    tools = R.default_tools(ctx, mini(ctx))
    e = tool_env(tools, change)
    trig = probe(e, log)
    rc, out = R.run(check_env_cmd(ctx.mini), cwd=ctx.mini, env=e, log=log)
    note = ""
    if name == "r32_tools_unset":
        note = "; ~/.espressif %s" % ("exists" if os.path.isdir(os.path.expanduser("~/.espressif")) else "absent")
    v, d = judge_check_env(rc, out, want, trig, "planted %s" % sorted(change))
    R.report(name, v, d + note, ctx.log)


def c_pre_release(ctx, log, name, change_of, want):
    mini(ctx)
    R.reset(ctx.mini, "main", "main")
    R.rmtree(os.path.join(ctx.mini, "build"))
    tools = R.default_tools(ctx, ctx.mini)
    change = change_of(dict(os.environ))
    e = tool_env(tools, change, {"REVERSE_IT_DIR": ctx.ri})
    trig = probe(e, log)
    before = R.snapshot(ctx.records(MINI_PROJECT))
    rc, out = R.release(ctx, ctx.mini, log, env=change)
    built = os.path.exists(os.path.join(ctx.mini, "build"))
    v, d = judge_pre_idf(rc, out, want, trig, built, before != R.snapshot(ctx.records(MINI_PROJECT)),
                         "planted %s" % sorted(change))
    R.report(name, v, d, ctx.log)
    R.rmtree(os.path.join(ctx.mini, "build"))


def c_pre_dev(ctx, log, name, change_of, want):
    r31_branch(ctx)
    tools = R.default_tools(ctx, ctx.mini)
    change = change_of(dict(os.environ))
    e = tool_env(tools, change)
    trig = probe(e, log)
    rc, out, _pid, _p = run_pid(build_cmd(ctx.mini, "r31-alpha"), ctx.mini, e, log)
    built = os.path.exists(os.path.join(ctx.mini, "build"))
    v, d = judge_pre_idf(rc, out, want, trig, built, False, "planted %s" % sorted(change))
    R.report(name, v, d, ctx.log)
    R.reset(ctx.mini, "main", "main")
    R.rmtree(os.path.join(ctx.mini, "build"))


def c_r31_unknown(ctx, log):
    name = "r31_unknown_variant"
    r31_branch(ctx)
    e = tool_env(R.default_tools(ctx, ctx.mini))
    rc, out, _pid, _p = run_pid(build_cmd(ctx.mini, UNKNOWN), ctx.mini, e, log)
    built = os.path.exists(os.path.join(ctx.mini, "build"))
    missing = [v for v in VARIANTS if not names(out, v)]
    if crashed(out):
        v, d = "FAIL", "a Python traceback (exit %d), not a refusal" % rc
    elif rc == 0:
        v, d = "FAIL", "variant %s accepted (exit 0); R31 says refuse" % UNKNOWN
    elif missing:
        v, d = "FAIL", "refused (exit %d), but the refusal does not name listed variant(s) %s" % (rc, missing)
    elif built:
        v, d = "FAIL", "refused (exit %d), but a build folder was made" % rc
    else:
        v, d = "PASS", "refused (exit %d), naming %s; no build folder" % (rc, sorted(VARIANTS))
    R.report(name, v, d, ctx.log)
    R.reset(ctx.mini, "main", "main")


def c_r31_none(ctx, log):
    name = "r31_no_variants"
    mini(ctx)
    R.reset(ctx.mini, "main", "main")
    R.rmtree(os.path.join(ctx.mini, "build"))
    listed = "variants" in json.load(open(os.path.join(ctx.mini, "vtrux_build.json")))
    e = tool_env(R.default_tools(ctx, ctx.mini))
    rc, out, _pid, _p = run_pid(build_cmd(ctx.mini, "r31-alpha"), ctx.mini, e, log)
    built = os.path.exists(os.path.join(ctx.mini, "build"))
    if listed:
        v, d = "VOID", "the project lists variants"
    elif crashed(out):
        v, d = "FAIL", "a Python traceback (exit %d), not a refusal" % rc
    elif rc == 0 or built:
        v, d = "FAIL", "exit %d, build folder %s; R31: a project listing no variants has no build command" % (
            rc, "made" if built else "not made")
    else:
        v, d = "PASS", "refused (exit %d); no build folder" % rc
    R.report(name, v, d, ctx.log)


def c_r31_build(ctx, log):
    """build r31-alpha --jobs 3, then (build folder removed) without --jobs; then the environment."""
    r31_branch(ctx)
    tools = R.default_tools(ctx, ctx.mini)
    e = tool_env(tools)
    rc, out, pid, procs = run_pid(build_cmd(ctx.mini, "r31-alpha", JOBS), ctx.mini, e, log, watch=True)
    if rc:
        R.report("r31_variant", "FAIL", "build r31-alpha --jobs %d exited %d" % (JOBS, rc), ctx.log)
        R.report("r31_jobs", "VOID", "the build failed", ctx.log)
    else:
        R.report("r31_variant", *judge_variant(ctx.mini, "r31-alpha", VARIANTS), ctx.log)
        R.report("r31_jobs", *judge_jobs(procs, pid, JOBS), ctx.log)
    tool_seen = parse_env(open(os.path.join(ctx.mini, "build", "r31-alpha", "r31_env.txt")).read(),
                          platform.system() == "Windows") if not rc else {}
    R.rmtree(os.path.join(ctx.mini, "build"))
    rc2, _out, pid2, procs2 = run_pid(build_cmd(ctx.mini, "r31-alpha"), ctx.mini, e, log, watch=True)
    if rc2:
        R.report("r31_no_jobs", "VOID", "build r31-alpha exited %d" % rc2, ctx.log)
    else:
        R.report("r31_no_jobs", *judge_jobs(procs2, pid2, None), ctx.log)
    R.rmtree(os.path.join(ctx.mini, "build"))
    # the same caller's environment, idf.py run directly with the variant's option
    plain = os.path.join(ctx.mini, "build", "r31-plain")
    opts = ["-D%s=%s" % kv for kv in VARIANTS["r31-alpha"].items()]
    rc3, _ = R.run([sys.executable, os.path.join(os.environ["IDF_PATH"], "tools", "idf.py"), "-B", plain] + opts +
                   ["reconfigure"], cwd=ctx.mini, env=e, log=log)
    plain_seen = parse_env(open(os.path.join(plain, "r31_env.txt")).read(),
                           platform.system() == "Windows") if not rc3 else {}
    v, d = judge_env_diff(plain_seen, tool_seen)
    with open(log, "a", encoding="utf-8") as f:
        f.write("r31_env: names seen by the plain configure %d, by the tooling's %d\n" % (len(plain_seen), len(tool_seen)))
    R.report("r31_env", v, d, ctx.log)
    R.reset(ctx.mini, "main", "main")
    R.rmtree(os.path.join(ctx.mini, "build"))


def c_r33_release(ctx, log, name, version):
    """A release build of the mini with main/idf_component.yml naming version (None: no idf)."""
    text = R.idf_manifest(version) if version is not None else "dependencies: {}\n"
    mini_branch(ctx, "r33", {"main/idf_component.yml": text}, "r33 case: the manifest names %s" % version)
    R.rmtree(os.path.join(ctx.mini, "build"))
    trig = R.git(ctx.mini, "show", "HEAD:main/idf_component.yml") + "\n" == text
    before = R.snapshot(ctx.records(MINI_PROJECT))
    rc, _ = R.release(ctx, ctx.mini, log, jobs=ctx.jobs)
    R.refusal(ctx, name, ctx.mini, rc, before, project=MINI_PROJECT, trigger=trig,
              note="R33: the manifest names %s" % ("no IDF" if version is None else '"%s"' % version))
    R.reset(ctx.mini, "main", "main")
    R.rmtree(os.path.join(ctx.mini, "build"))


def c_r33_release_eq(ctx, log):
    """A release build naming "==M.m.p" is recorded (R33), and its configure sees the caller's
    IDF_TOOLS_PATH as it is (R24: no default, nothing replaced)."""
    want = "==" + R.idf_version()
    mini_branch(ctx, "r33-eq", {"main/idf_component.yml": R.idf_manifest(want), "CMakeLists.txt": (None, TOOLS_PROBE)},
                "r33 case: the manifest names %s; the configure prints IDF_TOOLS_PATH" % want)
    # The new manifest makes main's dependencies.lock stale, and the release build's reconfigure
    # would rewrite it, leaving the worktree dirty (R8). A project commits the lock its manifest
    # gives, so the branch does too, as release_cases' fixture does.
    with open(os.path.join(ctx.out, "fixture.txt"), "a") as fx:
        R.normalise(ctx, ctx.mini, "r33-eq", fx)
    R.rmtree(os.path.join(ctx.mini, "build"))
    before = R.snapshot(ctx.records(MINI_PROJECT))
    rc, out = R.release(ctx, ctx.mini, log, jobs=ctx.jobs)
    rec = R.check_success(ctx, "r33_release_eq", ctx.mini, rc, before, project=MINI_PROJECT)
    seen = TOOLS_SEEN.findall(out)
    caller = os.environ.get("IDF_TOOLS_PATH")
    if rec is None:
        v, d = "VOID", "the release build was not recorded"
    elif not seen:
        v, d = "VOID", "the configure probe did not print"
    elif any(s != caller for s in seen):
        v, d = "FAIL", "the configure saw an IDF_TOOLS_PATH other than the caller's (%d of %d prints)" % (
            sum(s != caller for s in seen), len(seen))
    else:
        v, d = "PASS", "the configure saw the caller's IDF_TOOLS_PATH unchanged (%d print(s))" % len(seen)
    R.report("r24_tools_passed", v, d, ctx.log)
    R.reset(ctx.mini, "main", "main")
    R.rmtree(os.path.join(ctx.mini, "build"))


def other_env(other):
    """The caller's environment with the other install's exported variables in place of this one's."""
    e = {k: v for k, v in R.idf_env().items()
         if not (k.upper().startswith(("IDF_", "ESP_", "OPENOCD_")) or k.upper() == "PATH")}
    e.update({k: other[k] for k in OTHER_KEYS if k != "python"})
    return e


def lock_planted(lock, locked, this_v):
    """The copy configured under the other install has the lock state the case names: none
    (fresh), or the lock this install wrote for the same manifest, naming this IDF (locked)."""
    if not locked:
        return lock is None
    if lock is None:
        return False
    lock = lock.replace("\r\n", "\n")
    return "manifest_hash:" in lock and re.search(
        r"(?m)^  idf:\n(?:    .*\n)*?    version: %s$" % re.escape(this_v), lock) is not None


def c_r33_other(ctx, log, name, form, locked):
    """R33's can-fail: a configure under the other install of a project naming this IDF exactly.
    fresh (locked False): the copy has no dependencies.lock, so the Component Manager solves the
    manifest. locked: the copy has the lock this install wrote for that manifest, as a project
    commits it (R10), so the manifest and the lock agree when the other install meets them."""
    if not ctx.other:
        R.report(name, "SKIPPED", "no --other-idf given", ctx.log)
        return
    this_v, other_v = R.idf_version(), R.idf_version(ctx.other["IDF_PATH"])
    if this_v == other_v:
        R.report(name, "VOID", "the other install is v%s too" % this_v, ctx.log)
        return
    mini(ctx)
    base = os.path.join(ctx.root, "r33")
    rcs, written, planted = [], None, None
    for label, env, idf_py, ver in (
            ("this", R.idf_env(), [sys.executable, os.path.join(os.environ["IDF_PATH"], "tools", "idf.py")], form % this_v),
            ("other", other_env(ctx.other), [ctx.other["python"], os.path.join(ctx.other["IDF_PATH"], "tools", "idf.py")],
             form % this_v),
            ("control", other_env(ctx.other), [ctx.other["python"], os.path.join(ctx.other["IDF_PATH"], "tools", "idf.py")],
             form % other_v)):
        d = os.path.join(base, "%s-%s" % (name, label))
        R.rmtree(d)
        try:
            R.run(["git", "clone", "-q", "-b", "main", ctx.mini, d], check=True)
            with open(os.path.join(d, "main", "idf_component.yml"), "w", newline="") as f:
                f.write(R.idf_manifest(ver))
            lp = os.path.join(d, "dependencies.lock")
            if os.path.exists(lp):
                os.remove(lp)               # main's lock, written for main's manifest
            if label == "other" and locked and written is not None:
                with open(lp, "w", newline="") as f:
                    f.write(written)
            if label == "other":
                planted = open(lp, newline="").read() if os.path.exists(lp) else None
            with open(log, "a", encoding="utf-8") as f:
                f.write("%s %s: the manifest names %s; dependencies.lock %s\n" % (
                    name, label, ver, "this install's, for this manifest" if label == "other" and planted else "none"))
            rc, _ = R.run(idf_py + ["--no-ccache", "reconfigure"], cwd=d, env=env, log=log)
            rcs.append(rc)
            if label == "this" and os.path.exists(lp):
                written = open(lp, newline="").read()
        finally:
            R.rmtree(d)
    state = "with the lock v%s wrote for it" % this_v if locked else "with no lock"
    if not lock_planted(planted, locked, this_v):
        v, d = "VOID", "the copy for v%s did not have the lock state planted (%s)" % (other_v, state)
    else:
        v, d = judge_r33(rcs[0], rcs[1], rcs[2], "%s %s" % (form % this_v, state), other_v)
    R.report(name, v, d, ctx.log)


def bump(v):
    a, b, c = v.split(".")
    return "%s.%s.%d" % (a, b, int(c) + 1)


def outside_rom(env):
    t = env["IDF_TOOLS_PATH"].rstrip("\\/")
    return {"ESP_ROM_ELF_DIR": os.path.join(t + "-r32", os.path.relpath(env["ESP_ROM_ELF_DIR"], t))}


CASES = [(n, (lambda n, ch, w: lambda c, l: c_check_env(c, l, n, ch, w))(n, ch, w))
         for n, ch, w in r32_cases(dict(os.environ))] if all(v in os.environ for v in R32_VARS[1:]) else []
CASES += [
    ("r32_release_tools_unset", lambda c, l: c_pre_release(c, l, "r32_release_tools_unset",
                                                           lambda e: {"IDF_TOOLS_PATH": None}, "IDF_TOOLS_PATH")),
    ("r32_release_rom_outside", lambda c, l: c_pre_release(c, l, "r32_release_rom_outside", outside_rom,
                                                           "ESP_ROM_ELF_DIR")),
    ("r32_dev_tools_unset", lambda c, l: c_pre_dev(c, l, "r32_dev_tools_unset", lambda e: {"IDF_TOOLS_PATH": None},
                                                   "IDF_TOOLS_PATH")),
    ("r32_dev_rom_outside", lambda c, l: c_pre_dev(c, l, "r32_dev_rom_outside", outside_rom, "ESP_ROM_ELF_DIR")),
    ("r31_unknown_variant", c_r31_unknown),
    ("r31_no_variants", c_r31_none),
    ("r31_build", c_r31_build),
    ("r33_other_bare_fresh", lambda c, l: c_r33_other(c, l, "r33_other_bare_fresh", "%s", False)),
    ("r33_other_eq_fresh", lambda c, l: c_r33_other(c, l, "r33_other_eq_fresh", "==%s", False)),
    ("r33_other_bare_locked", lambda c, l: c_r33_other(c, l, "r33_other_bare_locked", "%s", True)),
    ("r33_other_eq_locked", lambda c, l: c_r33_other(c, l, "r33_other_eq_locked", "==%s", True)),
    ("r33_release_range", lambda c, l: c_r33_release(c, l, "r33_release_range", ">=5.4.0")),
    ("r33_release_star", lambda c, l: c_r33_release(c, l, "r33_release_star", "*")),
    ("r33_release_narrow", lambda c, l: c_r33_release(c, l, "r33_release_narrow",
                                                      ">=%s,<%s" % (R.idf_version(), bump(R.idf_version())))),
    ("r33_release_none", lambda c, l: c_r33_release(c, l, "r33_release_none", None)),
    ("r33_release_eq", c_r33_release_eq),
]


# ---------- self-test ----------

FAKE_MAIN = r'''
import os, sys
BUG = %r
V = ("IDF_PATH", "IDF_TOOLS_PATH", "IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR")


def inside(p, root):
    if BUG == "prefix":
        return p.startswith(root)
    if BUG == "trailing_sep":
        return os.path.normpath(p).startswith(root + os.sep) or os.path.normpath(p) == root
    if BUG != "no_norm":
        p, root = os.path.normpath(p), os.path.normpath(root)
    try:
        return os.path.commonpath([os.path.normcase(p), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:
        return False


def refuse(v):
    print("refused" if BUG == "unnamed" else "refused: %%s" %% v)
    return 1


def main():
    if sys.argv[1:] != ["check-env"]:
        return 2
    e = os.environ
    if BUG == "crash":
        e["IDF_PATH"]
    elif BUG != "no_idf_path" and (("IDF_PATH" not in e) if BUG == "idf_empty_ok" else not e.get("IDF_PATH")):
        return refuse("IDF_PATH")
    t = e.get("IDF_TOOLS_PATH")
    if BUG == "default_tools" and not t:
        t = os.path.expanduser("~/.espressif")
    if (t is None) if BUG == "empty_ok" else not t:
        return refuse("IDF_TOOLS_PATH")
    for v in ("IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR"):
        if BUG == "no_rom" and v == "ESP_ROM_ELF_DIR":
            continue
        if not e.get(v) or not inside(e[v], t):
            return refuse(v)
    print("ok")
    return 0


sys.exit(main())
'''

FAKE_BUGS = ["default_tools", "no_idf_path", "idf_empty_ok", "empty_ok", "no_rom", "prefix", "no_norm", "unnamed", "trailing_sep", "crash"]


def run_fake(tools, base, d):
    """The R32 cases against the fake tooling in tools: {case: verdict}."""
    got = {}
    for name, change, want in r32_cases(base):
        e = applied(dict(base, PYTHONPATH=tools), change)
        _rc, pout = R.run([sys.executable, "-c", PROBE], env=e)
        rc, out = R.run([sys.executable, "-B", "-m", "vtrux_build", "check-env"], cwd=d, env=e)
        got[name] = judge_check_env(rc, out, want, probe_ok(pout, e))[0]
    return got


def self_test():
    fails = []

    def expect(label, got, want):
        ok = got == want
        print("%-60s %s" % (label, "ok" if ok else "WRONG: got %r, want %r" % (got, want)))
        if not ok:
            fails.append(label)

    d = I.scratch("env_cases")
    t = os.path.join(d, "tools")
    base = {k: v for k, v in os.environ.items() if not k.upper().startswith(("IDF_", "ESP_"))}
    base.update(IDF_PATH=os.path.join(d, "idf"), IDF_TOOLS_PATH=t,
                IDF_PYTHON_ENV_PATH=os.path.join(t, "python_env", "idf5.4_py3.11_env"),
                ESP_ROM_ELF_DIR=os.path.join(t, "esp-rom-elfs", "20241011"))
    for v in R32_VARS:
        os.makedirs(base[v], exist_ok=True)

    # the cases' paths are what they claim, by the tester's own reading
    for name, change, want in r32_cases(base):
        e = applied(base, change)
        for v in ("IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR"):
            if v in change and change[v]:
                expect("%s: %s is outside" % (name, v), inside(e[v], e["IDF_TOOLS_PATH"]), False)
        if want is None:
            for v in ("IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR"):
                expect("%s: %s is inside" % (name, v), inside(e[v], e["IDF_TOOLS_PATH"]), True)
    change_of = {name: change for name, change, _want in r32_cases(base)}
    expect("prefix path starts with the tools path's text",
           applied(base, change_of["r32_pyenv_prefix"])["IDF_PYTHON_ENV_PATH"].startswith(t), True)
    expect("dotdot path starts with the tools path's text",
           applied(base, change_of["r32_pyenv_dotdot"])["IDF_PYTHON_ENV_PATH"].startswith(t), True)

    # the R32 cases against fake tooling: the right one passes all, each broken one fails some
    for bug in [""] + FAKE_BUGS:
        tools = os.path.join(d, "fake_" + (bug or "right"))
        os.makedirs(os.path.join(tools, "vtrux_build"))
        open(os.path.join(tools, "vtrux_build", "__init__.py"), "w").close()
        with open(os.path.join(tools, "vtrux_build", "__main__.py"), "w", newline="") as f:
            f.write(FAKE_MAIN % bug)
        got = run_fake(tools, base, d)
        bad = sorted(n for n, v in got.items() if v != "PASS")
        if not bug:
            expect("fake tooling, no bug: every R32 case PASS", bad, [])
        else:
            expect("fake tooling, bug %s: caught" % bug, bool(bad), True)
            print("    caught by %s" % bad)
    expect("an unplanted environment is VOID", judge_check_env(1, "refused: IDF_PATH", "IDF_PATH", False)[0], "VOID")
    expect("IDF_TOOLS_PATH does not name IDF_PATH", names("refused: IDF_TOOLS_PATH", "IDF_PATH"), False)
    expect("a traceback naming the variable is FAIL",
           judge_check_env(1, "Traceback (most recent call last):\nKeyError: 'IDF_PATH'", "IDF_PATH", True)[0], "FAIL")

    # R32 before a build
    for label, args, want in (("refused, nothing built", (1, "IDF_TOOLS_PATH unset", "IDF_TOOLS_PATH", True, False, False), "PASS"),
                              ("build folder made", (1, "IDF_TOOLS_PATH unset", "IDF_TOOLS_PATH", True, True, False), "FAIL"),
                              ("record written", (1, "IDF_TOOLS_PATH unset", "IDF_TOOLS_PATH", True, False, True), "FAIL"),
                              ("built", (0, "", "IDF_TOOLS_PATH", True, True, True), "FAIL"),
                              ("not naming it", (1, "refused", "IDF_TOOLS_PATH", True, False, False), "FAIL")):
        expect("pre-IDF: %s" % label, judge_pre_idf(*args)[0], want)

    # R31 --jobs: the tooling's own ninja, through a cmd, not the bootloader's under cmake
    root = 100
    procs = [{"pid": 101, "ppid": 100, "name": "python.exe", "cmd": "python idf.py"},
             {"pid": 102, "ppid": 101, "name": "cmake.exe", "cmd": "cmake -G Ninja"},
             {"pid": 103, "ppid": 102, "name": "ninja.exe", "cmd": "ninja cmTC_1234"},
             {"pid": 104, "ppid": 100, "name": "cmd.exe", "cmd": "cmd /c ninja"},
             {"pid": 105, "ppid": 104, "name": "ninja.exe", "cmd": "ninja -C build/r31-alpha -j 3"},
             {"pid": 106, "ppid": 105, "name": "cmake.exe", "cmd": "cmake --build ."},
             {"pid": 107, "ppid": 106, "name": "ninja.exe", "cmd": "ninja"},
             {"pid": 108, "ppid": 999, "name": "ninja.exe", "cmd": "ninja -j 8"}]
    expect("jobs: -j 3 by the tooling, bootloader's ignored", judge_jobs(procs, root, 3)[0], "PASS")
    expect("jobs: -j3 form", judge_jobs([dict(procs[4], cmd="ninja -j3 -C b")] + procs[3:4], root, 3)[0], "PASS")
    expect("jobs: wrong -j", judge_jobs([dict(procs[4], cmd="ninja -j 4")] + procs[3:4], root, 3)[0], "FAIL")
    expect("jobs: no -j with --jobs", judge_jobs([dict(procs[4], cmd="ninja -C b")] + procs[3:4], root, 3)[0], "FAIL")
    expect("jobs: -j without --jobs", judge_jobs(procs, root, None)[0], "FAIL")
    expect("jobs: none without --jobs", judge_jobs([dict(procs[4], cmd="ninja -C b")] + procs[3:4], root, None)[0], "PASS")
    expect("jobs: no tooling ninja seen", judge_jobs(procs[1:3] + procs[7:], root, 3)[0], "VOID")
    expect("jobs: -j 13 is not -j 3", judge_jobs([dict(procs[4], cmd="ninja -j 13")] + procs[3:4], root, 3)[0], "FAIL")

    # the process watcher on this host: a real child of a child, seen with its parent
    marker = "envcases_watch_%d" % os.getpid()
    inner = "import time; time.sleep(2.5)  # %s" % marker
    outer = "import subprocess, sys; subprocess.run([sys.executable, '-c', %r])" % inner
    _rc, _out, pid, seen = run_pid([sys.executable, "-c", outer], d, dict(os.environ), os.path.join(d, "watch.log"),
                                   watch=True)
    leaf = [p for p in seen if marker in (p["cmd"] or "") and "subprocess" not in (p["cmd"] or "")]
    by = {p["pid"]: p for p in seen}
    traced = False
    for p in leaf:
        q, hops = p, 0
        while q and hops < 6 and not traced:
            traced = q["ppid"] == pid
            q, hops = by.get(q["ppid"]), hops + 1
    expect("watcher: the grandchild seen and traced to the root", (bool(leaf), traced), (True, True))

    # R31 variant folders
    pj = os.path.join(d, "proj")

    def mk(variant, cache, other=False, top=False, binfile=True):
        R.rmtree(pj) if os.path.exists(pj) else None
        vd = os.path.join(pj, "build", variant)
        os.makedirs(vd)
        open(os.path.join(vd, "CMakeCache.txt"), "w").write(cache)
        open(os.path.join(vd, "project_description.json"), "w").write('{"app_bin": "a.bin"}')
        if binfile:
            open(os.path.join(vd, "a.bin"), "w").write("x")
        if other:
            os.makedirs(os.path.join(pj, "build", "r31-beta"))
        if top:
            open(os.path.join(pj, "build", "CMakeCache.txt"), "w").write("")
    good = "R31_OPT_A:UNINITIALIZED=alpha\nOTHER:STRING=1\n"
    for label, kw, want in (("right", {}, "PASS"), ("other variant built", {"other": True}, "FAIL"),
                            ("configured in build/", {"top": True}, "FAIL"), ("no .bin", {"binfile": False}, "FAIL")):
        mk("r31-alpha", good, **kw)
        expect("variant: %s" % label, judge_variant(pj, "r31-alpha", VARIANTS)[0], want)
    mk("r31-alpha", "R31_OPT_A:UNINITIALIZED=beta\n")
    expect("variant: wrong option value", judge_variant(pj, "r31-alpha", VARIANTS)[0], "FAIL")
    mk("r31-alpha", good + "R31_OPT_B:UNINITIALIZED=beta\n")
    expect("variant: the other's option too", judge_variant(pj, "r31-alpha", VARIANTS)[0], "FAIL")
    R.rmtree(pj)

    # R31 environment
    a = parse_env("PATH=x\nIDF_PATH=y\n=C:=C:\\w\n", True)
    expect("env parse", sorted(a), ["=C:", "IDF_PATH", "PATH"])
    expect("env: same", judge_env_diff(a, dict(a))[0], "PASS")
    expect("env: added", judge_env_diff(a, dict(a, NEW="1"))[0], "FAIL")
    expect("env: changed", judge_env_diff(a, dict(a, PATH="z"))[0], "FAIL")
    expect("env: dropped only", judge_env_diff(a, {"PATH": "x"})[0], "PASS")
    expect("env: nothing read", judge_env_diff({}, a)[0], "VOID")

    # R33
    expect("r33: refused under the other", judge_r33(0, 1, 0, "5.4.2", "5.4.1")[0], "PASS")
    expect("r33: accepted under the other", judge_r33(0, 0, 0, "5.4.2", "5.4.1")[0], "FAIL")
    expect("r33: fails here too", judge_r33(1, 1, 0, "5.4.2", "5.4.1")[0], "VOID")
    expect("r33: the other cannot configure it at all", judge_r33(0, 1, 1, "5.4.2", "5.4.1")[0], "VOID")
    lk = ("dependencies:\n  idf:\n    source:\n      type: idf\n    version: %s\ndirect_dependencies:\n- idf\n"
          "manifest_hash: 3ddd\ntarget: esp32c3\nversion: 2.0.0\n")
    expect("r33 lock: fresh, none", lock_planted(None, False, "5.4.2"), True)
    expect("r33 lock: fresh, but a lock left", lock_planted(lk % "5.4.2", False, "5.4.2"), False)
    expect("r33 lock: locked, this install's", lock_planted(lk % "5.4.2", True, "5.4.2"), True)
    expect("r33 lock: locked, this install's, CRLF", lock_planted((lk % "5.4.2").replace("\n", "\r\n"), True,
                                                                   "5.4.2"), True)
    expect("r33 lock: locked, none", lock_planted(None, True, "5.4.2"), False)
    expect("r33 lock: locked, naming the other IDF", lock_planted(lk % "5.4.1", True, "5.4.2"), False)
    expect("r33 lock: locked, no manifest_hash", lock_planted((lk % "5.4.2").replace("manifest_hash", "x"),
                                                                True, "5.4.2"), False)
    expect("r33 lock: locked, the version is not idf's",
           lock_planted(lk.replace("  idf:", "  other:") % "5.4.2", True, "5.4.2"), False)
    idf = os.path.join(d, "idfv", "tools", "cmake")
    os.makedirs(idf)
    open(os.path.join(idf, "version.cmake"), "w").write(
        "set(IDF_VERSION_MAJOR 5)\nset(IDF_VERSION_MINOR 4)\nset(IDF_VERSION_PATCH 2)\n")
    expect("idf_version reads version.cmake", R.idf_version(os.path.join(d, "idfv")), "5.4.2")
    expect("bump", bump("5.4.2"), "5.4.3")
    expect("manifest", R.idf_manifest("==5.4.2"), 'dependencies:\n  idf:\n    version: "==5.4.2"\n')
    print("\nself-test: %s" % ("PASS" if not fails else "FAIL: %s" % fails))
    return 1 if fails else 0


# ---------- main ----------

def dump_env(path):
    data = {k: os.environ.get(k) for k in OTHER_KEYS if k != "python"}
    data["python"] = sys.executable
    missing = [k for k, v in data.items() if not v]
    if missing:
        print("not an exported ESP-IDF shell: %s unset" % missing)
        return 2
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    print("wrote the exported variables of ESP-IDF v%s (names %s)" % (R.idf_version(data["IDF_PATH"]), sorted(data)))
    return 0


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dump-env")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--other-idf")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.dump_env:
        return dump_env(a.dump_env)
    if not (a.root and a.out) or any(v not in os.environ for v in R32_VARS):
        ap.error("--root and --out, from an exported ESP-IDF shell (%s set)" % ", ".join(R32_VARS))
    ns = argparse.Namespace(root=a.root, out=a.out, jobs=a.jobs, base=None, fixture=None)
    ctx = R.Ctx(ns)
    if not os.path.isdir(os.path.join(ctx.repo_src, ".git")):
        ap.error("--root has no fixture: run release_cases.py there first")
    ctx.other = json.load(open(a.other_idf, encoding="utf-8")) if a.other_idf else None
    if ctx.other and any(not ctx.other.get(k) for k in OTHER_KEYS):
        ap.error("--other-idf lacks %s" % [k for k in OTHER_KEYS if not ctx.other.get(k)])
    os.makedirs(ctx.out, exist_ok=True)
    os.makedirs(os.path.join(ctx.ri, "firmware"), exist_ok=True)
    ctx.log = os.path.join(ctx.out, "env_driver.log")
    names_ = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names_) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s  host %s  %s  python %s\nIDF v%s  other IDF %s  tooling %s (%s)\ncases %s\n\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S"), platform.node(), platform.platform(), platform.python_version(),
            R.idf_version(), "v" + R.idf_version(ctx.other["IDF_PATH"]) if ctx.other else "(none)",
            R.git(TOOLS, "rev-parse", "HEAD"), R.git(TOOLS, "status", "--porcelain") or "clean", names_))
    R.make_pin(ctx)         # the fixture's tag must mark the tooling under test
    for n, fn in CASES:
        if n not in names_:
            continue
        why = R.bench_clear(ctx.log)
        if why:
            R.report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "env_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            R.report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    with open(os.path.join(ctx.out, "env_results.json"), "a") as f:
        for r in R.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in R.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(R.RESULTS), len(bad),
                                                ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
