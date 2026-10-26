"""Gen-inhibit's diag decoder (port plan toolkit item 10), on shared/bench's
vtrux_bench.decode.

Gen-inhibit tester, 2026-10-24, from the reviewer's build message of that day and
its readings Q1 to Q6. Every bench tool, judge and host check that decodes the
WiCAN's diag pages decodes them through this module, and none reads the DBC itself.

WHERE THE DBC COMES FROM. Only git: gen-inhibit/diag/vtrux-wican-diag.dbc, read with
`git show <commit>:<path>`, never from a working tree, so an uncommitted edit is never
what decodes a capture. run_identity() refuses a working-tree DBC that differs from
HEAD, so a live run cannot start on one either.

PICKING A DBC (reading Q1). By the schema byte each schema page carries (SCHEMA_PAGES:
0x7F1 B0, 0x7F3 B6), from the DBC's git history: one DBC per DiagSchemaVersion, with
vtrux_bench.decode.check_history clean, and each DBC's own schema signals where the
table says. A schema byte no DBC in the history states is refused (UnknownSchema),
never decoded with a fallback.

PAGES WITHOUT A SCHEMA BYTE (0x7F2, 0x7F8, 0x7F9) take the schema of the latest
schema page before them in the same capture. Before the first one they are
UNDECODABLE (reading Q2): Decoder.feed() returns an Undecodable and counts it, the
capture is not failed on them, and a judge that needs one reads ERROR, never "absent".
A capture spanning two schemas switches at each schema page.

FRAMES OUTSIDE THE SCHEMA (UNVERSIONED: 0x7F0, the RESPOND probe WICAN_Probe). The schema
doc: 0x7F0 "is not a diag frame and carries no schema version", and its layout changes
only through that doc and the DBC first. So it is decoded at any point in a capture,
before the first schema page too, with its layout from the set's DBCs, and it never
moves the schema (reviewer, 2026-10-25, reading Q8 of the decode rework). The guard that
keeps this reading honest: every DBC the set can select must give WICAN_Probe the same
layout (id, length, and each signal's bits, sign, scale, offset, range, unit and
choices), or the set is refused, so a future probe change cannot be decoded silently
with another commit's layout.

RUN RECORDS (reading Q1). A live run records run_identity(): the harness commit, and
the DBC's commit, schema version and layout hash. A Decoder given that identity
refuses a schema page whose DBC is not that one (SchemaMismatch). A capture with no
such record (the e3d9437 baseline) is decoded by schema byte alone.

OFF GIT. The host suite and B15host run on madhouse-debian from tar copies with no
history, so export() writes the set (one DBC per version, a manifest of commit, version
and hash, the pinned decode.py, the interposer's DBC at its pin, and the run identity
of the tree it came from) and SchemaSet.from_export() reads it back, checking each DBC's
identity and hash, and the interposer DBC's sha256, against the manifest and the pins
again. schemas_for() and interposer_dbc() find the export when there is no git.

THE CORE PIN. vtrux_bench.decode is used at shared/main b9896a0 (the suite's pin;
reviewer, 2026-10-24). pins.json "core" holds that commit and decode.py's git blob id;
core() refuses a decode.py whose blob differs, wherever it was found.

THE INTERPOSER'S DBC (reading Q5). interposer_dbc() reads
charge-interposer/diag/vtrux-interposer-diag.dbc with `git show` at the
charge-interposer/main commit pinned in pins.json "git": reading only, no merge and
no checkout. An unpinned path, or a file whose sha256 differs from its pin, is refused.

GOLDEN TRACES (reading Q3). normalise_trace() turns a host trace's TX lines of diag
ids into their decoded signals, by name (after RenamedFrom), raw values, without
diag_schema_ver and diag_schema_ver_build; every other line stays as it is. Two traces
are equal when their normalised lines are, so a signal on one side only is a
difference. An undecodable line keeps its raw text, marked.

    py -3.14 -B gi_diag.py identity                  this worktree's run_identity()
    py -3.14 -B gi_diag.py schemas                   the schema set from git
    py -3.14 -B gi_diag.py export DIR                write the set for a copy with no git
    py -3.14 -B gi_diag.py decode [--export DIR]     stdin "ID HEX" lines -> decoded
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PINS_FILE = os.path.join(HERE, "pins.json")
DBC_PATH = "gen-inhibit/diag/vtrux-wican-diag.dbc"
DOC_PATH = "gen-inhibit/diag/wican_diag_schema.md"
CORE_FILE = "shared/bench/vtrux_bench/decode.py"
INTERPOSER_DBC_PATH = "charge-interposer/diag/vtrux-interposer-diag.dbc"
# The schema byte's place on each page that carries one (wican_diag_schema.md,
# "Versions"; reviewer, 2026-10-24): frame id -> (signal name, payload byte).
SCHEMA_PAGES = {0x7F1: ("diag_schema_ver", 0), 0x7F3: ("diag_schema_ver_build", 6)}
SCHEMA_SIGNALS = frozenset(n for n, _b in SCHEMA_PAGES.values())
# Frames outside the schema: frame id -> message name (wican_diag_schema.md, "0x7F0: the
# RESPOND timing probe"; reviewer, 2026-10-25, reading Q8).
UNVERSIONED = {0x7F0: "WICAN_Probe"}
MANIFEST = "manifest.json"
INTERPOSER_FILE = "vtrux-interposer-diag.dbc"
EXPORT_DIR = "diag_schemas"


class DiagError(Exception):
    pass


class UnknownSchema(DiagError):
    """A schema byte no DBC in the history states: refused, never decoded."""


class SchemaMismatch(DiagError):
    """A schema page whose DBC is not the one the run record names."""


class Undecodable(object):
    """A diag page that could not be decoded, with why. Not a decode: a judge that
    needs this page reads ERROR."""
    __slots__ = ("frame_id", "t", "reason")

    def __init__(self, frame_id, t, reason):
        self.frame_id, self.t, self.reason = frame_id, t, reason

    def __bool__(self):
        return False

    def __repr__(self):
        return "Undecodable(0x%03X, %r, %s)" % (self.frame_id, self.t, self.reason)


# ---------------------------------------------------------------- git and pins

def project_root():
    """The project folder (gen-inhibit/ since port step 0d, 2026-10-26), two above this
    module (tools/bench/); a copy with no git has the same layout, with its export at
    this root."""
    return os.path.normpath(os.path.join(HERE, "..", ".."))


def repo_root():
    """This firmware repo's root, one above the project folder. DBC_PATH and the other
    git paths here are relative to it."""
    return os.path.dirname(project_root())


def toplevel(repo):
    """The root of the worktree holding repo. DBC_PATH is repo-relative, and git
    reads a pathspec against -C's folder, so `status -- DBC_PATH` given the project
    folder (gen-inhibit/ since port step 0d) would match nothing and pass."""
    return os.path.normpath(git(repo, "rev-parse", "--show-toplevel").decode().strip())


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    if r.returncode != 0:
        raise DiagError("git %s: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace").strip()[-300:]))
    return r.stdout


def load_pins(path=None):
    with open(path or PINS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def blob_id(data):
    """git's blob id of these bytes, computed without git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


_CORE = {}


def core(search=(), pins=None):
    """vtrux_bench.decode at the pinned commit. Found, in order: importable as
    vtrux_bench.decode; vtrux_bench/decode.py in a folder of `search` (an export);
    or extracted from this repo's git at the pin into scratch/. Refused if its blob
    id is not the pin's."""
    pin = (pins or load_pins())["core"][CORE_FILE]
    if pin["blob"] in _CORE:
        return _CORE[pin["blob"]]
    path = None
    try:
        import vtrux_bench.decode as mod
        path = mod.__file__
    except ImportError:
        for d in search:
            p = os.path.join(d, "vtrux_bench", "decode.py")
            if os.path.isfile(p):
                path = p
                break
    if path is None:
        data = git(repo_root(), "show", "%s:%s" % (pin["commit"], CORE_FILE))
        d = os.path.join(repo_root(), "scratch", "gi-diag-core", pin["commit"][:12], "vtrux_bench")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "decode.py")
        with open(path, "wb") as fh:
            fh.write(data)
    with open(path, "rb") as fh:
        got = blob_id(fh.read())
    if got != pin["blob"]:
        raise DiagError("vtrux_bench.decode at %s has blob %s, its pin is %s (shared/main %s; pins.json core)"
                        % (path, got, pin["blob"], pin["commit"][:10]))
    spec = importlib.util.spec_from_file_location("gi_diag_vtrux_bench_decode", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _CORE[pin["blob"]] = mod
    return mod


def interposer_bytes(repo=None, pins=None, export=None):
    """The interposer's diag DBC, from git at its pinned commit (or from an export, in a
    copy with no git), checked against its pin."""
    pin = (pins or load_pins()).get("git", {}).get(INTERPOSER_DBC_PATH)
    if pin is None:
        raise DiagError("%s has no pin in pins.json \"git\": an unpinned read is refused" % INTERPOSER_DBC_PATH)
    if export is None and repo is None and not in_git():
        export = find_export()
    if export is not None:
        with open(os.path.join(export, INTERPOSER_FILE), "rb") as fh:
            data = fh.read()
    else:
        data = git(repo or repo_root(), "show", "%s:%s" % (pin["commit"], INTERPOSER_DBC_PATH))
    got = hashlib.sha256(data).hexdigest()
    if got != pin["sha256"]:
        raise DiagError("%s at %s has sha256 %s, its pin is %s"
                        % (INTERPOSER_DBC_PATH, pin["commit"][:10], got, pin["sha256"]))
    return data


def interposer_dbc(repo=None, pins=None, export=None):
    """The interposer's diag DBC as a cantools database (interposer_bytes)."""
    data = interposer_bytes(repo, pins, export)
    return core((export,) if export else ()).load(text=data.decode("utf-8"))


# ---------------------------------------------------------------- the schema set

def check_table(db, version):
    """Problems with where this DBC puts the schema byte, against SCHEMA_PAGES."""
    probs = []
    for fid, (name, byte) in sorted(SCHEMA_PAGES.items()):
        try:
            sig = db.get_message_by_frame_id(fid).get_signal_by_name(name)
        except KeyError:
            probs.append("schema %s: no signal %s on 0x%03X" % (version, name, fid))
            continue
        got = (sig.start, sig.length, sig.byte_order, sig.is_signed, float(sig.scale), float(sig.offset))
        want = (byte * 8, 8, "little_endian", False, 1.0, 0.0)
        if got != want:
            probs.append("schema %s: %s on 0x%03X is %r, the table says byte %d (%r)"
                         % (version, name, fid, got, byte, want))
    return probs


def message_layout(db, frame_id):
    """A message's layout, everything that decides its decode, or None if absent."""
    try:
        m = db.get_message_by_frame_id(frame_id)
    except KeyError:
        return None
    sigs = []
    for g in sorted(m.signals, key=lambda x: x.name):
        ch = sorted((int(k), str(v)) for k, v in (g.choices or {}).items())
        sigs.append((g.name, g.start, g.length, g.byte_order, g.is_signed, float(g.scale), float(g.offset),
                     g.minimum, g.maximum, g.unit, tuple(ch)))
    return (m.frame_id, m.name, m.length, tuple(sigs))


def check_unversioned(entries):
    """Problems: a frame outside the schema whose layout is not the same in every DBC."""
    probs = []
    for fid, name in sorted(UNVERSIONED.items()):
        lay = {v: message_layout(e.db, fid) for v, e in entries.items()}
        if len(set(lay.values())) > 1 or None in lay.values() or any(l[1] != name for l in lay.values() if l):
            probs.append("0x%03X %s is outside the schema but its layout differs between the DBCs (%s)"
                         % (fid, name, ", ".join("schema %s: %s" % (v, "absent" if lay[v] is None else
                                                                     "%d signals" % len(lay[v][3]))
                                                 for v in sorted(lay))))
    return probs


class Entry(object):
    __slots__ = ("version", "commit", "hash", "text", "db")

    def __init__(self, version, commit, hash_, text, db):
        self.version, self.commit, self.hash, self.text, self.db = version, commit, hash_, text, db


class SchemaSet(object):
    """One DBC per DiagSchemaVersion. from_git() or from_export(), never built by hand
    outside the tests."""

    def __init__(self, entries, source, head_version=None, identity=None):
        self.entries = dict(entries)
        self.source = source
        self.head_version = head_version
        self.identity = identity        # the exporting tree's run_identity, for an export
        self._renames = None

    @classmethod
    def from_git(cls, repo=None, path=DBC_PATH):
        D = core()
        repo = toplevel(repo or repo_root())
        probs = D.check_history(repo, path)
        if probs:
            raise DiagError("%s's history in git is not clean: %s" % (path, "; ".join(probs)))
        entries = {}
        for c, v, h, _actual, _p in D.history(repo, path):
            text = git(repo, "show", "%s:%s" % (c, path)).decode("utf-8")
            entries[v] = (c, h, text)       # oldest first, so the latest commit of each version stays
        head = git(repo, "log", "-1", "--format=%H", "HEAD", "--", path).decode().strip()
        head_v = next((v for v, (c, _h, _t) in entries.items() if c == head), None)
        return cls._build(entries, "git %s" % repo, head_v)

    @classmethod
    def from_export(cls, folder):
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as fh:
            man = json.load(fh)
        entries = {}
        for row in man["schemas"]:
            with open(os.path.join(folder, row["file"]), encoding="utf-8", newline="") as fh:
                entries[row["version"]] = (row["commit"], row["hash"], fh.read())
        s = cls._build(entries, "export %s" % folder, man.get("head_version"), search=(folder,))
        s.identity = man.get("identity")
        if s.identity is not None and s.get(s.identity["dbc_version"]).hash != s.identity["dbc_hash"]:
            raise DiagError("export %s: its identity names %s, its schema %s is %s"
                            % (folder, s.identity["dbc_hash"], s.identity["dbc_version"],
                               s.get(s.identity["dbc_version"]).hash))
        return s

    @classmethod
    def _build(cls, rows, source, head_version, search=()):
        D = core(search)
        entries = {}
        for v, (c, h, text) in rows.items():
            db = D.load(text=text)
            sv, sh = D.schema_identity(db)
            probs = D.check_identity(db) + check_table(db, v)
            if sv != v:
                probs.append("states DiagSchemaVersion %r, filed as %r" % (sv, v))
            if sh != h:
                probs.append("states DiagSchemaHash %r, filed as %r" % (sh, h))
            if probs:
                raise DiagError("schema %s (%s, %s): %s" % (v, c[:10], source, "; ".join(probs)))
            entries[v] = Entry(v, c, h, text, db)
        if not entries:
            raise DiagError("no diag DBC in %s" % source)
        probs = check_unversioned(entries)
        if probs:
            raise DiagError("%s: %s" % (source, "; ".join(probs)))
        return cls(entries, source, head_version)

    def get(self, version):
        e = self.entries.get(version)
        if e is None:
            raise UnknownSchema("schema byte %r: no DBC in %s states it (known: %s)"
                                % (version, self.source, ", ".join(str(v) for v in sorted(self.entries))))
        return e

    def head(self):
        """The DBC at HEAD (from git) or at the export's HEAD: for encoding test frames."""
        if self.head_version is None:
            raise DiagError("%s has no HEAD schema" % self.source)
        return self.get(self.head_version)

    def unversioned(self, frame_id):
        """A frame outside the schema: its message, the same in every DBC (_build checks)."""
        return self.get(self.head_version if self.head_version in self.entries else max(self.entries))             .db.get_message_by_frame_id(frame_id)

    def frame_ids(self):
        return frozenset(m.frame_id for e in self.entries.values() for m in e.db.messages)

    def renames(self):
        """{earlier name: current name}, from every DBC's RenamedFrom, followed to the end."""
        if self._renames is None:
            step = {}
            for v in sorted(self.entries):
                for m in self.entries[v].db.messages:
                    for s in m.signals:
                        a = (s.dbc.attributes if s.dbc else {}).get("RenamedFrom")
                        for old in re.split(r"[,\s]+", str(a.value if a is not None else "")):
                            if old and old != s.name:
                                step[old] = s.name
            out = {}
            for old in step:
                n, seen = old, set()
                while n in step and n not in seen:
                    seen.add(n)
                    n = step[n]
                out[old] = n
            self._renames = out
        return self._renames

    def export(self, folder, pins=None, identity=None, repo=None):
        """Write the set, the pinned decode.py, the interposer's DBC at its pin and the
        tree's run identity, for a copy of the tree with no git."""
        os.makedirs(os.path.join(folder, "vtrux_bench"), exist_ok=True)
        rows = []
        for v in sorted(self.entries):
            e = self.entries[v]
            fn = "vtrux-wican-diag_schema%d.dbc" % v
            with open(os.path.join(folder, fn), "w", encoding="utf-8", newline="") as fh:
                fh.write(e.text)
            rows.append({"version": v, "commit": e.commit, "hash": e.hash, "file": fn})
        src = core().__file__
        with open(src, "rb") as fh:
            data = fh.read()
        with open(os.path.join(folder, "vtrux_bench", "decode.py"), "wb") as fh:
            fh.write(data)
        open(os.path.join(folder, "vtrux_bench", "__init__.py"), "w").close()
        with open(os.path.join(folder, INTERPOSER_FILE), "wb") as fh:
            fh.write(interposer_bytes(repo, pins))
        pins = pins or load_pins()
        with open(os.path.join(folder, MANIFEST), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"source": self.source, "head_version": self.head_version, "schemas": rows,
                       "identity": identity, "core": pins["core"][CORE_FILE],
                       "interposer": dict(pins["git"][INTERPOSER_DBC_PATH], file=INTERPOSER_FILE)}, fh, indent=1)
            fh.write("\n")
        return folder


def in_git():
    """True when this module sits in a git worktree of this repo (its DBC tracked)."""
    return os.path.exists(os.path.join(repo_root(), ".git")) and os.path.isfile(os.path.join(repo_root(), DBC_PATH))


def find_export():
    """The diag_schemas/ export of a copy with no git: beside this module (B15host's
    copy) or at the copy's root (the host suite's)."""
    for d in (os.path.join(HERE, EXPORT_DIR), os.path.join(project_root(), EXPORT_DIR)):
        if os.path.isfile(os.path.join(d, MANIFEST)):
            return d
    raise DiagError("no git here (%s) and no %s/%s beside %s or at %s"
                    % (repo_root(), EXPORT_DIR, MANIFEST, HERE, project_root()))


def schemas_for(root=None):
    """The schema set: from git when root (default: this repo) is a worktree with the
    DBC, else from the export a copy with no git carries."""
    if root is None:
        return SchemaSet.from_git(repo_root()) if in_git() else SchemaSet.from_export(find_export())
    if os.path.exists(os.path.join(root, ".git")):
        return SchemaSet.from_git(root)
    exp = os.path.join(root, EXPORT_DIR)
    if os.path.isfile(os.path.join(exp, MANIFEST)):
        return SchemaSet.from_export(exp)
    raise DiagError("%s is neither a git worktree nor a copy with %s/%s" % (root, EXPORT_DIR, MANIFEST))


def run_identity(repo=None, schemas=None):
    """What a live run's record keeps about its decoder (reading Q1). Refuses a
    working-tree DBC that is not HEAD's."""
    repo = toplevel(repo or repo_root())
    dirty = git(repo, "status", "--porcelain", "--", DBC_PATH).decode().strip()
    if dirty:
        raise DiagError("%s has uncommitted changes (%s): a run decodes only a committed DBC" % (DBC_PATH, dirty))
    s = schemas or SchemaSet.from_git(repo)
    e = s.head()
    return {"harness_commit": git(repo, "rev-parse", "HEAD").decode().strip(),
            "dbc_path": DBC_PATH, "dbc_commit": e.commit, "dbc_version": e.version, "dbc_hash": e.hash}


# ---------------------------------------------------------------- decoding a capture

class Decoder(object):
    """Decodes one capture's diag pages in order. feed() returns a dict of signals for
    a diag page, None for a frame that is not one, or an Undecodable (counted in
    .undecodable). Raises UnknownSchema, or SchemaMismatch against `identity`."""

    def __init__(self, schemas, identity=None):
        self.schemas = schemas
        self.identity = identity
        self.ids = schemas.frame_ids()
        self.version = None
        self.switches = []          # (t, version) at each change of schema
        self.undecodable = []       # Undecodable

    def _bad(self, fid, t, why):
        u = Undecodable(fid, t, why)
        self.undecodable.append(u)
        return u

    def feed(self, frame_id, data, t=None, **decode_kw):
        if frame_id not in self.ids:
            return None
        data = bytes(data)
        if frame_id in UNVERSIONED:             # outside the schema: any time, never moves it
            return self.schemas.unversioned(frame_id).decode(data, **decode_kw)
        if frame_id in SCHEMA_PAGES:
            byte = SCHEMA_PAGES[frame_id][1]
            if len(data) <= byte:
                return self._bad(frame_id, t, "%d bytes, too short for its schema byte" % len(data))
            e = self.schemas.get(data[byte])
            if self.identity is not None and e.hash != self.identity["dbc_hash"]:
                raise SchemaMismatch("schema byte %d picks %s (%s), the run record names %s (%s)"
                                     % (e.version, e.hash, e.commit[:10], self.identity["dbc_hash"],
                                        self.identity["dbc_commit"][:10]))
            if e.version != self.version:
                self.switches.append((t, e.version))
                self.version = e.version
        if self.version is None:
            return self._bad(frame_id, t, "before the first schema page")
        try:
            msg = self.schemas.get(self.version).db.get_message_by_frame_id(frame_id)
        except KeyError:
            return self._bad(frame_id, t, "not in schema %d" % self.version)
        return msg.decode(data, **decode_kw)

    def summary(self):
        by = {}
        for u in self.undecodable:
            by[u.reason] = by.get(u.reason, 0) + 1
        return {"schemas": [v for _t, v in self.switches], "undecodable": len(self.undecodable),
                "undecodable_by_reason": by}


def decode_pages(frames, schemas=None, identity=None, **decode_kw):
    """[(t, id, data)] (data bytes or hex), in time order -> ([(t, id, page)], Decoder):
    every diag page through one Decoder, page a dict of signals or an Undecodable;
    frames of other ids are left out. Raises UnknownSchema or SchemaMismatch."""
    dec = Decoder(schemas or schemas_for(), identity)
    out = []
    for t, i, d in sorted(frames, key=lambda f: f[0]):
        if i not in dec.ids:
            continue
        out.append((t, i, dec.feed(i, bytes.fromhex(d) if isinstance(d, str) else bytes(d), t=t, **decode_kw)))
    return out, dec


# ---------------------------------------------------------------- host traces

TRACE_RE = re.compile(r"^(?P<t>\d+) TX (?P<kind>\S+) id=(?P<id>[0-9A-Fa-f]+) dlc=(?P<dlc>\d+) "
                      r"data=(?P<data>[0-9A-Fa-f]*)(?P<rest>.*)$")


def normalise_trace(text, schemas):
    """A host trace's lines, with each TX line of a diag id replaced by its decoded
    signals (raw, by current name, schema signals left out)."""
    dec = Decoder(schemas)
    ren = schemas.renames()
    ids = dec.ids
    out = []
    for ln in text.splitlines():
        m = TRACE_RE.match(ln)
        if not m or int(m.group("id"), 16) not in ids:
            out.append(ln)
            continue
        fid = int(m.group("id"), 16)
        page = dec.feed(fid, bytes.fromhex(m.group("data")), t=int(m.group("t")),
                        scaling=False, decode_choices=False)
        if isinstance(page, Undecodable):
            out.append("%s   [undecodable: %s]" % (ln, page.reason))
            continue
        sigs = sorted((ren.get(k, k), v) for k, v in page.items())
        body = " ".join("%s=%s" % (k, v) for k, v in sigs if k not in SCHEMA_SIGNALS)
        out.append("%s TX %s id=%03X dlc=%s {%s}%s" % (m.group("t"), m.group("kind"), fid, m.group("dlc"),
                                                     body, m.group("rest")))
    return out, dec


def compare_traces(want, got, schemas):
    """-> (equal, normalised want, normalised got)."""
    a, _da = normalise_trace(want, schemas)
    b, _db = normalise_trace(got, schemas)
    return a == b, a, b


# ---------------------------------------------------------------- command line

def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("identity")
    sub.add_parser("schemas")
    p = sub.add_parser("export")
    p.add_argument("folder")
    p = sub.add_parser("decode")
    p.add_argument("--export")
    a = ap.parse_args(argv)
    if a.cmd == "identity":
        print(json.dumps(run_identity(), indent=1))
    elif a.cmd == "schemas":
        s = schemas_for()
        for v in sorted(s.entries):
            e = s.entries[v]
            print("schema %d  %s  %s%s" % (v, e.hash, e.commit[:12], "  HEAD" if v == s.head_version else ""))
    elif a.cmd == "export":
        s = SchemaSet.from_git()
        print(s.export(a.folder, identity=run_identity(schemas=s)))
    else:
        s = SchemaSet.from_export(a.export) if a.export else schemas_for()
        dec = Decoder(s)
        for ln in sys.stdin:
            f = ln.split()
            if len(f) < 2:
                continue
            try:
                page = dec.feed(int(f[0], 16), bytes.fromhex(f[1]), scaling=False, decode_choices=False)
            except DiagError:
                raise
            except Exception as e:      # noqa: BLE001  a malformed frame, reported per line
                print("%s %s  DECODE FAILED: %s" % (f[0], f[1], e))
                continue
            if page is None:
                print("%s %s  not a diag id" % (f[0], f[1]))
            elif isinstance(page, Undecodable):
                print("%s %s  UNDECODABLE: %s" % (f[0], f[1], page.reason))
            else:
                print("%s %s  schema %d  %s" % (f[0], f[1], dec.version,
                                                " ".join("%s=%s" % kv for kv in sorted(page.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
