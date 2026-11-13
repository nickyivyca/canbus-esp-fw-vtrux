"""Mutants of slcan_can1's protocol layer and app: the can-fail of its host
tests, the T8a silence check among them.

Each mutant breaks one behaviour of ../main/slcan1_proto.c or
../main/app_main.c by a text substitution that must match exactly once. The
host tests (test_slcan1_proto.c, test_app.c) are built against the mutated
copy and must FAIL on every mutant; the unmutated files must PASS first.
Copies and builds go in <worktree>/scratch/slcan-can1-mutants/<pid>/,
removed at the end, pass or fail (AGENTS.md "Files and folders").

    python3.13 -B test/mutate.py      from the slcan_can1 folder, or anywhere
Exit 0: the originals pass and every mutant fails. Exit 1 otherwise.
"""

import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRCS = {
    "slcan1_proto.c": ("SRC", os.path.join(HERE, "..", "main", "slcan1_proto.c")),
    "app_main.c": ("APP", os.path.join(HERE, "..", "main", "app_main.c")),
}

# (name, the behaviour it breaks, old text, new text): slcan1_proto.c
PROTO_MUTANTS = [
    ("send-while-closed", "T8a: a frame is sent before an open",
     "    if (!s->open || s->listen_only) {\n        err(s);",
     "    if (s->listen_only) {\n        err(s);"),
    ("listen-sends", "T8a: listen-only offers frames to the port",
     "    if (!s->open || s->listen_only) {\n        err(s);",
     "    if (!s->open) {\n        err(s);"),
    ("l-opens-normal", "T8a: L opens the port normal, which ACKs",
     "        s->listen_only = (l[0] == 'L');\n        if (s->ops->open(s->ops->ctx, s->listen_only)) {",
     "        s->listen_only = (l[0] == 'L');\n        if (s->ops->open(s->ops->ctx, 0)) {"),
    ("close-ignored", "T8a: C does not take the port down",
     "            s->ops->close(s->ops->ctx);\n",
     ""),
    ("close-keeps-open", "T8a: C leaves the protocol open, sending",
     "            s->open = 0;\n            s->listen_only = 0;",
     "            s->listen_only = 0;"),
    ("refused-open-opens", "T8a: a refused open counts as open",
     "        if (s->ops->open(s->ops->ctx, s->listen_only)) {\n            s->open = 1;\n            ok(s);\n        } else {\n            err(s);\n        }",
     "        s->ops->open(s->ops->ctx, s->listen_only);\n        s->open = 1;\n        ok(s);"),
    ("reopen-while-open", "O while open opens the port again",
     "        if (s->open) {\n            ok(s);\n            break;\n        }",
     ""),
    ("init-open", "T8a: the protocol starts open",
     "    memset(s, 0, sizeof *s);\n    s->ops = ops;",
     "    memset(s, 0, sizeof *s);\n    s->ops = ops;\n    s->open = 1;"),
    ("tail-runs", "T8a: the tail of an overlong line runs as a command (slcan_main.cpp's loop)",
     "            s->len = 0;\n            s->skip = 1;\n            err(s);",
     "            s->len = 0;\n            err(s);"),
    ("other-rate-accepted", "a rate other than S6 is accepted",
     "if (n >= 2 && l[1] == '6') ok(s); else err(s);",
     "if (n >= 2) ok(s); else err(s);"),
    ("extended-sent", "an extended (T) frame is sent",
     "    case 't':\n        transmit(s, l, n);",
     "    case 't':\n    case 'T':\n        transmit(s, l, n);"),
    ("dlc-unchecked", "a DLC above 8 is accepted",
     "dl < 0 || dl > 8)",
     "dl < 0 || dl > 9)"),
    # No mutant removes the data-length check (n < 5 + len * 2): the line is
    # NUL-terminated at its length, so without the check the first missing
    # data digit reads NUL, which is no hex digit, and the frame is refused
    # the same way. An equivalent mutant, so not a can-fail.
    ("t-replies", "a queued t is answered, which the protocol never does",
     "        s->accepted++;\n",
     "        s->accepted++;\n        ok(s);\n"),
    ("full-uncounted", "a frame the queue had no room for is not counted",
     "        s->full++;\n",
     ""),
    ("version-changed", "V's fixed answer changed",
     "put(s, \"V0101\\r\");",
     "put(s, \"V0100\\r\");"),
    ("serial-changed", "N's fixed answer changed",
     "put(s, \"NF001\\r\");",
     "put(s, \"NF002\\r\");"),
    ("counters-swapped", "the ? line's accepted and queue_full swapped",
     "(unsigned long)s->accepted, (unsigned long)s->full,",
     "(unsigned long)s->full, (unsigned long)s->accepted,"),
    ("lf-ignored", "LF does not end a line",
     "if (c == '\\r' || c == '\\n') {",
     "if (c == '\\r') {"),
    ("unknown-ignored", "an unknown command is ignored, not refused",
     "        /* Everything else, T, r and R included, is refused, not ignored. */\n        err(s);",
     "        /* Everything else, T, r and R included, is refused, not ignored. */"),
]

# app_main.c
APP_MUTANTS = [
    ("boot-brings-up", "T8a: the port comes up at boot",
     "    s_setup = can1_setup();\n",
     "    s_setup = can1_setup();\n    can_port_up(CAN1_PORT);\n"),
    ("boot-keeps-state", "T8a: a reset does not return the protocol to closed",
     "    slcan1_init(&s_slcan, &s_ops);\n",
     "    if (!s_slcan.ops) slcan1_init(&s_slcan, &s_ops);\n"),
    ("mode-not-set", "T8a: L brings the port up in the last mode, not listen-only",
     "    return s_setup &&\n           twai_port_set_mode(listen_only ? CAN_PORT_MODE_LISTEN_ONLY\n                                          : CAN_PORT_MODE_NORMAL) == ESP_OK &&\n           can_port_up(CAN1_PORT) == ESP_OK;",
     "    return s_setup && can_port_up(CAN1_PORT) == ESP_OK;"),
    ("close-no-teardown", "T8a: C does not take the port down",
     "    (void)twai_port_teardown();\n",
     ""),
    ("no-drain", "received frames are not drained",
     "        while (can_port_receive(CAN1_PORT, &m, 0) == ESP_OK) {\n        }\n",
     ""),
    ("service-closed", "the port is serviced while closed",
     "    if (slcan1_is_open(&s_slcan)) {\n        can_port_service(CAN1_PORT);",
     "    can_port_service(CAN1_PORT);\n    if (slcan1_is_open(&s_slcan)) {"),
    ("recovery-never", "bus-off recovery not automatic",
     "    c.recovery = TWAI_PORT_RECOVERY_AUTO;\n",
     ""),
    ("rtr-kept", "remote frames handed up",
     "    c.drop_rtr = true;\n",
     ""),
    ("install-fail-aborts", "an install failure restarts the chip",
     "    c.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;\n",
     ""),
    ("queues-default", "the port's default queue depths",
     "    c.tx_queue_len = CAN1_TX_QUEUE;\n",
     ""),
    ("single-shot", "every send one attempt",
     "    c.single_shot = false;\n",
     "    c.single_shot = true;\n"),
    ("small-usb-buffer", "the USB receive buffer at the driver's default",
     "        .rx_buffer_size = USB_RX_BUFFER,\n",
     "        .rx_buffer_size = 256,\n"),
    ("tx-dropped-partial", "the ? line's tx_dropped leaves out refusals",
     "    out->tx_dropped = st.tx_dropped + st.tx_refused;\n",
     "    out->tx_dropped = st.tx_dropped;\n"),
    ("not-registered", "the port is not registered with can_port",
     "    return can_port_register(CAN1_PORT, &twai_port_ops, NULL) == ESP_OK;\n",
     "    return 1;\n"),
]

MUTANTS = ([("slcan1_proto.c",) + m for m in PROTO_MUTANTS]
           + [("app_main.c",) + m for m in APP_MUTANTS])


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
    scratch = os.path.join(root, "scratch", "slcan-can1-mutants", str(os.getpid()))
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
            print("ORIGINAL FAILS: the host tests must pass on the unmutated files")
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
