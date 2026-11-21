#!/usr/bin/env python3
"""Test of harness_names.py: the undefined, rebound, star and syntax hits
on made-up sources, the forms that must not hit, and the harness tree
clean. No hardware.

    python3.13 -B charge-interposer/harness_names_test.py [--can-fail]

Each case names what it checks:
  1. tree          every tracked .py under charge-interposer/ but firmware/
                   (more than 100) gives no hit
  2. undefined     run 69d8's vp1-vp3 form: a function reads a name the
                   function that bound it no longer binds; the hit names the
                   function, the name and the line it is read on; the same in
                   a nested function, and a method reading its class's name
  3. defined       no hit for a builtin, a parameter, a module name bound
                   after the function (def, class, import, assignment, a
                   try/except import), a name a function binds by `global`,
                   an enclosing function's name, a comprehension's name, an
                   except's name, the module's dunders
  4. rebound       run 69d8's board.* form: run_one's def started() and
                   `started = tap.wait_started()` inside a try and an if, a
                   nested starter calling it: one hit with both lines; and a
                   for, a with, an import, a walrus and an except each
                   rebinding a def
  5. not-rebound   no hit for a nested function binding the outer def's name
                   in its own body, two sibling functions each with its own
                   def and variable of one name, a def only called, a
                   module-level def rebound
  6. star-syntax   `from x import *` and a file that does not parse each hit
                   and stop that file
--can-fail runs the cases on the unedited harness_names.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail.

Charge-interposer tester, 2026-11-21 (the reviewer's plan (i) after run
69d8).
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "harness_names.py")


def load(path):
    spec = importlib.util.spec_from_file_location("harness_names", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hits(M, src):
    return M.check_source(src, "x.py")


def case_tree(M, work):
    files = M.harness_files(HERE)
    assert len(files) > 100, len(files)
    got = M.check_files(files)
    assert got == [], got[:5]


UNDEF_69D8 = '''\
def judgeable(rows):
    tsw = [x[0] for x in rows if x[2]]
    return bool(tsw)


def run_bench(rows):
    ok = judgeable(rows)
    if ok:
        flag = len(tsw)
        return flag
'''

UNDEF_NESTED = '''\
def outer():
    def inner():
        return missing_name + 1
    return inner


class C:
    k = 1

    def m(self):
        return k
'''


def case_undefined(M, work):
    got = hits(M, UNDEF_69D8)
    assert got == ["x.py:9: undefined: run_bench() reads 'tsw', which no "
                   "scope binds"], got
    got = hits(M, UNDEF_NESTED)
    assert got == ["x.py:3: undefined: inner() reads 'missing_name', which "
                   "no scope binds",
                   "x.py:11: undefined: m() reads 'k', which no scope binds"], got


DEFINED = '''\
import os
try:
    import fancy
except ImportError:
    fancy = None


def f(arg, *rest, kw=None, **more):
    print(len(rest), arg, kw, more, os.sep, fancy, LATER, Later, later_def)
    print(__file__, __name__, __doc__, __spec__)
    try:
        pass
    except ValueError as e:
        print(e)
    vals = [v * 2 for v in range(3)]
    gen = {k: v for k, v in zip("ab", vals)}

    def g():
        return arg + len(vals) + len(gen)
    return g


def setter():
    global SET_LATER
    SET_LATER = 1


def getter():
    return SET_LATER


LATER = 1


class Later:
    pass


def later_def():
    return None
'''


def case_defined(M, work):
    got = hits(M, DEFINED)
    assert got == [], got


REBOUND_69D8 = '''\
def run_one(tap):
    named = []

    def started(name, proc):
        named.append((name, proc))
        return proc

    def start_charger():
        return started("charger_sim", object())

    try:
        if tap:
            started = tap.wait_started()
        start_charger()
    finally:
        pass
'''

REBOUND_FORMS = '''\
import contextlib


def a():
    def f():
        return 1
    for f in range(2):
        pass


def b():
    def f():
        return 1
    with contextlib.nullcontext() as f:
        pass


def c():
    def os():
        return 1
    import os


def d():
    def f():
        return 1
    if (f := 2):
        pass


def e():
    def f():
        return 1
    try:
        pass
    except ValueError as f:
        pass
'''


def case_rebound(M, work):
    got = hits(M, REBOUND_69D8)
    assert got == ["x.py:4: rebound: run_one() binds 'started' again at line "
                   "13, hiding its def from the functions that call it"], got
    names = [h.split(": rebound: ")[1].split("()")[0]
             for h in hits(M, REBOUND_FORMS)]
    assert names == ["a", "b", "c", "d", "e"], hits(M, REBOUND_FORMS)


NOT_REBOUND = '''\
def outer():
    def helper():
        return 1

    def inner():
        helper = 2
        return helper
    return helper() + inner()


def one():
    def item():
        return 1
    return item()


def two():
    item = 3
    return item


def only_called():
    def h():
        return 1
    return h() + h()


def top():
    return 1


top = 2
'''


def case_not_rebound(M, work):
    got = hits(M, NOT_REBOUND)
    assert got == [], got


def case_star_syntax(M, work):
    got = hits(M, "from os import *\n\ndef f():\n    return nope\n")
    assert got == ["x.py:1: star: `from ... import *`, its names cannot be "
                   "checked"], got
    got = hits(M, "def f(:\n    pass\n")
    assert len(got) == 1 and got[0].startswith("x.py:1: syntax: "), got


CASES = [case_tree, case_undefined, case_defined, case_rebound,
         case_not_rebound, case_star_syntax]

MUTANTS = [
    ("undefined names never reported",
     "                if (s.is_referenced() and s.is_global()",
     "                if (False and s.is_global()"),
    ("builtins not taken as bound",
     "                        and n not in known and not hasattr(builtins, n)",
     "                        and n not in known"),
    ("a module's imports not taken as bound",
     "            if s.is_assigned() or s.is_imported()\n",
     "            if s.is_assigned()\n"),
    ("a module's assignments, defs and classes not taken as bound",
     "            if s.is_assigned() or s.is_imported()\n",
     "            if s.is_imported()\n"),
    ("a name bound by `global` not taken as bound",
     "            or s.is_declared_global()}",
     "            }"),
    ("the module's dunders not taken as bound",
     "                        and n not in MODULE_DUNDERS):",
     "                        ):"),
    ("an undefined hit at the def's line, not the read's",
     "            return min(lines) if lines else def_line",
     "            return def_line"),
    ("rebinding never reported",
     "            if name in defs:\n                hits.append",
     "            if False:\n                hits.append"),
    ("rebinding looked for only in the function's own statements",
     "        for c in ast.iter_child_nodes(n):\n            visit(c)\n\n    for st in node.body:\n        visit(st)\n    return out\n\n\ndef _defs",
     "\n    for st in node.body:\n        visit(st)\n    return out\n\n\ndef _defs"),
    ("a nested function's own bindings counted as its outer's",
     "                    if d is not None:\n                        visit(d)\n            return\n",
     "                    if d is not None:\n                        visit(d)\n            for c in n.body:\n                visit(c)\n            return\n"),
    ("imports not counted as rebinding",
     "                out.append(((a.asname or a.name).split(\".\")[0], n.lineno))",
     "                pass"),
    ("an except's name not counted as rebinding",
     "        elif isinstance(n, ast.ExceptHandler) and n.name:\n            out.append((n.name, n.lineno))",
     "        elif False:\n            pass"),
    ("star imports not refused",
     "    if stars:\n        return",
     "    if False:\n        return"),
    ("the tree's file list empty (a scan that reads nothing is clean)",
     "            if rel.endswith(\".py\") and not rel.startswith(\"firmware/\")]",
     "            if rel.endswith(\".py\") and len(rel) > 1000]"),
]


def run_cases(path, base):
    M = load(path)
    bad = []
    for c in CASES:
        work = os.path.join(base, c.__name__)
        os.makedirs(work)
        try:
            c(M, work)
            print("ok    %s" % c.__name__, flush=True)
        except Exception as e:                          # noqa: BLE001
            print("FAIL  %s  %s: %s" % (c.__name__, type(e).__name__,
                                        str(e)[:300]), flush=True)
            bad.append(c.__name__)
    return bad


def main(argv):
    with scratch_dir.run("harness-names-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\nharness_names_test: %s" % ("all checks OK" if not bad
                                               else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited harness_names.py", flush=True)
        if run_cases(MODULE, os.path.join(base, "control")):
            print("\nREFUSED: the control fails")
            return 1
        src = open(MODULE, encoding="utf-8").read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)), flush=True)
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            p = os.path.join(d, "harness_names.py")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_cases(p, os.path.join(d, "t"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad
                                      else "STILL PASSES -- nothing checks it"),
                  flush=True)
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
