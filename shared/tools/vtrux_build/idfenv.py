"""The IDF environment check (SPEC R32).

Before a release build or a dev build (R31) hands anything to ESP-IDF, the
environment it was started in must be one exported for a single ESP-IDF
install:

    IDF_PATH             set
    IDF_TOOLS_PATH       set and not empty
    IDF_PYTHON_ENV_PATH  set, and inside IDF_TOOLS_PATH
    ESP_ROM_ELF_DIR      set, and inside IDF_TOOLS_PATH

A failure is a refusal naming the variable. Nothing is defaulted: an unset
IDF_TOOLS_PATH is never replaced by ~/.espressif. With two installs on one
machine, each with its own tools folder, that default would build with the
other install's Python environment and tools. The two "inside" checks catch
a shell exported with one IDF_TOOLS_PATH and then given another.

Not covered: an IDF_PATH of one version with the other's tools folder. The
project's exact IDF version (R33) refuses that, at configure.

The form is the charge interposer's launcher's check_idf_env
(charge-interposer/vtrux_tools.py, charge-interposer/main 3c03edf), with
IDF_PATH added.

    values = check(os.environ)      # {name: value}, or EnvError
"""

import os

TOOLS = "IDF_TOOLS_PATH"
INSIDE_TOOLS = ("IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR")
NAMES = ("IDF_PATH", TOOLS) + INSIDE_TOOLS


class EnvError(ValueError):
    """The environment fails R32. str() names the variable."""


def inside(path, folder):
    """True if path is folder or under it, compared as this host OS
    compares paths (case-insensitively on Windows). Another drive is not
    inside."""
    path = os.path.normcase(os.path.abspath(path))
    folder = os.path.normcase(os.path.abspath(folder))
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:
        return False


def check(environ=None):
    """{name: value} of the four variables if environ (default: this
    process's environment) passes R32; else EnvError naming the first that
    fails."""
    environ = os.environ if environ is None else environ
    if not environ.get("IDF_PATH", ""):
        raise EnvError("IDF_PATH is %s: run this from a shell exported for "
                       "the project's ESP-IDF (R32)"
                       % ("empty" if "IDF_PATH" in environ else "not set"))
    tools = environ.get(TOOLS, "")
    if not tools.strip():
        raise EnvError(
            "%s is %s. Each ESP-IDF install has its own tools folder: set "
            "%s to the project's before the export (local-paths.md). It is "
            "never defaulted to ~/.espressif, which may be another install's "
            "(R32)" % (TOOLS, "empty" if TOOLS in environ else "not set",
                       TOOLS))
    for name in INSIDE_TOOLS:
        value = environ.get(name, "")
        if not value:
            raise EnvError("%s is %s: run this from a shell exported for the "
                           "project's ESP-IDF (R32)"
                           % (name, "empty" if name in environ else "not set"))
        if not inside(value, tools):
            raise EnvError(
                "%s is %s, not inside %s %s: this shell was exported with "
                "another %s (R32)" % (name, value, TOOLS, tools, TOOLS))
    return {n: environ[n] for n in NAMES}
