"""Command line for the chain runner.

    python -m vtrux_bench.run plan    --suite <file.py>:<NAME> --settings <json>
    python -m vtrux_bench.run run     --suite ... --settings ... [--group G]...
                                      [--check GLOB]... [--resume RUN_DIR]
                                      [--keep-lock PID]
    python -m vtrux_bench.run compare <ref run dir> <new run dir>

--suite names a Python file (or an importable module) and an attribute: a
Suite, or a function taking the settings dict and returning one.
--check globs match full ids ("<group>/<check>"); several are OR-ed.

run exits 0 only if every result is PASS, KNOWN-FAIL or NOT-APPLICABLE,
no known FAIL passed and no registry entry went unproduced; 1 otherwise;
2 if it refused to start (suite invalid, lock not held).
"""

import argparse
import importlib
import importlib.util
import json
import os
import sys

from .engine import Engine, exit_code
from .lock import LockError
from .model import Suite, SuiteError
from .record import compare


def load_settings(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_suite(spec, settings):
    if ":" not in spec[2:]:
        raise SuiteError("--suite must be <file or module>:<name>")
    where, name = spec.rsplit(":", 1)
    if where.endswith(".py"):
        modname = "_suite_" + os.path.splitext(os.path.basename(where))[0]
        s = importlib.util.spec_from_file_location(modname, where)
        mod = importlib.util.module_from_spec(s)
        sys.modules[modname] = mod
        s.loader.exec_module(mod)
    else:
        mod = importlib.import_module(where)
    obj = getattr(mod, name)
    if callable(obj) and not isinstance(obj, Suite):
        obj = obj(settings)
    if not isinstance(obj, Suite):
        raise SuiteError("%s is not a Suite" % spec)
    return obj


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vtrux-bench-run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("plan", "run"):
        p = sub.add_parser(c)
        p.add_argument("--suite", required=True)
        p.add_argument("--settings", required=True)
        p.add_argument("--group", action="append", default=[])
        p.add_argument("--check", action="append", default=[])
        if c == "run":
            p.add_argument("--resume")
            p.add_argument("--run-id")
            p.add_argument("--keep-lock", type=int, metavar="PID",
                           help="at the end, release the bench log's lock "
                           "and take it again for the suite's role, with no "
                           "run, under PID (the long-lived process of the "
                           "session doing the follow-on work), in one step")
    p = sub.add_parser("compare")
    p.add_argument("ref")
    p.add_argument("new")
    a = ap.parse_args(argv)

    if a.cmd == "compare":
        recs = []
        for d in (a.ref, a.new):
            with open(os.path.join(d, "record.json"), encoding="utf-8") as f:
                recs.append(json.load(f))
        diffs = compare(*recs)
        for ln in diffs:
            print(ln)
        print("%d difference(s)" % len(diffs))
        return 1 if diffs else 0

    try:
        settings = load_settings(a.settings)
        suite = load_suite(a.suite, settings)
        eng = Engine(suite, settings, groups=a.group, checks=a.check,
                     resume=getattr(a, "resume", None),
                     run_id=getattr(a, "run_id", None),
                     keep_lock=getattr(a, "keep_lock", None))
        if a.cmd == "plan":
            for ln in eng.plan_lines():
                print(ln)
            return 0
        rec = eng.run()
    except (SuiteError, LockError) as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    print("run %s: %s" % (rec["run_id"], os.path.join(eng.run_dir, "index.txt")))
    return exit_code(rec)


if __name__ == "__main__":
    sys.exit(main())
