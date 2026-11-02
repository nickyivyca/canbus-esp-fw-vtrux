"""The run's settings (ACCEPT-SPEC A6).

The runner writes a settings file for the run from the machine's settings
file (the chain runner's JSON, one per machine, not in git). The keys that
depend on a build are filled from the build records by the adapter's key
map, settings_keys(builds, trees), never edited by hand. Its runs_root is
the acceptance record's runs/, where the nested suite runs' records go (A7).

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
                           "runs_in_record")}

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
