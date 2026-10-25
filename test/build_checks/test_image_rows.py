"""Test of build_output.py's image rows after port step 0c (spec 12.3 item 3).

Spec 12.3 item 3 (port step 0c, user, 2026-10-25): the app descriptor's
version and `project_name` are labels; nothing checks them against the tree
(shared/tools R6), and the image is named by its image hash. So "no -dirty"
and "GIT_SHA embedded" are gone from the build-output list, and with them
(reviewer, 2026-10-25, Q1) "working tree clean", "image built from HEAD" and
"sources older than the image". `project_name` is a fixed string with no
commit in it; the project-name row (reviewer, Q2) fails a run of 7 or more
hex digits that resolves to a commit of this repo, and keeps the "wican-fw"
prefix.

Each case writes an image with an app descriptor into a scratch folder and
runs check_image() on it, classifying rows the way build_output.main() does.
Against the code before 0c (564c129) the "-dirty" and commit-in-name cases
fail, so the test can fail.

Run:  py -3.14 test/build_checks/test_image_rows.py   (exit 0 = all pass)
"""
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_output as B                                        # noqa: E402

TMP = os.path.join(B.REPO, "scratch", "build-checks", "test_image_rows")
RETIRED = ("no -dirty in the version", "git sha embedded", "working tree clean", "image built from HEAD",
           "sources older than the image")


def image(version, project):
    data = bytearray(b"\xe9" + b"\x00" * 0x1F)
    d = bytearray(256)
    struct.pack_into("<I", d, 0, B.APP_DESC_MAGIC)
    d[0x10:0x10 + len(version)] = version.encode()
    d[0x30:0x30 + min(len(project), 31)] = project[:31].encode()   # project_name[32], NUL-ended
    d[0x70:0x76] = b"v5.4.1"
    return bytes(data + d + b"\x00body" * 64)


def run(version, project):
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    p = os.path.join(TMP, "wican-fw_obd_test.bin")
    with open(p, "wb") as fh:
        fh.write(image(version, project))
    del B.rows[:]
    B.check_image(p)
    return {name: (ok, detail) for name, ok, detail, _ in B.rows}


def main():
    head = subprocess.run(("git", "-C", B.REPO, "rev-parse", "HEAD"), capture_output=True,
                          text=True, check=True).stdout.strip()
    absent = "deadbee"
    while subprocess.run(("git", "-C", B.REPO, "rev-parse", "--verify", "-q", absent + "^{commit}"),
                         capture_output=True).returncode == 0:
        absent = "%07x" % (int(absent, 16) + 1)
    failures = []

    def check(label, ok, detail=""):
        print("  %-4s %s%s" % ("ok" if ok else "FAIL", label, (": " + detail) if detail else ""))
        if not ok:
            failures.append(label)

    try:
        for version in (head[:7] + "-dirty", "v4.13", ""):
            r = run(version, "wican-fw_obd")
            bad = [n for n, (ok, _) in r.items() if ok is False]
            check("version %r: no row fails" % version, not bad, ", ".join(bad))
            check("version %r: no retired row" % version, not [n for n in RETIRED if n in r])
            check("version %r: shown" % version, version in (r.get("version (a label, not judged)") or (0, ""))[1])
        for label, project, want in (
                ("fixed name passes", "wican-fw_obd", True),
                ("bare wican-fw passes", "wican-fw", True),
                ("HEAD's 7 hex digits in the name fail", "wican-fw_obd_%s_man" % head[:7], False),
                ("a long run of HEAD's hex digits fails", "wican-fw_%s" % head[:22], False),
                ("HEAD's 7 hex digits upper-case fail", "wican-fw_obd_%s" % head[:7].upper(), False),
                ("HEAD's 6 hex digits pass (fewer than 7)", "wican-fw_obd_%s" % head[:6], True),
                ("7 hex digits naming no commit pass", "wican-fw_obd_%s" % absent, True),
                ("another prefix fails", "other_obd", False)):
            ok, detail = run("x", project)["project name"]
            check("%s (%s)" % (label, project), ok is want, detail)
            if want is False and "wican-fw" in project:
                check("  ... and names the commit", head[:7] in detail.lower(), detail)
        for name in ("check_image_identity", "check_sources_older_than_image"):
            check("%s is retired" % name, not hasattr(B, name))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n=== %s ===" % ("PASS" if not failures else "FAIL: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
