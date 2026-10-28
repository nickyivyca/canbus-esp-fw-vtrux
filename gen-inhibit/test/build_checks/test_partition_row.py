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

Since port step 1a (2026-10-27) stock is the board's reference table, in the
copy of the board component the build downloaded (gen-inhibit reviewer: one
copy, the board's). Each case's build dir sits in a throwaway project whose
managed_components/vtrux__wican_v3/ holds board.json and the table, copied from
the board's checkout at the tag the project pins (board_ref.py). Two more
cases: no board in managed_components/, and a board.json naming no file, each
NO REF, never a pass.

    python3 test_partition_row.py
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_ref as R                                           # noqa: E402
import build_output as B                                        # noqa: E402

TMP = os.path.join(B.WORKTREE, "scratch", "build-checks", "test_partition_row")   # not beside this file (0d finding 6)
BUILD = os.path.join(TMP, "build")


def project(board_folder, board_json=None):
    """TMP as a project with BUILD in it and the board's copy in managed_components/ (None: no board)."""
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(os.path.join(BUILD, "partition_table"))
    with open(os.path.join(BUILD, "project_description.json"), "w") as fh:
        json.dump({"project_path": TMP}, fh)
    if board_folder is not None:
        dst = R.managed_folder(TMP)
        os.makedirs(dst)
        board, table = R.read(board_folder)
        shutil.copyfile(table, os.path.join(dst, board["partition_table"]))
        with open(os.path.join(dst, "board.json"), "w") as fh:
            json.dump(board_json or board, fh)


def run(table_bytes):
    """check_partitions() on BUILD holding `table_bytes` (None = no table)."""
    shutil.rmtree(os.path.join(BUILD, "partition_table"), ignore_errors=True)
    os.makedirs(os.path.join(BUILD, "partition_table"))
    if table_bytes is not None:
        with open(os.path.join(BUILD, "partition_table", "partition-table.bin"), "wb") as fh:
            fh.write(table_bytes)
    B.rows.clear()
    B.check_partitions(BUILD)
    (_name, ok, detail, _ref), = B.rows
    return ok, detail


def main():
    try:
        tag, folder = R.pinned_folder()
        board, table = R.read(folder)
    except R.BoardRefError as e:
        print("no board to take stock from: %s: cannot test" % e)
        return 2
    stock = open(table, "rb").read()
    bad = bytearray(stock)
    bad[0x20] ^= 0x01
    print("stock: %s at %s" % (board["partition_table"], tag))
    cases = [
        ("matching table beside the image -> ok", stock, True, None),
        ("one byte differs -> FAIL", bytes(bad), False, None),
        ("no table beside the image -> FAIL naming its dir", None, False,
         os.path.basename(BUILD)),
    ]
    fails = 0
    try:
        project(folder)
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
        for label, args in (("no board in managed_components/ -> NO REF", (None,)),
                            ("board.json naming no file -> NO REF", (folder, dict(board, partition_table="none.bin")))):
            project(*args)
            ok, detail = run(stock)
            good = ok is None and B.rows[0][3] is True
            fails += not good
            print("  %-4s %-48s %s" % ("ok" if good else "FAIL", label, detail[:70]))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n=== %s ===" % ("PASS" if not fails else "FAIL"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
