# wican_status_host: the status page's fw_version on the host

`shared/wican` W15, its page half: the status page's `fw_version` is the app
descriptor's version string as it is, never parsed (no `0.00`). Owned by the
gen-inhibit tester. Written 2026-11-23 for stage (b) batch 1, on the gen-inhibit
reviewer's instruction of that day: test it on the host, as W12-W14 were
(`../wican_ota_host/`). The device's `/check_status` is not read; it stays barred.
The device half of W15, mDNS, is `tools/bench/wican_fw_version_check.py`.

`run_status_host.py` finds the `httpd_uri_t` that vtrux/wican's `config_server.c`
registers for `"/check_status"` and takes the handler it names out of the file as
it is, with the file-scope `#define`s that handler names. It compiles that on the
host under ASan and UBSan against `fake_status.c` and ESP-IDF v5.4.2's own cJSON
(`components/json/cJSON` of the ESP-IDF tree), with generated stand-ins for the
file-scope data the handler reads: `device_config`'s fields, `device_id` and
`can_datarate_str`. Each case writes a known version into the app descriptor and
runs the handler once. The body it sends must parse as one JSON object with one
`fw_version` key, and that key must be the version, the whole string. A prefix,
`0.00`, an empty value, a missing key or a second key fails.

The cases are strings shared/tools R4's `git describe --always --tags --dirty` can
put in the 32-byte field: an abbreviated commit, the same with `-dirty`, a describe
from a stock-style tag (`v4.20-...`, which the stock parse reads as 4.20), and 31
characters, the field's most. `git_version`, the content type, the sends and the
return are recorded, not judged.

By default it reads the checkout of the tag `firmware/main/idf_component.yml` pins
(`../gen_inhibit_shim/wican_ref.py`); `--wican DIR` reads another. The handler's
text and names are read only to build the fake. The cases come from the spec.

| File | What |
|---|---|
| `run_status_host.py` | Extracts, builds in `scratch/wican-status-host/<pid>/` (removed afterwards), runs each case in its own process, and judges. Exit 0 pass, 1 a case fails or is VOID (the handler crashed on the host), 2 refused. `--json` writes the records and rows. It reuses `../wican_ota_host/run_ota_host.py`'s C reading (comments, strings, preprocessor lines, file-scope blocks). |
| `fake_status.h`, `fake_status.c` | The ESP-IDF and component calls the handler makes, and the case's run: the descriptor (v5.4.2's `esp_app_desc_t`), `esp_app_get_description()`, and for the code before W15 `esp_ota_get_running_partition()` and `esp_ota_get_partition_description()`; `httpd_resp_set_type()` and `httpd_resp_send()`, which keep the body. |
| `test_run_status_host.py` | The harness's own test. See below. |

## The harness's test

`test_run_status_host.py` checks that:
- the pinned component passes every case;
- shared/main tag `2026.11.04`, from before W15, fails with `fw_version` `0.00` on
  the commit, dirty and full-field cases. Its stock-tag case is recorded as it
  comes out: on a 64-bit host the stock parse's `%ld` into a `uint32_t` overflows
  the stack and ASan stops it, so it is VOID, never a pass;
- six planted defects in the pinned handler each fail the cases they show in and
  pass the others: a renamed key, an empty value, a value cut to 8 characters, a
  leading `v` dropped, the stock parse put back, a second `fw_version` key;
- five mutants of the judge each change a verdict;
- the extraction takes the handler the registration names, past a registration in a
  comment, a commented-out `.handler` and a registration for another URI; no
  registration, two, one with no handler, or a handler with no definition is
  refused.

```
PYTHONDONTWRITEBYTECODE=1 python3.13 -B run_status_host.py [--wican DIR] [--idf DIR] [--json OUT]
PYTHONDONTWRITEBYTECODE=1 python3.13 -B test_run_status_host.py [--wican DIR] [--prefix DIR] [--idf DIR]
```

Run both in `build.slice` on madhouse (`systemd-run --user --scope
--slice=build.slice -q ...`). They need `cc` with ASan and UBSan, and ESP-IDF
v5.4.2's tree (`--idf`, else `$IDF_PATH`, else `~/esp/esp-idf-v5.4.2`); no export
is needed.
