#!/usr/bin/env python3
"""The tester's unpublished build-check rows applied to images built in OTHER trees.
Gen-inhibit tester, 2026-10-16.

build_output.py judges an image against the repo its own copy lives in (its REPO),
so the tester's clone cannot run it whole on an image built in a worktree or a prod
clone: the build record names CMake files outside REPO, and the clone's own
uncommitted harness changes fail its clean-tree row. This runs, from the tester's
copy, only the rows the harness change adds or alters -- the switch classification
(build_switches.scan over the image's own tree and build record) and the
stock-feature rows (check_no_stock_features) -- on each image, in its own tree.

    GI_FW=<tester clone> py -3.14 tester_rows_on_images.py <label>=<repo>,<build dir>,<bin> ...
"""
import os
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

FW = os.environ.get("GI_FW") or gi_paths.repo_root()
sys.path.insert(0, os.path.join(FW, "test", "build_checks"))
import build_output as BO      # noqa: E402
import build_switches as BS    # noqa: E402

bad = 0
for arg in sys.argv[1:]:
    label, rest = arg.split("=", 1)
    repo, build, img = rest.split(",")
    print("== %s (checks from %s)" % (label, os.path.basename(FW)))
    try:
        rec = BS.read_record(repo, build)
        found = BS.scan(repo, rec)
        unlisted, stale, malformed = BS.classify(found)
        counts = {}
        for s in found:
            if s in BS.SWITCHES:
                counts[BS.SWITCHES[s][0]] = counts.get(BS.SWITCHES[s][0], 0) + 1
        ok = not (unlisted or stale or malformed)
        print("  %-4s classified: %d symbols, unlisted %s, stale %s, malformed %s (%s)"
              % ("ok" if ok else "FAIL", len(found), unlisted, stale, malformed,
                 ", ".join("%d %s" % (n, c) for c, n in sorted(counts.items()))))
        bad += 0 if ok else 1
    except BS.RecordError as e:
        print("  FAIL classified: record unusable: %s" % e)
        bad += 1
    BO.rows.clear()
    BO.check_no_stock_features(img)
    for name, ok, detail, ref_missing in BO.rows:
        tag = "NOREF" if ok is None or ref_missing else ("ok" if ok else "FAIL")
        print("  %-4s %s -- %s" % (tag, name, detail[:200]))
        bad += 0 if tag == "ok" else 1
    BO.rows.clear()
print("PASS" if not bad else "FAIL: %d" % bad)
sys.exit(1 if bad else 0)
