"""A fake project adapter for the acceptance runner's own tests (ACCEPT-SPEC
A15): the core is proven with it, without any project's code (reviewer,
2026-11-01). Its project folder is "proj" in a git repo the tests build.

It gives what the core asks an adapter for. The names are made up; nothing
here runs a build or touches a board.
"""

PROJECT = "proj"

# build targets and the options each takes (A5)
TARGETS = {"fw": {"options": ["variant", "jobs"]}, "host": {"options": []}}

# shells a check may name, and the one a path gets when it names none
SHELLS = ("python", "git-bash", "powershell")


def default_shell(path):
    return {".py": "python", ".sh": "git-bash", ".ps1": "powershell"}.get(
        "." + path.rsplit(".", 1)[-1] if "." in path else "")


JUDGES = ("exit-code", "known-fails")
COMPARERS = ("transcript", "run-records")
SUITES = {"emulated": "proj/suite.py:EMULATED", "bench": "proj/suite.py:BENCH"}

# the committed harness-test catalogue (A8, A15): per entry its path,
# arguments, environment, shell, timeout, tags and the paths it covers
CATALOGUE = [
    dict(id="unit-a", path="proj/test/a_test.py", args=[], env={}, shell="python",
         timeout_s=60, tags=["unit", "fast"], covers=["proj/src/a.c"]),
    dict(id="unit-b", path="proj/test/b_test.py", args=["--quick"], env={"B": "1"},
         shell="python", timeout_s=60, tags=["unit"], covers=["proj/src/b.c"]),
    dict(id="sim-c", path="proj/test/c_sim.sh", args=[], env={}, shell="git-bash",
         timeout_s=600, tags=["sim"], covers=["proj/src/a.c", "proj/src/b.c"]),
]


def catalogue(repo, commit):
    return [dict(e) for e in CATALOGUE]


# the project's pins of the shared components at a commit, {component: commit
# or tag}; the tests set them
PINS = {}


def shared_pins(repo, commit):
    return dict(PINS)
