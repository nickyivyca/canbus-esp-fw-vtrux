# shared/twai_port/

The driver for the chip's built-in CAN controller (TWAI), as one ESP-IDF
component, `vtrux/twai_port` (`SPEC.md` T1), behind `shared/can_port`. It is
made at gen-inhibit's port step 1b from gen-inhibit's `can.c` and the TWAI
register code in its `gen_inhibit.c`; the charge interposer adopts it at its
step 2b, in place of `port_twai.cpp`. Its `test/` folder holds the one host
fake of the TWAI driver both projects' host builds use (T10).

The spec and `VALIDATION.md` belong to the reviewers: a change is drafted by
the reviewer of the project that needs it and ruled by the user in that
reviewer's session. `VERIFICATION.md` is the tester's, written when there is
code to test.

Until port step 1b's code lands, this folder holds only its spec.

## Who owns which file (SPEC T11)

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, T1 to T11, and why each requirement is written as it is | the reviewers, as above |
| the port's code and its unit tests | T1 to T9 | the implementor of the project that needs the change |
| `unit/`'s scripted stub of the driver API | part of the unit tests: each case sets the driver's returns and checks the port's calls; it models no controller behaviour (no states, alerts or arbitration of its own) | the implementor, as the unit tests (gen-inhibit reviewer, 2026-10-31) |
| `test/`'s fake of the TWAI driver | T10: what the controller does, in its states, recovery and per-chip behaviour | the tester of the project that needs the change (user, 2026-10-31, in both reviewers' sessions) |
| `VERIFICATION.md` | test evidence | the tester |
</content>
</invoke>
