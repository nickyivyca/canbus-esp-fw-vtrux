"""Where the charge interposer finds what is not in this repo.

Added by the charge-interposer tester, 2026-10-08, in the path-fix commit
after the move from SeaDrive (reviewer's rulings of the same day). Before the
move every file reached its neighbours by counting `..` up the SeaDrive tree
(projects/vtrux/tools/interposer/); in this repo those neighbours are
elsewhere, so every such path goes through this module instead, and nothing
machine-absolute is committed.

Two settings, both environment variables:

  VTRUX_DATA          REQUIRED for anything that reads data. The absolute
                      path of SeaDrive's `projects/vtrux` on this machine.
                      From it come the captures (`logs/`), the harness
                      fixtures -- traces, goldens, golden_regen_log.txt,
                      l3_stimulus.txt, the mutation scripts and the run
                      folders (`notes/artifacts/interposer-firmware/`,
                      `notes/artifacts/interposer-runs/`) -- and, for now,
                      `canre` (two levels up). There is no default: unset
                      is a clear error, never a guess.
  VTRUX_PUBLIC_REPO   Optional. The public canbus-reveng-vtrux-coda clone,
                      which supplies the vehicle DBCs. Default: the sibling
                      directory `../canbus-reveng-vtrux-coda` of this repo.

canre comes from SeaDrive, not the public clone, because the public copy
(1bd6d1a) predates SeaDrive's parser fixes for signed BUSMASTER timestamp
components and midnight rollover, and regress must read the captures with the
parser E1 ran on (reviewer, 2026-10-08). Switch canre_root() to the public
repo once it is synced.

Under WSL, pass the Windows value through with WSLENV=VTRUX_DATA/p, which
translates it to /mnt/c/...; the shell scripts read the same variable.

Every function resolves when CALLED, so importing a module that uses these
does not require the settings; only touching the data does.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
PUBLIC_DEFAULT = os.path.join(REPO_ROOT, "..", "canbus-reveng-vtrux-coda")


class PathsError(RuntimeError):
    pass


def data_dir():
    """SeaDrive's projects/vtrux, from VTRUX_DATA."""
    v = os.environ.get("VTRUX_DATA", "").strip()
    if not v:
        raise PathsError(
            "VTRUX_DATA is not set. Set it to the absolute path of SeaDrive's "
            "projects/vtrux on this machine (captures in logs/, the harness "
            "fixtures in notes/artifacts/interposer-firmware/, canre two "
            "levels up). Under WSL pass it with WSLENV=VTRUX_DATA/p. See "
            "charge-interposer/paths.py.")
    v = os.path.normpath(v)
    if not os.path.isdir(os.path.join(v, "notes", "artifacts")):
        raise PathsError(
            "VTRUX_DATA=%s has no notes/artifacts/ -- it must be SeaDrive's "
            "projects/vtrux" % v)
    return v


def artifacts():
    return os.path.join(data_dir(), "notes", "artifacts")


def fixtures():
    """The traces, goldens, l3_stimulus.txt and mutation scripts."""
    return os.path.join(artifacts(), "interposer-firmware")


def runs_dir():
    """Where run_scenario writes, and where the saved acceptance runs are."""
    return os.path.join(artifacts(), "interposer-runs")


def logs():
    """The captures."""
    return os.path.join(data_dir(), "logs")


def public_repo():
    v = os.environ.get("VTRUX_PUBLIC_REPO", "").strip()
    p = os.path.normpath(v or PUBLIC_DEFAULT)
    if not os.path.isfile(os.path.join(p, "projects", "vtrux",
                                       "epri-pt-bus.dbc")):
        raise PathsError(
            "no public repo at %s (projects/vtrux/epri-pt-bus.dbc missing). "
            "Clone canbus-reveng-vtrux-coda beside this repo or set "
            "VTRUX_PUBLIC_REPO." % p)
    return p


def dbc_dir():
    """The vehicle DBCs: the public repo's projects/vtrux."""
    return os.path.join(public_repo(), "projects", "vtrux")


def canre_root():
    """The directory holding canre/ -- SeaDrive's reverse-it root, for now."""
    p = os.path.normpath(os.path.join(data_dir(), "..", ".."))
    if not os.path.isdir(os.path.join(p, "canre")):
        raise PathsError("no canre/ at %s (two levels above VTRUX_DATA)" % p)
    return p


def add_canre(required=True):
    """Put canre's parent on sys.path. With required=False a missing
    setting is not an error -- for modules that never import canre."""
    try:
        p = canre_root()
    except PathsError:
        if required:
            raise
        return None
    if p not in sys.path:
        sys.path.insert(0, p)
    return p


if __name__ == "__main__":
    for name in ("data_dir", "fixtures", "runs_dir", "logs", "dbc_dir",
                 "canre_root"):
        try:
            print("%-11s %s" % (name, globals()[name]()))
        except PathsError as e:
            print("%-11s ERROR: %s" % (name, e))
