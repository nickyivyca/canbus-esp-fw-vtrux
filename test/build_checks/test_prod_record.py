"""Test of check_prod_record() in build_output.py against spec 12.3 item 3.

Spec 12.3 item 3 (prod builds moved to the shared tooling at port step 0,
user, 2026-10-25): a prod build is a release build of the shared build
tooling, which writes one record named by the image hash, with the image
beside it, in `$REVERSE_IT_DIR/firmware/builds/gen-inhibit/` (shared/tools
SPEC R13). An image with no such record is not a prod build and is reported
NO REFERENCE, not as a failure (changed 2026-10-07, user). A record that is
present must still match the image, so a present but wrong or unreadable
record is a FAIL.

Every case writes an image with an app descriptor and an image hash (the
ELF SHA-256 the descriptor holds, shared/tools R1) into a scratch folder,
and a records folder beside it, then classifies the row the way
build_output.main() does (ok None or ref_missing -> NO REF; False -> FAIL;
True -> ok). The retired `<image>.prod.json` beside an image must no longer
count. The last two cases feed the OLD missing-record row (a plain False)
through the same classifier and require it to come out FAIL, so the
assertion on the first case can fail.

Run:  py -3.14 test/build_checks/test_prod_record.py   (exit 0 = all pass)
"""
import hashlib
import json
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_output as B                                        # noqa: E402

TMP = os.path.join(B.REPO, "scratch", "build-checks", "test_prod_record")


def mark(row):
    _name, ok, _detail, ref_missing = row
    if ok is None or ref_missing:
        return "NO REF"
    return "ok" if ok else "FAIL"


def image(elf_sha, desc=True):
    """An app image: an app descriptor at 0x20 naming a version and project,
    the ELF SHA-256 at 0xB0, then a body."""
    data = bytearray(b"\xe9" + b"\x00" * 0x1F)
    d = bytearray(256)
    if desc:
        struct.pack_into("<I", d, 0, B.APP_DESC_MAGIC)
    d[0x10:0x17] = b"6771edf"
    d[0x30:0x3C] = b"wican-fw_obd"
    d[0x70:0x76] = b"v5.4.1"
    d[0x90:0xB0] = elf_sha
    data += d
    data += b"\x00image under test\xff" * 64
    return bytes(data)


def record_for(data, h, **over):
    rec = {"kind": "release build record (shared/tools SPEC R13)", "schema": 1, "project": "gen-inhibit",
           "commit": "6771edf" + "0" * 33, "image_hash": h, "image_size": len(data),
           "image": {"bin": h + ".bin", "elf": h + ".elf", "built_as": "wican-fw_obd_6771edf.bin"},
           "tooling": {"tag": "2026.10.24", "commit": "e40f5cb" + "0" * 33, "digest": "2d" * 32, "dirty": False}}
    rec.update(over)
    return rec


def run_case(data, record=None, bin_bytes=None, bin_name=None, old_record=False, folder_arg=True, env=None):
    """Write the image, and the records folder: the record unless None (a str is written as is), and
    the .bin beside it (bin_bytes, default the image; False for none). Run the check and classify."""
    shutil.rmtree(TMP, ignore_errors=True)
    builds = os.path.join(TMP, "build")
    recs = os.path.join(TMP, "ri", "firmware", "builds", "gen-inhibit")   # R13's folder, written out
    os.makedirs(builds)
    os.makedirs(recs)
    img = os.path.join(builds, "wican-fw_obd_6771edf.bin")
    with open(img, "wb") as fh:
        fh.write(data)
    h = B.image_hash(img)
    if old_record:
        with open(img + ".prod.json", "w", encoding="utf-8") as fh:
            json.dump({"md5": hashlib.md5(data).hexdigest(), "size": len(data), "clean_tree": True,
                       "reconfigured": True, "commit": "6771edf", "components": {}}, fh)
    if record is not None:
        with open(os.path.join(recs, (h or "none") + ".json"), "w", encoding="utf-8") as fh:
            fh.write(record if isinstance(record, str) else json.dumps(record))
        if bin_bytes is not False:
            with open(os.path.join(recs, bin_name or (h + ".bin")), "wb") as fh:
                fh.write(data if bin_bytes is None else bin_bytes)
    saved = os.environ.pop("REVERSE_IT_DIR", None)
    if env == "set":
        os.environ["REVERSE_IT_DIR"] = os.path.join(TMP, "ri")
    try:
        del B.rows[:]
        B.check_prod_record(img, recs if folder_arg else None)
    finally:
        os.environ.pop("REVERSE_IT_DIR", None)
        if saved is not None:
            os.environ["REVERSE_IT_DIR"] = saved
    assert len(B.rows) == 1, B.rows
    return mark(B.rows[0]), B.rows[0][2]


def main():
    sha = hashlib.sha256(b"the ELF").digest()
    h = sha.hex()
    data = image(sha)
    good = record_for(data, h)
    other = hashlib.sha256(b"another ELF").hexdigest()
    cases = [
        ("no record -> NO REF (not a prod build)", dict(), "NO REF", "not a prod build"),
        ("only the retired <image>.prod.json -> NO REF", dict(old_record=True), "NO REF", "not a prod build"),
        ("matching record and image beside it -> ok", dict(record=good), "ok", "built from 6771edf"),
        ("record found through REVERSE_IT_DIR -> ok", dict(record=good, folder_arg=False, env="set"), "ok",
         "built from 6771edf"),
        ("REVERSE_IT_DIR unset, no folder given -> NO REF", dict(record=good, folder_arg=False), "NO REF",
         "REVERSE_IT_DIR is unset"),
        ("image with no descriptor -> NO REF", dict(data=image(sha, desc=False)), "NO REF", "no image hash"),
        ("record's image hash another -> FAIL", dict(record=record_for(data, other)), "FAIL", "image hash"),
        ("record's project another -> FAIL", dict(record=dict(good, project="charge-interposer")), "FAIL",
         "project"),
        ("record's size wrong -> FAIL", dict(record=dict(good, image_size=len(data) + 1)), "FAIL", "size"),
        ("no .bin beside the record -> FAIL", dict(record=good, bin_bytes=False), "FAIL", "beside the record"),
        ("the .bin beside differs -> FAIL", dict(record=good, bin_bytes=data[:-1] + b"\x00"), "FAIL",
         "is not this image"),
        ("the .bin the record names, under another name -> ok",
         dict(record=dict(good, image={"bin": "named.bin"}), bin_name="named.bin"), "ok", "built from"),
        ("the .bin the record names absent, <hash>.bin there -> FAIL",
         dict(record=dict(good, image={"bin": "named.bin"})), "FAIL", "no named.bin"),
        ("no .bin named, <hash>.bin there -> ok", dict(record=dict(good, image={})), "ok", "built from"),
        ("unreadable record -> FAIL", dict(record="{not json"), "FAIL", "unreadable"),
        ("record not an object -> FAIL", dict(record="[1, 2]"), "FAIL", "unreadable"),
    ]
    failures = []
    try:
        for label, kw, want, text in cases:
            got, detail = run_case(kw.pop("data", data), **kw)
            ok = got == want and (text is None or text in detail)
            print("  %-4s %-58s got %s: %s" % ("ok" if ok else "FAIL", label, got, detail[:90]))
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
