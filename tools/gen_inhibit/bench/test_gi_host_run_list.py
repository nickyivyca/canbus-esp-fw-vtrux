#!/usr/bin/env python3
"""Test gi_host_run.sh's sync list: what it copies to ~/gi_host must be enough to run.

~/gi_host has no git, so a file outside the list is simply not there, and a tool that
imports it fails on the remote, or runs on a stale copy left by hand. project_dir.py was
outside it until 2026-10-26, so from_capture.py and replay_stimulus_delta.py could not
run there and its replay files went stale.

  1. The list (the `tar -C "$FW" -czf -` block) is found and names more than 20 paths.
  2. Every path it names exists in this tree.
  3. No shipped .py imports a module of its own folder that is not shipped (imports
     anywhere in the file, inside functions too).
  4. Controls: the real list with project_dir.py taken out must be flagged for
     from_capture.py and replay_stimulus_delta.py; a planted folder where a.py imports
     b inside a function must be flagged with b.py unlisted and pass with it listed.

    py -3.14 -B tools/gen_inhibit/bench/test_gi_host_run_list.py
    py -3.14 -B tools/gen_inhibit/bench/test_gi_host_run_list.py --mutants

--mutants runs the test on copies of gi_host_run.sh with one edit (GI_HOST_RUN_SH names
the copy). Gen-inhibit tester, 2026-10-26 (reviewer item: the project_dir.py sync gap).
"""
import ast
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = gi_paths.repo_root()
SCRIPT = os.environ.get("GI_HOST_RUN_SH") or os.path.join(HERE, "gi_host_run.sh")
START = 'tar -C "$FW" -czf - \\'
END = '-C "$EXPORT_PARENT"'
FAILS = []


def check(name, ok, detail=""):
    print("  %-4s %-70s %s" % ("ok" if ok else "FAIL", name, "" if ok else repr(detail)[:200]))
    if not ok:
        FAILS.append(name)


def sync_list(text):
    """The paths the sync's tar names, in order; shell variables ($MAIN_HDRS) left out."""
    lines = text.splitlines()
    try:
        i = next(n for n, l in enumerate(lines) if l.strip() == START)
    except StopIteration:
        return []
    out = []
    for l in lines[i + 1:]:
        s = l.strip()
        if s.startswith(END) or not s.endswith("\\"):
            break
        s = s[:-1].strip()
        if s and not s.startswith("$"):
            out.append(s)
    return out


def shipped_files(listed, root):
    """Every file the list ships, as root-relative paths with forward slashes."""
    out = set()
    for p in listed:
        full = os.path.join(root, p)
        if os.path.isdir(full):
            for dp, dns, fns in os.walk(full):
                dns[:] = [d for d in dns if d != "__pycache__"]
                out |= {os.path.relpath(os.path.join(dp, f), root).replace("\\", "/") for f in fns}
        else:
            out.add(p)
    return out


def imported(path):
    """Top-level names of every module the file imports, anywhere in it."""
    tree = ast.parse(open(path, "rb").read(), path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def missing_imports(listed, root):
    """[(shipped .py, module)] for each module of the importer's own folder that the list does not ship."""
    ship = shipped_files(listed, root)
    out = []
    for f in sorted(ship):
        if not f.endswith(".py") or not os.path.isfile(os.path.join(root, f)):
            continue
        folder = os.path.dirname(f)
        for name in sorted(imported(os.path.join(root, f))):
            sib = (folder + "/" if folder else "") + name + ".py"
            if os.path.isfile(os.path.join(root, sib)) and sib not in ship:
                out.append((f, name))
    return out


def checks():
    listed = sync_list(open(SCRIPT, encoding="utf-8").read())
    check("1 the sync list is found and names more than 20 paths", len(listed) > 20, len(listed))
    absent = [p for p in listed if not os.path.exists(os.path.join(ROOT, p))]
    check("2 every path in the sync list exists in this tree", not absent, absent)
    miss = missing_imports(listed, ROOT)
    check("3 no shipped .py imports an unshipped module of its own folder", not miss, miss)
    pd = "test/gen_inhibit_host/project_dir.py"
    ctl = missing_imports([p for p in listed if p != pd], ROOT)
    check("4a control: without project_dir.py, from_capture.py and replay_stimulus_delta.py are flagged",
          {("test/gen_inhibit_host/from_capture.py", "project_dir"),
           ("test/gen_inhibit_host/replay_stimulus_delta.py", "project_dir")} <= set(ctl), ctl)
    base = gi_paths.scratch("test_gi_host_run_list")
    try:
        os.makedirs(os.path.join(base, "t"))
        with open(os.path.join(base, "t", "a.py"), "w") as fh:
            fh.write("import os\n\n\ndef f():\n    from b import g\n    return g\n")
        open(os.path.join(base, "t", "b.py"), "w").close()
        got = missing_imports(["t/a.py"], base)
        check("4b control: a.py importing b inside a function, b.py unlisted: flagged", got == [("t/a.py", "b")], got)
        got = missing_imports(["t/a.py", "t/b.py"], base)
        check("4c control: the same with b.py listed: not flagged", got == [], got)
        got = missing_imports(["t"], base)
        check("4d control: the same folder listed whole: not flagged", got == [], got)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return FAILS


MUTANTS = [
    ("project_dir.py dropped from the sync", "    test/gen_inhibit_host/project_dir.py \\\n", ""),
    ("a path in the sync misspelled", "    test/gen_inhibit_host/run_tests.py \\\n", "    test/gen_inhibit_host/run_test.py \\\n"),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        got = checks()
        print("\n%s" % ("all ok" if not got else "%d FAIL: %s" % (len(got), "; ".join(got))))
        return 1 if got else 0
    base = gi_paths.scratch("test_gi_host_run_list_mutants")
    src = open(SCRIPT, encoding="utf-8", newline="").read()
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            p = os.path.join(base, "m%d_gi_host_run.sh" % n)
            open(p, "w", encoding="utf-8", newline="").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               env=dict(os.environ, GI_HOST_RUN_SH=p))
            fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
            dead = bool(fails) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "".join("\n           " + f[:130] for f in fails[:2])))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
