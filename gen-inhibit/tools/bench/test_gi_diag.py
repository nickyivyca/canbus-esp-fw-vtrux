#!/usr/bin/env python3
"""Tests gi_diag.py, gen-inhibit's diag decoder (port plan toolkit item 10).

Gen-inhibit tester, 2026-10-24, from the reviewer's build message and readings Q1 to Q6
of that day. The real DBC is read from this repo's git; the rest runs on fake
repositories in this run's scratch/ folder, whose DBC history is the real schema 7
followed by a made-up schema 8. Schema 8 renames diag_shift_pos to diag_shift_lever
(RenamedFrom), narrows diag_ctr_bad to 4 bits, adds diag_build_extra on 0x7F3 B7 and a
page 0x7FA, so each rule has an input where it matters.

  1. The schema set from git: the real repo gives schema 7 at 9c54669, hash
     7af6adef1d948197, and schema 8 at 564c129, hash 00401e953c405902, at HEAD
     (port step 0c). A history of 7, 7 (comment only) and 8 gives two
     schemas, the later commit of 7, HEAD 8. A version with two layouts, a stated hash
     that is not the DBC's own, a schema signal away from the table's byte and a DBC
     with no schema signal are each refused.
  2. Decoding (readings Q1, Q2): a schema byte no DBC states is refused; a page before
     the first schema page is Undecodable, counted, and the capture goes on; 0x7F2 is
     decoded with the latest schema page's layout, switching at each one, from 0x7F1
     and from 0x7F3; a schema page too short for its byte and a page the current
     schema does not have are Undecodable; a frame of another id is None. Against a
     run identity, a schema page whose DBC is not the record's is refused. A 0x7F0
     probe, outside the schema (reading Q8, 2026-10-25), decodes before the first schema
     page and after a switch alike, uncounted; a history whose WICAN_Probe layout changes
     (width, scale) or drops it is refused, a comment-only change is not.
  3. run_identity: a modified, staged or deleted DBC is refused; a clean one gives the
     harness commit and the DBC's commit, version and hash.
  4. Off git: an export reads back the same set; a manifest hash, a DBC text and a
     version that disagree are each refused; the export carries the pinned decode.py.
  5. Pins: core() refuses a decode.py whose blob is not the pin's; the interposer DBC
     is refused unpinned and on a sha256 that differs, and reads at its pin.
  6. Golden traces (reading Q3): the same pages on schema 7 and 8 compare equal,
     through the rename and the schema byte; a value change, a signal on one side only
     and a changed non-diag line each differ; an undecodable line keeps its raw text.
  7. No file under tools/ or test/ but this module and its tests names a diag DBC or
     the schema doc as a string, and gi_paths no longer resolves the WiCAN DBC.

    py -3.14 -B test_gi_diag.py      # exit 0 only if every check passes

Its mutants: test_gi_diag_mutants.py. GI_DIAG_DIR runs another copy of gi_diag.py.
"""
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("GI_DIAG_DIR") or HERE
REPO = gi_paths.repo_root()
PROJECT = gi_paths.project_root()     # gen-inhibit/ since port step 0d: tools/ and test/
FAILS = []

spec = importlib.util.spec_from_file_location("gi_diag_under_test", os.path.join(SRC_DIR, "gi_diag.py"))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)


def check(label, fn, detail=""):
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001  a crash is this check's FAIL
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


def raises(fn, cls=Exception, text=""):
    try:
        fn()
    except cls as e:
        return text in str(e)
    except Exception:           # noqa: BLE001  the wrong kind of error is not a refusal
        return False
    return False


# ---------------------------------------------------------------- fake histories

REAL7 = G.git(REPO, "show", "9c54669:%s" % G.DBC_PATH).decode("utf-8")


def rehash(text):
    """The text with its DiagSchemaHash set to its own layout hash."""
    D = G.core()
    import re
    cur = re.search(r'BA_ "DiagSchemaHash" "([0-9a-f]*)";', text).group(1)
    h = D.layout_hash(D.load(text=text))
    return text.replace('BA_ "DiagSchemaHash" "%s";' % cur, 'BA_ "DiagSchemaHash" "%s";' % h)


def schema8(base=REAL7):
    t = base.replace('BA_ "DiagSchemaVersion" 7;', 'BA_ "DiagSchemaVersion" 8;')
    t = t.replace("diag_shift_pos", "diag_shift_lever")
    t = t.replace(" SG_ diag_ctr_bad : 40|8@1+", " SG_ diag_ctr_bad : 40|4@1+")
    t = t.replace(' SG_ diag_schema_ver_build : 48|8@1+ (1,0) [0|255] "" Vector__XXX\n',
                  ' SG_ diag_schema_ver_build : 48|8@1+ (1,0) [0|255] "" Vector__XXX\n'
                  ' SG_ diag_build_extra : 56|8@1+ (1,0) [0|255] "" Vector__XXX\n')
    t = t.replace("\nCM_ ", '\nBO_ 2042 WICAN_Diag_New: 8 WICAN\n SG_ diag_new : 0|8@1+ (1,0) [0|255] "" Vector__XXX\n\nCM_ ', 1)
    t = t.replace('BA_ "DiagSchemaVersion" 8;',
                  'BA_ "DiagSchemaVersion" 8;\nBA_ "RenamedFrom" SG_ 2033 diag_shift_lever "diag_shift_pos";')
    return rehash(t)


def comment_only(text):
    return text.replace("Constant 0x5A.", "Constant 0x5A, always.")


def fake_repo(name, texts):
    """A git repo whose DBC history is `texts`, one commit each; -> (path, commits)."""
    d = os.path.join(BASE, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, os.path.dirname(G.DBC_PATH)))

    def g(*a):
        return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                               "-c", "core.autocrlf=false"] + list(a), check=True, capture_output=True,
                              text=True).stdout.strip()
    g("init", "-q")
    commits = []
    for i, t in enumerate(texts):
        with open(os.path.join(d, G.DBC_PATH), "w", encoding="utf-8", newline="") as fh:
            fh.write(t)
        g("add", "-A")
        g("commit", "-q", "-m", "dbc %d" % i)
        commits.append(g("rev-parse", "HEAD"))
    return d, commits, g


def page(fid, hexs):
    return fid, bytes.fromhex(hexs)


BASE = gi_paths.scratch("gi_diag", start=HERE)
S8 = schema8()
R2, C2, g2 = fake_repo("two", [REAL7, comment_only(REAL7), S8])
SET2 = G.SchemaSet.from_git(R2)


# ---------------------------------------------------------------- 1. the set from git

def t1():
    real = G.SchemaSet.from_git(REPO)
    e, e8 = real.get(7), real.get(8)
    check("1 the real repo: schema 7 at 9c54669, hash 7af6adef1d948197; schema 8 at 564c129, hash "
          "00401e953c405902, at HEAD",
          lambda: (sorted(real.entries), e.commit[:7], e.hash, e8.commit[:7], e8.hash, real.head_version) ==
          ([7, 8], "9c54669", "7af6adef1d948197", "564c129", "00401e953c405902", 8),
          (sorted(real.entries), e.commit[:7], e.hash, e8.commit[:7], e8.hash, real.head_version))
    check("1 7, 7 (comment), 8: schemas 7 and 8, HEAD 8",
          lambda: (sorted(SET2.entries), SET2.head_version) == ([7, 8], 8))
    check("1 the later commit of a version is the one kept", lambda: SET2.get(7).commit == C2[1])
    check("1 each DBC's hash is its own layout_hash",
          lambda: all(G.core().layout_hash(e.db) == e.hash for e in SET2.entries.values()))
    two_layouts = rehash(REAL7.replace(" SG_ diag_ctr_bad : 40|8@1+", " SG_ diag_ctr_bad : 40|4@1+"))
    r, _c, _g = fake_repo("twolayouts", [REAL7, two_layouts])
    check("1 a version with two layouts is refused",
          lambda: raises(lambda: G.SchemaSet.from_git(r), G.DiagError, "2 layouts"))
    stale = REAL7.replace(" SG_ diag_ctr_bad : 40|8@1+", " SG_ diag_ctr_bad : 40|4@1+")
    r, _c, _g = fake_repo("stalehash", [stale])
    check("1 a stated hash that is not the DBC's own is refused",
          lambda: raises(lambda: G.SchemaSet.from_git(r), G.DiagError, "layout_hash"))
    moved = rehash(REAL7.replace(" SG_ diag_schema_ver_build : 48|8@1+", " SG_ diag_schema_ver_build : 40|8@1+")
                   .replace(" SG_ diag_fw_ver16 : 32|16@1+", " SG_ diag_fw_ver16 : 24|16@1+")
                   .replace(" SG_ diag_git_hash : 0|32@1+", " SG_ diag_git_hash : 0|24@1+"))
    r, _c, _g = fake_repo("moved", [moved])
    check("1 a schema signal away from the table's byte is refused",
          lambda: raises(lambda: G.SchemaSet.from_git(r), G.DiagError, "the table says byte 6"))
    gone = rehash(REAL7.replace(' SG_ diag_schema_ver : 0|8@1+ (1,0) [0|255] "" Vector__XXX\n', "")
                  .replace("CM_ SG_ 2033 diag_schema_ver ", "CM_ BO_ 2033 "))
    r, _c, _g = fake_repo("gone", [gone])
    check("1 a DBC with no schema signal on 0x7F1 is refused",
          lambda: raises(lambda: G.SchemaSet.from_git(r), G.DiagError, "no signal diag_schema_ver on 0x7F1"))
    signed = rehash(REAL7.replace(" SG_ diag_schema_ver : 0|8@1+", " SG_ diag_schema_ver : 0|8@1-"))
    r, _c, _g = fake_repo("signed", [signed])
    check("1 a signed schema signal is refused",
          lambda: raises(lambda: G.SchemaSet.from_git(r), G.DiagError, "the table says byte 0"))
    check("1 renames: diag_shift_pos -> diag_shift_lever",
          lambda: SET2.renames() == {"diag_shift_pos": "diag_shift_lever"}, SET2.renames())


# ---------------------------------------------------------------- 2. decoding

def t2():
    d = G.Decoder(SET2)
    check("2 a schema byte no DBC states is refused",
          lambda: raises(lambda: d.feed(*page(0x7F1, "0901030600FF0000")), G.UnknownSchema, "schema byte 9"))
    d = G.Decoder(SET2)
    check("2 an unknown schema byte on 0x7F3 is refused",
          lambda: raises(lambda: d.feed(*page(0x7F3, "0000000001000900")), G.UnknownSchema, "schema byte 9"))
    d = G.Decoder(SET2)
    first = d.feed(*page(0x7F2, "0000000000F00000"), t=1)
    check("2 a page before the first schema page is Undecodable",
          lambda: isinstance(first, G.Undecodable) and first.reason == "before the first schema page", first)
    check("2 ... and not truthy, so it is never read as a decode", lambda: not first)
    p = d.feed(*page(0x7F1, "0701030600FF0000"), t=2, decode_choices=False)
    check("2 ... the capture goes on: the next 0x7F1 decodes on schema 7",
          lambda: d.version == 7 and p["diag_mode"] == 3, (d.version, p))
    p = d.feed(*page(0x7F2, "0000000000F00000"), t=3)
    check("2 0x7F2 after schema 7: diag_ctr_bad 8 bits (0xF0 = 240)", lambda: p["diag_ctr_bad"] == 240, p)
    d.feed(*page(0x7F1, "0801030600FF0000"), t=4)
    p = d.feed(*page(0x7F2, "0000000000F00000"), t=5)
    check("2 0x7F2 after schema 8: diag_ctr_bad 4 bits (0)", lambda: p["diag_ctr_bad"] == 0, p)
    d.feed(*page(0x7F3, "0000000001000700"), t=6)
    p = d.feed(*page(0x7F2, "0000000000F00000"), t=7)
    check("2 0x7F3 switches the schema too (back to 7)", lambda: p["diag_ctr_bad"] == 240 and d.version == 7, p)
    check("2 switches recorded with their times", lambda: d.switches == [(2, 7), (4, 8), (6, 7)], d.switches)
    s = d.feed(*page(0x7F3, "00000000"), t=8)
    check("2 a 0x7F3 too short for its schema byte is Undecodable",
          lambda: isinstance(s, G.Undecodable) and "too short" in s.reason, s)
    s = d.feed(*page(0x7FA, "01"), t=9)
    check("2 a page the current schema does not have is Undecodable",
          lambda: isinstance(s, G.Undecodable) and s.reason == "not in schema 7", s)
    check("2 a frame of another id is None", lambda: d.feed(*page(0x051, "080080FF7F08")) is None)
    check("2 undecodable counted: 3", lambda: len(d.undecodable) == 3 and d.summary()["undecodable"] == 3,
          d.summary())
    check("2 the summary lists the schemas in order", lambda: d.summary()["schemas"] == [7, 8, 7])
    ident8 = {"dbc_hash": SET2.get(8).hash, "dbc_commit": SET2.get(8).commit}
    d = G.Decoder(SET2, ident8)
    check("2 against a run identity: its own schema decodes",
          lambda: d.feed(*page(0x7F1, "0801030600FF0000"), decode_choices=False)["diag_mode"] == 3)
    check("2 against a run identity: another schema is refused",
          lambda: raises(lambda: d.feed(*page(0x7F1, "0701030600FF0000")), G.SchemaMismatch, "run record"))
    d = G.Decoder(SET2, ident8)
    check("2 against a run identity: refused on 0x7F3 too",
          lambda: raises(lambda: d.feed(*page(0x7F3, "0000000001000700")), G.SchemaMismatch))

    # 0x7F0 is outside the schema (reviewer, 2026-10-25, reading Q8): decoded before the
    # first schema page, never counted undecodable, never moving the schema.
    d = G.Decoder(SET2)
    p = d.feed(*page(0x7F0, "40420F00F401035A"), t=1, decode_choices=False)
    check("2 a 0x7F0 probe before the first schema page decodes",
          lambda: isinstance(p, dict) and p["probe_marker"] == 0x5A and p["probe_vcm_ctr"] == 3, p)
    check("2 ... it is not counted undecodable and sets no schema",
          lambda: not d.undecodable and d.version is None and not d.switches, (d.undecodable, d.version))
    d.feed(*page(0x7F1, "0801030600FF0000"), t=2)
    p8 = d.feed(*page(0x7F0, "40420F00F401035A"), t=3, decode_choices=False)
    check("2 ... and decodes the same after a schema 8 page", lambda: p8 == p, p8)
    d = G.Decoder(SET2, ident8)
    check("2 a probe is decoded against a run identity too (it carries no schema)",
          lambda: d.feed(*page(0x7F0, "40420F00F401035A"), decode_choices=False)["probe_vcm_ctr"] == 3)
    # The guard: WICAN_Probe's layout must be the same in every DBC the set can select.
    narrowed = rehash(S8.replace(" SG_ probe_vcm_ctr : 48|8@1+ (1,0) [0|15]", " SG_ probe_vcm_ctr : 48|4@1+ (1,0) [0|15]"))
    r, _c, _g = fake_repo("probe_changed", [REAL7, narrowed])
    check("2 a history whose WICAN_Probe layout changes is refused",
          lambda: raises(lambda: G.SchemaSet.from_git(r), G.DiagError, "outside the schema"))
    scaled = rehash(S8.replace(' SG_ probe_offset_us : 32|16@1+ (1,0) [0|65535] "us"',
                               ' SG_ probe_offset_us : 32|16@1+ (2,0) [0|65535] "us"'))   # the scale alone
    r, _c, _g = fake_repo("probe_scaled", [REAL7, scaled])
    check("2 ... a changed scale too",
          lambda: raises(lambda: G.SchemaSet.from_git(r), G.DiagError, "outside the schema"))
    import re as _re
    gone = rehash(_re.sub(r"BO_ 2032 WICAN_Probe: 8 WICAN\n(?: SG_ [^\n]*\n)+\n", "", S8))
    r, _c, _g = fake_repo("probe_gone", [REAL7, gone])
    check("2 ... and a DBC without WICAN_Probe",
          lambda: "WICAN_Probe" not in gone and raises(lambda: G.SchemaSet.from_git(r), G.DiagError, "outside the schema"))
    check("2 a comment-only change to the probe is not a layout change (the set with it builds)",
          lambda: "Constant 0x5A, always." in comment_only(REAL7) and SET2.get(8) is not None)


# ---------------------------------------------------------------- 3. run_identity

def t3():
    r, c, g = fake_repo("ident", [REAL7, S8])
    idn = G.run_identity(r)
    check("3 a clean DBC: harness commit, DBC commit, version and hash",
          lambda: idn == {"harness_commit": c[1], "dbc_path": G.DBC_PATH, "dbc_commit": c[1],
                          "dbc_version": 8, "dbc_hash": G.SchemaSet.from_git(r).get(8).hash}, idn)
    with open(os.path.join(r, "README"), "w") as fh:
        fh.write("x")
    g("add", "README")
    g("commit", "-q", "-m", "other")
    idn = G.run_identity(r)
    check("3 a later commit not touching the DBC: harness commit moves, DBC commit stays",
          lambda: idn["dbc_commit"] == c[1] and idn["harness_commit"] != c[1], idn)
    p = os.path.join(r, G.DBC_PATH)
    open(p, "a", encoding="utf-8").write("\n")
    check("3 a modified DBC is refused",
          lambda: raises(lambda: G.run_identity(r), G.DiagError, "uncommitted"))
    # given a folder inside the worktree (the project folder, gen-inhibit/, since port step 0d),
    # DBC_PATH is still read from the worktree's root
    check("3 a modified DBC is refused when given the project folder",
          lambda: raises(lambda: G.run_identity(os.path.join(r, "gen-inhibit")), G.DiagError, "uncommitted"))
    g("add", G.DBC_PATH)
    check("3 a staged DBC is refused", lambda: raises(lambda: G.run_identity(r), G.DiagError, "uncommitted"))
    g("reset", "-q", "--hard")
    os.remove(p)
    check("3 a deleted DBC is refused", lambda: raises(lambda: G.run_identity(r), G.DiagError, "uncommitted"))


# ---------------------------------------------------------------- 4. off git

def t4():
    ex = SET2.export(os.path.join(BASE, "export"), repo=REPO)
    back = G.SchemaSet.from_export(ex)
    check("4 an export reads back the same set",
          lambda: {v: (e.commit, e.hash, e.text) for v, e in back.entries.items()} ==
          {v: (e.commit, e.hash, e.text) for v, e in SET2.entries.items()} and back.head_version == 8)
    pin = G.load_pins()["core"][G.CORE_FILE]["blob"]
    check("4 the export carries the pinned decode.py",
          lambda: G.blob_id(open(os.path.join(ex, "vtrux_bench", "decode.py"), "rb").read()) == pin)
    man = json.load(open(os.path.join(ex, G.MANIFEST)))

    def tampered(name, edit_manifest=None, edit_file=None):
        d = os.path.join(BASE, "export_" + name)
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(ex, d)
        m = json.loads(json.dumps(man))
        if edit_manifest:
            edit_manifest(m)
            json.dump(m, open(os.path.join(d, G.MANIFEST), "w"))
        if edit_file:
            fn = os.path.join(d, man["schemas"][0]["file"])
            text = edit_file(open(fn, encoding="utf-8", newline="").read())
            open(fn, "w", encoding="utf-8", newline="").write(text)
        return d

    def bad_hash(m):
        m["schemas"][0]["hash"] = "0" * 16
    d = tampered("hash", edit_manifest=bad_hash)
    check("4 a manifest hash that is not the DBC's is refused",
          lambda: raises(lambda: G.SchemaSet.from_export(d), G.DiagError, "filed as"))
    d = tampered("text", edit_file=lambda t: t.replace(" SG_ diag_ctr_bad : 40|8@1+", " SG_ diag_ctr_bad : 40|6@1+"))
    check("4 a DBC text whose layout moved is refused",
          lambda: raises(lambda: G.SchemaSet.from_export(d), G.DiagError, "layout_hash"))

    def bad_version(m):
        m["schemas"][0]["version"] = 6
    d = tampered("version", edit_manifest=bad_version)
    check("4 a DBC filed under another version is refused",
          lambda: raises(lambda: G.SchemaSet.from_export(d), G.DiagError, "filed as 6"))
    root = os.path.join(BASE, "copy")
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(ex, os.path.join(root, "diag_schemas"))
    check("4 schemas_for a copy with no git reads its diag_schemas/",
          lambda: sorted(G.schemas_for(root).entries) == [7, 8])
    check("4 schemas_for a folder with neither is refused",
          lambda: raises(lambda: G.schemas_for(os.path.join(BASE, "nothing")), G.DiagError, "neither"))


# ---------------------------------------------------------------- 5. pins

def t5():
    pins = G.load_pins()
    bad = json.loads(json.dumps(pins))
    bad["core"][G.CORE_FILE]["blob"] = "0" * 40
    check("5 core() refuses a decode.py whose blob is not the pin's",
          lambda: raises(lambda: G.core(pins=bad), G.DiagError, "its pin is"))
    check("5 the core pin is shared/main b9896a0", lambda: pins["core"][G.CORE_FILE]["commit"].startswith("b9896a0"))
    nopin = json.loads(json.dumps(pins))
    nopin["git"] = {}
    check("5 the interposer DBC unpinned is refused",
          lambda: raises(lambda: G.interposer_dbc(REPO, nopin), G.DiagError, "unpinned"))
    wrong = json.loads(json.dumps(pins))
    wrong["git"][G.INTERPOSER_DBC_PATH]["sha256"] = "0" * 64
    check("5 the interposer DBC on a sha256 that differs is refused",
          lambda: raises(lambda: G.interposer_dbc(REPO, wrong), G.DiagError, "its pin is"))
    check("5 the interposer DBC reads at its pin (d83fc87), with 0x7F4",
          lambda: pins["git"][G.INTERPOSER_DBC_PATH]["commit"].startswith("d83fc87") and
          G.interposer_dbc(REPO).get_message_by_frame_id(0x7F4) is not None)


# ---------------------------------------------------------------- 6. golden traces

T7 = ("0 TX DIAG id=7F1 dlc=8 data=0701030600050000 ok=1\n"
      "0 EV MODE 3\n"
      "400000 TX DIAG id=7F2 dlc=8 data=0100000000030000 ok=1\n"
      "500000 TX INHIBIT id=051 dlc=6 data=080080FF7F08 ok=1\n"
      "800000 TX DIAG id=7F8 dlc=8 data=0000000000000000 ok=1\n")


def t6():
    t8 = T7.replace("data=0701030600050000", "data=0801030600050000")
    eq, a, b = G.compare_traces(T7, t8, SET2)
    check("6 the same pages on schema 7 and 8 compare equal (rename, schema byte)", lambda: eq,
          "\n".join(x for x in a + b if "7F1" in x))
    check("6 ... and the rename is read by its current name",
          lambda: any("diag_shift_lever=5" in x for x in a) and any("diag_shift_lever=5" in x for x in b))
    check("6 ... and the schema signals are left out", lambda: not any("diag_schema_ver" in x for x in a + b))
    eq, _a, _b = G.compare_traces(T7, T7.replace("data=0100000000030000", "data=0200000000030000"), SET2)
    check("6 a value change differs", lambda: not eq)
    eq, _a, _b = G.compare_traces(T7, T7.replace("500000 TX INHIBIT id=051 dlc=6 data=080080FF7F08",
                                                 "500000 TX INHIBIT id=051 dlc=6 data=080080FF7F09"), SET2)
    check("6 a changed non-diag line differs", lambda: not eq)
    w = T7 + "900000 TX DIAG id=7F3 dlc=8 data=0000000001000700 ok=1\n"
    g8 = t8 + "900000 TX DIAG id=7F3 dlc=8 data=0000000001000800 ok=1\n"
    eq, a, b = G.compare_traces(w, g8, SET2)
    check("6 a signal on one side only (diag_build_extra, schema 8) differs",
          lambda: not eq and any("diag_build_extra=0" in x for x in b), [x for x in b if "7F3" in x])
    u = "100 TX DIAG id=7F2 dlc=8 data=0100000000030000 ok=1\n" + T7
    a, dec = G.normalise_trace(u, SET2)
    check("6 an undecodable line keeps its raw text, marked",
          lambda: a[0] == "100 TX DIAG id=7F2 dlc=8 data=0100000000030000 ok=1   [undecodable: before the first schema page]"
          and len(dec.undecodable) == 1, a[0])
    eq, _a, _b = G.compare_traces(u, u.replace("100 TX DIAG id=7F2 dlc=8 data=01", "100 TX DIAG id=7F2 dlc=8 data=02"), SET2)
    check("6 ... so two undecodable lines differing in raw bytes differ", lambda: not eq)
    check("6 an unknown schema in a trace is refused",
          lambda: raises(lambda: G.normalise_trace(T7.replace("data=0701", "data=0901"), SET2), G.UnknownSchema))
    check("6 TX PROBE lines of 0x7F0 are decoded too",
          lambda: "probe_marker=90" in G.normalise_trace(
              T7 + "1000500 TX PROBE id=7F0 dlc=8 data=40420F00F401005A ok=1\n", SET2)[0][-1])


# ---------------------------------------------------------------- 7. no other reader

NAMES = ("vtrux-wican-diag.dbc", "wican_diag_schema.md", "vtrux-interposer-diag.dbc")
# A git path of the copies in this repo is how a reader names them (build_output.py reads
# them with git show); any other string naming them is a path outside git.
GIT_PATHS = ("gen-inhibit/diag/", "charge-interposer/diag/")
ALLOWED = {"tools/bench/gi_diag.py", "tools/bench/test_gi_diag.py",
           "tools/bench/test_gi_diag_mutants.py",
           # plants a fake reverse-it copy, to show build_output.py does not read it
           "test/build_checks/test_reference_paths.py"}


def string_hits(path):
    """String constants naming a diag DBC or the schema doc, docstrings left out."""
    src = open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                docs.add(id(b[0].value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs and any(x in n.value for x in NAMES) and not n.value.startswith(GIT_PATHS)]


def t7():
    # from the project folder, so the names are project-relative, as ALLOWED's are
    files = G.git(PROJECT, "ls-files", "tools", "test").decode().split()
    check("7 the scan has files to read (tools/ and test/ of the project folder)",
          lambda: sum(f.endswith(".py") for f in files) > 100, len(files))
    hits = {}
    for f in files:
        if f in ALLOWED or "/golden/" in f:
            continue
        p = os.path.join(PROJECT, f)
        if not os.path.isfile(p):
            continue
        if f.endswith(".py"):
            h = string_hits(p)
        elif f.endswith(".sh"):
            h = [ln.strip() for ln in open(p, encoding="utf-8", errors="replace")
                 if not ln.lstrip().startswith("#") and any(x in ln for x in NAMES)]
        else:
            continue
        if h:
            hits[f] = h
    check("7 no file under tools/ or test/ names a diag DBC or the schema doc as a string, but by its git path",
          lambda: not hits, hits)
    calls = [f for f in files if f.endswith((".py", ".sh")) and f not in ALLOWED and os.path.isfile(os.path.join(PROJECT, f))
             and "diag_dbc(" in open(os.path.join(PROJECT, f), encoding="utf-8", errors="replace").read()]
    check("7 no file calls a diag_dbc() (gi_paths' retired resolver)", lambda: not calls, calls)
    planted = os.path.join(BASE, "planted.py")
    open(planted, "w").write('"""doc naming vtrux-wican-diag.dbc"""\nX = "vtrux-wican-diag.dbc"\n'
                             'Y = "gen-inhibit/diag/vtrux-wican-diag.dbc"\n')
    check("7 ... the scan finds a planted one, and not its docstring or a git path",
          lambda: string_hits(planted) == ["vtrux-wican-diag.dbc"])
    check("7 gi_paths no longer resolves the WiCAN DBC", lambda: not hasattr(gi_paths, "diag_dbc"))


def t8():
    """The export a copy with no git carries, and the helpers the tools use."""
    real = G.SchemaSet.from_git(REPO)
    ident = G.run_identity(REPO, real)
    ex = real.export(os.path.join(BASE, "export8", G.EXPORT_DIR), identity=ident, repo=REPO)
    back = G.SchemaSet.from_export(ex)
    check("8 the export carries the tree's run identity", lambda: back.identity == ident, back.identity)
    check("8 the export carries the interposer DBC at its pin",
          lambda: G.interposer_dbc(export=ex).get_message_by_frame_id(0x7F4) is not None)
    man = json.load(open(os.path.join(ex, G.MANIFEST)))
    bad = dict(man, identity=dict(ident, dbc_hash="0" * 16))
    d = os.path.join(BASE, "export8bad", G.EXPORT_DIR)
    shutil.copytree(ex, d)
    json.dump(bad, open(os.path.join(d, G.MANIFEST), "w"))
    check("8 an export whose identity names another hash is refused",
          lambda: raises(lambda: G.SchemaSet.from_export(d), G.DiagError, "identity names"))
    d2 = os.path.join(BASE, "export8ipd", G.EXPORT_DIR)
    shutil.copytree(ex, d2)
    open(os.path.join(d2, G.INTERPOSER_FILE), "a").write("\n")
    check("8 an export whose interposer DBC differs from its pin is refused",
          lambda: raises(lambda: G.interposer_dbc(export=d2), G.DiagError, "its pin is"))
    # A copy laid out as B15host's (gi_diag.py, pins.json, diag_schemas/ beside them) and as
    # the host suite's (tools/bench/ under a root holding diag_schemas/, the project layout
    # since port step 0d), no git.
    for label, top, sub in (("B15host's copy", "b15copy", ""), ("the host suite's copy", "hostcopy",
                                                                 os.path.join("tools", "bench"))):
        root = os.path.join(BASE, top)
        here = os.path.join(root, sub) if sub else os.path.join(root, "gi_bench_x")
        os.makedirs(here, exist_ok=True)
        for f in ("gi_diag.py", "pins.json"):
            shutil.copy(os.path.join(SRC_DIR if f == "gi_diag.py" else HERE, f), here)
        shutil.copytree(ex, os.path.join(here if not sub else root, G.EXPORT_DIR))
        r = subprocess.run([sys.executable, "-B", os.path.join(here, "gi_diag.py"), "decode"],
                           input="7F2 0000000000000000\n7F1 %02x01030600FF0000\n7F2 0100000000000000\n051 080080FF7F08\n"
                                 "7F3 00000000\n" % real.head_version, capture_output=True, text=True, cwd=here,
                           env=dict(os.environ, PYTHONPATH=""), timeout=120)
        out = r.stdout.splitlines()
        ok = (r.returncode == 0 and len(out) == 5 and "UNDECODABLE: before the first schema page" in out[0]
              and "schema %d" % real.head_version in out[1] and "diag_tx_ok=1" in out[2]
              and "not a diag id" in out[3] and "UNDECODABLE" in out[4] and "too short" in out[4])
        check("8 %s: the CLI decodes from the export, by schema, with no git" % label,
              lambda ok=ok: ok, (r.returncode, out, r.stderr[-300:]))
        r = subprocess.run([sys.executable, "-B", os.path.join(here, "gi_diag.py"), "decode"],
                           input="7F1 EE01030600FF0000\n", capture_output=True, text=True, cwd=here,
                           env=dict(os.environ, PYTHONPATH=""), timeout=120)
        ok = r.returncode != 0 and "UnknownSchema" in r.stderr
        check("8 %s: the CLI refuses an unknown schema (non-zero, UnknownSchema)" % label,
              lambda ok=ok: ok, (r.returncode, r.stderr[-200:]))
    pages, dec = G.decode_pages([(0.2, 0x7F2, "0100000000000000"),
                                 (0.1, 0x7F1, "%02x01030600FF0000" % real.head_version),
                                 (0.3, 0x051, "080080FF7F08"), (0.0, 0x7F8, bytes(8))], real, decode_choices=False)
    check("8 decode_pages: in time order, other ids left out, a page before the first schema page Undecodable",
          lambda: [(t, i) for t, i, _p in pages] == [(0.0, 0x7F8), (0.1, 0x7F1), (0.2, 0x7F2)]
          and isinstance(pages[0][2], G.Undecodable) and pages[2][2]["diag_tx_ok"] == 1
          and dec.summary()["undecodable"] == 1, pages)


def main():
    for t in (t1, t2, t3, t4, t5, t6, t7, t8):
        try:
            t()
        except Exception as e:      # noqa: BLE001
            check("%s ran" % t.__name__, lambda: False, "CRASH %s: %s" % (type(e).__name__, e))
    shutil.rmtree(BASE, ignore_errors=True)
    print("\n%d FAIL" % len(FAILS) if FAILS else "\nall checks pass")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
