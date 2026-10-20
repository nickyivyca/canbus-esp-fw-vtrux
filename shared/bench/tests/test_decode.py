"""Tests of the diag DBC schema identity (vtrux_bench.decode). No hardware.

    python -B shared/bench/tests/test_decode.py            every test
    python -B shared/bench/tests/test_decode.py --can-fail  every test again
        under each of its mutants (a broken copy of what it guards), each of
        which must make it FAIL
    python -B shared/bench/tests/test_decode.py NAME...     some tests

The golden DBC (data/golden_layout.dbc) uses every feature the hash reads:
an extended id, a big-endian signed signal, an IEEE float, non-unit scale
and offset, a multiplexer, choices written out of order, plus the
comments, units, ranges, nodes and attributes it must ignore. Its
canonical text (data/golden_layout.txt) was written by hand from the rules
in decode.py's docstring, not generated, and GOLDEN_HASH is that text's
hash: a change to the canonical form fails here first.

Needs cantools and git. Scratch git repos go to the machine temp folder
(<tempdir>/vtrux-bench-tests/), never into a synced folder.
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vtrux_bench import decode as D                 # noqa: E402

DATA = os.path.join(HERE, "data")
GOLDEN_DBC = os.path.join(DATA, "golden_layout.dbc")
GOLDEN_TXT = os.path.join(DATA, "golden_layout.txt")
GOLDEN_HASH = "8c585cd221163525"
SCRATCH = os.path.join(tempfile.gettempdir(), "vtrux-bench-tests")


def golden_text():
    with open(GOLDEN_DBC, encoding="utf-8") as f:
        return f.read()


def edited(text, edits):
    """Apply (old, new) pairs; each old must occur exactly once. A callable
    edit takes and returns the whole text."""
    for e in edits:
        if callable(e):
            text = e(text)
            continue
        a, b = e
        assert text.count(a) == 1, "edit target %r occurs %d times" % (
            a, text.count(a))
        text = text.replace(a, b)
    return text


def h(text):
    return D.layout_hash(D.load(text=text))


# ---- tests --------------------------------------------------------------

def test_golden():
    """The golden DBC gives the hand-written canonical text and its hash,
    and states both its version and its own hash."""
    db = D.load(GOLDEN_DBC)
    with open(GOLDEN_TXT, encoding="utf-8") as f:
        want = f.read()
    got = D.canonical_text(db)
    assert got == want, "canonical text differs:\n%s\n--- want ---\n%s" % (
        got, want)
    assert D.layout_hash(db) == GOLDEN_HASH, D.layout_hash(db)
    assert D.schema_identity(db) == (7, GOLDEN_HASH), D.schema_identity(db)
    assert D.check_identity(db) == [], D.check_identity(db)


_SG_SEL = ' SG_ sel M : 0|8@1+ (1,0) [0|0] "" RX\n'
_EXT_BLOCK_END = ' SG_ volts : 32|32@1- (1,0) [0|0] "V" RX\n'
_MUX_BLOCK = ('BO_ 300 Mux: 8 TX\n'
              ' SG_ pageB m2 : 8|16@1+ (1,10) [0|0] "" RX\n'
              + _SG_SEL +
              ' SG_ pageA m1 : 8|16@1+ (0.01,0) [0|0] "kWh" RX\n\n')

# Every edit here changes how a frame decodes or what a value is called.
SENSITIVE = [
    ("start bit", [("SG_ temp : 7|12", "SG_ temp : 15|12")]),
    ("length", [("SG_ state : 8|8@", "SG_ state : 8|7@")]),
    ("byte order", [("SG_ state : 8|8@1+", "SG_ state : 8|8@0+")]),
    ("signedness", [("SG_ state : 8|8@1+", "SG_ state : 8|8@1-")]),
    ("float type", [("SIG_VALTYPE_ 2566848737 volts : 1;", "")]),
    ("scale", [("(0.01,0)", "(0.1,0)")]),
    ("offset", [("(1,10)", "(1,11)")]),
    ("mux id", [("pageB m2", "pageB m3")]),
    ("choice value", [('6 "HOLD"', '7 "HOLD"')]),
    ("choice name", [('"HOLD"', '"HELD"')]),
    ("choice added", [('2 "OVERRIDE"', '2 "OVERRIDE" 4 "TERMINATED"')]),
    ("frame id", [("BO_ 300 Mux", "BO_ 301 Mux")]),
    ("dlc", [("BO_ 500 Status: 4", "BO_ 500 Status: 5")]),
    ("extended flag", [("BO_ 300 Mux", "BO_ 2147483948 Mux")]),
    ("message name", [("BO_ 500 Status:", "BO_ 500 State:")]),
    # pageX still sorts after pageA, so only the name moves the hash
    ("signal name", [("SG_ pageB m2", "SG_ pageX m2")]),
    ("message added", [('CM_ "Comments', 'BO_ 600 New: 1 TX\n'
                        ' SG_ n : 0|1@1+ (1,0) [0|1] "" RX\n\nCM_ "Comments')]),
]

# None of these changes a decode or a name.
INSENSITIVE = [
    ("signal comment", [("A signal comment.", "Another comment.")]),
    ("message comment", [("A message comment.", "Reworded.")]),
    ("unit", [('"degC"', '"K"')]),
    ("range", [("[0|15]", "[0|7]")]),
    ("node list", [("BU_: TX RX", "BU_: TX RX EXTRA")]),
    ("sender", [("BO_ 300 Mux: 8 TX", "BO_ 300 Mux: 8 RX")]),
    ("cycle time attribute", [("BO_ 500 1000;", "BO_ 500 100;")]),
    ("RenamedFrom", [('"vcu_mode"', '"older_mode,vcu_mode"')]),
    ("stated hash", [('"%s"' % GOLDEN_HASH, '"0000000000000000"')]),
    ("stated version", [('"DiagSchemaVersion" 7;', '"DiagSchemaVersion" 8;')]),
    ("integral scale written 1 vs 1.0", [("(1.0,0.0)", "(1,0)")]),
    ("message order in the file",
     [(_MUX_BLOCK, ""), ("BO_ 2566848737", _MUX_BLOCK + "BO_ 2566848737")]),
    ("signal order in the file",
     [(_SG_SEL, ""), (' SG_ pageB m2', _SG_SEL + ' SG_ pageB m2')]),
    ("signal order, big-endian first",
     [(_EXT_BLOCK_END, ""),
      (' SG_ temp :', _EXT_BLOCK_END + ' SG_ temp :')]),
    ("VAL_ order", [('6 "HOLD" 0 "PASSTHROUGH" 2 "OVERRIDE"',
                     '2 "OVERRIDE" 6 "HOLD" 0 "PASSTHROUGH"')]),
]


def test_sensitive():
    """Each layout edit changes the hash. Compared with the unedited
    DBC's hash under the same code; test_golden pins the value."""
    base = golden_text()
    ref = h(base)
    same = [name for name, e in SENSITIVE if h(edited(base, e)) == ref]
    assert not same, "hash unchanged by: %s" % ", ".join(same)


def test_insensitive():
    """No edit outside the layout changes the hash (against the
    unedited DBC's hash under the same code)."""
    base = golden_text()
    ref = h(base)
    moved = [name for name, e in INSENSITIVE if h(edited(base, e)) != ref]
    assert not moved, "hash changed by: %s" % ", ".join(moved)


def test_unsafe_choice():
    """A choice name holding '|' or '=' is refused, not hashed."""
    base = golden_text()
    for bad in ("HO|LD", "HO=LD"):
        db = D.load(text=edited(base, [('"HOLD"', '"%s"' % bad)]))
        try:
            D.layout_hash(db)
        except D.DecodeError as e:
            assert "refused" in str(e), e
        else:
            raise AssertionError("hashed a choice named %r" % bad)


def test_identity():
    """A DBC must state an integer DiagSchemaVersion and a DiagSchemaHash
    equal to its own layout_hash."""
    base = golden_text()
    cases = [
        ("no version", [('BA_ "DiagSchemaVersion" 7;\n', "")], "integer"),
        ("no version definition",
         [('BA_ "DiagSchemaVersion" 7;\n', ""),
          ('BA_DEF_ "DiagSchemaVersion" INT 0 255;\n', ""),
          ('BA_DEF_DEF_ "DiagSchemaVersion" 0;\n', "")], "integer"),
        ("string version",
         [('BA_DEF_ "DiagSchemaVersion" INT 0 255;',
           'BA_DEF_ "DiagSchemaVersion" STRING ;'),
          ('BA_DEF_DEF_ "DiagSchemaVersion" 0;',
           'BA_DEF_DEF_ "DiagSchemaVersion" "";'),
          ('BA_ "DiagSchemaVersion" 7;', 'BA_ "DiagSchemaVersion" "7";')],
         "integer"),
        ("no hash", [('BA_ "DiagSchemaHash" "%s";\n' % GOLDEN_HASH, "")],
         "no DiagSchemaHash"),
        ("stale hash", [("(0.01,0)", "(0.1,0)")], "says " + GOLDEN_HASH),
    ]
    for name, e, want in cases:
        probs = D.check_identity(D.load(text=edited(base, e)))
        assert any(want in p for p in probs), "%s: %r" % (name, probs)


def _rmtree(p):
    def unlock(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if os.path.exists(p):
        shutil.rmtree(p, onerror=unlock)


def _repo(name):
    p = os.path.join(SCRATCH, "decode-" + name)
    _rmtree(p)
    os.makedirs(p)
    subprocess.run(["git", "init", "-q", p], check=True)
    return p


def _commit(repo, text, msg):
    os.makedirs(os.path.join(repo, "diag"), exist_ok=True)
    with open(os.path.join(repo, "diag", "x.dbc"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(text)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c",
                    "user.email=t@t", "commit", "-q", "-m", msg], check=True)


def _restate(text, version):
    """The text with DiagSchemaVersion `version` and its own hash."""
    text = text.replace('"DiagSchemaVersion" 7;',
                        '"DiagSchemaVersion" %d;' % version)
    return text.replace('"%s"' % GOLDEN_HASH, '"%s"' % h(text))


def test_history():
    """Across the DBC's git history: every commit's identity is checked,
    each DiagSchemaVersion has one layout, and a shallow clone is
    refused."""
    base = golden_text()
    layout2 = edited(base, [("(0.01,0)", "(0.1,0)")])
    layout3 = edited(base, [("(1,10)", "(1,11)")])

    good = _repo("history-good")
    _commit(good, base, "schema 7")
    _commit(good, edited(base, [("A signal comment.", "Reworded.")]),
            "comment only, still 7")
    _commit(good, _restate(layout2, 8), "schema 8")
    assert D.check_history(good, "diag/x.dbc") == [], \
        D.check_history(good, "diag/x.dbc")

    _commit(good, _restate(layout3, 8), "a new layout still called 8")
    probs = D.check_history(good, "diag/x.dbc")
    assert any("DiagSchemaVersion 8 has 2 layouts" in p for p in probs), probs

    stale = _repo("history-stale")
    _commit(stale, base, "schema 7")
    _commit(stale, layout2, "layout changed, hash not restated")
    probs = D.check_history(stale, "diag/x.dbc")
    assert any("DiagSchemaHash says" in p for p in probs), probs

    shallow = os.path.join(SCRATCH, "decode-history-shallow")
    _rmtree(shallow)
    subprocess.run(["git", "clone", "-q", "--depth", "1",
                    "file:///" + good.replace("\\", "/"), shallow],
                   check=True)
    try:
        D.check_history(shallow, "diag/x.dbc")
    except D.DecodeError as e:
        assert "shallow" in str(e), e
    else:
        raise AssertionError("a shallow clone's history was accepted")


TESTS = [test_golden, test_sensitive, test_insensitive, test_unsafe_choice,
         test_identity, test_history]


# ---- mutants (--can-fail) -------------------------------------------------

def _patch(attr, value):
    saved = {}

    def on():
        saved["v"] = getattr(D, attr)
        setattr(D, attr, value(saved["v"]) if callable(value) and
                getattr(value, "_wraps", False) else value)

    def off():
        setattr(D, attr, saved["v"])
    return on, off


def _wrap(fn):
    fn._wraps = True
    return fn


def _blank(attr, k):
    """The line builder `attr` with field k emptied."""
    return _patch(attr, _wrap(lambda orig: lambda x: "|".join(
        "" if i == k else f for i, f in enumerate(orig(x).split("|")))))


def _file_order_text(db):
    out = []
    for m in db.messages:
        out.append(D._msg_line(m))
        for s in m.signals:
            out.append(D._sig_line(s))
            out.extend(D._choice_lines(s))
    return "".join(line + "\n" for line in out)


def _msgs_unsorted(db):
    out = []
    for m in db.messages:
        out.append(D._msg_line(m))
        for s in sorted(m.signals, key=lambda s: (s.start, s.name)):
            out.append(D._sig_line(s))
            out.extend(D._choice_lines(s))
    return "".join(line + "\n" for line in out)


def _shallow_unchecked(orig):
    def git(repo, *args):
        if args[:2] == ("rev-parse", "--is-shallow-repository"):
            return b"false\n"
        return orig(repo, *args)
    return git


MUTANTS = {
    "test_golden": [
        ("scale and offset by repr(x)", _patch("_num", repr)),
        ("12 hex characters", _patch("HASH_CHARS", 12)),
    ],
    "test_sensitive": [
        ("no signal name", _blank("_sig_line", 1)),
        ("no start bit", _blank("_sig_line", 2)),
        ("no length", _blank("_sig_line", 3)),
        ("no byte order", _blank("_sig_line", 4)),
        ("no u/s/f", _blank("_sig_line", 5)),
        ("no scale", _blank("_sig_line", 6)),
        ("no offset", _blank("_sig_line", 7)),
        ("no mux", _blank("_sig_line", 8)),
        ("no frame id", _blank("_msg_line", 1)),
        ("no message name", _blank("_msg_line", 2)),
        ("no dlc", _blank("_msg_line", 3)),
        ("no extended flag", _blank("_msg_line", 4)),
        ("no choices", _patch("_choice_lines", lambda s: [])),
        ("choice values only", _patch("_choice_lines", _wrap(
            lambda orig: lambda s: [c.split("=")[0] for c in orig(s)]))),
    ],
    "test_insensitive": [
        ("units hashed", _patch("_sig_line", _wrap(
            lambda orig: lambda s: orig(s) + "|" + str(s.unit)))),
        ("comments hashed", _patch("_msg_line", _wrap(
            lambda orig: lambda m: orig(m) + "|" + str(m.comment)))),
        ("senders hashed", _patch("_msg_line", _wrap(
            lambda orig: lambda m: orig(m) + "|" + ",".join(m.senders)))),
        ("ranges hashed", _patch("_sig_line", _wrap(
            lambda orig: lambda s: orig(s) + "|" + str(s.maximum)))),
        # repr(x) for repr(float(x)) is caught by test_golden: cantools
        # 44 already parses 1.0 as int 1, so no edit here can show it.
        ("messages in file order", _patch("canonical_text", _msgs_unsorted)),
        ("everything in file order", _patch("canonical_text",
                                            _file_order_text)),
        ("choices in file order", _patch("_choice_lines", _wrap(
            lambda orig: lambda s: ["C|%d=%s" % (int(v), n) for v, n in
                                    (s.choices or {}).items()]))),
    ],
    "test_unsafe_choice": [
        ("no refusal", _patch("UNSAFE", ())),
    ],
    "test_identity": [
        ("stated identity trusted", _patch("schema_identity", _wrap(
            lambda orig: lambda db: (7, D.layout_hash(db))))),
    ],
    "test_history": [
        ("per-commit identity unchecked",
         _patch("check_identity", lambda db: [])),
        ("versions not compared", _patch("_layout_conflicts",
                                         lambda rows: [])),
        ("shallow clone not refused", _patch("_git", _wrap(
            _shallow_unchecked))),
    ],
}


def _run(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    tests = [t for t in TESTS if not names or t.__name__ in names]
    bad = total = 0
    for t in tests:
        if not can_fail:
            total += 1
            outcome = _run(t)
            print("%s  %-20s %s" % ("ok  " if outcome is None else "FAIL",
                                    t.__name__, outcome or ""))
            bad += outcome is not None
            continue
        for label, (on, off) in MUTANTS[t.__name__]:
            total += 1
            on()
            try:
                outcome = _run(t)
            finally:
                off()
            ok = outcome is not None
            print("%s  %-20s %-32s %s" % (
                "ok  " if ok else "FAIL", t.__name__, label,
                ("fails (" + outcome[:80] + ")") if ok else
                "STILL PASSES -- the test checks nothing"))
            bad += not ok
    print("\n%d of %d %s" % (total - bad, total,
                             "mutants caught" if can_fail else "passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
