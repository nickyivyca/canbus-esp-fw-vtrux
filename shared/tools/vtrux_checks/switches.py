"""R20, build switches: every switch the release build's source tests is in a table.

    python -B -m vtrux_checks switches --build <project>/build

SPEC R20 (shared/main d4c16e9). Two sets of files, both from the build's own
records, in every CMake build folder under build/ (the app's and the
bootloader's):

  - C files: every file a compile read, by ninja's deps log (the source and
    the headers it included);
  - CMake files: the CMakeLists.txt and *.cmake inputs of the RERUN_CMAKE edge.

The project's files are those under the project root (project_description.json's
project_path), minus the build folder and managed_components/. Each managed
component that carries a table (vtrux_checks.json in its own folder, key
"build_switches") is judged the same way on the files the build read under
managed_components/<name>/. A component that vtrux_build.json names in
shared_components and that carries no table fails.

What counts as tested (the readings of gen-inhibit spec 12.3 item 3, carried
over by the user's ruling of 2026-10-24):

  - The C scan is textual and over-inclusive: every identifier in an #if or
    #elif is tested, function-like macros included, and #ifdef, #ifndef,
    #elifdef and #elifndef test their one name. Comments are stripped and
    continuation lines joined first; string literals are not identifiers.
    CONFIG_* is left out.
  - A header guard is left out only in its exact shape: the file's first
    directive is `#ifndef X` and the next is `#define X` with no value.
  - A CMake build option is a variable an if(), elseif() or while() tests that
    can be set from outside the file: "the file sets it itself" is only
    set(VAR <value>) at file scope, with no CACHE or PARENT_SCOPE, before the
    test, whose value references only built-ins and variables the file has
    already set. $ENV{X} is always an option. CMake's built-in variables (the
    recorded cmake's --help-variable-list) are not: <PROJECT-NAME> expands to
    the project names the files declare, and a pattern starting with any other
    placeholder is not used.
  - An unlisted symbol fails, and so does a listed one the files no longer
    test (stale).

A table (the same form for a project and a component):

    {"build_switches": {
        "<SYMBOL>": {"class": "shipping variant" | "test-only" |
                              "stock feature off in this product" | "not a switch",
                     "reason": "...",
                     "kind": "...",            # "not a switch" only, required
                     "refusal": [<rule>, ...]  # test-only and stock-off only, required
                    }}}

A rule has exactly one key, and judges the build's outputs, never the flag:

    {"bin_strings_absent": ["text", ...]}            none of them in the .bin
    {"elf_symbols_absent": ["name", ...]}            none defined in the ELF
    {"elf_symbol_prefixes_absent": ["prefix", ...]}  no defined ELF symbol starts with one
    {"project_name_token_absent": "_rt"}             not a whole "_"-separated token of
                                                     the app descriptor's project_name
    {"project_row": "row name"}                      a row of the project's own harness

A project row's result is read from vtrux_project_rows.json in the build
folder, which the project's harness writes: {"image_hash": "<R1 hash of the
image it judged>", "rows": {"<row name>": "PASS" | "FAIL"}}. The rule fails if
the file is missing or unreadable, if its image hash is not this build's, or if
the row is absent or not PASS.

The ELF's defined symbols are read here from its .symtab, as `nm
--defined-only` lists them (mapping symbols left out).

Release builds only; bench and dev builds are not checked. The check does not
tell them apart: run it on a finished release build.
"""

import json
import os
import re
import struct
import subprocess

from vtrux_build import image as IMG
from vtrux_build.builddir import BuildDir, BuildDirError

from .scope import build_folders, norm, under

DATA_FILE = "vtrux_checks.json"
KEY = "build_switches"
ROWS_FILE = "vtrux_project_rows.json"
SHIP, TEST, STOCK, NOT = ("shipping variant", "test-only", "stock feature off in this product", "not a switch")
CLASSES = (SHIP, TEST, STOCK, NOT)
REFUSED = (TEST, STOCK)
RULES = ("bin_strings_absent", "elf_symbols_absent", "elf_symbol_prefixes_absent", "project_name_token_absent",
         "project_row")
PROJECT = "the project"
SHOW = 3


class SwitchesError(ValueError):
    pass


# ---------- tables ----------

def _strings(v, what):
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
        raise SwitchesError("%s: wants a non-empty list of non-empty strings" % what)


def check_rule(where, sym, i, rule):
    what = "%s: %s refusal rule %d" % (where, sym, i + 1)
    if not isinstance(rule, dict) or len(rule) != 1:
        raise SwitchesError("%s: wants one key, one of %s" % (what, ", ".join(RULES)))
    (k, v), = rule.items()
    if k not in RULES:
        raise SwitchesError("%s: %r is not one of %s" % (what, k, ", ".join(RULES)))
    if k in ("project_name_token_absent", "project_row"):
        if not isinstance(v, str) or not v.strip():
            raise SwitchesError("%s: %s wants a non-empty string" % (what, k))
    else:
        _strings(v, "%s: %s" % (what, k))


def read_table(folder, where, required=True):
    """{symbol: entry} from <folder>/vtrux_checks.json, refused unless every entry is
    whole. None when the file or its table is absent and not required."""
    p = os.path.join(folder, DATA_FILE)
    if not required and not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise SwitchesError("%s: %s cannot be read (%s)" % (where, DATA_FILE, e))
    t = data.get(KEY) if isinstance(data, dict) else None
    if t is None and not required:
        return None
    if not isinstance(t, dict) or not t:
        raise SwitchesError("%s: %s has no %r object naming at least one symbol" % (where, DATA_FILE, KEY))
    for sym, e in t.items():
        if not isinstance(e, dict):
            raise SwitchesError("%s: %s wants an object" % (where, sym))
        if e.get("class") not in CLASSES:
            raise SwitchesError("%s: %s: class %r is not one of the four" % (where, sym, e.get("class")))
        allowed = {"class", "reason"} | ({"kind"} if e["class"] == NOT else set()) | (
            {"refusal"} if e["class"] in REFUSED else set())
        extra = set(e) - allowed
        if extra:
            raise SwitchesError("%s: %s: keys %s are not allowed for %r" % (where, sym, sorted(extra), e["class"]))
        if not isinstance(e.get("reason"), str) or not e["reason"].strip():
            raise SwitchesError("%s: %s: no reason" % (where, sym))
        if e["class"] == NOT and (not isinstance(e.get("kind"), str) or not e["kind"].strip()):
            raise SwitchesError("%s: %s: 'not a switch' needs a kind" % (where, sym))
        if e["class"] in REFUSED:
            r = e.get("refusal")
            if not isinstance(r, list) or not r:
                raise SwitchesError("%s: %s: %s needs a refusal row" % (where, sym, e["class"]))
            for i, rule in enumerate(r):
                check_rule(where, sym, i, rule)
    return t


# ---------- the C scan ----------

IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
DIRECTIVE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elifdef|elifndef|elif|define)\b(.*)$")


def strip_c_comments(text):
    """Block and line comments out, string and char literals kept, newlines
    preserved so line numbers still mean something."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def logical_lines(text):
    """(first line number, line) with backslash continuations joined."""
    lines = strip_c_comments(text).split("\n")
    out, k = [], 0
    while k < len(lines):
        start, cur = k + 1, lines[k]
        while cur.endswith("\\") and k + 1 < len(lines):
            k += 1
            cur = cur[:-1] + " " + lines[k]
        out.append((start, cur))
        k += 1
    return out


def c_symbols(text):
    """[(symbol, line)] for every symbol a directive in this file tests, the header
    guard left out. String literals inside an #if are not identifiers."""
    lines = [(ln, line) for ln, line in logical_lines(text) if line.strip()]
    directives = []
    for ln, line in lines:
        m = DIRECTIVE.match(line)
        if m:
            directives.append((ln, m.group(1), m.group(2).strip()))
    guard = None
    if len(lines) >= 2:
        m0, m1 = DIRECTIVE.match(lines[0][1]), DIRECTIVE.match(lines[1][1])
        if m0 and m1 and m0.group(1) == "ifndef" and m1.group(1) == "define":
            g, d = m0.group(2).split(), m1.group(2).split()
            if len(g) == 1 and len(d) == 1 and g[0] == d[0]:
                guard = g[0]
    out = []
    for k, (ln, kind, rest) in enumerate(directives):
        if kind == "define":
            continue
        if kind in ("ifdef", "ifndef", "elifdef", "elifndef"):
            names = rest.split()[:1]
        else:
            rest = re.sub(r'"(\\.|[^"\\])*"', " ", rest)
            names = [x for x in IDENT.findall(rest) if x not in ("defined", "__has_include")]
        for s in names:
            if k == 0 and s == guard:
                continue
            out.append((s, ln))
    return out


# ---------- the CMake scan ----------

CMAKE_KEYWORDS = {
    "AND", "OR", "NOT", "DEFINED", "EQUAL", "LESS", "GREATER", "LESS_EQUAL",
    "GREATER_EQUAL", "STREQUAL", "STRLESS", "STRGREATER", "MATCHES", "EXISTS",
    "IS_DIRECTORY", "IS_ABSOLUTE", "COMMAND", "POLICY", "TARGET", "TEST",
    "IN_LIST", "VERSION_LESS", "VERSION_GREATER", "VERSION_EQUAL",
    "VERSION_LESS_EQUAL", "VERSION_GREATER_EQUAL", "TRUE", "FALSE", "ON", "OFF",
    "YES", "NO", "Y", "N", "IGNORE", "NOTFOUND", "ENV", "CACHE",
}
QUOTED = re.compile(r'"(\\.|[^"\\])*"', re.S)
VAR_REF = re.compile(r"\$\{([A-Za-z0-9_]+)\}")             # "$ENV{" is not "${"
ENV_REF = re.compile(r"\$ENV\{([A-Za-z0-9_]+)\}")
BLOCK_OPEN = {"if", "foreach", "while", "function", "macro", "block"}
BLOCK_CLOSE = {"endif", "endforeach", "endwhile", "endfunction", "endmacro", "endblock"}


def _skip_quoted(text, i):
    """Index just past the quoted argument starting at text[i] == '"'."""
    j, n = i + 1, len(text)
    while j < n and text[j] != '"':
        j += 2 if text[j] == "\\" else 1
    return min(j + 1, n)


def _bracket_end(text, i):
    """If a bracket argument or comment opens at text[i] ('[', '[=[', ...),
    the index just past its close, else None."""
    m = re.match(r"\[(=*)\[", text[i:])
    if not m:
        return None
    close = "]" + m.group(1) + "]"
    j = text.find(close, i + m.end())
    return len(text) if j < 0 else j + len(close)


def strip_cmake_comments(text):
    """Line and bracket comments out, quoted arguments kept whole (a '#' inside
    one is not a comment), newlines preserved for line numbers."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = _skip_quoted(text, i)
            out.append(text[i:j])
            i = j
        elif c == "#":
            j = _bracket_end(text, i + 1)
            if j is None:
                j = text.find("\n", i)
                j = n if j < 0 else j
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def cmake_commands(text):
    """[(name lower-cased, argument text, line)] for every command invocation,
    in file order. Parentheses inside quoted arguments do not count."""
    body = strip_cmake_comments(text)
    out, i, n = [], 0, len(body)
    head = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    while i < n:
        m = head.match(body, i)
        if not m or (i > 0 and (body[i - 1].isalnum() or body[i - 1] in "_$")):
            i += 1
            continue
        depth, j = 1, m.end()
        while j < n and depth:
            if body[j] == '"':
                j = _skip_quoted(body, j)
                continue
            depth += {"(": 1, ")": -1}.get(body[j], 0)
            j += 1
        out.append((m.group(1).lower(), body[m.end():j - 1], body.count("\n", 0, i) + 1))
        i = j
    return out


def cmake_args(args):
    """The arguments as words: a quoted argument is one word, quotes kept."""
    words, i, n = [], 0, len(args)
    while i < n:
        if args[i].isspace():
            i += 1
        elif args[i] == '"':
            j = _skip_quoted(args, i)
            words.append(args[i:j])
            i = j
        else:
            j = i
            while j < n and not args[j].isspace() and args[j] != '"':
                j += 1
            words.append(args[i:j])
            i = j
    return words


def condition_variables(cond):
    """(variables, environment variables) a condition tests: every unquoted
    identifier that is not a keyword or a number, every ${X} and every $ENV{X},
    quoted ones included ("${X}" STREQUAL "on" tests X)."""
    env = ENV_REF.findall(cond)
    names = []
    for q in QUOTED.finditer(cond):
        names += VAR_REF.findall(q.group(0))
    bare = ENV_REF.sub(" ", QUOTED.sub(" ", cond))
    for s in IDENT.findall(bare):
        if s.upper() in CMAKE_KEYWORDS or s.isdigit():
            continue
        names.append(s)
    return names, env


def cmake_symbols(text, is_builtin=lambda s: False):
    """[(variable, line)] for every build option an if()/elseif()/while() tests:
    not a built-in, and not a variable this file set itself before the test."""
    out, depth, own = [], 0, set()
    for name, args, ln in cmake_commands(text):
        if name in ("if", "elseif", "while"):
            names, env = condition_variables(args)
            for s in names:
                if s not in own and not is_builtin(s):
                    out.append((s, ln))
            for s in env:
                out.append((s, ln))
        if name in BLOCK_OPEN:
            depth += 1
        elif name in BLOCK_CLOSE:
            depth = max(0, depth - 1)
        elif name in ("set", "unset"):
            words = cmake_args(args)
            if not words:
                continue
            var, vals = words[0], words[1:]
            if name == "unset" or depth > 0 or not vals or "CACHE" in vals or "PARENT_SCOPE" in vals:
                own.discard(var)
                continue
            joined = " ".join(vals)
            refs = VAR_REF.findall(joined)
            if ENV_REF.search(joined) or not all(r in own or is_builtin(r) for r in refs):
                own.discard(var)
            else:
                own.add(var)
    return out


def _placeholder_regex(p):
    return "".join("[A-Za-z0-9_]+" if x.startswith("<") else re.escape(x)
                   for x in re.split(r"(<[^>]+>)", p) if x)


def builtin_matcher(patterns, project_names):
    """name -> bool over `cmake --help-variable-list`. <PROJECT-NAME> expands to
    the declared project names only; a pattern opening with any other placeholder
    is not used; other placeholders match one or more identifier characters."""
    exact, regs = set(), []
    for p in (x.strip() for x in patterns):
        if not p:
            continue
        if "<" not in p:
            exact.add(p)
        elif p.startswith("<PROJECT-NAME>"):
            regs += [re.compile(re.escape(n) + _placeholder_regex(p[len("<PROJECT-NAME>"):]) + "$")
                     for n in project_names]
        elif not p.startswith("<"):
            regs.append(re.compile(_placeholder_regex(p) + "$"))
    return lambda s: s in exact or any(r.match(s) for r in regs)


def project_names(cmake_texts):
    """The literal names project() declares in the CMake files."""
    names = set()
    for text in cmake_texts:
        for name, args, _ in cmake_commands(text):
            w = cmake_args(args)
            if name == "project" and w and re.match(r"^[A-Za-z0-9_.+-]+$", w[0]):
                names.add(w[0])
    return sorted(names)


def found_symbols(c_texts, cmake_texts, is_builtin):
    """{symbol: [where]} for one set of files; c_texts and cmake_texts are
    {label: text}. CONFIG_* is left out."""
    found = {}

    def add(sym, where):
        if not sym.startswith("CONFIG_"):
            found.setdefault(sym, []).append(where)
    for rel, text in sorted(c_texts.items()):
        for s, ln in c_symbols(text):
            add(s, "%s:%d" % (rel, ln))
    for rel, text in sorted(cmake_texts.items()):
        for s, ln in cmake_symbols(text, is_builtin):
            add(s, "%s:%d" % (rel, ln))
    return found


# ---------- the build's outputs ----------

SHT_SYMTAB, STT_SECTION, STT_FILE, SHN_UNDEF = 2, 3, 4, 0
# mapping symbols, which nm hides: RISC-V $x, $x<isa>, $d; Arm $a, $t, $d (each may carry .<n>)
MAPPING = re.compile(r"^\$(?:[adt]|x(?:rv[0-9a-z_]+)?)(?:\..*)?$")


def elf_defined_symbols(data):
    """The names nm --defined-only lists: every .symtab symbol with a section (not
    UNDEF), a name, and a type other than SECTION or FILE, less mapping symbols."""
    if data[:4] != b"\x7fELF":
        raise SwitchesError("the ELF is not an ELF")
    cls, enc = data[4], data[5]
    if cls not in (1, 2) or enc not in (1, 2):
        raise SwitchesError("ELF class %d or data encoding %d" % (cls, enc))
    e = "<" if enc == 1 else ">"
    try:
        if cls == 1:
            shoff, = struct.unpack_from(e + "I", data, 0x20)
            shentsize, shnum = struct.unpack_from(e + "HH", data, 0x2E)
            sh = e + "IIIIIIIIII"
        else:
            shoff, = struct.unpack_from(e + "Q", data, 0x28)
            shentsize, shnum = struct.unpack_from(e + "HH", data, 0x3A)
            sh = e + "IIQQQQIIQQ"
        secs = [struct.unpack_from(sh, data, shoff + i * shentsize) for i in range(shnum)]
        out, tables = set(), 0
        for s in secs:
            if s[1] != SHT_SYMTAB:
                continue
            tables += 1
            off, size, link, entsize = s[4], s[5], s[6], s[9]
            stroff = secs[link][4]
            for j in range(size // entsize):
                b = off + j * entsize
                if cls == 1:
                    name, _v, _sz, info, _o, shndx = struct.unpack_from(e + "IIIBBH", data, b)
                else:
                    name, info, _o, shndx, _v, _sz = struct.unpack_from(e + "IBBHQQ", data, b)
                if shndx == SHN_UNDEF or (info & 0xF) in (STT_SECTION, STT_FILE) or not name:
                    continue
                end = data.index(b"\0", stroff + name)
                n = data[stroff + name:end].decode("latin-1")
                if not MAPPING.match(n):
                    out.add(n)
    except (struct.error, IndexError, ValueError) as x:
        raise SwitchesError("the ELF's section or symbol table cannot be read (%s)" % x)
    if not tables:
        raise SwitchesError("the ELF has no symbol table (stripped?)")
    return out


def read_rows(build):
    """(image hash, {row: verdict}) from the project's result file. Raises SwitchesError."""
    p = os.path.join(build, ROWS_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise SwitchesError("%s cannot be read (%s)" % (ROWS_FILE, e))
    if (not isinstance(d, dict) or not isinstance(d.get("image_hash"), str) or not isinstance(d.get("rows"), dict)
            or not all(isinstance(v, str) for v in d["rows"].values())):
        raise SwitchesError('%s wants {"image_hash": "...", "rows": {"<row>": "PASS" | "FAIL"}}' % ROWS_FILE)
    return d["image_hash"].lower(), d["rows"]


def judge_rule(rule, out):
    """(ok, detail) for one rule against out: {"image": bytes, "elf_symbols": set,
    "project_name": str, "image_hash": str, "rows": read_rows() or the SwitchesError}."""
    (k, v), = rule.items()
    if k == "bin_strings_absent":
        hits = [s for s in v if s.encode("latin-1") in out["image"]]
        return not hits, ("the image carries %s" % ", ".join(repr(h) for h in hits) if hits
                          else "none of %d strings in the image" % len(v))
    if k == "elf_symbols_absent":
        hits = sorted(s for s in v if s in out["elf_symbols"])
        return not hits, ("the ELF defines %s" % ", ".join(hits) if hits else "none of %d symbols defined" % len(v))
    if k == "elf_symbol_prefixes_absent":
        hits = sorted(s for s in out["elf_symbols"] if any(s.startswith(p) for p in v))
        if hits:
            more = " and %d more" % (len(hits) - SHOW) if len(hits) > SHOW else ""
            return False, "the ELF defines %s%s" % (", ".join(hits[:SHOW]), more)
        return True, "no defined symbol starts with %s" % ", ".join(v)
    if k == "project_name_token_absent":
        hit = v.strip("_") in out["project_name"].split("_")
        return not hit, "project_name %r%s" % (out["project_name"], " has the token %r" % v if hit else "")
    rows = out.get("rows")
    if not isinstance(rows, tuple):
        return False, "project row %r: %s" % (v, rows if rows is not None else "no result file read")
    their_hash, verdicts = rows
    if their_hash != out["image_hash"]:
        return False, "project row %r: %s judged image %s, this build's is %s" % (
            v, ROWS_FILE, their_hash[:16], out["image_hash"][:16])
    if v not in verdicts:
        return False, "project row %r: not in %s" % (v, ROWS_FILE)
    return verdicts[v] == "PASS", "project row %r: %s" % (v, verdicts[v])


# ---------- the judge (pure) ----------

def judge(facts):
    """facts: {"scopes": {name: {"table": dict or None, "found": {sym: [where]}}},
    "named_without_table": [name], "outputs": as judge_rule() takes}.
    Returns [(scope, symbol, what, detail)] for every failure."""
    bad = []
    for name in facts["named_without_table"]:
        bad.append((name, "-", "no table", "named in vtrux_build.json's shared_components, no %s %r" % (
            DATA_FILE, KEY)))
    for name, sc in sorted(facts["scopes"].items(), key=lambda kv: (kv[0] != PROJECT, kv[0])):
        table, found = sc["table"], sc["found"]
        if table is None:
            continue
        for s in sorted(found):
            if s not in table:
                bad.append((name, s, "unlisted", ", ".join(found[s][:SHOW])))
        for s in sorted(table):
            if s not in found:
                bad.append((name, s, "stale", "listed, no longer tested"))
        for s, e in sorted(table.items()):
            for rule in e.get("refusal", []):
                ok, detail = judge_rule(rule, facts["outputs"])
                if not ok:
                    bad.append((name, s, "refusal", detail))
    return bad


# ---------- reading the build ----------

def is_cmake_file(p):
    b = os.path.basename(p).lower()
    return b == "cmakelists.txt" or b.endswith(".cmake")


def _text(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def builtins_of(folder):
    """The recorded cmake's --help-variable-list, as a matcher input."""
    cache = BuildDir(folder).cmake_cache()
    cmake = cache.get("CMAKE_COMMAND")
    if not cmake:
        raise SwitchesError("no CMAKE_COMMAND in %s's CMakeCache.txt" % folder)
    try:
        v = subprocess.run([cmake[1], "--help-variable-list"], capture_output=True, text=True)
    except OSError as e:
        raise SwitchesError("cannot run %s (%s)" % (cmake[1], e))
    if v.returncode or not v.stdout.strip():
        raise SwitchesError("cmake --help-variable-list gave nothing")
    return v.stdout.splitlines()


def component_folder(name):
    """managed_components/ folder name for a component name ("ns/name" -> "ns__name")."""
    return name.replace("/", "__")


def collect(build):
    """The facts judge() needs, all from the build's own records and outputs."""
    folders = build_folders(build)
    top = BuildDir(folders[0])
    root = norm(top.description()["project_path"])
    mdir = norm(os.path.join(root, "managed_components"))
    out_dir = norm(folders[0])
    files = {}                                  # scope -> {"c": {norm: abs}, "cmake": {norm: abs}}

    def scope_of(p):
        if not under(p, root) or under(p, out_dir):
            return None
        if under(p, mdir):
            rest = os.path.relpath(p, mdir).split(os.sep)
            return rest[0] if len(rest) > 1 else None
        return PROJECT
    for f in folders:
        bd = BuildDir(f)
        stamp = os.path.getmtime(bd.file("build.ninja"))
        for deps in bd.ninja_deps().values():
            for p in deps:
                a = os.path.abspath(bd.resolve(p))
                s = scope_of(norm(a))
                if s:
                    files.setdefault(s, {"c": {}, "cmake": {}})["c"][norm(a)] = a
        for p in bd.cmake_inputs():
            a = os.path.abspath(bd.resolve(p))
            s = scope_of(norm(a))
            if s and is_cmake_file(a):
                if os.path.getmtime(a) > stamp:
                    raise SwitchesError("%s is newer than %s's build.ninja: reconfigure first" % (
                        os.path.relpath(a, root), os.path.relpath(f, root)))
                files.setdefault(s, {"c": {}, "cmake": {}})["cmake"][norm(a)] = a
    proj = files.get(PROJECT, {"c": {}, "cmake": {}})
    if not proj["c"]:
        raise SwitchesError("the build's records name no C file read from the project root")
    if norm(os.path.join(root, "CMakeLists.txt")) not in proj["cmake"]:
        raise SwitchesError("the build's records lack the project's top-level CMakeLists.txt")
    patterns = builtins_of(folders[0])
    scopes = {}
    for s, fs in files.items():
        base = root if s == PROJECT else os.path.join(mdir, s)
        c = {os.path.relpath(p, root).replace(os.sep, "/"): _text(p) for p in fs["c"].values()}
        cm = {os.path.relpath(p, root).replace(os.sep, "/"): _text(p) for p in fs["cmake"].values()}
        table = read_table(base, s, required=(s == PROJECT))
        scopes[s] = {"table": table, "found": found_symbols(c, cm, builtin_matcher(patterns, project_names(
            cm.values())))}
    for n in (sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []):
        # a component with a table whose files the build did not read is still judged: every entry stale
        if n not in scopes and os.path.isdir(os.path.join(mdir, n)):
            t = read_table(os.path.join(mdir, n), n, required=False)
            if t is not None:
                scopes[n] = {"table": t, "found": {}}
    vb = os.path.join(root, "vtrux_build.json")
    named = []
    if os.path.exists(vb):
        try:
            shared = json.load(open(vb, encoding="utf-8")).get("shared_components", [])
        except ValueError as e:
            raise SwitchesError("vtrux_build.json cannot be read (%s)" % e)
        named = sorted(n for n in shared if (scopes.get(component_folder(n)) or {}).get("table") is None)
    app = IMG.AppImage.read(top.app_bin())
    with open(top.app_elf(), "rb") as f:
        syms = elf_defined_symbols(f.read())
    outputs = {"image": app.data, "elf_symbols": syms, "project_name": app.project_name,
               "image_hash": app.image_hash}
    if any("project_row" in r for sc in scopes.values() for e in (sc["table"] or {}).values()
           for r in e.get("refusal", [])):
        try:
            outputs["rows"] = read_rows(folders[0])
        except SwitchesError as e:
            outputs["rows"] = e
    return {"scopes": scopes, "named_without_table": named, "outputs": outputs, "folders": folders, "root": root}


def main(args):
    try:
        facts = collect(args.build)
    except (SwitchesError, BuildDirError, IMG.ImageError, OSError, KeyError) as e:
        print("FAIL R20: cannot read the build or its tables: %s" % e)
        return 1
    print("R20 build switches: %s" % ", ".join(os.path.relpath(f, facts["root"]) for f in facts["folders"]))
    for name, sc in sorted(facts["scopes"].items(), key=lambda kv: (kv[0] != PROJECT, kv[0])):
        if sc["table"] is None:
            print("  %s: %d tested, no table, not judged" % (name, len(sc["found"])))
            continue
        t = sc["table"]
        print("  %s: %d tested, %d listed" % (name, len(sc["found"]), len(t)))
        for s in sorted(set(sc["found"]) | set(t)):
            print("    %-40s %-34s %s" % (s, (t.get(s) or {}).get("class", "UNLISTED"),
                                         ", ".join(sc["found"].get(s, ["(not tested)"])[:SHOW])))
    bad = judge(facts)
    if not bad:
        print("PASS R20")
        return 0
    print("FAIL R20: %d failure(s)" % len(bad))
    for scope, sym, what, detail in bad:
        print("    %s: %s %s: %s" % (scope, sym, what, detail))
    return 1
