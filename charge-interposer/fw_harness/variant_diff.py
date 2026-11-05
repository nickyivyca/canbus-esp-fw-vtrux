#!/usr/bin/env python3
"""Spec 9, build check (C2b): two variants' builds differ only by the named
test switches. The witness build differs from the bench build only by the
witness switch, and the bench build from the flight build only by its
serial-output settings (user, 2026-10-13).

Charge-interposer tester, 2026-11-04, port step 2a acceptance (h), in the
form the charge-interposer reviewer accepted that day:
  witness vs bridge = {INTP_ORDER_WITNESS};
  bridge vs truck   = {INTP_NO_SERIAL} and nothing else: the sdkconfig
                      keys all equal (one sdkconfig for every variant, R28;
                      USB mode and CDC-on-boot fixed lines, 2026-10-25).

    py -3.14 -B variant_diff.py --pair <build A> <build B> --switches S[,S...]
    py -3.14 -B variant_diff.py --can-fail --pair <build A> <build B> --switches ...
    py -3.14 -B variant_diff.py --self-test

A build folder is an ESP-IDF build's (firmware/build/<variant>/). From each
it reads project_description.json, config/sdkconfig.json and
compile_commands.json, and judges:
  1. sdkconfig: every key the same, on both sides.
  2. compile commands: the same source files on both sides, and for each,
     the same flags but for -D of a named switch (with or without a value),
     added or removed. Every named switch must differ somewhere: a pair that
     does not differ is refused, as is one that differs in a switch not
     named.
Not compared, masked first on each side (what differs between two builds of
one commit in two folders, by construction):
  - each build's own folder (build_dir) and its project folder
    (project_path), from its project_description.json;
  - its project name, interposer_<variant> (spec 8.2: fixed per variant);
  - the arguments of -o, -MF, -MT and -MQ (object and dependency files:
    CMake shortens a long object path with a hash of the folder);
  - build dates and times (YYYY-MM-DD, HH:MM:SS), as in OpenThread's
    OPENTHREAD_BUILD_DATETIME.
Images are not compared: a switch changes the code.

--can-fail copies the two folders' three files to this run's scratch folder
and judges the copy unchanged (it must PASS), then with each of: an extra
-D on one source file of B; one sdkconfig key changed in B; the named
switches taken out of B; a source file only in B. Each must FAIL.
--self-test does the same on stand-in folders, with the masks shown at
work (a hashed object path, a build time, the project name, the folders),
for the harness suite where no build exists.

Exit 0 PASS, 1 FAIL, 2 refused (an unreadable folder, a bad call).
"""

import argparse
import collections
import copy
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
if CODE not in sys.path:
    sys.path.insert(0, CODE)

PATH_ARGS = ("-o", "-MF", "-MT", "-MQ")
DATE = re.compile(r"\d{4}-\d\d-\d\d")
TIME = re.compile(r"\b\d\d:\d\d:\d\d\b")


class Refused(Exception):
    pass


def _fwd(s):
    return s.replace("\\", "/")


def load(folder):
    """{"name", "sdkconfig", "commands": {file: Counter of flags}} of a
    build folder, masked."""
    def read(rel):
        p = os.path.join(folder, rel)
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            raise Refused("%s: %s" % (p, e))
    desc = read("project_description.json")
    sdk = read(os.path.join("config", "sdkconfig.json"))
    cc = read("compile_commands.json")
    name = desc.get("project_name") or ""
    subs = [(_fwd(desc.get("build_dir", "")), "<build>"),
            (_fwd(desc.get("project_path", "")), "<project>")]
    subs = [(a, b) for a, b in subs if a]
    subs.sort(key=lambda x: -len(x[0]))              # the longer first

    def mask(s):
        s = _fwd(s)
        for a, b in subs:
            s = s.replace(a, b)
        if name:
            s = s.replace(name, "<project-name>")
        return TIME.sub("<time>", DATE.sub("<date>", s))

    commands = {}
    for e in cc:
        toks = mask(e.get("command") or " ".join(e.get("arguments") or [])).split()
        flags, skip = [], False
        for t in toks[1:]:                           # the compiler is toks[0]
            if skip:
                skip = False
                continue
            if t in PATH_ARGS:
                skip = True
                continue
            if any(t.startswith(p) and len(t) > len(p) for p in PATH_ARGS):
                continue                             # -o<file> in one token
            flags.append(t)
        commands[mask(e["file"])] = collections.Counter(flags)
    return {"name": name, "sdkconfig": sdk, "commands": commands}


def _switch_of(flag, switches):
    if not flag.startswith("-D"):
        return None
    s = flag[2:].split("=", 1)[0]
    return s if s in switches else None


def judge(a, b, switches):
    """[(ok, line)]; the pair passes if every ok is True."""
    out = []
    keys = sorted(set(a["sdkconfig"]) | set(b["sdkconfig"]))
    sk = [k for k in keys if a["sdkconfig"].get(k, "<absent>") != b["sdkconfig"].get(k, "<absent>")]
    out.append((not sk, "sdkconfig: %d keys, %s" % (
        len(keys), "all the same" if not sk else "%d differ: %s" % (
            len(sk), ", ".join("%s %r -> %r" % (k, a["sdkconfig"].get(k, "<absent>"),
                                                b["sdkconfig"].get(k, "<absent>"))
                               for k in sk[:5])))))
    fa, fb = set(a["commands"]), set(b["commands"])
    only = sorted(fa ^ fb)
    out.append((not only, "source files: %d and %d, %s" % (
        len(fa), len(fb), "the same" if not only else "%d on one side only: %s" % (
            len(only), ", ".join(only[:4])))))
    seen = collections.Counter()
    other = []
    for f in sorted(fa & fb):
        ca, cb = a["commands"][f], b["commands"][f]
        for flag in sorted(set(ca) | set(cb)):
            if ca[flag] == cb[flag]:
                continue
            s = _switch_of(flag, switches)
            if s:
                seen[s] += 1
            else:
                other.append("%s: %s %s" % (f[-70:], flag, "added" if cb[flag] > ca[flag]
                                            else "removed"))
    out.append((not other, "flags: %s" % ("no difference but the named switches" if not other
                                          else "%d other differences: %s" % (
                                              len(other), "; ".join(other[:4])))))
    for s in switches:
        out.append((seen[s] > 0, "switch %s: differs on %d source file(s)" % (s, seen[s])))
    return out


def report(title, lines):
    print("== " + title, flush=True)
    for ok, ln in lines:
        print("%s %s" % ("  ok  " if ok else "FAIL  ", ln), flush=True)
    passed = all(ok for ok, _ in lines)
    print("variant_diff: %s" % ("PASS" if passed else "FAIL"), flush=True)
    return passed


# ---- can-fail and self-test ---------------------------------------------------
FILES = ("project_description.json", os.path.join("config", "sdkconfig.json"),
         "compile_commands.json")


def _copy(src, dst):
    for rel in FILES:
        os.makedirs(os.path.dirname(os.path.join(dst, rel)) or dst, exist_ok=True)
        with open(os.path.join(src, rel), encoding="utf-8") as f:
            data = json.load(f)
        with open(os.path.join(dst, rel), "w", encoding="utf-8") as f:
            json.dump(data, f)


def _edit(folder, rel, fn):
    p = os.path.join(folder, rel)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    data = fn(data)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _cmd_key(e):
    return "command" if "command" in e else "arguments"


def _add_flag(cc, flag):
    cc = copy.deepcopy(cc)
    e = cc[0]
    k = _cmd_key(e)
    if k == "command":
        parts = e[k].split(" ", 1)
        e[k] = parts[0] + " " + flag + (" " + parts[1] if len(parts) > 1 else "")
    else:
        e[k] = [e[k][0], flag] + e[k][1:]
    return cc


def _drop_switches(cc, switches):
    cc = copy.deepcopy(cc)
    for e in cc:
        k = _cmd_key(e)
        toks = e[k].split() if k == "command" else list(e[k])
        toks = [t for t in toks if not _switch_of(_fwd(t), switches)]
        e[k] = " ".join(toks) if k == "command" else toks
    return cc


def _one_more_file(cc):
    cc = copy.deepcopy(cc)
    e = copy.deepcopy(cc[0])
    e["file"] = e["file"] + ".extra.c"
    cc.append(e)
    return cc


def _sdk_change(sdk):
    sdk = dict(sdk)
    k = sorted(sdk)[0]
    v = sdk[k]
    sdk[k] = (not v) if isinstance(v, bool) else ((v + 1) if isinstance(v, int) else str(v) + "x")
    return sdk


def can_fail(a_dir, b_dir, switches, base):
    """0 if the copy passes and every corruption fails."""
    import scratch_dir
    bad = 0
    with scratch_dir.run(base) as work:
        a = os.path.join(work, "a")
        _copy(a_dir, a)
        cases = [("the copy, unchanged", None, True),
                 ("an extra -D on one source file of B",
                  ("compile_commands.json", lambda cc: _add_flag(cc, "-DINTP_CANFAIL_EXTRA=1")),
                  False),
                 ("one sdkconfig key changed in B",
                  (os.path.join("config", "sdkconfig.json"), _sdk_change), False),
                 ("the named switches taken out of B",
                  ("compile_commands.json", lambda cc: _drop_switches(cc, switches)), False),
                 ("a source file only in B", ("compile_commands.json", _one_more_file), False)]
        for i, (label, edit, want) in enumerate(cases):
            b = os.path.join(work, "b%d" % i)
            _copy(b_dir, b)
            if edit:
                _edit(b, edit[0], edit[1])
            passed = all(ok for ok, _ in judge(load(a), load(b), switches))
            good = passed == want
            bad += not good
            print("%s %s: %s" % ("ok    " if good else "FAIL  ", label,
                                 ("passes" if passed else "fails")
                                 + ("" if good else (" -- wanted it to %s"
                                                     % ("pass" if want else "fail")))), flush=True)
    print("\nvariant_diff --can-fail: %s" % ("every corruption caught, the copy passes"
                                             if not bad else "%d WRONG" % bad))
    return 1 if bad else 0


def _standin(folder, variant, defines, hashed=False, time="20:13:24", date="2026-11-03",
             sdk=None):
    """A stand-in build folder of variant, at folder."""
    proj = _fwd(os.path.join(os.path.dirname(folder), "proj-" + variant))
    bd = _fwd(folder)
    gxx = "C:/t/xtensa-esp32s3-elf-g++.exe"
    obj = ("CMakeFiles/x.dir/%s/y.c.obj" % ("0317e2e3b8646bd2" if hashed else "long/path/y")).replace(
        "/", "\\")
    cc = [{"directory": bd, "file": proj + "/main/machine.cpp",
           "command": "%s %s -I%s/config -I%s/main -o %s -c %s/main/machine.cpp"
                      % (gxx, " ".join(defines), bd, proj, obj, proj)},
          {"directory": bd, "file": proj + "/managed/y.c",
           "command": "%s -DOPENTHREAD_BUILD_DATETIME=\\\"%s %s\\\" -DPROJECT_NAME=\\\"interposer_%s\\\" "
                      "-MF %s.d -o %s -c %s/managed/y.c" % (gxx, date, time, variant, obj, obj,
                                                             proj)}]
    os.makedirs(os.path.join(folder, "config"), exist_ok=True)
    with open(os.path.join(folder, "project_description.json"), "w") as f:
        json.dump({"project_name": "interposer_" + variant, "build_dir": bd,
                   "project_path": proj}, f)
    with open(os.path.join(folder, "config", "sdkconfig.json"), "w") as f:
        json.dump(sdk or {"FREERTOS_HZ": 1000, "BT_ENABLED": False, "LOG_DEFAULT_LEVEL": 0}, f)
    with open(os.path.join(folder, "compile_commands.json"), "w") as f:
        json.dump(cc, f)


def self_test():
    import scratch_dir
    bad = 0

    def expect(label, a, b, switches, want):
        nonlocal bad
        lines = judge(load(a), load(b), switches)
        passed = all(ok for ok, _ in lines)
        good = passed == want
        bad += not good
        print("%s %s: %s%s" % ("ok    " if good else "FAIL  ", label,
                               "passes" if passed else "fails",
                               "" if good else " -- " + "; ".join(ln for ok, ln in lines if not ok)),
              flush=True)

    with scratch_dir.run("variant-diff-self-test") as work:
        br = os.path.join(work, "bridge")
        _standin(br, "bridge", ["-DARDUINO=1"])
        wi = os.path.join(work, "witness")
        _standin(wi, "witness", ["-DARDUINO=1", "-DINTP_ORDER_WITNESS=1"], hashed=True,
                 time="20:29:29", date="2026-11-04")
        tr = os.path.join(work, "truck")
        _standin(tr, "truck", ["-DARDUINO=1", "-DINTP_NO_SERIAL=1"])
        expect("witness vs bridge by INTP_ORDER_WITNESS (another folder, project name, "
               "hashed object path, build time)", br, wi, ["INTP_ORDER_WITNESS"], True)
        expect("bridge vs truck by INTP_NO_SERIAL", br, tr, ["INTP_NO_SERIAL"], True)
        expect("bridge vs truck named by the wrong switch", br, tr, ["INTP_ORDER_WITNESS"], False)
        expect("a pair with no difference", br, br, ["INTP_NO_SERIAL"], False)
        s2 = os.path.join(work, "truck-sdk")
        _standin(s2, "truck", ["-DARDUINO=1", "-DINTP_NO_SERIAL=1"],
                 sdk={"FREERTOS_HZ": 1000, "BT_ENABLED": True, "LOG_DEFAULT_LEVEL": 0})
        expect("an sdkconfig key differing", br, s2, ["INTP_NO_SERIAL"], False)
        o2 = os.path.join(work, "truck-opt")
        _standin(o2, "truck", ["-DARDUINO=1", "-DINTP_NO_SERIAL=1", "-O3"])
        expect("another flag differing", br, o2, ["INTP_NO_SERIAL"], False)
        bad += can_fail(br, wi, ["INTP_ORDER_WITNESS"], "variant-diff-self-canfail") != 0
    print("\nvariant_diff --self-test: %s" % ("all checks OK" if not bad else "%d FAILED" % bad))
    return 1 if bad else 0


def main(argv):
    ap = argparse.ArgumentParser(prog="variant_diff.py")
    ap.add_argument("--pair", nargs=2, metavar=("A", "B"))
    ap.add_argument("--switches", default="")
    ap.add_argument("--can-fail", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.self_test:
            return self_test()
        switches = [s for s in a.switches.split(",") if s]
        if not a.pair or not switches:
            print("variant_diff: REFUSED: --pair A B and --switches are needed")
            return 2
        if a.can_fail:
            return can_fail(a.pair[0], a.pair[1], switches, "variant-diff-canfail")
        lines = judge(load(a.pair[0]), load(a.pair[1]), switches)
        return 0 if report("%s vs %s, by %s" % (a.pair[0], a.pair[1], ",".join(switches)),
                           lines) else 1
    except Refused as e:
        print("variant_diff: REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
