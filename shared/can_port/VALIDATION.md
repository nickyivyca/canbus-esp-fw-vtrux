# Shared CAN port interface: validation evidence

Why each requirement in `SPEC.md` is written as it is. Test evidence, that
the component meets them, is in `VERIFICATION.md`, when there is code to
test. Each entry has a **Basis**, and a **Status**: *complete*, or *basis
pending*, saying what is missing.

**Where things are.** The port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
("Layer 2: shared components", `can_port`). The readings are of
`gen-inhibit/main` at `4020bc8` (`gen-inhibit/firmware/main/can.c`,
`shared/wican/can.h` at the pinned `2026.10.30`) and of
`charge-interposer/main` at `5426202` (`firmware/src/can_port.h`).

### C1. One interface
- **Basis:** port plan: `can_port`, one CAN port interface for both
  projects, replacing `can.c`'s API and the interposer's `CanPort` (stage
  (a)). One C interface over a backend per port index (user, 2026-10-31, in
  the gen-inhibit reviewer's session), on the charge-interposer reviewer's
  reading (2026-10-31): the X2's port 1 is the MCP2515, which stays Arduino
  code until it becomes `mcp2515_port` in stage (b), so the interface
  cannot be hard-wired to `twai_port`.
- **Ports not routed through it** (charge-interposer reviewer, 2026-10-31;
  user, 2026-10-31): in stage (a) the MCP2515 is a C++ Arduino class that
  only the interposer's own code drives, and no stock call reaches port 1,
  so a port a project drives directly stays allowed until its backend
  exists.
- **Status:** complete.

### C2. The operations
- **Basis:** what `CanPort` gives today (`can_port.h`), which the
  interposer's `test_canport_contract.cpp` checks through the interface:
  `send()` only enqueues and never waits for the wire, `service()` must not
  block, and the counters (charge-interposer reviewer, 2026-10-31). A
  backend that cannot count per frame gives a bound: the MCP2515 gives one
  flag per receive buffer, so at worst one loss per pass (`can_port.h`).
- **Status:** complete.

### C3. The stock calls are port 0
- **Basis:** port plan: `can_port` replaces `can.c`'s API, which the stock
  code calls (`shared/wican` W3, `can.h`). Stage (a) keeps stock behaviour
  as it is (port plan, "Stock WiCAN code"; `shared/wican` W2).
- **The rate codes stay with the stock calls** (user, 2026-10-31): the
  stock code passes `CAN_5K` to `CAN_1000K` (`can.h`), and `can.c` maps them
  through its own timing table, so that table is the stock layer's; the
  interposer passes a bitrate. `twai_port` takes bit timing.
- **Status:** complete.

### C4. The owner's refusal
- **Basis:** gen-inhibit spec 3.2 (review A3): while gen-inhibit owns the
  bus no other path may transmit, every refused attempt is counted (user,
  2026-10-12), and the log is rate-limited; the plan's "the send refusal
  become[s] a callback". It is also what spec 7 trip 7's completion test
  rests on: a frame queued by another task behind an inhibit frame would
  leave `msgs_to_tx` above zero after the inhibit has gone (`can.c`,
  `can_send()`'s comment).
- **Its own registration** (user, 2026-10-31): `can_port` sits below
  `vtrux/wican`, so it cannot read `wican`'s bus-owner callbacks (W5);
  gen-inhibit registers the same function with both.
- **Reading:** `can_send()` refuses with `ESP_ERR_INVALID_STATE` while
  `gen_inhibit_owns_bus()`, counts through
  `gen_inhibit_note_refused_can_send()`, and logs at most once every 5 s.
- **Status:** complete.

### C5. The stock teardown runs the hook
- **Basis:** gen-inhibit spec 8, the quiesce handshake (backstop): every
  stock `can_disable()` quiesces the worker before the driver is
  uninstalled, since other tasks may call it without knowing about
  gen-inhibit.
- **Status:** complete.

### C6. Ownership
- **Basis:** AGENTS.md "Shared code": a shared file has no single owning
  project; product code is the implementor's of the project that needs
  the change.
- **Status:** complete.
</content>
