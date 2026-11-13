"""Mutants of slcan_spi's protocol layer and jam channel: the can-fail of
its unit tests.

NOT FOR VEHICLE (x2_test_images SPEC T14).

Each mutant breaks one behaviour of ../main/slcan_proto.c or
../main/jam_frame.c, by a text substitution that must match exactly once.
The unit tests (test_slcan_proto.c, test_jam_frame.c) are built against the
mutated copy and must FAIL on every mutant; the unmutated files must PASS
first. Copies and builds go in
<worktree>/scratch/slcan-spi-mutants/<pid>/, removed at the end, pass or
fail (AGENTS.md "Files and folders").

    python -B test/mutate.py          from the slcan_spi folder, or anywhere
Exit 0: the original passes and every mutant fails. Exit 1 otherwise.
"""

import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRCS = {
    "slcan_proto.c": ("SRC", os.path.join(HERE, "..", "main", "slcan_proto.c")),
    "jam_frame.c": ("JSRC", os.path.join(HERE, "..", "main", "jam_frame.c")),
}

# (name, the behaviour it breaks, old text, new text): slcan_proto.c
PROTO_MUTANTS = [
    ("send-while-closed", "T8a: a frame is sent before an open",
     "    if (s->mode == SLCAN_CLOSED) {\n        put(s, BEL);\n        return;\n    }\n    if (s->mode != SLCAN_OPEN_NORMAL",
     "    if (0) {\n        put(s, BEL);\n        return;\n    }\n    if (s->mode == SLCAN_OPEN_LISTEN"),
    ("listen-sends", "T12: listen-only transmits",
     "if (s->mode != SLCAN_OPEN_NORMAL || s->ops->send",
     "if (s->ops->send"),
    ("open-without-rate", "T12: O or L opens without S6",
     "if (n != 1 || s->mode != SLCAN_CLOSED || !s->rate_ok) {",
     "if (n != 1 || s->mode != SLCAN_CLOSED) {"),
    ("refused-rate-kept", "T12: a refused Sn leaves an earlier S6 in force",
     "            s->rate_ok = 0;\n            put(s, BEL);",
     "            put(s, BEL);"),
    ("other-rate-accepted", "T12: a rate other than S6 is accepted",
     "} else if (n == 2 && l[1] == '6') {",
     "} else if (n == 2) {"),
    ("close-ignored", "T8a: C does not close",
     "        s->ops->close(s->ops->ctx);\n        s->mode = SLCAN_CLOSED;",
     "        s->mode = SLCAN_CLOSED;"),
    ("drop-unreported", "T12: a dropped frame is not reported to the host",
     "        s->ops->write(s->ops->ctx, e, n);\n        return;",
     "        return;"),
    ("drop-uncounted", "T12: a dropped frame is not counted in the F flags",
     "        s->ops->dropped(s->ops->ctx);\n",
     ""),
    ("error-line-short", "T12: an error line the kernel drops unread (4 characters or fewer)",
     "strcpy(out, \"e3OOO\\r\");\n    return 6;",
     "strcpy(out, \"e1O\\r\");\n    return 4;"),
    ("state-counters-swapped", "T12: the state line's counters in the wrong order",
     "\"s%c%03u%03u\\r\", state, rec, tec",
     "\"s%c%03u%03u\\r\", state, tec, rec"),
    ("id-unchecked", "frames with an identifier out of range are sent",
     "if (id > (f->ext ? 0x1FFFFFFFu : 0x7FFu)) return -1;",
     ""),
    ("dlc-unchecked", "a DLC above 8 is accepted",
     "if (d < '0' || d > '8') return -1;",
     "if (d < '0' || d > '9') return -1;"),
    ("identity-missing", "T10, T12: V/N do not give the identity",
     "        put(s, s->ops->identity);\n",
     ""),
    ("flags-not-read", "the F reply is not the flags",
     "snprintf(out, sizeof out, \"F%02X\\r\", s->ops->flags(s->ops->ctx));",
     "snprintf(out, sizeof out, \"F%02X\\r\", 0);"),
    ("local-only-when-open", "T13: a jam frame is refused while closed",
     "    if (s->ops->local && s->ops->local(s->ops->ctx, &f)) {\n        put(s, f.ext ? \"Z\" CR : \"z\" CR);\n        return;\n    }\n    if (s->mode == SLCAN_CLOSED) {\n        put(s, BEL);\n        return;\n    }",
     "    if (s->mode == SLCAN_CLOSED) {\n        put(s, BEL);\n        return;\n    }\n    if (s->ops->local && s->ops->local(s->ops->ctx, &f)) {\n        put(s, f.ext ? \"Z\" CR : \"z\" CR);\n        return;\n    }"),
    ("local-sent-too", "T13: a jam frame is also put on CAN2",
     "    if (s->ops->local && s->ops->local(s->ops->ctx, &f)) {\n        put(s, f.ext ? \"Z\" CR : \"z\" CR);\n        return;\n    }",
     "    if (s->ops->local) s->ops->local(s->ops->ctx, &f);"),
]

# jam_frame.c
JAM_MUTANTS = [
    ("sig0-unchecked", "T13: a command without the signature's first byte is acted on",
     "if (d[0] != JAM_SIG0 || d[1] != JAM_SIG1) return JAM_FRAME_BAD;",
     "if (d[1] != JAM_SIG1) return JAM_FRAME_BAD;"),
    ("sig1-unchecked", "T13: a command without the signature's second byte is acted on",
     "if (d[0] != JAM_SIG0 || d[1] != JAM_SIG1) return JAM_FRAME_BAD;",
     "if (d[0] != JAM_SIG0) return JAM_FRAME_BAD;"),
    ("jam-dlc-unchecked", "a short command frame is decoded",
     "if (f->id != JAM_CMD_ID || f->rtr || f->dlc != 8) return JAM_FRAME_BAD;",
     "if (f->id != JAM_CMD_ID || f->rtr) return JAM_FRAME_BAD;"),
    ("rtr-unchecked", "a remote frame is decoded as a command",
     "if (f->id != JAM_CMD_ID || f->rtr || f->dlc != 8) return JAM_FRAME_BAD;",
     "if (f->id != JAM_CMD_ID || f->dlc != 8) return JAM_FRAME_BAD;"),
    ("reply-id-is-command", "T13: a host frame on the reply ID is acted on as a command",
     "if (f->id != JAM_CMD_ID || f->rtr || f->dlc != 8) return JAM_FRAME_BAD;",
     "if (f->rtr || f->dlc != 8) return JAM_FRAME_BAD;"),
    ("reply-id-not-reserved", "T13: a host frame on the reply ID goes to CAN2",
     "return f->ext && (f->id == JAM_CMD_ID || f->id == JAM_REPLY_ID);",
     "return f->ext && f->id == JAM_CMD_ID;"),
    ("std-id-reserved", "an 11-bit frame is taken for the jam channel",
     "return f->ext && (f->id == JAM_CMD_ID || f->id == JAM_REPLY_ID);",
     "return (f->id == JAM_CMD_ID || f->id == JAM_REPLY_ID || f->id == (JAM_CMD_ID & 0x7FF));"),
    ("little-endian", "the arguments read little-endian",
     "return ((unsigned)p[0] << 8) | p[1];",
     "return ((unsigned)p[1] << 8) | p[0];"),
    ("stop-trailing-unchecked", "a STOP or STATUS with stray bytes is acted on",
     "            if (d[i]) return JAM_FRAME_BAD;\n",
     ""),
    ("reply-no-signature", "T13: reply frames without the signature",
     "        f->data[0] = JAM_SIG0;\n        f->data[1] = JAM_SIG1;\n",
     ""),
    ("reply-index-fixed", "reply frames all carry index 0",
     "        f->data[2] = (uint8_t)n;\n",
     "        f->data[2] = 0;\n"),
    ("reply-last-everywhere", "every reply frame marked last",
     "if (pos >= len || n == max) f->data[2] |= JAM_REPLY_LAST;",
     "f->data[2] |= JAM_REPLY_LAST;"),
    ("reply-overruns-max", "a long reply writes past the frames it was given",
     "    } while (pos < len && n < max);",
     "    } while (pos < len);"),
]

MUTANTS = ([("slcan_proto.c",) + m for m in PROTO_MUTANTS]
           + [("jam_frame.c",) + m for m in JAM_MUTANTS])


def worktree_root():
    out = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def ignored(root, path):
    r = subprocess.run(["git", "-C", root, "check-ignore", "-q", path])
    return r.returncode == 0


def _onerror(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError as e:
        print("could not remove %s: %s" % (path, e))


def remove(path):
    if os.path.exists(path):
        shutil.rmtree(path, onerror=_onerror)
    if os.path.exists(path):
        print("could not remove %s" % path)


def run_tests(out, var=None, src=None):
    args = ["make", "-s", "-C", HERE, "OUT=" + out]
    if var:
        args.append(var + "=" + src)
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    root = worktree_root()
    scratch = os.path.join(root, "scratch", "slcan-spi-mutants", str(os.getpid()))
    if not ignored(root, scratch):
        print("REFUSED: %s is not ignored by git (scratch/ must be)" % scratch)
        return 1
    originals = {}
    for fname, (_, path) in SRCS.items():
        with open(path, encoding="utf-8", newline="") as f:
            originals[fname] = f.read()
    bad = 0
    try:
        os.makedirs(scratch)
        rc, text = run_tests(os.path.join(scratch, "original"))
        if rc != 0:
            print("ORIGINAL FAILS: the unit tests must pass on the unmutated file")
            print(text)
            return 1
        print("original                  PASS (as it must)")
        for fname, name, what, old, new in MUTANTS:
            original = originals[fname]
            count = original.count(old)
            if count != 1:
                print("%-25s BROKEN MUTANT: its text matches %d times, not once" % (name, count))
                bad += 1
                continue
            d = os.path.join(scratch, name)
            os.makedirs(d)
            src = os.path.join(d, fname)
            with open(src, "w", encoding="utf-8", newline="") as f:
                f.write(original.replace(old, new))
            rc, text = run_tests(os.path.join(d, "build"), SRCS[fname][0], src)
            if rc == 0:
                print("%-25s SURVIVED: %s" % (name, what))
                bad += 1
            else:
                print("%-25s killed    (%s)" % (name, what))
    finally:
        remove(scratch)
        try:
            os.rmdir(os.path.dirname(scratch))
        except OSError:
            pass    # another run's folder is still there, or it is gone
    print()
    if bad:
        print("%d of %d mutants survived or are broken" % (bad, len(MUTANTS)))
        return 1
    print("all %d mutants killed" % len(MUTANTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
