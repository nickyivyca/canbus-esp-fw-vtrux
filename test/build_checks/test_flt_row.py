"""Test of the "project name is not a rehearsal build" row in build_output.py.

The row refuses an OTA_HEALTH_FAULT_INJECT image by the "_flt" suffix in
esp_app_desc.project_name. It used to test project.endswith("_flt"), which
holds only while "_flt" is the LAST suffix. On 2026-10-08 the diag branch
appended "_DIAG" after it, so a fault-injected diag build would have been
named ..._man_flt_DIAG and passed the row. The row now looks for "_flt" as a
whole "_"-delimited token anywhere in the name.

Each case writes a synthetic image with an esp_app_desc_t carrying the given
project name and no fault-injection format string, so only the name row can
refuse it. The last step swaps the old endswith() predicate back in and
requires at least one case to come out wrong, so these assertions can fail.

Run:  python test/build_checks/test_flt_row.py   (exit 0 = all pass)
"""
import os
import re
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_output as B                                        # noqa: E402

TMP = os.path.join(HERE, "_test_flt_row_tmp")
ROW = "project name is not a rehearsal build"


def image(project):
    desc = bytearray(256)
    struct.pack_into("<I", desc, 0, B.APP_DESC_MAGIC)
    desc[0x10:0x10 + 7] = b"a71b90a"
    name = project.encode("ascii")
    assert len(name) < 32, project
    desc[0x30:0x30 + len(name)] = name
    return bytes(B.APP_DESC_OFF) + bytes(desc) + b"\x00body\xff" * 64


def mark(project):
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    img = os.path.join(TMP, "wican-fw_obd_test.bin")
    with open(img, "wb") as fh:
        fh.write(image(project))
    del B.rows[:]
    B.check_not_rollback_rehearsal(img)
    hits = [r for r in B.rows if r[0] == ROW]
    assert len(hits) == 1, B.rows
    _name, ok, _detail, ref_missing = hits[0]
    if ok is None or ref_missing:
        return "NO REF"
    return "ok" if ok else "FAIL"


CASES = [
    ("wican-fw_obd_a71b90a_man", "ok"),
    ("wican-fw_obd_748d39b_man_DIAG", "ok"),
    ("wican-fw_obd_a71b90a_man_flt", "FAIL"),
    # project_name holds 31 characters, so the full ..._obd_<sha>_man_flt_DIAG
    # (33) cannot be built; shorter names with the same suffix order stand in.
    ("wican-fw_a71b90a_man_flt_DIAG", "FAIL"),
    ("wican-fw_a71b90a_man_DIAG_flt", "FAIL"),
    ("wican-fw_obd_a71b90a_man_fltx", "ok"),
]


def run(label):
    bad = []
    for project, want in CASES:
        got = mark(project)
        if got != want:
            bad.append(project)
        if label:
            print("  %-4s %-36s want %-4s got %s"
                  % ("ok" if got == want else "FAIL", project, want, got))
    return bad


def main():
    try:
        failures = run(True)
        saved = B.FLT_TOKEN
        B.FLT_TOKEN = re.compile(r"_flt$")      # the old endswith("_flt")
        try:
            old_bad = run(False)
        finally:
            B.FLT_TOKEN = saved
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("  old endswith() predicate gets %d case(s) wrong: %s"
          % (len(old_bad), old_bad))
    if not old_bad:
        failures.append("the old predicate passes every case, so the test cannot fail")
    print("%s: %d failure(s)" % ("PASS" if not failures else "FAIL", len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
