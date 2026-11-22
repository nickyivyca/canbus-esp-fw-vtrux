#!/usr/bin/env python3
"""Two name faults that compile but crash a harness path the first time it
runs, found by reading the source, so a path no test or hand take reached
is still covered:

  undefined  a name a function reads that no scope gives it: not its own,
             not an enclosing function's, not the module's (assigned,
             imported, def or class anywhere in it), not a builtin. Run
             69d8's vp1-vp3 and vport-bitrate stopped on one ("NameError:
             name 'tsw' is not defined", bench_vport 8b609320).
  rebound    a def (or class) whose name the same function also binds by
             assignment, a loop, a with, an import or a walrus. A nested
             function that calls the def gets whatever the name holds when
             it runs. Run 69d8's 29 board.* scenarios stopped on one
             (run_scenario 2b7059a9: `started = tap.wait_started()` in
             run_one hid run_one's started() helper: "TypeError: 'bool'
             object is not callable").
  star       `from x import *`: the names it brings cannot be known, so the
             file cannot be checked; refused rather than skipped.
  syntax     a file that does not parse.

Python's own symtable decides which scope a name belongs to, so a name is
judged as the compiler binds it.

    python3.13 -B charge-interposer/harness_names.py [FILE ...]
        no FILE: every tracked .py under charge-interposer/ but firmware/

Exit 0 clean, 1 with hits. Tested by harness_names_test.py.

Charge-interposer tester, 2026-11-21 (the reviewer's plan (i) after run
69d8).
"""

import ast
import builtins
import os
import subprocess
import symtable
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# set by the import system on every module, so never assigned in its source
MODULE_DUNDERS = ("__file__", "__name__", "__doc__", "__spec__", "__loader__",
                  "__package__", "__builtins__", "__path__", "__cached__")


def module_names(top):
    """The names a module binds at its top level, or in a function by a
    `global` statement (a def or class is an assignment to symtable)."""
    return {s.get_name() for s in top.get_symbols()
            if s.is_assigned() or s.is_imported()
            or s.is_declared_global()}


def _first_read(tree, def_line, scope, name):
    """The line where function `scope` (def at def_line) first reads
    name, else def_line."""
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda))
                and node.lineno == def_line
                and getattr(node, "name", "lambda") == scope):
            lines = [n.lineno for n in ast.walk(node)
                     if isinstance(n, ast.Name) and n.id == name
                     and isinstance(n.ctx, ast.Load)]
            return min(lines) if lines else def_line
    return def_line


def undefined(src, path):
    """[(line, scope, name)] for each name a function reads that no scope
    binds, at the line of its first read."""
    tree = ast.parse(src)
    top = symtable.symtable(src, path, "exec")
    known = module_names(top)
    hits = []

    def walk(t):
        if t.get_type() != "module":
            for s in t.get_symbols():
                n = s.get_name()
                if (s.is_referenced() and s.is_global()
                        and not s.is_declared_global()
                        and n not in known and not hasattr(builtins, n)
                        and n not in MODULE_DUNDERS):
                    hits.append((_first_read(tree, t.get_lineno(),
                                             t.get_name(), n),
                                 t.get_name(), n))
        for c in t.get_children():
            walk(c)

    walk(top)
    return hits


def _binds(node):
    """[(name, line)] node binds in its own scope, not inside a nested
    def, class or lambda (their bodies are their own scopes)."""
    out = []

    def visit(n):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            # the decorators and defaults run in this scope; the body not
            for d in n.decorator_list:
                visit(d)
            if not isinstance(n, ast.ClassDef):
                for d in n.args.defaults + n.args.kw_defaults:
                    if d is not None:
                        visit(d)
            return
        if isinstance(n, ast.Lambda):
            return
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.append((n.id, n.lineno))
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.append(((a.asname or a.name).split(".")[0], n.lineno))
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.append((n.name, n.lineno))
        for c in ast.iter_child_nodes(n):
            visit(c)

    for st in node.body:
        visit(st)
    return out


def _defs(node):
    """{name: line} of the defs and classes in node's own scope."""
    out = {}

    def visit(n):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            out.setdefault(n.name, n.lineno)
            return
        if isinstance(n, ast.Lambda):
            return
        for c in ast.iter_child_nodes(n):
            visit(c)

    for st in node.body:
        visit(st)
    return out


def rebound(src):
    """[(def line, scope, name, binding line)] for each def or class whose
    name its own function also binds another way."""
    hits = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defs = _defs(node)
        for name, line in _binds(node):
            if name in defs:
                hits.append((defs[name], node.name, name, line))
    return hits


def star(src):
    """[line] of each `from x import *`."""
    return [n.lineno for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.ImportFrom)
            and any(a.name == "*" for a in n.names)]


def check_source(src, path):
    """-> [hit line text] for one file's source."""
    try:
        compile(src, path, "exec", dont_inherit=True)
    except SyntaxError as e:
        return ["%s:%s: syntax: %s" % (path, e.lineno, e.msg)]
    stars = star(src)
    if stars:
        return ["%s:%d: star: `from ... import *`, its names cannot be "
                "checked" % (path, n) for n in stars]
    out = ["%s:%d: undefined: %s() reads %r, which no scope binds"
           % (path, line, scope, name)
           for line, scope, name in undefined(src, path)]
    out += ["%s:%d: rebound: %s() binds %r again at line %d, hiding its "
            "def from the functions that call it" % (path, line, scope,
                                                      name, at)
            for line, scope, name, at in rebound(src)]
    return out


def harness_files(root=HERE):
    """Every tracked .py under root but firmware/."""
    r = subprocess.run(["git", "-C", root, "ls-files", "--", "."],
                       capture_output=True, text=True, check=True)
    return [os.path.join(root, rel) for rel in r.stdout.splitlines()
            if rel.endswith(".py") and not rel.startswith("firmware/")]


def check_files(paths):
    hits = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            hits += check_source(f.read(), p)
    return hits


def main(argv):
    paths = argv or harness_files()
    hits = check_files(paths)
    for h in hits:
        print(h)
    print("harness_names: %d file(s), %s" % (
        len(paths), "%d hit(s)" % len(hits) if hits else "clean"))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
