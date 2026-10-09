"""Test of check_sources_older_than_image() in build_output.py.

Row 22: no tracked source may be newer than the image. Since 2026-10-09 a
newer file whose content is byte-identical to the commit the image names (a
branch switch away and back) reads NO REFERENCE instead of FAIL, and a newer
file with different content still FAILS -- the reviewer's condition for the
change was that a genuinely edited source keeps failing.

Each case builds a throwaway git repo with one commit, an image whose
esp_app_desc_t names that commit, and sets mtimes. The last step swaps in a
content comparison that always says "same", then one that always says
"different", and requires each to get a case wrong, so the comparison is what
the verdicts rest on.

Run:  python test/build_checks/test_sources_row.py   (exit 0 = all pass)
"""
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_output as B                                        # noqa: E402

TMP = os.path.join(HERE, "_test_sources_row_tmp")
ROW = "sources older than the image"


def git(*a):
    return subprocess.run(("git", "-C", TMP) + a, capture_output=True, text=True,
                          check=True).stdout.strip()


def image(path, ver):
    desc = bytearray(256)
    struct.pack_into("<I", desc, 0, B.APP_DESC_MAGIC)
    v = ver.encode("ascii")
    desc[0x10:0x10 + len(v)] = v
    desc[0x30:0x30 + 8] = b"wican-fw"
    with open(path, "wb") as fh:
        fh.write(bytes(B.APP_DESC_OFF) + bytes(desc))


def setup():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(os.path.join(TMP, "main"))
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("config", "core.autocrlf", "false")
    for n in ("a.c", "b.c"):
        with open(os.path.join(TMP, "main", n), "w", newline="\n") as fh:
            fh.write("int %s = 1;\n" % n[0])
    git("add", ".")
    git("commit", "-q", "-m", "x")
    return git("rev-parse", "--short=7", "HEAD")


def run_case(ver_of, newer, edit):
    sha = setup()
    img = os.path.join(TMP, "img.bin")
    image(img, ver_of(sha))
    t_img = os.path.getmtime(img)
    for n in ("a.c", "b.c"):
        p = os.path.join(TMP, "main", n)
        if n in edit:
            with open(p, "a", newline="\n") as fh:
                fh.write("int edited;\n")
        t = t_img + 100 if n in newer else t_img - 100
        os.utime(p, (t, t))
    del B.rows[:]
    B.check_sources_older_than_image(img)
    hits = [r for r in B.rows if r[0] == ROW]
    assert len(hits) == 1, B.rows
    _n, ok, detail, ref = hits[0]
    return ("NO REF" if ok is None or ref else ("ok" if ok else "FAIL")), detail


CASES = [
    ("all older -> ok", lambda s: s, (), (), "ok"),
    ("newer, same content (branch switch) -> NO REF", lambda s: s, ("a.c",), (), "NO REF"),
    ("newer, edited -> FAIL", lambda s: s, ("a.c",), ("a.c",), "FAIL"),
    ("one same, one edited -> FAIL", lambda s: s, ("a.c", "b.c"), ("b.c",), "FAIL"),
    ("image -dirty, same content -> FAIL", lambda s: s + "-dirty", ("a.c",), (), "FAIL"),
    ("image names no commit here -> FAIL", lambda s: "1234567", ("a.c",), (), "FAIL"),
]


def run_all(verbose):
    bad = []
    for label, ver_of, newer, edit, want in CASES:
        got, detail = run_case(ver_of, newer, edit)
        if got != want:
            bad.append(label)
        if verbose:
            print("  %-4s %-48s got %-6s %s" % ("ok" if got == want else "FAIL", label, got,
                                               detail[:90]))
    return bad


def main():
    repo = B.REPO
    B.REPO = TMP
    failures = []
    real = B._blob_same
    try:
        failures += run_all(True)
        for name, fake in (("always same", lambda rel, ver: True),
                           ("always different", lambda rel, ver: False)):
            B._blob_same = fake
            caught = run_all(False)
            B._blob_same = real
            print("  %-4s comparison '%s' gets %d case(s) wrong"
                  % ("ok" if caught else "FAIL", name, len(caught)))
            if not caught:
                failures.append("mutant %s not caught" % name)
    finally:
        B._blob_same = real
        B.REPO = repo
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n=== %s ===" % ("PASS" if not failures else "FAIL: %s" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
