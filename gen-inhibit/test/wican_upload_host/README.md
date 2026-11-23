# wican_upload_host: the multipart upload handlers' start on the host

`shared/wican` W16, with W12 for the OTA upload's refusal. Owned by the gen-inhibit
tester. Written 2026-11-23 for stage (b) batch 2, on the gen-inhibit reviewer's
instruction of that day, beside `../wican_ota_host/` (W12-W14), whose extraction and
fake it uses.

W16: both multipart upload handlers, the OTA upload and `/upload/car_data.json`, look
for the first boundary line, the part's `Content-Type` header and the blank line after
it only within the bytes received so far; they keep reading until they find them, the
buffer is full or the body ends, and only then refuse a start that lacks any of them,
with 400, nothing written. For the OTA upload that is a failed exit under W12.

`run_upload_host.py` takes both handlers out of vtrux/wican's `config_server.c` as they
are: the OTA handler as `../wican_ota_host/run_ota_host.py` finds it (the one function
that calls `esp_ota_begin()`), and the handler the `httpd_uri_t` for
`"/upload/car_data.json"` names, with the file-scope items they need (`#define`s, types,
the `file_server_data` struct, helper functions, followed through what they use). It
compiles them under ASan and UBSan against `../wican_ota_host/fake_idf.c` and
`fake_file.c`. The fake poisons the receive buffer past the bytes each receive
delivered, so a read of a byte not received stops the case with ASan's
use-after-poison: that is W16's "only within the bytes received", observed directly.
The handlers' text and names are read only to build the fake. The cases come from the
spec.

| File | What |
|---|---|
| `run_upload_host.py` | Extracts, builds in `scratch/wican-upload-host/<pid>/` (removed afterwards), runs each case of each handler in its own process, and judges. Exit 0 pass, 1 a row fails or the run is VOID, 2 refused. `--json` writes the records and rows. |
| `upload_cases.c` | The 14 cases, run as `upload_host ota\|car <case>`. |
| `fake_file.h`, `fake_file.c` | `/upload/car_data.json`'s file calls (`fopen`, `fwrite`, `fclose`, `unlink`) on an in-memory file, each counted, and W7's `flash_write_refused()`, never refusing. |
| `test_run_upload_host.py` | The harness's own test. See below. |

## The cases and rows

Each case runs on both handlers:
- `complete`, the control: the image arrives byte for byte (the OTA restarts into it;
  `car_data.json` holds it and the reply is 200). Otherwise the run is VOID.
- A well-formed start split across receives, accepted with every byte kept: split
  inside the boundary line, right after it, inside `Type:`, before the blank line and
  inside it; and the whole start 1 or 3 bytes per receive.
- A malformed start, refused with 400 and nothing written: no line break at all (no
  boundary line), no `Content-Type` header, no blank line, and a body that ends inside
  the start.
- A start that does not end before the buffer fills (a 6,000-character filename), in
  full-size receives and 7 bytes at a time: refused with 400, nothing written.

Nothing written is no `esp_ota_write()` for the OTA upload, and no `fopen()` or
`fwrite()` for `car_data.json`. For the OTA upload's refusals a W12 row also needs no
session open at return and no restart. A removal (`unlink`) is recorded, not judged. A
case that reads past the received bytes FAILS its W16 row; any other crash is VOID,
never a pass.

## The harness's test

`test_run_upload_host.py` checks that:
- shared/main tag `2026.11.23.2`, W16's code, passes all 38 rows;
- tag `2026.11.23`, from before W16, passes no W16 row, and its malformed starts are
  reads past the received bytes. This is the can-fail;
- nine planted defects in `2026.11.23.2`'s text each fail exactly their rows:
  - refusing after the first short read;
  - no blank-line check;
  - the end of the body not a stop;
  - the OTA refusal leaving its session open;
  - `car_data.json`'s refusal answered 500;
  - the file truncated on a refusal;
  - one byte written on an OTA refusal;
  - the data offset one short (the control: VOID);
  - stock's 200-byte `memchr` put back (judged by kind: every failure a read past the
    received bytes, including the cases whose first receives are under 200 bytes);
- six mutants of the judge each change a verdict;
- the registration's handler is found past a commented-out registration and one for
  another URI; none, or two, are refused.

```
PYTHONDONTWRITEBYTECODE=1 python3.13 -B run_upload_host.py [--wican DIR] [--json OUT]
PYTHONDONTWRITEBYTECODE=1 python3.13 -B test_run_upload_host.py [--wican DIR] [--prefix DIR]
```

`run_upload_host.py` reads the pinned checkout by default
(`../gen_inhibit_shim/wican_ref.py`); the test reads `../shared-2026.11.23.2/` and
`../shared-2026.11.23/` beside the worktree root. Run both in `build.slice` on madhouse
(`systemd-run --user --scope --slice=build.slice -q ...`). They need `cc` with ASan and
UBSan.
