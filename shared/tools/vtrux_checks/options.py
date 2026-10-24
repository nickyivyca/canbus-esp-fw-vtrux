"""R18, options: a release build's options against the project's release profile.

    python -B -m vtrux_checks options --build <project>/build

SPEC R18 (shared/main b5d5477). The build's options are config/sdkconfig.json
in every CMake build folder under build/ (the app's and the bootloader's), as
vtrux_build's BuildDir.options() reads them. The release profile is the
"release_profile" object in vtrux_checks.json in the project root
(project_description.json's project_path):

    {"release_profile": {
        "CONFIG_<NAME>": {"value": <value>, "spec": "<the project spec line it comes from>"},
        ...}}

A value is true, an integer, a string, or "unset". Every option the profile
names must have the profile's value in every build folder: "unset" passes
when the option is absent or off (false); any other value must be present
with that value and type (true is not 1). Options the profile does not name
are free. The whole sdkconfig is not compared: R8 holds the build to the
committed one.

false is refused in the profile: write "unset", which is what the spec means
by an option being off. Every entry needs a non-empty "spec"; an entry with
any other key is refused, so a misspelt "value" cannot pass silently.

Release builds only; bench and dev builds are not checked. The check does
not tell them apart: run it on a finished release build.
"""

import json
import os
import re

from vtrux_build.builddir import BuildDir, BuildDirError

from .scope import build_folders

DATA_FILE = "vtrux_checks.json"
KEY = "release_profile"
UNSET = "unset"
_NAME = re.compile(r"^CONFIG_[A-Z0-9_]+$")
ABSENT = object()


class OptionsError(ValueError):
    pass


def read_profile(project):
    """{option name: (value, spec line)} from the project's vtrux_checks.json."""
    p = os.path.join(project, DATA_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise OptionsError("%s cannot be read (%s)" % (DATA_FILE, e))
    prof = data.get(KEY) if isinstance(data, dict) else None
    if not isinstance(prof, dict) or not prof:
        raise OptionsError("%s has no %r object naming at least one option" % (DATA_FILE, KEY))
    bad, out = [], {}
    for name, e in prof.items():
        if not _NAME.match(name):
            bad.append("%r is not an option name (CONFIG_ and upper case)" % name)
            continue
        if not isinstance(e, dict) or set(e) != {"value", "spec"}:
            bad.append("%s: needs exactly \"value\" and \"spec\", has %s" % (
                name, sorted(e) if isinstance(e, dict) else type(e).__name__))
            continue
        v, spec = e["value"], e["spec"]
        if v is False:
            bad.append("%s: false; write \"unset\"" % name)
        elif not (v is True or v == UNSET or isinstance(v, str)
                  or (isinstance(v, int) and not isinstance(v, bool))):
            bad.append("%s: value %r is not true, an integer, a string or \"unset\"" % (name, v))
        elif not isinstance(spec, str) or not spec.strip():
            bad.append("%s: no spec line" % name)
        else:
            out[name] = (v, spec)
    if bad:
        raise OptionsError("%s %s: %s" % (DATA_FILE, KEY, "; ".join(bad)))
    return out


def meets(want, got):
    if want == UNSET:
        return got is ABSENT or got is False
    return got is not ABSENT and type(got) is type(want) and got == want


def show(v):
    return "absent" if v is ABSENT else json.dumps(v)


def judge(profile, options):
    """profile: read_profile()'s; options: {folder label: {name without CONFIG_: value}}.
    [(folder, option, want, got)] for every option not at the profile's value."""
    bad = []
    for folder, opts in options.items():
        for name, (want, _) in sorted(profile.items()):
            got = opts.get(name[len("CONFIG_"):], ABSENT)
            if not meets(want, got):
                bad.append((folder, name, want, got))
    return bad


def collect(build):
    """(project root, {folder label: options}), the label relative to the project root."""
    folders = build_folders(build)
    try:
        project = BuildDir(folders[0]).description()["project_path"]
    except KeyError:
        raise OptionsError("project_description.json has no project_path")
    options = {}
    for f in folders:
        label = os.path.relpath(f, project).replace(os.sep, "/")
        try:
            options[label] = BuildDir(f).options()
        except BuildDirError as e:
            raise OptionsError("%s: %s" % (label, e))
    return project, options


def main(args):
    try:
        project, options = collect(args.build)
        profile = read_profile(project)
    except (OSError, BuildDirError, OptionsError) as e:
        print("FAIL R18: cannot read the build or its profile: %s" % e)
        return 1
    bad = judge(profile, options)
    print("R18 options: %d option(s) in %s, %d build folder(s)" % (
        len(profile), os.path.normpath(os.path.join(project, DATA_FILE)), len(options)))
    folders = sorted(options)
    for name, (want, spec) in sorted(profile.items()):
        got = ", ".join("%s %s" % (f, show(options[f].get(name[len("CONFIG_"):], ABSENT))) for f in folders)
        print("  %-44s profile %-8s  %s" % (name, show(want) if want != UNSET else UNSET, got))
        print("  %-44s   (%s)" % ("", spec))
    if bad:
        print("FAIL R18: %d option(s) not at the profile's value" % len(bad))
        for folder, name, want, got in bad:
            print("    %s: %s is %s, the profile says %s (%s)" % (
                folder, name, show(got), UNSET if want == UNSET else show(want), profile[name][1]))
    else:
        print("PASS R18")
    return 1 if bad else 0
