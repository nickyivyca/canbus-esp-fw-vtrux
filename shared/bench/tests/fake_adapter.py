"""A fake project adapter for the acceptance runner's own tests (ACCEPT-SPEC
A15): the core is proven with it, without any project's code (reviewer,
2026-11-01). Its project folder is "proj" in a git repo the tests build.

It gives what the core asks an adapter for. The names are made up; nothing
here runs a build or touches a board.
"""

import os

PROJECT = "proj"
ROLE = "tester (test-suite)"

# build targets (A5): the options each takes, the committed file it runs in
# the build's tree, its shell, its arguments, and what it made
TARGETS = {
    "fw": {"options": ["variant", "jobs"], "run": "proj/build.py", "shell": "python",
           "args": lambda o, out: [out, o.get("variant", "dev")],
           "outputs": lambda out, o: {
               "images": {"app": os.path.join(out, "app.bin")},
               "elf": os.path.join(out, "app.elf"),
               "variant": o.get("variant", "dev"), "toolchain": "fake-cc 1.0"}},
    "host": {"options": [], "run": "proj/build.py", "shell": "python",
             "args": lambda o, out: [out, "host"],
             "outputs": lambda out, o: {"images": {}, "elf": os.path.join(out, "app.elf"),
                                        "variant": "host", "toolchain": "fake-cc 1.0"}},
}

# shells a check may name, and the one a path gets when it names none
SHELLS = ("python", "git-bash", "powershell")


def shell_argv(shell, settings):
    """The command a shell's file is run with, before the file's path."""
    if shell == "python":
        return list(settings["hosts"]["local"]["python"]) + ["-B"]
    if shell == "git-bash":
        return [settings["bash"]]
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]


# the machine variables a check may see beside the operator-neutral ones the
# core passes (AGENTS.md, local-paths.md)
MACHINE_VARS = ("FAKE_MACHINE_DIR",)


# the settings keys that depend on a build (A6), filled from the build records
def settings_keys(builds, trees):
    if "fw" not in builds:
        return {}
    return {"fw_image": builds["fw"]["images"]["app"]["path"],
            "fw_elf_sha256": builds["fw"]["elf"]["sha256"]}


def default_shell(path):
    return {".py": "python", ".sh": "git-bash", ".ps1": "powershell"}.get(
        "." + path.rsplit(".", 1)[-1] if "." in path else "")


JUDGES = ("exit-code", "known-fails")


def judge(name):
    from vtrux_bench.run.model import exit_code_judge
    return exit_code_judge
# the adapter's own comparers (A10), beside the core's (run-records,
# transcript); each takes (side a, side b, the compare's spec, the adapter)
# and gives its differences as {"kind", "line"}
COMPARERS = ("fake-count",)


def comparer(name):
    def count(a, b, spec, adapter):
        na, nb = len(a["record"]["results"]), len(b["record"]["results"])
        return [] if na == nb else [{"kind": "text", "line": "count %d -> %d" % (na, nb)}]
    return {"fake-count": count}[name]


# named masks a transcript comparison may use (A10)
MASKS = {"tree-root": r"tree-[a-z]+-[0-9]+"}
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


# the product's files in a tree (A4: the product digest), repo-relative
def product_files(root):
    return ["proj/check.py", "proj/suite.py"]


# where a tree's checkouts go inside its folder (A4): the project's, and
# each shared component's
def tree_paths(name, tree):
    out = {"project": "project"}
    out.update({c: "shared-%s" % c for c in tree["shared"]})
    return out
