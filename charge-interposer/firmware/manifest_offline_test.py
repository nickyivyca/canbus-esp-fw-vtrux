"""Offline test for manifest.py's row-identity rule. No PlatformIO, no build.

Modelled on build_lookup_offline_test.py next door, including its lesson:
the rule under test is IMPORTED, never reimplemented, because a test that
re-derives the logic it checks only agrees with itself.

WHY THIS FILE EXISTS. On 2026-10-08 a rebuild of unchanged sources
destroyed the manifest row of the image that was on the board. The row
pairing src_digest 111342c1dd0f with elf_sha256 7f197bfd... -- the image
the board reports in 0x7F7 -- was overwritten and could not be restored,
because builds/ archives no binaries. The cause was manifest.py keying
one row per image NAME while these builds are NOT reproducible, so one
name legitimately covers many different binaries (spec 8.2; measured
three times that day, two clean builds of identical source giving
different ELFs each time).

THE OLD RULE IS REIMPLEMENTED HERE ON PURPOSE, and only here. `case 2`
runs the name-only rule that caused the loss and asserts that it DOES
lose the row. That is what stops this file passing vacuously: without
it, every assertion below would also hold for a merge that silently did
nothing interesting, and a reverted merge_entry would have to be caught
by inspection rather than by the test. With it, reverting manifest.py to
the name-only rule fails case 3 and case 2 keeps documenting why.

    py -3.14 charge-interposer/firmware/manifest_offline_test.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def load_manifest_module():
    """manifest.py with SCons's globals stubbed out.

    It is a PlatformIO extra_script: at import it calls `Import("env")`
    and `env.AddPostAction(...)`, both injected by SCons, so a plain
    `import manifest` raises NameError outside a build. Stubbing them is
    what makes the rule testable at all -- and the rule being untestable
    is why the defect above shipped: it lived inline in a post-action
    that nothing could call.
    """
    class FakeEnv(object):
        def AddPostAction(self, *a, **k):
            pass

        def subst(self, s):
            return s

    ns = {
        "__name__": "manifest_undertest",
        "__file__": os.path.join(HERE, "manifest.py"),
        "env": FakeEnv(),
        "Import": lambda *a, **k: None,
    }
    with open(os.path.join(HERE, "manifest.py")) as f:
        src = f.read()
    exec(compile(src, "manifest.py", "exec"), ns)
    return ns


def name_only_rule(entries, entry):
    """THE OLD RULE, kept only so case 2 can show it failing.

    This is manifest.py's dedupe as it stood until 2026-10-08:

        entries = [e for e in entries if e.get("image") != base]
        entries.append(entry)

    Do not "fix" this function. It is the defect, on purpose.
    """
    out = [e for e in entries if e.get("image") != entry.get("image")]
    out.append(entry)
    return out


NAME = "interposer_111342c1dd0f_witness.bin"
OTHER = "interposer_f28781c9de97_witness.bin"

ELF_BOARD = "7f197bfd0177cf63b2463426fe9cf0739efc68de1a5e2a88492c536c828af9b0"
ELF_REBUILD = "c190275a11119a71621e37d3ca11b19ba4421d6c221c6c32bf467009dcdebb3b"


# Paths exactly as `git status --porcelain` prints them: relative to the
# REPOSITORY ROOT, not to the firmware directory git was pointed at with
# -C. Captured from this repository on 2026-10-08 rather than written
# from the documentation, because that spelling is the whole of what the
# exclusion has to match -- a check built from a guessed spelling would
# agree with itself and with nothing else.
MJ = "charge-interposer/firmware/builds/manifest.json"
SRC = "charge-interposer/firmware/src/machine.cpp"
UNTRACKED = "charge-interposer/firmware/builds/scratch_probe.tmp"
MOVED = "charge-interposer/firmware/src/machine_moved.cpp"

COMMIT = "3eb9a8faf3c8ae98bfb01e79a579e69ced6b1190"
PREFIX = "charge-interposer/firmware/"


class FakeProc(object):
    def __init__(self, out):
        self.returncode = 0
        self.stdout = out
        self.stderr = ""


class FakeSubprocess(object):
    """Enough of subprocess for git_identity, with canned git output.

    git_identity runs three git commands and builds the excluded path
    out of two of them, so testing it needs the commands answered, not
    the logic restated. Stubbing the module the way load_manifest_module
    stubs SCons keeps the function under test the real one.
    """

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def run(self, argv, **kw):
        self.calls.append(tuple(argv[3:]))
        return FakeProc(self.replies[tuple(argv[3:])])


def identity(ns, porcelain):
    saved = ns["subprocess"]
    ns["subprocess"] = FakeSubprocess({
        ("rev-parse", "HEAD"): COMMIT + "\n",
        ("rev-parse", "--show-prefix"): PREFIX + "\n",
        ("status", "--porcelain"): porcelain,
    })
    try:
        return ns["git_identity"](HERE)
    finally:
        ns["subprocess"] = saved


def row(image, elf, when, tag="witness"):
    return {"image": image, "env": "esp32-can-x2-witness", "tag": tag,
            "elf_sha256": elf, "intp_fw_ver16": 4, "built_utc": when}


def elfs(entries, image=NAME):
    return [e.get("elf_sha256") for e in entries if e.get("image") == image]


def main():
    ns = load_manifest_module()
    row_key = ns["row_key"]
    merge_entry = ns["merge_entry"]

    board = row(NAME, ELF_BOARD, "2026-10-06T06:04:15Z")
    rebuild = row(NAME, ELF_REBUILD, "2026-10-08T06:05:49Z")

    print("\nrow identity:")
    check(row_key(board) != row_key(rebuild),
          "two builds of one source are DIFFERENT rows -- the name alone "
          "is not the key")
    check(row_key(board) == row_key(dict(board)),
          "and the same build is the same row whatever object carries it")
    check(row_key({"image": NAME, "elf_sha256": None})
          == row_key({"image": NAME}),
          "a missing hash and an absent one are the same key, so a failed "
          "esptool cannot masquerade as a distinct build")

    print("\ncase 2 -- THE DEFECT, so this file cannot pass vacuously:")
    lost = name_only_rule([board], rebuild)
    check(ELF_BOARD not in elfs(lost),
          "the OLD name-only rule DELETES the flashed image's row "
          "(this is the 2026-10-08 loss, reproduced)")
    check(len(lost) == 1,
          "and leaves exactly one row where two builds happened")

    print("\ncase 3 -- the rule now in manifest.py, same inputs:")
    kept = merge_entry([board], rebuild)
    check(ELF_BOARD in elfs(kept),
          "the flashed image's row SURVIVES a rebuild under its name")
    check(ELF_REBUILD in elfs(kept),
          "and the rebuild is recorded too -- both images exist, so both "
          "are facts")
    check(len(kept) == 2, "two builds, two rows")

    print("\nit must not grow on a repeat of the SAME build:")
    again = merge_entry(kept, dict(rebuild))
    check(len(again) == 2,
          "re-merging an identical-ELF build replaces its row rather than "
          "appending a duplicate")
    check(elfs(again).count(ELF_REBUILD) == 1,
          "and that ELF appears exactly once")

    print("\nordering:")
    shuffled = merge_entry(merge_entry([], rebuild), board)
    when = [e.get("built_utc") for e in shuffled if e.get("image") == NAME]
    check(when == sorted(when),
          "several rows under one name read chronologically whatever order "
          "they were merged in")

    print("\na failed hash collapses among its own, real rows untouched:")
    f1 = row(NAME, None, "2026-10-08T07:00:00Z")
    f2 = row(NAME, None, "2026-10-08T08:00:00Z")
    withfail = merge_entry(merge_entry(again, f1), f2)
    check(len(withfail) == 3,
          "two failed builds leave ONE unhashed row, not two -- failures "
          "do not accumulate")
    check(ELF_BOARD in elfs(withfail) and ELF_REBUILD in elfs(withfail),
          "and neither real row is disturbed by them")

    print("\nother images are not collateral:")
    elsewhere = merge_entry(withfail, row(OTHER, ELF_REBUILD,
                                          "2026-10-08T06:33:18Z"))
    check(len(elfs(elsewhere, OTHER)) == 1,
          "merging a different image name adds its own row")
    check(len(elfs(elsewhere, NAME)) == 3,
          "and touches nothing under the first name, even sharing an ELF")


    print("\nthe dirty flag -- what counts as an uncommitted change:")
    is_dirty = ns["porcelain_is_dirty"]
    check(is_dirty("", MJ) is False,
          "a clean tree is clean")
    check(is_dirty(" M " + MJ + "\n", MJ) is False,
          "the build's OWN write to builds/manifest.json is not dirt -- "
          "this is the whole state the next build sees")
    check(is_dirty("M  " + MJ + "\n", MJ) is False,
          "nor is it when staged rather than merely modified")
    check(is_dirty(" M " + SRC + "\n", MJ) is True,
          "an edited source IS dirt")
    check(is_dirty(" M " + MJ + "\n M " + SRC + "\n", MJ) is True,
          "and it is still dirt alongside the excluded write, so the "
          "exclusion cannot swallow a real edit")
    check(is_dirty("?? " + UNTRACKED + "\n", MJ) is True,
          "an untracked file is dirt")
    check(is_dirty("R  " + SRC + " -> " + MOVED + "\n", MJ) is True,
          "a rename is dirt")
    check(is_dirty("R  " + SRC + " -> " + MJ + "\n", MJ) is True,
          "including a rename whose DESTINATION is the excluded path -- "
          "both sides of the arrow have to be ignorable")

    print("\nthe exclusion is one path, not a pattern:")
    sibling = "charge-interposer/firmware/builds/README.md"
    check(is_dirty(" M " + sibling + "\n", MJ) is True,
          "builds/README.md is not excluded -- builds/ is not the unit")
    check(is_dirty(" M " + MJ + ".bak\n", MJ) is True,
          "and the match is the whole path, not a prefix of it")

    print("\nan unreadable line counts as dirty, never as clean:")
    check(is_dirty('?? "charge-interposer/firmware/builds/odd name"\n', MJ)
          is True,
          "a path git had to quote is some other file, so it is dirt")
    check(is_dirty("xx\n", MJ) is True,
          "and a line that does not parse at all is dirt, because "
          "under-reporting puts a false provenance claim in the manifest")

    print("\nthe excluded path is DERIVED, and that is load-bearing:")
    check(is_dirty(" M " + MJ + "\n", "builds/manifest.json") is True,
          "a firmware-relative spelling matches nothing, so the flag "
          "returns to always-true -- this is what a hardcoded literal "
          "would have done when the tree moved")
    check(ns["porcelain_paths"]("R  " + SRC + " -> " + MOVED)
          == [SRC, MOVED],
          "a rename line yields both of its paths")

    print("\ngit_identity itself, with git's answers canned:")
    clean = identity(ns, " M " + MJ + "\n")
    check(clean == {"git_commit": COMMIT, "git_dirty": False},
          "commit recorded and dirty=false when only the build's own "
          "manifest write is outstanding")
    dirty = identity(ns, " M " + MJ + "\n M " + SRC + "\n")
    check(dirty == {"git_commit": COMMIT, "git_dirty": True},
          "and dirty=true once a source file is edited")

    if failures:
        print("\nmanifest_offline_test: %d FAILURE(S)" % failures)
        return 1
    print("\nmanifest_offline_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
