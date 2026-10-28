"""The command line.

    python -B -m vtrux_checks scope --build DIR (--release | --bench)
                                    R17: the files the build read
    python -B -m vtrux_checks options --build DIR
                                    R18, R26-R29: a release build's options against
                                    the project's release profile, the options
                                    every release build shares and the board's
                                    settings
    python -B -m vtrux_checks switches --build DIR
                                    R20: every switch the release build's source tests
                                    is in the table, and each refusal row passes
    python -B -m vtrux_checks board --build DIR
                                    R21: the partition table, the application slot
                                    and the target against the board's data
    python -B -m vtrux_checks pins --build DIR
                                    R30: no project object read the board's
                                    board_pins.h unless it is on the project's list

Exit 0 when the check passes, 1 when it fails or cannot read the build, 2 on
a usage error.
"""

import argparse
import sys

from . import board, options, pins, scope, switches


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vtrux_checks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scope", help="R17: the files the build read")
    s.add_argument("--build", required=True, help="the build folder (<project>/build)")
    kind = s.add_mutually_exclusive_group(required=True)
    kind.add_argument("--release", dest="kind", action="store_const", const="release",
                      help="a release build: shared code only from managed_components/")
    kind.add_argument("--bench", dest="kind", action="store_const", const="bench",
                      help="a bench or dev build: shared/ may also be read directly")
    o = sub.add_parser("options", help="R18, R26-R29: a release build's options against the release profile, the options every release build shares and the board's settings")
    o.add_argument("--build", required=True, help="the build folder of a release build (<project>/build)")
    w = sub.add_parser("switches", help="R20: build switches and their refusal rows")
    w.add_argument("--build", required=True, help="the build folder of a release build (<project>/build)")
    b = sub.add_parser("board", help="R21: partition table, slot and target against the board data")
    b.add_argument("--build", required=True, help="the build folder of a release build (<project>/build)")
    n = sub.add_parser("pins", help="R30: project objects that read the board's board_pins.h against the project's list")
    n.add_argument("--build", required=True, help="the build folder of a release build (<project>/build)")
    args = ap.parse_args(argv)
    return {"scope": scope, "options": options, "switches": switches, "board": board, "pins": pins}[args.cmd].main(args)


if __name__ == "__main__":
    sys.exit(main())
