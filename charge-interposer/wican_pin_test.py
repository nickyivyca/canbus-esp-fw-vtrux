#!/usr/bin/env python3
"""Test of wican_pin.py and the committed wican_pin.json. No hardware, and
no image needed: the check runs on made-up files.

    python3.13 -B charge-interposer/wican_pin_test.py [--can-fail]     (madhouse)
    py -3.14 -B charge-interposer/wican_pin_test.py [--can-fail]       (Windows)

Each case names what it checks:
  1. committed   wican_pin.json is the pin the reviewer asked for: gen-inhibit
                 7f2d5032 _man, the madhouse rebuild's image hash 2aac016d...,
                 code hash bc49dce0..., four files at 0x0, 0x8000, 0xd000
                 and 0x10000, the app the last
  2. shape       load() refuses a pin with each defect: another project, a
                 short commit, a bad image hash, a file outside the folder, a
                 bad offset, size, sha256 or md5, a repeated offset, no app
                 or two
  3. check-ok    a folder holding made-up files with the pin's own figures
                 passes
  4. check-bad   a missing file, one byte more, a changed byte (same size)
                 are each named; a file only its md5 would catch is too
--can-fail runs the cases on the unedited wican_pin.py first (the control,
which must pass), then on edited copies, one edit each; every edit must
make a case fail.

Charge-interposer tester, 2026-11-11.
"""

import copy
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "wican_pin.py")


def load_mod(path):
    spec = importlib.util.spec_from_file_location("wican_pin", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.PIN_FILE = os.path.join(HERE, "wican_pin.json")
    return mod


def committed():
    with open(os.path.join(HERE, "wican_pin.json"), encoding="utf-8") as f:
        return json.load(f)


def case_committed(M, work):
    p = M.load()
    s, i = p["source"], p["image"]
    assert s["project"] == "gen-inhibit" and s["commit"].startswith("7f2d5032") \
        and s["variant"] == "_man", s
    assert i["image_hash"].startswith("2aac016d") and s["code_hash"].startswith("bc49dce0"), i
    assert [f["offset"] for f in i["files"]] == ["0x0", "0x8000", "0xd000", "0x10000"], i
    assert [f.get("app", False) for f in i["files"]] == [False, False, False, True], i
    assert i["chip"] == "ESP32-C3", i


def _refused(M, work, edit, n):
    p = committed()
    edit(p)
    path = os.path.join(work, "pin%d.json" % n)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(p, f)
    try:
        M.load(path)
    except M.PinError:
        return True
    return False


def case_shape(M, work):
    def f(k):
        return lambda p: p["image"]["files"][k]

    edits = [
        ("another project", lambda p: p["source"].update(project="charge-interposer")),
        ("a short commit", lambda p: p["source"].update(commit="7f2d5032")),
        ("a bad image hash", lambda p: p["image"].update(image_hash="2aac016d")),
        ("a bad code hash", lambda p: p["source"].update(code_hash="x" * 64)),
        ("a file outside the folder", lambda p: f(0)(p).update(name="../x.bin")),
        ("an absolute file", lambda p: f(0)(p).update(name="/x.bin")),
        ("a bad offset", lambda p: f(1)(p).update(offset="8000")),
        ("a bad size", lambda p: f(1)(p).update(size=0)),
        ("a bad sha256", lambda p: f(2)(p).update(sha256="0" * 63)),
        ("a bad md5", lambda p: f(2)(p).update(md5="0" * 31)),
        ("a repeated offset", lambda p: f(2)(p).update(offset="0x8000")),
        ("no app", lambda p: f(3)(p).pop("app")),
        ("two apps", lambda p: f(2)(p).update(app=True)),
        ("no files", lambda p: p["image"].update(files=[])),
    ]
    missed = [name for n, (name, e) in enumerate(edits) if not _refused(M, work, e, n)]
    assert not missed, "not refused: %s" % missed
    assert not _refused(M, work, lambda p: None, 99), "the committed pin itself refused"


def _fake(work, tag):
    """A pin whose files are made up in a folder of their own -> (pin, folder)."""
    folder = os.path.join(work, tag)
    pin = copy.deepcopy(committed())
    for k, f in enumerate(pin["image"]["files"]):
        data = (b"%d:%s:" % (k, f["name"].encode())) * (50 + 7 * k)
        path = os.path.join(folder, *f["name"].split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        f.update(size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                 md5=hashlib.md5(data).hexdigest())
    return pin, folder


def case_check_ok(M, work):
    pin, folder = _fake(work, "ok")
    assert M.check(folder, pin) == [], M.check(folder, pin)


def case_check_bad(M, work):
    pin, folder = _fake(work, "bad")
    files = pin["image"]["files"]
    os.remove(os.path.join(folder, *files[0]["name"].split("/")))
    p1 = os.path.join(folder, *files[1]["name"].split("/"))
    with open(p1, "ab") as fh:
        fh.write(b"x")
    p2 = os.path.join(folder, *files[2]["name"].split("/"))
    with open(p2, "r+b") as fh:
        b = fh.read(1)
        fh.seek(0)
        fh.write(bytes([b[0] ^ 1]))
    pin["image"]["files"][3]["md5"] = "0" * 32
    bad = M.check(folder, pin)
    want = [files[0]["name"] + ": missing", files[1]["name"] + ": ", files[2]["name"] + ": sha256",
            files[3]["name"] + ": md5"]
    for w in want:
        assert any(x.startswith(w) for x in bad), (w, bad)
    assert any("bytes, pinned" in x for x in bad if x.startswith(files[1]["name"])), bad


CASES = [case_committed, case_shape, case_check_ok, case_check_bad]

MUTANTS = [
    ("another project accepted", '    if src.get("project") != "gen-inhibit":',
     '    if False:'),
    ("a short commit accepted", 'HEX40 = re.compile(r"[0-9a-f]{40}\\Z")',
     'HEX40 = re.compile(r"[0-9a-f]{7,40}\\Z")'),
    ("a path outside the folder accepted",
     '        if not n or os.path.isabs(n) or ".." in n.split("/"):',
     '        if not n:'),
    ("a repeated offset accepted", '        if n in names or f.get("offset") in offsets:',
     '        if n in names:'),
    ("no app accepted", '    if sum(1 for f in files if f.get("app")) != 1:',
     '    if sum(1 for f in files if f.get("app")) > 1:'),
    ("a missing file passes", '            bad.append("%s: missing (%s)" % (f["name"], path))\n',
     ''),
    ("the size not checked", '        if len(data) != f["size"]:', '        if False:'),
    ("the sha256 not checked", '        if hashlib.sha256(data).hexdigest() != f["sha256"]:',
     '        if False:'),
    ("the md5 not checked", '        if hashlib.md5(data).hexdigest() != f["md5"]:',
     '        if False:'),
]


def run_cases(path, base):
    M = load_mod(path)
    bad = []
    for c in CASES:
        work = os.path.join(base, c.__name__)
        os.makedirs(work)
        try:
            c(M, work)
            print("ok    %s" % c.__name__, flush=True)
        except Exception as e:                          # noqa: BLE001
            print("FAIL  %s  %s: %s" % (c.__name__, type(e).__name__, str(e)[:300]), flush=True)
            bad.append(c.__name__)
    return bad


def main(argv):
    with scratch_dir.run("wican-pin-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\nwican_pin_test: %s" % ("all checks OK" if not bad
                                           else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited wican_pin.py", flush=True)
        if run_cases(MODULE, os.path.join(base, "control")):
            print("\nREFUSED: the control fails")
            return 1
        with open(MODULE, encoding="utf-8") as f:
            src = f.read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)), flush=True)
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            p = os.path.join(d, "wican_pin.py")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_cases(p, os.path.join(d, "t"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad
                                      else "STILL PASSES -- nothing checks it"), flush=True)
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
