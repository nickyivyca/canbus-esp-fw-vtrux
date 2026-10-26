# `firmware/`

The generator inhibitor's firmware: the upstream MeatPi WiCAN tree, laid out
as upstream lays it out, with the inhibitor in its `main/` component. This
folder is the ESP-IDF build root (`CMakeLists.txt`, `sdkconfig`,
`dependencies.lock`, the partition table), so bench and dev builds run here,
e.g. `ninja -C gen-inhibit/firmware/build -j 1` from the repo root (AGENTS.md
"Builds during wall-clock tests"). Upstream's own README is the repo root's
`README.md`; the project's file map is `../README.md`.

Not from upstream:

- `shared_pins.json`: the project's pins of shared code, read by the release
  launcher `../vtrux_tools.py` and the pinned tooling.
- `vtrux_build.json`: the project's build data for the release tooling
  (`project`, `shared_components`).
- `vtrux_checks.json`: the project's data for the shared build checks, the
  tester's.

`tools/wican-log-viewer/` is upstream's Rust log viewer, not part of the
generator inhibitor and not maintained here. `docs/`, `sch/`,
`vehicle_profiles*`, `.vehicle_profiles/` and `minify.bat` are upstream's too.
