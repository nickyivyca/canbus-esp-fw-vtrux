"""Where the reverse-it project folder is, for the tools in this folder.

The project folder (the DBCs, the captures) is a SEPARATE tree from this
firmware repo. Its canre/ still marks it, but since the canre-pin step
(2026-10-26) the tools take canre from the pin, ../../tools/bench/canre_pin.py,
not from it. The folder is wherever that machine's SeaDrive or Seafile client
syncs it (each worktree's local-paths.md records the value). So there is no
default path (methodology, 2026-10-20): it
comes from --project-dir, else from $REVERSE_IT_DIR, and neither is an error
that names the variable. A default that missed used to fail later as
"No module named 'canre'", which points at Python rather than at the setting.

$REVERSE_IT_DIR replaces $GEN_INHIBIT_REPO here (and VTRUX_DATA in the
interposer's tools); under WSL it crosses over with WSLENV=REVERSE_IT_DIR/p.
"""

import os

VAR = "REVERSE_IT_DIR"
EXAMPLE = "/path/to/reverse-it"     # a placeholder: no real machine's path (user, 2026-10-23)


class ProjectDirError(Exception):
    pass


def resolve(given=None, environ=None):
    """The project folder, from `given` (--project-dir), else $REVERSE_IT_DIR.

    Raises ProjectDirError, naming the variable, when neither is set, when the
    path does not exist, or when it has no canre/ (not the project folder: a
    capture replayed through the wrong parser still produces a .scn file, and
    the only symptom is that it disagrees with the goldens later, which reads
    as a firmware change).
    """
    environ = os.environ if environ is None else environ
    src = "--project-dir" if given else "$" + VAR
    p = given or environ.get(VAR, "")
    if not p:
        raise ProjectDirError(
            "no project folder: pass --project-dir <reverse-it root> or set %s,\n"
            "  e.g. export %s=%s (under WSL also WSLENV=%s/p)" % (VAR, VAR, EXAMPLE, VAR))
    p = os.path.expanduser(p)
    if not os.path.isdir(p):
        raise ProjectDirError("%s '%s' does not exist (set %s to the reverse-it root)" % (src, p, VAR))
    if not os.path.isdir(os.path.join(p, "canre")):
        raise ProjectDirError("%s '%s' has no canre/, so it is not the reverse-it root" % (src, p))
    return p
