# Shared diag frames: verification evidence

Test evidence that the code in `shared/diag/` meets each requirement in
`SPEC.md`. Why each requirement is written as it is: `VALIDATION.md`. The
tester's file (`README.md`).

Each entry names the requirements it covers (D1 to D6), the commit of the
code it tested, the checks and the commit they ran from, and its run files
under `$REVERSE_IT_DIR`, with each file's hash. A pass verifies the code it
ran on. Entries arrive as commits on the tester's `shared/diag/<topic>`
branch, and the reviewer of the project that needs them merges and pushes.

## D6 on tag `2026.11.23.5` (charge-interposer tester, 2026-11-24)

**Code tested:** `shared/diag` at tag `2026.11.23.5` (`shared/main`
`2a7ab8be`, code commit `1339d402`), the version the charge interposer's
schema 5 batch pins. **Checks:** `shared/diag/test/fit_checks.py` at
`f4cbbbf2` on `shared/diag/tester-fit-checks`, clean. Its `shared/diag` code
is the tag's: `git diff 2026.11.23.5 f4cbbbf2` over `shared/diag`, without
`test/` and `README.md`, is empty (each run file's header). The checks were
written from `SPEC.md` D6 and the macros' interface in `README.md`, "Using
it" (`test/README.md`). **DBCs:**
- the fixture `test/fixtures/fit.dbc`;
- gen-inhibit's published diag DBC from `origin/gen-inhibit/main` `cca06f36`
  (last changed in `d2902fbf`), as is and with `diag_tx_fail` narrowed from
  8 to 4 bits;
- the charge interposer's from `origin/charge-interposer/main` `b6c6be9b`
  (last changed in `d83fc878`).

**Compilers:**
- host gcc 14.2.0 as C11 and gnu17, and g++ 14.2.0 as C++17 and gnu++2b;
- ESP-IDF v5.4.2's `xtensa-esp32s3-elf` and `riscv32-esp-elf` 14.2.0, as
  gnu17 and gnu++2b, compile only.

Every case uses `-Wall -Wextra -Werror`. **Host:** madhouse-debian, Python
3.13, cantools 41.3.1, in `build.slice`. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/runs/shared_diag_d6_f4cbbbf2/`
(`SHA256SUMS` hashes all three; the folder's `README.md` lists them).

| Req | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| D6 | PASS | **The checks compile in C and in C++.** 53 cases on 8 compilers, 424 case runs, all as expected. A fail case passes only if the compiler stops on a static assertion that names the signal. **A fitting value builds:** 26 cases through the put, capped, bit, mask and constant forms, into unsigned signals of 1 to 64 bits, Intel and Motorola. On the host, each one puts the expected value. **The capped form caps at the signal's maximum:** `uint32_t` 300 and 16 into 4 bits give 15, and 15 and 9 stay as they are. `uint16_t` 4096 into 12 bits gives 4095. `UINT64_MAX` into 32 bits gives 4294967295, and `UINT64_MAX` and 2^63 into 63 bits give 2^63 - 1. **A build fails:** for a type wider than its signal outside the capped form (`uint16_t` into 8 and 12 bits, `uint8_t` into 4, `uint32_t` into 24, `uint64_t` into 63 and 64). It also fails for a signed value through the put form (`int8_t`, `int`, an `int` literal) and through the capped form (`int32_t`, `int64_t`). It fails for a mask over its signal (0x1F into 4 bits, 0x1000 into 12, -1) and for a constant over its signal (16 into 4 bits, 256 into 8, -1). It fails for a signed signal through each of the five forms. **Gen-inhibit's width-check case:** with `diag_tx_fail` at 4 bits, a `uint32_t` 300 through the capped form builds and gives 15, and through the put form it fails on `WICAN_Diag_Counters.diag_tx_fail`. At 8 bits, the capped form gives 255. **Both projects' generated headers compile** with the checks in them. | `fit_f4cbbbf2.txt` (`1e069c3b...c9176`) |
| D6 (can-fail) | PASS | `fit_checks.py --mutants`: 13 one-line defects planted in the fixture's generated checks, run against the host cases (192 case runs each). The defects: the put form takes a wider type or a 64-bit type; in C, and then in C++, every type is taken as unsigned; a signed signal is taken; the capped form does not cap, caps one above the maximum, or takes a signed value; a mask up to twice the signal, or a negative mask, is taken; a constant one over the signal, or a negative constant, is taken; a signal's maximum is one too high. Each defect makes at least one case not as expected (13 of 13 killed). The unedited header passes every case (the control). | `fit_mutants_f4cbbbf2.txt` (`907596af...b2d27`) |
| D6 (judge) | PASS | `fit_checks.py --selftest`: the judge on 11 made-up compiler results. A build or a failure where the other was expected is a FAIL, and so is a wrong value. A static assertion that names another signal, or another compile error, is VOID. | `fit_selftest_f4cbbbf2.txt` (`292c11a2...1f3a1`) |

**Not tested:** the capped form into a 64-bit unsigned signal, because a raw
`int64_t` value holds at most 2^63 - 1. Each project's own move to the checks
(D6's last sentence) is that project's verification, not this file's.

## D2-D4 on tag `2026.11.23.3` (gen-inhibit tester, 2026-11-23)

**Code tested:** `shared/diag` at tag `2026.11.23.3` (`ada2ab37`), the
version gen-inhibit pins as `vtrux/diag` from its stage (b) batch 2
(`gen-inhibit/main` `db1bfeae`). **Checks:** `shared/diag/test/` at
`shared/main` `ef88e63f`, clean, whose `shared/diag` code is the tag's: `git
diff 2026.11.23.3 ef88e63f` over `shared/diag`, leaving out `test/` and
`README.md`, is empty (the run file's header). The checks were written from
`SPEC.md` and judge the code against cantools' reading of each DBC, never
against the packers' own code (`test/README.md`). **DBCs:** the fixture
`test/fixtures/verify.dbc` and both projects' published diag DBCs as origin's
main branches held them: gen-inhibit's from `origin/gen-inhibit/main`
`db998aca` (last changed in `d2902fbf`, schema 9) and the charge
interposer's from `origin/charge-interposer/main` `5a940b38` (last changed in
`d83fc878`). **Host:** madhouse-debian, Python 3.13, `cc` with ASan and
UBSan, in `build.slice`. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/stage_b2_db998aca/shared_diag/`
(`SHA256SUMS` hashes both; the folder's `README.md` lists them).

| Req | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| D3 | PASS | **The table is the DBC's.** For the fixture (10 messages, 31 signals), gen-inhibit's DBC (6, 25) and the interposer's (6, 33): the generated C table, the Python `MESSAGES` and the header's per-signal `__BITS`, `__SIGNED` and index constants and per-message `__ID` give every message's id, extended flag and DLC, and every signal's start bit, length, byte order, signedness and multiplexing, as cantools reads the DBC. **A misfit is reported, not truncated.** 400 value vectors per message (seed 20261123) through both packers: the bytes are cantools' encode of the same values with each misfit left 0, and the count and the mask name exactly the signals out of range. Fixture 2650 vectors all fit, 1314 with misfits, 36 with a selector that does not fit (result -1 and a zeroed frame, from a frame handed in as A5 bytes); gen-inhibit 1186 / 1214 / 0; interposer 1172 / 1182 / 46. | `verify_ef88e63f.txt` (`21729c19...878bf`) |
| D4 | PASS | **C and Python agree.** On the same three tables, both list the signals in the same order, both range functions give each signal's range from its width, and the same vectors give the same result, mask and bytes: 4000 on the fixture, 2400 on each project's DBC. | `verify_ef88e63f.txt` |
| D2 | PASS | **The build id.** For 65 hashes (made here, and four edge hashes), `diag_build_id_from()` and `diag_build_id()` over the driver's descriptor give hash bytes 0-3 with byte 0 most significant. Packed into a 32-bit unsigned Intel signal, the wire bytes are hash[3..0], and the DBC's value printed `%08x` reads as the hash's first four bytes. | `verify_ef88e63f.txt` |
| D2-D4 (can-fail) | PASS | `test/mutate.py`: 27 one-line defects planted in a copy of the code, 11 in the C packer, 5 in the Python twin, 9 in the generator and 2 in the build id. `verify_diag.py` fails on each one (27 of 27 killed), and the unedited copy passes (the control). | `mutate_ef88e63f.txt` (`82312155...67ebb`) |
| D1 | not tested | Scope: what the component holds and what each project keeps. Nothing to run. | |
| D5 | not tested here | The schema convention is each project's DBC. gen-inhibit's half, `DiagSchemaHash` its own layout hash and the committed tables regenerated from its DBC byte for byte, is in gen-inhibit's `VERIFICATION.md`, 12.3, stage (b) batch 2. | |

The first run of these checks, at `55e9538a` on the tester's branch before
the reviewer's merge, passed the same way; this entry rests on the run above.
