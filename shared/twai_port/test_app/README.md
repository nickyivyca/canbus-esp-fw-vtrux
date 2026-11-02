# shared/twai_port/test_app/

The transmit-abort probe (`../SPEC.md` T13), compiled into a project's
image only when the build sets the component's switch
`TWAI_PORT_TXABORT_PROBE`, and never into a release image (`shared/tools`
R20). Moved at port step 1c from gen-inhibit's `gi_txabort_probe.c` and
`.h`, with its URI, its parameters and its answer unchanged.

| File | What |
|---|---|
| `twai_port_txabort_probe.c` | `POST /gi_txabort`: what the chip's abort command does to a frame awaiting arbitration, to one being transmitted and to an empty buffer, what the driver reports afterwards, and how long the buffer takes to come free (gen-inhibit spec 5.2 item 9). It writes the abort command register directly, so it is a measurement build's only |
| `include/twai_port_txabort_probe.h` | what the project calls: `twai_port_txabort_register()`, from its URI hook (`shared/wican` W8), with its check of whether it owns the bus, while which the probe refuses, and the body of that refusal |
