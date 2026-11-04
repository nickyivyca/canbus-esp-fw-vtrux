# `firmware/`

The generator inhibitor's firmware: the upstream MeatPi WiCAN tree, laid out
as upstream lays it out, with the inhibitor in its `main/` component. The
stock WiCAN code itself is not here any more: it is the shared component
`vtrux/wican` (below). This folder is the ESP-IDF build root (`CMakeLists.txt`, `sdkconfig`,
`dependencies.lock`), so bench and dev builds run here,
e.g. `ninja -C gen-inhibit/firmware/build -j 1` from the repo root (AGENTS.md
"Builds during wall-clock tests"). Upstream's own README is the repo root's
`README.md`; the project's file map is `../README.md`.

**ESP-IDF v5.4.2** (spec 15, port step 1d), named exactly in
`main/idf_component.yml` (shared/tools R33). Every build runs in a shell
exported for it, with `IDF_TOOLS_PATH` set to its tools folder before the
export (`local-paths.md`). The bench and dev builds here are `idf.py` /
`ninja` builds, not the tooling's `build` command, so each is preceded, in
the same shell, by `py -3.14 -B gen-inhibit/vtrux_tools.py check-env` from
the repo root (shared/tools R32); a release build runs that check itself.
A configure in a shell exported for another IDF rewrites
`dependencies.lock`'s `idf` version: a release build refuses that (R8),
but a bench or dev build does not, so `git status` after a configure shows
it. The bootloader moves with the IDF version, and OTA cannot replace it,
so a WiCAN moved to v5.4.2 gets its bootloader flashed by USB (spec 15).

**The board is a dependency** (shared/boards SPEC B1 and B2, port step 1a):
`vtrux/wican_v3`, the board component on `shared/main`, taken from git at the
tag `main/idf_component.yml` names, into `managed_components/vtrux__wican_v3/`.
Every build takes it there, dev builds included; never through
`override_path`. It brings the pins (`board_pins.h`), the board calls and the
per-model values (`board.h`, which the stock code's `hw_config.h` includes),
the hardware name, and the partition table, which `sdkconfig` names by its
path in `managed_components/`. Stock WiCAN's `HARDWARE_VER` is gone. Only the
files on the project's R30 list include `board_pins.h` (shared/tools SPEC
R30). Since port step 1c no file in `main/` does: the restart test, which
did, is `vtrux/restart_guard`'s now. The stock start-up, which drives the
LEDs and reads the Bluetooth-enable input by pin, is on the stock
component's own list, its `pins.json`.

**The stock WiCAN code is a dependency too** (shared/wican SPEC W1, spec
3.3, port step 1a): `vtrux/wican`, with `vtrux/debug_logs`, from git at the
`shared/main` tag `main/idf_component.yml` names, beside the board. So is the
CAN port since port step 1b: the stock code's CAN calls are `vtrux/can_port`'s
on port 0 (W3), backed by `vtrux/twai_port`, at the same tag; `can.c`
retired. `main/` holds only gen-inhibit's own code: `app_main` in `main.c`
(the hooks, then `wican_init()`, `gen_inhibit_init()`, `wican_start()`), the
inhibitor, with port 0's setup in `gen_inhibit.c` (spec 3.3), and its hooks
into the stock code (`gi_wican.c`: port 0's registration, the bus-owner
callbacks, the two endpoints, the probe and restart-test registrations). Since
port step 1c every software restart takes port 0 off the bus first through
`vtrux/restart_guard`, at the same tag, which sets its own link-time wrap of
`esp_restart_noos()`; the TX-abort probe and the restart test are the test
apps of `vtrux/twai_port` (T13) and `vtrux/restart_guard` (G5), built in by
their switches, `-DTWAI_PORT_TXABORT_PROBE=1` and `-DRESTART_GUARD_TEST=1`
(the latter's image name ends `_rt`). The stock code's switches are set off in the
top-level `CMakeLists.txt`: `WICAN_SLCAN_COMMANDS` and the four feature
switches (spec 3.2). The rollback rehearsal's fault hook,
`OTA_HEALTH_FAULT_INJECT`, is the stock component's switch too (W7), given on
the command line; the top-level file only puts `_flt` in such an image's
name. `main/Kconfig.projbuild` is upstream's and unused; it stays so
`sdkconfig` does not change.

**`dependencies.lock` is committed as LF.** On Windows the Component Manager
writes it with CRLF line endings whenever a reconfigure updates it, so
convert it to LF before committing it (AGENTS.md "Files and folders"). A
reconfigure that changes `sdkconfig` writes it as CRLF too.

**Log levels** (spec 12.3 item 3, shared/tools R28; port step 1a2). The
committed `sdkconfig` has the release levels: ESP-IDF default and maximum
none, bootloader error. Release and bench builds take it as it is, so a
bench `_man` or `_auto` image prints only the ROM's reset banner and panic
output. Test-only builds keep info: with any test-only switch on, the
top-level `CMakeLists.txt` builds from an sdkconfig of its own in the build
directory, layered from `sdkconfig` and then `sdkconfig.log_info`, and made
afresh at every configure. A dev build that wants info layers the same files
on the command line, from this folder:

    idf.py -B build-dev -DSDKCONFIG=build-dev/sdkconfig "-DSDKCONFIG_DEFAULTS=sdkconfig;sdkconfig.log_info" build

Its `build-dev/sdkconfig` is made once; delete it to take a later change to
the committed `sdkconfig`.

**To move a git dependency to another tag, delete `dependencies.lock` and its
`managed_components/` folders first.** With the lock in place, a changed
`version:` in `main/idf_component.yml` can be solved to the commit the lock
already holds, and the reconfigure keeps the old code without a word.
Reconfigure, then check that the lock's `version:` for each `vtrux/*`
dependency is the new tag's commit.

Not from upstream:

- `shared_pins.json`: the project's pins of shared tooling, read by the
  release launcher `../vtrux_tools.py` and the pinned tooling.
- `vtrux_build.json`: the project's build data for the release tooling
  (`project`, `shared_components`: the board, `vtrux/wican_v3`; the
  stock code, `vtrux/wican` and `vtrux/debug_logs`; the CAN port,
  `vtrux/can_port` and `vtrux/twai_port`, since port step 1b; and the
  restart guard, `vtrux/restart_guard`, since port step 1c).
- `vtrux_checks.json`: the project's data for the shared build checks, the
  tester's.
- `sdkconfig.log_info`: info-level logging, layered over `sdkconfig` for
  test-only and dev builds (above).

`tools/wican-log-viewer/` is upstream's Rust log viewer, not part of the
generator inhibitor and not maintained here. `docs/`, `sch/`,
`vehicle_profiles*`, `.vehicle_profiles/` and `minify.bat` are upstream's too.
