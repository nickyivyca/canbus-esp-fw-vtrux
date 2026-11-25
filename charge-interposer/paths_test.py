"""Tests paths.py and the path-fix commit it came with (tester, 2026-10-08).

  - each setting resolves where it should, and each mistake -- REVERSE_IT_DIR
    unset, pointing at the wrong folder (projects/vtrux, its value under the
    old VTRUX_DATA, included), only the retired VTRUX_DATA set, no public
    repo -- is a clear PathsError, not a wrong path (REVERSE_IT_DIR replaced
    VTRUX_DATA on 2026-10-20: user, via the methodology session);
  - code_dir() is charge-interposer/ itself, and an INTERPOSER_CODE naming
    another tree is refused (the moved scripts test the tree they are in);
  - canre is the pinned install (canre_pin.py), with or without
    REVERSE_IT_DIR, and never a canre/ under REVERSE_IT_DIR (port step 0,
    reviewer 2026-10-26; canre_pin_test.py tests the pin itself);
  - import_canbench() loads the pinned can-tools commit's canbench
    (can_tools_pin.py; AGENTS.md, "can-tools", user 2026-10-30; pin: user
    ruling 2026-10-30), not the clone's HEAD or working tree, and never
    reverse-it's canbench/ mirror, with REVERSE_IT_DIR set or not; it puts
    neither the install, the clone nor the reverse-it root on sys.path nor
    makes the root's canre/ importable, shown able to fail (the clone and
    the root each put on sys.path); and it refuses CAN_TOOLS_DIR unset, a
    CAN_TOOLS_DIR with no canbench/, the pin not installed, and a canbench
    already imported from elsewhere (the mirror included). The machine's
    own canbench (the can-tools.pth) is taken off sys.path first, so the
    checks do not depend on it; can_tools_pin_test.py tests the pin;
  - `run_scenario.py --list` runs with REVERSE_IT_DIR unset (only touching
    data needs it);
  - diag_dbc() is the git copy, diag/vtrux-interposer-diag.dbc beside
    paths.py, and needs no setting (the readers moved off the reverse-it
    copy: reviewer, 2026-10-24);
  - nothing under charge-interposer/ holds a machine-absolute path, no
    .py / .sh / .cpp / .h still climbs `..` out of the repo to SeaDrive's
    notes/, no .py / .sh / .cpp / .h other than paths.py and this file
    still names VTRUX_DATA, no code but paths.py names the diag DBC's file
    (every other reader goes through diag_dbc(); build_check.py, which
    also did, retired at port step 2a; from diag schema 5, 2026-11-24, the
    product's diag_table.py and the intp_diag_table.py it writes may, at
    charge-interposer/'s top only, while each still names it and the
    generator reads the file diag_dbc() names), and no .py puts the working folder
    ("." or os.getcwd()) on sys.path, which is how the reverse-it root's
    canre/ used to come in with the scripts run from it --
    all five scans proven able to fail on a planted line. The scans read
    what is or can be committed: what git ignores (a build's
    firmware/managed_components/, build/) is left out (git_ignored,
    2026-11-21), shown on a planted repo, untracked files it does not
    ignore still read.

Fake trees are built in a throwaway folder beside this file and removed.

Run:  python charge-interposer/paths_test.py
      python charge-interposer/paths_test.py --can-fail
        runs this test with each mutant of paths.py in MUTANTS (its text in
        a scratch_dir folder, run in the real module's namespace through
        PATHS_UNDER_TEST, so paths.py's own location still holds; the file
        itself is never edited); every mutant must make it fail, and the
        unmutated text must pass. Committed 2026-11-01 (the pin move): at
        the cutover it was a scratch script that edited paths.py in place.
        SCAN_MUTANTS mutate this file's own git_ignored filter and its diag
        DBC allowances, run as a mutated copy with PATHS_TEST_HERE naming
        charge-interposer/.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys

# PATHS_TEST_HERE: --can-fail runs a mutated copy of this file from its
# scratch folder (SCAN_MUTANTS) and points it back at charge-interposer/.
HERE = (os.environ.get("PATHS_TEST_HERE")
        or os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import paths as PA                     # noqa: E402
import canre_pin                       # noqa: E402
import scratch_dir                     # noqa: E402

# --can-fail: the child runs paths.py's mutated text in the real module's
# namespace (its __file__ unchanged), so every importer of paths sees it.
if os.environ.get("PATHS_UNDER_TEST"):
    with open(os.environ["PATHS_UNDER_TEST"], encoding="utf-8") as _f:
        exec(compile(_f.read(), PA.__file__, "exec"), PA.__dict__)

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def raises(fn, fragment):
    try:
        fn()
    except PA.PathsError as e:
        return fragment in str(e)
    return False


# A path that only exists on one machine. Kept as fragments so this file does
# not match itself.
ABS_RE = re.compile("|".join([
    r"seadrive" + r"_root",
    r"[A-Za-z]:[/\\]" + r"Users",
    r"/home/" + r"[a-z]",
    r"/mnt/c/" + r"Users",
    r"My " + r"Libraries",
]))
# Code (not comment) that climbs out of the repo to SeaDrive's notes/.
CLIMB_RE = re.compile(r"\.\.[\"'/\\, ]+\.\..*notes")
# The retired variable, in fragments so the pattern is not a literal here.
RETIRED_RE = re.compile("VTRUX" + "_DATA")
RETIRED_OK = ("paths.py", "paths_test.py")
# The diag DBC's file name in code. Only paths.py names it (and this test,
# in its docstring); build_check.py did too until it retired at port step
# 2a.
DIAG_RE = re.compile("vtrux-interposer-" + r"diag\.dbc")
DIAG_OK = ("paths.py", "paths_test.py")
# Product files that name it too, by path from charge-interposer/ (diag
# schema 5, tracker J): the implementor's table generator, whose input it
# is, and the tables it writes, whose header names their source. Each must
# still name it, and the generator must read the file diag_dbc() names.
DIAG_PRODUCT = ("diag_table.py", "intp_diag_table.py",
                "firmware/main/intp_diag_table.h")


def diag_named(hits):
    """The diag DBC scan's hits no allowance covers: paths.py and this test
    by base name, DIAG_PRODUCT only by their whole path from the scan's
    root."""
    return [h for h in hits
            if os.path.basename(h.split(":", 1)[0]) not in DIAG_OK
            and h.split(":", 1)[0].replace(os.sep, "/") not in DIAG_PRODUCT]
# The working folder put on sys.path: "." or os.getcwd(), in code.
CWD_RE = re.compile(r"sys\.path\.(insert|append)\([^)]*(['\"]\.['\"]|getcwd)")


def git_ignored(root):
    """What git ignores under root, as `git ls-files --others --ignored
    --exclude-standard --directory` lists it there: paths relative to root,
    a folder ending in "/". None when git cannot list it (no git, not a work
    tree): the scans then read every file. The scans read what is or can be
    committed, so a build's output git ignores is not read: the
    IDF component manager's firmware/managed_components/ (upstream READMEs
    with example home-folder paths) failed the absolute-path scan once a build
    ran in the worktree (harness run harness_fixes_1ce954e9, 2026-11-21)."""
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "--others",
                            "--ignored", "--exclude-standard", "--directory"],
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return set(ln for ln in r.stdout.splitlines() if ln)


def _is_ignored(rel, ignored):
    """rel (relative to the scan's root) is listed, or lies in a listed
    folder."""
    if not ignored:
        return False
    rel = rel.replace(os.sep, "/")
    if rel in ignored or rel + "/" in ignored:
        return True
    parts = rel.split("/")
    return any("/".join(parts[:i]) + "/" in ignored
               for i in range(1, len(parts)))


def scan(root, exts, pattern, code_only, ignored=None):
    """Hits of pattern in root's files with one of exts; ignored (from
    git_ignored) leaves out what git ignores."""
    hits = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".pio", "__pycache__", ".git")
                   and not _is_ignored(os.path.relpath(os.path.join(d, x),
                                                       root), ignored)]
        for fn in files:
            if not fn.endswith(exts):
                continue
            p = os.path.join(d, fn)
            if _is_ignored(os.path.relpath(p, root), ignored):
                continue
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    s = line.strip()
                    if code_only and (s.startswith("#") or s.startswith("//")
                                      or s.startswith(";")):
                        continue
                    if pattern.search(line):
                        hits.append("%s:%d: %s" % (os.path.relpath(p, root),
                                                   i, s[:100]))
    return hits


def _drop_canbench():
    for k in [k for k in sys.modules if k.split(".")[0] == "canbench"]:
        del sys.modules[k]


def _root_exposed(root):
    """Whether the reverse-it root is on sys.path or its canre/ is the one
    an `import canre` would find."""
    import importlib.util
    r = os.path.normcase(os.path.normpath(root))
    on_path = any(os.path.normcase(os.path.normpath(p or ".")) == r
                  for p in sys.path)
    saved = sys.modules.pop("canre", None)
    try:
        spec = importlib.util.find_spec("canre")
    finally:
        if saved is not None:
            sys.modules["canre"] = saved
    locs = list(getattr(spec, "submodule_search_locations", None) or []) \
        if spec else []
    if spec and spec.origin:
        locs.append(spec.origin)
    fake = any(os.path.normcase(os.path.normpath(l)).startswith(r + os.sep)
               for l in locs)
    return on_path or fake


def _on_path(d):
    """Whether folder d is on sys.path."""
    r = os.path.normcase(os.path.normpath(d))
    return any(os.path.normcase(os.path.normpath(p or ".")) == r
               for p in sys.path)


def canbench_checks(root, tmp):
    """import_canbench(): the pinned can-tools commit's canbench, written
    out of a stand-in clone at CAN_TOOLS_DIR (can_tools_pin_test.fixture:
    HEAD past the pin, an edit in its working tree), never reverse-it's
    canbench/ mirror; nothing on sys.path (the root would bring SeaDrive's
    canre/ with it, as bus.py's add_canre(required=False) did before port
    step 0). root is the fake reverse-it root, REVERSE_IT_DIR on entry; it
    gets a mirror canbench/ that must never be the one loaded."""
    import can_tools_pin_test as CT
    fix = CT.fixture(os.path.join(tmp, "ct"))
    clone, inst = fix["clone"], fix["folder"]
    for rel, text in (
            ("canbench/__init__.py", "MARK = 'mirror'\n"),
            ("canbench/live/__init__.py", ""),
            ("canbench/live/receiver.py", "X = 3\n"),
            ("canre/__init__.py", "FAKE = True\n")):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
    path_in = CT.off_pth()
    path0 = list(sys.path)
    try:
        with CT.pointed(fix) as ctp:
            os.environ.pop("CAN_TOOLS_DIR", None)
            check(raises(PA.import_canbench, "CAN_TOOLS_DIR is not set"),
                  "CAN_TOOLS_DIR unset: a PathsError naming the setting, the "
                  "mirror under REVERSE_IT_DIR not taken instead")
            check("canbench" not in sys.modules,
                  "... and no canbench is left imported")
            os.environ["CAN_TOOLS_DIR"] = tmp
            check(raises(PA.import_canbench, "has no canbench/"),
                  "a CAN_TOOLS_DIR with no canbench/ is refused")
            check(raises(PA.can_tools_dir, "it must be the can-tools clone"),
                  "... and can_tools_dir() says it must be the clone")

            os.environ["CAN_TOOLS_DIR"] = clone
            check(os.path.normpath(PA.can_tools_dir())
                  == os.path.normpath(clone), "can_tools_dir() is CAN_TOOLS_DIR")
            check(raises(PA.import_canbench, "can_tools_pin.py install"),
                  "the pin not installed: a PathsError naming the install "
                  "command, the clone not taken instead")
            ctp.install()
            cb = PA.import_canbench()
            import canbench.live.receiver as rcv
            check(getattr(cb, "MARK", None) == "pinned"
                  and getattr(rcv, "X", None) == 1,
                  "import_canbench() loads the pinned commit's canbench, not "
                  "the clone's HEAD or working tree or the mirror, and "
                  "canbench.live.receiver imports from it")
            check(not _on_path(clone) and not _on_path(inst)
                  and not _root_exposed(root),
                  "... with neither the install, the clone nor the reverse-it "
                  "root on sys.path, and the root's canre/ not importable")
            check(PA.import_canbench() is cb, "a second call returns the "
                  "same module")
            _drop_canbench()
            rit = os.environ.pop("REVERSE_IT_DIR")
            try:
                check(getattr(PA.import_canbench(), "MARK", None) == "pinned",
                      "import_canbench() needs no REVERSE_IT_DIR")
            finally:
                os.environ["REVERSE_IT_DIR"] = rit
            _drop_canbench()

            # the path checks above can fail: the clone on sys.path, and the
            # old way, the reverse-it root on sys.path
            sys.path.insert(0, clone)
            check(_on_path(clone), "can-fail: the clone put on sys.path is "
                  "caught")
            import canbench                          # noqa: F401
            sys.path[:] = path0
            check(raises(PA.import_canbench, "already imported from"),
                  "a canbench already imported from the clone (the .pth's "
                  "way) is refused")
            _drop_canbench()
            sys.path.insert(0, root)
            import canbench                          # noqa: F401,F811
            check(_root_exposed(root),
                  "can-fail: the root put on sys.path (the old way) is caught")
            sys.path[:] = path0
            check(raises(PA.import_canbench, "already imported from"),
                  "a canbench already imported from the mirror is refused")
            _drop_canbench()

            other = os.path.join(tmp, "other")
            os.makedirs(os.path.join(other, "canbench"))
            with open(os.path.join(other, "canbench", "__init__.py"), "w") as f:
                f.write("")
            sys.path.insert(0, other)
            import canbench                          # noqa: F401,F811
            sys.path[:] = path0
            check(raises(PA.import_canbench, "already imported from"),
                  "a canbench already imported from elsewhere is refused")
            _drop_canbench()

            shutil.rmtree(os.path.join(clone, "canbench"))
            check(raises(PA.import_canbench, "has no canbench/"),
                  "a clone with no canbench/ is a PathsError")
    finally:
        sys.path[:] = path_in
        _drop_canbench()


_USE = "        return can_tools_pin.use()\n"


def _by_file(where):
    """import_canbench's body replaced by a by-file load of where/canbench,
    with no pin (7d74975's way)."""
    return ("        import importlib.util\n"
            "        d = os.path.join(%s, 'canbench')\n"
            "        spec = importlib.util.spec_from_file_location(\n"
            "            'canbench', os.path.join(d, '__init__.py'),\n"
            "            submodule_search_locations=[d])\n"
            "        mod = importlib.util.module_from_spec(spec)\n"
            "        sys.modules['canbench'] = mod\n"
            "        spec.loader.exec_module(mod)\n"
            "        return mod\n" % where)


# (what, text in paths.py, its replacement): each must make this test fail.
MUTANTS = [
    ("canbench from the clone's working tree, unpinned (7d74975)",
     _USE, _by_file("can_tools_dir()")),
    ("canbench from the mirror (the old way)", _USE, _by_file("reverse_it_dir()")),
    ("CAN_TOOLS_DIR unchecked",
     "        return can_tools_pin.clone()\n",
     "        return os.environ.get('CAN_TOOLS_DIR', '')\n"),
    ("a canbench imported from elsewhere is taken", _USE,
     "        if 'canbench' in sys.modules:\n"
     "            return sys.modules['canbench']\n" + _USE),
    ("the clone put on sys.path", _USE,
     "        sys.path.insert(0, can_tools_pin.clone())\n" + _USE),
    ("the pin's refusal not a PathsError",
     _USE + "    except can_tools_pin.CanToolsPinError as e:\n"
     "        raise PathsError(str(e))\n",
     _USE + "    except KeyError as e:\n"
     "        raise PathsError(str(e))\n"),
]


# (what, text in this file, its replacement): mutants of the scans' own
# filter (git_ignored, 2026-11-21), run as a mutated copy of this file with
# PATHS_TEST_HERE naming charge-interposer/; each must make it fail.
SCAN_MUTANTS = [
    ("the scans read what git ignores",
     "    if not ignored:\n        return False\n",
     "    return False\n"),
    ("every untracked file left out, not only the ignored ones",
     '"--others",\n                            "--ignored", "--exclude',
     '"--others",\n                            "--exclude'),
    ("the scans of charge-interposer/ not given git's list",
     "        ign = git_ignored(HERE)\n", "        ign = None\n"),
    # The diag DBC's product allowances (diag schema 5, 2026-11-24).
    ("no product allowance: the generator and its tables not allowed",
     'DIAG_PRODUCT = ("diag_table.py", ' '"intp_diag_table.py",\n'
     '                "firmware/main/intp_diag_table.h")\n',
     'DIAG_PRODUCT = ()\n'),
    ("an allowance kept for a file that no longer names the DBC",
     'DIAG_PRODUCT = ("diag_table.py", ' '"intp_diag_table.py",\n',
     'DIAG_PRODUCT = ("bus.py", "diag_table.py", "intp_diag_table.py",\n'),
    ("the generator compared with another path than diag_dbc()",
     "os.path.normpath(dt.DBC) == os.path.normpath(PA." "diag_dbc())",
     "os.path.normpath(dt.DBC) == os.path.normpath(PA.diag_dbc() + '.x')"),
    ("the allowances taken by base name anywhere",
     '            and h.split(":", 1)[0].replace(os.sep, "/") not in '
     'DIAG_PRODUCT]',
     '            and os.path.basename(h.split(":", 1)[0]) not in '
     'DIAG_PRODUCT]'),
]


def can_fail():
    with open(os.path.join(HERE, "paths.py"), encoding="utf-8") as f:
        src = f.read()
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        own = f.read()
    jobs = ([("unmutated (must pass)", "paths", "", "")]
            + [(w, "paths", o, n) for w, o, n in MUTANTS]
            + [(w, "scan", o, n) for w, o, n in SCAN_MUTANTS])
    base = scratch_dir.make("paths-test-canfail")
    bad = 0
    try:
        for i, (what, kind, old, new) in enumerate(jobs):
            text = src if kind == "paths" else own
            if old and text.count(old) != 1:
                print("NOT EXERCISED  %s: its text is not in %s once"
                      % (what, "paths.py" if kind == "paths" else
                         "paths_test.py"))
                bad += 1
                continue
            if kind == "paths":
                p = os.path.join(base, "paths_m%02d.py" % i)
                run = [sys.executable, "-B", os.path.abspath(__file__)]
                env = dict(os.environ, PATHS_UNDER_TEST=p)
            else:
                p = os.path.join(base, "paths_test_m%02d.py" % i)
                run = [sys.executable, "-B", p]
                env = dict(os.environ, PATHS_TEST_HERE=HERE)
                env.pop("PATHS_UNDER_TEST", None)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text.replace(old, new) if old else text)
            r = subprocess.run(run, env=env, capture_output=True, text=True,
                               timeout=600)
            out = (r.stdout + r.stderr).splitlines()
            fails = [ln.strip() for ln in out if ln.strip().startswith("FAIL")]
            if not old:
                good = r.returncode == 0
                print("%s  %s (rc %d)" % ("ok      " if good else "BROKEN  ",
                                          what, r.returncode))
            else:
                good = r.returncode != 0
                print("%s  %s: %s" % ("caught  " if good else "SURVIVED", what,
                                      (fails or out[-1:] or ["no output"])[0][:150]))
            bad += not good
    finally:
        scratch_dir.remove(base)
    print("\npaths_test --can-fail: %s"
          % ("all %d mutants caught, the unmutated text passes"
             % (len(MUTANTS) + len(SCAN_MUTANTS))
             if not bad else "%d FAILED" % bad))
    return 1 if bad else 0


def main():
    if sys.argv[1:] == ["--can-fail"]:
        return can_fail()
    names = ("REVERSE_IT_DIR", "VTRUX_DATA", "VTRUX_PUBLIC_REPO", "CANRE_DIR",
             "INTERPOSER_CODE", "CAN_TOOLS_DIR")
    saved = {k: os.environ.get(k) for k in names}
    tmp = scratch_dir.make("paths-test")
    try:
        os.environ.pop("REVERSE_IT_DIR", None)
        os.environ.pop("VTRUX_DATA", None)
        check(raises(PA.data_dir, "REVERSE_IT_DIR is not set"),
              "REVERSE_IT_DIR unset: a PathsError naming the setting")
        check(raises(PA.fixtures, "REVERSE_IT_DIR is not set"),
              "... and fixtures() says the same, not a wrong path")
        pinned = os.path.normpath(canre_pin.folder())
        check(os.path.normpath(PA.canre_root()) == pinned,
              "canre_root() is the pinned install (%s), REVERSE_IT_DIR unset"
              % os.path.relpath(pinned, os.path.dirname(HERE)))

        os.environ["REVERSE_IT_DIR"] = tmp
        check(raises(PA.data_dir, "has no projects/vtrux/notes/artifacts"),
              "REVERSE_IT_DIR pointing at the wrong folder is refused")

        root = os.path.join(tmp, "reverse-it")
        vtrux = os.path.join(root, "projects", "vtrux")
        os.makedirs(os.path.join(vtrux, "notes", "artifacts",
                                 "interposer-firmware"))
        os.environ["REVERSE_IT_DIR"] = vtrux
        check(raises(PA.data_dir, "has no projects/vtrux/notes/artifacts"),
              "REVERSE_IT_DIR set to projects/vtrux (the old VTRUX_DATA "
              "value) is refused")

        os.environ.pop("REVERSE_IT_DIR")
        os.environ["VTRUX_DATA"] = vtrux
        check(raises(PA.data_dir, "VTRUX_DATA is set, but it was retired"),
              "only the retired VTRUX_DATA set (to a valid projects/vtrux): "
              "refused, saying it is retired -- never read as a fallback")
        os.environ.pop("VTRUX_DATA")

        os.environ["REVERSE_IT_DIR"] = root
        check(os.path.normpath(PA.reverse_it_dir()) == os.path.normpath(root),
              "reverse_it_dir() is REVERSE_IT_DIR")
        check(PA.data_dir() == vtrux, "data_dir() is projects/vtrux/")
        check(PA.fixtures() == os.path.join(vtrux, "notes", "artifacts",
                                            "interposer-firmware"),
              "fixtures() is projects/vtrux/notes/artifacts/"
              "interposer-firmware/")
        check(PA.runs_dir() == os.path.join(vtrux, "notes", "artifacts",
                                            "interposer-runs"),
              "runs_dir() is projects/vtrux/notes/artifacts/interposer-runs/")
        check(PA.logs() == os.path.join(vtrux, "logs"),
              "logs() is projects/vtrux/logs/")
        os.makedirs(os.path.join(root, "canre"))
        check(os.path.normpath(PA.canre_root()) == pinned,
              "a canre/ under REVERSE_IT_DIR is not taken: canre_root() is "
              "still the pinned install")
        os.environ["CANRE_DIR"] = os.path.join(tmp, "no-canre-here")
        check(raises(PA.canre_root, "not installed"),
              "a missing pinned install is a PathsError, not a fallback")
        if saved["CANRE_DIR"] is None:
            os.environ.pop("CANRE_DIR")
        else:
            os.environ["CANRE_DIR"] = saved["CANRE_DIR"]
        canbench_checks(root, tmp)
        os.environ["VTRUX_DATA"] = os.path.join(tmp, "elsewhere")
        check(PA.data_dir() == vtrux,
              "a stale VTRUX_DATA beside REVERSE_IT_DIR changes nothing")
        os.environ.pop("VTRUX_DATA")

        pub = os.path.join(tmp, "pub")
        os.makedirs(os.path.join(pub, "projects", "vtrux"))
        os.environ["VTRUX_PUBLIC_REPO"] = pub
        check(raises(PA.dbc_dir, "no public repo"),
              "a public repo with no DBCs is refused")
        open(os.path.join(pub, "projects", "vtrux", "epri-pt-bus.dbc"),
             "w").close()
        check(PA.dbc_dir() == os.path.join(pub, "projects", "vtrux"),
              "VTRUX_PUBLIC_REPO is honoured")
        os.environ.pop("VTRUX_PUBLIC_REPO")
        os.environ.pop("REVERSE_IT_DIR")
        dd = PA.diag_dbc()
        check(os.path.normpath(dd) == os.path.normpath(os.path.join(
            HERE, "diag", "vtrux-interposer-" + "diag.dbc")),
              "diag_dbc() is diag/ beside paths.py, REVERSE_IT_DIR unset")
        with open(dd, encoding="utf-8", errors="replace") as f:
            first = f.readline()
        check(first.startswith('VERSION "vtrux-interposer-diag schema'),
              "... and it is the diag DBC (%r)" % first.strip()[:60])
        os.environ["REVERSE_IT_DIR"] = root
        open(os.path.join(vtrux, "vtrux-interposer-" + "diag.dbc"),
             "w").close()
        check(PA.diag_dbc() == dd,
              "a reverse-it copy under REVERSE_IT_DIR is not read: "
              "diag_dbc() is still the git copy")
        check(os.path.normpath(PA.PUBLIC_DEFAULT) == os.path.normpath(
            os.path.join(HERE, "..", "..", "canbus-reveng-vtrux-coda")),
              "the default public repo is the sibling clone, by relative "
              "path")

        os.environ.pop("INTERPOSER_CODE", None)
        check(PA.code_dir() == HERE, "code_dir() is the folder paths.py is in, "
              "with INTERPOSER_CODE unset")
        os.environ["INTERPOSER_CODE"] = HERE + os.sep
        check(PA.code_dir() == HERE, "... and with INTERPOSER_CODE naming it")
        os.environ["INTERPOSER_CODE"] = tmp
        check(raises(PA.code_dir, "is not this tree's charge-interposer/"),
              "an INTERPOSER_CODE naming another tree is refused")
        if saved["INTERPOSER_CODE"] is None:
            os.environ.pop("INTERPOSER_CODE")
        else:
            os.environ["INTERPOSER_CODE"] = saved["INTERPOSER_CODE"]

        env = dict(os.environ)
        env.pop("REVERSE_IT_DIR", None)
        env.pop("VTRUX_DATA", None)
        r = subprocess.run([sys.executable,
                            os.path.join(HERE, "run_scenario.py"), "--list"],
                           capture_output=True, text=True, env=env)
        check(r.returncode == 0 and r.stdout.strip(),
              "run_scenario.py --list runs with REVERSE_IT_DIR unset "
              "(exit %d%s)"
              % (r.returncode, "" if r.returncode == 0
                 else ": " + (r.stderr.strip().splitlines() or [""])[-1]))

        # The scans, and that they can fail.
        exts = (".py", ".sh", ".cpp", ".h", ".ini", ".md", ".json", ".txt")
        ign = git_ignored(HERE)
        check(ign is not None, "git lists what it ignores under "
              "charge-interposer/ (the scans read only what can be committed)")
        hits = [h for h in scan(HERE, exts, ABS_RE, False, ign)
                if not h.startswith(os.path.basename(tmp))]
        check(not hits, "no machine-absolute path under charge-interposer/ "
              "(%s)" % hits[:3])
        hits = [h for h in scan(HERE, (".py", ".sh", ".cpp", ".h"),
                                CLIMB_RE, True, ign)
                if not h.startswith(os.path.basename(tmp))]
        check(not hits, "no .py / .sh / .cpp / .h climbs out of the repo to "
              "notes/ (%s)" % hits[:3])
        hits = [h for h in scan(HERE, (".py", ".sh", ".cpp", ".h"),
                                RETIRED_RE, False, ign)
                if not h.startswith(os.path.basename(tmp))
                and os.path.basename(h.split(":", 1)[0]) not in RETIRED_OK]
        check(not hits, "no .py / .sh / .cpp / .h but paths.py and this "
              "test names the retired variable (%s)" % hits[:3])
        raw = [h for h in scan(HERE, (".py", ".sh", ".cpp", ".h"),
                               DIAG_RE, True, ign)
               if not h.startswith(os.path.basename(tmp))]
        hits = diag_named(raw)
        check(not hits, "no code but paths.py names the "
              "diag DBC's file: every other reader uses diag_dbc() (%s)"
              % hits[:3])
        named = set(h.split(":", 1)[0].replace(os.sep, "/") for h in raw)
        check(set(DIAG_PRODUCT) <= named, "each product file allowed to "
              "name the diag DBC's file still names it (%s does not)"
              % sorted(set(DIAG_PRODUCT) - named))
        spec = importlib.util.spec_from_file_location(
            "diag_table_under_test", os.path.join(HERE, "diag_table.py"))
        dt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dt)
        check(os.path.normpath(dt.DBC) == os.path.normpath(PA.diag_dbc()),
              "diag_table.py, the table generator, reads the DBC "
              "diag_dbc() names (%s)" % dt.DBC)
        hits = [h for h in scan(HERE, (".py",), CWD_RE, True, ign)
                if not h.startswith(os.path.basename(tmp))
                and os.path.basename(h.split(":", 1)[0]) != "paths_test.py"]
        check(not hits, "no .py puts the working folder on sys.path (%s)"
              % hits[:3])
        plant = os.path.join(tmp, "plant")
        os.makedirs(plant)
        with open(os.path.join(plant, "a.py"), "w") as f:
            f.write('X = "C:' + '/Users/someone/thing"\n')
            up = '"' + '..", "' + '.."'      # in fragments: no self-match
            f.write('Y = os.path.join(HERE, ' + up + ', "notes", "x")\n')
            f.write('# Z = os.path.join(HERE, ' + up + ', "notes", "x")\n')
            dn = '"vtrux-interposer-' + 'diag.dbc"'
            f.write('D = os.path.join(paths.data_dir(), ' + dn + ')\n')
            f.write('# D = os.path.join(paths.data_dir(), ' + dn + ')\n')
            f.write('sys.path.insert(0, "' + '.")\n')
            f.write('# sys.path.insert(0, "' + '.")\n')
        with open(os.path.join(plant, "c.py"), "w") as f:
            f.write('sys.path.append(os.' + 'getcwd())\n')
        with open(os.path.join(plant, "b.cpp"), "w") as f:
            f.write('const char* v = std::getenv("VTRUX' + '_DATA");\n')
        check(len(scan(plant, (".py",), ABS_RE, False)) == 1,
              "the absolute-path scan catches a planted path")
        check(len(scan(plant, (".py",), CLIMB_RE, True)) == 1,
              "the climb scan catches a planted climb, and skips it in a "
              "comment")
        check(len(scan(plant, (".cpp",), RETIRED_RE, False)) == 1,
              "the retired-variable scan catches a planted getenv")
        check(len(scan(plant, (".py",), DIAG_RE, True)) == 1,
              "the diag DBC scan catches a planted reverse-it read, and "
              "skips it in a comment")
        top = ["diag_table.py:41: x", "intp_diag_table.py:1: x",
               "firmware/main/intp_diag_table.h:2: x",
               "paths.py:3: x", "paths_test.py:2: x"]
        deep = ["sub/diag_table.py:4: x", "intp_diag_table.h:1: x",
                "firmware/intp_diag_table.py:1: x", "run_scenario.py:9: x"]
        check(diag_named(top) == [] and diag_named(deep) == deep,
              "the diag DBC allowances cover paths.py, this test, and the "
              "product's generator and tables only at their own paths (%s)"
              % (diag_named(top + deep),))
        check(len(scan(plant, (".py",), CWD_RE, True)) == 2,
              "the working-folder scan catches a planted '.' and getcwd(), "
              "and skips them in a comment")
        # What git ignores is left out, and nothing else: a planted repo
        # with an ignored folder (a nested file, as managed_components/
        # holds) and an untracked file it does not ignore.
        pg = os.path.join(tmp, "plant_git")
        os.makedirs(os.path.join(pg, "ign", "deep"))
        r = subprocess.run(["git", "init", "-q", pg], capture_output=True,
                           text=True)
        line = 'X = "/home/' + 'someone/thing"\n'
        with open(os.path.join(pg, ".gitignore"), "w") as f:
            f.write("ign/\n")
        with open(os.path.join(pg, "ign", "deep", "x.py"), "w") as f:
            f.write(line)
        with open(os.path.join(pg, "y.py"), "w") as f:
            f.write(line)
        pig = git_ignored(pg)
        hit_all = scan(pg, (".py",), ABS_RE, False)
        hit_ign = scan(pg, (".py",), ABS_RE, False, pig)
        check(r.returncode == 0 and len(hit_all) == 2
              and [h.split(":", 1)[0] for h in hit_ign] == ["y.py"],
              "the absolute-path scan leaves out a file in a folder git "
              "ignores and still reads an untracked file it does not "
              "(all %s; with git's list %s, from %s)"
              % (hit_all, hit_ign, sorted(pig or [])))
    finally:
        scratch_dir.remove(tmp)  # the stand-in clone's objects are read-only
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\npaths_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
