# `tools/`

Host-side tooling for this firmware. Nothing here is built into an image or
runs on the device.

| What | Description |
|---|---|
| `prod_build.py` | **Builds an image meant for the truck, and refuses if anything is unclean.** Spec 12.3 item 3 and spec 13 "Which build". Separate exits for an uncommitted tree, HEAD not on `gen-inhibit/main` (another branch, or detached), a `managed_components/` component that disagrees with its own `CHECKSUMS.json` or `dependencies.lock` (checked again after its own `idf.py reconfigure` has fetched them and before `idf.py build`, where a component the lock names but that is missing is a refusal), a build not preceded by `idf.py reconfigure`, a `build/` whose CMake cache sets a test or measurement flag (`GI_TEST_RESTART_QUIESCE` and the `GI_INSTRUMENT_*` and `OTA_HEALTH_FAULT_INJECT` flags; a reconfigure keeps them) or turns on a stock feature this product leaves off (`WICAN_FEATURE_MQTT`, `_ELM327`, `_AUTOPID`, `_REALDASH`; spec 3.2), and, read from the ELF after the build, an image carrying the restart test build's symbols, lacking spec 3's restart step (`__wrap_esp_restart_noos`, called by `esp_restart` and `panic_restart`), or carrying a stock feature's code (its functions, or `app_main` calling `can_enable`). The image judged and recorded is the one the build named in `build/project_description.json` (`app_bin`), not the newest `.bin` in `build/`; a missing name or file is a refusal. On success it writes the commit, md5 and size beside the image as `<image>.prod.json`, which `test/build_checks/build_output.py` requires. |
| `test_prod_build_fetch.py` | Implementor self-test of `prod_build.py`'s refusal 3 on a fresh clone with no `managed_components/`: a fake `idf.py` whose reconfigure fetches a tampered component, nothing, or a good one, and the build must be asked for only in the last. `--script` runs another copy of `prod_build.py` against the same cases. Needs this checkout's `managed_components/espressif__mdns` for the good case. Not evidence. |
| `test_prod_build_image.py` | Implementor self-test of which image `prod_build.py` judges and records: `main()` on a scratch folder with the other checks passed, a fake `idf.py` whose build writes `build/`, and refusal 6 replaced by a recorder. With a stray, newer `.bin` of another name beside the named image, the named one must be judged and recorded and the stray get no record; with no `project_description.json`, no `app_bin`, or an `app_bin` naming no file, it must refuse with nothing judged or recorded. `--script` runs another copy. Not evidence. |
| `test_prod_build_image_mutants.py` | Runs `test_prod_build_image.py` against a copy of `prod_build.py` that picks the newest `.bin` by mtime again; the test must fail it by a check (`stray`), not a crash. Not evidence. |
| `wican-log-viewer/` | **Upstream.** A Rust log viewer that came with the MeatPi WiCAN firmware. Not part of the generator inhibitor and not maintained here. |
| `gen_inhibit/` | **The generator inhibitor's host tools**: the pure core (`sync.py`, `inhibit.py`, `models.py`), the on-vehicle runner, the benches, and the virtual-bus host-confinement guard. Moved here from the `reverse-it` repo on 2026-10-08 so the reference and its firmware port live together. See [`gen_inhibit/README.md`](gen_inhibit/README.md) for the file map, and `HANDOFF.md` there for the field procedure. |

A note for anyone adding to `gen_inhibit/`: that folder is published in a
public repo, and `gen_inhibit/test_publish_scan.py` fails the build of anyone
who commits a credential, an SSID, a MAC, a private address or an absolute
path into a user's home. Settings with no committed default are listed in its
README.
