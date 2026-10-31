"""Tests the BUILD SCRIPT: manifest.py refuses to write a manifest row whose
git state is dirty or unreadable (reviewer, 2026-10-15), and the predicate it
rests on, `build_identity.row_is_committable`.

WHY THE RULE EXISTS. On 2026-10-15 a source file was edited while a
six-environment build was running. Five rows landed carrying
`git_dirty: true` against a commit whose tree hashes to neither of the two
digests they named, so a custody lookup reading `1700b071` off a board would
have resolved it to `5df0e62` and been wrong. Those rows, and two older ones
from 2026-10-09, were removed by hand in 0159d53 and f5b45ed. This is the
thing that stops the next set being written at all.

TWO LAYERS, DELIBERATELY, because either on its own can pass while the rule
is broken. The predicate's cases say the DECISION is right. The record()
cases say manifest.py actually ASKS -- a predicate nobody calls is exactly
the check that cannot fail, and it is the shape this project keeps finding
(AGENTS.md, "A discriminator that cannot fail"). Deleting the guard from
manifest.py leaves every predicate case passing and fails the record() ones.

HOW manifest.py IS LOADED. It cannot be imported: `Import("env")` at the top
is a SCons builtin. It is exec'd into a namespace carrying a stub `Import`
and a fake `env` instead -- which is also how the expensive parts are
replaced, since the module's own global lookups resolve through that same
namespace. So `git_identity`, `source_digest`, `elf_sha256_from_esptool`,
`fw_ver16` and `toolchain` are all substituted there: no esptool, no git, no
real source tree. What is NOT substituted is the code under test.

This file is not a build input. `build_identity.EXTRA_INPUTS` names the
build scripts explicitly and the walk covers `firmware/src`, so adding a
test beside the script it tests does not move `src_digest`.

Run:  py -3.14 charge-interposer/firmware/manifest_test.py
"""

import io
import json
import os
import shutil
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from build_identity import row_is_committable          # noqa: E402

MANIFEST_PY = os.path.join(HERE, "manifest.py")

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


# --- removing the throwaway trees ----------------------------------------
#
# Never shutil.rmtree(ignore_errors=True) (AGENTS.md, "Removing scratch").
# Git for Windows marks .git/objects read-only, rmtree cannot delete a
# read-only file on Windows, and ignore_errors then leaves the whole tree
# without a word: 1,085 run folders were found that way on 2026-10-31.

def _long_path(path):
    """Windows: the \\\\?\\ form, so a deep tree is not cut off at MAX_PATH."""
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        path = "\\\\?\\" + path
    return path


def _clear_readonly_and_retry(func, path, exc):
    """rmtree's error handler: clear the read-only flag, retry once. If the
    retry fails too, that error propagates to remove_tree."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path):
    """Remove a throwaway tree, pass or fail; print it if it stays."""
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(_long_path(path), onexc=_clear_readonly_and_retry)
        else:
            shutil.rmtree(_long_path(path),
                          onerror=lambda func, p, ei:
                          _clear_readonly_and_retry(func, p, ei[1]))
    except OSError as e:
        print("LEFTOVER  %s: %s" % (path, e))


# --- the predicate ------------------------------------------------------
#
# Every case is asserted in BOTH directions -- the verdict and, when it is a
# refusal, that a reason came with it. A predicate that returned
# `(False, None)` would leave the build printing a blank explanation.

COMMIT = "cf17a92a0000000000000000000000000000000a"

CASES = [
    ("a clean tree is committable",
     {"git_commit": COMMIT, "git_dirty": False}, True),
    ("a dirty tree is NOT",
     {"git_commit": COMMIT, "git_dirty": True}, False),
    ("an unreadable dirty state is NOT, even with a commit",
     {"git_commit": COMMIT, "git_error": "dirty state unknown: boom"},
     False),
    ("no commit at all is NOT",
     {"git_error": "no commit: not a git repository"}, False),
    ("neither field reported is NOT -- unknown is refused, not assumed",
     {"git_commit": COMMIT}, False),
    ("an empty identity is NOT", {}, False),
]

print("the predicate, build_identity.row_is_committable:")
for what, identity, want in CASES:
    ok, why = row_is_committable(identity)
    check(ok is want, what)
    if not want:
        check(ok is want and isinstance(why, str) and len(why) > 10,
              "   ...and it says why: %r" % (why,))
    else:
        check(why is None, "   ...and a committable row has no reason")

# A dirty tree and an unreadable one must not be refused for the same
# stated reason: the build message is the only thing a reader gets, and
# "uncommitted changes" sent after a git failure would send them looking
# for an edit that is not there.
_, dirty_why = row_is_committable({"git_commit": COMMIT, "git_dirty": True})
_, err_why = row_is_committable({"git_commit": COMMIT,
                                 "git_error": "dirty state unknown: boom"})
check(dirty_why != err_why,
      "a dirty tree and an unreadable one give DIFFERENT reasons")


# --- the wiring: manifest.py's record() ---------------------------------

class FakeEnv(object):
    """Just enough SCons env: the three variables manifest.py substitutes,
    and the AddPostAction its last line calls."""

    def __init__(self, project_dir, pioenv):
        self._v = {
            "$PROJECT_DIR": project_dir,
            "$PIOENV": pioenv,
            "$PROJECT_PACKAGES_DIR": os.path.join(project_dir, "packages"),
        }
        self.post_actions = []

    def subst(self, s):
        return self._v.get(s, s)

    def AddPostAction(self, target, action):
        self.post_actions.append((target, action))


DIGEST = "abcdef012345"
ELF = "38f6efb5" + "c" * 56


def load_manifest(env):
    """exec manifest.py with a stub Import and `env` already bound."""
    with io.open(MANIFEST_PY, encoding="utf-8") as fh:
        src = fh.read()
    ns = {"__name__": "manifest_under_test", "__file__": MANIFEST_PY,
          "Import": lambda *a, **k: None, "env": env}
    exec(compile(src, MANIFEST_PY, "exec"), ns)
    return ns


def run_record(identity):
    """One post-build action against a throwaway tree.

    Returns (rows, stdout_text) -- the manifest's rows, or None if the file
    was never created.
    """
    tmp = tempfile.mkdtemp(prefix="manifest_test_")
    try:
        build_dir = os.path.join(tmp, ".pio", "build", "esp32-can-x2")
        os.makedirs(build_dir)
        bin_path = os.path.join(build_dir,
                                "interposer_%s_bridge.bin" % DIGEST)
        with open(bin_path, "wb") as fh:
            fh.write(b"\xe9not a real image, and nothing here reads it")

        env = FakeEnv(tmp, "esp32-can-x2")
        ns = load_manifest(env)
        check(len(env.post_actions) == 1,
              "   ...manifest.py still registers exactly one post action")

        # Everything that would touch esptool, git or a real source tree.
        ns["source_digest"] = lambda fw_dir: DIGEST + "0" * 20
        ns["elf_sha256_from_esptool"] = lambda p: (ELF, None)
        ns["fw_ver16"] = lambda: 9
        ns["toolchain"] = lambda: {"platform": "test", "framework": "test"}
        ns["git_identity"] = lambda fw_dir: dict(identity)

        out = io.StringIO()
        stdout, sys.stdout = sys.stdout, out
        try:
            ns["record"](None, [bin_path], env)
        finally:
            sys.stdout = stdout

        path = os.path.join(tmp, "builds", "manifest.json")
        if not os.path.exists(path):
            return None, out.getvalue()
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)["builds"], out.getvalue()
    finally:
        remove_tree(tmp)


print()
print("the wiring, manifest.py record():")

# The control FIRST. If this does not write a row the refusals below prove
# nothing -- they would pass against a record() that never writes anything.
rows, out = run_record({"git_commit": COMMIT, "git_dirty": False})
check(rows is not None and len(rows) == 1,
      "a clean tree DOES get a row (the control: without this the "
      "refusals below are vacuous)")
if rows:
    r = rows[0]
    check(r.get("git_dirty") is False and r.get("git_commit") == COMMIT,
          "   ...carrying the commit and git_dirty false")
    check(r.get("src_digest") == DIGEST and r.get("build_id_0x7F7") == ELF[:8],
          "   ...and the digest and 0x7F7 id of the image it was given")

for what, identity in [
        ("a dirty tree", {"git_commit": COMMIT, "git_dirty": True}),
        ("an unreadable dirty state",
         {"git_commit": COMMIT, "git_error": "dirty state unknown: boom"}),
        ("no commit at all",
         {"git_error": "no commit: not a git repository"}),
        ("neither field reported", {"git_commit": COMMIT})]:
    rows, out = run_record(identity)
    check(rows is None, "%s gets NO row, and no manifest is created" % what)
    check("NO MANIFEST ROW" in out,
          "   ...and the build says so rather than failing silently")

# The refusal must not be a build failure. record() returning is the
# mechanism; a raise here would stop a local build that is allowed.
try:
    run_record({"git_commit": COMMIT, "git_dirty": True})
    raised = False
except BaseException:            # SystemExit included, which is the risk
    raised = True
check(not raised, "a refused row does NOT fail the build")

# And the digest guard it sits beside still RAISES, so the two severities
# have not been flattened into one. Same rig, but the tree hashes to
# something other than the name the image carries.
tmp = tempfile.mkdtemp(prefix="manifest_test_")
try:
    build_dir = os.path.join(tmp, ".pio", "build", "esp32-can-x2")
    os.makedirs(build_dir)
    bin_path = os.path.join(build_dir, "interposer_%s_bridge.bin" % DIGEST)
    with open(bin_path, "wb") as fh:
        fh.write(b"\xe9x")
    env = FakeEnv(tmp, "esp32-can-x2")
    ns = load_manifest(env)
    ns["source_digest"] = lambda fw_dir: "ffffffffffff" + "0" * 20
    ns["elf_sha256_from_esptool"] = lambda p: (ELF, None)
    ns["fw_ver16"] = lambda: 9
    ns["toolchain"] = lambda: {}
    ns["git_identity"] = lambda fw_dir: {"git_commit": COMMIT,
                                         "git_dirty": False}
    try:
        ns["record"](None, [bin_path], env)
        digest_raised = False
    except SystemExit:
        digest_raised = True
    check(digest_raised,
          "a SOURCE DIGEST MISMATCH still RAISES -- the two severities are "
          "not flattened: that image must not reach a flash command")
finally:
    remove_tree(tmp)


# --- the cleanup itself ------------------------------------------------
#
# A tree laid out as Git for Windows leaves one: a read-only file under
# .git/objects. The precondition is asserted first, or a fixture the OS
# let us write would make the removal check pass without testing anything.

print()
print("the cleanup, remove_tree():")
tmp = tempfile.mkdtemp(prefix="manifest_test_")
obj_dir = os.path.join(tmp, ".git", "objects", "ab")
os.makedirs(obj_dir)
obj = os.path.join(obj_dir, "cdef0123456789")
with open(obj, "wb") as fh:
    fh.write(b"x")
os.chmod(obj, stat.S_IREAD)
check(not os.access(obj, os.W_OK),
      "the fixture's .git object file IS read-only (the precondition)")
remove_tree(tmp)
check(not os.path.exists(tmp),
      "remove_tree removes a tree holding a read-only .git object")

print()
print("manifest_test: %s" % ("all checks OK" if not failures
                             else "%d failure(s)" % failures))
sys.exit(1 if failures else 0)
