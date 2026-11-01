# test/cpp/

The charge interposer's C++ interface to the fake: `twai_fake.h` (namespace
`twaifake`, class `TwaiFake`, `fake()`) and `twai_fake.cpp`, with the names
and meanings of the interposer's own `host_bridge/twai_fake.h` at
`charge-interposer/main` `5426202`, so its tests build on the merged fake as
they are written. Each member calls into `../twai_fake.c`, the model.
`../README.md` has the wiring and what changed from that fake.

| File | What |
|---|---|
| `twai_fake.h`, `twai_fake.cpp` | the interface |
| `test_cpp_wrapper.cpp` | its own tests, for the members that do more than pass their arguments on: `setBusOffAfterSent(n)` strikes at once for 0 (as the interposer's fake: 0 frames sent >= 0), after the n-th frame for n > 0, never for n < 0, and replaces any count already set. `make cxx-test` |
| `README.md` | this |

Two differences from the interposer's file that its suite does not reach
(interposer tester's review, 2026-10-31):

- **`assumptions()`** returns `../twai_fake.c`'s table, on its scale
  (`TWAI_FAKE_SOURCE`, `TWAI_FAKE_MEASURED`, `TWAI_FAKE_ASSUMED`,
  `TWAI_FAKE_NOT_MODELLED`), not
  `prov::Assumption` on the interposer's `provenance.h` scale (`MEASURED` <
  `DATASHEET` < `ASSUMED` < `NOT_MODELLED`). So `provenance.h` is no longer
  shared by both of the interposer's fakes for the TWAI one; a caller that
  tests "rests on evidence" maps `TWAI_FAKE_SOURCE` and
  `TWAI_FAKE_MEASURED` itself.
- **`txDiscardedByStop()`** also adds a start's discards, which are always
  zero: nothing can be queued while the controller is stopped.
