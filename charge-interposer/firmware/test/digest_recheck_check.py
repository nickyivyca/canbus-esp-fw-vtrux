"""Does the post-link source-digest re-check actually FAIL when it should?

Review-shaped question, asked 2026-10-05 by the generator-inhibit session:
is the digest computed at configure time (and therefore reusable while
stale) or at build time, and does anything re-check it against the tree
after the link?

Answer for this project: build time, recomputed on every `pio run`, and
since 2026-10-05 re-checked after the link by manifest.py. Before that
the only verification was `digest_of(basename)`, which reads the digest
back out of the filename the pre-action wrote -- a comparison of the name
with itself, which could not fail. This script is what stops the
replacement having the same property.

The rule it tests (reviewer, 2026-10-31): until port step 2a the image
file name carries the source digest (spec section 12, "Specified
2026-10-25", 8.2 build identity), and the source digest identifies the
product source for the emulated suite (8.2). A stale digest in the name
breaks both, and the post-link re-check is what guards it. This script
retires with the PlatformIO build, and manifest.py, at 2a.

Two tests, and the second is the one that matters:

1. THE DETECTOR IS SENSITIVE. source_digest() must change when any
   hashed input changes by one byte, and return when it is restored.
   Run against a scratch copy of the tree, so the real one is untouched.

2. THE CHECK FIRES END TO END. Start a real build and change a hashed
   input while it runs, after the image name is fixed and before
   manifest.py verifies it. The build must FAIL.

   manifest.py itself is the input it edits, deliberately: it is hashed
   (see DIGEST_INPUTS) but is a post-action that cannot change the image
   bytes, so the test exercises the mismatch without producing a
   genuinely corrupt image. It is also already imported by the time the
   edit lands, so the running build is unaffected by its content.

   The edit lands when PlatformIO prints the "Linking <image>.elf" line
   (2026-10-31). By then the name is fixed, and the re-check runs after
   the link. Until then it landed 2.5 s after the start, which on
   NICKY-XPS was before the name was fixed: the image was named with
   the edited tree's digest, the name was right, nothing was stale, and
   the build passed, which this test reported as the product failing.
   So a premise is checked first: the Linking line's image name carries
   the digest the tree had before the edit, and the edit moved it. If
   not, the probe never put a stale digest in a name, and the result is
   NOT EXERCISED (exit 3), not a FAIL of the product.

    py -3.14 -B charge-interposer/firmware/test/digest_recheck_check.py
    py -3.14 -B charge-interposer/firmware/test/digest_recheck_check.py --can-fail

--can-fail runs test 2 unmutated and then three ways that must not pass
(each a real build): the edit made before the build starts (too early:
NOT EXERCISED), the edit taken out again before the re-check runs (the
re-check sees a clean tree: FAIL), and the build left with nothing to
link (no link: NOT EXERCISED). It then requires an untouched build to
pass.
"""

import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, FW)
# charge-interposer/, for scratch_dir; last, so it shadows nothing
sys.path.append(os.path.dirname(os.path.dirname(HERE)))
import scratch_dir                                  # noqa: E402

from build_identity import DIGEST_CHARS, source_digest     # noqa: E402

MARK = "# digest re-check probe, removed by the test\n"
LINK = re.compile(r"^Linking (\S+)")
# printed after the link and before the .bin post-action (manifest.py)
SIZED = re.compile(r"^(Retrieving maximum program size|Checking size) ")
PASS, FAIL, NOT_EXERCISED = "PASS", "FAIL", "NOT EXERCISED"
failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def append_mark(path):
    """Append MARK, PRESERVING the file's existing line endings.

    Read and written in BINARY. A text-mode read plus a write with
    newline="\\n" rewrites every line ending in the file, which on
    2026-10-05 silently converted machine.h's 594 CRLF endings to LF:
    the C++ was unaffected, the build succeeded, and the source digest
    moved. Restoring CRLF restored the digest exactly, which is how the
    cause was found. A script that edits hashed inputs must not be the
    thing that changes them.
    """
    with open(path, "rb") as f:
        b = f.read()
    crlf = b.count(b"\r\n") > b.count(b"\n") - b.count(b"\r\n")
    add = MARK.replace("\n", "\r\n") if crlf else MARK
    with open(path, "wb") as f:
        f.write(b + add.encode("ascii"))
    return b


def restore(path, original_bytes):
    with open(path, "wb") as f:
        f.write(original_bytes)


def digest():
    return source_digest(FW)[:DIGEST_CHARS]


def test_sensitivity():
    print("1. the detector is sensitive (on a scratch copy of the tree):")
    tmp = scratch_dir.make("digest-recheck-check")
    try:
        dst = os.path.join(tmp, "firmware")
        shutil.copytree(FW, dst, ignore=shutil.ignore_patterns(
            ".pio", "builds", "__pycache__", "test"))
        base = source_digest(dst)[:DIGEST_CHARS]
        check(len(base) == DIGEST_CHARS,
              "a digest is produced for the scratch tree (%s)" % base)

        for rel in ("src/machine.h", "platformio.ini", "manifest.py"):
            p = os.path.join(dst, rel)
            if not os.path.exists(p):
                check(False, "%s missing from the scratch tree" % rel)
                continue
            orig = append_mark(p)
            moved = source_digest(dst)[:DIGEST_CHARS]
            restore(p, orig)
            back = source_digest(dst)[:DIGEST_CHARS]
            check(moved != base and back == base,
                  "one appended line to %s moves the digest (%s -> %s) and "
                  "removing it returns it" % (rel, base, moved))

        # A file that is NOT hashed must not move it -- otherwise the
        # digest churns on every comment edit and nobody trusts it.
        notes = os.path.join(dst, "README.md")
        if os.path.exists(notes):
            orig = append_mark(notes)
            same = source_digest(dst)[:DIGEST_CHARS]
            restore(notes, orig)
            check(same == base,
                  "but editing README.md does NOT move it, so the digest "
                  "covers what it claims and nothing else")
    finally:
        scratch_dir.remove(tmp)


def force_link():
    """Delete the selftest environment's .bin/.elf, so the build links.

    FORCE A LINK, or this test cannot fail. manifest.py's record() is a
    PlatformIO post-action attached to the .bin target, so it runs only
    when that target is actually built. The first version of this test
    edited manifest.py -- which is hashed but not compiled -- against an
    already-built environment, so SCons had nothing to do, record() never
    ran, and the test reported exit 0 and called the check broken. The
    check was fine; the probe never reached it.

    This is also a real limitation of the re-check and not merely a
    testing detail: a `pio run` with nothing to do does not re-verify the
    image already sitting in .pio. build_check.py covers that case,
    comparing the images on disk against the tree.
    """
    bd = os.path.join(FW, ".pio", "build", "selftest")
    removed = []
    if os.path.isdir(bd):
        for f in sorted(os.listdir(bd)):
            if f.endswith((".bin", ".elf")):
                os.remove(os.path.join(bd, f))
                removed.append(f)
    print("      removed %d built artifact(s) to force a link: %s"
          % (len(removed), ", ".join(removed) or "none"))


def build_probed(probe="link", link=True):
    """One selftest build, with MARK appended to manifest.py:

    "link"   when the "Linking" line appears (the test);
    "before" before the build starts (a can-fail: too early);
    "undo"   at the "Linking" line, and taken out again when the size
             check after the link is printed, before the re-check runs
             (a can-fail: the re-check sees a clean tree).

    link=False keeps the built image, so the build has nothing to link
    (a can-fail). manifest.py is restored in a finally. Returns what
    judge() reads."""
    target = os.path.join(FW, "manifest.py")
    rec = {"probe": probe, "d0": digest(), "d1": None, "edited": False,
           "linked": None, "rc": None, "out": ""}
    orig = None

    def edit():
        nonlocal orig
        orig = append_mark(target)
        rec["edited"] = True
        rec["d1"] = digest()

    if link:
        force_link()
    t0 = time.time()
    lines = []
    try:
        if probe == "before":
            edit()
        p = subprocess.Popen(
            [sys.executable, "-m", "platformio", "run", "-e", "selftest"],
            cwd=FW, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1)
        try:
            for line in p.stdout:
                lines.append(line)
                m = LINK.match(line)
                if m and rec["linked"] is None:
                    rec["linked"] = os.path.basename(m.group(1))
                    print("      %5.1f s  %s" % (time.time() - t0,
                                                 line.strip()[:120]))
                    if probe in ("link", "undo") and orig is None:
                        edit()
                        print("      %5.1f s  probe line appended to "
                              "manifest.py (digest %s -> %s)"
                              % (time.time() - t0, rec["d0"], rec["d1"]))
                elif (probe == "undo" and orig is not None
                      and SIZED.match(line)):
                    restore(target, orig)
                    orig = None
                    print("      %5.1f s  probe line taken out again, "
                          "before the re-check" % (time.time() - t0))
            rec["rc"] = p.wait(timeout=600)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
    finally:
        if orig is not None:
            restore(target, orig)
            print("      manifest.py restored")
    rec["out"] = "".join(lines)
    return rec


def judge(rec):
    """(verdict, reasons). NOT EXERCISED when the premise fails: no edit
    landed, it did not move the digest, nothing linked, or the linked
    image is not named with the pre-edit digest (so the name was fixed
    after the edit and is not stale). Otherwise PASS only if the build
    failed with SOURCE DIGEST MISMATCH."""
    d0, d1, name = rec["d0"], rec["d1"], rec["linked"]
    premise = []
    if not rec["edited"]:
        premise.append("the probe edit never landed")
    elif d1 == d0:
        premise.append("the probe edit did not move the digest (%s)" % d0)
    if name is None:
        premise.append("no Linking line: the build linked nothing, so the "
                       "post-link re-check never ran")
    elif "_%s_" % d0 not in name:
        premise.append("the linked image %s is not named with the pre-edit "
                       "digest %s: the probe landed before the name was "
                       "fixed, so no name was stale" % (name, d0))
    if premise:
        return NOT_EXERCISED, premise
    bad = []
    if rec["rc"] == 0:
        bad.append("the build passed (exit 0), recording an image whose name "
                   "asserts sources it was not built from")
    if "SOURCE DIGEST MISMATCH" not in rec["out"]:
        bad.append("no SOURCE DIGEST MISMATCH in the build output")
    return (FAIL, bad) if bad else (PASS, [])


def report(rec):
    verdict, reasons = judge(rec)
    for r in reasons:
        print("      " + r)
    for line in rec["out"].splitlines():
        if "SOURCE DIGEST MISMATCH" in line:
            print("      " + line.strip()[:150])
            break
    return verdict


def test_end_to_end():
    print("\n2. the check fires end to end (a real build, edited mid-flight):")
    rec = build_probed("link")
    verdict = report(rec)
    if verdict == NOT_EXERCISED:
        print("NOT EXERCISED  the premise failed, so this says nothing "
              "about the re-check")
    else:
        check(verdict == PASS,
              "the build FAILED (exit %s) with SOURCE DIGEST MISMATCH, "
              "rather than recording an image whose name (%s) asserts "
              "sources it was not built from" % (rec["rc"], rec["linked"]))
    # And the tree must be back exactly as it was, or this test has left
    # the next build carrying a digest nobody chose.
    d = digest()
    check(d == rec["d0"], "the tree's digest is back to %s after the restore "
          "(%s)" % (rec["d0"], d))
    return verdict


def clean_build():
    print("\n3. a clean rebuild must now succeed again:")
    p = subprocess.run([sys.executable, "-m", "platformio", "run",
                        "-e", "selftest"],
                       cwd=FW, capture_output=True, text=True, timeout=600)
    check(p.returncode == 0,
          "the same build with nothing touching it passes (exit %d)"
          % p.returncode)


# (name, build_probed arguments, the verdict it must get). "undo" runs
# before "nolink", whose build then has nothing to link.
MUTANTS = [
    ("before", dict(probe="before"), NOT_EXERCISED),
    ("undo", dict(probe="undo"), FAIL),
    ("nolink", dict(probe="link", link=False), NOT_EXERCISED),
]


def can_fail():
    print("can-fail: test 2 unmutated, then three ways that must not pass")
    rows = []
    base = test_end_to_end()
    rows.append(("unmutated", PASS, base))
    for name, kw, want in MUTANTS:
        print("\nmutant %s (must read %s):" % (name, want))
        got = report(build_probed(**kw))
        rows.append((name, want, got))
        print("      -> %s" % got)
    clean_build()
    print("\ncan-fail:")
    bad = 0
    for name, want, got in rows:
        ok = want == got
        bad += not ok
        print("  %s  %-9s must read %-13s read %s" % (
            "ok  " if ok else "FAIL", name, want, got))
    print("\n%d of %d as they must be" % (len(rows) - bad, len(rows)))
    return 1 if bad or failures else 0


def main(argv):
    if argv == ["--can-fail"]:
        return can_fail()
    if argv:
        print("usage: digest_recheck_check.py [--can-fail]")
        return 2
    test_sensitivity()
    verdict = test_end_to_end()
    clean_build()
    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    if verdict == NOT_EXERCISED:
        print("\ndigest_recheck_check: NOT EXERCISED -- the probe did not "
              "put a stale digest in an image name")
        return 3
    print("\ndigest_recheck_check: all checks OK -- the re-check is "
          "sensitive, fires, and does not fire spuriously")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
