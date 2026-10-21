"""Tests paths.py and the path-fix commit it came with (tester, 2026-10-08).

  - each setting resolves where it should, and each mistake -- REVERSE_IT_DIR
    unset, pointing at the wrong folder (projects/vtrux, its value under the
    old VTRUX_DATA, included), only the retired VTRUX_DATA set, no public
    repo -- is a clear PathsError, not a wrong path (REVERSE_IT_DIR replaced
    VTRUX_DATA on 2026-10-20: user, via the methodology session);
  - add_canre(required=False) tolerates a missing setting;
  - `run_scenario.py --list` runs with REVERSE_IT_DIR unset (only touching
    data needs it);
  - nothing under charge-interposer/ holds a machine-absolute path, no
    .py / .sh / .cpp / .h still climbs `..` out of the repo to SeaDrive's
    notes/, and no .py / .sh / .cpp / .h other than paths.py and this file
    still names VTRUX_DATA --
    all three scans proven able to fail on a planted line.

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


def main():
    names = ("REVERSE_IT_DIR", "VTRUX_DATA", "VTRUX_PUBLIC_REPO")
    saved = {k: os.environ.get(k) for k in names}
    tmp = tempfile.mkdtemp(dir=HERE, prefix="paths_test_")
    try:
        os.environ.pop("REVERSE_IT_DIR", None)
        os.environ.pop("VTRUX_DATA", None)
        check(raises(PA.data_dir, "REVERSE_IT_DIR is not set"),
              "REVERSE_IT_DIR unset: a PathsError naming the setting")
        check(raises(PA.fixtures, "REVERSE_IT_DIR is not set"),
              "... and fixtures() says the same, not a wrong path")
        check(PA.add_canre(required=False) is None,
              "add_canre(required=False) tolerates the missing setting")

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
        check(PA.add_canre(required=False) is None,
              "... and add_canre(required=False) takes no canre from it")
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
        check(raises(PA.canre_root, "no canre/"),
              "no canre/ in REVERSE_IT_DIR is refused")
        os.makedirs(os.path.join(root, "canre"))
        check(os.path.normpath(PA.canre_root()) == os.path.normpath(root),
              "canre_root() is REVERSE_IT_DIR itself")
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
        check(os.path.normpath(PA.PUBLIC_DEFAULT) == os.path.normpath(
            os.path.join(HERE, "..", "..", "canbus-reveng-vtrux-coda")),
              "the default public repo is the sibling clone, by relative "
              "path")

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
        plant = os.path.join(tmp, "plant")
        os.makedirs(plant)
        with open(os.path.join(plant, "a.py"), "w") as f:
            f.write('X = "C:' + '/Users/someone/thing"\n')
            up = '"' + '..", "' + '.."'      # in fragments: no self-match
            f.write('Y = os.path.join(HERE, ' + up + ', "notes", "x")\n')
            f.write('# Z = os.path.join(HERE, ' + up + ', "notes", "x")\n')
        with open(os.path.join(plant, "b.cpp"), "w") as f:
            f.write('const char* v = std::getenv("VTRUX' + '_DATA");\n')
        check(len(scan(plant, (".py",), ABS_RE, False)) == 1,
              "the absolute-path scan catches a planted path")
        check(len(scan(plant, (".py",), CLIMB_RE, True)) == 1,
              "the climb scan catches a planted climb, and skips it in a "
              "comment")
        check(len(scan(plant, (".cpp",), RETIRED_RE, False)) == 1,
              "the retired-variable scan catches a planted getenv")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
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
