"""The pass/fail rules for wican_diag_check.py, in a module so they can be run
without a bus.

WHY THIS IS A SEPARATE FILE. The rules used to be a dict literal in the middle
of wican_diag_check.py, after the adapters are open and the device is armed.
That made them unreachable without hardware, and on 2026-10-04 three rows I had
just added to it would have raised TypeError on the first hardware run, ending
it in a traceback instead of a verdict (caught by the reviewing session before
it ever ran). wican_diag_check.py is a flat script whose top level opens the
bus, so it cannot be imported to test; extracting the rules is what makes them
testable, and importing them is what stops the test from testing a copy.

THE BUG THAT PROMPTED IT, because it is a trap in cantools rather than a typo.
`db.decode_message()` defaults to `decode_choices=True`, so any signal with a
`VAL_` table comes back as a `NamedSignalValue`, not an int -- and `int()` on
one raises:

    TypeError: int() argument must be a string, a bytes-like object or a
               real number, not 'NamedSignalValue'

In this DBC that is five signals: `diag_mode`, `diag_abort_reason`,
`diag_arm_block`, `diag_disable_reason`, `diag_shift_pos`. The pre-existing
rows happened to avoid it -- `diag_mode` was compared with `str()` -- so the
file gave no hint that `int()` was unsafe on a decoded signal, and the two new
choice signals were exactly the ones that break. `raw()` below handles both
shapes rather than relying on which signals have a `VAL_` today, since adding
one to `diag_flags` tomorrow would otherwise break a row that looks unrelated.

MY ACTUAL MISTAKE was narrower and worth naming: I verified the five pages
decoded and the file compiled, and called that "verified offline". Neither of
those evaluates an assertion. A decode check proves the INPUTS exist and a
compile proves the SYNTAX parses, and between them they feel like coverage
while leaving every expression in the dict unrun.
"""


def raw(sig):
    """Numeric value of a decoded signal, whether or not it has a VAL_ table.

    cantools returns NamedSignalValue for a signal with choices and a plain
    int/float otherwise, and which signals have choices is a property of the
    DBC rather than of this check. getattr covers both without asking.
    """
    return int(getattr(sig, "value", sig))


def build_checks(st, bd, s2, sk, dbc_schema):
    """The rule table. `st`/`bd`/`s2`/`sk` are decoded 0x7F1/0x7F3/0x7F8/0x7F9.

    Returns an ordered dict of label -> bool. Every value must be a plain bool
    so a row cannot pass by being a truthy object.
    """
    flags = raw(st["diag_flags"])
    abort = raw(s2["diag_abort_reason"])

    return {
        # Pinned to the schema this DBC describes, not to a literal. These
        # read ==1 until 2026-09-22 and had been stale since schema 2 landed
        # on 2026-09-19 -- the script would have failed against the very build
        # the hardware suite passed on. Compare against the DBC's own VERSION
        # string so the two cannot drift apart again silently.
        "schema_ver matches the DBC": raw(st["diag_schema_ver"]) == dbc_schema,
        "fw_ver >= 3 (spec 7.1)": raw(st["diag_fw_ver"]) >= 3,
        # Left as a label comparison rather than converted to raw()==3: the
        # label is what a reader of the output wants to see, and diag_mode's
        # VAL_ table is the thing being relied on either way.
        "mode==INHIBIT": str(st["diag_mode"]) == "INHIBIT",
        "git_hash nonzero": raw(bd["diag_git_hash"]) != 0,
        "bus_on flag set": (flags & 0x02) != 0,
        # Spec 7.1 bit6. The PCAN stimulus sends 0x592 key-on, so this must
        # read set -- and if it does not, nothing this device does with 0x051
        # on a real bus can be trusted, because transmission is gated on
        # exactly this bit.
        "key_on flag set (bit6)": (flags & 0x40) != 0,
        # Spec 7.1 bit7. Nothing in this run trips a section 7 abort, so it
        # must be clear; a set bit means the device is latched and standing
        # down.
        "abort_latched clear (bit7)": (flags & 0x80) == 0,

        # --- 0x7F8 Status2, added 2026-10-04 -------------------------------
        #
        # Nothing in this run trips a section 7 abort, so the reason on the
        # wire must be 0 "none".
        "0x7F8 abort_reason none": abort == 0,

        # arm_gate_ok() sets arm_block = GI_BLOCK_NONE on its success path
        # (gen_inhibit_core.c, last two lines of the function), so a device
        # that is live reports 0 here. Checked rather than assumed, because
        # the failure paths each set their own code and only the success path
        # clears it.
        "0x7F8 arm_block none": raw(s2["diag_arm_block"]) == 0,

        # CROSS-PAGE INVARIANT, and the reason it is worth asserting: bit7
        # comes from st->abort_latched and this byte from st->abort_reason --
        # two separate fields, packed into two different frames. They are set
        # together (gen_inhibit_core.c:578-580) and cleared together (:382/391
        # on arm, :1554-1555 on key-on); the one site that clears the reason
        # alone (:1362, the gate going live) sits in the else branch of the
        # latched check at :1331, so it cannot run while the latch is set. So
        # they cannot legitimately disagree, and a disagreement means one of
        # the two pages is packed wrong -- which no single-page check can see.
        #
        # Safe against the two frames being sampled at different moments only
        # because BOTH are expected clear here. If this harness ever drives a
        # real abort, this row has to become a same-frame comparison.
        "0x7F8 abort_reason agrees with bit7":
            (abort != 0) == ((flags & 0x80) != 0),

        # --- 0x7F9 Skips ---------------------------------------------------
        #
        # THERE IS DELIBERATELY NO ROW HERE, and the first draft of this
        # change had one: "skip counters in range", 0 <= n < 2**32. That
        # cannot fail -- a 32-bit unsigned decode is in that range by
        # construction -- so it would have added a green row that tested
        # nothing, which is worse than no row because it makes the page look
        # covered. A check whose failure mode is indistinguishable from
        # success is not a check (AGENTS.md).
        #
        # What covers 0x7F9 is in wican_diag_check.py: it is in DIAG_IDS, so
        # its absence is a MISSING line and a FAIL, and db.decode_message()
        # raises on a malformed frame. Its counters are reported there rather
        # than asserted, because that harness paces 0x051 as fast as its loop
        # runs rather than to a measured truck rate -- a skip would be neither
        # a pass nor a failure, and a zero would not be evidence that skips do
        # not happen. wican_sched_bench.py's arms answer that.
    }
