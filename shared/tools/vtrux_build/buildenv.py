"""A release build's environment (SPEC R24), built from an empty one.

Only the names on PASS for this host OS come from the caller: what ESP-IDF
needs to find itself and its tools, and what the host OS needs to run
programs. Every other name is dropped, so nothing set in the caller's
environment (EXTRA_CFLAGS, CFLAGS, IDF_TARGET, a Component Manager setting,
PYTHONPATH, ...) reaches the build. The list says what passes rather than
what is refused, so a route nobody listed is closed too. A name the build
needs and the list lacks shows as a failed build, not a different one.

Then the tooling adds its own settings, which win over the caller's:

    IDF_COMPONENT_STRICT_CHECKSUM=1   R10, the Component Manager's strict mode
    SOURCE_DATE_EPOCH=<EPOCH>         R9, the compile date (see EPOCH)
    IDF_TOOLS_PATH                    only when the caller's is unset or
                                      empty: the default ESP-IDF itself
                                      uses (~/.espressif), resolved here, so
                                      the build finds the tools R9's
                                      toolchain check reads without a home
                                      folder

    env, passed, dropped = environment(os.environ)

passed and dropped are sorted names, never values: the record lists passed
(R13, R24) and the build script prints dropped.
"""

import os

from . import components

# R9: one fixed SOURCE_DATE_EPOCH for every project and commit, so that
# source using __DATE__ or __TIME__ builds the same each time (GCC takes
# both from it; ESP-IDF v5.4.1 never sets it). A stopgap for the Arduino
# core the interposer compiles: remove it when no project compiles that
# core. 2026-01-01T00:00:00Z, the value the interposer's PlatformIO build
# uses (the two need not match).
EPOCH = "1767225600"

# What ESP-IDF needs to find itself and its tools, on every host OS, as
# the export sets them. PATH holds the exported tool folders (toolchain,
# CMake, Ninja, the IDF venv); idf.py checks that it runs in
# IDF_PYTHON_ENV_PATH's venv; the configure reads ESP_ROM_ELF_DIR
# unconditionally (tools/cmake/gdbinit.cmake) and fails without it.
IDF_NAMES = ("PATH", "IDF_PATH", "IDF_PYTHON_ENV_PATH", "IDF_TOOLS_PATH",
             "ESP_ROM_ELF_DIR")

# What the host OS needs to run programs. Found on NICKY-XPS by removing
# names until a configure and build of a small project failed, each one
# failing without the name it notes (2026-10-24); the build with only these
# gave the same image as one with the whole exported environment.
HOST_NAMES = {
    # SYSTEMROOT: Python's socket module (asyncio, which idf.py imports)
    # cannot load without it. PROCESSOR_ARCHITECTURE: Python's
    # platform.machine() reads it, and idf_tools.py refuses an unknown
    # platform. TEMP, TMP: GCC's temporary files (without them it tries
    # C:\WINDOWS\ and fails); Python's tempfile reads them too.
    "nt": ("SYSTEMROOT", "PROCESSOR_ARCHITECTURE", "TEMP", "TMP"),
    # Not tried on a Linux host yet. Python and GCC need nothing from the
    # environment there but the temporary folder, if the caller moves it
    # from /tmp.
    "posix": ("TMPDIR",),
}


def pass_list(os_name=None):
    """The names a release build takes from its caller on this host OS."""
    return IDF_NAMES + HOST_NAMES[os_name or os.name]


def environment(environ=None, os_name=None, default_tools_path=None):
    """(env, passed, dropped) for a release build's ESP-IDF calls.
    environ is the caller's environment (default: this process's).
    default_tools_path is IDF_TOOLS_PATH when the caller sets none
    (default: ~/.espressif, as idf_tools.py has it)."""
    environ = os.environ if environ is None else environ
    os_name = os_name or os.name
    # Windows names are case-insensitive: compare them upper-cased.
    key = (lambda n: n.upper()) if os_name == "nt" else (lambda n: n)
    wanted = {key(n) for n in pass_list(os_name)}
    env, passed, dropped = {}, [], []
    for name, value in environ.items():
        if key(name) in wanted:
            env[name] = value
            passed.append(name)
        else:
            dropped.append(name)
    tools = [n for n in env if key(n) == "IDF_TOOLS_PATH"]
    if not (tools and env[tools[0]]):
        for n in tools:
            del env[n]
        env["IDF_TOOLS_PATH"] = (default_tools_path
                                 or os.path.join(os.path.expanduser("~"),
                                                 ".espressif"))
    env[components.STRICT_ENV] = "1"
    env["SOURCE_DATE_EPOCH"] = EPOCH
    return env, sorted(passed, key=key), sorted(dropped, key=key)
