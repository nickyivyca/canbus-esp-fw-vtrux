"""Test of check_prod_record() in build_output.py against spec 12.3 item 3.

Spec 12.3 item 3 (changed 2026-10-07, user): an image with no
`<image>.prod.json` is not a prod build and is reported NO REFERENCE, not as a
failure. A record that is present must still match the image, so a present
but wrong or unreadable record is a FAIL.

Every case runs through the same row-classification build_output.main() uses
(ok None or ref_missing -> NO REF; False -> FAIL; True -> ok). The last two
cases feed the OLD behaviour (a missing record as a plain False) through the
same classifier and require it to come out FAIL, so the assertion on case 1
can fail.

Run:  python test/build_checks/test_prod_record.py   (exit 0 = all pass)
"""
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_output as B                                        # noqa: E402

TMP = os.path.join(HERE, "_test_prod_record_tmp")


def mark(row):
    _name, ok, _detail, ref_missing = row
    if ok is None or ref_missing:
        return "NO REF"
    return "ok" if ok else "FAIL"


def run_case(image_bytes, record):
    """Write an image (and a record unless None), run the check, classify."""
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    img = os.path.join(TMP, "wican-fw_obd_test.bin")
    with open(img, "wb") as fh:
        fh.write(image_bytes)
    if record is not None:
        with open(img + ".prod.json", "w", encoding="utf-8") as fh:
            fh.write(record if isinstance(record, str) else json.dumps(record))
    del B.rows[:]
    B.check_prod_record(img)
    assert len(B.rows) == 1, B.rows
    return mark(B.rows[0]), B.rows[0][2]


def main():
    data = b"\x00image under test\xff" * 64
    good = {"md5": hashlib.md5(data).hexdigest(), "size": len(data),
            "clean_tree": True, "reconfigured": True, "commit": "6771edf",
            "components": {}}
    cases = [
        ("no record -> NO REF (not a prod build)", None, "NO REF",
         "not a prod build"),
        ("matching record -> ok", good, "ok", None),
        ("record with wrong md5 -> FAIL", dict(good, md5="0" * 32), "FAIL",
         None),
        ("record without clean_tree -> FAIL", dict(good, clean_tree=False),
         "FAIL", None),
        ("unreadable record -> FAIL", "{not json", "FAIL", None),
    ]
    failures = []
    try:
        for label, rec, want, text in cases:
            got, detail = run_case(data, rec)
            ok = got == want and (text is None or text in detail)
            print("  %-4s %-44s got %s" % ("ok" if ok else "FAIL", label, got))
            if not ok:
                failures.append(label)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    # The classifier must be able to tell the old behaviour from the new.
    old_style = ("prod-build record present", False, "no record", False)
    new_style = ("prod-build record present", None, "not a prod build", True)
    for label, row, want in (("self-check: old missing-record row is FAIL",
                              old_style, "FAIL"),
                             ("self-check: new missing-record row is NO REF",
                              new_style, "NO REF")):
        ok = mark(row) == want
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    print("\n=== %s ===" % ("PASS" if not failures else
                            "FAIL: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
