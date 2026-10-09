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

    py -3.14 projects/vtrux/tools/interposer/firmware/manifest_offline_test.py
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

    if failures:
        print("\nmanifest_offline_test: %d FAILURE(S)" % failures)
        return 1
    print("\nmanifest_offline_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
