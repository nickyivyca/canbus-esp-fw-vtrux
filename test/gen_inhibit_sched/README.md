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
