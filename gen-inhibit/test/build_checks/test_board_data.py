#!/usr/bin/env python3
"""Tests the WiCAN's board data for shared/tools R21, vtrux_checks.json key "board", against the spec.

    python3 test_board_data.py

Spec 12.3 item 3: the partition table byte-identical to stock, the image against the 1,781,760-byte
slot; spec section 2: "ESP32-C3, 4 MB flash". R21's readings (gen-inhibit reviewer, 2026-10-24):
the board data is the target and the path of the reference table, committed; the slot is that
table's smallest app partition, so it is not written in the data. R21 as worded in shared/tools
a5e7c46 (user, 2026-10-24, option B) adds the board's flash size, written as ESP-IDF names it.
This checks the data: exactly those three keys, the target the committed sdkconfig's
CONFIG_IDF_TARGET, the reference tracked by git, its smallest app partition the spec's slot, and
the flash size the spec's 4 MB and the committed sdkconfig's CONFIG_ESPTOOLPY_FLASHSIZE. Each
judgement is first shown failing on a planted input.
"""

import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(os.path.dirname(os.path.dirname(HERE)), "firmware")   # the WiCAN tree (port step 0d)
SLOT = 1781760          # spec 12.3 item 3
FLASH = "4MB"           # spec section 2: "ESP32-C3, 4 MB flash"
_TARGET = re.compile(r'^CONFIG_IDF_TARGET="([^"]+)"\s*$', re.M)
_FLASHSIZE = re.compile(r'^CONFIG_ESPTOOLPY_FLASHSIZE="([^"]+)"\s*$', re.M)


def app_sizes(table):
    """Sizes of the app entries (type 0) of a partition-table .bin."""
    out = []
    for i in range(0, len(table) - 31, 32):
        if table[i:i + 2] != b"\xaa\x50":
            break
        if table[i + 2] == 0:
            out.append(struct.unpack_from("<I", table, i + 8)[0])
    return out


def judge(board, sdkconfig, table, tracked):
    """[failure] for the board object, the committed sdkconfig's text, the reference's bytes
    (None if missing) and whether git tracks the reference."""
    bad = []
    if not isinstance(board, dict) or set(board) != {"target", "partition_table", "flash_size"}:
        return ["board is not an object with exactly target, partition_table and flash_size: %r" % (board,)]
    m = _TARGET.findall(sdkconfig)
    if m != [board["target"]]:
        bad.append("target %r, sdkconfig's CONFIG_IDF_TARGET %s" % (board["target"], m))
    if board["flash_size"] != FLASH:
        bad.append("flash_size %r, the spec's %s" % (board["flash_size"], FLASH))
    fs = _FLASHSIZE.findall(sdkconfig)
    if fs != [board["flash_size"]]:
        bad.append("flash_size %r, sdkconfig's CONFIG_ESPTOOLPY_FLASHSIZE %s" % (board["flash_size"], fs))
    if table is None:
        bad.append("no file at %s" % board["partition_table"])
    else:
        sizes = app_sizes(table)
        if not sizes or min(sizes) != SLOT:
            bad.append("the reference's smallest app partition is %s, the spec's slot %d" % (min(sizes) if sizes else None, SLOT))
    if not tracked:
        bad.append("%s is not tracked by git" % board["partition_table"])
    return bad


def real():
    with open(os.path.join(REPO, "vtrux_checks.json"), encoding="utf-8") as f:
        board = json.load(f).get("board")
    with open(os.path.join(REPO, "sdkconfig"), encoding="utf-8") as f:
        sdk = f.read()
    rel = board.get("partition_table", "") if isinstance(board, dict) else ""
    p = os.path.join(REPO, rel)
    table = open(p, "rb").read() if rel and os.path.isfile(p) else None
    tracked = bool(rel) and subprocess.run(["git", "-C", REPO, "ls-files", "--error-unmatch", "--", rel],
                                           capture_output=True).returncode == 0
    return board, sdk, table, tracked


def main():
    def entry(t, size, label):
        return b"\xaa\x50" + struct.pack("<BBII", t, 0x10, 0x10000, size) + label.ljust(16, b"\x00") + b"\x00" * 4
    good_t = entry(1, 0x4000, b"nvs") + entry(0, SLOT, b"ota_0") + entry(0, SLOT, b"ota_1") + b"\xff" * 32
    good_b = {"target": "esp32c3", "partition_table": "ref.bin", "flash_size": "4MB"}
    sdk = 'CONFIG_SOC_X=y\nCONFIG_IDF_TARGET="esp32c3"\nCONFIG_ESPTOOLPY_FLASHSIZE="4MB"\n'
    checks = [
        ("faithful data passes", judge(good_b, sdk, good_t, True) == []),
        ("another target fails", len(judge(dict(good_b, target="esp32s3"), sdk, good_t, True)) == 1),
        ("no target in sdkconfig fails", len(judge(good_b, sdk.replace("CONFIG_IDF_TARGET", "CONFIG_X"), good_t, True)) == 1),
        ("a flash size the spec does not give fails", len(judge(dict(good_b, flash_size="8MB"), sdk.replace("4MB", "8MB"),
                                                               good_t, True)) == 1),
        ("a flash size sdkconfig does not set fails", len(judge(good_b, sdk.replace("4MB", "8MB"), good_t, True)) == 1),
        ("no flash size in sdkconfig fails", len(judge(good_b, sdk.replace("CONFIG_ESPTOOLPY_FLASHSIZE", "CONFIG_Y"),
                                                       good_t, True)) == 1),
        ("no flash size key fails", len(judge({"target": "esp32c3", "partition_table": "ref.bin"}, sdk, good_t, True)) == 1),
        ("a missing reference fails", len(judge(good_b, sdk, None, True)) == 1),
        ("an untracked reference fails", len(judge(good_b, sdk, good_t, False)) == 1),
        ("a smaller ota_1 fails", len(judge(good_b, sdk, good_t.replace(entry(0, SLOT, b"ota_1"), entry(0, SLOT - 4096, b"ota_1")),
                                            True)) == 1),
        ("no app partition fails", len(judge(good_b, sdk, entry(1, 0x4000, b"nvs") + b"\xff" * 32, True)) == 1),
        ("a slot key fails", len(judge(dict(good_b, slot=SLOT), sdk, good_t, True)) == 1),
        ("a board name fails", len(judge("wican", sdk, good_t, True)) == 1),
    ]
    got = judge(*real())
    checks.append(("gen-inhibit's own board data: %s" % ("; ".join(got) or "target, tracked reference, slot %d, flash size %s"
                                                         % (SLOT, FLASH)), got == []))
    bad = 0
    for label, ok in checks:
        print("%s  %s" % ("ok  " if ok else "FAIL", label))
        bad += not ok
    print("\n%d check(s), %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
