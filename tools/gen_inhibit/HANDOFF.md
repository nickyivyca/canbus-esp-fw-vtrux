# Field handoff — SUPERSEDED 2026-09-06

This was a pre-departure checklist, written before the inhibitor had ever run on
a vehicle. It has served its purpose: the tool went to the truck on 2026-09-06,
transmitted onto a live powertrain bus, and produced its first inhibit result.
The pre-departure section is now history, and the field procedure has been
overtaken by what actually happened.

**Do not follow this file.** Read instead:

| | |
|---|---|
| What happened on the truck, and what is still open | [`../../notes/gen-inhibit-truck-tests.md`](../../notes/gen-inhibit-truck-tests.md) |
| How the tool works, the procedure, reading the verdict | [`README.md`](README.md) — see **On the truck** |

## What it got wrong, kept because it is instructive

- **Every timing number it carried was void on Windows anyway.**
  `time.monotonic()` is `GetTickCount64()` there — 15.625 ms resolution, coarser
  than the entire 10 ms slot being measured. The laptop's first preflight failed
  NO-GO for that reason alone, and the file's advice to "re-measure on the
  machine you are using" would not have caught it: the re-measurement was the
  thing that was broken.

- **It assumed the field procedure would be preflight → short live run → real
  test, at leisure.** In practice the truck was in evap pending, so key cycles
  were expensive, and the generator starts **~6.3 s after the bus comes up** —
  faster than the tool's own start-up sequence. The runner grew
  `--wait-for-bus` so it can be started before the key, and its identify and
  warmup were cut so it arms inside that window.

- **It treated a single CAN error frame as a stop-everything signal.** With the
  generator running and the tool provably silent, this bus produced 9 error
  frames in 43 s in one observe run — and 0 across a whole key-on-to-key-off
  cycle in another. That trip ended three consecutive armed runs before anything
  could be learned. It is now a rate, set from replayed measurement.

- **It promised the `.log` "drops straight into survey.py like any other
  capture."** It did not: a payload-keyed echo test was discarding genuine VCM
  frames, so the capture under-reported the bus by half. Only an independent
  logger could reveal that.

The instincts it got right, and worth keeping: rehearse the failure you most
need to recognise before you need to recognise it, and do not trust a number
just because your own instrument produced it.
