# `tools/`

Host-side tooling for this firmware. Nothing here is built into an image or
runs on the device.

| What | Description |
|---|---|
| `prod_build.py` | **Builds an image meant for the truck, and refuses if anything is unclean.** Spec 12.3 item 3 and spec 13 "Which build". Separate exits for an uncommitted tree, a `managed_components/` component that disagrees with its own `CHECKSUMS.json` or `dependencies.lock`, and a build not preceded by `idf.py reconfigure`. On success it writes the commit, md5 and size beside the image as `<image>.prod.json`, which `test/build_checks/build_output.py` requires. |
| `wican-log-viewer/` | **Upstream.** A Rust log viewer that came with the MeatPi WiCAN firmware. Not part of the generator inhibitor and not maintained here. |
| `gen_inhibit/` | **The generator inhibitor's host tools**: the pure core (`sync.py`, `inhibit.py`, `models.py`), the on-vehicle runner, the benches, and the virtual-bus host-confinement guard. Moved here from the `reverse-it` repo on 2026-10-08 so the reference and its firmware port live together. See [`gen_inhibit/README.md`](gen_inhibit/README.md) for the file map, and `HANDOFF.md` there for the field procedure. |

A note for anyone adding to `gen_inhibit/`: that folder is published in a
public repo, and `gen_inhibit/test_publish_scan.py` fails the build of anyone
who commits a credential, an SSID, a MAC, a private address or an absolute
path into a user's home. Settings with no committed default are listed in its
README.
