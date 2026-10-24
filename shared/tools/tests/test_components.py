"""Tests of the downloaded-component check (vtrux_build.components): SPEC R10.

    python -B shared/tools/tests/test_components.py
"""

import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import scratch                                  # noqa: E402
from vtrux_build import components              # noqa: E402

CHASH = "ab" * 32
FILES = {"CMakeLists.txt": b"idf_component_register()\n",
         "src/m.c": b"int m;\n", "include/m.h": b"#pragma once\n"}


def lock_text(entries):
    out = ["dependencies:"]
    for name, typ, chash in entries:
        out.append("  %s:" % name)
        if chash:
            out.append("    component_hash: %s" % chash)
        out += ["    source:", "      type: %s" % typ, "    version: 1.0.0"]
    out += ["  idf:", "    source:", "      type: idf", "    version: 5.4.1",
            "target: esp32c3", "version: 2.0.0", ""]
    return "\n".join(out)


def project(name, lock_entries=(("espressif/m", "service", CHASH),),
            files=FILES, listed=None, comp_hash=CHASH, dirname="espressif__m",
            checksums=True):
    root = scratch.fresh(name)
    scratch.write(os.path.join(root, "dependencies.lock"),
                  lock_text(lock_entries))
    if dirname is None:
        return root
    cdir = os.path.join(root, "managed_components", dirname)
    for rel, data in files.items():
        scratch.write(os.path.join(cdir, *rel.split("/")), data)
    man = {"version": "1.0", "algorithm": "sha256", "created_at": "x",
           "files": [{"path": rel, "size": len(d),
                      "hash": hashlib.sha256(d).hexdigest()}
                     for rel, d in (listed or files).items()]}
    if checksums:
        scratch.write(os.path.join(cdir, "CHECKSUMS.json"), json.dumps(man))
    if comp_hash is not None:
        scratch.write(os.path.join(cdir, ".component_hash"), comp_hash + "\n")
    return root


class Managed(unittest.TestCase):

    def problems(self, root):
        ok, rep = components.check_managed(root)
        probs = [p for r in rep.values() for p in r["problems"]]
        self.assertEqual(ok, not probs)
        return probs, rep

    def test_clean(self):
        probs, rep = self.problems(project("comp_clean"))
        self.assertEqual(probs, [])
        self.assertEqual(rep["espressif__m"]["files_checked"], 3)

    def test_edited_file(self):
        root = project("comp_edit")
        scratch.write(os.path.join(root, "managed_components", "espressif__m",
                                   "src", "m.c"), b"int m = 1;\n")
        probs, _ = self.problems(root)
        self.assertTrue(any("m.c" in p for p in probs), probs)

    def test_same_size_edit(self):
        root = project("comp_edit2")
        scratch.write(os.path.join(root, "managed_components", "espressif__m",
                                   "src", "m.c"), b"int n;\n")
        probs, _ = self.problems(root)
        self.assertEqual(probs, ["hash mismatch: src/m.c"])

    def test_added_file(self):
        root = project("comp_added")
        scratch.write(os.path.join(root, "managed_components", "espressif__m",
                                   "src", "extra.c"), b"")
        probs, _ = self.problems(root)
        self.assertEqual(probs, ["not listed in CHECKSUMS.json: src/extra.c"])

    def test_missing_listed_file(self):
        files = dict(FILES)
        del files["src/m.c"]
        probs, _ = self.problems(project("comp_missing", files=files,
                                         listed=FILES))
        self.assertEqual(probs, ["missing: src/m.c"])

    def test_component_hash_differs(self):
        probs, _ = self.problems(project("comp_hash", comp_hash="cd" * 32))
        self.assertEqual(len(probs), 1)
        self.assertIn(".component_hash", probs[0])

    def test_no_component_hash(self):
        probs, _ = self.problems(project("comp_nohash", comp_hash=None))
        self.assertEqual(probs, ["no .component_hash"])

    def test_no_checksums(self):
        probs, _ = self.problems(project("comp_nosums", checksums=False))
        self.assertEqual(probs, ["no CHECKSUMS.json"])

    def test_lock_names_one_not_on_disk(self):
        probs, rep = self.problems(project("comp_absent", dirname=None))
        self.assertIn("espressif__m", rep)
        self.assertEqual(len(probs), 1)
        self.assertIn("not in managed_components", probs[0])

    def test_on_disk_not_in_lock(self):
        probs, _ = self.problems(project("comp_unlocked", lock_entries=()))
        self.assertEqual(probs, ["not in dependencies.lock"])

    def test_local_and_idf_not_expected_on_disk(self):
        probs, rep = self.problems(project(
            "comp_local", lock_entries=(("mine", "local", None),),
            dirname=None))
        self.assertEqual((probs, rep), ([], {}))

    def test_git_expected_on_disk(self):
        probs, _ = self.problems(project(
            "comp_git", lock_entries=(("twai_port", "git", CHASH),),
            dirname=None))
        self.assertEqual(len(probs), 1)


if __name__ == "__main__":
    unittest.main()
