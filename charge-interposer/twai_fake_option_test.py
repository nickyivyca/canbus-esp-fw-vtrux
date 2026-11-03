#!/usr/bin/env python3
"""Tests of host_bridge's TWAI_FAKE option (firmware/test/host_bridge/run.sh)
and of its can-fail (harness_checks/shared_fake_canfail.py). No g++, no
hardware: run.sh is run by bash with stand-in g++ and python3 that record
what they were given, and the can-fail is judged on stand-in suite runs.

    py -3.14 -B charge-interposer/twai_fake_option_test.py [--can-fail]

Tester, 2026-11-02 (the charge-interposer reviewer's order: port step 1b's
acceptance E as committed harness, so the acceptance runner can re-run it).
--can-fail runs every test first unchanged (the control, which must pass),
then with each of the can-fail's RULES switched off, then with run.sh
mutated; each must make a test fail.

Since 2026-11-02 they also test where run.sh works (the charge-interposer
reviewer's order of that day): one run folder under the tree's own
scratch/charge-interposer-harness/host-bridge/, never the system temp
folder (the tests set TMPDIR, which must stay empty), removed when run.sh
ends, pass, fail or refusal, with the tool folders above it, and a folder
it cannot remove printed. And (the reviewer's reply of the same day) that
another run's cleanup removing the tool folders between run.sh making them
and making its run folder in them costs a retry, three tries in all, with
waits of 0.1 s and 0.4 s before the second and third, as scratch_dir.make's:
a stand-in mktemp, also from BASH_ENV, removes them first, and it and a
stand-in sleep log the order of tries and waits.

run.sh always runs from a copy of host_bridge and main/ at their depth under
a test folder of its own (tree_copy), so its tree's root, and its scratch/,
are that folder's. On Windows bash is Git Bash by its full path (a bare
"bash" there is WSL's, regress.py); elsewhere bash. Git Bash puts its own
folders first on PATH, so the stand-in rm is a function from BASH_ENV, not
a file on PATH. Throwaway folders come from scratch_dir.py and are removed
when the run ends.
"""

import contextlib
import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                    # noqa: E402

FW = os.path.join(HERE, "firmware")
HB = os.path.join(FW, "test", "host_bridge")
CANFAIL = os.path.join(HERE, "harness_checks", "shared_fake_canfail.py")
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"


def bash():
    if os.name == "nt":
        if not os.path.exists(GIT_BASH):
            raise SystemExit("REFUSED: no Git Bash at %s" % GIT_BASH)
        return GIT_BASH
    return "bash"


def fwd(p):
    return p.replace("\\", "/")


def bash_form(p):
    """p as bash prints it: on Windows Git Bash's /c/... form."""
    p = fwd(p)
    if os.name == "nt" and len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


# ---- stand-ins --------------------------------------------------------------
GPP = r"""#!/bin/sh
# stand-in g++: logs its arguments (tab-separated), what the first -I
# folder holds and whether the first source (the test) reaches
# ../../main/main.cpp from its own folder, as test_event_layout's #include
# does; writes an output that runs and prints nothing
out=""; first=""; prev=""; t=""
for a in "$@"; do
  if [ -n "$out" ] && [ -z "$t" ]; then t="$a"; fi
  [ "$prev" = "-o" ] && out="$a"
  [ "$prev" = "-I" ] && [ -z "$first" ] && first="$a"
  prev="$a"
done
{
  printf 'CALL'
  for a in "$@"; do printf '\t%s' "$a"; done
  printf '\n'
  for f in twai_fake.h twai_fake.cpp driver/twai.h test_l2_bridge.cpp; do
    if [ -e "$first/$f" ]; then printf 'HAS\t%s\n' "$f"; else printf 'NO\t%s\n' "$f"; fi
  done
  f="../../main/main.cpp"
  if [ -f "$(dirname "$t")/$f" ]; then printf 'HAS\t%s\n' "$f"; else printf 'NO\t%s\n' "$f"; fi
} >> "$STUB_LOG"
# STUB_GPP_FAIL: every build fails
[ -n "${STUB_GPP_FAIL:-}" ] && exit 1
printf '#!/bin/sh\nexit 0\n' > "$out"
chmod +x "$out"
"""
PY3 = r"""#!/bin/sh
# stand-in python3: logs its arguments
{ printf 'PY'; for a in "$@"; do printf '\t%s' "$a"; done; printf '\n'; } >> "$STUB_LOG"
exit 0
"""
# read by bash before run.sh (BASH_ENV): with STUB_RM_FAIL set, rm -rf fails
# and removes nothing; any other rm is the real one
RM_ENV = r"""rm() {
  if [ -n "${STUB_RM_FAIL:-}" ] && [ "${1:-}" = "-rf" ]; then return 1; fi
  command rm "$@"
}
# with STUB_MKTEMP_RACE=N, the first N mktemp -d calls for a run folder in
# host-bridge/ find host-bridge/ and its parent removed just before, as
# another run's cleanup would remove them, and fail; counted in the file
# STUB_RACE_COUNT names (mktemp runs in a subshell). Each such call, and each
# sleep, is logged in order to the file STUB_RACE_LOG names.
sleep() {
  echo "sleep $*" >> "$STUB_RACE_LOG"
  command sleep "$@"
}
mktemp() {
  case "${2:-}" in
    */host-bridge/run_*) echo "mktemp" >> "$STUB_RACE_LOG" ;;
  esac
  if [ -n "${STUB_MKTEMP_RACE:-}" ] && [ "${1:-}" = "-d" ]; then
    case "${2:-}" in
      */host-bridge/run_*)
        n="$(cat "$STUB_RACE_COUNT" 2>/dev/null || echo 0)"
        if [ "$n" -lt "$STUB_MKTEMP_RACE" ]; then
          echo $((n + 1)) > "$STUB_RACE_COUNT"
          d="$(dirname "$2")"
          command rmdir "$d" "$(dirname "$d")"
        fi
        ;;
    esac
  fi
  command mktemp "$@"
}
"""


def tree_copy(dst, run_sh_text=None):
    """host_bridge and main/ copied at their depth under dst, as a tree's
    charge-interposer/firmware/, so run.sh's tree root is dst; run_sh_text,
    if given, is its run.sh. The copy's run.sh."""
    fw = os.path.join(dst, "charge-interposer", "firmware")
    shutil.copytree(os.path.join(FW, "main"), os.path.join(fw, "main"))
    hb = os.path.join(fw, "test", "host_bridge")
    shutil.copytree(HB, hb)
    if run_sh_text is not None:
        with open(os.path.join(hb, "run.sh"), "w", encoding="utf-8", newline="") as f:
            f.write(run_sh_text)
    return os.path.join(hb, "run.sh")


def harness(ctx):
    """run.sh's tool folder in its tree's scratch/"""
    return os.path.join(ctx["root"], "scratch", "charge-interposer-harness")


def run_folders(ctx, calls):
    """the run folders the builds' outputs went to; each must be in the
    tree's scratch/charge-interposer-harness/host-bridge/run_*"""
    base = bash_form(os.path.join(harness(ctx), "host-bridge")) + "/run_"
    ws = set()
    for argv, _has in calls:
        o = bash_form(argv[argv.index("-o") + 1])
        assert o.startswith(base) and "/" in o[len(base):], (o, base)
        ws.add(o[:o.index("/", len(base))])
    return ws


def all_removed(ctx, tmp):
    """nothing left by run.sh: no tool folder in scratch/, nothing in the
    system temp folder (TMPDIR)"""
    assert not os.path.exists(harness(ctx)), (harness(ctx), os.listdir(harness(ctx)))
    sc = os.path.join(ctx["root"], "scratch")
    assert not os.path.exists(sc) or os.listdir(sc) == [], os.listdir(sc)
    assert os.listdir(tmp) == [], os.listdir(tmp)


def stubs(work):
    d = os.path.join(work, "bin")
    os.makedirs(d)
    for name, text in (("g++", GPP), ("python3", PY3)):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return d


def run_sh(ctx, work, env, args=()):
    """run.sh (ctx's) under bash with the stand-ins first on PATH; (rc,
    output, [calls], [python3 calls]): a call is (argv, {file: present in
    the first -I folder})."""
    bindir = stubs(work)
    log = os.path.join(work, "stub.log")
    tmp = os.path.join(work, "tmp")
    os.makedirs(tmp)
    rm_env = os.path.join(work, "rm_env.sh")
    with open(rm_env, "w", encoding="utf-8", newline="\n") as f:
        f.write(RM_ENV)
    e = dict(os.environ)
    for k in ("TWAI_FAKE", "TWAI_FAKE_DIR", "INTERPOSER_CODE", "STUB_GPP_FAIL",
              "STUB_RM_FAIL", "STUB_MKTEMP_RACE"):
        e.pop(k, None)
    e.update(PATH=bindir + os.pathsep + e.get("PATH", ""), STUB_LOG=fwd(log),
             TMPDIR=fwd(tmp), L3_STIMULUS="unused", BASH_ENV=fwd(rm_env),
             STUB_RACE_COUNT=fwd(os.path.join(work, "race_count")),
             STUB_RACE_LOG=fwd(os.path.join(work, "race_log")))
    e.update(env)
    p = subprocess.run([bash(), fwd(ctx["run_sh"])] + list(args), env=e,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    calls, py = [], []
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            for line in f.read().splitlines():
                parts = line.split("\t")
                if parts[0] == "CALL":
                    calls.append((parts[1:], {}))
                elif parts[0] in ("HAS", "NO"):
                    calls[-1][1][parts[1]] = parts[0] == "HAS"
                elif parts[0] == "PY":
                    py.append(parts[1:])
    return p.returncode, p.stdout, calls, py, tmp


def fake_dir(work, extra=None):
    """A stand-in shared fake: twai_fake.c holding each mutant's text once,
    cpp/twai_fake.cpp, and a mutate.py with MUTANTS."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_sfc", CANFAIL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    d = os.path.join(work, "fake")
    os.makedirs(os.path.join(d, "cpp"))
    muts = [(n, "/* old %d */ x = %d;" % (i, i), "/* new %d */ x = -%d;" % (i, i))
            for i, n in enumerate(m.NAMES)] + (extra or [])
    with open(os.path.join(d, "twai_fake.c"), "w", encoding="utf-8", newline="\n") as f:
        f.write("int x;\n" + "\n".join(o for _n, o, _w in muts[:len(m.NAMES)]) + "\n")
    with open(os.path.join(d, "cpp", "twai_fake.cpp"), "w", encoding="utf-8", newline="\n") as f:
        f.write("// fake\n")
    with open(os.path.join(d, "mutate.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("MUTANTS = %r\n" % (muts,))
    return d


# ---- run.sh -----------------------------------------------------------------
def test_refusals(ctx, work):
    """run.sh refuses, rc 2 and before any build: an unknown TWAI_FAKE,
    TWAI_FAKE=shared with no TWAI_FAKE_DIR or one without the fake's
    sources, and an unknown argument; and leaves nothing behind."""
    fd = fake_dir(work)
    os.remove(os.path.join(fd, "cpp", "twai_fake.cpp"))
    cases = [({"TWAI_FAKE": "bogus"}, ()), ({"TWAI_FAKE": "shared"}, ()),
             ({"TWAI_FAKE": "shared", "TWAI_FAKE_DIR": fwd(fd)}, ()), ({}, ("--x",))]
    for i, (env, args) in enumerate(cases):
        w = os.path.join(work, "r%d" % i)
        os.makedirs(w)
        rc, outp, calls, _py, tmp = run_sh(ctx, w, env, args)
        assert rc == 2 and not calls, (env, args, rc, outp[-300:], len(calls))
        all_removed(ctx, tmp)


def _check_own(ctx, calls, outp, tmp):
    tests = sorted(n for n in os.listdir(HB) if n.startswith("test_") and n.endswith(".cpp"))
    assert len(calls) == len(tests), (len(calls), tests)
    for argv, has in calls:
        i = argv.index("-I")
        assert fwd(argv[i + 1]).endswith("/firmware/test/host_bridge"), argv
        assert argv[i + 2] == "-I" and fwd(argv[i + 3]).endswith("/main"), argv
        assert not any("twai_fake.c" == os.path.basename(a) for a in argv), argv
        assert any(fwd(a).endswith("/host_bridge/twai_fake.cpp") for a in argv), argv
        assert argv.count("-I") == 2, argv
        assert has["twai_fake.h"] and has["driver/twai.h"], has
        assert has["../../main/main.cpp"], has
    assert "TWAI" not in outp, outp
    assert len(run_folders(ctx, calls)) == 1, run_folders(ctx, calls)
    all_removed(ctx, tmp)


def test_own_default(ctx, work):
    """Today's behaviour is the default: with TWAI_FAKE unset, and with
    TWAI_FAKE=own, every test builds from host_bridge itself, with our own
    twai_fake.cpp, -I host_bridge -I main and nothing more, and nothing
    extra is printed; every binary in one run folder under the tree's
    scratch/charge-interposer-harness/host-bridge/, nothing in the system
    temp folder, and nothing left when run.sh ends."""
    for i, env in enumerate(({}, {"TWAI_FAKE": "own"})):
        w = os.path.join(work, "o%d" % i)
        os.makedirs(w)
        rc, outp, calls, _py, tmp = run_sh(ctx, w, env)
        assert rc == 0, (env, rc, outp[-300:])
        _check_own(ctx, calls, outp, tmp)


def test_shared_build(ctx, work):
    """TWAI_FAKE=shared builds as acceptance E did: from a copy of
    host_bridge, at its depth beside a copy of main/ so a test's
    ../../main/main.cpp is reached, without our twai_fake.h, twai_fake.cpp
    and driver/twai.h;
    each test given the shared twai_fake.c and cpp/twai_fake.cpp right after
    it; -I <copy> -I main -I <fake>/include -I <fake>/host -I <fake>/cpp;
    the copy and the binaries in one run folder under the tree's
    scratch/charge-interposer-harness/host-bridge/, nothing in the system
    temp folder, and nothing left when run.sh ends; nothing extra
    printed."""
    fd = fwd(fake_dir(work))
    rc, outp, calls, _py, tmp = run_sh(ctx, work, {"TWAI_FAKE": "shared", "TWAI_FAKE_DIR": fd})
    assert rc == 0, (rc, outp[-300:])
    tests = sorted(n for n in os.listdir(HB) if n.startswith("test_") and n.endswith(".cpp"))
    assert len(calls) == len(tests), (len(calls), tests)
    ws = run_folders(ctx, calls)
    assert len(ws) == 1, ws
    copies = set()
    for argv, has in calls:
        i = argv.index("-I")
        b = argv[i + 1]
        copies.add(b)
        assert bash_form(b) == list(ws)[0] + "/shared/test/host_bridge", (b, ws)
        assert fwd(argv[i + 3]).endswith("/main"), argv
        assert argv[i + 4:i + 10] == ["-I", fd + "/include", "-I", fd + "/host",
                                      "-I", fd + "/cpp"], argv
        o = argv.index("-o")
        srcs = argv[o + 2:]
        # the test itself from the copy too: a test in host_bridge/ would
        # find our twai_fake.h beside it first
        assert bash_form(srcs[0]).startswith(bash_form(b) + "/test_"), (srcs[0], b)
        assert srcs[1:3] == [fd + "/twai_fake.c", fd + "/cpp/twai_fake.cpp"], srcs
        assert not any(fwd(a).endswith("host_bridge/twai_fake.cpp") or
                       os.path.basename(a) == "twai_fake.cpp" and not a.startswith(fd)
                       for a in srcs), srcs
        assert not has["twai_fake.h"] and not has["twai_fake.cpp"] and \
            not has["driver/twai.h"] and has["test_l2_bridge.cpp"], has
        # the copy at host_bridge's depth, beside a copy of main/: a test's
        # #include "../../main/main.cpp" reaches it (2026-11-02's smoke run
        # found test_event_layout not built from a copy without it)
        assert has["../../main/main.cpp"], has
    assert len(copies) == 1, copies
    all_removed(ctx, tmp)
    assert "TWAI" not in outp, outp


def test_removed_on_fail(ctx, work):
    """A run whose builds all fail (each "DID NOT BUILD", rc 1) leaves
    nothing behind either, on our own fake and on the shared one."""
    fd = fwd(fake_dir(work))
    tests = sorted(n for n in os.listdir(HB) if n.startswith("test_") and n.endswith(".cpp"))
    for i, env in enumerate(({}, {"TWAI_FAKE": "shared", "TWAI_FAKE_DIR": fd})):
        w = os.path.join(work, "f%d" % i)
        os.makedirs(w)
        rc, outp, calls, _py, tmp = run_sh(ctx, w, dict(env, STUB_GPP_FAIL="1"))
        assert rc == 1 and outp.count("DID NOT BUILD") == len(tests), (env, rc, outp[-300:])
        assert len(calls) == len(tests), (env, len(calls))
        all_removed(ctx, tmp)


def test_leftover_reported(ctx, work):
    """A run folder run.sh cannot remove is printed with its path, the run's
    exit code unchanged, and the tool folders above it stay (rmdir only
    takes empty ones)."""
    rc, outp, calls, _py, _tmp = run_sh(ctx, work, {"STUB_RM_FAIL": "1"})
    try:
        assert rc == 0, (rc, outp[-300:])
        ws = run_folders(ctx, calls)
        assert len(ws) == 1, ws
        w = list(ws)[0]
        assert "run.sh: could not remove %s" % w in outp, (w, outp[-300:])
        assert os.path.isdir(harness(ctx)), harness(ctx)
    finally:
        left = scratch_dir.remove(harness(ctx))
        assert not left, left


def race_count(work):
    try:
        with open(os.path.join(work, "race_count"), encoding="utf-8") as f:
            return int(f.read().strip())
    except FileNotFoundError:
        return 0


def race_log(work):
    try:
        with open(os.path.join(work, "race_log"), encoding="utf-8") as f:
            return f.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


# three tries, with scratch_dir.make's waits before the second and third
THREE_TRIES = ["mktemp", "sleep 0.1", "mktemp", "sleep 0.4", "mktemp"]


def test_race_retried(ctx, work):
    """Another run's cleanup removing host-bridge/ and its parent between
    run.sh's mkdir -p and its mktemp (twice running) costs nothing: both are
    made again, after a wait of 0.1 s and then 0.4 s, the third try makes
    the run folder, the run is as usual, nothing from the failed tries is
    printed, and nothing is left. With no race: one try, no wait."""
    rc, outp, calls, _py, tmp = run_sh(ctx, work, {"STUB_MKTEMP_RACE": "2"})
    assert race_count(work) == 2, race_count(work)
    assert race_log(work) == THREE_TRIES, race_log(work)
    assert rc == 0, (rc, outp[-300:])
    assert "mktemp" not in outp and "No such file" not in outp, outp[-300:]
    _check_own(ctx, calls, outp, tmp)
    w = os.path.join(work, "plain")
    os.makedirs(w)
    rc, outp, calls, _py, tmp = run_sh(ctx, w, {})
    assert rc == 0 and race_log(w) == ["mktemp"], (rc, race_log(w))


def test_race_bounded(ctx, work):
    """A race lost three times running is a refusal, rc 2 and before any
    build, after exactly three tries, the same waits between them, with the
    last try's reason printed, and nothing left behind."""
    rc, outp, calls, _py, tmp = run_sh(ctx, work, {"STUB_MKTEMP_RACE": "9"})
    assert race_count(work) == 3, race_count(work)
    assert race_log(work) == THREE_TRIES, race_log(work)
    assert rc == 2 and not calls, (rc, outp[-300:], len(calls))
    assert "mktemp" in outp, outp[-300:]
    all_removed(ctx, tmp)


def test_canfail_mode(ctx, work):
    """run.sh --can-fail runs the shared fake's can-fail
    (harness_checks/shared_fake_canfail.py) with python3, and builds
    nothing itself."""
    rc, outp, calls, py, _t = run_sh(ctx, work, {}, ("--can-fail",))
    assert rc == 0 and not calls, (rc, outp[-300:], calls)
    assert len(py) == 1 and fwd(py[0][0]).endswith("/harness_checks/shared_fake_canfail.py") \
        and len(py[0]) == 1, py


# ---- the can-fail -----------------------------------------------------------
def same_runner(changed=(), control_differs=False, record=None):
    """A stand-in suite run: our own fake prints two ok lines; the shared fake
    prints the same unless its twai_fake.c differs from the original (then a
    FAIL line), except for the mutants in changed, whose change it does not
    see."""
    def runner(env, log):
        if record is not None:
            record.append(dict(env))
        lines = ["ok one", "ok two"]
        if env.get("TWAI_FAKE") == "shared":
            with open(os.path.join(env["TWAI_FAKE_DIR"], "twai_fake.c"), encoding="utf-8") as f:
                src = f.read()
            if "/* new " in src and not any("/* new %d */" % i in src for i in changed):
                lines.append("FAIL a fake behaviour")
            if control_differs:
                lines.append("ok extra")
        with open(log, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return 1
    return runner


def judge(ctx, work, runner):
    out = []
    fd = fake_dir(os.path.join(work, "f"))
    w = os.path.join(work, "w")
    os.makedirs(w)
    rc = ctx["canfail"].judge(fd, w, runner, out.append)
    return rc, "\n".join(out), fd


def test_canfail_judges(ctx, work):
    """The can-fail passes only when the control (the shared fake
    unchanged) compares the same as our own fake and every mutant compares
    different; a mutant the comparison does not see, or a control that
    differs, is a FAIL. Every shared run is TWAI_FAKE=shared on its own
    fresh copy of the fake, and the fake itself is never changed."""
    rec = []
    rc, out, fd = judge(ctx, os.path.join(work, "a"), same_runner(record=rec))
    assert rc == 0 and out.count("-- caught") == 3 and "the same" in out, out
    shared = [e for e in rec if e.get("TWAI_FAKE") != "own"]
    assert len(rec) == 5 and len(shared) == 4, rec
    assert all(e["TWAI_FAKE"] == "shared" for e in shared), rec
    dirs = [e["TWAI_FAKE_DIR"] for e in shared]
    assert len(set(dirs)) == 4 and fd not in dirs, dirs
    with open(os.path.join(fd, "twai_fake.c"), encoding="utf-8") as f:
        assert "/* new " not in f.read()
    rc, out, _ = judge(ctx, os.path.join(work, "b"), same_runner(changed=(1,)))
    assert rc == 1 and "NOT CAUGHT" in out, out
    rc, out, _ = judge(ctx, os.path.join(work, "c"), same_runner(control_differs=True))
    assert rc == 1 and "NOT THE SAME" in out, out


def test_canfail_refusals(ctx, work):
    """Refused before any run: a mutant name the fake's mutate.py does not
    hold, a mutant text not found exactly once; and from main(): no
    TWAI_FAKE_DIR, an argument, INTERPOSER_CODE naming another tree."""
    C = ctx["canfail"]
    ran = []
    fd = fake_dir(os.path.join(work, "a"))
    p = os.path.join(fd, "mutate.py")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace(C.NAMES[0], "something else"))
    try:
        C.judge(fd, work, lambda e, l: ran.append(e) or 0, lambda s: None)
    except C.Refused:
        pass
    else:
        raise AssertionError("an unknown mutant name was taken")
    fd = fake_dir(os.path.join(work, "b"))
    with open(os.path.join(fd, "twai_fake.c"), "a", encoding="utf-8", newline="\n") as f:
        f.write("/* old 0 */ x = 0;\n")
    try:
        C.judge(fd, work, lambda e, l: ran.append(e) or 0, lambda s: None)
    except C.Refused:
        pass
    else:
        raise AssertionError("a mutant text found twice was taken")
    assert not ran, ran
    with env_set(TWAI_FAKE_DIR=None, INTERPOSER_CODE=None):
        assert C.main([], runner=lambda e, l: ran.append(e) or 0, out=lambda s: None) == 2
    with env_set(TWAI_FAKE_DIR=fd, INTERPOSER_CODE=None):
        assert C.main(["x"], runner=lambda e, l: ran.append(e) or 0, out=lambda s: None) == 2
    with env_set(TWAI_FAKE_DIR=fd, INTERPOSER_CODE=work):
        assert C.main([], runner=lambda e, l: ran.append(e) or 0, out=lambda s: None) == 2
    assert not ran, ran


@contextlib.contextmanager
def env_set(**kw):
    old = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


TESTS = [test_refusals, test_own_default, test_shared_build, test_removed_on_fail,
         test_leftover_reported, test_race_retried, test_race_bounded,
         test_canfail_mode, test_canfail_judges, test_canfail_refusals]

# run.sh mutants: each is the run.sh of the tree copy (tree_copy) the tests
# then run
RUN_SH_MUTANTS = [
    ("an unknown TWAI_FAKE taken as our own", "  own) ;;\n", "  own|*) ;;\n"),
    ("no fake folder checked",
     '    if [ -z "$SH" ] || [ ! -f "$SH/twai_fake.c" ] || [ ! -f "$SH/cpp/twai_fake.cpp" ]; then',
     '    if false; then'),
    ("our fake kept in the copy", '    rm "$B/twai_fake.h" "$B/twai_fake.cpp" "$B/driver/twai.h"\n',
     ""),
    ("the shared sources not built", 'srcs=("$t" ${FAKE_SRCS[@]+"${FAKE_SRCS[@]}"})',
     'srcs=("$t")'),
    ("the shared includes first", '-I "$B" -I "$SRC" ${FAKE_INC[@]+"${FAKE_INC[@]}"}',
     '${FAKE_INC[@]+"${FAKE_INC[@]}"} -I "$B" -I "$SRC"'),
    ("the binaries in the system temp folder", 'bin="$W/$name"',
     'bin="${TMPDIR:-/tmp}/${name}_$$"'),
    ("the run folder in the system temp folder",
     'W="$(mktemp -d "$WT/host-bridge/run_XXXXXX" 2>/dev/null)" && break',
     'W="$(mktemp -d "${TMPDIR:-/tmp}/run_XXXXXX" 2>/dev/null)" && break'),
    ("one try only", "for try in 1 2; do", "for try in ; do"),
    ("tries without end", "for try in 1 2; do", "for try in 1 2 3 4 5 6 7 8 9 10 11 12; do"),
    ("no wait before the second try", '  [ "$try" = 1 ] || sleep 0.1\n', ""),
    ("no wait before the last try", "  sleep 0.4\n", ""),
    ("a wait before the first try too", '  [ "$try" = 1 ] || sleep 0.1\n',
     "  sleep 0.1\n"),
    ("the tool folders not made again for the last try",
     '  mkdir -p "$WT/host-bridge" || exit 2\n', ""),
    ("the failed tries printed",
     'W="$(mktemp -d "$WT/host-bridge/run_XXXXXX" 2>/dev/null)" && break',
     'W="$(mktemp -d "$WT/host-bridge/run_XXXXXX")" && break'),
    ("the last try's reason not printed",
     '  W="$(mktemp -d "$WT/host-bridge/run_XXXXXX")" || exit 2\n',
     '  W="$(mktemp -d "$WT/host-bridge/run_XXXXXX" 2>/dev/null)" || exit 2\n'),
    ("the copy outside the run folder", '    T="$W/shared"\n',
     '    T="$(mktemp -d "${TMPDIR:-/tmp}/host_bridge_shared_XXXXXX")" || exit 2\n'),
    ("nothing removed on exit", "trap cleanup EXIT\n", ""),
    ("the run folder left behind", '  rm -rf "$W"\n', ""),
    ("the tool folders left behind", '  rmdir "$WT/host-bridge" "$WT" 2>/dev/null || true\n', ""),
    ("a leftover not reported",
     '  if [ -e "$W" ]; then echo "run.sh: could not remove $W" >&2; fi\n', ""),
    ("the copy not at host_bridge's depth", '    B="$T/test/host_bridge"\n', '    B="$T/hb"\n'),
    ("no main/ beside the copy", '    cp -R "$SRC/." "$T/main/" || exit 2\n', ""),
    ("built from host_bridge itself", 'for t in "$B"/test_*.cpp; do',
     'for t in "$HERE"/test_*.cpp; do'),
    ("the mode not the can-fail", '  --can-fail) exec python3 "$HERE/../../../harness_checks/'
     'shared_fake_canfail.py" ;;', '  --can-fail) ;;'),
]


def load_canfail():
    import importlib.util
    spec = importlib.util.spec_from_file_location("shared_fake_canfail_under_test", CANFAIL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_all(ctx, base, run_sh_text=None):
    """every test, with run.sh from a tree copy of its own under base"""
    tree = os.path.join(base, "tree")
    ctx = dict(ctx, run_sh=tree_copy(tree, run_sh_text), root=tree)
    bad = []
    for t in TESTS:
        work = os.path.join(base, t.__name__)
        os.makedirs(work)
        try:
            t(ctx, work)
            print("ok    %s" % t.__name__, flush=True)
        except Exception as e:
            print("FAIL  %s  %s: %s" % (t.__name__, type(e).__name__, str(e)[:240]), flush=True)
            bad.append(t.__name__)
    return bad


def main(argv):
    with scratch_dir.run("twai-fake-option-test") as base:
        ctx = {"canfail": load_canfail()}
        if "--can-fail" not in argv:
            bad = run_all(ctx, os.path.join(base, "tests"))
            print("\n%d of %d passed" % (len(TESTS) - len(bad), len(TESTS)))
            return 1 if bad else 0
        print("-- control", flush=True)
        bad = run_all(ctx, os.path.join(base, "control"))
        if bad:
            print("\nREFUSED: the control fails (%s)" % ", ".join(bad))
            return 1
        caught = total = 0
        for rule in sorted(ctx["canfail"].RULES):
            total += 1
            m = load_canfail()
            m.RULES[rule] = False
            print("-- rule %s off" % rule, flush=True)
            bad = run_all(dict(ctx, canfail=m), os.path.join(base, "rule-" + rule))
            caught += bool(bad)
            print("%s  rule %s off: %s" % ("ok  " if bad else "FAIL", rule,
                                          ("fails " + ", ".join(bad)) if bad else
                                          "STILL PASSES -- nothing checks it"), flush=True)
        with open(os.path.join(HB, "run.sh"), encoding="utf-8", newline="") as f:
            src = f.read()
        for n, (name, old, new) in enumerate(RUN_SH_MUTANTS, 1):
            total += 1
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)))
                continue
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_all(ctx, os.path.join(base, "m%d" % n), src.replace(old, new))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad else
                                      "STILL PASSES -- nothing checks it"), flush=True)
        print("\n%d of %d mutants caught" % (caught, total))
        return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
