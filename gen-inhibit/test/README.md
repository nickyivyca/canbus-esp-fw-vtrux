# gen-inhibit/test/

gen-inhibit's tests that compile and run C on the host, and the build checks.
Each folder has its own `README.md` with what it tests and how to run it. The
owners are as the project's file map, `../README.md`, gives them.

| Folder | What | Owner |
|---|---|---|
| `gen_inhibit_sched/` | unit tests of the transmit scheduler (spec 5.2 item 10's catalogue, its emulation half) | implementor |
| `gen_inhibit_shim/` | unit tests of the shim against a fake TWAI driver; its `wican_ref.py` finds the stock code in the pinned `../shared-<tag>/` checkout | implementor |
| `gen_inhibit_host/` | the host suite: `main/gen_inhibit_core.c` run on a PC, with its scenarios and golden traces | tester |
| `gen_inhibit_spec_shim/` | the spec 12.4 requirement tests on the mock-HAL shim, built against `gen_inhibit_shim/`'s fakes | tester |
| `build_checks/` | the build-output checks, run on the image that will be flashed (spec 12.3 item 3, 12.4 row 22) | tester |
| `wican_ota_host/` | `shared/wican`'s OTA upload handler on the host, compiled from the pinned `config_server.c` against a fake ESP-IDF (W12-W14) | tester |
| `wican_upload_host/` | `shared/wican`'s two multipart upload handlers on the host, with `wican_ota_host/`'s extraction and fake (W16, and W12 for the OTA's refusal); not yet in the file map | tester |
| `wican_status_host/` | `shared/wican`'s status page handler on the host, the same way (W15's page half) | tester |
