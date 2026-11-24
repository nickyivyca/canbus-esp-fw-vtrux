# Shared diag frames: verification evidence

Test evidence that the code in `shared/diag/` meets each requirement in
`SPEC.md`. Why each requirement is written as it is: `VALIDATION.md`. The
tester's file (`README.md`).

Each entry names the requirements it covers (D1 to D5), the commit of the
code it tested, the checks and the commit they ran from, and its run files
under `$REVERSE_IT_DIR`, with each file's hash. A pass verifies the code it
ran on. Entries arrive as commits on the tester's `shared/diag/<topic>`
branch, and the reviewer of the project that needs them merges and pushes.

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
