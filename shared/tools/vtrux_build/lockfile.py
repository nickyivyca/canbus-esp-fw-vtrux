"""Read an ESP-IDF Component Manager dependencies.lock.

A line scanner, not a YAML library: the tooling that checks downloaded
components against the lock should not itself depend on a downloaded
package, and the lock's shape is fixed by the Component Manager (lock
version 2.0.0, as IDF 5.4.1's manager writes it):

    dependencies:
      <name>:                      two spaces
        component_hash: <hex>      four spaces, scalars
        dependencies:              four spaces, a list (not read)
        - name: idf
        source:                    four spaces, a map
          type: service|git|local|idf|...   six spaces, scalars
          git: <url>  path: <path>  registry_url: <url>
        version: <version>
    direct_dependencies: ...
    target: esp32c3

Only the scalars named above are read. Quoted scalars ('x' or "x") are
unquoted. A lock that does not start its dependencies block as shown raises
LockError.

What a source type means for a build (Component Manager 2.x):
    service, git   downloaded into managed_components/<dir>
    local          read from a folder: a `path:` or an `override_path:`
                   dependency (the lock records both as `path`)
    idf            ESP-IDF itself, not a component folder
"""

import hashlib
import re

DOWNLOADED_TYPES = ("service", "git")

_NAME = re.compile(r"^  ([^\s:#][^:]*):\s*$")
_SCALAR4 = re.compile(r"^    ([A-Za-z_]+):\s*(.*?)\s*$")
_SCALAR6 = re.compile(r"^      ([A-Za-z_]+):\s*(.*?)\s*$")


class LockError(ValueError):
    pass


def _unquote(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def parse(text):
    """{name: {"component_hash", "version", "source": {...}}} from the
    lock's text. Keys absent in the lock are absent here."""
    lines = text.splitlines()
    try:
        start = lines.index("dependencies:")
    except ValueError:
        raise LockError("no top-level 'dependencies:' line")
    out = {}
    cur = None
    in_source = False
    for ln in lines[start + 1:]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if not ln.startswith(" "):
            break                       # the next top-level key
        m = _NAME.match(ln)
        if m:
            cur = {}
            out[_unquote(m.group(1).strip())] = cur
            in_source = False
            continue
        if cur is None:
            raise LockError("an indented line before the first component: %r"
                            % ln)
        m = _SCALAR6.match(ln)
        if m and in_source:
            cur.setdefault("source", {})[m.group(1)] = _unquote(m.group(2))
            continue
        m = _SCALAR4.match(ln)
        if m:
            key, val = m.group(1), m.group(2)
            in_source = key == "source" and val == ""
            if in_source:
                cur.setdefault("source", {})
            elif key in ("component_hash", "version") and val:
                cur[key] = _unquote(val)
            continue
        if ln.startswith("    -") or ln.startswith("      "):
            continue                    # list items and their fields
        raise LockError("unexpected line in dependencies: %r" % ln)
    return out


def read(path):
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def lf_sha256(data):
    """SHA-256 of data (bytes) with CRLF line endings normalised to LF
    (SPEC R13), so one lock gives one hash whatever its line endings, on
    any host and in any checkout."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def file_lf_sha256(path):
    with open(path, "rb") as f:
        return lf_sha256(f.read())


def managed_dir_name(name):
    """The managed_components/ folder the Component Manager uses for a
    dependency: "namespace/name" becomes "namespace__name"."""
    return name.replace("/", "__", 1)
