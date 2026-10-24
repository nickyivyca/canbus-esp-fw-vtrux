"""The command line.

    python -B -m vtrux_checks scope --build DIR (--release | --bench)
                                    R17: the files the build read

Exit 0 when the check passes, 1 when it fails or cannot read the build, 2 on
a usage error.
"""

import argparse
import sys

from . import scope


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
    args = ap.parse_args(argv)
    return scope.main(args)


if __name__ == "__main__":
    sys.exit(main())
