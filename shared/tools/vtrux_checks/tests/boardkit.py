"""A project's board for the checks' tests: a dependencies.lock naming a shared/boards/* component,
and the component's folder in managed_components/ with its board.json and sdkconfig.board.

    lock(project, entries)                 dependencies.lock with these {name: source} entries
    board(project, data=None, sdkconfig=None, name=NAME)
                                           the lock with one git board, and its folder
"""

import json
import os

NAME = "vtrux/board_a"
GIT = {"type": "git", "git": "https://example.invalid/vtrux.git", "path": "shared/boards/board_a"}
IDF = {"type": "idf"}
SDKCONFIG = ('# fixed by the board\nCONFIG_FIXED_ON=y\nCONFIG_FIXED_HEX=0x1000\nCONFIG_FIXED_STR="a \\"b\\""\n'
             '# CONFIG_FIXED_OFF is not set\nCONFIG_APP_ONLY=y\n')
# The options a build of this board has for SDKCONFIG, in every folder; APP_ONLY only in the app's.
FIXED = {"FIXED_ON": True, "FIXED_HEX": 0x1000, "FIXED_STR": 'a "b"', "FIXED_OFF": False}
REQUIRED = [{"setting": "a single option", "option": "CONFIG_REQ"},
            {"setting": "a choice", "choice": ["CONFIG_CON_A", "CONFIG_CON_B", "CONFIG_CON_C"]}]
# The required options' values in a build whose profile names REQ true and CON_B.
REQ_BUILD = {"REQ": True, "CON_A": False, "CON_B": True, "CON_C": False}


def data(**kw):
    d = {"target": "chipa", "flash_size": "XMB", "partition_table": "ref/pt.bin", "required": REQUIRED,
         "hardware_name": "A"}
    d.update(kw)
    return d


def lock(project, entries):
    lines = ["dependencies:", "  idf:", "    source:", "      type: idf", "    version: 5.4.1"]
    for name, src in entries.items():
        lines += ["  %s:" % name, "    component_hash: %s" % ("0" * 64), "    dependencies:", "    - name: idf",
                  "      require: private", "      version: '>=5.0'", "    source:"]
        lines += ["      %s: %s" % (k, v) for k, v in sorted(src.items())]
        lines += ["    version: 2026.10.27"]
    lines += ["direct_dependencies:"] + ["- %s" % n for n in entries] + ["manifest_hash: %s" % ("1" * 64),
                                                                        "target: chipa", "version: 2.0.0", ""]
    with open(os.path.join(project, "dependencies.lock"), "w", newline="\n") as f:
        f.write("\n".join(lines))


def folder(project, name=NAME):
    return os.path.join(project, "managed_components", name.replace("/", "__", 1))


def board(project, board_data=None, sdkconfig=SDKCONFIG, name=NAME):
    """The lock naming one git board, and its folder; returns the folder."""
    lock(project, {name: dict(GIT, path="shared/boards/" + name.split("/")[-1])})
    d = folder(project, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "board.json"), "w", newline="\n") as f:
        json.dump(board_data if board_data is not None else data(), f)
    if sdkconfig is not None:
        with open(os.path.join(d, "sdkconfig.board"), "w", newline="\n") as f:
            f.write(sdkconfig)
    return d
