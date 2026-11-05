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


def shell_argv(shell, settings, host="local"):
    """The command a shell's file is run with, before the file's path, on
    a host."""
    if shell == "python":
        return list(settings["hosts"][host]["python"]) + ["-B"]
    if shell == "git-bash":
        return [settings["bash"]]
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]


# the machine variables a check may see beside the operator-neutral ones the
# core passes (AGENTS.md, local-paths.md)
MACHINE_VARS = ("FAKE_MACHINE_DIR",)


# what names a tree to the code run in it (A15): the environment of every
# check run there, and the settings keys of a suite run there
def tree_env(tree):
    return {"FAKE_TREE": tree["project_dir"]}


def tree_settings(tree):
    return {"fake_tree": tree["project_dir"]}


# the settings keys that depend on a build (A6), filled from the build records
def settings_keys(builds, trees):
    if not (builds.get("fw") or {}).get("images"):      # none, failed or not run
        return {}
    return {"fw_image": builds["fw"]["images"]["app"]["path"],
            "fw_elf_sha256": builds["fw"]["elf"]["sha256"]}


# a suite run's images (A9): its images_from builds' records, as the core
# wrote them, by variant
def suite_settings(run, records):
    return {"fake_images": {r["variant"]: {"app": r["images"]["app"]["path"],
                                           "elf_sha256": r["elf"]["sha256"],
                                           "build": r["id"]} for r in records}}


def default_shell(path):
    return {".py": "python", ".sh": "git-bash", ".ps1": "powershell"}.get(
        "." + path.rsplit(".", 1)[-1] if "." in path else "")


JUDGES = ("exit-code", "known-fails", "sub-lines")


def judge(name):
    from vtrux_bench.run.model import Result, exit_code_judge
    if name != "sub-lines":
        return exit_code_judge

    def sub_lines(run):
        """one result per log line "SUB <name> <verdict>", in their order"""
        out = []
        for ln in run.log_text().splitlines():
            p = ln.split()
            if len(p) == 3 and p[0] == "SUB":
                out.append(Result(p[2], p[1]))
        return out
    return sub_lines


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


def identifier_scan(folder):
    """A13: the identifier hits in a record, masked: here, MACs."""
    import re
    mac = re.compile(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
    hits = []
    for d, _dirs, files in os.walk(folder):
        for n in files:
            p = os.path.join(d, n)
            with open(p, encoding="utf-8", errors="replace") as f:
                for i, ln in enumerate(f, 1):
                    if mac.search(ln):
                        hits.append("%s:%d MAC <masked>" % (os.path.relpath(p, folder), i))
    return hits


def artifacts_row(folder, summary):
    """A13: the record's row in the runs folder's README (the fake's
    artifacts README)."""
    row = "| `runs/%s/` | %s |" % (os.path.basename(folder), summary["verdict"]["lines"][0])
    with open(os.path.join(os.path.dirname(folder), "README.md"), "a", encoding="utf-8") as f:
        f.write(row + "\n")
    return row
SUITES = {"emulated": "proj/suite.py:EMULATED", "bench": "proj/suite.py:BENCH"}

# the committed harness-test catalogue (A8, A15): per entry its path,
# arguments, environment, shell, timeout, tags and the paths it covers
CATALOGUE = [
    dict(id="unit-a", path="proj/test/a_test.py", args=[], env={}, shell="python",
         timeout_s=60, tags=["unit", "fast"], covers=["proj/src/a.c"]),
    dict(id="unit-b", path="proj/test/b_test.py", args=["--quick"], env={"B": "1"},
         shell="python", timeout_s=60, tags=["unit"], covers=["proj/src/b.c"]),
    dict(id="sim-c", path="proj/test/c_sim.sh", args=[], env={}, shell="git-bash",
         timeout_s=600, tags=["sim"], covers=["proj/src/a.c", "proj/src/b.c"],
         judge="known-fails", cls="wallclock"),
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
