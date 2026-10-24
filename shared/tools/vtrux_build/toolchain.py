"""The compilers a release build may use (SPEC R9): this IDF install's own.

ccache can run every compile with no launcher in the compile commands: a
copy, hardlink or link of it, or a wrapper script, named after the compiler
and first on PATH becomes the compiler CMake chooses (ccache's masquerade
mode). Looking for ccache cannot see a copy or a wrapper, so the check is
positive instead: each compiler CMake chose must be the toolchain's own
binary.

    toolchain file  the build folder's CMAKE_TOOLCHAIN_FILE (CMakeCache.txt),
                    which must be in $IDF_PATH/tools/cmake/, names each
                    language's compiler: set(CMAKE_<LANG>_COMPILER <name>)
    tool folders    for every tool in $IDF_PATH/tools/tools.json, at the
                    version it marks "recommended", each of its export_paths
                    under <IDF_TOOLS_PATH>/tools/<tool>/<version>/
                    (IDF_TOOLS_PATH unset: ~/.espressif, as idf_tools.py)
    the compiler    exactly one tool folder holds a file of the name the
                    toolchain file gives (".exe" allowed); the compiler
                    CMake chose (CMake<LANG>Compiler.cmake) has that name,
                    and with its links resolved it is a regular file in that
                    folder

Not covered: a toolchain install whose own files were replaced. The check
trusts the IDF install, as the build does.
"""

import json
import os
import re

_SET_COMPILER = re.compile(
    r'^\s*set\(\s*CMAKE_(C|CXX|ASM)_COMPILER\s+"?([^\s")]+)"?\s*\)', re.M)


class ToolchainError(Exception):
    pass


def _key(path):
    return os.path.normcase(os.path.normpath(path))


class Toolchain:
    def __init__(self, idf_path, tools_path):
        self.idf_path = os.path.abspath(idf_path)
        self.tools_path = os.path.abspath(tools_path)
        p = os.path.join(self.idf_path, "tools", "tools.json")
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ToolchainError("this IDF's tool list %s cannot be read: %s"
                                 % (p, e))
        # (tool, version, folder) for every recommended version's export path
        self.folders = []
        for t in data.get("tools", []):
            for v in t.get("versions", []):
                if v.get("status") != "recommended":
                    continue
                for ep in t.get("export_paths", []):
                    self.folders.append((t["name"], v["name"], os.path.normpath(
                        os.path.join(self.tools_path, "tools", t["name"],
                                     v["name"], *ep))))

    @classmethod
    def from_environ(cls, environ=None):
        env = os.environ if environ is None else environ
        idf = env.get("IDF_PATH")
        if not idf:
            raise ToolchainError("IDF_PATH is not set")
        tools = (env.get("IDF_TOOLS_PATH")
                 or os.path.expanduser(os.path.join("~", ".espressif")))
        return cls(idf, tools)

    def compiler_names(self, toolchain_file):
        """{language: compiler name} the toolchain file sets. It must be one
        of this IDF's, in $IDF_PATH/tools/cmake/."""
        here = os.path.dirname(os.path.realpath(toolchain_file))
        ours = os.path.realpath(os.path.join(self.idf_path, "tools", "cmake"))
        if _key(here) != _key(ours):
            raise ToolchainError("the toolchain file %s is not one of this "
                                 "IDF's (%s)" % (toolchain_file, ours))
        try:
            with open(toolchain_file, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise ToolchainError("the toolchain file %s cannot be read: %s"
                                 % (toolchain_file, e))
        return dict(_SET_COMPILER.findall(text))

    def folder_for(self, name):
        """(tool, version, folder): the one tool folder holding name, or
        name.exe."""
        hits = [(tool, ver, f) for tool, ver, f in self.folders
                if any(os.path.isfile(os.path.join(f, n))
                       for n in (name, name + ".exe"))]
        if not hits:
            raise ToolchainError(
                "no tool of this IDF holds %s at the version its tools.json "
                "recommends, under %s: the toolchain this IDF uses is not "
                "installed" % (name, os.path.join(self.tools_path, "tools")))
        if len(hits) > 1:
            raise ToolchainError(
                "%s is in %d tool folders (%s), so which is the toolchain's "
                "own is not known" % (name, len(hits),
                                      ", ".join(f for _, _, f in hits)))
        return hits[0]

    def check(self, name, chosen):
        """(tool, version, folder) if chosen, the compiler CMake chose, is
        the toolchain's own name; else ToolchainError."""
        tool, ver, folder = self.folder_for(name)
        if _key(os.path.basename(chosen)) not in (_key(name),
                                                   _key(name + ".exe")):
            raise ToolchainError("it is %s, not the %s the toolchain file "
                                 "names" % (chosen, name))
        real = os.path.realpath(chosen)
        if not os.path.isfile(real):
            raise ToolchainError("it is %s, which is not a file" % chosen)
        if _key(os.path.dirname(real)) != _key(os.path.realpath(folder)):
            shown = chosen if _key(real) == _key(chosen) else (
                "%s (resolved: %s)" % (chosen, real))
            raise ToolchainError(
                "it is %s, not the toolchain's own %s in %s (%s %s). A copy, "
                "link or wrapper of ccache named after the compiler runs "
                "every compile through ccache" % (shown, name, folder, tool,
                                                  ver))
        return tool, ver, folder
