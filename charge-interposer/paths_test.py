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
    still names VTRUX_DATA, no code but paths.py and
    firmware/test/build_check.py names the diag DBC's file (every other
    reader goes through diag_dbc()), and no .py puts the working folder
    ("." or os.getcwd()) on sys.path, which is how the reverse-it root's
    canre/ used to come in with the scripts run from it --
    all five scans proven able to fail on a planted line.

Fake trees are built in a throwaway folder beside this file and removed.

Run:  python charge-interposer/paths_test.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as PA                     # noqa: E402
import canre_pin                       # noqa: E402

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
# The diag DBC's file name in code. Only paths.py names it, and
# build_check.py, which reads the same git copy from the tree it checks
# (and this test, in its docstring).
DIAG_RE = re.compile("vtrux-interposer-" + r"diag\.dbc")
DIAG_OK = ("paths.py", "build_check.py", "paths_test.py")
# The working folder put on sys.path: "." or os.getcwd(), in code.
CWD_RE = re.compile(r"sys\.path\.(insert|append)\([^)]*(['\"]\.['\"]|getcwd)")


def scan(root, exts, pattern, code_only):
    hits = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".pio", "__pycache__", ".git")]
        for fn in files:
            if not fn.endswith(exts):
                continue
            p = os.path.join(d, fn)
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


def main():
    names = ("REVERSE_IT_DIR", "VTRUX_DATA", "VTRUX_PUBLIC_REPO", "CANRE_DIR",
             "INTERPOSER_CODE", "CAN_TOOLS_DIR")
    saved = {k: os.environ.get(k) for k in names}
    tmp = tempfile.mkdtemp(dir=HERE, prefix="paths_test_")
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
        hits = [h for h in scan(HERE, exts, ABS_RE, False)
                if not h.startswith(os.path.basename(tmp))]
        check(not hits, "no machine-absolute path under charge-interposer/ "
              "(%s)" % hits[:3])
        hits = [h for h in scan(HERE, (".py", ".sh", ".cpp", ".h"),
                                CLIMB_RE, True)
                if not h.startswith(os.path.basename(tmp))]
        check(not hits, "no .py / .sh / .cpp / .h climbs out of the repo to "
              "notes/ (%s)" % hits[:3])
        hits = [h for h in scan(HERE, (".py", ".sh", ".cpp", ".h"),
                                RETIRED_RE, False)
                if not h.startswith(os.path.basename(tmp))
                and os.path.basename(h.split(":", 1)[0]) not in RETIRED_OK]
        check(not hits, "no .py / .sh / .cpp / .h but paths.py and this "
              "test names the retired variable (%s)" % hits[:3])
        hits = [h for h in scan(HERE, (".py", ".sh", ".cpp", ".h"),
                                DIAG_RE, True)
                if not h.startswith(os.path.basename(tmp))
                and os.path.basename(h.split(":", 1)[0]) not in DIAG_OK]
        check(not hits, "no code but paths.py and build_check.py names the "
              "diag DBC's file: every other reader uses diag_dbc() (%s)"
              % hits[:3])
        hits = [h for h in scan(HERE, (".py",), CWD_RE, True)
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
        check(len(scan(plant, (".py",), CWD_RE, True)) == 2,
              "the working-folder scan catches a planted '.' and getcwd(), "
              "and skips them in a comment")
    finally:
        import can_tools_pin_test as CT
        CT.rmtree(tmp)          # the stand-in clone's objects are read-only
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
