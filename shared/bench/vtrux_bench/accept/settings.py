"""The run's settings (ACCEPT-SPEC A6).

The runner writes a settings file for the run from the machine's settings
file (the chain runner's JSON, one per machine, not in git). The keys that
depend on a build are filled from the build records by the adapter's key
map, settings_keys(builds, trees), never edited by hand. Its runs_root is
the acceptance record's runs/, where the nested suite runs' records go (A7).
Each suite run gets its own settings file too, these with the adapter's
tree_settings(tree) over them (write_tree_settings), so a suite run in a
tree is told that tree, and, for a suite run with images_from, the
adapter's suite_settings from those builds' records (suite_images), so the
suite flashes the list's own images (A9).

The unmasked file is written to a scratch folder of the run's own (held in
its Made, so it is removed with the run, A14), never into the record. A
masked copy goes in the record, settings.masked.json: MAC addresses and
whatever the adapter's redact_text() masks.

Passwords are never read (AGENTS.md): a machine settings file with a key
that names a password, a secret, a token or a PSK is refused, naming the
key, never its value.

RULES: each is switched off by a mutant in tests/test_accept_compile.py,
which must then fail.
"""

import json
import os
import re

from .. import scratch as S

RULES = {k: True for k in ("keymap", "masked", "scratch_only", "no_passwords",
                           "runs_in_record", "tree_settings", "images_built",
                           "images_one_variant", "images_copy", "images_settings",
                           "images_refusal")}

PASSWORD_KEY = re.compile(r"(?i)pass(word|wd|phrase)?|secret|token|psk")
MAC = re.compile(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")


class SettingsError(Exception):
    pass


def _keys(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield prefix + k
            yield from _keys(v, prefix + k + ".")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _keys(v, "%s%d." % (prefix, i))


def mask(text, adapter=None):
    """text with MACs and the adapter's identifiers masked."""
    text = MAC.sub("<MAC>", text)
    redact = getattr(adapter, "redact_text", None)
    return redact(text) if redact else text


def masked(settings, adapter=None):
    """A masked copy of a settings dict, for every record a run writes (A6:
    the chain runner's record_settings, nested runs' --record-settings)."""
    return json.loads(mask(json.dumps(settings), adapter))


def suite_images(run, builds, adapter):
    """The settings a suite run gets from the builds whose images it
    flashes, its images_from (A9: the flash and the identify after it are
    the chain runner's, under its own lock, with the list's own images;
    A5). The adapter's suite_settings(run, records) makes them from the
    build records as this runner wrote them, with the sha256 it recorded
    (a copy, so nothing the adapter does changes the record); it never
    reads its own from the build folders (the charge-interposer reviewer,
    2026-11-04).

    (settings, None), or (None, why) when a build it names made no images
    (failed, not run, or a target with none): that suite run is then not
    started, and the rest of the run goes on (as a missing setting, A12).
    Refused (SettingsError), before anything runs: two of its builds of one
    variant (which one would it flash?), an adapter with no suite_settings,
    or one whose suite_settings refuses them (raises)."""
    names = list(run.get("images_from") or [])
    if not names:
        return {}, None
    recs = [builds.get(n) or {"verdict": "absent"} for n in names]
    bad = ["%s (%s)" % (n, r.get("verdict") or "?") for n, r in zip(names, recs)
           if not r.get("images")]
    if bad and RULES["images_built"]:
        return None, ("not started: its images_from build(s) made no images: %s"
                      % ", ".join(bad))
    variants = [r.get("variant") for r in recs if r.get("variant") is not None]
    dup = sorted({v for v in variants if variants.count(v) > 1})
    if dup and RULES["images_one_variant"]:
        raise SettingsError("suite run %s: its images_from gives variant(s) %s from more "
                            "than one build, so which it flashes is not said"
                            % (run.get("id"), ", ".join(dup)))
    fn = getattr(adapter, "suite_settings", None)
    if fn is None:
        raise SettingsError("suite run %s names images_from, and the adapter has no "
                            "suite_settings to give a suite its images" % run.get("id"))
    try:
        return fn(run, json.loads(json.dumps(recs)) if RULES["images_copy"] else recs), None
    except Exception as e:
        if not RULES["images_refusal"]:
            raise
        # the adapter refusing them (a variant the suite needs and no build
        # gives, say) refuses the run, before anything is flashed
        raise SettingsError("suite run %s: the adapter's suite_settings refused its "
                            "images_from: %s" % (run.get("id"), e))


def write_tree_settings(s, adapter, item_id, tree, rec, made, parent, images=None):
    """The settings of one suite run (A7): the run's settings, s, with the
    adapter's tree_settings(tree) over them, the keys that name the tree to
    the suite run in it (the interposer's interposer_code), and over those
    its images, suite_images()'s settings; unmasked in the run's scratch,
    masked in the record as settings.<id>.masked.json. (path, masked
    path)."""
    s2 = dict(s)
    fn = getattr(adapter, "tree_settings", None)
    if fn is not None and RULES["tree_settings"]:
        s2.update(fn(tree))
    if images and RULES["images_settings"]:
        s2.update(images)
    d = made.add(S.make(parent, "settings-%s" % item_id))
    path = os.path.join(d, "settings.json")
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        json.dump(s2, f, indent=1, sort_keys=True)
    masked_path = os.path.join(rec.path, "settings.%s.masked.json" % item_id)
    with open(masked_path, "x", encoding="utf-8", newline="\n") as f:
        f.write(mask(json.dumps(s2, indent=1, sort_keys=True), adapter))
    return path, masked_path


def write_settings(machine_path, adapter, builds, trees, rec, made, parent):
    """(path of the run's settings file, the settings)."""
    with open(machine_path, encoding="utf-8") as f:
        s = json.load(f)
    if RULES["no_passwords"]:
        bad = sorted(k for k in _keys(s) if PASSWORD_KEY.search(k.rsplit(".", 1)[-1]))
        if bad:
            raise SettingsError("the machine settings %s hold key(s) naming a password "
                                "or secret: %s. Passwords are never read (AGENTS.md); "
                                "pass them by environment variable" % (machine_path,
                                                                        ", ".join(bad)))
    if RULES["keymap"]:
        s.update(adapter.settings_keys(builds, trees))
    if RULES["runs_in_record"]:
        # nested suite runs (A7) make their records inside the acceptance
        # record, and a suite's `requires` finds this acceptance's own runs
        s["runs_root"] = os.path.join(rec.path, "runs")
    if RULES["scratch_only"]:
        d = made.add(S.make(parent, "settings"))
        path = os.path.join(d, "settings.json")
    else:
        path = os.path.join(rec.path, "settings.json")
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        json.dump(s, f, indent=1, sort_keys=True)
    text = json.dumps(s, indent=1, sort_keys=True)
    with open(os.path.join(rec.path, "settings.masked.json"), "x", encoding="utf-8",
              newline="\n") as f:
        f.write(mask(text, adapter) if RULES["masked"] else text)
    rec.say("settings: written for the run; masked copy in the record "
            "(settings.masked.json)")
    return path, s
