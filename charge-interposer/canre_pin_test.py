#!/usr/bin/env python3
"""Tests canre_pin.py: the harness's canre is the commit pins.json pins, or it
is refused.

Charge-interposer tester, 2026-10-26 (port step 0 for the interposer), from
gen-inhibit's test_canre_pin.py (5d85dce), with the interposer's folder rule.
Stand-in installs are made in a scratch_dir run folder (2026-10-31),
laid out as pip lays one out (canre/, a canre-<version>.dist-info with
METADATA, direct_url.json and a RECORD of sha256s), so no network is needed:

  1. pin(): the committed entry is the reviewer's (repo, 0.1.0, ffbe0291...);
     a pins file with no canre entry, a commit that is short or not
     lower-case hex, or no repo, is refused.
  2. verify(): a stand-in install of the pin passes; refused are a missing
     folder (the refusal names the install command), another commit, another
     repo, a vcs other than git, no direct_url.json, an editable install,
     another version, two dist-infos, a file changed after the install, a
     file added to canre/, a listed file removed, and no canre/__init__.py.
     A __pycache__ in canre/ is not refused.
  3. use(), each in a fresh interpreter: canre and canre.parsers load from
     the install, which is first on sys.path; a canre folder earlier on
     sys.path but not imported (SeaDrive's, the way before this step) does
     not win; a canre already imported from it is refused; an install that
     fails verify() is refused.
  4. install(), with a stand-in pip in canre_pin.PIP: into
     <worktree>/scratch/canre/<commit> with --no-deps --target and the
     pinned git URL, no partial folder left, and never into $CANRE_DIR; a
     second call installs nothing; a pip that installs another commit, or
     fails, leaves no folder and no partial; a folder there already that
     fails the checks is refused, not replaced, and pip is not run.
  5. folder(): a worktree's .git (a file) is found by walking up; with no
     .git anywhere and no $CANRE_DIR it is refused, naming CANRE_DIR;
     $CANRE_DIR, when set, is the folder; a relative one is refused.
  6. Every .py under charge-interposer/ (firmware/ aside) that imports canre
     also calls canre_pin.use or paths.add_canre, and replay.py is among
     them. Control: a planted file importing canre without either is
     flagged; with one, not.
  7. The real install of the pin passes verify(): in a worktree the one
     `canre_pin.py install` made; on madhouse the shipped $CANRE_DIR.

    py -3.14 -B charge-interposer/canre_pin_test.py
    py -3.14 -B charge-interposer/canre_pin_test.py --can-fail

--can-fail runs the test on copies of canre_pin.py with one edit each
(CANRE_PIN_SRC names the copy's folder, with pins.json beside it), and
passes only if every one is caught.
"""
import ast
import base64
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("CANRE_PIN_SRC") or HERE
sys.path.insert(0, SRC_DIR)
import canre_pin  # noqa: E402
import scratch_dir  # noqa: E402

REPO = "https://github.com/nickyivyca/canbus-reveng-vtrux-coda"
COMMIT = "ffbe0291f7b817360f03f1082e07240804cf6084"
OTHER = "0123456789abcdef0123456789abcdef01234567"
FAILS = []


def check(name, ok, detail=""):
    print("  %-4s %-72s %s" % ("ok" if ok else "FAIL", name,
                               "" if ok else repr(detail)[:220]))
    if not ok:
        FAILS.append(name)


def refused(fn, *args, has="", **kw):
    """The refusal's text if fn raises CanrePinError (containing has), else
    None; any other exception is not a refusal either (it is None), so a
    mutant that crashes is a FAIL."""
    try:
        fn(*args, **kw)
    except canre_pin.CanrePinError as e:
        return str(e) if has in str(e) else None
    except Exception:       # noqa: BLE001
        return None
    return None


def attempt(fn, *args, **kw):
    """fn's result, or "refused: ..." / "error: ..." for what it raised."""
    try:
        return fn(*args, **kw)
    except canre_pin.CanrePinError as e:
        return "refused: %s" % e
    except Exception as e:  # noqa: BLE001
        return "error: %s: %s" % (type(e).__name__, e)


def b64(data):
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_install(d, commit=COMMIT, repo=REPO, version="0.1.0", vcs="git",
                 du="vcs", mark="pinned"):
    """A stand-in of what pip --target leaves: canre/ (whose MARK is mark)
    and its dist-info."""
    files = {"canre/__init__.py": "MARK = %r\n" % mark,
             "canre/parsers/__init__.py":
                 "def parse_file(path):\n    return iter(())\n"}
    info = "canre-%s.dist-info" % version
    files[info + "/METADATA"] = ("Metadata-Version: 2.1\nName: canre\n"
                                 "Version: %s\n\nbody\n" % version)
    files[info + "/INSTALLER"] = "pip\n"
    if du == "vcs":
        files[info + "/direct_url.json"] = json.dumps(
            {"url": repo, "vcs_info": {"commit_id": commit, "vcs": vcs}})
    elif du == "editable":
        files[info + "/direct_url.json"] = json.dumps(
            {"url": "file:///src/canre", "dir_info": {"editable": True}})
    rows = []
    for rel, text in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        rows.append("%s,sha256=%s,%d" % (rel, b64(text.encode()),
                                         len(text.encode())))
    rows.append(info + "/RECORD,,")
    with open(os.path.join(d, info, "RECORD"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write("\n".join(rows) + "\n")
    return d


FAKE_PIP = r'''
import json, os, sys
sys.path.insert(0, os.environ["TEST_DIR"])
import canre_pin_test as T
a = sys.argv[1:]
with open(os.environ["PIP_LOG"], "a") as fh:
    fh.write(json.dumps(a) + "\n")
if os.environ.get("PIP_EXIT"):
    os.makedirs(a[a.index("--target") + 1], exist_ok=True)
    sys.exit(int(os.environ["PIP_EXIT"]))
commit = os.environ.get("PIP_COMMIT") or a[-1].rsplit("@", 1)[1]
T.make_install(a[a.index("--target") + 1], commit=commit)
'''


def child(code, *args):
    """Run code in a fresh interpreter with canre_pin importable; (exit,
    output)."""
    env = dict(os.environ)
    env.pop("CANRE_DIR", None)
    r = subprocess.run([sys.executable, "-B", "-c",
                        "import sys; sys.path.insert(0, sys.argv[1]); "
                        "import canre_pin\n" + code, SRC_DIR] + list(args),
                       capture_output=True, text=True, timeout=120, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


PIN_CALLS = {"canre_pin.use", "paths.add_canre", "add_canre", "PA.add_canre"}


def importers(root):
    """[(path, uses the pin)] for each .py under root (firmware/ aside) that
    imports canre."""
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [x for x in dns if x not in ("firmware", "__pycache__")]
        for f in fns:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            tree = ast.parse(open(p, "rb").read(), p)
            imp = any((isinstance(n, ast.Import) and
                       any(a.name.split(".")[0] == "canre" for a in n.names))
                      or (isinstance(n, ast.ImportFrom) and not n.level and
                          (n.module or "").split(".")[0] == "canre")
                      for n in ast.walk(tree))
            if not imp:
                continue
            calls = {ast.unparse(n.func) for n in ast.walk(tree)
                     if isinstance(n, ast.Call)}
            out.append((p, bool(calls & PIN_CALLS)))
    return out


def checks():
    base = scratch_dir.make("canre-pin-test")
    try:
        # 1. the pin
        p = canre_pin.pin()
        check("1 pins.json's canre entry is the reviewer's (repo, 0.1.0, ffbe0291...)",
              p == {"repo": REPO, "version": "0.1.0", "commit": COMMIT}, p)
        for name, entry in (("no canre entry", None),
                            ("a short commit", dict(p, commit=COMMIT[:7])),
                            ("an upper-case commit", dict(p, commit=COMMIT.upper())),
                            ("no repo", {"version": "0.1.0", "commit": COMMIT})):
            f = os.path.join(base, "pins_%s.json" % name.replace(" ", "_"))
            with open(f, "w") as fh:
                json.dump({"other": {}} if entry is None else {"canre": entry}, fh)
            check("1 a pins file with %s is refused" % name,
                  refused(canre_pin.pin, f) is not None)

        # 2. verify
        good = make_install(os.path.join(base, "good"))
        info = attempt(canre_pin.verify, good, p)
        check("2 a stand-in install of the pin passes",
              info == os.path.join(good, "canre-0.1.0.dist-info"), info)
        e = refused(canre_pin.verify, os.path.join(base, "absent"), p, has="install")
        check("2 a missing folder is refused, naming the install command",
              e is not None and "canre_pin.py" in e, e)
        bad = {
            "another commit": dict(commit=OTHER),
            "another repo": dict(repo="https://example.org/fork"),
            "a vcs other than git": dict(vcs="hg"), "no direct_url.json": dict(du=None),
            "an editable install": dict(du="editable"),
            "another version": dict(version="0.2.0"),
        }
        for name, kw in bad.items():
            d = make_install(os.path.join(base, "bad_" + name.replace(" ", "_")), **kw)
            check("2 %s is refused" % name, refused(canre_pin.verify, d, p) is not None)
        d = make_install(os.path.join(base, "two_infos"))
        shutil.copytree(os.path.join(d, "canre-0.1.0.dist-info"),
                        os.path.join(d, "canre-0.0.9.dist-info"))
        check("2 two dist-infos are refused", refused(canre_pin.verify, d, p) is not None)
        d = make_install(os.path.join(base, "changed"))
        with open(os.path.join(d, "canre", "parsers", "__init__.py"), "a") as fh:
            fh.write("# edited\n")
        e = refused(canre_pin.verify, d, p)
        check("2 a file changed after the install is refused, by name",
              e is not None and "parsers" in e, e)
        d = make_install(os.path.join(base, "added"))
        open(os.path.join(d, "canre", "parsers", "extra.py"), "w").close()
        e = refused(canre_pin.verify, d, p)
        check("2 a file added to canre/ is refused, by name",
              e is not None and "extra.py" in e, e)
        d = make_install(os.path.join(base, "removed"))
        os.remove(os.path.join(d, "canre", "parsers", "__init__.py"))
        check("2 a listed file removed is refused", refused(canre_pin.verify, d, p) is not None)
        d = make_install(os.path.join(base, "noinit"))
        shutil.rmtree(os.path.join(d, "canre"))
        check("2 no canre/__init__.py is refused", refused(canre_pin.verify, d, p) is not None)
        d = make_install(os.path.join(base, "pycache"))
        os.makedirs(os.path.join(d, "canre", "__pycache__"))
        open(os.path.join(d, "canre", "__pycache__", "x.cpython-314.pyc"), "wb").close()
        check("2 control: a __pycache__ in canre/ is not refused",
              refused(canre_pin.verify, d, p) is None)

        # 3. use, in fresh interpreters
        shadow = os.path.join(base, "seadrive")
        os.makedirs(os.path.join(shadow, "canre", "parsers"))
        with open(os.path.join(shadow, "canre", "__init__.py"), "w") as fh:
            fh.write("MARK = 'shadow'\n")
        open(os.path.join(shadow, "canre", "parsers", "__init__.py"), "w").close()
        show = ("import canre, canre.parsers, os\n"
                "print(canre.MARK, sys.path[0] == sys.argv[2], "
                "canre.parsers.__file__.startswith(sys.argv[2] + os.sep))\n")
        rc, out = child("canre_pin.use(sys.argv[2])\n" + show, good)
        check("3 use: canre and canre.parsers load from the install, first on sys.path",
              rc == 0 and out == "pinned True True", (rc, out[-200:]))
        rc, out = child("sys.path.insert(0, sys.argv[3]); canre_pin.use(sys.argv[2])\n"
                        + show, good, shadow)
        check("3 use: a canre folder earlier on sys.path, not yet imported, does not win",
              rc == 0 and out == "pinned True True", (rc, out[-200:]))
        rc, out = child("sys.path.insert(0, sys.argv[3]); import canre; "
                        "canre_pin.use(sys.argv[2])", good, shadow)
        check("3 use: a canre already imported from elsewhere is refused",
              rc != 0 and "CanrePinError" in out and "already imported" in out,
              (rc, out[-200:]))
        rc, out = child("canre_pin.use(sys.argv[2])", os.path.join(base, "bad_another_commit"))
        check("3 use: an install that fails the checks is refused",
              rc != 0 and "CanrePinError" in out, (rc, out[-200:]))

        # 4. install, with a stand-in pip, into a planted worktree
        wt = os.path.join(base, "wt")
        start = os.path.join(wt, "charge-interposer")
        os.makedirs(start)
        with open(os.path.join(wt, ".git"), "w") as fh:
            fh.write("gitdir: elsewhere\n")
        fake = os.path.join(base, "fake_pip.py")
        with open(fake, "w") as fh:
            fh.write(FAKE_PIP)
        log = os.path.join(base, "pip_log.txt")
        elsewhere = os.path.join(base, "canre_dir")
        saved_pip, saved_env = canre_pin.PIP, dict(os.environ)
        canre_pin.PIP = [sys.executable, "-B", fake]
        os.environ.update(TEST_DIR=HERE, PIP_LOG=log, CANRE_DIR=elsewhere)

        def calls():
            return [json.loads(ln) for ln in open(log)] if os.path.exists(log) else []

        def partials():
            par = os.path.join(wt, "scratch", "canre")
            return [x for x in os.listdir(par) if ".partial-" in x] \
                if os.path.isdir(par) else []
        want = os.path.join(wt, "scratch", "canre", COMMIT)
        try:
            got = attempt(canre_pin.install, p, start)
            c = calls()
            check("4 install: into <worktree>/scratch/canre/<commit>, installed now, checked",
                  got == (want, True) and refused(canre_pin.verify, want, p) is None, got)
            check("4 install: $CANRE_DIR set, but nothing is written there",
                  not os.path.exists(elsewhere), elsewhere)
            check("4 install: pip is run once, --no-deps --target <partial> "
                  "git+<repo>@<commit>",
                  len(c) == 1 and "--no-deps" in c[0] and
                  c[0][-1] == "git+%s@%s" % (REPO, COMMIT) and
                  ".partial-" in c[0][c[0].index("--target") + 1], c)
            check("4 install: no partial folder is left", partials() == [], partials())
            got = attempt(canre_pin.install, p, start)
            check("4 install again: nothing installed, pip not run",
                  got == (want, False) and len(calls()) == 1, (got, len(calls())))
            scratch_dir.remove(want)
            for name, env in (("installs another commit", {"PIP_COMMIT": OTHER}),
                              ("fails", {"PIP_EXIT": "1"})):
                os.environ.update(env)
                e = refused(canre_pin.install, p, start)
                for k in env:
                    del os.environ[k]
                check("4 a pip that %s: refused, no folder, no partial" % name,
                      e is not None and not os.path.exists(want) and partials() == [],
                      (e, partials()))
            scratch_dir.remove(want)
            make_install(want, commit=OTHER)
            n = len(calls())
            e = refused(canre_pin.install, p, start, has="not replaced")
            check("4 a folder there already that fails the checks: refused, not "
                  "replaced, pip not run",
                  e is not None and len(calls()) == n and
                  json.load(open(os.path.join(want, "canre-0.1.0.dist-info",
                                              "direct_url.json")))
                  ["vcs_info"]["commit_id"] == OTHER, e)
        finally:
            canre_pin.PIP = saved_pip
            os.environ.clear()
            os.environ.update(saved_env)

        # 5. folder
        check("5 worktree: a worktree's .git file is found by walking up",
              os.path.normcase(canre_pin.worktree(start) or "") == os.path.normcase(wt),
              canre_pin.worktree(start))
        check("5 folder: in a worktree, <worktree>/scratch/canre/<commit>",
              attempt(canre_pin.folder, p, start, environ={}) == want,
              attempt(canre_pin.folder, p, start, environ={}))
        nogit = os.path.join(base, "nogit", "code")
        os.makedirs(nogit)
        with walk_within(canre_pin, base):
            e = refused(canre_pin.folder, p, nogit, environ={}, has="CANRE_DIR")
        check("5 folder: no .git anywhere and no CANRE_DIR is refused, naming it",
              e is not None, attempt(canre_pin.folder, p, nogit, environ={}))
        got = attempt(canre_pin.folder, p, nogit, environ={"CANRE_DIR": elsewhere})
        check("5 folder: CANRE_DIR, when set, is the folder", got == elsewhere, got)
        got = attempt(canre_pin.folder, p, start, environ={"CANRE_DIR": elsewhere})
        check("5 folder: ... in a worktree too", got == elsewhere, got)
        check("5 folder: a relative CANRE_DIR is refused",
              refused(canre_pin.folder, p, nogit,
                      environ={"CANRE_DIR": "rel/canre"}) is not None,
              attempt(canre_pin.folder, p, nogit, environ={"CANRE_DIR": "rel/canre"}))

        # 6. every importer goes through the pin
        found = importers(HERE)
        names = sorted(os.path.basename(f) for f, _ in found)
        check("6 the canre importers are found (replay.py among them)",
              "replay.py" in names, names)
        check("6 every one of them calls canre_pin.use or paths.add_canre",
              all(ok for _, ok in found), [f for f, ok in found if not ok])
        pl = os.path.join(base, "planted")
        os.makedirs(pl)
        with open(os.path.join(pl, "a.py"), "w") as fh:
            fh.write("def f():\n    from canre.parsers import parse_file\n"
                     "    return parse_file\n")
        with open(os.path.join(pl, "b.py"), "w") as fh:
            fh.write("import paths\npaths.add_canre()\nimport canre\n")
        got = sorted((os.path.basename(f), ok) for f, ok in importers(pl))
        check("6 control: an import without the pin is flagged, one with it is not",
              got == [("a.py", False), ("b.py", True)], got)

        # 7. the real install
        d = attempt(canre_pin.folder, p)
        e = d if d.startswith(("refused:", "error:")) else attempt(canre_pin.verify, d, p)
        e = None if isinstance(e, str) and e.endswith(".dist-info") else e
        check("7 the real install of the pin (%s) passes the checks"
              % ("$CANRE_DIR" if os.environ.get("CANRE_DIR") else "this worktree's"),
              e is None, e)
    finally:
        scratch_dir.remove(base)
    return FAILS


@contextlib.contextmanager
def walk_within(mod, base):
    """The stand-ins are made in a scratch_dir run folder, inside this
    repo's own worktree (2026-10-31; before, the machine temp folder), so a
    walk up from them would reach the real .git. For the no-git case the
    walk sees only what is under base, as if base were the whole disk."""
    real = mod.worktree
    top = os.path.normcase(os.path.abspath(base)) + os.sep

    def within(start=None, exists=os.path.exists):
        return real(start, exists=lambda p: os.path.normcase(
            os.path.abspath(p)).startswith(top) and exists(p))
    mod.worktree = within
    try:
        yield
    finally:
        mod.worktree = real


MUTANTS = [
    ("the commit not compared", 'vcs.get("commit_id") != p["commit"]', "False"),
    ("the repo not compared", 'du.get("url") != p["repo"] or ', ""),
    ("the vcs not compared", 'vcs.get("vcs") != "git" or ', ""),
    ("the version not compared", '    if version != p["version"]:', "    if False:"),
    ("no direct_url.json accepted",
     "    if not os.path.isfile(du_path):\n        raise",
     "    if not os.path.isfile(du_path):\n        return info\n        raise"),
    ("two dist-infos accepted", "    if len(infos) != 1:", "    if not infos:"),
    ("the RECORD hashes not compared", "            if got != want:", "            if False:"),
    ("a file added to canre/ accepted", "                  not in listed]",
     "                  and False]"),
    ("a canre imported earlier accepted", "    if mod is not None and not _same(",
     "    if False and not _same("),
    ("the install folder last on sys.path", "    sys.path.insert(0, d)",
     "    sys.path.append(d)"),
    ("a short commit accepted", '    if len(c) != 40 or c.strip("0123456789abcdef"):',
     "    if False:"),
    ("worktree ignores .git", '        if exists(os.path.join(d, ".git")):',
     "        if False:"),
    ("no worktree falls back to a guess",
     "    if wt is None:\n        raise",
     "    if wt is None:\n        wt = os.path.dirname(os.path.abspath(start or HERE))\n"
     "    if False:\n        raise"),
    ("CANRE_DIR ignored", "    if env:\n        if not os.path.isabs",
     "    if False:\n        if not os.path.isabs"),
    ("a relative CANRE_DIR accepted", "        if not os.path.isabs(env):",
     "        if False:"),
    ("install honours CANRE_DIR", "    d = folder(p, start, environ={})",
     "    d = folder(p, start)"),
    ("install takes dependencies", '"--no-deps", ', ""),
    ("install does not check before moving into place",
     "        verify(tmp, p)\n        os.rename", "        os.rename"),
    ("install replaces a folder that is there",
     "    if os.path.isdir(d):\n        try:", "    if False:\n        try:"),
    ("install leaves its partial folder on a failure",
     "    finally:\n        scratch_dir.remove(tmp)",
     "    finally:\n        pass"),
]


def main():
    if "--can-fail" not in sys.argv[1:]:
        got = checks()
        print("\ncanre_pin_test: %s" % ("all checks OK" if not got else
                                        "%d FAILED: %s" % (len(got), "; ".join(got))))
        return 1 if got else 0
    base = scratch_dir.make("canre-pin-mutants")
    src = open(os.path.join(HERE, "canre_pin.py"), encoding="utf-8", newline="").read()
    # The copy's real install (7) is named by CANRE_DIR: the folder the
    # unmutated module resolves here.
    real = canre_pin.folder()
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate([("control: unmutated", "", "")]
                                             + MUTANTS):
            if n == 0:
                d = os.path.join(base, "m0")
                os.makedirs(d)
                shutil.copy(os.path.join(HERE, "pins.json"), d)
                shutil.copy(os.path.join(HERE, "canre_pin.py"), d)
                r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                                   capture_output=True, text=True, timeout=900,
                                   env=dict(os.environ, CANRE_PIN_SRC=d, CANRE_DIR=real))
                ok = r.returncode == 0 and "all checks OK" in r.stdout
                print("%s m0 the unmutated copy passes" % ("ok      " if ok else "FAIL    "))
                if not ok:
                    print(r.stdout[-1500:] + r.stderr[-800:])
                    errors += 1
                continue
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            shutil.copy(os.path.join(HERE, "pins.json"), d)
            open(os.path.join(d, "canre_pin.py"), "w", encoding="utf-8",
                 newline="").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                               capture_output=True, text=True,
                               env=dict(os.environ, CANRE_PIN_SRC=d, CANRE_DIR=real),
                               timeout=900)
            fails = [ln.strip() for ln in r.stdout.splitlines()
                     if ln.strip().startswith("FAIL")]
            dead = bool(fails) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "".join("\n           " + f[:130] for f in fails[:2])))
    finally:
        scratch_dir.remove(base)
    print("\ncanre_pin_test --can-fail: %d of %d killed by a check, %d errors"
          % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
