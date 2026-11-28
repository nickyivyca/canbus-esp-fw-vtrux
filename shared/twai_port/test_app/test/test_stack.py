#!/usr/bin/env python3
"""Hold every stack frame of twai_port_unacked.c to FRAME_MAX bytes on the host.

The implementor's (SPEC.md T11, T13). m1-m3's answer runs on the caller's
task: on gen-inhibit that is the httpd task, with a 5120-byte stack, of which
the URI handler and twai_port_txabort_run() already hold about 2.6 KB. At
shared/main 3bb6c945 txp_write_json() kept its buffers on the stack, 3520
bytes on the C3, and every m1-m3 call overflowed the stack and panicked at
its answer (gen-inhibit VERIFICATION.md 12.4, 2026-11-28). The answer's
buffers are now static. This check fails if any function grows a frame over
FRAME_MAX again, or one with no bound.

    python3 test_stack.py <file.su>

The .su file is gcc's -fstack-usage output for twai_port_unacked.c, which the
Makefile writes to OUT (stack_unacked.su). Host frames differ from the C3's,
but a buffer of a kilobyte shows on both: FRAME_MAX is a budget for small
frames, not a measurement of the target. On x86-64 the answer's
45-argument snprintf() alone spills about 590 bytes of arguments, hence 768;
any one of the answer's buffers back on the stack goes over it.
"""
import sys

FRAME_MAX = 768


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.split("\n")[0] + "\nusage: test_stack.py <file.su>")
    bad = 0
    rows = 0
    with open(sys.argv[1], encoding="utf-8") as fh:
        for line in fh:
            loc, n, kind = line.rstrip("\n").split("\t")
            rows += 1
            # "dynamic,bounded" (a variadic call's spill, say) is fine within
            # the limit; plain "dynamic" has no bound at all.
            if int(n) > FRAME_MAX or kind not in ("static", "dynamic,bounded"):
                print("  FAIL %s: %s bytes, %s (limit %d, bounded)" % (loc, n, kind, FRAME_MAX))
                bad += 1
    if rows == 0:
        print("  FAIL no functions in %s" % sys.argv[1])
        bad += 1
    print("stack: %d function(s), %s: %d failure(s)" % (rows, "PASS" if bad == 0 else "FAIL", bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
