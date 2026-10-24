"""Tests of the dependencies.lock and idf_component.yml readers
(vtrux_build.lockfile, vtrux_build.manifest).

    python -B shared/tools/tests/test_lock_manifest.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vtrux_build import lockfile, manifest      # noqa: E402

# The shape IDF 5.4.1's Component Manager writes, with a git and a local
# dependency added the way it records them.
LOCK = """\
dependencies:
  espressif/mdns:
    component_hash: af6306fe65d637a3683d1cf671508fcedd6b05f9ca029a8815abeab64001fb8d
    dependencies:
    - name: idf
      require: private
      version: '>=5.0'
    source:
      registry_url: https://components.espressif.com/
      type: service
    version: 1.4.0
  idf:
    source:
      type: idf
    version: 5.4.1
  twai_port:
    component_hash: 1111111111111111111111111111111111111111111111111111111111111111
    source:
      git: https://example.invalid/repo.git
      path: shared/components/twai_port
      type: git
    version: 0123456789abcdef0123456789abcdef01234567
  restart_guard:
    dependencies: []
    source:
      path: C:/work/shared/components/restart_guard
      type: local
    version: '*'
direct_dependencies:
- espressif/mdns
- idf
manifest_hash: 5c9b5d1c7e215efa0c5588484ef0de1d38e5349e46173168a65ef08cb960b438
target: esp32c3
version: 2.0.0
"""

MANIFEST = """\
## IDF Component Manager Manifest File
dependencies:
  espressif/mdns: "*"
  ## Required IDF version
  idf:
    version: ">=4.1.0"
  twai_port:
    version: "shared-twai_port-v1"   # the tag
    git: https://example.invalid/repo.git
    path: shared/components/twai_port
    rules:
      - if: "target in [esp32c3]"
  restart_guard:
    version: "shared-restart_guard-v2"
    git: https://example.invalid/repo.git
    override_path: "../../shared/components/restart_guard"
  # commented:
  #   override_path: nope
targets:
  - esp32c3
"""


class Lock(unittest.TestCase):

    def setUp(self):
        self.l = lockfile.parse(LOCK)

    def test_entries(self):
        self.assertEqual(sorted(self.l), ["espressif/mdns", "idf",
                                          "restart_guard", "twai_port"])

    def test_service(self):
        e = self.l["espressif/mdns"]
        self.assertEqual(e["component_hash"][:8], "af6306fe")
        self.assertEqual(e["version"], "1.4.0")
        self.assertEqual(e["source"]["type"], "service")

    def test_git_and_local(self):
        self.assertEqual(self.l["twai_port"]["source"],
                         {"git": "https://example.invalid/repo.git",
                          "path": "shared/components/twai_port", "type": "git"})
        self.assertEqual(self.l["twai_port"]["version"][:4], "0123")
        self.assertEqual(self.l["restart_guard"]["source"]["type"], "local")
        self.assertEqual(self.l["restart_guard"]["version"], "*")
        self.assertNotIn("component_hash", self.l["restart_guard"])

    def test_list_fields_not_read_as_source(self):
        self.assertNotIn("require", self.l["espressif/mdns"]["source"])

    def test_refuses_other_shapes(self):
        with self.assertRaises(lockfile.LockError):
            lockfile.parse("target: esp32c3\n")
        with self.assertRaises(lockfile.LockError):
            lockfile.parse("dependencies:\n   odd: indent\n")

    def test_dir_name(self):
        self.assertEqual(lockfile.managed_dir_name("espressif/mdns"),
                         "espressif__mdns")
        self.assertEqual(lockfile.managed_dir_name("twai_port"), "twai_port")


class Manifest(unittest.TestCase):

    def setUp(self):
        self.m = manifest.parse(MANIFEST)

    def test_entries(self):
        self.assertEqual(sorted(self.m), ["espressif/mdns", "idf",
                                          "restart_guard", "twai_port"])
        self.assertEqual(self.m["espressif/mdns"], {"version": "*"})
        self.assertEqual(self.m["idf"], {"version": ">=4.1.0"})

    def test_tag_and_override(self):
        self.assertEqual(self.m["twai_port"]["version"], "shared-twai_port-v1")
        self.assertNotIn("override_path", self.m["twai_port"])
        self.assertEqual(self.m["restart_guard"]["override_path"],
                         "../../shared/components/restart_guard")

    def test_comment_is_not_a_field(self):
        m = manifest.parse("dependencies:\n  x:\n    version: '1'\n"
                           "    # override_path: ../x\n")
        self.assertEqual(m, {"x": {"version": "1"}})

    def test_four_space_indent(self):
        m = manifest.parse("dependencies:\n    x:\n        override_path: ../x\n")
        self.assertEqual(m["x"]["override_path"], "../x")

    def test_flow_style_refused(self):
        with self.assertRaises(manifest.ManifestError):
            manifest.parse("dependencies:\n  x: {override_path: ../x}\n")

    def test_other_top_level_keys_ignored(self):
        m = manifest.parse("version: 1.0.0\ndescription: d\n"
                           "dependencies:\n  y: '*'\nfiles:\n  exclude:\n"
                           "    - x\n")
        self.assertEqual(m, {"y": {"version": "*"}})


if __name__ == "__main__":
    unittest.main()
