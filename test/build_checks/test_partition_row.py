#!/usr/bin/env python3
"""Tests build_output.py's partition-table row against the image's own build dir.

Gen-inhibit tester, 2026-10-08. The row used to read build/ whatever image it
was given, so an image built with -B build-tester-<sha> failed it ("no
build/partition_table/partition-table.bin") although its own table was
byte-identical to stock. Three cases, each in a throwaway build dir:

  matching table beside the image   -> ok
  a table that differs by one byte  -> FAIL
  no table beside the image         -> FAIL, naming that dir, not build/

Plus the guard against the old behaviour: with a mismatched table beside the
image, a stock-matching table in build/ must NOT rescue it.

    python3 test_partition_row.py
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_output as B                                        # noqa: E402

TMP = os.path.join(HERE, "_test_partition_tmp")
STOCK = os.path.join(B.REF, "stock-v4.13-partition-table.bin")


def run(table_bytes):
    """check_partitions() on a build dir holding `table_bytes` (None = no table)."""
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(os.path.join(TMP, "partition_table"))
    if table_bytes is not None:
        with open(os.path.join(TMP, "partition_table", "partition-table.bin"), "wb") as fh:
            fh.write(table_bytes)
    B.rows.clear()
    B.check_partitions(TMP)
    (_name, ok, detail, _ref), = B.rows
    return ok, detail


def main():
    if not os.path.exists(STOCK):
        print("no stock reference at %s: cannot test" % STOCK)
        return 2
    stock = open(STOCK, "rb").read()
    bad = bytearray(stock)
    bad[0x20] ^= 0x01
    cases = [
        ("matching table beside the image -> ok", stock, True, None),
        ("one byte differs -> FAIL", bytes(bad), False, None),
        ("no table beside the image -> FAIL naming its dir", None, False,
         "_test_partition_tmp"),
    ]
    fails = 0
    try:
        for label, data, want, must_mention in cases:
            ok, detail = run(data)
            good = ok is want and (must_mention is None or must_mention in detail)
            fails += not good
            print("  %-4s %-48s %s" % ("ok" if good else "FAIL", label, detail[:70]))
        # The old code read build/ regardless; a good table there must not
        # rescue a bad one beside the image. Only meaningful if build/ has one.
        repo_table = os.path.join(B.REPO, "build", "partition_table", "partition-table.bin")
        ok, detail = run(bytes(bad))
        good = ok is False
        fails += not good
        print("  %-4s %-48s %s" % ("ok" if good else "FAIL",
                                  "bad table beside image, build/ ignored", detail[:70]))
        if not os.path.exists(repo_table):
            print("       (build/ has no table here, so this case also passes on the old code;"
                  " the 'names its dir' case is the one that fails it)")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n=== %s ===" % ("PASS" if not fails else "FAIL"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
