# Spec 12.4 requirement tests on the mock-HAL shim (tester)

The tester's tests of the product on the host, for the spec 12.4 rows that
judge product code (reviewer, 2026-10-20: *a test that judges product code is
not written by the implementor*). They build `main/gen_inhibit.c`,
`gen_inhibit_core.c`, `gi_sched.c` and `can.c` as shipped against the
implementor's fakes in `../gen_inhibit_shim/`, which are **not edited**: the
tester's fault behaviour sits on top, through link-time wraps
(`-Wl,--wrap=twai_...`) and the fakes' own public controls. Expected values come
from the spec and from what the layer injected and observed, never from the
product source.

Gen-inhibit tester, 2026-10-20. First row: **"a bus with no other node,
emulated"**.

## Files

| File | What |
|---|---|
| `spec_layer.h`, `spec_layer.c` | The fault layer. With no other node: every frame never completes (the fake's air time made too long to elapse, so the head reads as transmitting, as a frame retrying unacknowledged does); one error frame per retry period while a frame waits (`bus_error_count`, `TWAI_ALERT_BUS_ERROR` latched); TEC 8 per error to 128 (error passive, `ERR_PASS`), REC 0; on request bus-off. Logs every `twai_receive()` return with its virtual time (the device's check points) and every injected error; counts `twai_initiate_recovery()` calls. **The retry period and alert pattern are a guess** until the bench row measures them; then the layer replays the measured pattern (spec 12.4). |
| `nonode_judge.h`, `nonode_judge.c` | The row's verdicts as pure functions of what a case observed (R1-R7 below, and two checks on the layer). |
| `judge_test.c` | The judge on made-up observations: each verdict shown to pass and to fail. No fakes, no product code. |
| `nonode_test.c` | The cases. One case per process (`nonode_test <case>`; no argument lists them): the firmware's statics outlive `ft_reset()`, and trip 6's error history is one of them. |
| `build.sh` | Builds `nonode_test` (`bash build.sh <out>`). `SPEC_LAYER`, `NN_TEST`, `NN_JUDGE` substitute a source (the mutants). |
| `mutate.py` | Mutants of the judge (caught by `judge_test`), the layer and the cases (caught by a case's RESULT line). A crash, a build failure or no RESULT line is not a catch. |
| `run_spec_shim.py` | The evidence run: the product identity (HEAD, `main/`'s tree against `--base`, nothing modified, each built file's md5), the stimulus decoded with the DBCs, `judge_test`, every case. Writes `identity.json`, `payloads.txt`, `judge_test.txt`, `cases.txt`, `summary.txt` into `--out`. |

## The row, as judged

| | Requirement (spec 12.4 row, 7 trips 2 and 6, 7.1) |
|---|---|
| R1 | no `0x051` from the device after its answer to the last VCM command |
| R2 | trips are evaluated once per received frame or per 200 ms receive timeout: the latch lands at a check point (the tick right after a receive return); a latch at any other time is NOT AS SPECIFIED (reviewer, 2026-10-20) |
| R3 | trip 6 if its threshold (10 error frames within 10 s) is met at a check **strictly earlier** than the one that finds `0x051` stale; otherwise trip 2, 0.5-0.7 s after the last `0x051` (a tie is trip 2; reviewer, 2026-10-20) |
| R4 | a stuck diag frame does not block the worker, the status page or the latch |
| R5 | traffic back without a key-on: still latched, the stuck frame completes, diag pages resume, no `0x051` |
| R6 | a key-on then clears the latch |
| R7 | bus-off: no recovery; off the bus through traffic and a key cycle (latched, frames dropped, no reinstall) until a re-arm, which comes more than 10 s after the last error (spec 7: a re-arm goes live only once the bus has been free of a qualifying burst for 10 s) |
| layer | the device's `err_n` moves with the layer's count (reviewer, 2026-10-20: otherwise every case could fall through to trip 2); no error before a frame waits |

## The cases

| case | injected | the 10th error | expected |
|---|---|---|---|
| `fast` | a retry every 300 us | just after the first frame waits | trip 6 at the first check after it |
| `before` | placed | +300 ms after the last `0x051`, before the +400 ms check | trip 6 at that check |
| `tie` | placed | +550 ms, after the check before the stale one | trip 2 at the stale check (the tie) |
| `slow` | placed | +1.5 s | trip 2 at the stale check |
| `busoff_few` | a retry every 1 ms, bus-off after 5 | none | trip 2; R7 |
| `busoff_many` | a retry every 300 us, bus-off after 12 | before bus-off | trip 6; R7 |
| `answer_stuck` | the nodes go before the answer to the last command completes; a retry every 150 ms | +1.5 s | trip 2 at the stale check; A1-A5 below |
| `layer` | the layer's own mechanics on a live device | | |

**The answer-stuck case** (reviewer, 2026-10-20; spec 5.2 items 5 and 6): the
device's answer to the last VCM command is the frame stuck in the controller
when the latch lands, so the trip must withdraw it as a stand-down. How the
controller's abort removes a frame that is retrying is the layer's model
(`spec_layer.h`). Judged, besides R1-R6:

| | |
|---|---|
| A1 | the answer never completes on the wire, traffic back included, and no `0x051` is on the wire after traffic returns |
| A2 | counted as `standdown` +1, `skipped` +0 |
| A3 | `queued + carried_in = sent + dropped + withdrawn + depth + held`, every class, on every page read |
| A4 | `withdrawn + dropped = skipped + standdown` (inhibit) on every read with no inhibit frame held (not mid-withdrawal, which 5.2 item 6 excludes) |
| A5 | the withdrawal ends the wait: a diag page is handed to the driver after the latch, becomes the stuck frame, and completes when traffic returns |

A case whose check points or injected errors did not land where it meant
(a check within 20 ms of the 0.5 s freshness edge, a frame received in the
silence, the 10th error outside the case's region) is VOID, never a pass.

## Running

From Windows (WSL's gcc):

```
py -3.14 -B test/gen_inhibit_spec_shim/run_spec_shim.py --out <run folder> --dbc-dir <reverse-it>/projects/vtrux
py -3.14 -B test/gen_inhibit_spec_shim/mutate.py
```

Run files go to the gen-inhibit harness folder in reverse-it
(`projects/vtrux/notes/artifacts/gen-inhibit/runs/`).
