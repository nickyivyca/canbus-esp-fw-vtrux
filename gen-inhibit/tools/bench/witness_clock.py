"""Which clock the Kvaser witness's times are on, and the guard that keeps a check
from judging on receipt time.

Gen-inhibit tester, 2026-11-11, the gen-inhibit reviewer's A2 ruling (b) of that day:
spec 12 requires the timing witness to timestamp at the wire, in hardware. The Kvaser
witnesses in `interlock_bench.py`, `x2_jam_cases.py` and `x2_jammer.py` keep each
frame's hardware stamp and its host receipt time. They use the hardware stamp when
its median lag behind receipt is under `MAX_LAG_S`, so that it is on the epoch clock
the page samples and the posts use. Otherwise they fell back to receipt time, and
the checks went on to judge on it. Now a check is never judged on receipt time:
it is VOID, with the clock note.

  hw_note(lag)        the note for hardware stamps, with the median lag
  RECEIPT             the note for receipt time (the stamps are not on the epoch clock)
  NONE                the note when the witness has no time to place (nothing heard)
  judgeable(note)     True for hw_note's notes and NONE; False for anything else
  require(note)       raises ReceiptClock(note) unless judgeable(note)
  void_verdict(note)  the verdict string a check gets instead of a judgement
  void(rec, note)     puts that verdict, and the note, in a record

The data on either clock is still recorded (the witness file, the counts), labelled
by its note, so the run can be read again. Only the judgement is withheld.
"""

MAX_LAG_S = 2.0
HW = "kvaser_hw"
RECEIPT = "receipt (hw not epoch)"
NONE = "none"
WHY = ("spec 12: the timing witness timestamps at the wire, in hardware; the Kvaser's "
       "hardware stamps were not on the epoch clock, so its times are host receipt times "
       "and nothing is judged on them (gen-inhibit reviewer, A2 (b), 2026-11-11)")


class ReceiptClock(Exception):
    """A check reached a judgement with the witness on receipt time."""

    def __init__(self, note):
        super().__init__("witness clock is %r: not judged on receipt time" % (note,))
        self.note = note


def hw_note(lag, digits=4):
    return "%s (median receipt lag %.*f s)" % (HW, digits, lag)


def judgeable(note):
    return isinstance(note, str) and (note == NONE or note.startswith(HW + " "))


def require(note):
    if not judgeable(note):
        raise ReceiptClock(note)


def void_verdict(note):
    return "VOID: witness clock %r, not judged (%s)" % (note, WHY)


def void(rec, note):
    rec["verdict"] = void_verdict(note)
    rec["why"] = "witness on receipt time: %s" % (note,)
    rec["witness_clock_void"] = note
    return rec
