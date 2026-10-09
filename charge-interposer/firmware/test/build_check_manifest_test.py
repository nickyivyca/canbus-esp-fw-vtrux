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

    # spec 8.2 (a): a build proves it ran by a fresh manifest row
    f = BC.fresh_row_problem
    t0 = BC._utc_seconds("2026-10-08T20:31:13Z") + 0.6
    fresh = {"env": "truck", "elf_sha256": ELF_A,
             "built_utc": "2026-10-08T20:31:13Z"}
    stale = dict(fresh, built_utc="2026-10-08T20:31:12Z")
    check(f([fresh], "truck", ELF_A, t0) is None,
          "a row stamped in the build's starting second passes (the stamp "
          "has no fraction)")
    p = f([stale], "truck", ELF_A, t0)
    check(p is not None and "did not write" in p,
          "a row stamped before the build started FAILS -> %s" % p)
    check(f([], "truck", ELF_A, t0) is not None,
          "no row at all (a cached build records nothing) FAILS")
    check(f([dict(fresh, env="slcan")], "truck", ELF_A, t0) is not None,
          "a fresh row for another environment FAILS")
    check(f([dict(fresh, elf_sha256=ELF_B)], "truck", ELF_A, t0) is not None,
          "a fresh row for other bytes FAILS")
    check(f([stale, fresh], "truck", ELF_A, t0) is None,
          "an old row beside a fresh one passes")
    check(f([dict(fresh, built_utc="yesterday")], "truck", ELF_A, t0)
          is not None, "an unparseable built_utc FAILS")
    p = f([dict(fresh, elf_sha256=None)], "truck", ELF_A, t0)
    check(p is not None and "None" in p and "does not identify" in p,
          "a fresh row whose elf_sha256 is null FAILS as a row that does not "
          "identify the image, not as 'recorded nothing' -> %s" % p)
    p = f([dict(stale, env="truck")], "truck", ELF_A, t0)
    check(p is not None and "did not write" in p,
          "only an older row with the right bytes FAILS as not this build's")

    # spec 8.2 (b): every differing byte run is listed; only the ELF hash
    # at 0xb0 and the trailing 33 bytes may differ
    base = bytes(range(256)) * 4

    def flip(b, spans):
        b = bytearray(b)
        for lo, hi in spans:
            for i in range(lo, hi):
                b[i] ^= 0xFF
        return bytes(b)
    n = len(base)
    check(BC.diff_runs(base, flip(base, [(3, 5), (9, 10)]))
          == [(3, 5), (9, 10)], "diff_runs lists each run with its bounds")
    check(BC.diff_runs(base, flip(base, [(n - 2, n)])) == [(n - 2, n)],
          "diff_runs closes a run that reaches the end")
    other = flip(base, [(0xb0, 0xd0), (n - 33, n)])
    runs, p = BC.cross_host_problems(base, other)
    check(not p and runs == [(0xb0, 0xd0), (n - 33, n)],
          "the measured 3eb9a8f shape (32 B at 0xb0 + 33 B trailer, 65 "
          "bytes) passes and is listed -> %s %s" % (runs, p))
    runs, p = BC.cross_host_problems(base, flip(base, [(0xb4, 0xb6)]))
    check(not p, "a partial difference inside the hash field passes")
    runs, p = BC.cross_host_problems(base, flip(other, [(n - 40, n - 39)]))
    check(len(p) == 1 and "outside" in p[0] and len(runs) == 3,
          "one byte just before the trailer FAILS and is listed -> %s" % p)
    runs, p = BC.cross_host_problems(base, flip(base, [(0xaf, 0xb2)]))
    check(len(p) == 1 and "outside" in p[0],
          "a run straddling the start of the hash field FAILS -> %s" % p)
    runs, p = BC.cross_host_problems(base, flip(base, [(0x20, 0x21)]))
    check(len(p) == 1 and "0x20-0x20" in p[0],
          "a loaded-section byte FAILS, with its offset -> %s" % p)
    runs, p = BC.cross_host_problems(base, base + b"\0")
    check(p and "length" in p[0], "different lengths FAIL")
    runs, p = BC.cross_host_problems(base, base)
    check(p and "IDENTICAL" in p[0],
          "two identical files FAIL (one host twice, or one file twice)")

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\nbuild_check_manifest_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
