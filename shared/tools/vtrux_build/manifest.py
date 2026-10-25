"""Read the dependencies of an ESP-IDF component manifest (idf_component.yml).

A line scanner, for the same reason as lockfile.py. It reads the block form
the Component Manager documents, at any indent:

    dependencies:
      espressif/mdns: "*"          inline: the version spec only
      twai_port:
        version: "v1.2.0"          for a git source, the tag or ref pinned
        git: https://...
        path: shared/components/twai_port
        override_path: ../../shared/components/twai_port

Comments and blank lines are skipped. Flow style ({...} or [...]) as a
dependency's value raises ManifestError rather than being misread. Fields
other than version, git, path, override_path and require are ignored.

What the lock cannot tell: dependencies.lock records an override_path
dependency as a local `path`, and a git dependency's version as the commit
it resolved to. The tag pinned and the override are only here.
"""

import re

FIELDS = ("version", "git", "path", "override_path", "require")

_KEY = re.compile(r"^( *)([^\s:#'\"][^:]*|'[^']*'|\"[^\"]*\"):(?:\s+(.*?))?\s*$")


class ManifestError(ValueError):
    pass


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def _strip_comment(ln):
    """Drop a trailing "# ..." that is outside quotes."""
    q = None
    for i, c in enumerate(ln):
        if q:
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == "#" and (i == 0 or ln[i - 1] in " \t"):
            return ln[:i].rstrip()
    return ln.rstrip()


def parse(text):
    """{dependency name: {field: value}} from a manifest's text; an inline
    spec gives {"version": spec}."""
    out = {}
    in_deps = False
    dep_indent = None
    field_indent = None
    cur = None
    for raw in text.splitlines():
        ln = _strip_comment(raw)
        if not ln.strip():
            continue
        if "\t" in ln[:len(ln) - len(ln.lstrip())]:
            raise ManifestError("a tab in the indent: %r" % raw)
        m = _KEY.match(ln)
        indent = len(ln) - len(ln.lstrip(" "))
        if indent == 0:
            in_deps = bool(m) and m.group(2) == "dependencies" and not m.group(3)
            dep_indent, cur = None, None
            continue
        if not in_deps:
            continue
        if ln.lstrip().startswith("-"):
            continue                    # list items (rules, matches)
        if not m:
            raise ManifestError("an unreadable line under dependencies: %r"
                                % raw)
        key, val = _unquote(m.group(2)), (m.group(3) or "")
        if val[:1] in ("{", "["):
            raise ManifestError("flow style is not read: %r" % raw)
        if dep_indent is None:
            dep_indent = indent
        if indent == dep_indent:
            cur = {}
            field_indent = None
            out[key] = cur
            if val:
                cur["version"] = _unquote(val)
            continue
        if indent < dep_indent:
            raise ManifestError("an indent shallower than the dependencies: "
                                "%r" % raw)
        if cur is None:
            raise ManifestError("a field before any dependency: %r" % raw)
        if field_indent is None:
            field_indent = indent
        if indent != field_indent:
            continue                    # inside a field's own block
        if key in FIELDS and val:
            cur[key] = _unquote(val)
    return out


def read(path):
    with open(path, encoding="utf-8") as f:
        return parse(f.read())
