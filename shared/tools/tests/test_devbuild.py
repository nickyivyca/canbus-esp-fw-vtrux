"""Tests of the dev build command (vtrux_build.devbuild, SPEC R31) and the
variants it reads (vtrux_build.project, R22).

    python -B shared/tools/tests/test_devbuild.py

idf.py and ninja are replaced by recorders; nothing is built.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import scratch                                                   # noqa: E402
from vtrux_build import devbuild                                 # noqa: E402
from vtrux_build.project import Project, ProjectError            # noqa: E402
from vtrux_build.release import Refused                          # noqa: E402

TOOLS = os.path.abspath(os.path.join(os.sep, "t", "espressif-5.4.2"))
IDF_ENV = {
    "IDF_PATH": os.path.abspath(os.path.join(os.sep, "t", "esp-idf-v5.4.2")),
    "IDF_TOOLS_PATH": TOOLS,
    "IDF_PYTHON_ENV_PATH": os.path.join(TOOLS, "python_env", "v"),
    "ESP_ROM_ELF_DIR": os.path.join(TOOLS, "tools", "esp-rom-elfs", "r"),
}
VARIANTS = {"truck": {"INTP_VARIANT": "truck"},
            "bench": {"INTP_VARIANT": "bench", "X_OPT": "a b"},
            "plain": {}}


def project(name, variants=VARIANTS):
    root = scratch.fresh(name)
    data = {"project": "proj", "shared_components": []}
    if variants is not None:
        data["variants"] = variants
    scratch.write(os.path.join(root, "vtrux_build.json"), json.dumps(data))
    return root


class Recorder:
    """Stands in for idf.py and ninja: records each call and its
    environment, and returns the code it is given for that step."""

    def __init__(self, idf_rc=0, ninja_rc=0):
        self.calls, self.envs = [], {}
        self.idf_rc, self.ninja_rc = idf_rc, ninja_rc

    def idf(self, args, env):
        self.calls.append(("idf", list(args)))
        self.envs["idf"] = env
        return self.idf_rc

    def ninja(self, args, env):
        self.calls.append(("ninja", list(args)))
        self.envs["ninja"] = env
        return self.ninja_rc


class Build(unittest.TestCase):

    def setUp(self):
        p = mock.patch.dict(os.environ, IDF_ENV)
        p.start()
        self.addCleanup(p.stop)

    def build(self, root, variant, jobs=None, rec=None):
        rec = rec or Recorder()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                folder = devbuild.build(root, variant, jobs=jobs,
                                        idf=rec.idf, ninja=rec.ninja)
            return folder, rec, None
        except Refused as e:
            return None, rec, str(e)

    def test_reconfigure_then_ninja_in_the_variants_folder(self):
        root = project("db_ok")
        for variant, jobs in (("truck", None), ("bench", 1), ("plain", 4)):
            folder, rec, why = self.build(root, variant, jobs)
            self.assertIsNone(why)
            want = os.path.join(root, "build", variant)
            self.assertEqual(folder, want)
            defs = []
            for k, v in VARIANTS[variant].items():
                defs += ["-D", "%s=%s" % (k, v)]
            self.assertEqual(rec.calls, [
                ("idf", ["-B", want] + defs + ["reconfigure"]),
                ("ninja", ["-C", want]
                 + (["-j", str(jobs)] if jobs else []) + ["all"])])

    def test_caller_environment_as_it_is(self):
        """R31: nothing added to the caller's environment, and nothing
        taken away (R24 is for release builds only)."""
        root = project("db_env")
        with mock.patch.dict(os.environ, {"EXTRA_CFLAGS": "-DKEEP"}):
            want = dict(os.environ)
            _, rec, why = self.build(root, "truck")
        self.assertIsNone(why)
        self.assertEqual(rec.envs["idf"], want)
        self.assertEqual(rec.envs["ninja"], want)
        self.assertNotIn("SOURCE_DATE_EPOCH", rec.envs["idf"])
        self.assertNotIn("IDF_COMPONENT_STRICT_CHECKSUM", rec.envs["idf"])

    def test_variant_not_listed_names_the_list(self):
        root = project("db_unknown")
        for variant in ("trukc", None):
            _, rec, why = self.build(root, variant)
            self.assertIn("R31", why)
            self.assertIn("truck bench plain", why)
            self.assertEqual(rec.calls, [])

    def test_no_variants_no_build_command(self):
        for name, variants in (("db_none", None), ("db_empty", {})):
            root = project(name, variants)
            _, rec, why = self.build(root, "truck")
            self.assertIn("lists no variants", why)
            self.assertEqual(rec.calls, [])

    def test_r32_runs_first(self):
        root = project("db_r32")
        for name in ("IDF_TOOLS_PATH", "ESP_ROM_ELF_DIR"):
            with mock.patch.dict(os.environ, {}):
                os.environ.pop(name)
                _, rec, why = self.build(root, "truck")
            self.assertIn("R32", why)
            self.assertIn(name, why)
            self.assertEqual(rec.calls, [])

    def test_jobs(self):
        root = project("db_jobs")
        for bad in (0, -1, "2", True):
            _, rec, why = self.build(root, "truck", bad)
            self.assertIn("--jobs", why)
            self.assertEqual(rec.calls, [])

    def test_reconfigure_failure_stops(self):
        root = project("db_rcfail")
        _, rec, why = self.build(root, "truck", rec=Recorder(idf_rc=2))
        self.assertIn("reconfigure failed (exit 2)", why)
        self.assertEqual([c[0] for c in rec.calls], ["idf"])

    def test_ninja_failure(self):
        root = project("db_nfail")
        _, rec, why = self.build(root, "truck", rec=Recorder(ninja_rc=1))
        self.assertIn("ninja failed (exit 1)", why)

    def test_bad_project_data(self):
        root = scratch.fresh("db_baddata")
        scratch.write(os.path.join(root, "vtrux_build.json"), "{")
        _, rec, why = self.build(root, "truck")
        self.assertIn("vtrux_build.json", why)

    def test_real_runners_need_the_exported_tools(self):
        """Without injected runners, idf.py comes from IDF_PATH and the
        venv: a fake export that has neither is refused before anything
        runs."""
        root = project("db_real")
        with self.assertRaises(Refused) as cm, \
                contextlib.redirect_stdout(io.StringIO()):
            devbuild.build(root, "truck")
        self.assertIn("does not exist", str(cm.exception))

    def test_main_and_check_env(self):
        root = project("db_main")
        args = mock.Mock(project=root, variant="nope", jobs=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(devbuild.main(args), 1)
            self.assertEqual(devbuild.check_env_main(), 0)
            with mock.patch.dict(os.environ, {"IDF_PATH": ""}):
                self.assertEqual(devbuild.check_env_main(), 1)


class Variants(unittest.TestCase):
    """vtrux_build.json's "variants" (R31, R22)."""

    def proj(self, variants):
        return Project("r", {"project": "p", "shared_components": [],
                             "variants": variants})

    def test_read_in_order(self):
        self.assertEqual(list(self.proj(VARIANTS).variants()),
                         ["truck", "bench", "plain"])
        self.assertEqual(Project("r", {"project": "p",
                                       "shared_components": []}).variants(),
                         {})

    def test_malformed(self):
        for bad in ([], "truck", {"../x": {}}, {"a/b": {}}, {".x": {}},
                    {"": {}}, {"t": []}, {"t": {"1X": "a"}},
                    {"t": {"X Y": "a"}}, {"t": {"X": 1}},
                    {"t": {"X": True}}, {"t": {"X": "a\nb"}}):
            with self.assertRaises(ProjectError, msg=repr(bad)):
                self.proj(bad).variants()

    def test_a_fault_in_variants_does_not_stop_loading(self):
        """A release build does not read variants: Project still loads."""
        p = self.proj("not an object")
        self.assertEqual(p.name, "p")


if __name__ == "__main__":
    unittest.main()
