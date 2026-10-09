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

Two tests, and the second is the one that matters:

1. THE DETECTOR IS SENSITIVE. source_digest() must change when any
   hashed input changes by one byte, and return when it is restored.
   Run against a scratch copy of the tree, so the real one is untouched.

2. THE CHECK FIRES END TO END. Start a real build and change a hashed
   input while it runs, after build_name.py has fixed the name and
   before manifest.py verifies it. The build must FAIL.

   manifest.py itself is the input it edits, deliberately: it is hashed
   (see DIGEST_INPUTS) but is a post-action that cannot change the image
   bytes, so the test exercises the mismatch without producing a
   genuinely corrupt image. It is also already imported by the time the
   edit lands, so the running build is unaffected by its content.

    py -3.14 projects/vtrux/tools/interposer/firmware/test/digest_recheck_check.py
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, FW)

from build_identity import DIGEST_CHARS, source_digest     # noqa: E402

MARK = "# digest re-check probe, removed by the test\n"
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


def test_sensitivity():
    print("1. the detector is sensitive (on a scratch copy of the tree):")
    tmp = tempfile.mkdtemp(prefix="digest_sens_")
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
        shutil.rmtree(tmp, ignore_errors=True)


def test_end_to_end():
    print("\n2. the check fires end to end (a real build, edited mid-flight):")
    target = os.path.join(FW, "manifest.py")
    orig = None
    state = {}

    # FORCE A LINK, or this test cannot fail.
    #
    # manifest.py's record() is a PlatformIO post-action attached to the
    # .bin target, so it runs only when that target is actually built. The
    # first version of this test edited manifest.py -- which is hashed but
    # not compiled -- against an already-built environment, so SCons had
    # nothing to do, record() never ran, and the test reported exit 0 and
    # called the check broken. The check was fine; the probe never reached
    # it.
    #
    # This is also a real limitation of the re-check and not merely a
    # testing detail: a `pio run` with nothing to do does not re-verify
    # the image already sitting in .pio. build_check.py covers that case,
    # comparing the images on disk against the tree.
    bd = os.path.join(FW, ".pio", "build", "selftest")
    removed = []
    if os.path.isdir(bd):
        for f in os.listdir(bd):
            if f.endswith((".bin", ".elf")):
                try:
                    os.remove(os.path.join(bd, f))
                    removed.append(f)
                except OSError:
                    pass
    print("      removed %d built artifact(s) to force a link: %s"
          % (len(removed), ", ".join(removed) or "none"))

    def edit_soon():
        # After build_name.py has fixed the image name, before the link
        # finishes. The build takes about 8 s here.
        time.sleep(2.5)
        state["orig"] = append_mark(target)
        state["edited"] = True

    t = threading.Thread(target=edit_soon)
    t.daemon = True
    t.start()
    try:
        p = subprocess.run(
            [sys.executable, "-m", "platformio", "run", "-e", "selftest"],
            cwd=FW, capture_output=True, text=True, timeout=600)
        out = (p.stdout or "") + (p.stderr or "")
        t.join(timeout=30)
        orig = state.get("orig")
        check(state.get("edited") is True,
              "the probe edit landed while the build was running")
        check(p.returncode != 0,
              "the build FAILED (exit %d), rather than recording an image "
              "whose name asserts sources it was not built from"
              % p.returncode)
        check("SOURCE DIGEST MISMATCH" in out,
              "and it failed with the digest mismatch, not something else")
        for line in out.splitlines():
            if "SOURCE DIGEST MISMATCH" in line:
                print("      " + line.strip()[:150])
                break
    finally:
        if orig is None:
            orig = state.get("orig")
        if orig is not None:
            restore(target, orig)
            print("      manifest.py restored")

    # And the tree must be back exactly as it was, or this test has left
    # the next build carrying a digest nobody chose.
    d = source_digest(FW)[:DIGEST_CHARS]
    print("      tree digest after restore: %s" % d)
    return d


def main():
    test_sensitivity()
    test_end_to_end()
    print("\n3. a clean rebuild must now succeed again:")
    p = subprocess.run([sys.executable, "-m", "platformio", "run",
                        "-e", "selftest"],
                       cwd=FW, capture_output=True, text=True, timeout=600)
    check(p.returncode == 0,
          "the same build with nothing touching it passes (exit %d)"
          % p.returncode)

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\ndigest_recheck_check: all checks OK -- the re-check is "
          "sensitive, fires, and does not fire spuriously")
    return 0


if __name__ == "__main__":
    sys.exit(main())
