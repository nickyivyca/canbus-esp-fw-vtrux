#!/usr/bin/env python3
"""Test host_identity.sh, and that every script here that builds C runs it.

host_identity.sh is run in a real bash with fakes first on PATH: a gcc that answers
--version with two lines (the line must hold only the first), a gcc that gives
nothing, and a uname that gives nothing (each must read NOT RECORDED and exit 1).
Then every *.sh in this folder whose commands run gcc or make must call
host_identity.sh, except baseline_host_run.sh, whose remote log's first lines
record $(hostname) and gcc --version's first line; a planted script that builds
without it must be flagged, and one that calls it must not.

    py -3.14 -B tools/gen_inhibit/bench/test_host_identity.py
    py -3.14 -B tools/gen_inhibit/bench/test_host_identity.py --mutants

Gen-inhibit tester, 2026-10-24 (every host C run records its host and compiler).
"""
import os
import re
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GIT_BASH = r"C:/Program Files/Git/usr/bin/bash.exe"
BASH = GIT_BASH if os.name == "nt" and os.path.exists(GIT_BASH) else shutil.which("bash")
FAKE_GCC = "gcc (Fake 9.9.9-1) 9.9.9"
BUILD = re.compile(r"(^|[\s;&|(\"'])(gcc|make)\s")
OWN_RECORD = {"baseline_host_run.sh": ['$(hostname)', 'gcc --version | head -1']}
FAILS = []


def check(name, ok, detail=""):
    print("  %-4s %-70s %s" % ("ok" if ok else "FAIL", name, "" if ok else repr(detail)[:160]))
    if not ok:
        FAILS.append(name)


def fakes(d, gcc_out, uname_out=None):
    os.makedirs(d)
    with open(os.path.join(d, "gcc"), "w", newline="\n") as fh:
        fh.write("#!/bin/sh\n" + ("".join('echo "%s"\n' % l for l in gcc_out) + "exit 0\n" if gcc_out else "exit 127\n"))
    if uname_out is not None:
        with open(os.path.join(d, "uname"), "w", newline="\n") as fh:
            fh.write("#!/bin/sh\n" + ('echo "%s"\n' % uname_out if uname_out else "exit 1\n"))
    for f in os.listdir(d):
        os.chmod(os.path.join(d, f), 0o755)
    return d


def run_id(folder, bindir):
    e = dict(os.environ, PATH=bindir.replace("\\", "/") + os.pathsep + os.environ.get("PATH", ""),
             MSYS_NO_PATHCONV="1")
    r = subprocess.run([BASH, os.path.join(folder, "host_identity.sh").replace("\\", "/")], capture_output=True,
                       text=True, env=e, timeout=60)
    return r.returncode, r.stdout


def builds_without_record(name, text):
    """True if the script runs gcc or make and records no host and compiler."""
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    if not BUILD.search(code):
        return False
    if name in OWN_RECORD:
        return not all(s in code for s in OWN_RECORD[name])
    return "host_identity.sh" not in code


def checks(folder, tmp):
    del FAILS[:]
    node = subprocess.run([BASH, "-c", "uname -n"], capture_output=True, text=True).stdout.strip()
    rc, out = run_id(folder, fakes(os.path.join(tmp, "gcc2"), [FAKE_GCC, "Copyright line two"]))
    want = "# host %s; %s\n" % (node, FAKE_GCC)
    check("a gcc: one line, this host and gcc's first line", out == want, (out, want))
    check("a gcc: exit 0", rc == 0, rc)
    rc, out = run_id(folder, fakes(os.path.join(tmp, "nogcc"), []))
    check("no gcc: the compiler reads NOT RECORDED", out == "# host %s; compiler NOT RECORDED\n" % node, out)
    check("no gcc: exit 1", rc == 1, rc)
    rc, out = run_id(folder, fakes(os.path.join(tmp, "nouname"), [FAKE_GCC], uname_out=""))
    check("no host: the host reads NOT RECORDED", out == "# host NOT RECORDED; %s\n" % FAKE_GCC, out)
    check("no host: exit 1", rc == 1, rc)

    seen = 0
    for f in sorted(os.listdir(folder)):
        if f.endswith(".sh") and f != "host_identity.sh":
            text = open(os.path.join(folder, f), encoding="utf-8").read()
            code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
            if BUILD.search(code):
                seen += 1
                check("%s builds C and records host and compiler" % f, not builds_without_record(f, text))
    check("the scan found the scripts that build C (at least 5)", seen >= 5, seen)
    check("control: a script that builds without the record is flagged",
          builds_without_record("planted.sh", "set -e\nmake -s shim_test && ./shim_test\n"))
    check("control: a gcc line without the record is flagged",
          builds_without_record("planted.sh", 'ssh x "cd t && gcc -o a a.c"\n'))
    check("control: the same script calling host_identity.sh is not",
          not builds_without_record("planted.sh", 'ssh x "bash -s" < host_identity.sh\nmake -s shim_test\n'))
    check("control: a script that builds nothing is not", not builds_without_record("planted.sh", "echo make\n"))
    return list(FAILS)


MUTANTS = [
    ("host_identity.sh", "no exit status", '[ -n "$h" ] && [ -n "$c" ]\n', "true\n"),
    ("host_identity.sh", "every line of gcc --version", "gcc --version 2>/dev/null | head -1",
     "gcc --version 2>/dev/null"),
    ("host_identity.sh", "the OS name, not the host", "h=$(uname -n 2>/dev/null)", "h=$(uname -s 2>/dev/null)"),
    ("gi_host_run.sh", "gi_host_run records nothing",
     'ssh "$REMOTE" "bash -s" < "$(dirname "${BASH_SOURCE[0]}")/host_identity.sh"\n', ""),
    ("shim_wire_probe.sh", "shim_wire_probe records nothing",
     'ID="$(ssh "$REMOTE" "bash -s" < "$HERE/host_identity.sh")"', 'ID=""'),
    ("baseline_host_run.sh", "baseline_host_run drops its gcc line",
     'echo "# \\$(gcc --version | head -1); $PY \\$($PY --version 2>&1)"\n', ""),
]


def main():
    base = gi_paths.scratch("test_host_identity")
    try:
        if "--mutants" not in sys.argv[1:]:
            got = checks(HERE, base)
            print("\n%s" % ("all ok" if not got else "%d FAIL: %s" % (len(got), "; ".join(got))))
            return 1 if got else 0
        killed = errors = 0
        for n, (f, name, old, new) in enumerate(MUTANTS, 1):
            src = open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(os.path.join(d, "tmp"))
            for s in os.listdir(HERE):
                if s.endswith(".sh"):
                    shutil.copy(os.path.join(HERE, s), d)
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(src.replace(old, new))
            print("--- m%d %s" % (n, name))
            got = checks(d, os.path.join(d, "tmp"))
            killed += bool(got)
            print("%s m%d %s  (%d failing check(s)%s)" % ("KILLED  " if got else "SURVIVED", n, name, len(got),
                                                        ("; " + got[0]) if got else ""))
        print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
        return 0 if killed == len(MUTANTS) and not errors else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
