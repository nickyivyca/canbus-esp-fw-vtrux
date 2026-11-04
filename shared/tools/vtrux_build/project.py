"""A project's build data (SPEC R22): what the tooling needs to know about one
project, kept in that project's root as vtrux_build.json. The tooling has no
project names or project facts of its own.

The keys the build script reads:

    project            the record folder's name, firmware/builds/<project>/
                       (lower case letters, digits and "-")
    shared_components  the dependency names, as idf_component.yml and
                       dependencies.lock spell them, of the shared firmware
                       components the project pins (goal 12). R16 refuses a
                       release build that takes one from a local folder, and
                       R13 records the version each is pinned at. [] when it
                       pins none.

The key the dev build command reads (R31), and only it, through
variants():

    variants           {variant: {CMake option: value}}: the project's bench
                       and dev build variants, each with the -D options it
                       is configured with, e.g.
                       {"truck": {"INTP_VARIANT": "truck"}}. A variant name
                       is also its build folder's, build/<variant>. Absent
                       or {}: the project has no build command.

Other keys are neither rejected nor read. A release build does not read
variants, so a fault in it does not stop one. The build checks' data (the
release profile, the switch table and the board's data) is not kept here but
in vtrux_checks.json, which the tester owns.
"""

import json
import os
import re

DATA_FILE = "vtrux_build.json"
_PROJECT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# A variant names a folder under build/: no separators, no leading dot.
_VARIANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
# A CMake variable name, as -D sets it.
_OPTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProjectError(ValueError):
    pass


class Project:
    def __init__(self, root, data):
        self.root = os.path.abspath(root)
        self.data = data
        name = data.get("project")
        if not isinstance(name, str) or not _PROJECT.match(name):
            raise ProjectError("%s: 'project' must be lower case letters, "
                               "digits and '-', not %r" % (DATA_FILE, name))
        shared = data.get("shared_components")
        if (not isinstance(shared, list)
                or not all(isinstance(s, str) and s for s in shared)
                or len(set(shared)) != len(shared)):
            raise ProjectError("%s: 'shared_components' must be a list of "
                               "distinct dependency names ([] for none)"
                               % DATA_FILE)
        self.name = name
        self.shared_components = list(shared)

    def variants(self):
        """R31: {variant: {option: value}}, in the file's order; {} if the
        project lists none. ProjectError if the key is malformed."""
        data = self.data.get("variants", {})
        if not isinstance(data, dict):
            raise ProjectError("%s: 'variants' must be an object of variant "
                               "names, not %r" % (DATA_FILE, data))
        out = {}
        for name, opts in data.items():
            if not _VARIANT.match(name):
                raise ProjectError("%s: the variant name %r is not letters, "
                                   "digits, '_' and '-' (it names the build "
                                   "folder build/<variant>)"
                                   % (DATA_FILE, name))
            if not isinstance(opts, dict):
                raise ProjectError("%s: variant %r must be an object of CMake "
                                   "options, not %r" % (DATA_FILE, name, opts))
            for opt, value in opts.items():
                if not _OPTION.match(opt):
                    raise ProjectError("%s: variant %r: %r is not a CMake "
                                       "variable name" % (DATA_FILE, name, opt))
                if (not isinstance(value, str) or "\n" in value
                        or "\r" in value):
                    raise ProjectError("%s: variant %r: %s must be a one-line "
                                       "string, not %r"
                                       % (DATA_FILE, name, opt, value))
            out[name] = dict(opts)
        return out

    @classmethod
    def load(cls, root):
        path = os.path.join(root, DATA_FILE)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except OSError as e:
            raise ProjectError("no project build data: %s (%s)" % (path, e))
        except ValueError as e:
            raise ProjectError("%s is not valid JSON: %s" % (path, e))
        if not isinstance(data, dict):
            raise ProjectError("%s is not a JSON object" % path)
        return cls(root, data)
