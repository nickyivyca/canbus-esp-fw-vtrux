"""Tests of the component digest (vtrux_build.digest): SPEC R7.

    python -B shared/tools/tests/test_digest.py
"""

import hashlib
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import scratch                                             # noqa: E402
from vtrux_build.digest import component_digest, component_files  # noqa: E402

FILES = {
    "CMakeLists.txt": b"idf_component_register(SRCS a.c)\n",
    "a.c": b"int a;\n",
    "include/a.h": b"#pragma once\n",
    "sub/deep/b.bin": bytes(range(256)),
}


def tree(name, files=FILES):
    root = scratch.fresh(name)
    for rel, data in files.items():
        scratch.write(os.path.join(root, *rel.split("/")), data)
    return root


class Digest(unittest.TestCase):

    def test_definition(self):
        """sha256 over path NUL bytes NUL, sorted by path, "/" separators."""
        root = tree("digest_def")
        h = hashlib.sha256()
        for rel in sorted(FILES, key=lambda r: r.encode()):
            h.update(rel.encode() + b"\0" + FILES[rel] + b"\0")
        self.assertEqual(component_digest(root), h.hexdigest())

    def test_folder_does_not_matter(self):
        a = tree("digest_a")
        b = tree(os.path.join("digest_b", "deeper", "elsewhere"))
        self.assertEqual(component_digest(a), component_digest(b))

    def test_documents_and_gitignore_left_out(self):
        a = component_digest(tree("digest_docs0"))
        extra = dict(FILES)
        extra.update({"README.md": b"x", "VERIFICATION.md": b"y",
                      "sub/notes.md": b"z", ".gitignore": b"build/\n",
                      "sub/.gitignore": b"*.o\n"})
        self.assertEqual(component_digest(tree("digest_docs1", extra)), a)

    def test_everything_else_counts(self):
        a = component_digest(tree("digest_all0"))
        for rel, data in (("README.MD", b"x"), ("notes.md.txt", b"x"),
                          ("new.c", b""), (".gitattributes", b"* text\n"),
                          ("__pycache__/m.pyc", b"\0")):
            extra = dict(FILES)
            extra[rel] = data
            self.assertNotEqual(component_digest(tree("digest_all1", extra)), a,
                                "%s was not counted" % rel)

    def test_content_and_path_count(self):
        a = component_digest(tree("digest_c0"))
        edited = dict(FILES)
        edited["a.c"] = b"int a; \n"
        self.assertNotEqual(component_digest(tree("digest_c1", edited)), a)
        moved = dict(FILES)
        moved["src/a.c"] = moved.pop("a.c")
        self.assertNotEqual(component_digest(tree("digest_c2", moved)), a)

    def test_empty_file_vs_missing(self):
        a = component_digest(tree("digest_e0"))
        extra = dict(FILES)
        extra["empty"] = b""
        self.assertNotEqual(component_digest(tree("digest_e1", extra)), a)

    def test_no_git_ref(self):
        """A git checkout of the files and a plain copy give one digest:
        .git is outside the component folder, and nothing reads a ref."""
        if not shutil.which("git"):
            self.skipTest("no git")
        repo = scratch.fresh("digest_git")
        comp = os.path.join(repo, "comp")
        for rel, data in FILES.items():
            scratch.write(os.path.join(comp, *rel.split("/")), data)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "c"],
                     ["tag", "v1"]):
            subprocess.run(["git", "-C", repo, "-c", "core.autocrlf=false"]
                           + args, check=True, env=env)
        self.assertEqual(component_digest(comp),
                         component_digest(tree("digest_plain")))

    def test_order_is_by_path_bytes(self):
        root = tree("digest_order", {"b": b"1", "a/z": b"2", "a-b": b"3",
                                     "C": b"4"})
        self.assertEqual([r for r, _ in component_files(root)],
                         ["C", "a-b", "a/z", "b"])

    def test_missing_root(self):
        with self.assertRaises(NotADirectoryError):
            component_digest(os.path.join(scratch.ROOT, "no-such-folder"))


if __name__ == "__main__":
    unittest.main()
