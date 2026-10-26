#!/usr/bin/env python3
"""Spec 12.3 item 3: every build-time switch is classified.

    python3 build_switches.py [--repo DIR] [--build DIR]   # default: this checkout, <repo>/build

WHAT THE SPEC ASKS (added 2026-10-13, user). Every preprocessor symbol the
product source tests, other than ESP-IDF's `CONFIG_*` and header guards, and
every build option tested by a CMake file the build reads from the repo, is
listed as one of:

    shipping variant   a configuration that may fly (auto-arm, for example);
    test-only          must never reach a vehicle; has refusal rules that
                       judge the image and the ELF (build_output.py evaluates
                       them);
    stock feature off in this product
                       (class added 2026-10-16, user; written as shared/tools
                       SPEC R20 writes it since the table moved) one of the
                       spec 3.2 feature switches: on in the shared code, off
                       here. It has the same kind of refusal rules as a
                       test-only switch, but no test trigger, so it is not in
                       TRIGGERS;
    not a switch       a default value, a value the build injects, or a
                       library macro -- each with its reason. Those three are
                       the spec's examples, not a closed list (reviewer's
                       reading, 2026-10-13).

An unlisted one fails the build checks. So does a listed one the source no
longer tests: a renamed switch would otherwise leave a stale entry beside an
unlisted new name, and the stale entry would read as coverage.

Product source is what the build compiled: every file ninja's deps log says
a compile read (`ninja -t deps`, VALID records, in the build directory and each
CMake build folder below it, such as the bootloader's), inside the repo, minus
the build directory and `managed_components/`. READING CHANGED 2026-10-24
(gen-inhibit reviewer, Q12 of shared/tools R20, after the user's Q1 ruling on
the shared check): it was `main/` and `components/`, walked whole (reviewer
2026-10-13). A file the build does not compile cannot reach the image, and what
decides whether it is compiled is in the CMake files, which are still scanned.
So the C scan needs a built tree, not only a configured one.

THE CMAKE FILES come from the build's own record of what it read, not from a
list kept here (spec, changed 2026-10-13, user): the inputs of build.ninja's
regeneration rule (`ninja -t query build.ninja`), keeping CMakeLists.txt and
*.cmake files inside the repo, minus the build directory and
`managed_components/` (excluded by that exact directory, as on the C side;
reviewer 2026-10-13). Files outside the repo -- the IDF install, CMake's own
modules -- are not the repo's. So a new include() is scanned the first time
the build reads it, with nothing to update here. The scan therefore needs a
built build directory, and it refuses (fails, never falls back to a walk)
when:
  - there is no build.ninja, or ninja cannot answer the query or list its deps;
  - the deps log names no file compiled from the repo (configured, not built);
  - the scanned list is empty or lacks the top-level CMakeLists.txt (the record
    is not this tree's -- a filter that came back empty must not read as clean);
  - a scanned file is newer than build.ninja (the record may predate an edit,
    such as a new include(); reconfigure first).
It prints the files it scanned and the ones it excluded, with the reason.

A BUILD OPTION is a variable that can be set from outside the file that tests
it: the CMake cache, a -D, or the environment. A variable the file sets itself
before testing it, and CMake's built-in variables, are not build options (spec,
added 2026-10-13, user). Read narrowly, so the scan errs towards listing
(reviewer, 2026-10-13):
  - "sets itself" is only `set(VAR <value>)` at file scope (not inside
    if/foreach/while/function/macro/block), with no CACHE and no PARENT_SCOPE,
    on a line before the test, whose value references nothing but built-ins and
    variables the file has already set. `set(X ${Y})` with Y from outside
    keeps X a build option: Y reaches the test through it. Every other way of
    setting a variable -- string(LENGTH ...), a function's out-parameter, a set
    inside an if, `set(X)` with no value, unset() -- leaves it a build option;
  - `$ENV{X}` is always a build option;
  - a variable referenced inside a quoted string in a condition ("${X}") is
    tested, like an unquoted one;
  - the built-ins are `cmake --help-variable-list` of the cmake the build
    recorded (CMAKE_COMMAND in CMakeCache.txt). <PROJECT-NAME> expands only to
    a project name the scanned files declare; any other placeholder matches
    only in a pattern with a literal prefix, so CMAKE_<LANG>_FLAGS counts and
    <PackageName>_ROOT, which would match anything ending in _ROOT, does not.

THE SCAN IS TEXTUAL and deliberately over-inclusive. Every identifier in an
`#if`/`#elif` expression is a tested symbol, so a function-like macro such as
`ESP_IDF_VERSION_VAL` has to be listed too. A scan that tried to be clever
about which identifiers "really" count would be one more place for a switch
to hide. Comments are stripped and continuation lines joined first, so a
commented-out `#if` is not counted and a split one is.

A HEADER GUARD is recognised only in its exact shape: the file's first
directive is `#ifndef X` and the next is `#define X` with no value. The same
`#ifndef X` followed by `#define X 60000` is a default value, which a `-D`
can override, and it has to be listed.

THE TABLE is in the repo root's vtrux_checks.json (key "build_switches"), with
the trigger table that 12.4's "test-only features are inert in the build that
will fly" draws from (key "inert_triggers"; spec: "This list is also what keeps
the trigger table ... complete"). Moved there 2026-10-24 (gen-inhibit reviewer,
shared/tools R20 Q7) so the shared R20 check reads the same table. This check
stays until gen-inhibit's release builds move to the shared tooling (Q9).
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(os.path.abspath(os.path.join(HERE, "..", "..")), "firmware")   # the WiCAN tree (port step 0d)

SHIP, TEST, NOT = "shipping variant", "test-only", "not a switch"
STOCK = "stock feature off in this product"     # shared/tools SPEC R20's wording, the table's since the move
CLASSES = (SHIP, TEST, STOCK, NOT)
REFUSED = (TEST, STOCK)            # the classes whose entry carries refusal rules
# A "not a switch" entry carries a kind and a reason. The spec's kinds (default
# value, injected value, library macro) are examples, not a closed list
# (reviewer's reading, 2026-10-13), so the kind is checked for presence only.

# THE TABLE IS DATA, in the repo root's vtrux_checks.json (moved 2026-10-24, the
# gen-inhibit reviewer's ruling on shared/tools R20 Q7), so this check, the shared
# R20 check (shared/tools/vtrux_checks/switches.py) and the bench rigs read one
# table:
#   "build_switches": symbol -> {"class", "reason", "kind" (not a switch),
#                                "refusal": [rule, ...] (test-only, stock-off)}
#   "inert_triggers": symbol -> {"probes": [[method, path], ...], "state",
#                                "effect", "observe": [...]}
# A rule judges the image or the ELF, never the flag (shared/tools SPEC R20):
# bin_strings_absent, elf_symbols_absent, elf_symbol_prefixes_absent,
# project_name_token_absent, or project_row (a row build_output.py writes).
# build_output.py's check_test_only_refused evaluates every rule on the image.
#
# Spec 12.4 "test-only features are inert in the build that will fly": for every
# test-only switch, the trigger a test run sends, the device state it is sent in,
# the effect it exists to produce, and what the inert check observes. The bench rigs
# READ THEIR REQUESTS FROM THE SAME FILE (restart_error_frames.py --trigger,
# test_only_inert.py), so a changed trigger changes the negative check and the test
# runs together, and the negative check cannot go on probing a path the feature no
# longer uses.
TABLE_FILE = os.path.join(REPO, "vtrux_checks.json")


def load_table(path=TABLE_FILE):
    """(SWITCHES, TRIGGERS) from vtrux_checks.json.
    SWITCHES: symbol -> (class, kind or None, reason, refusal rules or None);
    TRIGGERS: symbol -> {"probes": ((method, path), ...), "state", "effect", "observe": (...)}."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    sw = {s: (e.get("class"), e.get("kind"), e.get("reason"), e.get("refusal"))
          for s, e in d.get("build_switches", {}).items()}
    tr = {s: {"probes": tuple(tuple(x) for x in t.get("probes", ())), "state": t.get("state"),
              "effect": t.get("effect"), "observe": tuple(t.get("observe", ()))}
          for s, t in d.get("inert_triggers", {}).items()}
    return sw, tr


SWITCHES, TRIGGERS = load_table()


CMAKE_KEYWORDS = {
    "AND", "OR", "NOT", "DEFINED", "EQUAL", "LESS", "GREATER", "LESS_EQUAL",
    "GREATER_EQUAL", "STREQUAL", "STRLESS", "STRGREATER", "MATCHES", "EXISTS",
    "IS_DIRECTORY", "IS_ABSOLUTE", "COMMAND", "POLICY", "TARGET", "TEST",
    "IN_LIST", "VERSION_LESS", "VERSION_GREATER", "VERSION_EQUAL",
    "VERSION_LESS_EQUAL", "VERSION_GREATER_EQUAL", "TRUE", "FALSE", "ON", "OFF",
    "YES", "NO", "Y", "N", "IGNORE", "NOTFOUND", "ENV", "CACHE",
}
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
            j = n if j < 0 else j
            i = j
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
    """[(symbol, line)] for every symbol a directive in this file tests, header
    guard excluded. String literals inside an #if are not identifiers."""
    lines = [(ln, line) for ln, line in logical_lines(text) if line.strip()]
    directives = []
    for ln, line in lines:
        m = DIRECTIVE.match(line)
        if m:
            directives.append((ln, m.group(1), m.group(2).strip()))
    # The guard: the file's first non-blank line is `#ifndef X` and the next is
    # `#define X` with no value. Anything before it -- an #include, code -- and
    # it is an ordinary #ifndef.
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
    """(variables, environment variables) a condition tests. Every unquoted
    identifier that is not a keyword or a number, every ${X} and every $ENV{X}
    -- quoted ones included: "${X}" STREQUAL "on" tests X."""
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
    not a built-in, and not a variable this file set itself before the test
    (narrow reading -- see the module docstring)."""
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
            var = words[0]
            vals = words[1:]
            if (name == "unset" or depth > 0 or not vals
                    or "CACHE" in vals or "PARENT_SCOPE" in vals):
                own.discard(var)
                continue
            joined = " ".join(vals)
            refs = VAR_REF.findall(joined)
            if ENV_REF.search(joined) or not all(r in own or is_builtin(r) for r in refs):
                own.discard(var)
            else:
                own.add(var)
    return out


# ------------------------------------------------------- the build's record --

class RecordError(Exception):
    """The build's record cannot be read, or cannot be trusted. The scan fails."""


EXCLUDED_DIRS = {"managed_components": "downloaded, pinned by dependencies.lock (reviewer 2026-10-13)"}
Record = collections.namedtuple("Record", "build_dir scanned excluded outside is_builtin cmake c_files")


def parse_query(text):
    """The input paths of the rule `ninja -t query build.ninja` prints: every
    line between `input:` and `outputs:`, explicit, implicit (|) and
    order-only (||) alike."""
    paths, inputs = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("input:"):
            inputs = True
        elif s.startswith("outputs:"):
            inputs = False
        elif inputs and s:
            s = s.lstrip("|").strip()
            if s:
                paths.append(s)
    return paths


def _norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def _inside(p, root):
    p, root = _norm(p), _norm(root)
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def is_cmake_file(p):
    b = os.path.basename(p)
    return b == "CMakeLists.txt" or b.lower().endswith(".cmake")


def record_files(paths, repo, build_dir):
    """(scanned, excluded, outside) from the record's paths. A relative path is
    relative to the build directory (ninja's working directory).
      scanned   repo-relative CMake files, sorted;
      excluded  [(repo-relative path, reason)] for CMake files inside the repo
                that are not scanned;
      outside   how many recorded CMake files lie outside the repo."""
    scanned, excluded, outside = set(), set(), 0
    for p in paths:
        a = os.path.join(build_dir, p)
        if not is_cmake_file(a):
            continue
        if _inside(a, build_dir):
            if _inside(a, repo):
                excluded.add((os.path.relpath(a, repo).replace(os.sep, "/"), "the build directory"))
            else:
                outside += 1
            continue
        if not _inside(a, repo):
            outside += 1
            continue
        rel = os.path.relpath(a, repo).replace(os.sep, "/")
        top = rel.split("/")[0]
        if top in EXCLUDED_DIRS:
            excluded.add((rel, EXCLUDED_DIRS[top]))
            continue
        scanned.add(rel)
    return sorted(scanned), sorted(excluded), outside


def check_record(scanned, repo, build_ninja):
    """Raise RecordError unless the scanned list can be trusted: non-empty, this
    tree's (it holds the top-level CMakeLists.txt), and no file newer than the
    record."""
    if not scanned:
        raise RecordError("the record names no CMake file in the repo")
    if "CMakeLists.txt" not in scanned:
        raise RecordError("the record lacks the top-level CMakeLists.txt: it is not this tree's build")
    t = os.path.getmtime(build_ninja)
    newer = [r for r in scanned if os.path.getmtime(os.path.join(repo, r)) > t]
    if newer:
        raise RecordError("newer than the record (build.ninja): %s -- reconfigure first" % ", ".join(newer))


def cache_value(build_dir, key):
    """A CMakeCache.txt entry's value, or None."""
    p = os.path.join(build_dir, "CMakeCache.txt")
    if not os.path.isfile(p):
        return None
    for line in open(p, encoding="utf-8", errors="replace"):
        if line.startswith(key + ":"):
            return line.split("=", 1)[1].rstrip("\r\n")
    return None


def _placeholder_regex(p):
    return "".join("[A-Za-z0-9_]+" if x.startswith("<") else re.escape(x)
                   for x in re.split(r"(<[^>]+>)", p) if x)


def builtin_matcher(patterns, project_names):
    """name -> bool over `cmake --help-variable-list`. <PROJECT-NAME> expands
    to the declared project names only; a pattern opening with any other
    placeholder is not used (it would match too much); other placeholders
    match one or more identifier characters."""
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


def project_names(repo, scanned):
    """The literal names project() declares in the scanned files."""
    names = set()
    for rel in scanned:
        text = open(os.path.join(repo, rel), encoding="utf-8", errors="replace").read()
        for name, args, _ in cmake_commands(text):
            w = cmake_args(args)
            if name == "project" and w and re.match(r"^[A-Za-z0-9_.+-]+$", w[0]):
                names.add(w[0])
    return sorted(names)


def read_record(repo, build_dir, run=subprocess.run):
    """The Record of the configured build in build_dir. Raises RecordError."""
    bn = os.path.join(build_dir, "build.ninja")
    if not os.path.isfile(bn):
        raise RecordError("no build.ninja in %s: configure first" % build_dir)
    ninja = cache_value(build_dir, "CMAKE_MAKE_PROGRAM") or "ninja"
    cmake = cache_value(build_dir, "CMAKE_COMMAND")
    if not cmake:
        raise RecordError("no CMAKE_COMMAND in %s/CMakeCache.txt" % build_dir)
    try:
        q = run([ninja, "-t", "query", "build.ninja"], cwd=build_dir, capture_output=True, text=True)
        v = run([cmake, "--help-variable-list"], capture_output=True, text=True)
    except OSError as e:
        raise RecordError("cannot run ninja or cmake: %s" % e)
    if q.returncode != 0:
        raise RecordError("ninja -t query build.ninja failed: %s" % (q.stderr or q.stdout).strip()[:200])
    if v.returncode != 0 or not v.stdout.strip():
        raise RecordError("cmake --help-variable-list gave nothing")
    scanned, excluded, outside = record_files(parse_query(q.stdout), repo, build_dir)
    check_record(scanned, repo, bn)
    c_files = set()
    for folder in build_folders(build_dir):
        try:
            d = run([ninja, "-t", "deps"], cwd=folder, capture_output=True, text=True)
        except OSError as e:
            raise RecordError("cannot run ninja: %s" % e)
        if d.returncode != 0:
            raise RecordError("ninja -t deps in %s failed: %s" % (folder, (d.stderr or d.stdout).strip()[:200]))
        c_files.update(c_record_files(parse_deps(d.stdout), repo, build_dir, folder))
    if not c_files:
        raise RecordError("the build's deps log names no file compiled from the repo: build first")
    return Record(build_dir, scanned, excluded, outside,
                  builtin_matcher(v.stdout.splitlines(), project_names(repo, scanned)), cmake, sorted(c_files))


def parse_deps(text):
    """The dependency paths `ninja -t deps` prints, for every output whose record
    is VALID: a STALE record is an output this build no longer has, or one older
    than its record."""
    paths, keep = [], False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            keep = "(STALE)" not in line
        elif keep:
            paths.append(line.strip())
    return paths


def build_folders(build_dir):
    """build_dir and every CMake build folder below it (its own CMakeCache.txt and
    build.ninja, such as the bootloader's)."""
    out = [build_dir]
    for d, dirs, files in os.walk(build_dir):
        dirs.sort()
        if not _inside(d, build_dir) or _norm(d) == _norm(build_dir):
            continue
        if "CMakeCache.txt" in files and "build.ninja" in files:
            out.append(d)
    return out


def c_record_files(paths, repo, build_dir, folder):
    """Repo-relative paths of the files the compiles read (a relative path is
    relative to the folder ninja ran in), minus the build directory and
    EXCLUDED_DIRS."""
    out = set()
    for p in paths:
        a = os.path.join(folder, p)
        if _inside(a, build_dir) or not _inside(a, repo):
            continue
        rel = os.path.relpath(a, repo).replace(os.sep, "/")
        if rel.split("/")[0] in EXCLUDED_DIRS:
            continue
        out.add(rel)
    return sorted(out)


def scan(repo=REPO, record=None):
    """{symbol: [where, ...]} over the product source, CONFIG_* excluded: the
    files the build compiled (the record's c_files) and the CMake files in the
    record."""
    found = {}

    def add(sym, where):
        if sym.startswith("CONFIG_"):
            return
        found.setdefault(sym, []).append(where)

    if record is None:
        record = read_record(repo, os.path.join(repo, "build"))
    for rel in record.c_files:
        text = open(os.path.join(repo, rel), encoding="utf-8", errors="replace").read()
        for s, ln in c_symbols(text):
            add(s, "%s:%d" % (rel, ln))
    for rel in record.scanned:
        text = open(os.path.join(repo, rel), encoding="utf-8", errors="replace").read()
        for s, ln in cmake_symbols(text, record.is_builtin):
            add(s, "%s:%d" % (rel, ln))
    return found


LIST_RULES = ("bin_strings_absent", "elf_symbols_absent", "elf_symbol_prefixes_absent")
TEXT_RULES = ("project_name_token_absent", "project_row")


def rule_problems(refusal):
    """What is wrong with an entry's refusal rules, as shared/tools R20 reads them:
    a list of one-key objects, a list rule a non-empty list of non-empty strings,
    a text rule a non-empty string."""
    if not isinstance(refusal, list):
        return ["refusal is not a list of rules"]
    out = []
    for k, r in enumerate(refusal, 1):
        if not isinstance(r, dict) or len(r) != 1:
            out.append("refusal rule %d is not one key" % k)
            continue
        (name, v), = r.items()
        if name in LIST_RULES:
            if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
                out.append("refusal rule %d (%s) wants a non-empty list of strings" % (k, name))
        elif name in TEXT_RULES:
            if not isinstance(v, str) or not v.strip():
                out.append("refusal rule %d (%s) wants a non-empty string" % (k, name))
        else:
            out.append("refusal rule %d: %r is not a rule" % (k, name))
    return out


def classify(found, table=None, triggers=None):
    """The comparison, as data: (unlisted, stale, malformed). A test-only switch
    with no trigger entry, and a trigger entry for anything not test-only, are
    malformed: the 12.4 inert check draws its probes from TRIGGERS."""
    table = SWITCHES if table is None else table
    triggers = TRIGGERS if triggers is None else triggers
    unlisted = sorted(s for s in found if s not in table)
    stale = sorted(s for s in table if s not in found)
    bad = []
    for s, entry in sorted(table.items()):
        cls, kind, reason, refusal = entry
        if cls not in CLASSES:
            bad.append("%s: class %r is not one of the four" % (s, cls))
        if cls == NOT and not (kind or "").strip():
            bad.append("%s: 'not a switch' needs a kind" % s)
        if not (reason or "").strip():
            bad.append("%s: no reason" % s)
        if cls in REFUSED and not refusal:
            bad.append("%s: %s with no refusal row" % (s, cls))
        if cls not in REFUSED and refusal:
            bad.append("%s: refusal rules on a %s entry" % (s, cls))
        if cls in REFUSED and refusal:
            bad += ["%s: %s" % (s, why) for why in rule_problems(refusal)]
        if cls == TEST and not (triggers.get(s) or {}).get("probes"):
            bad.append("%s: test-only with no trigger (12.4 inert check)" % s)
    for s in sorted(triggers):
        if (table.get(s) or (None,))[0] != TEST:
            bad.append("%s: has a trigger but is not a listed test-only switch" % s)
    return unlisted, stale, bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=REPO, help="the tree to scan (default: this checkout)")
    ap.add_argument("--build", help="its built build directory (default: <repo>/build)")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    build_dir = os.path.abspath(a.build or os.path.join(repo, "build"))
    try:
        rec = read_record(repo, build_dir)
    except RecordError as e:
        print("FAIL: the build's record cannot be used: %s" % e)
        return 1
    print("CMake files from the build's record (%s):" % rec.cmake)
    for r in rec.scanned:
        print("  scanned   %s" % r)
    for r, why in rec.excluded:
        print("  excluded  %s  (%s)" % (r, why))
    print("  outside the repo: %d (IDF install, CMake modules)" % rec.outside)
    print("Files the build compiled, from its deps log: %d" % len(rec.c_files))
    print()
    found = scan(repo, rec)
    unlisted, stale, bad = classify(found)
    for s in sorted(found):
        e = SWITCHES.get(s)
        print("%-40s %-16s %s" % (s, e[0] if e else "UNLISTED", ", ".join(found[s][:3])))
    print()
    for s in stale:
        print("STALE      %s (listed, no longer tested)" % s)
    for b in bad:
        print("MALFORMED  %s" % b)
    print("unlisted %d, stale %d, malformed %d" % (len(unlisted), len(stale), len(bad)))
    return 1 if (unlisted or stale or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
