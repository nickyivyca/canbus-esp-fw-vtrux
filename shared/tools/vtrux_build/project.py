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

Other keys are neither rejected nor read. The build checks' data (the
release profile, the switch table and the board's data) is not kept here but
in vtrux_checks.json, which the tester owns.
"""

import json
import os
import re

DATA_FILE = "vtrux_build.json"
_PROJECT = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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
