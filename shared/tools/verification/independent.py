"""The tester's own reading of SPEC R1, R2 and R7, written from SPEC.md and ESP-IDF's
published app image format, never from vtrux_build's source, so the verification
compares two independent readings (as shared/bench's layout_hash_crosscheck does).

The image format (ESP-IDF "App Image Format"): a 24-byte header (magic 0xE9, the
segment count at byte 1, hash_appended at byte 23); each segment an 8-byte header
(load address, length) and its data; zero padding up to the byte before a 16-byte
boundary, then a checksum byte (0xEF XOR every segment data byte); then, when
hash_appended is 1, the SHA-256 of everything before it. The app descriptor
(esp_app_desc_t, magic 0xABCD5432) opens the first segment's data, at 0x20: version
at 0x30 (32 bytes), project_name 0x50 (32), time 0x70 (16), date 0x80 (16), idf_ver
0x90 (32), app_elf_sha256 0xb0 (32).

It also holds the verification scripts' scratch: scratch(name) gives a run its own folder,
removed when the process exits, and remove_tree(path) is the one remover they all use
(AGENTS.md "Removing scratch"). `python -B independent.py --self-test` tests the remover.

Gen-inhibit tester, 2026-10-24 (C6f verification); scratch removal 2026-11-02.
"""
import atexit
import contextlib
import hashlib
import io
import os
import shutil
import stat
import struct
import subprocess
import sys
import time

IMAGE_MAGIC = 0xE9
DESC_MAGIC = 0xABCD5432
DESC = 0x20
FIELDS = {"version": (0x30, 32), "project_name": (0x50, 32), "time": (0x70, 16), "date": (0x80, 16),
          "idf_ver": (0x90, 32), "elf_sha256": (0xB0, 32)}
R2_FIELDS = ("version", "time", "date", "elf_sha256")      # SPEC R2: blanked descriptor fields


class Bad(Exception):
    pass


def layout(data):
    """(segments [(offset of data, length)], checksum offset, sha offset or None); raises Bad
    unless the image parses to exactly its length with a good checksum and SHA-256."""
    if len(data) < 24 or data[0] != IMAGE_MAGIC:
        raise Bad("no image header")
    pos, segs, x = 24, [], 0xEF
    for _ in range(data[1]):
        if pos + 8 > len(data):
            raise Bad("segment header past the end")
        _addr, n = struct.unpack_from("<II", data, pos)
        pos += 8
        if pos + n > len(data):
            raise Bad("segment data past the end")
        segs.append((pos, n))
        for b in data[pos:pos + n]:
            x ^= b
        pos += n
    cks = pos + (15 - pos % 16)
    if cks >= len(data) or any(data[pos:cks]):
        raise Bad("no checksum byte, or padding not zero")
    if data[cks] != x:
        raise Bad("checksum")
    sha = None
    end = cks + 1
    if data[23] == 1:
        sha, end = end, end + 32
        if end > len(data) or hashlib.sha256(data[:sha]).digest() != data[sha:end]:
            raise Bad("SHA-256")
    if end != len(data):
        raise Bad("%d bytes after the image" % (len(data) - end))
    if not segs or struct.unpack_from("<I", data, DESC)[0] != DESC_MAGIC:
        raise Bad("no app descriptor")
    return segs, cks, sha


def blanks(data):
    """The (offset, length) ranges SPEC R2 blanks."""
    _segs, cks, sha = layout(data)
    out = [FIELDS[f] for f in R2_FIELDS] + [(cks, 1)]
    if sha is not None:
        out.append((sha, 32))
    return sorted(out)


def code_hash(data):
    """SPEC R2: the .bin's SHA-256 with the blanked ranges zeroed (a copy; the image is read)."""
    b = bytearray(data)
    for off, n in blanks(data):
        b[off:off + n] = bytes(n)
    return hashlib.sha256(bytes(b)).hexdigest()


def image_hash(data):
    """SPEC R1: the ELF's SHA-256 as the image embeds it."""
    layout(data)
    off, n = FIELDS["elf_sha256"]
    return data[off:off + n].hex()


def field(data, name):
    off, n = FIELDS[name]
    return data[off:off + n]


def build(segments=None, version=b"abc1234", project=b"proj", tm=b"00:00:00", dt=b"Jan  1 2026",
          idf=b"v5.4.1", elf_sha=bytes(range(32)), chip_id=5, hash_appended=1):
    """A valid synthetic app image: the descriptor opens segment 0 (followed by its data)."""
    def pad(v, n):
        return v + bytes(n - len(v))
    desc = (struct.pack("<II8x", DESC_MAGIC, 0) + pad(version, 32) + pad(project, 32) + pad(tm, 16) +
            pad(dt, 16) + pad(idf, 32) + elf_sha + bytes(256 - 0xB0 - 32 + DESC))[:256]
    segments = segments or [bytes((i * 7 + 3) % 256 for i in range(200)), bytes((i * 13 + 1) % 256 for i in range(333))]
    segs = [desc + segments[0]] + list(segments[1:])
    hdr = struct.pack("<BBBBI", IMAGE_MAGIC, len(segs), 2, 0x1F, 0x40380000)
    hdr += struct.pack("<B3sHBHH4sB", 0xEE, b"\0\0\0", chip_id, 0, 0, 0xFFFF, b"\0" * 4, hash_appended)
    body, x = bytearray(hdr), 0xEF
    for i, s in enumerate(segs):
        body += struct.pack("<II", 0x3C000000 + 0x10000 * i, len(s)) + s
        for b in s:
            x ^= b
    body += bytes(15 - len(body) % 16) + bytes([x])
    if hash_appended:
        body += hashlib.sha256(bytes(body)).digest()
    return bytes(body)


def with_field(data, name, value):
    """data with one descriptor field set, rebuilt so it stays a valid image."""
    segs, cks, sha = layout(data)
    b = bytearray(data[:cks])
    off, n = FIELDS[name]
    b[off:off + n] = value + bytes(n - len(value))
    return reseal(b, segs, data[23] == 1)


def with_byte(data, offset, value):
    """data with one byte changed inside a segment, resealed."""
    segs, cks, _sha = layout(data)
    b = bytearray(data[:cks])
    b[offset] = value
    return reseal(b, segs, data[23] == 1)


def reseal(b, segs, hashed):
    x = 0xEF
    for off, n in segs:
        for v in b[off:off + n]:
            x ^= v
    b = bytes(b) + bytes([x])
    return b + hashlib.sha256(b).digest() if hashed else b


# ---------- scratch: a run's own folder, and the one remover (AGENTS.md "Removing scratch") ----------

def scratch(name):
    """This run's own folder in the worktree's gitignored scratch/ (AGENTS.md "Files and folders"),
    removed by remove_tree when the process exits, pass or fail."""
    d = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            raise RuntimeError("not in a git worktree")
        d = os.path.dirname(d)
    d = os.path.join(d, "scratch", "shared-tools-verification", name,
                     "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    os.makedirs(d)
    atexit.register(remove_tree, d)
    return d


def long_path(p):
    """p made absolute and, on Windows, long-path safe: the \\\\?\\ prefix lifts the 260-character
    limit and stops Windows rewriting names (it drops a trailing dot or space)."""
    p = os.path.abspath(p)
    if os.name != "nt" or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def _say(text):
    print(text, flush=True)


def _retry_writable(func, path, errors):
    """rmtree's error handler: clear the read-only flag (Git for Windows sets it on .git/objects)
    and try once more. A second failure is kept, with its reason. A link is never followed."""
    if os.path.islink(path):
        errors.append("%s: a link, not followed" % path)
        return
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        func(path)
    except OSError as e:
        errors.append("%s: %s" % (path, e))


def remove_tree(p):
    """Remove folder p and everything in it. True when it is gone, or was never there. When any of
    it is left, prints the folder and the reasons and returns False: it is never silent."""
    lp = long_path(p)
    if not os.path.lexists(lp):
        return True
    errors = []

    def handler(func, path, _exc):
        _retry_writable(func, path, errors)
    shutil.rmtree(lp, **({"onexc": handler} if sys.version_info >= (3, 12) else {"onerror": handler}))
    if not os.path.lexists(lp):
        return True
    more = " (and %d more)" % (len(errors) - 5) if len(errors) > 5 else ""
    _say("could not remove %s: %s%s" % (p, "; ".join(errors[:5]) or "it is still there", more))
    return False


# ---------- the remover's self-test: the remover as written, then fakes with one rule broken ----------

REMOVER_FAKES = ["ignores errors", "no read-only retry", "no long-path prefix", "trusts rmtree", "says nothing",
                 "no removal at exit"]
WINDOWS_ONLY = ("no read-only retry", "no long-path prefix")    # rules only Windows needs
_CHILD = r'''
import os, stat, sys
sys.path.insert(0, sys.argv[1])
import independent as I
mode = sys.argv[2]
if mode == "no-atexit":
    import atexit
    atexit.register = lambda *a, **k: None
d = I.scratch("independent-selftest-exit")
print(d, flush=True)
f = os.path.join(d, ".git", "objects", "f")
os.makedirs(os.path.dirname(f))
with open(f, "w") as h:
    h.write("x")
os.chmod(f, stat.S_IREAD)
if mode == "held":
    if os.name == "nt":
        keep = open(f)
    else:
        os.chmod(os.path.dirname(f), 0o500)
'''


def _remover(broken):
    """(remover, the module names it patches) for the remover as written (None) or a fake."""
    if broken == "ignores errors":
        return (lambda p: (shutil.rmtree(long_path(p), ignore_errors=True), True)[1]), {}
    if broken == "trusts rmtree":
        return (lambda p: (remove_tree(p), True)[1]), {}
    if broken == "no read-only retry":
        return remove_tree, {"_retry_writable": lambda func, path, errors: errors.append(path)}
    if broken == "no long-path prefix":
        return remove_tree, {"long_path": os.path.abspath}
    if broken == "says nothing":
        return remove_tree, {"_say": lambda text: None}
    return remove_tree, {}


@contextlib.contextmanager
def _patched(names):
    old = {k: globals()[k] for k in names}
    globals().update(names)
    try:
        yield
    finally:
        globals().update(old)


def _call(remover, patch, p):
    buf = io.StringIO()
    with _patched(patch), contextlib.redirect_stdout(buf):
        got = remover(p)
    return got, buf.getvalue()


def _case_tree(base, remover, patch):
    """A tree like a clone's: a read-only file in .git/objects, a path past 260 characters and,
    on Windows, a folder whose name ends in a dot, which only a \\\\?\\ path reaches."""
    d = os.path.join(base, "tree")
    ro = os.path.join(d, ".git", "objects", "ab", "cdef")
    os.makedirs(os.path.dirname(ro))
    with open(ro, "w") as f:
        f.write("x")
    os.chmod(ro, stat.S_IREAD)
    deep = long_path(os.path.join(d, *(["d" * 50] * 6)))
    os.makedirs(deep)
    with open(os.path.join(deep, "f"), "w") as f:
        f.write("x")
    if os.name == "nt":
        dot = long_path(d) + "\\trailing."
        os.makedirs(dot + "\\in")
        with open(dot + "\\in\\f", "w") as f:
            f.write("x")
    got, _out = _call(remover, patch, d)
    gone = not os.path.lexists(long_path(d))
    if not gone:
        remove_tree(d)
    return got is True and gone, "returned %r, the tree %s" % (got, "gone" if gone else "still there")


def _case_held(base, remover, patch):
    """A tree with a file it cannot remove (held open on Windows, its folder read-only elsewhere):
    False, the tree still there, and the folder named in what it printed."""
    d = os.path.join(base, "held")
    sub = os.path.join(d, "sub")
    os.makedirs(sub)
    f = os.path.join(sub, "f")
    with open(f, "w") as h:
        h.write("x")
    if os.name == "nt":
        held = open(f)
        release = held.close
    else:
        os.chmod(sub, 0o500)
        release = lambda: os.chmod(sub, 0o700)  # noqa: E731
    try:
        got, out = _call(remover, patch, d)
    finally:
        release()
    there = os.path.lexists(long_path(d))
    said = ("could not remove %s" % d) in out
    remove_tree(d)
    return (got is False and there and said,
            "returned %r, the tree %s, %s" % (got, "still there" if there else "gone", "printed" if said else "not printed"))


def _case_exit(mode):
    """A child that makes a scratch() folder (a read-only file in it) and exits: the folder gone,
    nothing printed; with mode "held", a file in it still open (or its folder read-only off
    Windows) at exit: the folder still there and printed."""
    p = subprocess.run([sys.executable, "-B", "-c", _CHILD, os.path.dirname(os.path.abspath(__file__)), mode],
                       capture_output=True, text=True)
    lines = p.stdout.splitlines()
    d = lines[0] if lines else ""
    there = bool(d) and os.path.lexists(long_path(d))
    said = bool(d) and ("could not remove %s" % d) in p.stdout
    good = p.returncode == 0 and bool(d) and (there and said if mode == "held" else not there and not said)
    if there:
        if mode == "held" and os.name != "nt":
            os.chmod(os.path.join(d, ".git", "objects"), 0o700)
        remove_tree(d)
    return good, "exit %d, the folder %s, %s%s" % (p.returncode, "still there" if there else "gone",
                                                  "printed" if said else "not printed",
                                                  "" if p.returncode == 0 else ": " + p.stderr[-300:])


def self_test():
    root = scratch("independent-selftest")
    as_root = hasattr(os, "geteuid") and os.geteuid() == 0      # chmod stops nothing for root
    ok = True
    for b in [None] + REMOVER_FAKES:
        if b in WINDOWS_ONLY and os.name != "nt":
            print("--- fake: %s: not applicable off Windows" % b)
            continue
        print("--- %s" % ("fake: " + b if b else "the remover as written (must pass)"))
        base = os.path.join(root, "fake%02d" % (REMOVER_FAKES.index(b) + 1 if b else 0))
        os.makedirs(base)
        remover, patch = _remover(b)
        rows = [("a clone-like tree is removed",) + _case_tree(base, remover, patch),
                ("its scratch() folder is gone after the process exits",)
                + _case_exit("no-atexit" if b == "no removal at exit" else "plain")]
        if as_root:
            print("  (running as root: the cases with a folder it cannot remove are skipped)")
        else:
            rows += [("a folder it cannot remove: False, and printed",) + _case_held(base, remover, patch),
                     ("a scratch() folder it cannot remove at exit is printed",) + _case_exit("held")]
        for label, good, detail in rows:
            print("  %s %s: %s" % ("ok  " if good else "FAIL", label, detail))
        caught = not all(r[1] for r in rows)
        ok &= caught == (b is not None)
        print("%s %s" % ("CAUGHT  " if caught and b else "PASSED  " if not b and not caught else "MISSED  ",
                         b or "the remover as written"))
    gone = remove_tree(root)
    print("  %s the self-test's own folder removed" % ("ok  " if gone else "FAIL"))
    ok &= gone
    print("\nself-test: %s" % ("every broken fake caught, the remover as written passed" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] != ["--self-test"]:
        print("usage: python -B independent.py --self-test")
        sys.exit(2)
    sys.exit(self_test())
