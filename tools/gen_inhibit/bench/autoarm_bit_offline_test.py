"""Offline test of diag_build_page_check.py's --expect-autoarm discriminator.

Runs with no hardware. It IMPORTS `autoarm_verdict` from the harness and calls it, so
the code under test is the code the bench runs.

THE FIRST VERSION OF THIS FILE DID NOT, and that is worth keeping on the record because
it is the defect this whole folder is written against. It defined its own `verdict()`,
"lifted verbatim in shape" from the harness, and never imported the harness at all.
Deleting the population gate from `diag_build_page_check.py`, or flipping its mismatch
comparison, left all 7 cases passing -- so it proved a copy correct and said nothing
about the bench path. Only the cantools encode/decode half was ever shared. Caught by
the reviewing session, 2026-10-04, reading the two files side by side.

It is the sharper instance of the shape, too: what the test failed to cover was itself a
gate written against checks that cannot fail.

WHAT THE SEVEN CASES COVER. Both images against both expectations; bit3 varying across
pages, which cannot happen in one image because it is compiled in; and **a silent device
against both expectations**. That last pair is the point. `--expect-autoarm 0` is the
SHIPPING expectation, so bit3 clear is the pass -- and a device emitting nothing at all
shows no bits set, satisfying it while proving nothing. Without the gate, "this is the
shipping image" and "there is no device here" are the same observation.

Assertions are on the harness's REASON CODE, not just its boolean. A flipped comparison
then shows up as the wrong reason rather than merely the wrong verdict.

CONFIRM IT BITES. This test is only worth having if it fails when the harness breaks.
Verified 2026-10-04 by mutation: removing the `if not flags:` gate from
`autoarm_verdict` turns the two SILENT rows red (reason "ok"/"mismatch" instead of
"no_pages"); inverting the mismatch comparison turns four rows red. Re-do that after any
edit to `autoarm_verdict` rather than trusting a green run.

    py -3.14 autoarm_bit_offline_test.py
"""

import os
import sys

import cantools
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = gi_paths.reverse_it_dir()
DBC = gi_paths.diag_dbc()

sys.path.insert(0, HERE)
#
# Importing the harness pulls in python-can, which is why this is not a bare import
# at the top: say what is wrong rather than raising ImportError from a module the
# reader did not know was involved. Nothing is opened at import time -- the harness
# guards everything behind `if __name__ == "__main__"`.
#
try:
    from diag_build_page_check import (AUTOARM_BIT, STATUS_ID, autoarm_verdict)
except ImportError as exc:
    raise SystemExit(
        "cannot import diag_build_page_check: %r\n"
        "  It needs python-can even though this test touches no hardware.\n"
        "  Install it for this interpreter, or run under py -3.14 on NICKY-XPS." % exc)

db = cantools.database.load_file(DBC)
msg = db.get_message_by_frame_id(STATUS_ID)
print("DBC %s -> %s, signals: %s"
      % (os.path.relpath(DBC, REPO), msg.name,
         ", ".join(s.name for s in msg.signals)))
print("harness: diag_build_page_check.autoarm_verdict, AUTOARM_BIT = 0x%02X"
      % AUTOARM_BIT)


def status_payload(flags):
    """A plausible 0x7F1 with the given diag_flags byte."""
    return msg.encode({
        "diag_schema_ver": 5,
        "diag_fw_ver": 4,
        "diag_mode": 3,
        "diag_flags": flags,
        "diag_disable_reason": 0,
        "diag_shift_pos": 0,
        "diag_soc_x100": 55.0,
    })


#
# bit1|bit2|bit5|bit6 are set in BOTH arms: bus_on, bus_ours, inhibit_live, key_on. The
# point is that bit3 must be read out from among other set bits, not from a byte that is
# only ever 0x00 or 0x08 -- a mask error would survive the simpler fixture.
#
SHIPPING = 0x02 | 0x04 | 0x20 | 0x40
AUTOARM = SHIPPING | AUTOARM_BIT

ship_pages = [msg.decode(status_payload(SHIPPING)) for _ in range(25)]
auto_pages = [msg.decode(status_payload(AUTOARM)) for _ in range(25)]

print("\nshipping flag byte 0x%02X decodes to diag_flags=%s"
      % (SHIPPING, ship_pages[0]["diag_flags"]))
print("auto-arm flag byte 0x%02X decodes to diag_flags=%s"
      % (AUTOARM, auto_pages[0]["diag_flags"]))

CASES = [
    ("shipping image, --expect-autoarm 0", ship_pages, 0, True, "ok"),
    ("shipping image, --expect-autoarm 1", ship_pages, 1, False, "mismatch"),
    ("auto-arm image, --expect-autoarm 1", auto_pages, 1, True, "ok"),
    ("auto-arm image, --expect-autoarm 0", auto_pages, 0, False, "mismatch"),
    ("SILENT device,  --expect-autoarm 0", [], 0, False, "no_pages"),
    ("SILENT device,  --expect-autoarm 1", [], 1, False, "no_pages"),
    ("bit3 varies across pages, expect 0",
     ship_pages[:5] + auto_pages[:5], 0, False, "varied"),
    ("undecodable pages only, expect 0",
     [{"_decode_error": "repr(exc)"}] * 5, 0, False, "no_pages"),
]

print()
bad = 0
for name, pages, expect, want_ok, want_reason in CASES:
    ok, reason, detail = autoarm_verdict(pages, expect)
    good = (ok == want_ok and reason == want_reason)
    if not good:
        bad += 1
    print("  [%s] %-36s -> ok=%-5s reason=%-9s (wanted ok=%s reason=%s)"
          % ("ok" if good else "BAD", name, ok, reason, want_ok, want_reason))

print()
if bad:
    print("%d case(s) wrong -- autoarm_verdict does not behave as this test expects."
          % bad)
    sys.exit(1)
print("all %d cases as intended." % len(CASES))
print("The two SILENT rows and the undecodable row are why this exists: without the")
print("population gate, 'shipping image' and 'no device at all' are one observation.")
