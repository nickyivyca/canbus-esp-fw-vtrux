#!/usr/bin/env python3
"""Tests of how host_bridge builds on the shared vehicle port and TWAI
driver fake (fw_harness/host_bridge/run.sh and its l2_build.sh), and of
the shared fake's can-fail (harness_checks/shared_fake_canfail.py). No
compiler, no hardware: run.sh is run by bash with stand-in gcc, g++ and
python3 that record what they were given, and the can-fail is judged on
stand-in suite runs.

    py -3.14 -B charge-interposer/twai_fake_option_test.py [--can-fail]

Tester, 2026-11-02 (the charge-interposer reviewer's order: port step 1b's
acceptance E as committed harness, so the acceptance runner can re-run
it), when run.sh had a TWAI_FAKE option (own or shared). Port step 2b
(tester, 2026-11-08; the reviewer's rulings for 2b): our own fake and the
option are retired, and run.sh builds on the shared fake only, compiling
the real twai_port and can_port as C. The file keeps its name (the suite
lists and the file map name it); what it tests:
  - refusals, rc 2 and before any compile: an unknown argument; a
    TWAI_FAKE_DIR whose checkout lacks any of the seven shared files;
    with no TWAI_FAKE_DIR, the pinned checkout not found; and the pins not
    the firmware's versions (shared_pin.py idf-match, run first);
  - the build: each C source once, by gcc as C11, to the run folder; each
    test by g++ with our stand-ins first on the include path, then main/,
    then the fake's include/, host/ and cpp/, then twai_port's and
    can_port's include/; the fake's C++ interface and object in every
    test; the vehicle port's sources and the port objects in the two that
    build the bridge and in test_canport_contract (both ports through
    CanPort&); no file of our retired fake anywhere;
  - with no TWAI_FAKE_DIR, the pinned folders from shared_pin.py where,
    can_port's by its own where;
  - a C source that does not build: its line, rc 1, and the tests that
    need its object not built;
  - the test list and closing lines, where run.sh works and what it
    leaves, the retries, and the can-fail mode, as before 2b.

Where run.sh works (the charge-interposer reviewer's order of
2026-11-02): one run folder under the tree's own scratch/cih/host-bridge/
(scratch_dir.py's short root, since 2026-11-05), never the system temp
folder (the tests set TMPDIR, which must stay empty), removed when run.sh
ends, pass, fail or refusal, with the tool folders above it, and a folder
it cannot remove printed. Another run's cleanup removing the tool folders
between run.sh making them and making its run folder in them costs a
retry, three tries in all, with waits of 0.1 s and 0.4 s before the second
and third, as scratch_dir.make's: a stand-in mktemp, from BASH_ENV, removes
them first, and it and a stand-in sleep log the order of tries and waits.

--can-fail runs every test first unchanged (the control, which must pass),
then with each of the can-fail's RULES switched off, then with run.sh or
l2_build.sh mutated; each must make a test fail.

run.sh always runs from a copy of host_bridge and firmware/main/ at their
depth under a test folder of its own (tree_copy), so its tree's root, and
its scratch/, are that folder's. On Windows bash is Git Bash by its full
path (a bare "bash" there is WSL's, regress.py); elsewhere bash. Git Bash
puts its own folders first on PATH, so the stand-in rm is a function from
BASH_ENV, not a file on PATH. Throwaway folders come from scratch_dir.py
and are removed when the run ends.
"""

import contextlib
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                    # noqa: E402

FW = os.path.join(HERE, "firmware")
HB = os.path.join(HERE, "fw_harness", "host_bridge")
# test_event_layout's #include, from host_bridge's own folder
MAIN_CPP = "../../firmware/main/main.cpp"
CANFAIL = os.path.join(HERE, "harness_checks", "shared_fake_canfail.py")
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
# the C sources run.sh compiles, by object name: (component, file)
C_SRCS = {"twai_fake": ("fake", "twai_fake.c"),
          "twai_port": ("tp", "twai_port.c"),
          "twai_port_ll": ("tp", "twai_port_ll_unsupported.c"),
          "can_port": ("cp", "can_port.c")}
PORT_OBJS = ("twai_port.o", "twai_port_ll.o", "can_port.o")
# main/'s sources of the two tests that build the bridge
L2_FW = ("main.cpp", "port_mcp2515.cpp", "vehicle_port.cpp", "port_view.cpp",
         "charger_port.cpp", "machine.cpp")
EVENT_FW = ("port_mcp2515.cpp", "vehicle_port.cpp", "port_view.cpp",
            "charger_port.cpp", "machine.cpp")
# test_canport_contract's: both ports through CanPort& (since 2026-11-09 the
# vehicle port's PortView too), so it links the port objects
CONTRACT_FW = ("port_mcp2515.cpp", "vehicle_port.cpp", "port_view.cpp")
# the tests that link the port objects
PORT_TESTS = ("test_canport_contract", "test_event_layout", "test_l2_bridge")
# the tests that compile the MCP2515 driver and no other main/ source
DRIVER_TESTS = ("test_tx_failed", "test_mcp2515_busoff", "test_tx_throughput",
                "test_sameid_hold", "test_tx_stall_trace", "test_order_witness")
# each test's -D flags (none for the rest)
TEST_DEFS = {"test_l2_bridge": ("-DINTP_NO_SERIAL=1",),
             "test_event_layout": ("-DHOST_SERIAL_CAPTURE=1",),
             "test_tx_stall_trace": ("-DINTP_TX_TRACE=1",),
             "test_order_witness": ("-DINTP_ORDER_WITNESS=1",)}
RETIRED = ("twai_fake.h", "twai_fake.cpp", "driver/twai.h")


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


def norm(p):
    """p in bash's form with its ../ resolved, as a path compares: run.sh
    hands the compilers main/ as $HERE/../../firmware/main."""
    return posixpath.normpath(bash_form(p))


def same(a, b):
    return norm(a) == norm(b)


# ---- stand-ins --------------------------------------------------------------
# one stand-in for gcc and g++ (the log names which)
CC = r"""#!/bin/sh
# stand-in compiler: logs its name and arguments (tab-separated), what the
# first -I folder holds and whether the first source after -o's output
# reaches ../../firmware/main/main.cpp from its own folder, as
# test_event_layout's #include does; writes an output that runs and prints
# nothing
tool="$(basename "$0")"
out=""; first=""; prev=""; t=""
for a in "$@"; do
  if [ -n "$out" ] && [ -z "$t" ]; then t="$a"; fi
  [ "$prev" = "-o" ] && out="$a"
  [ "$prev" = "-I" ] && [ -z "$first" ] && first="$a"
  prev="$a"
done
{
  printf 'CALL\t%s' "$tool"
  for a in "$@"; do printf '\t%s' "$a"; done
  printf '\n'
  for f in twai_fake.h twai_fake.cpp driver/twai.h test_l2_bridge.cpp; do
    if [ -e "$first/$f" ]; then printf 'HAS\t%s\n' "$f"; else printf 'NO\t%s\n' "$f"; fi
  done
  f="../../firmware/main/main.cpp"
  if [ -f "$(dirname "$t")/$f" ]; then printf 'HAS\t%s\n' "$f"; else printf 'NO\t%s\n' "$f"; fi
} >> "$STUB_LOG"
# STUB_GPP_FAIL: every g++ build fails; STUB_GCC_FAIL=<text>: a gcc
# compile whose arguments contain <text> fails
if [ "$tool" = "g++" ] && [ -n "${STUB_GPP_FAIL:-}" ]; then exit 1; fi
if [ "$tool" = "gcc" ] && [ -n "${STUB_GCC_FAIL:-}" ]; then
  case "$*" in *"$STUB_GCC_FAIL"*) exit 1 ;; esac
fi
printf '#!/bin/sh\nexit 0\n' > "$out"
chmod +x "$out"
"""
PY3 = r"""#!/bin/sh
# stand-in python3: logs its arguments. shared_pin.py's idf-match exits
# STUB_IDF_RC (default 0); its `where shared/twai_port` / `where
# shared/can_port` print STUB_WHERE_TP / STUB_WHERE_CP, or fail when unset
{ printf 'PY'; for a in "$@"; do printf '\t%s' "$a"; done; printf '\n'; } >> "$STUB_LOG"
case "${2:-} ${3:-}" in
  "idf-match ") exit "${STUB_IDF_RC:-0}" ;;
  "where shared/twai_port") [ -n "${STUB_WHERE_TP:-}" ] || exit 1; echo "$STUB_WHERE_TP" ;;
  "where shared/can_port") [ -n "${STUB_WHERE_CP:-}" ] || exit 1; echo "$STUB_WHERE_CP" ;;
esac
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


def tree_copy(dst, texts=None):
    """host_bridge and main/ copied at their depth under dst, as a tree's
    charge-interposer/fw_harness/host_bridge and charge-interposer/firmware/
    main, so run.sh's tree root is dst; texts, {file in host_bridge: text},
    replace those files in the copy. The copy's run.sh."""
    ci = os.path.join(dst, "charge-interposer")
    shutil.copytree(os.path.join(FW, "main"), os.path.join(ci, "firmware", "main"))
    hb = os.path.join(ci, "fw_harness", "host_bridge")
    shutil.copytree(HB, hb)
    for name, text in (texts or {}).items():
        with open(os.path.join(hb, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)
    return os.path.join(hb, "run.sh")


def harness(ctx):
    """run.sh's tool folder in its tree's scratch/"""
    return os.path.join(ctx["root"], "scratch", "cih")


def out_of(call):
    return call["argv"][call["argv"].index("-o") + 1]


def run_folders(ctx, calls):
    """the run folders the compilers' outputs went to; each must be in the
    tree's scratch/cih/host-bridge/run_*"""
    base = bash_form(os.path.join(harness(ctx), "host-bridge")) + "/run_"
    ws = set()
    for c in calls:
        o = bash_form(out_of(c))
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
    for name, text in (("gcc", CC), ("g++", CC), ("python3", PY3)):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return d


def run_sh(ctx, work, env, args=()):
    """run.sh (ctx's) under bash with the stand-ins first on PATH; (rc,
    output, [calls], [python3 calls], tmp): a call is {"tool", "argv",
    "has": {file: present in the first -I folder}}; py lists every python3
    call, in order, with "CC" between them where a compile came."""
    bindir = stubs(work)
    log = os.path.join(work, "stub.log")
    tmp = os.path.join(work, "tmp")
    os.makedirs(tmp)
    rm_env = os.path.join(work, "rm_env.sh")
    with open(rm_env, "w", encoding="utf-8", newline="\n") as f:
        f.write(RM_ENV)
    e = dict(os.environ)
    for k in ("TWAI_FAKE", "TWAI_FAKE_DIR", "INTERPOSER_CODE", "STUB_GPP_FAIL",
              "STUB_GCC_FAIL", "STUB_RM_FAIL", "STUB_MKTEMP_RACE", "STUB_IDF_RC",
              "STUB_WHERE_TP", "STUB_WHERE_CP"):
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
                    calls.append({"tool": parts[1], "argv": parts[2:], "has": {}})
                    if not py or py[-1] != "CC":
                        py.append("CC")
                elif parts[0] in ("HAS", "NO"):
                    calls[-1]["has"][parts[1]] = parts[0] == "HAS"
                elif parts[0] == "PY":
                    py.append(parts[1:])
    return p.returncode, p.stdout, calls, py, tmp


def fake_dir(work, extra=None, name="co"):
    """A stand-in checkout <work>/<name>/shared/ with twai_port (its two C
    sources, include/twai_port.h, test/ with a twai_fake.c holding each
    can-fail mutant's text once, cpp/twai_fake.cpp and a mutate.py with
    MUTANTS) and can_port (can_port.c, include/can_port.h). Its
    shared/twai_port/test."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_sfc", CANFAIL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sh = os.path.join(work, name, "shared")
    d = os.path.join(sh, "twai_port", "test")
    muts = [(n, "/* old %d */ x = %d;" % (i, i), "/* new %d */ x = -%d;" % (i, i))
            for i, n in enumerate(m.NAMES)] + (extra or [])
    files = {
        os.path.join(d, "twai_fake.c"):
            "int x;\n" + "\n".join(o for _n, o, _w in muts[:len(m.NAMES)]) + "\n",
        os.path.join(d, "cpp", "twai_fake.cpp"): "// fake\n",
        os.path.join(d, "mutate.py"): "MUTANTS = %r\n" % (muts,),
        os.path.join(sh, "twai_port", "twai_port.c"): "// port\n",
        os.path.join(sh, "twai_port", "twai_port_ll_unsupported.c"): "// ll\n",
        os.path.join(sh, "twai_port", "include", "twai_port.h"): "// h\n",
        os.path.join(sh, "can_port", "can_port.c"): "// can\n",
        os.path.join(sh, "can_port", "include", "can_port.h"): "// h\n",
    }
    for p, text in files.items():
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return d


# the seven files run.sh needs in the checkout, relative to the fake's folder
NEEDED = ("twai_fake.c", "cpp/twai_fake.cpp", "../twai_port.c",
          "../twai_port_ll_unsupported.c", "../include/twai_port.h",
          "../../can_port/can_port.c", "../../can_port/include/can_port.h")


def tests_of(folder=HB):
    return sorted(n[:-4] for n in os.listdir(folder)
                  if n.startswith("test_") and n.endswith(".cpp"))


def idf_first(py):
    """shared_pin.py idf-match is the first python3 call, before any
    compile"""
    assert py and py[0] != "CC" and py[0][1:] == ["idf-match"] and \
        fwd(py[0][0]).endswith("/shared_pin.py"), py


# ---- run.sh -----------------------------------------------------------------
def test_refusals(ctx, work):
    """run.sh refuses, rc 2 and before any compile, and leaves nothing
    behind: an unknown argument; a TWAI_FAKE_DIR whose checkout lacks any
    one of the seven shared files; with no TWAI_FAKE_DIR, the pinned
    twai_port or can_port not found; the pins not the firmware's versions
    (idf-match failing), with a good TWAI_FAKE_DIR."""
    good = fwd(fake_dir(work, name="good"))
    cases = [({"TWAI_FAKE_DIR": good}, ("--x",)), ({}, ()),
             ({"STUB_WHERE_TP": "/nowhere"}, ()),
             ({"TWAI_FAKE_DIR": good, "STUB_IDF_RC": "1"}, ())]
    for i, rel in enumerate(NEEDED):
        fd = fake_dir(work, name="m%d" % i)
        os.remove(os.path.normpath(os.path.join(fd, rel)))
        cases.append(({"TWAI_FAKE_DIR": fwd(fd)}, ()))
    for i, (env, args) in enumerate(cases):
        w = os.path.join(work, "r%d" % i)
        os.makedirs(w)
        rc, outp, calls, _py, tmp = run_sh(ctx, w, env, args)
        assert rc == 2 and not calls, (env, args, rc, outp[-300:], len(calls))
        all_removed(ctx, tmp)


def check_build(ctx, calls, py, outp, tmp, fd, tp, cp):
    """The build as the module says, with the fake's folder fd and the
    components tp and cp."""
    hb_copy = os.path.dirname(ctx["run_sh"])
    main = os.path.join(ctx["root"], "charge-interposer", "firmware", "main")
    inc = [hb_copy, main, fd + "/include", fd + "/host", fd + "/cpp",
           tp + "/include", cp + "/include"]
    idf_first(py)
    cc = [c for c in calls if c["tool"] == "gcc"]
    cxx = [c for c in calls if c["tool"] == "g++"]
    assert len(cc) + len(cxx) == len(calls), [c["tool"] for c in calls]
    # the C sources first, each once, as C11, compile only
    assert calls[:len(cc)] == cc, "the C sources are compiled before the tests"
    objs = {}
    for c in cc:
        a = c["argv"]
        assert "-std=c11" in a and "-c" in a, a
        name = os.path.basename(out_of(c))[:-2]
        assert name in C_SRCS and name not in objs, (name, sorted(objs))
        comp, f = C_SRCS[name]
        want = {"fake": fd, "tp": tp, "cp": cp}[comp] + "/" + f
        assert same(a[-1], want), (a[-1], want)
        objs[name] = out_of(c)
        got = [a[i + 1] for i, x in enumerate(a) if x == "-I"]
        assert len(got) == len(inc) and all(same(g, w) for g, w in zip(got, inc)), \
            (got, inc)
    assert sorted(objs) == sorted(C_SRCS), sorted(objs)
    # every test, by g++
    tests = tests_of()
    built = [os.path.basename(out_of(c)) for c in cxx]
    assert built == tests, (built, tests)
    for c in cxx:
        a, has, name = c["argv"], c["has"], os.path.basename(out_of(c))
        got = [a[i + 1] for i, x in enumerate(a) if x == "-I"]
        assert len(got) == len(inc) and all(same(g, w) for g, w in zip(got, inc)), \
            (name, got, inc)
        srcs = a[a.index("-o") + 2:]
        assert same(srcs[0], os.path.join(hb_copy, name + ".cpp")), (name, srcs[0])
        assert same(srcs[1], fd + "/cpp/twai_fake.cpp"), (name, srcs[1])
        for f in os.listdir(hb_copy):
            if f.endswith(".cpp") and not f.startswith("test_"):
                assert any(same(s, os.path.join(hb_copy, f)) for s in srcs), (name, f)
        assert any(same(s, objs["twai_fake"]) for s in srcs), (name, srcs)
        port = [o for o in PORT_OBJS if any(os.path.basename(s) == o for s in srcs)]
        fw = [os.path.basename(s) for s in srcs if same(os.path.dirname(s), main)]
        if name == "test_l2_bridge":
            assert sorted(port) == sorted(PORT_OBJS), (name, port)
            assert sorted(fw) == sorted(L2_FW), (name, fw)
            assert "-DINTP_NO_SERIAL=1" in a, a
        elif name == "test_event_layout":
            assert sorted(port) == sorted(PORT_OBJS), (name, port)
            assert sorted(fw) == sorted(EVENT_FW), (name, fw)
        elif name == "test_canport_contract":
            assert sorted(port) == sorted(PORT_OBJS), (name, port)
            assert sorted(fw) == sorted(CONTRACT_FW), (name, fw)
        elif name in DRIVER_TESTS:
            assert not port and fw == ["port_mcp2515.cpp"], (name, port, fw)
        else:
            assert not port and not fw, (name, port, fw)
        defs = sorted(x for x in a if x.startswith("-D"))
        assert defs == sorted(TEST_DEFS.get(name, ())), (name, defs)
        assert not any(os.path.basename(s) == "twai_fake.cpp" and not same(s, srcs[1])
                       for s in srcs), (name, srcs)
        for f in RETIRED:
            assert not has[f], (name, f)
        assert has["test_l2_bridge.cpp"] and has[MAIN_CPP], (name, has)
    assert len(run_folders(ctx, calls)) == 1, run_folders(ctx, calls)
    assert "TWAI" not in outp, outp[-300:]
    all_removed(ctx, tmp)


def test_build(ctx, work):
    """With TWAI_FAKE_DIR: idf-match first; each C source once, by gcc as
    C11, to the run folder; every test by g++ with -I <host_bridge> -I main
    -I <fake>/include -I <fake>/host -I <fake>/cpp -I <twai_port>/include
    -I <can_port>/include; the test from host_bridge, then the fake's C++
    interface, every non-test .cpp of host_bridge and the fake's object;
    the bridge's two tests with the vehicle port's sources and the port
    objects; no file of our retired fake in the folder the tests build
    from; one run folder, nothing left, nothing extra printed."""
    fd = fwd(fake_dir(work))
    rc, outp, calls, py, tmp = run_sh(ctx, work, {"TWAI_FAKE_DIR": fd})
    assert rc == 0, (rc, outp[-300:])
    check_build(ctx, calls, py, outp, tmp, fd, fd + "/..", fd + "/../../can_port")


def test_pinned(ctx, work):
    """With no TWAI_FAKE_DIR: after idf-match, twai_port from shared_pin.py
    where shared/twai_port (the fake its test/), and can_port from its own
    where shared/can_port, not from beside twai_port."""
    fd = fake_dir(work, name="a")
    tp = fwd(os.path.dirname(fd))
    other = fake_dir(work, name="b")
    cp = fwd(os.path.join(os.path.dirname(os.path.dirname(other)), "can_port"))
    rc, outp, calls, py, tmp = run_sh(ctx, work, {"STUB_WHERE_TP": tp, "STUB_WHERE_CP": cp})
    assert rc == 0, (rc, outp[-300:])
    wh = [p[1:] for p in py if p != "CC"]
    assert wh[:3] == [["idf-match"], ["where", "shared/twai_port"],
                      ["where", "shared/can_port"]], wh
    check_build(ctx, calls, py, outp, tmp, tp + "/test", tp, cp)


def test_c_source_fails(ctx, work):
    """A C source that does not build: its line "<name> (C, <path>) DID NOT
    BUILD", rc 1; the tests that link its object print "<test> DID NOT
    BUILD (no <object>)" and are not compiled; the others are built and
    run, and nothing is left."""
    fd = fwd(fake_dir(work))
    rc, outp, calls, _py, tmp = run_sh(ctx, work, {"TWAI_FAKE_DIR": fd,
                                                   "STUB_GCC_FAIL": "can_port.c"})
    assert rc == 1, (rc, outp[-300:])
    lines = outp.splitlines()
    assert sum(ln.startswith("can_port (C, ") and ln.endswith(" DID NOT BUILD")
               for ln in lines) == 1, outp[-400:]
    for t in PORT_TESTS:
        assert "%s DID NOT BUILD (no can_port.o)" % t in lines, (t, outp[-400:])
    built = [os.path.basename(out_of(c)) for c in calls if c["tool"] == "g++"]
    assert built == [t for t in tests_of() if t not in PORT_TESTS], built
    all_removed(ctx, tmp)


def test_removed_on_fail(ctx, work):
    """A run whose test builds all fail (each "DID NOT BUILD", rc 1) leaves
    nothing behind either."""
    fd = fwd(fake_dir(work))
    tests = tests_of()
    rc, outp, calls, _py, tmp = run_sh(ctx, work, {"TWAI_FAKE_DIR": fd, "STUB_GPP_FAIL": "1"})
    assert rc == 1 and outp.count("DID NOT BUILD") == len(tests), (rc, outp[-300:])
    assert len([c for c in calls if c["tool"] == "g++"]) == len(tests), len(calls)
    all_removed(ctx, tmp)


LIST_RE = r"^host_bridge tests \((\d+)\): ?(.*)$"
CLOSE_RE = r"^run\.sh: (\S+) exit (\d+)$"


def test_list_and_closing(ctx, work):
    """run.sh prints its test list once, before any build: every test_*.cpp
    of the folder it builds from, its count, in the order it then builds
    them; and after each test one closing line, "run.sh: <test> exit
    <rc>" (the stand-in binaries exit 0), or "<test> DID NOT BUILD" and no
    closing line. With no test in the folder: an empty list, and rc 1
    (follow-up (B), tester, 2026-11-08; bench_suite.host_bridge_judge reads
    these two forms)."""
    fd = fwd(fake_dir(work))
    tests = tests_of()
    for i, fail in enumerate((False, True)):
        w = os.path.join(work, "l%d" % i)
        os.makedirs(w)
        e = {"TWAI_FAKE_DIR": fd}
        if fail:
            e["STUB_GPP_FAIL"] = "1"
        rc, outp, calls, _py, tmp = run_sh(ctx, w, e)
        lists = re.findall(LIST_RE, outp, re.M)
        assert len(lists) == 1, (fail, lists)
        names = lists[0][1].split()
        assert int(lists[0][0]) == len(names) and sorted(names) == tests, (fail, lists)
        built = [os.path.basename(out_of(c)) for c in calls if c["tool"] == "g++"]
        assert built == names, (fail, built, names)
        before = outp.split(re.search(LIST_RE, outp, re.M).group(0), 1)[0]
        assert "DID NOT BUILD" not in before and "run.sh: test_" not in before, before
        closes = re.findall(CLOSE_RE, outp, re.M)
        if fail:
            assert rc == 1 and closes == [], (rc, closes)
            assert [ln for ln in outp.splitlines() if ln.endswith(" DID NOT BUILD")] == \
                ["%s DID NOT BUILD" % t for t in names], outp[-300:]
        else:
            assert rc == 0 and closes == [(t, "0") for t in names], (rc, closes)
        all_removed(ctx, tmp)
    # no test in the folder: the list printed empty, and the run fails
    hb = os.path.dirname(ctx["run_sh"])
    gone = os.path.join(work, "gone")
    os.makedirs(gone)
    for t in tests:
        shutil.move(os.path.join(hb, t + ".cpp"), gone)
    try:
        w = os.path.join(work, "empty")
        os.makedirs(w)
        rc, outp, calls, _py, tmp = run_sh(ctx, w, {"TWAI_FAKE_DIR": fd})
        assert rc == 1 and not [c for c in calls if c["tool"] == "g++"], (rc, len(calls))
        assert re.findall(LIST_RE, outp, re.M) == [("0", "")], outp[-300:]
        all_removed(ctx, tmp)
    finally:
        for t in tests:
            shutil.move(os.path.join(gone, t + ".cpp"), hb)


def test_leftover_reported(ctx, work):
    """A run folder run.sh cannot remove is printed with its path, the run's
    exit code unchanged, and the tool folders above it stay (rmdir only
    takes empty ones)."""
    fd = fwd(fake_dir(work))
    rc, outp, calls, _py, _tmp = run_sh(ctx, work, {"TWAI_FAKE_DIR": fd, "STUB_RM_FAIL": "1"})
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
    fd = fwd(fake_dir(work))
    rc, outp, calls, py, tmp = run_sh(ctx, work, {"TWAI_FAKE_DIR": fd,
                                                  "STUB_MKTEMP_RACE": "2"})
    assert race_count(work) == 2, race_count(work)
    assert race_log(work) == THREE_TRIES, race_log(work)
    assert rc == 0, (rc, outp[-300:])
    assert "mktemp" not in outp and "No such file" not in outp, outp[-300:]
    check_build(ctx, calls, py, outp, tmp, fd, fd + "/..", fd + "/../../can_port")
    w = os.path.join(work, "plain")
    os.makedirs(w)
    rc, outp, calls, _py, tmp = run_sh(ctx, w, {"TWAI_FAKE_DIR": fd})
    assert rc == 0 and race_log(w) == ["mktemp"], (rc, race_log(w))


def test_race_bounded(ctx, work):
    """A race lost three times running is a refusal, rc 2 and before any
    compile, after exactly three tries, the same waits between them, with
    the last try's reason printed, and nothing left behind."""
    fd = fwd(fake_dir(work))
    rc, outp, calls, _py, tmp = run_sh(ctx, work, {"TWAI_FAKE_DIR": fd,
                                                   "STUB_MKTEMP_RACE": "9"})
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
    assert rc == 0 and not calls, (rc, outp[-300:], len(calls))
    assert len(py) == 1 and fwd(py[0][0]).endswith("/harness_checks/shared_fake_canfail.py") \
        and len(py[0]) == 1, py


# ---- the can-fail -----------------------------------------------------------
def same_runner(changed=(), control_differs=False, record=None):
    """A stand-in suite run: two ok lines, and a FAIL line if the fake's
    twai_fake.c differs from the original, except for the mutants in
    changed, whose change it does not see; with control_differs, the second
    run prints one more line."""
    n = [0]

    def runner(env, log):
        n[0] += 1
        if record is not None:
            record.append(dict(env))
        lines = ["ok one", "ok two"]
        with open(os.path.join(env["TWAI_FAKE_DIR"], "twai_fake.c"), encoding="utf-8") as f:
            src = f.read()
        if "/* new " in src and not any("/* new %d */" % i in src for i in changed):
            lines.append("FAIL a fake behaviour")
        if control_differs and n[0] == 2:
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
    """The can-fail passes only when the two controls (the fake unchanged)
    compare the same and every mutant compares different from the first;
    a mutant the comparison does not see, or a control that differs, is a
    FAIL. Every run is on its own fresh copy of twai_port and can_port,
    with TWAI_FAKE_DIR naming the copy's fake, and the fake itself is never
    changed."""
    rec = []
    rc, out, fd = judge(ctx, os.path.join(work, "a"), same_runner(record=rec))
    assert rc == 0 and out.count("-- caught") == 3 and "the same" in out, out
    assert len(rec) == 5, rec
    dirs = [e["TWAI_FAKE_DIR"] for e in rec]
    assert len(set(dirs)) == 5 and fd not in dirs, dirs
    for d in dirs:
        assert os.path.isfile(os.path.join(d, "..", "twai_port.c")) and \
            os.path.isfile(os.path.join(d, "..", "..", "can_port", "can_port.c")), d
    with open(os.path.join(fd, "twai_fake.c"), encoding="utf-8") as f:
        assert "/* new " not in f.read()
    rc, out, _ = judge(ctx, os.path.join(work, "b"), same_runner(changed=(1,)))
    assert rc == 1 and "NOT CAUGHT" in out, out
    rc, out, _ = judge(ctx, os.path.join(work, "c"), same_runner(control_differs=True))
    assert rc == 1 and "NOT THE SAME" in out, out


def test_canfail_refusals(ctx, work):
    """Refused before any run: a mutant name the fake's mutate.py does not
    hold, a mutant text not found exactly once, a fake with no can_port
    beside its twai_port; and from main(): a TWAI_FAKE_DIR without the
    fake, an argument, INTERPOSER_CODE naming another tree."""
    C = ctx["canfail"]
    ran = []

    def refused(fd):
        try:
            C.judge(fd, work, lambda e, l: ran.append(e) or 0, lambda s: None)
        except C.Refused:
            return True
        return False
    fd = fake_dir(work, name="a")
    p = os.path.join(fd, "mutate.py")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace(C.NAMES[0], "something else"))
    assert refused(fd), "an unknown mutant name was taken"
    fd = fake_dir(work, name="b")
    with open(os.path.join(fd, "twai_fake.c"), "a", encoding="utf-8", newline="\n") as f:
        f.write("/* old 0 */ x = 0;\n")
    assert refused(fd), "a mutant text found twice was taken"
    fd = fake_dir(work, name="c")
    shutil.rmtree(os.path.join(fd, "..", "..", "can_port"))
    assert refused(fd), "a fake with no can_port beside it was taken"
    assert not ran, ran
    empty = os.path.join(work, "empty")
    os.makedirs(empty)
    with env_set(TWAI_FAKE_DIR=empty, INTERPOSER_CODE=None):
        assert C.main([], runner=lambda e, l: ran.append(e) or 0, out=lambda s: None) == 2
    fd = fake_dir(work, name="d")
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


TESTS = [test_refusals, test_build, test_pinned, test_c_source_fails, test_removed_on_fail,
         test_list_and_closing, test_leftover_reported, test_race_retried, test_race_bounded,
         test_canfail_mode, test_canfail_judges, test_canfail_refusals]

# mutants: (file in host_bridge, name, old, new); each is applied to the
# tree copy (tree_copy) the tests then run
MUTANTS = [
    # run.sh
    ("run.sh", "an unknown argument taken",
     '  *) echo "usage: run.sh [--can-fail]" >&2; exit 2 ;;', "  *) ;;"),
    ("run.sh", "the mode not the can-fail", '  --can-fail) exec python3 "$HERE/../../'
     'harness_checks/shared_fake_canfail.py" ;;', "  --can-fail) ;;"),
    ("run.sh", "no shared folders refused", "hb_shared || exit 2\n", "hb_shared\n"),
    ("run.sh", "the shared includes first",
     'INC=(-I "$B" -I "$SRC" "${HB_INC_SHARED[@]}")',
     'INC=("${HB_INC_SHARED[@]}" -I "$B" -I "$SRC")'),
    ("run.sh", "the C sources not built", 'hb_c_objs "$W" "$B" "$SRC" || fail=1\n', ""),
    # Not a mutant: "|| fail=1" taken off hb_c_objs. Every C object is linked
    # by some test (the fake's by all of them), so a C source that does not
    # build always leaves a test unbuilt, which fails the run by itself: the
    # mutant is equivalent (the can-fail of 2026-11-09 found it surviving).
    # Since 2026-11-11 each test's sources come from l2_build.sh's
    # hb_test_inputs (below); run.sh takes them from it.
    ("run.sh", "the test's own sources not taken", '  srcs=("${HB_T_SRCS[@]}")\n',
     '  srcs=("${HB_T_SRCS[0]}")\n'),
    ("run.sh", "the test's objects not taken",
     '  objs=(${HB_T_OBJS[@]+"${HB_T_OBJS[@]}"})\n', "  objs=()\n"),
    ("run.sh", "the test's defines not taken",
     '  defs=(${HB_T_DEFS[@]+"${HB_T_DEFS[@]}"})\n', "  defs=()\n"),
    ("run.sh", "every test built as test_l2_bridge",
     '  hb_test_inputs "$name" "$B" "$SRC" "$W"\n',
     '  hb_test_inputs test_l2_bridge "$B" "$SRC" "$W"\n'),
    ("run.sh", "a test missing an object still built", '  if [ -n "$missing" ] ||\n',
     "  if false ||\n"),
    ("run.sh", "the binaries in the system temp folder", 'bin="$W/$name"',
     'bin="${TMPDIR:-/tmp}/${name}_$$"'),
    ("run.sh", "the run folder in the system temp folder",
     'W="$(mktemp -d "$WT/host-bridge/run_XXXXXX" 2>/dev/null)" && break',
     'W="$(mktemp -d "${TMPDIR:-/tmp}/run_XXXXXX" 2>/dev/null)" && break'),
    ("run.sh", "one try only", "for try in 1 2; do", "for try in ; do"),
    ("run.sh", "tries without end", "for try in 1 2; do",
     "for try in 1 2 3 4 5 6 7 8 9 10 11 12; do"),
    ("run.sh", "no wait before the second try", '  [ "$try" = 1 ] || sleep 0.1\n', ""),
    ("run.sh", "no wait before the last try", "  sleep 0.4\n", ""),
    ("run.sh", "a wait before the first try too", '  [ "$try" = 1 ] || sleep 0.1\n',
     "  sleep 0.1\n"),
    ("run.sh", "the tool folders not made again for the last try",
     '  mkdir -p "$WT/host-bridge" || exit 2\n', ""),
    ("run.sh", "the failed tries printed",
     'W="$(mktemp -d "$WT/host-bridge/run_XXXXXX" 2>/dev/null)" && break',
     'W="$(mktemp -d "$WT/host-bridge/run_XXXXXX")" && break'),
    ("run.sh", "the last try's reason not printed",
     '  W="$(mktemp -d "$WT/host-bridge/run_XXXXXX")" || exit 2\n',
     '  W="$(mktemp -d "$WT/host-bridge/run_XXXXXX" 2>/dev/null)" || exit 2\n'),
    ("run.sh", "nothing removed on exit", "trap cleanup EXIT\n", ""),
    ("run.sh", "the run folder left behind", '  rm -rf "$W"\n', ""),
    ("run.sh", "the tool folders left behind",
     '  rmdir "$WT/host-bridge" "$WT" 2>/dev/null || true\n', ""),
    ("run.sh", "a leftover not reported",
     '  if [ -e "$W" ]; then echo "run.sh: could not remove $W" >&2; fi\n', ""),
    # the test list and the closing lines (follow-up (B), 2026-11-08)
    ("run.sh", "no test list printed",
     '\necho "host_bridge tests (${#tests[@]}): ${tests[*]+${tests[*]}}"\n', "\n"),
    ("run.sh", "the list's count wrong",
     'echo "host_bridge tests (${#tests[@]}): ', 'echo "host_bridge tests (99): '),
    ("run.sh", "one test left out of the run", 'for name in ${tests[@]+"${tests[@]}"}; do',
     "for name in ${tests[@]:1}; do"),
    ("run.sh", "an empty list not a failure", '[ "${#tests[@]}" -gt 0 ] || fail=1\n', ""),
    ("run.sh", "no closing line", "  printf '\\nrun.sh: %s exit %s\\n' \"$name\" \"$rc\"\n", ""),
    ("run.sh", "the closing line before the test runs",
     "  rc=0\n", "  printf '\\nrun.sh: %s exit %s\\n' \"$name\" 0\n  rc=0\n"),
    ("run.sh", "a closing line after a failed build too",
     '    echo "$name DID NOT BUILD${missing:+ (no$missing)}"\n',
     '    echo "$name DID NOT BUILD${missing:+ (no$missing)}"\n    echo "run.sh: $name exit 1"\n'),
    ("run.sh", "a missing object not named",
     '    echo "$name DID NOT BUILD${missing:+ (no$missing)}"\n',
     '    echo "$name DID NOT BUILD"\n'),
    # l2_build.sh
    ("l2_build.sh", "idf-match not run", '  python3 "$pin" idf-match >&2 || {', "  true || {"),
    ("l2_build.sh", "idf-match after the folders",
     '  python3 "$pin" idf-match >&2 || {\n', "  { false; } && {\n"),
    ("l2_build.sh", "no shared file checked", '    if [ ! -f "$f" ]; then', "    if false; then"),
    ("l2_build.sh", "can_port from beside twai_port when pinned",
     '      HB_CP="$(python3 "$pin" where shared/can_port)" || {',
     '      HB_CP="$HB_TP/../can_port" || {'),
    ("l2_build.sh", "the pinned fake not twai_port's test/", '    HB_SH="$HB_TP/test"\n',
     '    HB_SH="$HB_TP"\n'),
    ("l2_build.sh", "the C sources compiled as C++", "    if ! gcc -std=c11",
     "    if ! g++ -std=c11"),
    ("l2_build.sh", "the C sources not compile-only", "         -c -o \"$obj/${c%%:*}.o\"",
     "         -o \"$obj/${c%%:*}.o\""),
    ("l2_build.sh", "the C3's register file",
     '"twai_port_ll:$HB_TP/twai_port_ll_unsupported.c"',
     '"twai_port_ll:$HB_TP/twai_port_ll_esp32c3.c"'),
    ("l2_build.sh", "can_port.c not compiled", '\n             "can_port:$HB_CP/can_port.c")',
     ")"),
    ("l2_build.sh", "the fake's includes out of order",
     'HB_INC_SHARED=(-I "$HB_SH/include" -I "$HB_SH/host" -I "$HB_SH/cpp"',
     'HB_INC_SHARED=(-I "$HB_SH/host" -I "$HB_SH/include" -I "$HB_SH/cpp"'),
    ("l2_build.sh", "can_port's include left out", '\n                 -I "$HB_TP/include" -I "$HB_CP/include")',
     '\n                 -I "$HB_TP/include")'),
    ("l2_build.sh", "a failed C source not reported",
     '      echo "${c%%:*} (C, ${c#*:}) DID NOT BUILD"\n', ""),
    # Not a mutant: hb_c_objs's "rc=1" taken out. As "|| fail=1" above, a
    # failed C source leaves its object missing, and every user of it then
    # fails on that (run.sh's missing-object check, l2_build.sh's link).
    ("l2_build.sh", "the bridge without port_view",
     "HB_FW_L2=(main.cpp port_mcp2515.cpp vehicle_port.cpp port_view.cpp",
     "HB_FW_L2=(main.cpp port_mcp2515.cpp vehicle_port.cpp"),
    # hb_test_inputs (moved here from run.sh, 2026-11-11)
    ("l2_build.sh", "the fake not linked", '  HB_T_OBJS=("$obj/twai_fake.o")\n',
     "  HB_T_OBJS=()\n"),
    ("l2_build.sh", "the fake's C++ interface not built",
     '  HB_T_SRCS=("$b/$name.cpp" "$HB_SH/cpp/twai_fake.cpp")\n',
     '  HB_T_SRCS=("$b/$name.cpp")\n'),
    ("l2_build.sh", "a stand-in left out",
     "    case \"$(basename \"$f\")\" in test_*) continue ;; esac\n    HB_T_SRCS+=",
     "    case \"$(basename \"$f\")\" in test_*|idf_host.cpp) continue ;; esac\n    HB_T_SRCS+="),
    ("l2_build.sh", "the bridge without the port objects",
     '      for f in "${HB_FW_L2[@]}"; do HB_T_SRCS+=("$fw/$f"); done\n'
     '      HB_T_OBJS+=("${port_objs[@]}")\n',
     '      for f in "${HB_FW_L2[@]}"; do HB_T_SRCS+=("$fw/$f"); done\n'),
    ("l2_build.sh", "the bridge with serial",
     '      HB_T_OBJS+=("${port_objs[@]}")\n      HB_T_DEFS+=(-DINTP_NO_SERIAL=1)\n',
     '      HB_T_OBJS+=("${port_objs[@]}")\n'),
    ("l2_build.sh", "the event layout without the vehicle port's setup",
     'local ports=("$fw/port_mcp2515.cpp" "$fw/vehicle_port.cpp" "$fw/port_view.cpp"',
     'local ports=("$fw/port_mcp2515.cpp" "$fw/port_view.cpp"'),
    ("l2_build.sh", "the contract test without the vehicle port",
     '      HB_T_SRCS+=("$fw/port_mcp2515.cpp" "$fw/vehicle_port.cpp" "$fw/port_view.cpp")\n'
     '      HB_T_OBJS+=("${port_objs[@]}")\n',
     '      HB_T_SRCS+=("$fw/port_mcp2515.cpp")\n'),
    ("l2_build.sh", "the driver tests without the driver",
     '    test_tx_failed|test_mcp2515_busoff|test_tx_throughput|test_sameid_hold)\n'
     '      HB_T_SRCS+=("$fw/port_mcp2515.cpp") ;;\n',
     '    test_tx_failed|test_mcp2515_busoff|test_tx_throughput|test_sameid_hold) ;;\n'),
    ("l2_build.sh", "the stall trace without its switch",
     "      HB_T_DEFS+=(-DINTP_TX_TRACE=1)\n", ""),
    ("l2_build.sh", "the event layout without the capturing Serial",
     "      HB_T_DEFS+=(-DHOST_SERIAL_CAPTURE=1)\n", ""),
]


def load_canfail():
    import importlib.util
    spec = importlib.util.spec_from_file_location("shared_fake_canfail_under_test", CANFAIL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_all(ctx, base, texts=None):
    """every test, with run.sh from a tree copy of its own under base"""
    tree = os.path.join(base, "tree")
    ctx = dict(ctx, run_sh=tree_copy(tree, texts), root=tree)
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
        srcs = {}
        for f in sorted({f for f, _n, _o, _w in MUTANTS}):
            with open(os.path.join(HB, f), encoding="utf-8", newline="") as fh:
                srcs[f] = fh.read()
        for n, (f, name, old, new) in enumerate(MUTANTS, 1):
            total += 1
            if srcs[f].count(old) != 1:
                print("FAIL  m%d %s: %s: NOT APPLIED (the edit matched %d times)"
                      % (n, f, name, srcs[f].count(old)))
                continue
            print("-- m%d %s: %s" % (n, f, name), flush=True)
            bad = run_all(ctx, os.path.join(base, "m%d" % n),
                          {f: srcs[f].replace(old, new)})
            caught += bool(bad)
            print("%s  m%d %s: %s: %s" % ("ok  " if bad else "FAIL", n, f, name,
                                          ("fails " + ", ".join(bad)) if bad else
                                          "STILL PASSES -- nothing checks it"), flush=True)
        print("\n%d of %d mutants caught" % (caught, total))
        return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
