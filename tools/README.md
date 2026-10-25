# `tools/`

Host-side tooling for this firmware. Nothing here is built into an image or
runs on the device.

| What | Description |
|---|---|
| `vtrux_tools.py` | **Starts the shared build tooling** (`shared/tools`, on `shared/main`) at the version this project pins in `shared_pins.json` at the repo root, from its read-only checkout `../shared-<tag>/` beside the worktree's root (shared/tools SPEC R23): `release` (a release build and its record, that spec's section 3), `image` and `digest`. A prod build is a `release` at the pinned version (spec 12.3 item 3); none is made during the shared-code port. It reads only the pin's tag, and if the checkout is missing it refuses and prints the command that creates it; every check of the pin is the pinned tooling's own. It keeps bytecode out of the checkout, whose digest that check compares. The project's build data for the tooling is `vtrux_build.json` at the repo root (`project`, `shared_components`). |
| `test_vtrux_tools.py` | Implementor self-test of `vtrux_tools.py`, a copy of it in a scratch worktree: no pin, a pin with no tag, a missing checkout (the refusal names the command), and a stand-in checkout whose command line gets the arguments and keeps no `__pycache__`. Not evidence. |
| `wican-log-viewer/` | **Upstream.** A Rust log viewer that came with the MeatPi WiCAN firmware. Not part of the generator inhibitor and not maintained here. |
| `gen_inhibit/` | **The generator inhibitor's host tools**: the pure core (`sync.py`, `inhibit.py`, `models.py`), the on-vehicle runner, the benches, and the virtual-bus host-confinement guard. Moved here from the `reverse-it` repo on 2026-10-08 so the reference and its firmware port live together. See [`gen_inhibit/README.md`](gen_inhibit/README.md) for the file map, and `HANDOFF.md` there for the field procedure. |

A note for anyone adding to `gen_inhibit/`: that folder is published in a
public repo, and `gen_inhibit/test_publish_scan.py` fails the build of anyone
who commits a credential, an SSID, a MAC, a private address or an absolute
path into a user's home. Settings with no committed default are listed in its
README.
