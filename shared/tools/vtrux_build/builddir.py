"""Read an ESP-IDF build directory: the build's own record of what it did.

This is the interface the build checks read (the tester's, SPEC R17 to R21)
as well as the build script. Everything here only reads; nothing runs ninja,
CMake or idf.py, so it works on a copied build folder and in tests.

    bd = BuildDir("<project>/build")
    bd.description()     project_description.json, as a dict
    bd.app_bin()         R11: the image this build named (absolute path)
    bd.app_elf()         the ELF it named, the same way
    bd.config_file()     R8: the sdkconfig it used (absolute path)
    bd.options()         build/config/sdkconfig.json: the options in effect
    bd.cmake_cache()     CMakeCache.txt: {name: (type, value)}
    bd.ninja_deps()      .ninja_deps: {output: [files its depfile recorded]}
    bd.ninja_edges()     build.ninja (and the files it includes): every
                         build statement as an Edge, with its own bindings
                         and its rule's command as ninja would run it
    bd.cmake_inputs()    the files CMake read, as build.ninja's re-run rule
                         records them
    bd.components()      {component name: its folder}, from the description
    bd.files_read()      the union of ninja_deps, every edge's explicit and
                         implicit inputs, and cmake_inputs, as absolute
                         normalised paths

Paths from ninja are as ninja recorded them: relative to the build folder or
absolute. resolve() makes one absolute. On Windows, CMake writes "C$:/..."
in build.ninja; the "$:" escape is undone here, so paths come back as
"C:/...".

Errors: BuildDirError for anything missing or unreadable that a caller
asked for. Nothing falls back to a guess.
"""

import json
import os
import re
import struct

NINJA_DEPS_SIGNATURE = b"# ninjadeps\n"
NINJA_DEPS_VERSIONS = (3, 4)


class BuildDirError(ValueError):
    pass


class Edge:
    """One ninja build statement. bindings are its own indented variables,
    expanded in the file scope as ninja does; command is its rule's command
    as ninja would run it (None for phony or a rule with no command)."""

    __slots__ = ("rule", "outputs", "implicit_outputs", "inputs",
                 "implicit", "order_only", "validations", "bindings",
                 "command")

    def __init__(self, rule, outputs, implicit_outputs, inputs, implicit,
                 order_only, validations, bindings=None, command=None):
        self.rule = rule
        self.outputs = outputs
        self.implicit_outputs = implicit_outputs
        self.inputs = inputs
        self.implicit = implicit
        self.order_only = order_only
        self.validations = validations
        self.bindings = bindings or {}
        self.command = command

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return "Edge(%s: %s)" % (" ".join(self.outputs), self.rule)


def _read_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise BuildDirError("%s cannot be read (%s)" % (what, e))


class BuildDir:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self._desc = None
        self._edges = None

    def file(self, *parts):
        return os.path.join(self.path, *parts)

    def resolve(self, p):
        """A path ninja recorded, made absolute and normalised."""
        if not os.path.isabs(p):
            p = os.path.join(self.path, p)
        return os.path.normpath(p)

    # --- project_description.json -------------------------------------

    def description(self):
        if self._desc is None:
            d = _read_json(self.file("project_description.json"),
                           "build/project_description.json")
            if not isinstance(d, dict):
                raise BuildDirError("build/project_description.json is not "
                                    "an object")
            self._desc = d
        return self._desc

    def _named(self, key):
        name = self.description().get(key)
        if not isinstance(name, str) or not name:
            raise BuildDirError("build/project_description.json has no %r, so "
                                "which file this build made is unknown" % key)
        if os.path.basename(name) != name:
            raise BuildDirError("build/project_description.json's %r is %r, "
                                "not a file name in build/" % (key, name))
        p = self.file(name)
        if not os.path.isfile(p):
            raise BuildDirError("build/project_description.json names %s as "
                                "its %s, and build/ has no such file"
                                % (name, key))
        return p

    def app_bin(self):
        """R11: the .bin project_description.json names as app_bin. Never the
        newest .bin in the folder: a missing name or file raises."""
        return self._named("app_bin")

    def app_elf(self):
        return self._named("app_elf")

    def config_file(self):
        """R8: the sdkconfig the build used, project_description.json's
        config_file (IDF's SDKCONFIG build property, however it was set),
        as an absolute normalised path. It need not exist."""
        p = self.description().get("config_file")
        if not isinstance(p, str) or not p:
            raise BuildDirError("build/project_description.json has no "
                                "config_file, so which sdkconfig this build "
                                "used is unknown")
        if not os.path.isabs(p):
            raise BuildDirError("build/project_description.json's config_file "
                                "is %r, not an absolute path" % p)
        return os.path.normpath(p)

    def components(self):
        """{name: folder} for every component the build used
        (build_components, with each one's dir from build_component_info)."""
        d = self.description()
        names = d.get("build_components")
        info = d.get("build_component_info")
        if not isinstance(names, list) or not isinstance(info, dict):
            raise BuildDirError("build/project_description.json has no "
                                "build_components / build_component_info")
        out = {}
        for n in names:
            if n == "":
                continue    # IDF 5.4.1 ends the list with an empty entry
            ci = info.get(n)
            if not isinstance(ci, dict) or not ci.get("dir"):
                raise BuildDirError("no folder recorded for component %r" % n)
            out[n] = os.path.normpath(ci["dir"])
        return out

    # --- options --------------------------------------------------------

    def options(self):
        """The sdkconfig options in effect, as build/config/sdkconfig.json
        gives them (CONFIG_ prefix left off, as IDF writes it; values typed:
        bool, int or str)."""
        d = _read_json(self.file("config", "sdkconfig.json"),
                       "build/config/sdkconfig.json")
        if not isinstance(d, dict):
            raise BuildDirError("build/config/sdkconfig.json is not an object")
        return d

    def cmake_cache(self):
        """{name: (type, value)} from build/CMakeCache.txt."""
        p = self.file("CMakeCache.txt")
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            raise BuildDirError("build/CMakeCache.txt cannot be read (%s)" % e)
        out = {}
        for ln in lines:
            if not ln or ln.startswith("//") or ln.startswith("#"):
                continue
            key, sep, val = ln.partition("=")
            if not sep:
                continue
            name, _, typ = key.partition(":")
            out[name] = (typ, val)
        return out

    # --- ninja ----------------------------------------------------------

    def ninja_deps(self, current_only=True):
        """{output: [deps]} from build/.ninja_deps, the depfile records ninja
        keeps (headers and sources each compile read). The newest record for
        an output wins, as in ninja.

        The log keeps records for outputs an earlier configure had and this
        one does not (an object file under an old image name, for example)
        until ninja recompacts it. With current_only (the default) only
        outputs of an edge in today's build.ninja are returned, as
        `ninja -t deps` lists them; current_only=False returns the whole
        log."""
        deps = parse_ninja_deps(self.file(".ninja_deps"))
        if not current_only:
            return deps
        outs = set()
        for e in self.ninja_edges():
            outs.update(self.resolve(p) for p in e.outputs + e.implicit_outputs)
        return {k: v for k, v in deps.items() if self.resolve(k) in outs}

    def ninja_edges(self):
        if self._edges is None:
            self._edges = parse_ninja_file(self.file("build.ninja"), self.path)
        return self._edges

    def cmake_inputs(self):
        """The files CMake read when it configured: the inputs of the edge
        that re-runs CMake (rule RERUN_CMAKE), as CMake wrote them."""
        for e in self.ninja_edges():
            if e.rule == "RERUN_CMAKE":
                return e.inputs + e.implicit
        raise BuildDirError("build.ninja has no RERUN_CMAKE edge")

    def files_read(self):
        """Every file the build's records say it read, absolute and
        normalised, sorted. Outputs of other edges are included where they
        are inputs: telling a generated file from a source is the caller's
        job."""
        seen = set()
        for deps in self.ninja_deps().values():
            seen.update(self.resolve(p) for p in deps)
        for e in self.ninja_edges():
            seen.update(self.resolve(p) for p in e.inputs + e.implicit)
        seen.update(self.resolve(p) for p in self.cmake_inputs())
        return sorted(seen)


def parse_ninja_deps(path):
    """Read ninja's binary deps log (format versions 3 and 4).

        "# ninjadeps\\n", int32 version, then records, each a uint32 size
        and size bytes. Size with the top bit set: a deps record, int32
        output id, the mtime (int32 in v3, two uint32 in v4), then int32
        dep ids. Otherwise a path record: the path, NUL padded to 4 bytes,
        then uint32 ~id, where id counts path records from 0.
    """
    try:
        with open(path, "rb") as f:
            b = f.read()
    except OSError as e:
        raise BuildDirError("%s cannot be read (%s)" % (path, e))
    if not b.startswith(NINJA_DEPS_SIGNATURE):
        raise BuildDirError("%s is not a ninja deps log" % path)
    o = len(NINJA_DEPS_SIGNATURE)
    if len(b) < o + 4:
        raise BuildDirError("%s ends inside its header" % path)
    version = struct.unpack_from("<i", b, o)[0]
    if version not in NINJA_DEPS_VERSIONS:
        raise BuildDirError("%s is ninja deps log version %d; this reads %s"
                            % (path, version, NINJA_DEPS_VERSIONS))
    o += 4
    mtime_words = 2 if version >= 4 else 1
    paths = []
    deps = {}
    while o < len(b):
        if o + 4 > len(b):
            raise BuildDirError("%s ends inside a record header" % path)
        size = struct.unpack_from("<I", b, o)[0]
        o += 4
        is_deps = bool(size & 0x80000000)
        size &= 0x7FFFFFFF
        if o + size > len(b) or size % 4:
            raise BuildDirError("%s has a truncated or misaligned record at %d"
                                % (path, o - 4))
        rec = b[o:o + size]
        o += size
        if is_deps:
            n = size // 4
            if n < 1 + mtime_words:
                raise BuildDirError("%s has a short deps record" % path)
            ids = struct.unpack("<%di" % n, rec)
            out_id, dep_ids = ids[0], ids[1 + mtime_words:]
            try:
                deps[paths[out_id]] = [paths[i] for i in dep_ids]
            except IndexError:
                raise BuildDirError("%s names a path id it has not defined"
                                    % path)
        else:
            if size < 4:
                raise BuildDirError("%s has a short path record" % path)
            name = rec[:-4].rstrip(b"\0")
            check = struct.unpack("<I", rec[-4:])[0]
            if check != (~len(paths)) & 0xFFFFFFFF:
                raise BuildDirError("%s: path record %d fails its checksum"
                                    % (path, len(paths)))
            paths.append(name.decode("utf-8", "surrogateescape"))
    return deps


def _ninja_lines(text):
    """Logical lines: "$" at the end of a line joins the next, whose leading
    whitespace is dropped."""
    out, cur = [], None
    for raw in text.splitlines():
        if cur is not None:
            raw = cur + raw.lstrip(" ")
            cur = None
        stripped = raw.rstrip("\r")
        # An odd run of "$" at the end is a continuation; "$$" is a dollar.
        n = len(stripped) - len(stripped.rstrip("$"))
        if n % 2:
            cur = stripped[:-1]
            continue
        out.append(stripped)
    if cur is not None:
        out.append(cur)
    return out


_SIMPLE_VARNAME = re.compile(r"[A-Za-z0-9_-]+")


def _expand(s, env, keep_escapes):
    """Undo ninja's escapes in s and expand $name and ${name} from env (an
    unset variable is empty, as in ninja). With keep_escapes, "$ " and "$:"
    stay escaped, so the caller can still split on them."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c != "$":
            out.append(c)
            i += 1
            continue
        nxt = s[i + 1:i + 2]
        if nxt in (" ", ":"):
            out.append(s[i:i + 2] if keep_escapes else nxt)
            i += 2
        elif nxt == "$":
            out.append("$$" if keep_escapes else "$")
            i += 2
        elif nxt == "{":
            j = s.find("}", i + 2)
            if j < 0:
                raise BuildDirError("an unclosed ${ in %r" % s)
            out.append(_escape(env.get(s[i + 2:j], ""), keep_escapes))
            i = j + 1
        else:
            m = _SIMPLE_VARNAME.match(s, i + 1)
            if not m:
                raise BuildDirError("a bad $ escape in %r" % s)
            out.append(_escape(env.get(m.group(0), ""), keep_escapes))
            i = m.end()
    return "".join(out)


def _escape(v, keep_escapes):
    """An expanded value, re-escaped when the caller still splits: a space
    in a variable's value does not separate paths in ninja."""
    if not keep_escapes:
        return v
    return v.replace("$", "$$").replace(" ", "$ ").replace(":", "$:")


def _ninja_paths(s, env):
    """Split a path list on unescaped spaces, expanding variables from env
    and undoing "$ ", "$:", "$$". Separators "|", "||" and "|@" are
    returned as their own tokens."""
    s = _expand(s, env, keep_escapes=True)
    toks, cur, i = [], [], 0
    while i < len(s):
        c = s[i]
        if c == "$":
            cur.append(s[i + 1])
            i += 2
            continue
        if c == " ":
            if cur:
                toks.append("".join(cur))
                cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    if cur:
        toks.append("".join(cur))
    return toks


def _split_colon(s):
    """The first unescaped ':' in a build line."""
    i = 0
    while i < len(s):
        if s[i] == "$":
            i += 2
            continue
        if s[i] == ":":
            return s[:i], s[i + 1:]
        i += 1
    raise BuildDirError("a build statement with no ':': %r" % s)


_BINDING = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s?(.*)$")


def _edge_command(rule, bindings, env, inputs, outputs):
    """A rule's command as ninja runs it for one edge: $in and $out, then
    the edge's bindings, then the rule's, then the file scope."""
    if not rule or "command" not in rule:
        return None
    scope = dict(env)
    scope.update(bindings)
    scope["in"] = " ".join(inputs)
    scope["out"] = " ".join(outputs)
    for k, v in rule.items():
        if k != "command" and k not in bindings and k not in ("in", "out"):
            scope[k] = _expand(v, scope, keep_escapes=False)
    return _expand(rule["command"], scope, keep_escapes=False)


def parse_ninja_text(text, base, _seen=None, env=None, rules=None):
    """Every build statement in a ninja file's text, following include
    (same scope) and subninja (a child scope), with paths relative to base,
    the build folder. Paths in a build line are expanded with the file-scope
    variables bound above it, as ninja does. An edge's own indented
    bindings never change its paths; they are kept in Edge.bindings, and
    its rule's command in Edge.command."""
    env = {} if env is None else env
    rules = {} if rules is None else rules
    edges = []
    lines = _ninja_lines(text)
    i = 0
    while i < len(lines):
        ln = lines[i]
        i += 1
        if not ln or ln.startswith("#") or ln[0] in " \t":
            continue
        word, _, rest = ln.partition(" ")
        body = {}
        if word in ("build", "rule", "pool"):
            # Its indented bindings; blank and comment lines are skipped, as
            # ninja's lexer skips them.
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip() or nxt.strip().startswith("#"):
                    i += 1
                    continue
                if nxt[0] not in " \t":
                    break
                # A value keeps its trailing spaces: CMake's LAUNCHER ends
                # in one, and the command runs ${LAUNCHER}<compiler>.
                m = _BINDING.match(nxt.lstrip(" \t"))
                if m:
                    body[m.group(1)] = m.group(2)
                i += 1
        if word in ("include", "subninja"):
            p = _expand(rest.strip(), env, keep_escapes=False)
            if not os.path.isabs(p):
                p = os.path.join(base, p)
            if word == "include":
                edges.extend(parse_ninja_file(p, base, _seen, env, rules))
            else:
                edges.extend(parse_ninja_file(p, base, _seen, dict(env),
                                              dict(rules)))
            continue
        if word == "rule":
            rules[rest.strip()] = body
            continue
        if word not in ("build", "pool", "default"):
            m = _BINDING.match(ln)
            if m:
                env[m.group(1)] = _expand(m.group(2), env, keep_escapes=False)
            continue
        if word != "build":
            continue
        left, right = _split_colon(rest)
        outs = _ninja_paths(left, env)
        outputs, implicit_outputs = outs, []
        if "|" in outs:
            k = outs.index("|")
            outputs, implicit_outputs = outs[:k], outs[k + 1:]
        toks = _ninja_paths(right, env)
        if not toks:
            raise BuildDirError("a build statement with no rule: %r" % ln)
        rule, toks = toks[0], toks[1:]
        groups = {"": [], "|": [], "||": [], "|@": []}
        key = ""
        for t in toks:
            if t in groups:
                key = t
                continue
            groups[key].append(t)
        bindings = {k: _expand(v, env, keep_escapes=False)
                    for k, v in body.items()}
        command = _edge_command(rules.get(rule), bindings, env, groups[""],
                                outputs)
        edges.append(Edge(rule, outputs, implicit_outputs, groups[""],
                          groups["|"], groups["||"], groups["|@"],
                          bindings, command))
    return edges


def parse_ninja_file(path, base, _seen=None, env=None, rules=None):
    _seen = set() if _seen is None else _seen
    key = os.path.normcase(os.path.abspath(path))
    if key in _seen:
        raise BuildDirError("%s is included twice" % path)
    _seen.add(key)
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            text = f.read()
    except OSError as e:
        raise BuildDirError("%s cannot be read (%s)" % (path, e))
    return parse_ninja_text(text, base, _seen, env, rules)
