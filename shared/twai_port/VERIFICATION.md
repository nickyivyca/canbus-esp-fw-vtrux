# shared/twai_port: verification evidence

Test evidence that the code in `shared/twai_port/` meets each requirement
in `SPEC.md`, and the evidence its build-switch table (`vtrux_checks.json`,
shared/tools R20) rests on. Why each requirement is written as it is:
`VALIDATION.md`. The tester's file (`README.md`).

Each entry names the requirements it covers, the commit of the code it
tested, the checks and the commit they ran from, and its run files under
`$REVERSE_IT_DIR`, with each file's hash. A pass verifies the code it ran
on. Entries arrive as commits on a tester's `shared/twai_port/<topic>`
branch, and the reviewer of the project that needs them merges and pushes.

## `TWAI_PORT_HOST_SWITCH` refused by every ESP-IDF build (charge-interposer tester, 2026-11-28)

**What it is evidence for:** the table's entry for `TWAI_PORT_HOST_SWITCH`,
class "not a switch", kind "host-test-only define, refused by every ESP-IDF
build". No image can carry the define, so it has no image row. The
evidence is the refusal itself (charge-interposer reviewer, 2026-11-27).
**Code tested:** `shared/twai_port/CMakeLists.txt` and `twai_port_ll.h` at
`f41286d3` on `shared/twai_port/stage-b-fake`. Both files are the
implementor's `544d74ae`, unchanged to `f41286d3`. **Check:**
`shared/twai_port/test/host_switch_refused.sh` at `f41286d3`, clean tracked
tree. **Host:** madhouse-debian, ESP-IDF v5.4.2, gcc 14.2.0, in
`build.slice`. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/runs/shared_twai_port_host_switch_f41286d3/`
(`SHA256SUMS` hashes both; the folder's `README.md` lists them).

| Check | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| ESP-IDF configure | PASS | A minimal ESP32-S3 project with `twai_port`, `can_port` and the x2 board. Without the define, `idf.py reconfigure` exits 0 and the component builds the S3 register file (the control). With `-DTWAI_PORT_HOST_SWITCH=1`, `idf.py reconfigure` exits 2 on the component's `FATAL_ERROR` (CMakeLists.txt:77): "vtrux/twai_port: TWAI_PORT_HOST_SWITCH is for host builds on the T10 fake only; an ESP-IDF build must not set it". | `host_switch_refused_plain.txt` (`af5cc33e...89fce8`) |
| Compile guard | PASS | `twai_port_ll.h` compiles on the host with the define (the control: the testers' host builds). With `ESP_PLATFORM` also defined, the compile stops on its `#error` (twai_port_ll.h:32). | `host_switch_refused_plain.txt` |
| Can-fail | PASS | `HOST_SWITCH_REFUSED_BROKEN=1` makes the two refused runs without the define. The check then fails both of them and exits 1. | `host_switch_refused_broken.txt` (`72fdf537...658dfd`) |
