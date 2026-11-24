"""shared/diag: a project's frame table, generated from its DBC (SPEC.md D3, D4),
with its fit checks (D6).

    <python> -B diag_table_gen.py DBC --prefix PREFIX \\
        --c-header OUT.h --c-source OUT.c --python OUT.py

writes one table three ways: a C header and source for diag_pack.c, and a
Python module for python/diag_pack.py. The output depends only on the DBC's
layout, its name and its two schema attributes, and on PREFIX: no dates, no
paths, so regenerating from the same DBC gives the same bytes, and a check
can regenerate and compare.

The table:
- messages in frame id order (then the extended flag), each with its name,
  id, extended flag and DLC;
- each message's signals in start-bit order (then name), each with its name,
  start bit as written, length, byte order, signedness and multiplexing.
Scale, offset, ranges, units, choices and comments are not in it: the
packers take raw values.

Refused (exit 2, nothing written): a float signal, a DLC over 8, over 64
signals in a message, a signal outside its frame, two signals of one message
sharing a bit where both can be present, nested multiplexing, a signal under
more than one multiplexer value, a name that is not a C identifier, and a DBC
without an integer DiagSchemaVersion (D5).

In the C header, for PREFIX p and a message M with a signal s:
    p_SCHEMA_VERSION, p_SCHEMA_HASH    the DBC's DiagSchemaVersion and
                                       DiagSchemaHash, as stated
    p_M                                M's index in p_messages
    p_M__ID                            M's frame id
    p_M__s                             s's index in M's signals
    p_M__N                             M's signal count
    p_M__s__BITS, p_M__s__SIGNED       s's length and signedness, as
                                       constants, for a project's
                                       compile-time checks
    p_MESSAGE_COUNT, p_messages[]

The C header ends with the fit checks (D6): macros that put one raw value
into an int64_t array in the table's order, each checked when the firmware
compiles. One form serves C11 and C++ under GCC. For a raw array r:
    p_FIT_PUT(r, M, s, e)        e's type is unsigned, not bool, and no
                                 wider than s; never 64 bits wide
    p_FIT_CAP(r, M, s, e)        e capped at s's largest value; e's type
                                 is unsigned, of any width
    p_FIT_BIT(r, M, s, cond)     1 if cond, else 0
    p_FIT_MASK(r, M, s, e, mask) e & mask; mask is a constant that fits s
    p_FIT_CONST(r, M, s, c)      c, a constant that fits s
Each one fails the build if s is signed. A type wider than s fails the
build only outside p_FIT_CAP. An unsigned signal's largest value
is 2^BITS - 1, or INT64_MAX from 63 bits up, as the packers take it. The
frame-id and schema checks stay in each project.

The Python module has SCHEMA_VERSION, SCHEMA_HASH and MESSAGES.

Needs cantools, the parser shared/bench's decode.py uses.
"""

import argparse
import os
import re
import sys

C_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MUX_NONE = -1
MUX_SELECTOR = -2


class Refused(Exception):
    pass


def _positions(start, length, big_endian):
    pos = start
    out = []
    for _ in range(length):
        out.append(pos)
        if not big_endian:
            pos += 1
        elif pos % 8 == 0:
            pos += 15
        else:
            pos -= 1
    return out


def _ident(kind, name):
    if not C_IDENT.match(name):
        raise Refused("%s name %r is not a C identifier" % (kind, name))
    return name


def table(db):
    """-> (version, hash, [message dict]) from a cantools database."""
    attrs = db.dbc.attributes if db.dbc else {}
    v = attrs.get("DiagSchemaVersion")
    h = attrs.get("DiagSchemaHash")
    version = v.value if v is not None else None
    if not isinstance(version, int) or isinstance(version, bool):
        raise Refused("no integer DiagSchemaVersion attribute (got %r)" % version)
    schema_hash = h.value if h is not None else ""
    if not isinstance(schema_hash, str) or '"' in schema_hash or "\\" in schema_hash:
        raise Refused("DiagSchemaHash %r cannot be written as a string" % schema_hash)

    out = []
    for m in sorted(db.messages, key=lambda m: (m.frame_id, m.is_extended_frame)):
        _ident("message", m.name)
        if m.length > 8:
            raise Refused("%s: DLC %d is over 8" % (m.name, m.length))
        sigs = sorted(m.signals, key=lambda s: (s.start, s.name))
        if len(sigs) > 64:
            raise Refused("%s: %d signals, over 64" % (m.name, len(sigs)))
        rows = []
        selectors = [s.name for s in sigs if s.is_multiplexer]
        for s in sigs:
            _ident("signal", s.name)
            if s.is_float:
                raise Refused("%s.%s is a float signal" % (m.name, s.name))
            if not 1 <= s.length <= 64:
                raise Refused("%s.%s: length %d" % (m.name, s.name, s.length))
            be = s.byte_order == "big_endian"
            if any(p >= m.length * 8 for p in _positions(s.start, s.length, be)):
                raise Refused("%s.%s lies outside the %d-byte frame" % (m.name, s.name, m.length))
            if s.is_multiplexer:
                if s.multiplexer_ids:
                    raise Refused("%s.%s: nested multiplexing" % (m.name, s.name))
                mux = MUX_SELECTOR
            elif s.multiplexer_ids:
                if len(s.multiplexer_ids) != 1:
                    raise Refused("%s.%s is under %d multiplexer values"
                                  % (m.name, s.name, len(s.multiplexer_ids)))
                if len(selectors) != 1 or s.multiplexer_signal != selectors[0]:
                    raise Refused("%s.%s: its multiplexer is not the message's one selector"
                                  % (m.name, s.name))
                mux = int(s.multiplexer_ids[0])
                if not 0 <= mux <= 32767:
                    raise Refused("%s.%s: multiplexer value %d" % (m.name, s.name, mux))
            else:
                mux = MUX_NONE
            rows.append({"name": s.name, "start": s.start, "length": s.length,
                         "big_endian": be, "is_signed": bool(s.is_signed), "mux": mux})
        _check_overlap(m.name, rows)
        out.append({"name": m.name, "id": m.frame_id, "extended": bool(m.is_extended_frame),
                    "dlc": m.length, "signals": rows})
    return version, schema_hash, out


def _check_overlap(mname, rows):
    """Two signals that can be present together share no bit."""
    owner = {}
    for r in rows:
        for p in _positions(r["start"], r["length"], r["big_endian"]):
            for other in owner.get(p, ()):
                if r["mux"] < 0 or other["mux"] < 0 or r["mux"] == other["mux"]:
                    raise Refused("%s: %s and %s share bit %d" % (mname, other["name"], r["name"], p))
            owner.setdefault(p, []).append(r)


def _banner(dbc_name, version, schema_hash, comment):
    lines = ["Generated by shared/diag tools/diag_table_gen.py from %s" % dbc_name,
             "(DiagSchemaVersion %d, DiagSchemaHash %s)." % (version, schema_hash or "none"),
             "Do not edit: regenerate from the DBC (shared/diag SPEC.md D3)."]
    if comment == "c":
        return "/*\n" + "".join(" * %s\n" % l for l in lines) + " */\n"
    return '"""' + "\n".join(lines) + '\n"""\n'


def c_header(prefix, dbc_name, version, schema_hash, msgs):
    guard = prefix.upper() + "_H"
    out = [_banner(dbc_name, version, schema_hash, "c"),
           "#ifndef %s\n#define %s\n\n#include \"diag_pack.h\"\n\n" % (guard, guard),
           "#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n",
           "#define %s_SCHEMA_VERSION %d\n" % (prefix, version),
           "#define %s_SCHEMA_HASH \"%s\"\n\n" % (prefix, schema_hash)]
    out.append("enum {\n")
    for i, m in enumerate(msgs):
        out.append("    %s_%s = %d,\n" % (prefix, m["name"], i))
    out.append("    %s_MESSAGE_COUNT = %d\n};\n\n" % (prefix, len(msgs)))
    for m in msgs:
        out.append("/* %s: 0x%X%s, DLC %d */\n" % (m["name"], m["id"],
                                                  " extended" if m["extended"] else "", m["dlc"]))
        out.append("#define %s_%s__ID 0x%Xu\n" % (prefix, m["name"], m["id"]))
        out.append("enum {\n")
        for j, s in enumerate(m["signals"]):
            out.append("    %s_%s__%s = %d,\n" % (prefix, m["name"], s["name"], j))
        out.append("    %s_%s__N = %d\n};\n" % (prefix, m["name"], len(m["signals"])))
        for s in m["signals"]:
            out.append("#define %s_%s__%s__BITS %d\n" % (prefix, m["name"], s["name"], s["length"]))
            out.append("#define %s_%s__%s__SIGNED %d\n" % (prefix, m["name"], s["name"],
                                                         1 if s["is_signed"] else 0))
        out.append("\n")
    out.append("extern const diag_message_t %s_messages[%s_MESSAGE_COUNT];\n\n" % (prefix, prefix))
    out.append("#ifdef __cplusplus\n}\n#endif\n\n")
    out.append(FIT_CHECKS.replace("@P@", prefix))
    out.append("\n#endif\n")
    return "".join(out)


# The fit checks (D6), the same text for every table but its prefix. One form
# compiles as C11 and as C++ under GCC: __typeof__ and the static_assert name
# work in both. Only the two type tests differ: C++ warns on a cast to a
# const type, and C has no <type_traits>. It sits after the extern "C"
# block, since <type_traits> must not be inside one.
FIT_CHECKS = r"""/*
 * Fit checks (shared/diag SPEC.md D6): each macro puts one raw value into
 * an int64_t array in the table's order, and fails the build if the value
 * could exceed its signal or the signal is signed. Only the capped form
 * takes a type wider than its signal.
 *   @P@_FIT_PUT(r, M, s, e)         e's type is unsigned, not bool, no wider
 *                                   than s and under 64 bits
 *   @P@_FIT_CAP(r, M, s, e)         e capped at s's largest value; e's type
 *                                   is unsigned, of any width
 *   @P@_FIT_BIT(r, M, s, cond)      1 if cond, else 0
 *   @P@_FIT_MASK(r, M, s, e, mask)  e & mask; mask is a constant that fits s
 *   @P@_FIT_CONST(r, M, s, c)       c, a constant that fits s
 * An unsigned signal's largest value is 2^BITS - 1, or INT64_MAX from 63
 * bits up, as the packers take it.
 */
#ifdef __cplusplus
#include <type_traits>
#define @P@_FIT_UNSIGNED_TYPE_(e) \
    (std::is_unsigned<std::remove_cv<__typeof__(e)>::type>::value)
#define @P@_FIT_NOT_BOOL_(e) \
    (!std::is_same<std::remove_cv<__typeof__(e)>::type, bool>::value)
#else
#include <assert.h>
#define @P@_FIT_UNSIGNED_TYPE_(e) ((__typeof__(e))-1 > 0)
#define @P@_FIT_NOT_BOOL_(e) _Generic((e), _Bool: 0, default: 1)
#endif
#define @P@_FIT_SIG_(M, S) @P@_##M##__##S
#define @P@_FIT_BITS_(M, S) @P@_##M##__##S##__BITS
#define @P@_FIT_MAX_(M, S)                                                   \
    (@P@_FIT_BITS_(M, S) >= 63 ? INT64_MAX                                   \
     : (int64_t)((UINT64_C(1) << (@P@_FIT_BITS_(M, S) % 64)) - 1))
#define @P@_FIT_UNSIGNED_SIG_(M, S)                                        \
    static_assert(!@P@_##M##__##S##__SIGNED,                                 \
                  #M "." #S " is signed: the fit checks take unsigned signals")
#define @P@_FIT_PUT(r, M, S, e)                                              \
    do {                                                                     \
        @P@_FIT_UNSIGNED_SIG_(M, S);                                         \
        static_assert(@P@_FIT_UNSIGNED_TYPE_(e) && @P@_FIT_NOT_BOOL_(e) &&   \
                      sizeof(e) * 8 <= @P@_FIT_BITS_(M, S) &&                \
                      sizeof(e) * 8 < 64,                                    \
                      #M "." #S ": the type of " #e                          \
                      " could exceed the signal: bound it first");           \
        (r)[@P@_FIT_SIG_(M, S)] = (int64_t)(e);                              \
    } while (0)
#define @P@_FIT_CAP(r, M, S, e)                                              \
    do {                                                                     \
        @P@_FIT_UNSIGNED_SIG_(M, S);                                         \
        static_assert(@P@_FIT_UNSIGNED_TYPE_(e),                             \
                      #M "." #S ": " #e " is signed: the capped form takes " \
                      "unsigned values");                                    \
        const uint64_t @P@_fit_v_ = (uint64_t)(e);                           \
        (r)[@P@_FIT_SIG_(M, S)] =                                            \
            @P@_fit_v_ > (uint64_t)@P@_FIT_MAX_(M, S)                        \
                ? @P@_FIT_MAX_(M, S) : (int64_t)@P@_fit_v_;                  \
    } while (0)
#define @P@_FIT_BIT(r, M, S, cond)                                           \
    do {                                                                     \
        @P@_FIT_UNSIGNED_SIG_(M, S);                                         \
        (r)[@P@_FIT_SIG_(M, S)] = (cond) ? 1 : 0;                            \
    } while (0)
#define @P@_FIT_MASK(r, M, S, e, mask)                                       \
    do {                                                                     \
        @P@_FIT_UNSIGNED_SIG_(M, S);                                         \
        static_assert((int64_t)(mask) >= 0 &&                                \
                      (int64_t)(mask) <= @P@_FIT_MAX_(M, S),                 \
                      #M "." #S ": the mask " #mask " exceeds the signal");  \
        (r)[@P@_FIT_SIG_(M, S)] = (int64_t)((uint64_t)(e) & (uint64_t)(mask)); \
    } while (0)
#define @P@_FIT_CONST(r, M, S, c)                                            \
    do {                                                                     \
        @P@_FIT_UNSIGNED_SIG_(M, S);                                         \
        static_assert((int64_t)(c) >= 0 && (int64_t)(c) <= @P@_FIT_MAX_(M, S), \
                      #M "." #S ": " #c " exceeds the signal");              \
        (r)[@P@_FIT_SIG_(M, S)] = (int64_t)(c);                              \
    } while (0)
"""


def c_source(prefix, header_name, dbc_name, version, schema_hash, msgs):
    out = [_banner(dbc_name, version, schema_hash, "c"), "#include \"%s\"\n\n" % header_name]
    for m in msgs:
        if not m["signals"]:
            continue
        out.append("static const diag_signal_t %s_%s__signals[%s_%s__N] = {\n"
                   % (prefix, m["name"], prefix, m["name"]))
        for s in m["signals"]:
            mux = {MUX_NONE: "DIAG_MUX_NONE", MUX_SELECTOR: "DIAG_MUX_SELECTOR"}.get(s["mux"], str(s["mux"]))
            out.append("    { \"%s\", %d, %d, %s, %s, %s },\n" % (
                s["name"], s["start"], s["length"], "true" if s["big_endian"] else "false",
                "true" if s["is_signed"] else "false", mux))
        out.append("};\n\n")
    out.append("const diag_message_t %s_messages[%s_MESSAGE_COUNT] = {\n" % (prefix, prefix))
    for m in msgs:
        sigs = ("%s_%s__signals" % (prefix, m["name"])) if m["signals"] else "NULL"
        out.append("    { \"%s\", 0x%Xu, %s, %d, %d, %s },\n" % (
            m["name"], m["id"], "true" if m["extended"] else "false", m["dlc"],
            len(m["signals"]), sigs))
    out.append("};\n")
    return "".join(out)


def python_module(dbc_name, version, schema_hash, msgs):
    out = [_banner(dbc_name, version, schema_hash, "py"), "\n",
           "SCHEMA_VERSION = %d\n" % version,
           "SCHEMA_HASH = %r\n\n" % schema_hash,
           "MESSAGES = (\n"]
    for m in msgs:
        out.append("    {\"name\": %r, \"id\": 0x%X, \"extended\": %r, \"dlc\": %d, \"signals\": (\n"
                   % (m["name"], m["id"], m["extended"], m["dlc"]))
        for s in m["signals"]:
            out.append("        {\"name\": %r, \"start\": %d, \"length\": %d, \"big_endian\": %r, "
                       "\"is_signed\": %r, \"mux\": %d},\n"
                       % (s["name"], s["start"], s["length"], s["big_endian"], s["is_signed"], s["mux"]))
        out.append("    )},\n")
    out.append(")\n")
    return "".join(out)


def generate(dbc_path, prefix, header_name):
    """-> (header text, source text, python text)."""
    import cantools
    _ident("prefix", prefix)
    db = cantools.database.load_file(dbc_path)
    version, schema_hash, msgs = table(db)
    name = os.path.basename(dbc_path)
    return (c_header(prefix, name, version, schema_hash, msgs),
            c_source(prefix, header_name, name, version, schema_hash, msgs),
            python_module(name, version, schema_hash, msgs))


def _write(path, text):
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dbc")
    ap.add_argument("--prefix", required=True, help="C identifier prefix, e.g. gi_dbc")
    ap.add_argument("--c-header", required=True)
    ap.add_argument("--c-source", required=True)
    ap.add_argument("--python", required=True)
    a = ap.parse_args(argv)
    try:
        h, c, p = generate(a.dbc, a.prefix, os.path.basename(a.c_header))
        for t in (h, c, p):
            t.encode("ascii")
    except Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    except UnicodeEncodeError as e:
        print("REFUSED: non-ASCII in the DBC's names or attributes: %s" % e, file=sys.stderr)
        return 2
    _write(a.c_header, h)
    _write(a.c_source, c)
    _write(a.python, p)
    print("wrote %s, %s, %s" % (a.c_header, a.c_source, a.python))
    return 0


if __name__ == "__main__":
    sys.exit(main())
