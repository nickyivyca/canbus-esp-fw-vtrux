# wican_ota_host: the OTA upload handler on the host

Gen-inhibit spec 9.2 items 4-6, which are `shared/wican` W12, W13 and W14. Owned
by the gen-inhibit tester. Written 2026-11-23 for stage (b) batch 1.

`run_ota_host.py` takes the OTA handler out of vtrux/wican's `config_server.c` as it
is. The handler is the one function that calls `esp_ota_begin()`, and the script
takes the file-scope `#define`s and the struct that function uses with it. It
compiles them on the host against a fake of the ESP-IDF calls the handler makes,
under ASan and UBSan. Then it runs the handler through a complete upload and
through each kind of failed one, and judges each case's record against the
spec's words.

By default it reads the checkout of the tag `firmware/main/idf_component.yml`
pins (`../gen_inhibit_shim/wican_ref.py`); `--wican DIR` reads another. The
handler's text and the ESP-IDF names in it are read only to build the fake. The
cases come from the spec.

| File | What |
|---|---|
| `run_ota_host.py` | Extracts, builds in `scratch/wican-ota-host/<pid>/` (removed afterwards), runs each case in its own process, and judges. See the rows below. Exit 0 pass, 1 fail or VOID, 2 refused. `--json` writes the records and rows. |
| `fake_idf.h` | The ESP-IDF names, types and constants the handler uses. The constants are v5.4.2's own: `OTA_SIZE_UNKNOWN`, `OTA_WITH_SEQUENTIAL_WRITES`, and the `HTTPD_SOCK_ERR_*` returns. |
| `fake_engine.h`, `fake_idf.c` | The fake's behaviour and each case's record. The client is a `curl -F ota_file=@...` multipart body, sent in chunks of at most 1436 bytes. At set body offsets it can inject receive timeouts (a count, or forever), a reset, an invalid socket or a close. A failed `esp_ota_begin`, `_write`, `_end` or `_set_boot_partition` can be injected too. The record covers each `esp_ota_begin` size, the sessions begun, ended and still open at return, the timeouts (in a row and in all), the restart timer, the reply, and whether the written image equals the image sent. |
| `ota_cases.c` | The 17 cases. |
| `test_run_ota_host.py` | The harness's own test. See below. |

## The rows

- **W14:** every `esp_ota_begin()` is given `OTA_WITH_SEQUENTIAL_WRITES`.
- **W12:** after any failed exit (12 kinds, the client's and the device's), no
  session is open when the handler returns. The handler may leave one open
  only if it started the restart. A failed `esp_ota_begin` leaves none.
- **W13:** a stall mid-body, or before the first byte, is dropped on exactly
  the 4th timeout in a row. Ending the session is W12's row for the same case.
  These do not drop the upload, which completes byte for byte:
  - 3 timeouts in a row;
  - 3 timeouts four times, with chunks between;
  - 2 timeouts twice, with chunks between.
- **Recorded, not judged:** the W13 row for a stall at the trailing boundary,
  with the timeouts taken and the reply. This follows the gen-inhibit
  reviewer's ruling of 2026-11-23: W13 bounds how long a stalled upload holds
  the server, and a read that gives up sooner keeps that bound.
- **The control:** the complete upload must restart into an image written
  byte for byte. Otherwise the run is VOID.
- **The 5 s:** each timeout's 5 s is the server's, and the bench measures it
  (`tools/bench/ota_failpaths_bench.py`).

## The harness's test

`test_run_ota_host.py` checks that:
- the pinned component passes;
- shared/main tag `2026.11.04`, from before W12-W14, fails all three:
  - W12 on a reset, a close and an invalid socket mid-body;
  - W13 on a stall, which took 21 timeouts;
  - W14 on every case;
- eight planted one-line defects in the pinned handler each fail their own
  requirement and no other. One of them is the parked write pointer of spec
  9.2 item 2, which the control turns VOID;
- five mutants of the judge each change a verdict;
- the extraction is not misled by braces or by the word `esp_ota_begin` in
  comments, strings, chars or `#define`s;
- no handler, or two, is refused.

```
PYTHONDONTWRITEBYTECODE=1 python3.13 -B run_ota_host.py [--wican DIR] [--json OUT]
PYTHONDONTWRITEBYTECODE=1 python3.13 -B test_run_ota_host.py [--wican DIR] [--prefix DIR]
```

Run both in `build.slice` on madhouse (`systemd-run --user --scope
--slice=build.slice -q ...`). They need `cc` with ASan and UBSan.
