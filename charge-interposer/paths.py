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
                      interposer-firmware/`, `.../interposer-runs/`). There
                      is no default: unset is a clear error, never a guess.
  VTRUX_PUBLIC_REPO   Optional. The public canbus-reveng-vtrux-coda clone,
                      which supplies the vehicle DBCs. Default: the sibling
                      directory `../canbus-reveng-vtrux-coda` of this repo.

REVERSE_IT_DIR replaced VTRUX_DATA (which named projects/vtrux) on
2026-10-20: one variable for both firmware projects (user, via the
methodology session). VTRUX_DATA is retired and never read; set alone it is
refused with a message saying so, so an old environment fails loudly instead
of resolving somewhere else.

canre comes from the public repo at the commit pins.json pins, installed
and checked by canre_pin.py (port step 0 for the interposer, reviewer,
2026-10-26). It carries both parser fixes (signed BUSMASTER timestamp
components and midnight rollover) that kept canre on SeaDrive's copy from
2026-10-08 until then. SeaDrive's canre is never read.

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


def code_dir():
    """charge-interposer/, the folder this file is in: the code a script in
    this tree tests, found from its own location since the scripts moved
    into git (port step 0, reviewer 2026-10-26; port plan goal 20 item 9).
    INTERPOSER_CODE is not needed; if it is set, it must name this same
    folder, or it is refused rather than one tree tested with another's
    scripts."""
    v = os.environ.get("INTERPOSER_CODE", "").strip()
    if v and os.path.normcase(os.path.realpath(v)) != \
            os.path.normcase(os.path.realpath(_HERE)):
        raise PathsError(
            "INTERPOSER_CODE=%s is not this tree's charge-interposer/ (%s): "
            "a script tests the tree it is in; unset INTERPOSER_CODE or run "
            "the other tree's copy" % (v, _HERE))
    return _HERE


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


def _canre_pin():
    """canre_pin.py beside this file."""
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import canre_pin
    return canre_pin


def canre_root():
    """The directory holding canre/: the pinned install (canre_pin.py), not
    SeaDrive's. Checked, not imported."""
    canre_pin = _canre_pin()
    try:
        d = canre_pin.folder()
        canre_pin.verify(d)
    except canre_pin.CanrePinError as e:
        raise PathsError(str(e))
    return d


def add_canre():
    """Put the pinned canre first on sys.path and import it
    (canre_pin.use: all four checks). Refuses any other canre."""
    canre_pin = _canre_pin()
    try:
        return canre_pin.use()
    except canre_pin.CanrePinError as e:
        raise PathsError(str(e))


if __name__ == "__main__":
    for name in ("reverse_it_dir", "data_dir", "fixtures", "runs_dir", "logs", "dbc_dir",
                 "canre_root"):
        try:
            print("%-14s %s" % (name, globals()[name]()))
        except PathsError as e:
            print("%-14s ERROR: %s" % (name, e))
