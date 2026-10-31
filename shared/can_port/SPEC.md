# Shared CAN port interface: specification

**Status:** approved by the user on 2026-10-31, in the gen-inhibit
reviewer's session, and drafted that day by the gen-inhibit reviewer for
gen-inhibit's port step 1b, after the charge-interposer reviewer's read. As
a shared component's spec this follows port plan goal 20: the reviewer whose
project needs a change drafts it, the user rules in that reviewer's session,
and the other project's reviewer reads it before it merges into
`shared/main`.

- **Why** each requirement is written as it is: `VALIDATION.md`, beside
  this file.
- **Test evidence:** `VERIFICATION.md`, the tester's, when there is code to
  test.
- **Sources:** the ESP shared-code port plan,
  `$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
  (`can_port` under Layer 2 of "The split"), `shared/twai_port/SPEC.md` and
  `shared/wican/SPEC.md`.

Requirements are numbered C1 to C6. The numbers are stable.

- **C1. One interface.** `shared/can_port/` is one ESP-IDF component,
  `vtrux/can_port`, in C: the CAN port interface for both projects. A
  project registers, at start-up, a backend for each port index it routes
  through `can_port`; a port its own code drives directly is allowed until
  that port's backend exists. A backend is a table of operations;
  `twai_port` is one, and the interposer's MCP2515 port is another until it
  becomes `mcp2515_port` in stage (b).
- **C2. The operations,** for each port:
  - bring up and tear down (for `twai_port`, its T3 and T4), and set the
    mode;
  - receive: take one frame if one is waiting, or wait up to a bound the
    caller gives;
  - send: enqueue only, never wait for the wire; false means the frame was
    not queued, and it is counted;
  - service: non-blocking, called on every pass of the project's loop;
  - the status read and counters of `twai_port` T7, each as the hardware
    allows: a backend that cannot count a loss per frame gives a bound, and
    says so;
  - the port's name.
- **C3. The stock calls are port 0.** The CAN functions the stock WiCAN
  code calls (`shared/wican/can.h`: enable, disable, the setters, receive,
  send, init and the queries) are `can_port`'s, on port 0, with today's
  behaviour (`shared/wican` W2).
- **C4. The owner's refusal.** A project may register an owner hook. While
  it says the bus is owned, the stock send (C3) refuses: it counts the
  refusal through the project's hook, logs at most once every 5 s, and
  returns an error. The owner's own frames use the port's send (C2), which
  this refusal does not touch.
- **C5. The stock teardown runs the hook.** The stock disable (C3) tears
  port 0 down through its backend, so the pre-teardown hook (`twai_port` T2)
  runs first, every time.
- **C6. Ownership** is per file in the component's `README.md`.
