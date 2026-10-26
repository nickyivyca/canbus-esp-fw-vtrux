"""R18, R26, R27, R28, options: a release build's options against the project's release
profile and the options every release build shares.

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

SPEC R26-R28 (shared/main 5151e77), applied whatever the profile names, so a
profile need not list them (readings ruled by the gen-inhibit reviewer, 2026-10-25):

  R26  CONFIG_SECURE_BOOT, CONFIG_SECURE_FLASH_ENC_ENABLED,
       CONFIG_FLASH_ENCRYPTION_ENABLED and CONFIG_EFUSE_VIRTUAL "unset" (absent
       or false) in every build folder.
  R27  the profile names CONFIG_FREERTOS_HZ as an integer (R18 then holds the
       build to it); a profile without it fails.
  R28  CONFIG_LOG_DEFAULT_LEVEL 0, CONFIG_LOG_MAXIMUM_LEVEL 0 and
       CONFIG_BOOTLOADER_LOG_LEVEL 1 in every build folder. A project compiles
       the Arduino core when a build folder's options name
       ARDUHAL_LOG_DEFAULT_LEVEL (the arduino-esp32 component's Kconfig) and when
       its components include an Arduino component (arduino, arduino-esp32, or a
       managed <namespace>__arduino-esp32); both must agree, and a disagreement
       fails naming both. When it does, ARDUHAL_LOG_DEFAULT_LEVEL is 0 in every
       folder that names it. No compile command in any build folder's
       compile_commands.json defines CORE_DEBUG_LEVEL or LOG_LOCAL_LEVEL with -D,
       with or without a value, whoever passes it (ESP-IDF included).

A profile that names one of R26's or R28's options at another value, or
CONFIG_FREERTOS_HZ as anything but an integer, is refused, naming the rule.

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

# R26 and R28: the same values in every release build of every project. ESP-IDF v5.4.1's names
# (components/log/Kconfig.level, components/bootloader/Kconfig.log; VALIDATION R28).
R26 = {name: (UNSET, "R26") for name in ("CONFIG_SECURE_BOOT", "CONFIG_SECURE_FLASH_ENC_ENABLED",
                                         "CONFIG_FLASH_ENCRYPTION_ENABLED", "CONFIG_EFUSE_VIRTUAL")}
R28 = {"CONFIG_LOG_DEFAULT_LEVEL": (0, "R28"), "CONFIG_LOG_MAXIMUM_LEVEL": (0, "R28"),
       "CONFIG_BOOTLOADER_LOG_LEVEL": (1, "R28")}
ARDUHAL = "CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL"
ARDUHAL_VALUE = 0
TICK = "CONFIG_FREERTOS_HZ"
_ARDUINO_COMPONENT = re.compile(r"^(?:[A-Za-z0-9_.-]+__)?arduino(?:[-_]esp32)?$", re.I)
_LOG_DEFINE = re.compile(r"(?<![A-Za-z0-9_])-D\s*(CORE_DEBUG_LEVEL|LOG_LOCAL_LEVEL)(?![A-Za-z0-9_])")


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
            fixed = dict(R26, **R28)
            fixed[ARDUHAL] = (ARDUHAL_VALUE, "R28")
            if name in fixed and not same(v, fixed[name][0]):
                bad.append("%s: %s contradicts %s, which sets it to %s in every release build" % (
                    name, label(v), fixed[name][1], label(fixed[name][0])))
            elif name == TICK and not (isinstance(v, int) and not isinstance(v, bool)):
                bad.append("%s: %s, but R27 needs an integer" % (name, label(v)))
            else:
                out[name] = (v, spec)
    if bad:
        raise OptionsError("%s %s: %s" % (DATA_FILE, KEY, "; ".join(bad)))
    return out


def same(a, b):
    return type(a) is type(b) and a == b


def label(v):
    return UNSET if v == UNSET else show(v)


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


def judge_tick(profile):
    """R27: None when the profile names CONFIG_FREERTOS_HZ (read_profile has held it to an
    integer), else the reason."""
    return None if TICK in profile else "the profile does not name %s" % TICK


def arduino(options, components):
    """R28's Arduino question. options: {folder: options}; components: {folder: [component
    names]}. (compiles, disagreement or None): Kconfig and components must agree."""
    by_kconfig = sorted(f for f, o in options.items() if ARDUHAL[len("CONFIG_"):] in o)
    by_component = sorted("%s: %s" % (f, c) for f, cs in components.items() for c in cs
                          if _ARDUINO_COMPONENT.match(c))
    if bool(by_kconfig) != bool(by_component):
        return bool(by_kconfig or by_component), (
            "%s names %s but no Arduino component is built" % (", ".join(by_kconfig), ARDUHAL) if by_kconfig else
            "an Arduino component is built (%s) but no build folder's options name %s" % (
                ", ".join(by_component), ARDUHAL))
    return bool(by_kconfig), None


def judge_arduino(options):
    """R28: ARDUHAL_LOG_DEFAULT_LEVEL 0 in every folder that names it."""
    key = ARDUHAL[len("CONFIG_"):]
    return [(f, ARDUHAL, ARDUHAL_VALUE, o[key]) for f, o in sorted(options.items())
            if key in o and not meets(ARDUHAL_VALUE, o[key])]


def log_defines(commands):
    """commands: {folder: compile_commands.json's list}. [(folder, define, files)]: each
    -DCORE_DEBUG_LEVEL / -DLOG_LOCAL_LEVEL, with or without a value, and the files compiled
    with it."""
    found = {}
    for folder, entries in sorted(commands.items()):
        for e in entries:
            cmd = e.get("command")
            if cmd is None:
                cmd = " ".join(e.get("arguments") or [])
            for m in _LOG_DEFINE.finditer(cmd):
                found.setdefault((folder, m.group(1)), []).append(e.get("file", "?"))
    return [(f, d, files) for (f, d), files in sorted(found.items())]


def collect(build):
    """(project root, {folder: options}, {folder: components}, {folder: compile commands}),
    each folder labelled relative to the project root."""
    folders = build_folders(build)
    try:
        project = BuildDir(folders[0]).description()["project_path"]
    except KeyError:
        raise OptionsError("project_description.json has no project_path")
    options, components, commands = {}, {}, {}
    for f in folders:
        label_ = os.path.relpath(f, project).replace(os.sep, "/")
        bd = BuildDir(f)
        try:
            options[label_] = bd.options()
        except BuildDirError as e:
            raise OptionsError("%s: %s" % (label_, e))
        try:
            components[label_] = sorted(bd.components())
        except BuildDirError as e:
            raise OptionsError("%s: %s" % (label_, e))
        p = bd.file("compile_commands.json")
        try:
            with open(p, encoding="utf-8") as fh:
                cc = json.load(fh)
        except (OSError, ValueError) as e:
            raise OptionsError("%s: compile_commands.json cannot be read (%s)" % (label_, e))
        if not isinstance(cc, list):
            raise OptionsError("%s: compile_commands.json is not a list" % label_)
        commands[label_] = cc
    return project, options, components, commands


def main(args):
    try:
        project, options, components, commands = collect(args.build)
        profile = read_profile(project)
    except (OSError, BuildDirError, OptionsError) as e:
        print("FAIL R18: cannot read the build or its profile: %s (R26, R27 and R28 not checked)" % e)
        return 1
    folders = sorted(options)
    failed = False

    bad = judge(profile, options)
    print("R18 options: %d option(s) in %s, %d build folder(s)" % (
        len(profile), os.path.normpath(os.path.join(project, DATA_FILE)), len(options)))
    for name, (want, spec) in sorted(profile.items()):
        got = ", ".join("%s %s" % (f, show(options[f].get(name[len("CONFIG_"):], ABSENT))) for f in folders)
        print("  %-44s profile %-8s  %s" % (name, label(want), got))
        print("  %-44s   (%s)" % ("", spec))
    if bad:
        failed = True
        print("FAIL R18: %d option(s) not at the profile's value" % len(bad))
        for folder, name, want, got in bad:
            print("    %s: %s is %s, the profile says %s (%s)" % (folder, name, show(got), label(want), profile[name][1]))
    else:
        print("PASS R18")

    for rule, table in (("R26", R26), ("R28", R28)):
        print("%s options, every release build:" % rule)
        for name, (want, _) in sorted(table.items()):
            got = ", ".join("%s %s" % (f, show(options[f].get(name[len("CONFIG_"):], ABSENT))) for f in folders)
            print("  %-44s want    %-8s  %s" % (name, label(want), got))
        bad = judge(table, options)
        if rule == "R28":
            compiles, disagree = arduino(options, components)
            print("  Arduino core compiled: %s" % ("yes" if compiles else "no"))
            arduhal = judge_arduino(options) if compiles else []
            defines = log_defines(commands)
            print("  -D CORE_DEBUG_LEVEL / LOG_LOCAL_LEVEL in compile_commands.json: %s" % (
                "none" if not defines else "%d" % len(defines)))
        else:
            disagree, arduhal, defines = None, [], []
        if bad or disagree or arduhal or defines:
            failed = True
            print("FAIL %s" % rule)
            for folder, name, want, got in bad + arduhal:
                print("    %s: %s is %s, %s says %s" % (folder, name, show(got), rule, label(want)))
            if disagree:
                print("    the Arduino core: %s" % disagree)
            for folder, define, files in defines:
                print("    %s: -D%s in %d compile command(s), e.g. %s" % (folder, define, len(files), files[0]))
        else:
            print("PASS %s" % rule)

    why = judge_tick(profile)
    if why:
        failed = True
        print("FAIL R27: %s" % why)
    else:
        print("PASS R27: %s %s (%s)" % (TICK, show(profile[TICK][0]), profile[TICK][1]))
    return 1 if failed else 0
