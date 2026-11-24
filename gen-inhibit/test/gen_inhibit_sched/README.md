# `test/gen_inhibit_sched/` — the transmit scheduler's cases

Spec 5.2 item 10's catalogue, **emulation half**. A case that exists only in
emulation does not count toward acceptance, so every pass criterion here is one a
bench witness can also read: which frames reached the wire, in what order, and how
late.

Needs only gcc, make and python3 (user, 2026-10-24):
- **Self-checks** run in WSL on `NICKY-XPS` (Ubuntu-20.04: gcc 9.4.0, GNU Make
  4.2.1, python3 3.10.7), from the worktree's `/mnt/c/...` path, compiling and
  running in one `wsl -e` call (`tools-reference.md`, "WSL", its two traps).
- **Evidence runs** stay on `madhouse-debian` (gcc 14.2.0, python3 3.13.5), in
  the tester's part H.

```sh
make && ./sched_test      # the cases
make asan && ./sched_test # the same, with ASan/UBSan
python3 mutate.py         # do the cases actually notice?
```

| File | What |
|---|---|
| `fake_buf.c/.h` | **The controller's single TX buffer and its abort, modelled from the measurement** — every constant traceable to `artifacts/gen-inhibit/runs/txabort_full.json` (firmware `f6eb9ec`, 254 trials). Air time 249 us, abort-to-free 7 us, the three `TS=1` outcomes, and the property that matters most: `fb_wire_count()` and `fb_outstanding()` are **different questions**, because the hardware reports an aborted frame exactly as a real transmission. Contention figures are labelled STRESS at each call site (spec 12.2: bench replays are burstier than the truck). |
| `sched_test.c` | Ten cases. Two exist because a review found the defect they now pin (`D1` the deadline purge, `D2` the sliding skip window); one exists because the mutation harness reported its guard uncovered (`D3`). |
| `mutate.py` | Removes one guard at a time from a **copy** and checks the cases notice. A build failure is reported as INCONCLUSIVE, never as caught, and a mutation whose anchor does not match exactly once is a SKIP; either fails the run. |
| `.gitignore` | `sched_test`, the one file `make` and `make asan` write here. `mutate.py` builds its mutants under `scratch/`. |

## Three things this suite found that reading the code did not

**The model was unfaithful in the direction that hid a guard.** `FB_ABORT_FREE_US`
was measured at 6–21 us, written down, and then thrown away with a `(void)` cast,
so an abort freed the buffer inside the same tick that issued it. The scheduler's
`!aborting` guard — which stops an aborted frame being credited as sent — then had
no reachable failure, and `mutate.py` showed the guard could be deleted with the
whole suite still green. This is the trap the shim's `shim_harness.h` (`fake_twai.h`
until port step 1b) opens with: a model that
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

**It happened again, so a SKIP now fails the run** (2026-10-24). ae7b3cb added
gs_tick's `!s->aborting` guard to `gs_command_received` as well, so the
guard's anchor matched twice and that mutation was skipped from then on, with
neither site mutated and the run exiting 0. It is now two mutations, one per
site, each anchored with the line above it, and each names its own case.
`mutate.py` lists every skipped mutation and exits 1.

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

### 2026-10-16: stand-downs are not skips

Spec 5.2 item 5 as changed 2026-10-16: an answer withdrawn because the inhibit
stopped being live is a **stand-down**, never a skip, and the inhibit-class
identity is `withdrawn + dropped == skipped + standdown`. Three cases, each
locked to a command (its deadline, then its answer queued, then the stand-down):

* `case_standdown_queued_answer` -- the answer waits behind a page on the wire
  (`fb_stick_transmitting`);
* `case_standdown_held_answer` -- the answer is in the controller, and the VCM's
  next command arrives before the abort loop has run;
* `case_standdown_after_deadline_books_once` -- a stand-down during a deadline's
  abort books nothing more.

`standdown` is read from `gs_json()`, so the file still builds against `c65c45e`
and fails there on the assertions (19 of them) instead of at compile time.
`case_identity_withdraw_api_balances` now expects a stand-down where it expected
a skip.

**A re-arm while a withdrawal is still running** (spec 5.2 item 6, 2026-10-16):
`case_rearm_during_deadline_withdrawal` and
`case_rearm_during_standdown_withdrawal` call `gs_rearm()` before the abort loop
has removed an inhibit the old arm gave up. The new arm must count it as a
stand-down beside its `carried_in`, so its own identity holds once the loop books
`withdrawn`; on `c6fdab5` it read one over. `case_rearm_with_live_answer_is_no_standdown`
is the other side: a held answer nobody gave up leaves as `sent`.

**A completion seen only after the VCM's next `0x051` was dequeued is a late
skip** (spec 7 trip 7; user 2026-11-23, after B4's l3s at
`gen-inhibit-bench_20261123T171226_4b15` counted one late frame of three on
time). The worker reports each dequeue with `gs_rx_dequeued()`, before the
dequeue's `gs_tick()`. If the dequeued frame is a command that was already
waiting at its receive, neither that tick nor `gs_command_received()` credits a
held inhibit's departure, and the deadline books it late through the
buffer-`EMPTY` branch. Until the path (i) change below this was
`gs_hold_credit()`.

* `case_held_credit_is_late` -- the inhibit completes while nothing ticks, the
  hold is set, and the deadline must book a late skip, not `ontime` and not
  `sent`; the identity balances once the loop closes, and the next answer is on
  time. Its control half runs the same sequence without the hold (the receive
  blocked) and expects `ontime`.
* `case_held_credit_spares_telemetry` -- the hold is the inhibit's only: a page
  that finished meanwhile is credited as sent.

`mutate.py` breaks each site that honours the hold (`gs_tick`'s credit, the
deadline's credit, the deadline ending the hold); all three are caught. The two
`!aborting` mutants' anchors moved with it.

**The decision with frames waiting** (spec 7 trip 7, user 2026-11-23, path
(i)). When the scheduler sees a timed frame complete, it reads the receive
queue's count through the HAL (`gs_hal_t.rx_waiting`), after the completion
read. 0 is on time. With n > 0 the verdict waits until the worker has removed
n frames: the VCM's next `0x051` among them makes it a late skip, and otherwise
it is on time, with frames waiting (`ontime_unverified`). `gs_tick()` and
`gs_command_received()` take no count since. `fake_buf` models the count
(`fb_set_rx_waiting()`), and a frame arriving just after the completion read
(`fb_arrive_on_free()`). `seen_with_waiting()` and `dequeue()` drive the cases:

* `case_ontime_unverified` -- three frames counted, none a command: on time,
  with frames waiting, decided after the third, and the hold ends at the
  completion, not at the last dequeue.
* `case_counted_command_is_late` -- the command is the second counted frame: a
  late skip.
* `case_empty_queue_is_on_time` -- nothing counted: `ontime` at once.
* `case_arrival_between_reads_is_counted` -- the frame that arrives between the
  completion read and the count is counted, and it is the command.
* `case_command_after_the_count_is_on_time` -- the dequeue limit: a command
  behind the counted frames does not make it late.
* `case_deadline_ends_the_wait_on_time` -- the completion seen after the
  command was removed (the receive blocked): the frames behind it came after
  it, so the deadline credits it on time, with frames waiting.
* `case_standdown_ends_the_wait` -- a stand-down while the verdict waits is a
  stand-down (spec 5.2 item 5), never a skip or on time.

**RESPOND's probe takes the same path** (spec 11, user 2026-11-23): the wait
keys on the arm's timed class. A probe is never withdrawn and is never a skip;
one found late is credited as sent once its deadline has passed, so it is in
neither on-time count. `case_probe_counted_command_is_late` covers the command
among the counted frames and path (ii), and
`case_probe_not_among_them_is_on_time` the other two outcomes.

`mutate.py` takes a list of (old, new) pairs for a mutant that needs more than
one edit. Ten mutants break the new rule, each caught by its case: the count
read before the completion read (the reviewer's count order), the verdict
waiting for one frame more (the dequeue limit), never waiting, a counted
command not held, the deadline not crediting a verdict that waits, a
stand-down not ending the wait, the hold running to the last dequeue, and three
that key the wait on the inhibit class instead of the timed class.
