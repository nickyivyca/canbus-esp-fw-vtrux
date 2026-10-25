"""Where the charge interposer finds what is not in this repo.

Added by the charge-interposer tester, 2026-10-08, in the path-fix commit
after the move from SeaDrive (reviewer's rulings of the same day). Before the
move every file reached its neighbours by counting `..` up the SeaDrive tree
(projects/vtrux/tools/interposer/); in this repo those neighbours are
elsewhere, so every such path goes through this module instead, and nothing
machine-absolute is committed.

Two settings, both environment variables:

  REVERSE_IT_DIR      REQUIRED for anything that reads data. The absolute
                      path of SeaDrive's reverse-it root on this machine (the
                      folder holding canre/ and projects/). From it come the
                      captures (`projects/vtrux/logs/`), the harness
                      fixtures -- traces, goldens, golden_regen_log.txt,
                      l3_stimulus.txt, the mutation scripts and the run
                      folders (`projects/vtrux/notes/artifacts/
                      interposer-firmware/`, `.../interposer-runs/`) -- and,
                      for now, `canre`. There is no default: unset is a
                      clear error, never a guess.
  VTRUX_PUBLIC_REPO   Optional. The public canbus-reveng-vtrux-coda clone,
                      which supplies the vehicle DBCs. Default: the sibling
                      directory `../canbus-reveng-vtrux-coda` of this repo.

REVERSE_IT_DIR replaced VTRUX_DATA (which named projects/vtrux) on
2026-10-20: one variable for both firmware projects (user, via the
methodology session). VTRUX_DATA is retired and never read; set alone it is
refused with a message saying so, so an old environment fails loudly instead
of resolving somewhere else.

canre comes from SeaDrive, not the public clone, because the public copy
(1bd6d1a) predates SeaDrive's parser fixes for signed BUSMASTER timestamp
components and midnight rollover, and regress must read the captures with the
parser E1 ran on (reviewer, 2026-10-08). Switch canre_root() to the public
repo once it is synced.

The interposer diag DBC is in this repo (diag/), so diag_dbc() needs no
setting; it is here so that every reader names one path.

Under WSL, pass the Windows value through with WSLENV=REVERSE_IT_DIR/p, which
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


def reverse_it_dir():
    """SeaDrive's reverse-it root, from REVERSE_IT_DIR."""
    v = os.environ.get("REVERSE_IT_DIR", "").strip()
    if not v:
        retired = ""
        if os.environ.get("VTRUX_DATA", "").strip():
            retired = (" (VTRUX_DATA is set, but it was retired on "
                       "2026-10-20 and is not read: set REVERSE_IT_DIR to "
                       "the folder two levels above it)")
        raise PathsError(
            "REVERSE_IT_DIR is not set%s. Set it to the absolute path of "
            "SeaDrive's reverse-it root on this machine (canre/ and "
            "projects/vtrux/ inside it). Under WSL pass it with "
            "WSLENV=REVERSE_IT_DIR/p. See charge-interposer/paths.py."
            % retired)
    v = os.path.normpath(v)
    if not os.path.isdir(os.path.join(v, "projects", "vtrux", "notes",
                                      "artifacts")):
        raise PathsError(
            "REVERSE_IT_DIR=%s has no projects/vtrux/notes/artifacts/ -- it "
            "must be SeaDrive's reverse-it root" % v)
    return v


def data_dir():
    """SeaDrive's projects/vtrux, under REVERSE_IT_DIR."""
    return os.path.join(reverse_it_dir(), "projects", "vtrux")


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


def diag_dbc():
    """The interposer diag DBC: the git copy in diag/ beside this file, the
    published interface from schema 4 (user, 2026-10-19). Needs no setting.
    The reverse-it copy, projects/vtrux/vtrux-interposer-diag.dbc, is not
    read: the readers in this repo moved here (reviewer, 2026-10-24)."""
    p = os.path.join(_HERE, "diag", "vtrux-interposer-diag.dbc")
    if not os.path.isfile(p):
        raise PathsError("no diag DBC at %s" % p)
    return p


def canre_root():
    """The directory holding canre/ -- SeaDrive's reverse-it root, for now."""
    p = reverse_it_dir()
    if not os.path.isdir(os.path.join(p, "canre")):
        raise PathsError("no canre/ at %s (REVERSE_IT_DIR)" % p)
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
    for name in ("reverse_it_dir", "data_dir", "fixtures", "runs_dir", "logs", "dbc_dir",
                 "canre_root"):
        try:
            print("%-14s %s" % (name, globals()[name]()))
        except PathsError as e:
            print("%-14s ERROR: %s" % (name, e))
