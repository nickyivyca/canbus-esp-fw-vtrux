"""The project's board, for R21, R29 and R30: the one shared/boards/* component in dependencies.lock.

SPEC R21 (shared/main 2f72a20) and shared/boards/SPEC.md B2 and B4. A project names its board
by its dependency and nowhere else. The board is the one dependencies.lock entry whose source is
a shared/boards/<board> folder: a git source whose path is exactly shared/boards/<board>, or a
local one (a path: or override_path: dependency) whose path ends in shared/boards/<board>. The
checks fail unless there is exactly one, and unless it is downloaded (git) into
managed_components/, where its data is read, so the board checked is the board built.

The board's data, board.json in the component's folder (B4, the tester's):

    {"target": "<IDF_TARGET>",
     "flash_size": "<as ESP-IDF names it: 4MB>",
     "partition_table": "<the reference partition-table .bin, from the board's folder>",
     "required": [{"setting": "<what the board leaves to the project>", "option": "CONFIG_<NAME>"},
                  {"setting": "...", "choice": ["CONFIG_<A>", "CONFIG_<B>", ...]}],
     "hardware_name": "<the hardware name>"}

"required" lists the settings required of the project (B3.2). One that is a single ESP-IDF
option names it; one that is a Kconfig choice lists the choice's members on the board's target.
No hash is stored beside the reference table (git holds it) and no slot (R21: the smallest app
partition of the reference table).

The board's sdkconfig.board holds the settings fixed by the board (B3.1), in ESP-IDF's sdkconfig
form: CONFIG_<NAME>=y, a number (decimal or 0x hex), a quoted string, or
"# CONFIG_<NAME> is not set".
"""

import json
import os
import re

from vtrux_build import lockfile

BOARD_JSON = "board.json"
SDKCONFIG_BOARD = "sdkconfig.board"
KEYS = ("target", "flash_size", "partition_table", "required", "hardware_name")
UNSET = "unset"
_GIT_PATH = re.compile(r"^shared/boards/[^/]+$")
_LOCAL_PATH = re.compile(r"(?:^|/)shared/boards/[^/]+$")
_TARGET = re.compile(r"^[a-z0-9]+$")
_OPTION = re.compile(r"^CONFIG_[A-Z0-9_]+$")
_SET = re.compile(r"^(CONFIG_[A-Z0-9_]+)=(.*)$")
_NOT_SET = re.compile(r"^# (CONFIG_[A-Z0-9_]+) is not set$")
_INT = re.compile(r"^-?(?:0[xX][0-9A-Fa-f]+|\d+)$")


class BoardError(ValueError):
    pass


def _source_path(entry):
    return entry.get("source", {}).get("path", "").replace("\\", "/").rstrip("/")


def is_board(entry):
    """Whether a dependencies.lock entry is a shared/boards/* component."""
    s = entry.get("source", {})
    p = _source_path(entry)
    if p.startswith("./"):
        p = p[2:]
    if s.get("type") == "git":
        return bool(_GIT_PATH.match(p))
    return s.get("type") == "local" and bool(_LOCAL_PATH.search(p))


def find(project):
    """(lock name, folder) of the project's board in managed_components/; BoardError unless exactly one."""
    lock = os.path.join(project, "dependencies.lock")
    try:
        entries = lockfile.read(lock)
    except (OSError, lockfile.LockError) as e:
        raise BoardError("dependencies.lock cannot be read (%s)" % e)
    boards = sorted(n for n, e in entries.items() if is_board(e))
    if len(boards) != 1:
        raise BoardError("dependencies.lock has %d shared/boards/* components%s; a release build needs exactly one"
                         % (len(boards), (": " + ", ".join(boards)) if boards else ""))
    name = boards[0]
    src = entries[name]["source"]
    if src.get("type") != "git":
        raise BoardError("the board %s comes from a local folder (%s), not downloaded into managed_components/"
                         % (name, _source_path(entries[name])))
    folder = os.path.join(project, "managed_components", lockfile.managed_dir_name(name))
    if not os.path.isdir(folder):
        raise BoardError("the board %s is not in managed_components/ (%s)" % (
            name, os.path.relpath(folder, project).replace(os.sep, "/")))
    return name, folder


def _option(v, what):
    if not isinstance(v, str) or not _OPTION.match(v):
        raise BoardError("%s: %r is not a CONFIG_ option name" % (what, v))
    return v


def read(folder):
    """board.json from a board's folder, checked; the reference table's path made absolute."""
    p = os.path.join(folder, BOARD_JSON)
    try:
        with open(p, encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, ValueError) as e:
        raise BoardError("the board's %s cannot be read (%s)" % (BOARD_JSON, e))
    if not isinstance(b, dict) or set(b) != set(KEYS):
        raise BoardError("the board's %s needs exactly %s, has %s" % (
            BOARD_JSON, ", ".join('"%s"' % k for k in KEYS), sorted(b) if isinstance(b, dict) else type(b).__name__))
    if not isinstance(b["target"], str) or not _TARGET.match(b["target"]):
        raise BoardError("%s: target %r is not an IDF_TARGET name" % (BOARD_JSON, b["target"]))
    if not isinstance(b["flash_size"], str) or not b["flash_size"]:
        raise BoardError("%s: flash_size %r is not a size name" % (BOARD_JSON, b["flash_size"]))
    ref = b["partition_table"]
    if not isinstance(ref, str) or not ref or os.path.isabs(ref) or ".." in re.split(r"[\\/]", ref):
        raise BoardError("%s: partition_table %r is not a path inside the board's folder" % (BOARD_JSON, ref))
    if not isinstance(b["hardware_name"], str) or not b["hardware_name"]:
        raise BoardError("%s: hardware_name %r is not a name" % (BOARD_JSON, b["hardware_name"]))
    req = b["required"]
    if not isinstance(req, list):
        raise BoardError("%s: required is not a list" % BOARD_JSON)
    required, seen = [], set()
    for i, r in enumerate(req):
        what = "%s required[%d]" % (BOARD_JSON, i)
        if not isinstance(r, dict) or len(r) != 2 or not isinstance(r.get("setting"), str) or not r["setting"] \
                or not ({"option", "choice"} & set(r)):
            raise BoardError("%s: needs a \"setting\" and one of \"option\" or \"choice\", has %r" % (what, r))
        if "option" in r:
            opts = [_option(r["option"], what)]
        else:
            if not isinstance(r["choice"], list) or len(r["choice"]) < 2:
                raise BoardError("%s: choice needs a list of at least two options" % what)
            opts = [_option(o, what) for o in r["choice"]]
        if seen & set(opts) or len(set(opts)) != len(opts):
            raise BoardError("%s: names an option twice" % what)
        seen |= set(opts)
        required.append({"setting": r["setting"], "kind": "option" if "option" in r else "choice", "options": opts})
    return {"target": b["target"], "flash_size": b["flash_size"],
            "partition_table": os.path.normpath(os.path.join(folder, ref)),
            "required": required, "hardware_name": b["hardware_name"]}


def _value(raw, where):
    if raw == "y":
        return True
    if _INT.match(raw):
        return int(raw, 0)
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return re.sub(r'\\(.)', r"\1", raw[1:-1])
    raise BoardError("%s: value %r is not y, a number or a quoted string" % (where, raw))


def read_sdkconfig_board(folder):
    """{CONFIG_<NAME>: True | int | str | "unset"} from the board's sdkconfig.board."""
    p = os.path.join(folder, SDKCONFIG_BOARD)
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        raise BoardError("the board's %s cannot be read (%s)" % (SDKCONFIG_BOARD, e))
    out = {}
    for n, ln in enumerate(lines, 1):
        s = ln.strip()
        where = "%s line %d" % (SDKCONFIG_BOARD, n)
        m, u = _SET.match(s), _NOT_SET.match(s)
        if m:
            name, v = m.group(1), _value(m.group(2), where)
        elif u:
            name, v = u.group(1), UNSET
        elif not s or s.startswith("#"):
            continue
        else:
            raise BoardError("%s: %r is not an sdkconfig line" % (where, s))
        if name in out:
            raise BoardError("%s: %s is set twice" % (where, name))
        out[name] = v
    if not out:
        raise BoardError("the board's %s sets nothing" % SDKCONFIG_BOARD)
    return out
