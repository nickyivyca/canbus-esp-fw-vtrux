# diag_host: host builds of the core with shared/diag

Owned by the gen-inhibit tester. Written 2026-11-23 for stage (b) batch 2
(gen-inhibit/main db1bfeae), where the core started packing its diag frames through
shared/diag: the generated table `firmware/main/gi_diag_table.c` and `vtrux/diag`'s
`diag_pack_message()`, pinned at the tag `firmware/main/idf_component.yml` gives it.

Every host build of `gen_inhibit_core.c` takes its diag sources from here:
`../gen_inhibit_host/Makefile`, `drained_check.py`'s control build,
`../gen_inhibit_spec_shim/build.sh` and `../build_checks/Makefile`. The core uses
`__typeof__`, so the compiler is GCC or compatible.

**Spec 12.3 item 2** ("no diag misfit reported by the packer, on any trace"): the core
discards the packer's report, as spec 10 says the device does, so a host build takes
it at the packer. `misfit_wrap.c`, linked with `-Wl,--wrap=diag_pack_message`, sees
every call, records each one that reported a misfit or refused its table entry, and
passes the result on unchanged. `host_runner` prints each record as a
`<t> !! DIAG MISFIT ...` line, and `../gen_inhibit_host/invariants.py` fails any trace
with one. A build where the packer was never called through the wrapper, though diag
frames went out, prints `!! DIAG PACKER NOT WRAPPED` and exits 2. Any other program
built with the wrapper exits 3 if a record is still not taken when it exits.

**Spec 10's compile-time width check** has its can-fail here, `width_check.py`.

| File | What |
|---|---|
| `diag_host.py` | The pinned shared/diag folder (`../shared-<tag>/shared/diag`, or `<project>/shared/diag` in the `~/gi_host` copy `tools/bench/gi_host_run.sh` ships), and the arguments a cc line adds: `args [--wrap] [--diag DIR]`, `dir`. Exit 2 with what is missing. |
| `misfit_wrap.h`, `misfit_wrap.c` | The wrapper over `diag_pack_message()` and its records (`gi_misfit_take()`, `gi_misfit_calls()`, `gi_misfit_format()`). |
| `misfit_wrap_test.c` | The wrapper's own cases, run by `test_diag_host.py`. |
| `test_diag_host.py` | The helper's and the wrapper's tests. (1) The pin and the checkout, the `~/gi_host` copy first, a missing checkout refused. (2) The wrapper: a fit not recorded; a misfit recorded with id, count, mask, name and value, the caller's mask set; a refused table entry recorded as -1; a record nobody takes ends the process with exit 3. (3) The can-fail end to end: `host_runner` on a copy of the core with 0x7F3's `diag_reserved16` planted at 65536 (straight into the raw array, past the width check) prints a MISFIT line at each 0x7F3 and `invariants.py` fails the trace; the unplanted copy prints none and passes. (4) Without `--wrap` the runner refuses with `DIAG PACKER NOT WRAPPED`. |
| `width_check.py` | Spec 10: compiles the core against planted tables. The control builds; a DBC with `diag_tx_ok` 32 -> 16 bits, `diag_flags` 8 -> 7, `diag_mode` 8 -> 2 (PASSIVE does not fit) or `diag_flags` signed, and the core putting `diag_flags` as a `uint16_t` or an `int8_t`, each fail on a static assertion naming the signal; `diag_tx_fail` 8 -> 4, a field the core caps, builds, as spec 10 allows. Another compile error is VOID. `--selftest` checks the judge. |

```
PYTHONDONTWRITEBYTECODE=1 python3.13 -B test_diag_host.py
PYTHONDONTWRITEBYTECODE=1 python3.13 -B width_check.py [--keep] | --selftest
```

Run both in `build.slice` on madhouse (`systemd-run --user --scope --slice=build.slice
-q ...`). They build under `scratch/diag-host-test/<pid>/` and
`scratch/width-check/<pid>/`, removed afterwards. `width_check.py` needs cantools (the
generator's).
