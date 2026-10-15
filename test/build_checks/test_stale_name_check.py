#!/usr/bin/env python3
"""Tests stale_name_check.py's judgements (no build, no ESP-IDF).

    python3 test_stale_name_check.py

The real-build half is the run itself, in both directions: it must fail on a
tree with the stale-name trap and pass on one without it (spec 12.4 "no stale
image name").
"""

import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stale_name_check as S                                       # noqa: E402

TMP = os.path.join(HERE, "_test_stale_name_tmp")
SHA = "3ee47b7c0ffee0123456789abcdef0123456789a"
OTHER = "95619b7c0ffee0123456789abcdef0123456789a"


def desc(ver, proj=None):
    return (ver, proj if proj is not None else "wican-fw_obd_%s_man" % ver, "v5.4.1", "", "")


def img(ver, suffix="_man"):
    return ["build/wican-fw_obd_%s%s.bin" % (ver, suffix)]


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    try:
        now = time.time()
        for f, age in (("wican-fw_obd_3ee47b7_man.bin", 100), ("wican-fw_obd_3ee47b7-dirty_man.bin", 0),
                       ("ota_data_initial.bin", 0), ("bootloader.bin", 0), ("wican-fw_obd_x.elf", 0),
                       ("wican-fw_obd_3ee47b7_man_rt.bin", 0)):
            p = os.path.join(TMP, f)
            open(p, "wb").close()
            os.utime(p, (now - age, now - age))
        os.makedirs(os.path.join(TMP, "wican-fw_dir.bin"))
        written = sorted(os.path.basename(p) for p in S.written_images(TMP, now - 10))
        prev = os.path.join(TMP, "wican-fw_obd_95619b7_man.bin")
        open(prev, "wb").close()
        since = S.step_start(gap=0.05)
        time.sleep(0.05)   # file times tick coarser than time.time(); a build writes seconds later
        after = os.path.join(TMP, "wican-fw_obd_ab2fa89_man.bin")
        open(after, "wb").close()
        window = [os.path.basename(p) for p in S.written_images(TMP, since)]
        try:
            missing_ok = S.written_images(os.path.join(HERE, "_no_such_build_dir"), 0) == []
        except OSError:
            missing_ok = False
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    checks = [
        ("version: clean and dirty parse; a tag, a suffix, too short or empty do not",
         S.parse_version("3ee47b7") == ("3ee47b7", False) and S.parse_version("3ee47b7-dirty") == ("3ee47b7", True)
         and S.parse_version("v10-3-g82e4e09") is None and S.parse_version("3ee47b7-dirty-x") is None
         and S.parse_version("3ee47b") is None and S.parse_version("") is None and S.parse_version(None) is None),
        ("name: the token before _man/_auto, with -dirty; a suffix after it allowed",
         S.name_version("wican-fw_obd_3ee47b7-dirty_man.bin") == "3ee47b7-dirty"
         and S.name_version("wican-fw_obd_3ee47b7_auto") == "3ee47b7"
         and S.name_version("wican-fw_obd_3ee47b7_man_rt.bin") == "3ee47b7"
         and S.name_version("wican-fw_obd_pro_3ee47b7_man") == "3ee47b7"
         and S.name_version("wican-fw_obd_3ee47b7.bin") is None and S.name_version(None) is None),
        ("carries: the abbreviation must be a prefix of HEAD, and dirty must match exactly",
         S.carries("3ee47b7", SHA, False) and S.carries("3ee47b7c0f-dirty", SHA, True)
         and not S.carries("3ee47b7", SHA, True) and not S.carries("3ee47b7-dirty", SHA, False)
         and not S.carries("95619b7", SHA, False) and not S.carries(None, SHA, False)
         and not S.carries("3ee4000", SHA, False)),
        ("written: this project's top-level .bin files modified since the step began, nothing else",
         written == ["wican-fw_obd_3ee47b7-dirty_man.bin", "wican-fw_obd_3ee47b7_man_rt.bin"]),
        ("step window: an image the previous step wrote just before is not this step's",
         window == ["wican-fw_obd_ab2fa89_man.bin"]),
        ("written: a missing build directory is no images, not a crash",
         missing_ok),
        ("judge: a dirty step whose image reads -dirty in all three passes",
         S.judge(0, img("3ee47b7-dirty"), desc("3ee47b7-dirty"), SHA, True)[0] is True),
        ("judge: THE TRAP -- a dirty step whose image reads the clean name fails",
         S.judge(0, img("3ee47b7"), desc("3ee47b7"), SHA, True)[0] is False),
        ("judge: a commit step whose image reads the previous commit fails",
         S.judge(0, img("95619b7"), desc("95619b7"), SHA, False)[0] is False),
        ("judge: a clean step reading -dirty fails",
         S.judge(0, img("3ee47b7-dirty"), desc("3ee47b7-dirty"), SHA, False)[0] is False),
        ("judge: each reading alone can fail it (filename, version, project_name)",
         S.judge(0, img("3ee47b7"), desc("3ee47b7-dirty"), SHA, True)[0] is False
         and S.judge(0, img("3ee47b7-dirty"), desc("3ee47b7", "wican-fw_obd_3ee47b7-dirty_man"), SHA, True)[0] is False
         and S.judge(0, img("3ee47b7-dirty"), desc("3ee47b7-dirty", "wican-fw_obd_3ee47b7_man"), SHA, True)[0] is False),
        ("judge: a failed build fails even with a right-looking image",
         S.judge(2, img("3ee47b7-dirty"), desc("3ee47b7-dirty"), SHA, True)[0] is False),
        ("judge: no image, two images, or no app descriptor fail (undetermined is not a pass)",
         S.judge(0, [], None, SHA, False)[0] is False
         and S.judge(0, img("3ee47b7") + img("3ee47b7", "_man_rt"), desc("3ee47b7"), SHA, False)[0] is False
         and S.judge(0, img("3ee47b7"), None, SHA, False)[0] is False),
        ("judge: a longer abbreviation of the right commit passes",
         S.judge(0, img("3ee47b7c0f"), desc("3ee47b7c0f"), SHA, False)[0] is True),
        ("no git: a failed build that wrote no image passes",
         S.judge_nogit(False, 1, [])[0] is True),
        ("no git: an image written fails, even with the build failing",
         S.judge_nogit(False, 1, img("3ee47b7"))[0] is False),
        ("no git: a build that succeeded fails, image or not",
         S.judge_nogit(False, 0, [])[0] is False and S.judge_nogit(False, 0, img("3ee47b7"))[0] is False),
        ("no git: if git still answered the step proves nothing and fails",
         S.judge_nogit(True, 1, [])[0] is False),
        ("judge: the right commit for another HEAD fails",
         S.judge(0, img("3ee47b7"), desc("3ee47b7"), OTHER, False)[0] is False),
    ]
    fails = 0
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
