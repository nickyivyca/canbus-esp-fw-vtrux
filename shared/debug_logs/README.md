# shared/debug_logs/

Stock WiCAN's UDP debug log, as its own ESP-IDF component, `vtrux/debug_logs`
(`../wican/SPEC.md` W1). It came from gen-inhibit's tree at port step 1a, byte
for byte from `gen-inhibit/main` `0b03841`
(`gen-inhibit/firmware/components/debug_logs/`), unchanged. A project takes it
from git at the same pinned tag as `vtrux/wican`, which needs it.

| File | What | Owner |
|---|---|---|
| `src/debug_logs.c`, `include/debug_logs.h`, `include/debug_logs_config.h` | the logger: lines queued to a UDP destination once the network is up | the implementor of the project that needs the change |
| `CMakeLists.txt`, `Kconfig`, `README.md` | the component itself | the same |
| `vtrux_checks.json` | the build-switch table (R20) | the tester of the project that needs the change |

It has no `pins.json`: none of its files includes the board's `board_pins.h`
(`shared/tools` R30).
