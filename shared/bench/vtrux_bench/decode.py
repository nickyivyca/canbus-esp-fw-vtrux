"""Diag DBC schema identity (toolkit item 10, first part).

A diag DBC carries two database attributes:

    DiagSchemaVersion  INT     the schema byte its frames carry
    DiagSchemaHash     STRING  layout_hash() of the DBC itself

layout_hash() is the first 16 hex characters of SHA-256 over a canonical
text of the parsed database: everything that changes how a frame decodes
or what a decoded value is called, and nothing else. Names are in it (user
ruling, 2026-10-19): the judges read by name, so to a decoder a rename is
a layout change, and two DBCs on one schema byte that differ in a name
would decode one capture two ways. A rename therefore needs a schema bump;
the signal's RenamedFrom attribute lists its earlier names, oldest first.

The canonical text, UTF-8, one line each, "\\n" after every line:

    M|<frame id, decimal>|<name>|<dlc>|<extended 0/1>
    S|<name>|<start bit as written>|<length>|<le/be>|<u/s/f>|<scale>|<offset>|<mux>
    C|<value>=<name>

Messages sorted by frame id (then extended flag); each message's signals
follow it, sorted by start bit then name; each signal's choices follow it,
sorted by value. Scale and offset are repr(float(x)), so the hash does not
depend on whether cantools keeps an integral 1.0 as int. <mux> is "-",
"M" for a multiplexer, or "m<id>[,<id>...]@<multiplexer>". "f" is an IEEE
float signal. Left out: comments, units, ranges, nodes, senders and
receivers, and every attribute (cycle times included). A choice name
holding "|", "=" or a line break is refused rather than escaped, so the
lines stay readable.

Agreed between both testers and both reviewers, 2026-10-19. Changing any
of it changes every hash: the golden test in tests/test_decode.py pins it.

check_identity(db) checks one DBC; check_history(repo, path) checks the
DBC across its git history: every commit carries both attributes, its
DiagSchemaHash equals its layout_hash, and each DiagSchemaVersion has one
hash. It refuses a shallow clone, whose history is incomplete.

Needs cantools (imported when used); the rest of the toolkit is stdlib.
"""

import hashlib
import subprocess

VERSION_ATTR = "DiagSchemaVersion"
HASH_ATTR = "DiagSchemaHash"
RENAMED_ATTR = "RenamedFrom"
HASH_CHARS = 16
# Characters a choice name (free DBC text) may not hold: they would let two
# different layouts give the same lines (gen-inhibit tester, 2026-10-19).
# Message and signal names are DBC identifiers and cannot hold them.
UNSAFE = ("|", "=", "\n", "\r")


class DecodeError(Exception):
    pass


def _num(x):
    return repr(float(x))


def _msg_line(m):
    return "M|%d|%s|%d|%d" % (m.frame_id, m.name, m.length,
                              1 if m.is_extended_frame else 0)


def _mux(s):
    if s.is_multiplexer:
        return "M"
    if s.multiplexer_ids:
        return "m%s@%s" % (",".join(str(i) for i in sorted(s.multiplexer_ids)),
                           s.multiplexer_signal)
    return "-"


def _sig_line(s):
    kind = "f" if s.is_float else ("s" if s.is_signed else "u")
    order = "be" if s.byte_order == "big_endian" else "le"
    return "S|%s|%d|%d|%s|%s|%s|%s|%s" % (
        s.name, s.start, s.length, order, kind, _num(s.scale),
        _num(s.offset), _mux(s))


def _choice_lines(s):
    out = []
    for v, n in sorted((s.choices or {}).items(), key=lambda kv: int(kv[0])):
        n = str(n)
        if any(ch in n for ch in UNSAFE):
            raise DecodeError("choice %d of %s is %r: a choice name holding "
                              "'|', '=' or a line break is refused, as it "
                              "would make two layouts give the same lines"
                              % (int(v), s.name, n))
        out.append("C|%d=%s" % (int(v), n))
    return out


def canonical_text(db):
    out = []
    for m in sorted(db.messages,
                    key=lambda m: (m.frame_id, m.is_extended_frame)):
        out.append(_msg_line(m))
        for s in sorted(m.signals, key=lambda s: (s.start, s.name)):
            out.append(_sig_line(s))
            out.extend(_choice_lines(s))
    return "".join(line + "\n" for line in out)


def layout_hash(db):
    return hashlib.sha256(
        canonical_text(db).encode("utf-8")).hexdigest()[:HASH_CHARS]


def load(path=None, text=None):
    import cantools
    if text is not None:
        return cantools.database.load_string(text, "dbc")
    return cantools.database.load_file(path)


def schema_identity(db):
    """-> (DiagSchemaVersion or None, DiagSchemaHash or None)."""
    attrs = db.dbc.attributes if db.dbc else {}
    v = attrs.get(VERSION_ATTR)
    h = attrs.get(HASH_ATTR)
    return (v.value if v is not None else None,
            h.value if h is not None else None)


def check_identity(db):
    """-> list of problems (empty: the DBC states its schema and its hash
    is its own)."""
    v, h = schema_identity(db)
    probs = []
    if not isinstance(v, int) or isinstance(v, bool):
        probs.append("no integer %s attribute (got %r)" % (VERSION_ATTR, v))
    actual = layout_hash(db)
    if h is None:
        probs.append("no %s attribute (layout_hash is %s)" % (HASH_ATTR,
                                                              actual))
    elif h != actual:
        probs.append("%s says %s, layout_hash is %s" % (HASH_ATTR, h, actual))
    return probs


def _git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    if r.returncode != 0:
        raise DecodeError("git %s: %s" % (" ".join(args),
                                          r.stderr.decode("utf-8", "replace")
                                          .strip()[-300:]))
    return r.stdout


def history(repo, path):
    """-> [(commit, version, stated hash, layout_hash, problems)], oldest
    first, for every commit that touched `path` (repo-relative)."""
    if _git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise DecodeError("%s is a shallow clone: the DBC's history is "
                          "incomplete (clones that run suites need full "
                          "history)" % repo)
    commits = _git(repo, "log", "--reverse", "--format=%H", "--",
                   path).decode().split()
    out = []
    for c in commits:
        try:
            text = _git(repo, "show", "%s:%s" % (c, path)).decode("utf-8")
        except DecodeError:
            continue        # the commit deleted it
        db = load(text=text)
        v, h = schema_identity(db)
        out.append((c, v, h, layout_hash(db), check_identity(db)))
    return out


def check_history(repo, path):
    """-> list of problems across the DBC's git history: any commit whose
    identity is wrong, and any DiagSchemaVersion with more than one
    layout."""
    rows = history(repo, path)
    if not rows:
        return ["%s has no history in %s" % (path, repo)]
    probs = ["%s: %s" % (c[:10], "; ".join(p)) for c, _v, _h, _a, p in rows
             if p]
    return probs + _layout_conflicts(rows)


def _layout_conflicts(rows):
    """Each DiagSchemaVersion has one layout across the history."""
    seen = {}
    for c, v, _h, actual, _p in rows:
        seen.setdefault(v, {}).setdefault(actual, c)
    return ["%s %s has %d layouts: %s" % (
                VERSION_ATTR, v, len(hashes),
                ", ".join("%s (first at %s)" % (h, c[:10])
                          for h, c in hashes.items()))
            for v, hashes in sorted(seen.items(), key=lambda kv: str(kv[0]))
            if len(hashes) > 1]
