# `firmware/`

The generator inhibitor's firmware: the upstream MeatPi WiCAN tree, laid out
as upstream lays it out, with the inhibitor in its `main/` component. This
folder is the ESP-IDF build root (`CMakeLists.txt`, `sdkconfig`,
`dependencies.lock`), so bench and dev builds run here,
e.g. `ninja -C gen-inhibit/firmware/build -j 1` from the repo root (AGENTS.md
"Builds during wall-clock tests"). Upstream's own README is the repo root's
`README.md`; the project's file map is `../README.md`.

**The board is a dependency** (shared/boards SPEC B1 and B2, port step 1a):
`vtrux/wican_v3`, the board component on `shared/main`, taken from git at the
tag `main/idf_component.yml` names, into `managed_components/vtrux__wican_v3/`.
Every build takes it there, dev builds included; never through
`override_path`. It brings the pins (`board_pins.h`), the board calls and the
per-model values (`board.h`, which `main/hw_config.h` includes), the hardware
name, and the partition table, which `sdkconfig` names by its path in
`managed_components/`. Stock WiCAN's `HARDWARE_VER` is gone. Only the files on
the project's R30 list include `board_pins.h` (shared/tools SPEC R30):
`main/main.c` (LEDs, Bluetooth-enable input) and `main/gi_restart_test.c`
(test build only).

**`dependencies.lock` is committed as LF.** On Windows the Component Manager
writes it with CRLF line endings whenever a reconfigure updates it, so
convert it to LF before committing it (AGENTS.md "Files and folders").

Not from upstream:

- `shared_pins.json`: the project's pins of shared tooling, read by the
  release launcher `../vtrux_tools.py` and the pinned tooling.
- `vtrux_build.json`: the project's build data for the release tooling
  (`project`, `shared_components`: the board, `vtrux/wican_v3`).
- `vtrux_checks.json`: the project's data for the shared build checks, the
  tester's.

`tools/wican-log-viewer/` is upstream's Rust log viewer, not part of the
generator inhibitor and not maintained here. `docs/`, `sch/`,
`vehicle_profiles*`, `.vehicle_profiles/` and `minify.bat` are upstream's too.
