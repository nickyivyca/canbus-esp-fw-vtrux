#!/usr/bin/env python3
"""Tests of fw_harness/hw_mechanics.py (spec 10, spec 12), on the IDF
project's layout of port step 2a.

    py -3.14 -B charge-interposer/hw_mechanics_test.py [--can-fail]

Tester, 2026-11-03. hw_mechanics says whether a change touched one of spec
10's hardware mechanics, against a manifest recorded at the last truck
flash. Port step 2a moved the firmware into an ESP-IDF project and the pins
into the board component, so its map was rewritten; these check the map
against the tree and each of its three kinds of entry on a copy:

  - on this tree, every mapped file, function and setting is found, and
    board_ports.h, idf_component.yml and dependencies.lock are among the
    whole files (the pins and the board's version since 2a), and since
    port step 2b vehicle_port, port_view, charger_port and bridge_port.h,
    with 2a's port_twai and can_port.h no longer named;
  - on a copy of the mapped files the state is the same as on the tree
    (the control), and then: each whole file removed is a problem naming
    it; one byte changed in it changes its key and no other; each setting's
    value changed changes its key and no other, and its pattern no longer
    matching is a problem; setup() renamed is a problem, a line added to it
    changes its key, an INTP_PRINT line added does not;
  - main(): --record writes a manifest and refuses to overwrite one; a
    compare against it reads unchanged (exit 0) until board_ports.h
    changes, then names it (exit 1); with no manifest, exit 1.

--can-fail runs the tests on mutants of hw_mechanics.py's text, each of
which must make a test fail, and on the unmutated text, which must pass.
Needs REVERSE_IT_DIR (hw_mechanics reads paths.fixtures() when loaded);
the manifests these write go in a scratch_dir run folder, never the real
flash-manifests folder.
"""

import contextlib
import io
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                    # noqa: E402

SRC = os.path.join(HERE, "fw_harness", "hw_mechanics.py")
FW = os.path.join(HERE, "firmware")
# what the map must watch for the pins and the board (2a), and for the
# ports since port step 2b: the vehicle port's settings, the port view, the
# charger port's registration and the renamed port contract (the rest of
# the map is checked entry by entry below, whatever it holds)
MUST_WATCH = ("main/board_ports.h", "main/idf_component.yml",
              "dependencies.lock", "main/vehicle_port.cpp", "main/vehicle_port.h",
              "main/port_view.cpp", "main/port_view.h", "main/charger_port.cpp",
              "main/charger_port.h", "main/port_mcp2515.cpp", "main/bridge_port.h",
              "sdkconfig")
# the files port step 2b retired, which the map must no longer name (a
# missing mapped file is a problem, so the tree test fails on them anyway;
# this says why)
RETIRED = ("main/port_twai.cpp", "main/port_twai.h", "main/can_port.h")


def load(text=None):
    """hw_mechanics as a module, from its file or from text (a mutant),
    run with its own __file__ so it finds the tree as the real one does."""
    if text is None:
        with open(SRC, encoding="utf-8") as f:
            text = f.read()
    m = types.ModuleType("hw_mechanics_under_test")
    m.__file__ = SRC
    exec(compile(text, SRC, "exec"), m.__dict__)
    return m


def mapped_files(H):
    rels = set(H.WHOLE_FILES)
    rels.update(r for r, _f, _w in H.FUNCTIONS)
    rels.update(r for r, _l, _p, _w in H.SETTINGS)
    return sorted(rels)


def copy_mapped(H, dst):
    for rel in mapped_files(H):
        d = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(os.path.join(FW, rel), d)
    return dst


def edit(path, old, new):
    with open(path, encoding="utf-8", newline="") as f:
        t = f.read()
    assert t.count(old) >= 1, (path, old)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(t.replace(old, new, 1))


def diff_keys(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


# ---- the tests --------------------------------------------------------------
def test_tree(H, work):
    """The map on this tree: nothing missing, the 2a entries present."""
    state, problems = H.collect()
    assert problems == [], problems
    for rel in MUST_WATCH:
        assert rel in H.WHOLE_FILES, rel
        assert "file:" + rel in state, rel
    for rel in RETIRED:
        assert rel not in H.WHOLE_FILES, rel
    assert os.path.normpath(H.FW) == os.path.normpath(FW), (H.FW, FW)
    for rel, fn, _w in H.FUNCTIONS:
        assert "fn:%s:%s" % (rel, fn) in state, (rel, fn)
    for rel, label, _p, _w in H.SETTINGS:
        assert "set:%s:%s" % (rel, label) in state, (rel, label)


def _copy(H, work, name):
    d = copy_mapped(H, os.path.join(work, name))
    H.FW = d
    return d


def test_whole_files(H, work):
    """A whole file: removed is a problem naming it; one byte changed
    changes its key alone."""
    real, _ = H.collect()
    for i, rel in enumerate(sorted(H.WHOLE_FILES)):
        d = _copy(H, work, "w%d" % i)
        base, problems = H.collect()
        assert problems == [] and base == real, (rel, problems)
        p = os.path.join(d, rel)
        with open(p, "ab") as f:
            f.write(b"\n")
        after, problems = H.collect()
        assert problems == [], problems
        assert diff_keys(base, after) == ["file:" + rel], (rel, diff_keys(base, after))
        os.remove(p)
        _s, problems = H.collect()
        assert any(rel in pr for pr in problems), (rel, problems)


def test_settings(H, work):
    """A setting: its value changed changes its key alone; its pattern no
    longer matching is a problem."""
    import re
    for i, (rel, label, pattern, _w) in enumerate(H.SETTINGS):
        d = _copy(H, work, "s%d" % i)
        base, _ = H.collect()
        p = os.path.join(d, rel)
        with open(p, encoding="utf-8", newline="") as f:
            text = f.read()
        m = re.search(pattern, text)
        assert m, (rel, label)
        old = m.group(0)
        v = m.group(1)
        new = old[:m.start(1) - m.start(0)] + str(int(v) + 1) + old[m.end(1) - m.start(0):]
        edit(p, old, new)
        after, problems = H.collect()
        assert problems == [], problems
        assert diff_keys(base, after) == ["set:%s:%s" % (rel, label)], (label, diff_keys(base, after))
        # every occurrence reworded so the pattern finds none
        with open(p, encoding="utf-8", newline="") as f:
            text = f.read()
        text = re.sub(pattern, lambda mm: mm.group(0).replace(mm.group(1), "X" + mm.group(1)), text)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        _s, problems = H.collect()
        assert any(label in pr for pr in problems), (label, problems)


def test_functions(H, work):
    """setup(): renamed is a problem; a line added changes its key; an
    INTP_PRINT line added does not."""
    for i, (rel, fn, _w) in enumerate(H.FUNCTIONS):
        sig = "void %s() {" % fn
        d = _copy(H, work, "f%d" % i)
        base, _ = H.collect()
        p = os.path.join(d, rel)
        edit(p, sig, sig + '\n  INTP_PRINTLN("a print only");')
        after, problems = H.collect()
        assert problems == [] and diff_keys(base, after) == [], diff_keys(base, after)
        edit(p, sig, sig + "\n  delay(1);")
        after, problems = H.collect()
        assert problems == [], problems
        assert diff_keys(base, after) == ["fn:%s:%s" % (rel, fn)], diff_keys(base, after)
        edit(p, sig, "void %s_renamed() {" % fn)
        _s, problems = H.collect()
        assert any(fn + "()" in pr for pr in problems), problems


def _main(H, argv):
    out = io.StringIO()
    old = sys.argv
    sys.argv = ["hw_mechanics.py"] + argv
    try:
        with contextlib.redirect_stdout(out):
            rc = H.main()
    finally:
        sys.argv = old
    return rc, out.getvalue()


def test_record_compare(H, work):
    """--record, then compare: unchanged reads 0; board_ports.h changed
    reads 1 and names it; a second --record of the label is refused; no
    manifest reads 1."""
    d = _copy(H, work, "m")
    H.MANIFESTS = os.path.join(work, "manifests")
    rc, out = _main(H, [])
    assert rc == 1 and "No manifest yet" in out, (rc, out)
    rc, out = _main(H, ["--record", "test-flash"])
    assert rc == 0 and os.path.exists(os.path.join(H.MANIFESTS, "test-flash.json")), (rc, out)
    rc, out = _main(H, ["--record", "test-flash"])
    assert rc == 2 and "already exists" in out, (rc, out)
    rc, out = _main(H, [])
    assert rc == 0 and "No mapped hardware mechanic has changed" in out, (rc, out)
    with open(os.path.join(d, "main", "board_ports.h"), "ab") as f:
        f.write(b"// one more line\n")
    rc, out = _main(H, [])
    assert rc == 1 and "file:main/board_ports.h" in out, (rc, out)


TESTS = [test_tree, test_whole_files, test_settings, test_functions,
         test_record_compare]


def run_all(text=None, quiet=False):
    """-> the names of the tests that failed"""
    failed = []
    with scratch_dir.run("hw-mechanics-test") as base:
        for i, t in enumerate(TESTS):
            work = os.path.join(base, "t%d" % i)
            os.makedirs(work)
            H = load(text)
            try:
                t(H, work)
                if not quiet:
                    print("ok    " + t.__name__)
            except Exception as e:                   # noqa: BLE001
                failed.append(t.__name__)
                if not quiet:
                    print("FAIL  %s  %s: %s" % (t.__name__, type(e).__name__, str(e)[:300]))
    return failed


# ---- can-fail ---------------------------------------------------------------
MUTANTS = [
    ("board_ports.h not watched",
     '    "main/board_ports.h": "pin assignment: which board port each side "\n'
     '                          "takes, and the checks on the board\'s answer",\n', ""),
    ("the board's version not watched",
     '    "dependencies.lock": "same, as resolved",\n', ""),
    ("a missing file skipped, not a problem",
     '            problems.append("mapped file is missing: %s (%s)" % (rel, why))\n',
     ""),
    ("a setting not found skipped",
     "        if not found:\n", "        if False:\n"),
    ("the print lines counted", '        if "INTP_PRINT" in line:\n',
     '        if False:\n'),
    ("the map read from the old layout",
     'FW = os.path.normpath(os.path.join(_HERE, "..", "firmware"))\n',
     'FW = os.path.normpath(os.path.join(_HERE, ".."))\n'),
    ("a change never reported", "    if not changed:\n", "    if True:\n"),
    ("a manifest overwritten", "        if os.path.exists(path):\n", "        if False:\n"),
    # port step 2b's map
    ("the vehicle port's settings not watched",
     '    "main/vehicle_port.h": "same",\n', ""),
    ("the port view not watched", '    "main/port_view.cpp": "the port contract the '
     'bridge reads both ports "\n', '    "main/port_view.cpp.x": "the port contract '
     'the bridge reads both ports "\n'),
    ("the charger port's registration not watched",
     '    "main/charger_port.h": "same",\n', ""),
    ("the retired port_twai still mapped",
     '    "main/bridge_port.h": "the port contract both drivers implement",\n',
     '    "main/bridge_port.h": "the port contract both drivers implement",\n'
     '    "main/can_port.h": "the retired name",\n'),
]


def can_fail():
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    bad = 0
    failed = run_all(text, quiet=True)
    print(("ok    " if not failed else "FAIL  ") + "control: the unmutated text passes"
          + ("" if not failed else " (%s)" % failed))
    bad += bool(failed)
    caught = 0
    for name, old, new in MUTANTS:
        if text.count(old) != 1:
            print("FAIL  %s: its text occurs %d times in hw_mechanics.py, not once"
                  % (name, text.count(old)))
            bad += 1
            continue
        failed = run_all(text.replace(old, new), quiet=True)
        if failed:
            caught += 1
            print("ok    %s: fails %s" % (name, ", ".join(failed)))
        else:
            bad += 1
            print("FAIL  %s: no test failed" % name)
    print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
    return 1 if bad else 0


def main():
    if "--can-fail" in sys.argv[1:]:
        return can_fail()
    failed = run_all()
    print("\n%d of %d passed" % (len(TESTS) - len(failed), len(TESTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
