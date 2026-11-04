"""Dev builds by the tooling (SPEC R31): a bench or dev build of one variant.

    python -B -m vtrux_build build <variant> [--project DIR] [--jobs N]

The variants are the project's data (R22): vtrux_build.json's "variants",
each naming the CMake options it is configured with (project.py). In order,
each refusal an exit:

  1. the project's build data reads, and lists the variant. A project that
     lists none has no build command; a variant not listed is refused, and
     the refusal names the list;
  2. --jobs, if given, is a whole number of at least 1;
  3. R32: the environment is exported for one ESP-IDF install (idfenv.py);
  4. in build/<variant> under the project root:
     `idf.py -C <root> -B build/<variant> -D <option>=<value>... reconfigure`,
     then `ninja -C build/<variant> [-j N] all`.

Both run in the caller's environment as it is: nothing is added, and
nothing taken away (R24 constrains release builds only). R14 applies: a
dev build need not meet the release rules, so none of release.py's other
checks run. The reconfigure is run every time, so a fresh folder gets its
variant's options and the managed components are resolved; --jobs 1 from
an Idle shell is the throttled form of AGENTS.md "Builds during wall-clock
tests".
"""

import os

from . import idfenv
from .project import Project, ProjectError
from .release import Refused, check_jobs, idf_runner, ninja_runner, ok, say


def _variant(proj, variant):
    """The variant's {option: value}. Refused if the project lists none or
    not this one."""
    try:
        variants = proj.variants()
    except ProjectError as e:
        raise Refused(str(e))
    if not variants:
        raise Refused("%s lists no variants, so the project has no build "
                      "command (R31)" % os.path.join(proj.root,
                                                     "vtrux_build.json"))
    if variant not in variants:
        raise Refused("%s; the variants are: %s (R31)"
                      % ("no variant given" if variant is None
                         else "no variant %r" % variant,
                         " ".join(variants)))
    return variants[variant]


def build(project_root, variant, jobs=None, idf=None, ninja=None):
    """The dev build of one variant. Returns its build folder. Raises
    Refused with the reason. idf and ninja replace the real idf.py and
    ninja runners (tests)."""
    root = os.path.abspath(project_root)
    try:
        proj = Project.load(root)
    except ProjectError as e:
        raise Refused(str(e))
    options = _variant(proj, variant)
    ok("variant (R31)", "%s: %s" % (variant, " ".join(
        "-D %s=%s" % kv for kv in options.items()) or "no options"))
    check_jobs(jobs)
    try:
        idfenv.check()
    except idfenv.EnvError as e:
        raise Refused(str(e))
    ok("IDF environment (R32)", os.environ.get("IDF_TOOLS_PATH", ""))
    if idf is None:
        idf = idf_runner(root)
    if ninja is None:
        ninja = ninja_runner()
    # The caller's environment as it is (R31: nothing added).
    env = dict(os.environ)
    folder = os.path.join(root, "build", variant)
    args = ["-B", folder]
    for opt, value in options.items():
        args += ["-D", "%s=%s" % (opt, value)]
    args.append("reconfigure")
    say("\nidf.py -C %s %s ..." % (root, " ".join(args)))
    rc = idf(args, env)
    if rc != 0:
        raise Refused("idf.py reconfigure failed (exit %d)" % rc)
    ok("reconfigure", folder)
    nargs = ["-C", folder] + (["-j", str(jobs)] if jobs else []) + ["all"]
    say("\nninja %s ..." % " ".join(nargs))
    rc = ninja(nargs, env)
    if rc != 0:
        raise Refused("ninja failed (exit %d)" % rc)
    ok("built", folder)
    return folder


def main(args):
    say("=" * 72)
    say("DEV BUILD (shared/tools SPEC R31): not a release build")
    say("=" * 72)
    try:
        folder = build(args.project, args.variant, jobs=args.jobs)
    except Refused as e:
        say("\nREFUSED: %s" % e)
        return 1
    say("\nBUILT: %s" % folder)
    return 0


def check_env_main(args=None):
    """The check-env command (R32): exit 0 if this environment passes,
    1 with the refusal if not."""
    try:
        values = idfenv.check()
    except idfenv.EnvError as e:
        say("REFUSED: %s" % e)
        return 1
    for name in idfenv.NAMES:
        ok(name, values[name])
    say("\nIDF ENVIRONMENT OK (R32).")
    return 0
