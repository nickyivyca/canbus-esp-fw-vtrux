#!/usr/bin/env python3
"""Tests the WiCAN's board data, board.json in the board component gen-inhibit pins
(shared/boards wican_v3), against the spec.

    python3 test_board_data.py

The board is the vtrux/wican_v3 dependency in firmware/main/idf_component.yml, taken from git at
a tag, and its data is read from that tag's checkout, ../shared-<tag>/ beside the worktree root
(board_ref.py; gen-inhibit reviewer, 2026-10-27). Spec 12.3 item 3: the partition table
byte-identical to stock, the image against the 1,781,760-byte slot; spec section 2: "ESP32-C3,
4 MB flash". R21's readings (gen-inhibit reviewer, 2026-10-24): the reference table is committed,
and the slot is its smallest app partition. This checks the data: the target the committed
sdkconfig's CONFIG_IDF_TARGET, the flash size the spec's 4 MB and the committed sdkconfig's
CONFIG_ESPTOOLPY_FLASHSIZE, the reference tracked by git in the board's checkout and its smallest
app partition the spec's slot, and every setting the board lists as required of the project
named by the release profile in vtrux_checks.json (shared/tools R29: an option at any value, a
choice with exactly one member true), so the profile cites the spec for each. Each judgement is
first shown failing on a planted input.
"""

import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_ref as R  # noqa: E402

REPO = R.REPO
SLOT = 1781760          # spec 12.3 item 3
FLASH = "4MB"           # spec section 2: "ESP32-C3, 4 MB flash"
_TARGET = re.compile(r'^CONFIG_IDF_TARGET="([^"]+)"\s*$', re.M)
_FLASHSIZE = re.compile(r'^CONFIG_ESPTOOLPY_FLASHSIZE="([^"]+)"\s*$', re.M)
KEYS = ("target", "flash_size", "partition_table", "required")


def app_sizes(table):
    """Sizes of the app entries (type 0) of a partition-table .bin."""
    out = []
    for i in range(0, len(table) - 31, 32):
        if table[i:i + 2] != b"\xaa\x50":
            break
        if table[i + 2] == 0:
            out.append(struct.unpack_from("<I", table, i + 8)[0])
    return out


def judge_required(required, profile):
    """[failure] for each required setting the profile ({CONFIG_X: {"value", "spec"}}) does not name."""
    bad = []
    for r in required:
        what = r.get("setting", "?") if isinstance(r, dict) else "?"
        if isinstance(r, dict) and isinstance(r.get("option"), str):
            if r["option"] not in profile:
                bad.append("%s: the profile does not name %s" % (what, r["option"]))
        elif isinstance(r, dict) and isinstance(r.get("choice"), list):
            on = [o for o in r["choice"] if (profile.get(o) or {}).get("value") is True]
            if len(on) != 1:
                bad.append("%s: the profile sets %d of %s true; it needs exactly one" % (what, len(on), r["choice"]))
        else:
            bad.append("a required setting neither an option nor a choice: %r" % (r,))
    return bad


def judge(board, sdkconfig, table, tracked, profile):
    """[failure] for board.json, the committed sdkconfig's text, the reference's bytes (None if
    missing), whether git tracks the reference, and the release profile."""
    bad = []
    if not isinstance(board, dict) or not all(k in board for k in KEYS):
        return ["board.json is not an object with %s: %r" % (", ".join(KEYS), board)]
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
    if not isinstance(board["required"], list):
        bad.append("required is not a list: %r" % (board["required"],))
    else:
        bad += judge_required(board["required"], profile)
    return bad


def real():
    """judge()'s inputs from the pinned board's checkout and the project. Raises R.BoardRefError."""
    tag, folder = R.pinned_folder()
    board, table_path = R.read(folder)
    with open(os.path.join(REPO, "sdkconfig"), encoding="utf-8") as f:
        sdk = f.read()
    with open(os.path.join(REPO, "vtrux_checks.json"), encoding="utf-8") as f:
        profile = json.load(f).get("release_profile") or {}
    table = open(table_path, "rb").read() if os.path.isfile(table_path) else None
    tracked = subprocess.run(["git", "-C", folder, "ls-files", "--error-unmatch", "--", board["partition_table"]],
                             capture_output=True).returncode == 0
    return tag, (board, sdk, table, tracked, profile)


def main():
    def entry(t, size, label):
        return b"\xaa\x50" + struct.pack("<BBII", t, 0x10, 0x10000, size) + label.ljust(16, b"\x00") + b"\x00" * 4
    good_t = entry(1, 0x4000, b"nvs") + entry(0, SLOT, b"ota_0") + entry(0, SLOT, b"ota_1") + b"\xff" * 32
    req = [{"setting": "the console", "choice": ["CONFIG_C_A", "CONFIG_C_B"]},
           {"setting": "Bluetooth", "option": "CONFIG_BT"}]
    good_b = {"target": "esp32c3", "partition_table": "ref.bin", "flash_size": "4MB", "required": req,
              "hardware_name": "OBD"}
    prof = {"CONFIG_C_B": {"value": True, "spec": "s"}, "CONFIG_C_A": {"value": "unset", "spec": "s"},
            "CONFIG_BT": {"value": "unset", "spec": "s"}}
    sdk = 'CONFIG_SOC_X=y\nCONFIG_IDF_TARGET="esp32c3"\nCONFIG_ESPTOOLPY_FLASHSIZE="4MB"\n'

    def n(board=good_b, s=sdk, table=good_t, tracked=True, profile=prof):
        return len(judge(board, s, table, tracked, profile))
    checks = [
        ("faithful data passes", n() == 0),
        ("another target fails", n(board=dict(good_b, target="esp32s3")) == 1),
        ("no target in sdkconfig fails", n(s=sdk.replace("CONFIG_IDF_TARGET", "CONFIG_X")) == 1),
        ("a flash size the spec does not give fails", n(board=dict(good_b, flash_size="8MB"), s=sdk.replace("4MB", "8MB")) == 1),
        ("a flash size sdkconfig does not set fails", n(s=sdk.replace("4MB", "8MB")) == 1),
        ("no flash size in sdkconfig fails", n(s=sdk.replace("CONFIG_ESPTOOLPY_FLASHSIZE", "CONFIG_Y")) == 1),
        ("no flash size key fails", n(board={k: v for k, v in good_b.items() if k != "flash_size"}) == 1),
        ("no required key fails", n(board={k: v for k, v in good_b.items() if k != "required"}) == 1),
        ("a missing reference fails", n(table=None) == 1),
        ("an untracked reference fails", n(tracked=False) == 1),
        ("a smaller ota_1 fails", n(table=good_t.replace(entry(0, SLOT, b"ota_1"), entry(0, SLOT - 4096, b"ota_1"))) == 1),
        ("no app partition fails", n(table=entry(1, 0x4000, b"nvs") + b"\xff" * 32) == 1),
        ("a board name, not an object, fails", n(board="wican") == 1),
        ("a required option the profile does not name fails", n(profile={k: v for k, v in prof.items() if k != "CONFIG_BT"}) == 1),
        ("a required choice with no member true fails", n(profile=dict(prof, CONFIG_C_B={"value": "unset", "spec": "s"})) == 1),
        ("a required choice with two members true fails", n(profile=dict(prof, CONFIG_C_A={"value": True, "spec": "s"})) == 1),
        ("a required choice member set to 1, not true, fails", n(profile=dict(prof, CONFIG_C_B={"value": 1, "spec": "s"})) == 1),
        ("a required setting of another form fails", n(board=dict(good_b, required=req + [{"setting": "x", "value": 1}])) == 1),
        ("a choice whose unset members the profile leaves out passes",
         n(profile={k: v for k, v in prof.items() if k != "CONFIG_C_A"}) == 0),
    ]
    for label, text, want in (
            ("the block form", "dependencies:\n  idf: '>=5.4'\n  vtrux/wican_v3:\n    git: https://x/y.git\n"
                               "    path: shared/boards/wican_v3\n    version: \"2026.10.27\"  # the pin\n", "2026.10.27"),
            ("the inline form", "dependencies:\n  vtrux/wican_v3: '2026.10.27'\n", "2026.10.27"),
            ("not pinned", "dependencies:\n  espressif/mdns: '*'\n", None),
            ("another dependency's version below it", "dependencies:\n  vtrux/wican_v3:\n    path: p\n"
                                                      "  other/x:\n    version: 9.9.9\n", None),
            ("a longer name", "dependencies:\n  vtrux/wican_v3x:\n    version: 1\n", None)):
        got = R.parse_pin(text)
        checks.append(("the pin, %s: %s" % (label, got), got == want))
    try:
        tag, inputs = real()
        got = judge(*inputs)
        checks.append(("gen-inhibit's board at %s: %s" % (tag, "; ".join(got) or "target, flash size %s, tracked reference, "
                                                          "slot %d, every required setting named" % (FLASH, SLOT)), got == []))
    except R.BoardRefError as e:
        checks.append(("gen-inhibit's board: %s" % e, False))
    bad = 0
    for label, ok in checks:
        print("%s  %s" % ("ok  " if ok else "FAIL", label))
        bad += not ok
    print("\n%d check(s), %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
