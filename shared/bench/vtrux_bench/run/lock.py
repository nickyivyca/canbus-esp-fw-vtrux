"""The bench lock, as kept in bench-holder.md (reverse-it firmware/).

The file is the lock (bench-holder rule 1). Under rule 2 (user,
2026-10-17) a chained run holds the lock from start to finish: the tester
takes it, and messages the other tester, before starting the run, and
releases it after. The runner never edits the file. It only reads the
"**Holder:**" line of the "## Lock" section, to refuse to run any group
that needs the bench unless its suite's role holds the lock.
"""


class LockError(Exception):
    pass


def holder(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().replace("\r\n", "\n").split("\n")
    try:
        start = lines.index("## Lock")
    except ValueError:
        raise LockError("no '## Lock' section in %s" % path)
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if ln.startswith("**Holder:**"):
            return ln[len("**Holder:**"):].strip()
    raise LockError("no Holder line in the Lock section of %s" % path)


def is_free(h):
    return h.startswith("--")


def held_by(h, role):
    return h.startswith(role)
