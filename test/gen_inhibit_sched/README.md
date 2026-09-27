# `test/gen_inhibit_sched/` — the transmit scheduler's cases

Spec 5.2 item 10's catalogue, **emulation half**. A case that exists only in
emulation does not count toward acceptance, so every pass criterion here is one a
bench witness can also read: which frames reached the wire, in what order, and how
late.

Needs only gcc. `NICKY-XPS` has no native gcc — run on `madhouse-debian`
(gcc 14.2.0, python3 3.13.5), per `tools-reference.md`.

```sh
make && ./sched_test      # the cases
make asan && ./sched_test # the same, with ASan/UBSan
python3 mutate.py         # do the cases actually notice?
```

| File | What |
|---|---|
| `fake_buf.c/.h` | **The controller's single TX buffer and its abort, modelled from the measurement** — every constant traceable to `artifacts/gen-inhibit/runs/txabort_full.json` (firmware `f6eb9ec`, 254 trials). Air time 249 us, abort-to-free 7 us, the three `TS=1` outcomes, and the property that matters most: `fb_wire_count()` and `fb_outstanding()` are **different questions**, because the hardware reports an aborted frame exactly as a real transmission. Contention figures are labelled STRESS at each call site (spec 12.2: bench replays are burstier than the truck). |
| `sched_test.c` | Ten cases. Two exist because a review found the defect they now pin (`D1` the deadline purge, `D2` the sliding skip window); one exists because the mutation harness reported its guard uncovered (`D3`). |
| `mutate.py` | Removes one guard at a time from a **copy** and checks the cases notice. A build failure is reported as INCONCLUSIVE, never as caught. |

## Three things this suite found that reading the code did not

**The model was unfaithful in the direction that hid a guard.** `FB_ABORT_FREE_US`
was measured at 6–21 us, written down, and then thrown away with a `(void)` cast,
so an abort freed the buffer inside the same tick that issued it. The scheduler's
`!aborting` guard — which stops an aborted frame being credited as sent — then had
no reachable failure, and `mutate.py` showed the guard could be deleted with the
whole suite still green. This is the trap `fake_twai.h` opens with: a model that
errs in the same direction as the defect proves nothing.

**The mutation harness had the same defect it was built to find.** Its first run
printed "the mutant does not compile — counts as caught" for all five mutations.
Every one was a broken temp layout (`-I../../main` resolving outside the temp
tree); zero mutations were actually exercised, and the summary was clean. A build
failure is now an error that prints the compiler output.

**Two of the original cases were wrong, and the failures were right.** One asserted
that three skips spanning 1.05 s should trip "3 within 1 s" — they should not, and
loosening the window to make the test pass would have broken the spec. The other
expected the scheduler to issue an abort while the buffer was transmitting and to
recover from the no-effect case; it never issues that command, so the hardware
behaviour is unreachable from the scheduler and what the case should assert is the
avoidance. Chasing that one found a real wart: preemption used to enter the abort
state even when the buffer was transmitting, wait for the frame to complete on the
wire, and then requeue it — **sending every preempted-but-completed telemetry page
twice.**

### Three mutations were skipped for two commits, and the count said 13

`note_skip()` gained a `late` argument when skips were split by kind, and three
anchors still spelled the one-argument call. `mutate.py` reported them as `SKIP`
with the match count, which is the honest behaviour -- and the run was still
being read as "13 mutations caught" when it was **10 caught and 3 skipped**. A
skipped mutation is an unpinned property that looks like a passing one at a
glance, because the summary line underneath says every mutation with a matching
case was caught, and that sentence is true.

Two of the three needed more than a re-point:

* **R5's anchor now matched twice.** `late_on_wire++` is raised for
  `GS_BUF_TRANSMITTING` and again for `GS_BUF_EMPTY` -- both are frames that went
  out late -- so the bare two lines were ambiguous. The anchor carries the `if`
  in, which also makes it say which branch it breaks.
* **D1's anchor was broken by an end-of-line comment**, a `/* never handed over,
  never on the wire */` added next to the call. Re-pointing it at the whole body
  would leave prose in the anchor for the next comment edit to disarm again, so
  it is cut back to the loop head and the mutation kills the loop by its
  condition.

**Read the match counts, not just the verdicts.** `(pattern found 0 times)` and
`(pattern found 2 times)` are the same shape of failure as a filter returning
nothing because its premise is wrong.

### Round 15: D8, and the third case of mine that argued with the spec

`case_rearm_keeps_what_the_hardware_holds` pins `gs_rearm()` itself: a mode
change while the controller still holds a frame must not forget it.

**Its first version failed on correct code**, and the code was right. It held a
telemetry page AWAITING, re-armed, queued an **inhibit**, and asserted that
nothing more had been handed over. But an inhibit behind an AWAITING lower-class
frame is exactly what item 6 preemption is for -- abort the page, hand the
inhibit over -- so two submits is the correct answer and that scenario cannot
ask D8's question at all. The successor has to be the **same class**, where no
preemption is available and the only thing deciding whether the scheduler waits
is whether it still knows the controller is busy.

That is the third time a case here asserted against intended behaviour rather
than finding a defect (the other two are in the section above). All three had the
same tell: the assertion was written from what the case was *about* rather than
from what the spec *says* happens.

**How the defect shows up here is not how it shows up on the device.**
`fb_submit()` refuses while the buffer is occupied -- "the scheduler must never
do this: item 3" -- so `gs_init()` in place of `gs_rearm()` produces a *refusal*
in this model, while on the device `twai_transmit()` would accept the frame into
the driver's FIFO behind the old one. Same fault, two symptoms; E1's case 27
covers the FIFO form, this one covers the refusal form, and both are written down
at the call site so neither reads as the whole story.
