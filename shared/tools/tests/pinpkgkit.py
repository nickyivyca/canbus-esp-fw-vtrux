"""Stand-ins for the package pins' tests (test_vtrux_pins_packages.py), so no
network is needed:

- make_canre(): what `pip install --target` leaves for canre: canre/, and a
  canre-<version>.dist-info with METADATA, direct_url.json and a RECORD of
  sha256s;
- FAKE_PIP: a stand-in pip, run as a script with this folder in TESTS_DIR.
  It logs its arguments to PIP_LOG, exits PIP_EXIT if that is set, and
  otherwise writes make_canre() into its --target, at PIP_COMMIT if that is
  set, else at the commit in its git+<repo>@<commit> argument;
- a stand-in can-tools clone: make_clone().

This module imports nothing from the tests' scratch, so the stand-in pip can
import it.
"""

import base64
import hashlib
import json
import os
import subprocess

REPO = "https://example.invalid/canre"
OTHER = "0123456789abcdef0123456789abcdef01234567"
GIT = ["git", "-c", "core.autocrlf=false", "-c", "core.symlinks=true",
       "-c", "user.name=stand-in", "-c", "user.email=stand-in@example.invalid",
       "-c", "commit.gpgsign=false"]


def _b64(data):
    return base64.urlsafe_b64encode(
        hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_canre(d, commit, repo=REPO, version="0.1.0", vcs="git", du="vcs",
               mark="pinned"):
    """A stand-in canre install in folder d. Returns d."""
    files = {"canre/__init__.py": "MARK = %r\n" % mark,
             "canre/parsers/__init__.py":
                 "def parse_file(path):\n    return iter(())\n"}
    info = "canre-%s.dist-info" % version
    files[info + "/METADATA"] = ("Metadata-Version: 2.1\nName: canre\n"
                                 "Version: %s\n\nbody\n" % version)
    files[info + "/INSTALLER"] = "pip\n"
    if du == "vcs":
        files[info + "/direct_url.json"] = json.dumps(
            {"url": repo, "vcs_info": {"commit_id": commit, "vcs": vcs}})
    elif du == "editable":
        files[info + "/direct_url.json"] = json.dumps(
            {"url": "file:///src/canre", "dir_info": {"editable": True}})
    rows = []
    for rel, text in files.items():
        p = os.path.join(d, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        rows.append("%s,sha256=%s,%d" % (rel, _b64(text.encode()),
                                         len(text.encode())))
    rows.append(info + "/RECORD,,")
    with open(os.path.join(d, info, "RECORD"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write("\n".join(rows) + "\n")
    return d


FAKE_PIP = r'''
import json, os, sys
sys.path.insert(0, os.environ["TESTS_DIR"])
import pinpkgkit
a = sys.argv[1:]
with open(os.environ["PIP_LOG"], "a") as fh:
    fh.write(json.dumps(a) + "\n")
target = a[a.index("--target") + 1]
if os.environ.get("PIP_EXIT"):
    os.makedirs(target, exist_ok=True)
    sys.exit(int(os.environ["PIP_EXIT"]))
commit = os.environ.get("PIP_COMMIT") or a[-1].rsplit("@", 1)[1]
pinpkgkit.make_canre(target, commit)
'''


PINNED_FILES = {
    "canbench/__init__.py": "MARK = 'pinned'\n",
    "canbench/live/__init__.py": "",
    "canbench/live/receiver.py":
        "def detect(bitrate):\n    return 'PINNED %d' % bitrate\n",
    "canbench/live/note.md": "line one\nline two\n",
    ".gitattributes": "* text eol=crlf\n",
}
LATER_FILES = {
    "canbench/live/receiver.py":
        "def detect(bitrate):\n    return 'HEAD %d' % bitrate\n",
}


def write(base, files):
    for rel, text in files.items():
        p = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def git(c, *args):
    r = subprocess.run(GIT + ["-C", c] + list(args), capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def make_clone(c):
    """A stand-in can-tools clone at c, with three commits: one with no
    canbench/, the pinned one (with a `* text eol=crlf` .gitattributes, so a
    checkout-style export is told apart from the raw blobs), and a later one,
    checked out as HEAD with an edit left in its working tree. Returns
    {"first", "commit", "later"}."""
    os.makedirs(c)
    git(c, "init", "-q")
    write(c, {"README.md": "can-tools stand-in\n"})
    git(c, "add", "-A")
    git(c, "commit", "-q", "-m", "first, no canbench")
    first = git(c, "rev-parse", "HEAD")
    write(c, PINNED_FILES)
    git(c, "add", "-A")
    git(c, "commit", "-q", "-m", "pinned")
    commit = git(c, "rev-parse", "HEAD")
    write(c, LATER_FILES)
    git(c, "add", "-A")
    git(c, "commit", "-q", "-m", "later")
    later = git(c, "rev-parse", "HEAD")
    for rel in PINNED_FILES:
        if rel.startswith("canbench/"):
            os.remove(os.path.join(c, *rel.split("/")))
    git(c, "checkout", "-q", "--", ".")
    with open(os.path.join(c, "canbench", "__init__.py"), "a") as fh:
        fh.write("WORKING_TREE_EDIT = True\n")
    return {"first": first, "commit": commit, "later": later}


def add_link_commit(c):
    """A commit on top of HEAD whose canbench/ holds a symbolic link (mode
    120000). Returns it. The working tree is left as it was."""
    blob = subprocess.run(GIT + ["-C", c, "hash-object", "-w", "--stdin"],
                          input="__init__.py", capture_output=True,
                          text=True, check=True).stdout.strip()
    subprocess.run(GIT + ["-C", c, "update-index", "--add", "--cacheinfo",
                          "120000,%s,canbench/link.py" % blob], check=True)
    tree = git(c, "write-tree")
    commit = git(c, "commit-tree", tree, "-p", "HEAD", "-m", "a link")
    git(c, "reset", "-q", "HEAD")
    return commit
