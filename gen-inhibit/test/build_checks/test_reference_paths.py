"""Test of where build_output.py gets the diag DBC and the schema doc.

Since 2026-10-24 (port plan toolkit item 10; the gen-inhibit reviewer's build message
of that day) both are in this repo, gen-inhibit/diag/, and build_output.py reads them
from GIT: HEAD's copies of --ref-repo (default this repo), never the working tree and
never $REVERSE_IT_DIR's frozen copies. A working-tree copy with uncommitted changes is
refused as a FAIL row; a repo without them is NO REFERENCE, naming --ref-repo; a DBC
history that fails gi_diag's checks is a FAIL. The emitted-frames row decodes the
goldens through gi_diag's schema set from the same refs, and is NO REFERENCE without it.

Each case imports build_output.py afresh in a subprocess, with REVERSE_IT_DIR pointing
at a fake root holding a different DBC, so a read of the old path would show. The fake
repositories are made in this run's scratch/ folder (AGENTS.md "Files and folders").

Run:  python gen-inhibit/test/build_checks/test_reference_paths.py   (exit 0 = all pass)
      python gen-inhibit/test/build_checks/test_reference_paths.py --target <copy of build_output.py>
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))   # the project folder, gen-inhibit/ since port step 0d
WORKTREE = os.path.dirname(REPO)                         # the repo root, with scratch/
FIRMWARE = os.path.join(REPO, "firmware")                # the WiCAN tree, build_output's REPO
FAILS = []
DBC, DOC = "gen-inhibit/diag/vtrux-wican-diag.dbc", "gen-inhibit/diag/wican_diag_schema.md"
ROWS = ("diag references from git", "DBC defines every diag page", "emitted diag frames decode",
        "abort reasons are all in the DBC", "schema doc version matches",
        "DIAG_FW_VERSION is the schema doc's latest rev")

PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import build_output as B
B.REPO = os.environ["REF_TEST_REPO"]      # this repo's WiCAN tree, also for a copy under test (--target)
B.WORKTREE = os.environ["REF_TEST_WORKTREE"]    # and its root, where scratch/ is
del B.rows[:]
dbc, doc = B.refs_from_git(sys.argv[2])
B.check_diag(dbc)
B.check_schema_doc(doc)
B.check_fw_version(doc)
B.check_abort_reason_parity(dbc)
extra = []
if len(sys.argv) > 3:
    del B.rows[:]
    B.check_diag(sys.argv[3])
    extra = [[r[0], r[1], r[2], r[3]] for r in B.rows]
print(json.dumps({"dbc": dbc, "doc": doc, "has_default": hasattr(B, "DEFAULT_DBC") or hasattr(B, "DEFAULT_SCHEMA_DOC"),
                  "dbc_bytes": open(dbc, "rb").read().hex() if dbc else None,
                  "rows": [[r[0], r[1], r[2], r[3]] for r in B.rows] if not extra else extra}))
"""


# records_dir() reads REVERSE_IT_DIR for the release records (shared/tools R13); no other
# code in build_output.py may, since the DBC and the schema doc come from git.
RECORDS_DEF = re.compile(r"\ndef records_dir\(\):\n(?:(?:[ \t]+[^\n]*)?\n)*")
OLD_PATH = re.compile(r'"projects", "vtrux"|projects/vtrux/vtrux-wican-diag|REVERSE_IT_DIR"')


def reverse_it_paths(src):
    """The old reverse-it DBC and doc defaults left in `src`, outside records_dir()."""
    return [m.group(0) for m in OLD_PATH.finditer(RECORDS_DEF.sub("\n", src))]


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "core.autocrlf=false"] + list(a), check=True, capture_output=True).stdout


def remove_tree(d):
    """rmtree that also removes read-only files: on Windows git's objects are read-only, and
    ignore_errors=True left them behind, so a later run given the same PID found the folder
    there and crashed in makedirs (2026-10-25: 101 leftovers, a crashed run every few)."""
    def writable(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if os.path.exists(d):
        shutil.rmtree(d, onexc=writable)


def scratch():
    d = os.path.join(WORKTREE, "scratch", "build-checks-tests", "reference_paths_%d" % os.getpid())
    remove_tree(d)
    os.makedirs(d)
    return d


def fake_rit(base):
    """A fake reverse-it root whose projects/vtrux/ holds a DBC and doc unlike the repo's."""
    root = os.path.join(base, "reverse it")
    os.makedirs(os.path.join(root, "projects", "vtrux", "notes", "artifacts", "gen-inhibit"))
    open(os.path.join(root, "projects", "vtrux", "vtrux-wican-diag.dbc"), "w").write('VERSION "fake"\n')
    open(os.path.join(root, "projects", "vtrux", "notes", "artifacts", "gen-inhibit", "wican_diag_schema.md"),
         "w").write("fake\n")
    return root


def fake_repo(base, name, texts):
    """A git repo holding gen-inhibit/diag/: texts is [(DBC text, doc text)], one commit each."""
    d = os.path.join(base, name)
    os.makedirs(os.path.join(d, "gen-inhibit", "diag"))
    git(d, "init", "-q")
    for k, (dbc, doc) in enumerate(texts):
        open(os.path.join(d, DBC), "w", encoding="utf-8", newline="").write(dbc)
        open(os.path.join(d, DOC), "w", encoding="utf-8", newline="").write(doc)
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "c%d" % k)
    return d


def probe(target_dir, ref_repo, rit, extra=None):
    env = dict(os.environ, REVERSE_IT_DIR=rit, REF_TEST_REPO=FIRMWARE, REF_TEST_WORKTREE=WORKTREE)
    r = subprocess.run([sys.executable, "-B", "-c", PROBE, target_dir, ref_repo] + ([extra] if extra else []),
                       capture_output=True, text=True, env=env, cwd=target_dir, timeout=300)
    if r.returncode != 0:
        return {"crash": (r.stdout + r.stderr)[-600:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def row(res, name):
    for r in res.get("rows", []):
        if r[0] == name:
            return r
    return None


def main():
    target = os.path.join(HERE, "build_output.py")
    if "--target" in sys.argv[1:]:
        target = os.path.abspath(sys.argv[sys.argv.index("--target") + 1])
    tdir = os.path.dirname(target)
    base = scratch()
    rit = fake_rit(base)
    real_dbc = git(REPO, "show", "9c54669:%s" % DBC).decode("utf-8")
    real_doc = git(REPO, "show", "9c54669:%s" % DOC).decode("utf-8")

    # 1. This repo: HEAD's copies, read from git, whatever REVERSE_IT_DIR says. Given the
    # project folder (gen-inhibit/, port step 0d), which refs_from_git takes to the root.
    res = probe(tdir, REPO, rit)
    check("this repo: the module imports and runs the rows", "crash" not in res, res.get("crash"))
    check("this repo: no REVERSE_IT_DIR default is left (DEFAULT_DBC, DEFAULT_SCHEMA_DOC)",
          res.get("has_default") is False, res.get("has_default"))
    r = row(res, "diag references from git")
    check("this repo: 'diag references from git' passes, naming HEAD and the DBC's commit and hash",
          r is not None and r[1] is True and "schema" in r[2] and "hash" in r[2], r)
    head_dbc = git(REPO, "show", "HEAD:%s" % DBC)
    check("this repo: the DBC the rows read is HEAD's bytes, not the fake root's",
          res.get("dbc_bytes") == head_dbc.hex(), (res.get("dbc") or "")[-80:])
    check("this repo: the copies are written under this repo's scratch/",
          all(os.path.normcase(p or "").startswith(os.path.normcase(os.path.join(WORKTREE, "scratch")))
              for p in (res.get("dbc"), res.get("doc"))), (res.get("dbc"), res.get("doc")))
    for name in ROWS[1:]:
        r = row(res, name)
        check("this repo: '%s' passes" % name, r is not None and r[1] is True, r)

    # 2. A working-tree change is refused, and is never what is read.
    d = fake_repo(base, "dirty_dbc", [(real_dbc, real_doc)])
    open(os.path.join(d, DBC), "a", encoding="utf-8").write("\n")
    res = probe(tdir, d, rit)
    r = row(res, "diag references from git")
    check("a modified DBC: 'diag references from git' FAILS, uncommitted", r is not None and r[1] is False
          and "uncommitted" in r[2], r)
    # With no DBC, check_diag stops at its "every diag ID" row, before the page rows.
    for name in ("DBC defines every diag ID",) + ROWS[3:]:
        r = row(res, name)
        check("a modified DBC: '%s' is NO REFERENCE, not a pass" % name, r is not None and r[1] is None and r[3], r)
    check("a modified DBC: no DBC row passes",
          not [r for r in res.get("rows", []) if r[1] is True and r[0] != "diag schema version readable"
               and r[0] != "diag IDs readable"], [r[0] for r in res.get("rows", []) if r[1] is True])
    d = fake_repo(base, "dirty_doc", [(real_dbc, real_doc)])
    open(os.path.join(d, DOC), "a", encoding="utf-8").write("x\n")
    r = row(probe(tdir, d, rit), "diag references from git")
    check("a modified schema doc: FAILS, uncommitted", r is not None and r[1] is False and "uncommitted" in r[2], r)
    d = fake_repo(base, "staged", [(real_dbc, real_doc)])
    open(os.path.join(d, DBC), "a", encoding="utf-8").write("\n")
    git(d, "add", DBC)
    r = row(probe(tdir, d, rit), "diag references from git")
    check("a staged DBC: FAILS, uncommitted", r is not None and r[1] is False and "uncommitted" in r[2], r)
    d = fake_repo(base, "clean", [(real_dbc, real_doc)])
    r = row(probe(tdir, d, rit), "diag references from git")
    check("a clean fake repo: passes", r is not None and r[1] is True, r)
    # A working-tree change git status does not show (assume-unchanged): what is read must
    # still be HEAD's bytes, which only a read from git gives.
    d = fake_repo(base, "hidden", [(real_dbc, real_doc)])
    git(d, "update-index", "--assume-unchanged", DBC)
    open(os.path.join(d, DBC), "a", encoding="utf-8").write('CM_ "hidden working-tree edit";\n')
    res = probe(tdir, d, rit)
    check("a hidden working-tree edit: the DBC read is HEAD's bytes, not the working tree's",
          res.get("dbc_bytes") == git(d, "show", "HEAD:%s" % DBC).hex(), (res.get("crash") or "")[-200:])

    # 3. Not there.
    d = os.path.join(base, "no_diag")
    os.makedirs(d)
    git(d, "init", "-q")
    open(os.path.join(d, "x"), "w").write("x")
    git(d, "add", "x")
    git(d, "commit", "-q", "-m", "x")
    res = probe(tdir, d, rit)
    r = row(res, "diag references from git")
    check("a repo without gen-inhibit/diag/: NO REFERENCE naming --ref-repo",
          r is not None and r[1] is None and r[3] and "--ref-repo" in r[2], r)
    r = row(res, "abort reasons are all in the DBC")
    check("... and the DBC rows NO REFERENCE too", r is not None and r[1] is None and r[3], r)
    d = os.path.join(base, "not_git")
    os.makedirs(d)
    r = row(probe(tdir, d, rit), "diag references from git")
    check("a folder that is not a git worktree: NO REFERENCE", r is not None and r[1] is None and r[3], r)

    # 4. A history gi_diag refuses.
    other = real_dbc.replace(" SG_ diag_ctr_bad : 40|8@1+", " SG_ diag_ctr_bad : 40|4@1+")
    d = fake_repo(base, "stale_hash", [(other, real_doc)])
    r = row(probe(tdir, d, rit), "diag references from git")
    check("a DBC whose DiagSchemaHash is not its own: FAILS", r is not None and r[1] is False and "layout_hash" in r[2], r)

    # 5. The emitted-frames row only decodes with the schema set it read.
    loose = os.path.join(base, "loose.dbc")
    open(loose, "w", encoding="utf-8", newline="").write(real_dbc)
    res = probe(tdir, REPO, rit, extra=loose)
    r = row(res, "emitted diag frames decode")
    check("check_diag on a DBC file refs_from_git did not write: the emitted-frames row is NO REFERENCE",
          r is not None and r[1] is None and r[3], r)

    src = open(target, encoding="utf-8").read()
    bad = [m.group(0) for m in re.finditer(r"seadrive_root|nickivyc|My Libraries|NotGit|Seafile|C:[\\/]+Users", src)]
    check("no machine-specific path in build_output.py", not bad, bad)
    old = reverse_it_paths(src)
    check("build_output.py no longer builds a reverse-it path to the DBC or the doc", not old, old)
    # The check itself: records_dir() may read REVERSE_IT_DIR (the release records, shared/tools
    # R13, since a9ac0ff); the old DBC default, anywhere else, still fails it.
    old_default = '\ndef _ref_root():\n    root = os.environ.get("REVERSE_IT_DIR", "").strip()\n'
    check("the check: a REVERSE_IT_DIR read outside records_dir() fails it",
          reverse_it_paths(src + old_default) == ['REVERSE_IT_DIR"'], reverse_it_paths(src + old_default))
    check("the check: the old projects/vtrux join fails it",
          reverse_it_paths('x = os.path.join(root, "projects", "vtrux")\n') == ['"projects", "vtrux"'], "")
    check("the check: build_output.py does read REVERSE_IT_DIR, inside records_dir()",
          'REVERSE_IT_DIR"' in src and 'REVERSE_IT_DIR"' in RECORDS_DEF.search(src).group(0), "")

    # remove_tree itself: a folder holding a read-only file, as git leaves its objects.
    left = os.path.join(base, "leftover", "objects")
    os.makedirs(left)
    ro = os.path.join(left, "ab")
    open(ro, "w").close()
    os.chmod(ro, stat.S_IREAD)
    remove_tree(os.path.dirname(left))
    check("remove_tree removes a folder holding a read-only file (git's objects on Windows)",
          not os.path.exists(os.path.dirname(left)), "left behind")

    remove_tree(base)
    print("%d FAIL" % len(FAILS) if FAILS else "all ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
