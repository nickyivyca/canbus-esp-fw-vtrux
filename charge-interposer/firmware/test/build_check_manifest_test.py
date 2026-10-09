"""Tests the HARNESS: build_check.check_manifest's by-bytes row match,
and check_images_fresh (an image the build did not write is not evidence).

Added by the tester, 2026-10-07, with the change it tests. An image file's
name comes from the source digest, and two clean rebuilds of one digest gave
two different ELFs under the same name -- so "the manifest has a row with
this name" can describe a different binary. check_manifest now reads the ELF
hash out of each image (build_lookup.elf_sha256_of_bin) and requires a row
with that name AND those bytes.

Each case builds a throwaway firmware tree -- a fake image carrying a known
ELF hash in its application descriptor, and a fake builds/manifest.json --
and points build_check at it, so nothing real is read or written.

Run:  py -3.14 projects/vtrux/tools/interposer/firmware/test/build_check_manifest_test.py
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_check as BC

failures = 0

ELF_A = "7f197bfd0177cf63b2463426fe9cf0739efc68de1a5e2a88492c536c828af9b0"
ELF_B = "b1b23f81539e0a83" + "0" * 48
NAME = "interposer_111342c1dd0f_witness.bin"


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def fake_bin(path, elf_hex, magic=0xABCD5432):
    b = bytearray(256)
    b[32:36] = magic.to_bytes(4, "little")
    b[176:208] = bytes.fromhex(elf_hex)
    with open(path, "wb") as f:
        f.write(bytes(b))


def run_case(disk_elf, rows, magic=0xABCD5432):
    """-> problems from check_manifest over a fake tree."""
    root = tempfile.mkdtemp(dir=HERE)
    try:
        os.makedirs(os.path.join(root, "builds"))
        img = os.path.join(root, NAME)
        fake_bin(img, disk_elf, magic)
        with open(os.path.join(root, "builds", "manifest.json"), "w") as f:
            json.dump({"builds": rows}, f)
        saved = (BC.FW, BC.image)
        BC.FW = root
        BC.image = lambda env: img
        try:
            return BC.check_manifest(["esp32-can-x2-witness"])
        finally:
            BC.FW, BC.image = saved
    finally:
        shutil.rmtree(root, ignore_errors=True)


def row(elf, name=NAME, git=True):
    r = {"image": name, "elf_sha256": elf, "env": "esp32-can-x2-witness"}
    if git:                    # spec 8.2 fields, as the build writes them
        r.update(git_commit="c380d61" + "0" * 33, git_dirty=False)
    return r


def main():
    p = run_case(ELF_A, [row(ELF_A)])
    check(not p, "a row with this name and these bytes passes (%s)" % p)

    p = run_case(ELF_A, [row(ELF_B), row(ELF_A)])
    check(not p, "several rows under one name pass when one has these bytes "
                 "(the append-per-ELF manifest) (%s)" % p)

    p = run_case(ELF_A, [row(ELF_B)])
    check(any("describes a different binary" in x for x in p),
          "a row with this NAME but other bytes FAILS -- the hazard of "
          "2026-10-07 -> %s" % p)

    p = run_case(ELF_A, [row(ELF_A, name="interposer_other_witness.bin")])
    check(any("describes a different binary" in x for x in p),
          "these bytes under a different name do not satisfy it -> %s" % p)

    p = run_case(ELF_A, [row(ELF_A)], magic=0x12345678)
    check(any("no readable application descriptor" in x for x in p),
          "an image with no descriptor is a problem, not a pass -> %s" % p)

    # check_images_fresh: an image older than the build's start is not
    # evidence that its environment builds (the reused witness image of
    # 2026-10-07).
    import time
    root = tempfile.mkdtemp(dir=HERE)
    try:
        img = os.path.join(root, NAME)
        fake_bin(img, ELF_A)
        saved = BC.image
        BC.image = lambda env: img
        try:
            old = time.time() - 600
            os.utime(img, (old, old))
            p = BC.check_images_fresh(["esp32-can-x2-witness"], time.time())
            check(any("was not compiled in this run" in x for x in p),
                  "an image older than the build's start FAILS -> %s" % p)
            now = time.time() + 5
            os.utime(img, (now, now))
            p = BC.check_images_fresh(["esp32-can-x2-witness"],
                                      time.time())
            check(not p, "an image written after the start passes (%s)" % p)
        finally:
            BC.image = saved
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # spec 8.2 (2026-10-08): the rows for this build carry the git commit
    # and dirty flag, or an explicit git_error -- never neither
    g = BC.check_git_fields
    check(not g(NAME, [row(ELF_A)]),
          "a row with a commit and git_dirty false passes")
    check(not g(NAME, [{"git_error": "no commit: not a git repository"}]),
          "a row with an explicit git_error passes")
    check(any("spec 8.2" in x for x in g(NAME, [row(ELF_A, git=False)])),
          "a matching row with neither a commit nor a git_error FAILS")
    check(any("git_dirty" in x for x in g(NAME, [{"git_commit": "abc1234",
                                                   "git_dirty": None}])),
          "a row whose git_dirty is not true/false FAILS")
    check(any("no git commit" in x for x in g(NAME, [{"git_commit": "HEAD",
                                                       "git_dirty": True}])),
          "a commit that is not a hex sha FAILS")
    check(not g(NAME, [row(ELF_A, git=False), row(ELF_A)]),
          "an old pre-8.2 row beside this build's complete row passes "
          "(reproducible builds put both on the same bytes)")
    p = run_case(ELF_A, [row(ELF_A, git=False)])
    check(any("spec 8.2" in x for x in p),
          "check_manifest itself fails a build whose only row has no git "
          "fields -> %s" % p)

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\nbuild_check_manifest_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
