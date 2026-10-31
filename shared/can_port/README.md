# shared/can_port/

The CAN port interface both projects use, as one ESP-IDF component,
`vtrux/can_port` (`SPEC.md` C1): a backend per port index, and the stock
WiCAN code's CAN calls on port 0 (C3). It replaces gen-inhibit's `can.c` API
at port step 1b, and the charge interposer's `CanPort` at its step 2b.

The spec and `VALIDATION.md` belong to the reviewers: a change is drafted by
the reviewer of the project that needs it and ruled by the user in that
reviewer's session. `VERIFICATION.md` is the tester's, written when there is
code to test.

Until port step 1b's code lands, this folder holds only its spec.

## Who owns which file (SPEC C6)

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, C1 to C6, and why each requirement is written as it is | the reviewers, as above |
| the interface's code and its unit tests | C1 to C5 | the implementor of the project that needs the change |
| `VERIFICATION.md` | test evidence | the tester |
</content>
</invoke>
