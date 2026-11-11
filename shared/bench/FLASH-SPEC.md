# The OTA flasher: specification

**Status:** approved by the user on 2026-11-11, in the charge-interposer
reviewer's session, and drafted that day by the charge-interposer reviewer;
revised the same day from the gen-inhibit reviewer's read, to keep
gen-inhibit's rules as they are (the revision approved by the user,
2026-11-11). The gen-inhibit reviewer reads it before it merges into
`shared/main` (port plan goal 20). It follows the user's ruling of
2026-11-11 (option C): the interposer's runs load the WiCAN through the
shared tooling, so gen-inhibit's OTA upload moves into `shared/bench`'s
flasher (port plan, shared bench toolkit item 2, "flash"), written to run
on Windows and on Linux for the bench's move to madhouse-debian (port plan
step 3). The bench
lock's spec is `SPEC.md` and the acceptance runner's `ACCEPT-SPEC.md`,
beside this file.

Requirements are numbered O1 to O13. The numbers are stable.

**What moves, and what must not change.** The reference is gen-inhibit's
`gen-inhibit/tools/bench_flash.py` and the `upload()` and `ota()` of its
`suite_gen_inhibit.py`, at gen-inhibit/main `40ecfa5e` (rules from the
gen-inhibit reviewer, 2026-10-08, 2026-10-16 and 2026-10-20, and the user,
2026-10-16 and 2026-10-19). This flasher keeps their behaviour. The changes
from them are only the ones marked **Change** below. Where this text and
the reference differ on anything not marked as a change, the reference
governs and the text is corrected. gen-inhibit keeps its own copies until
it chooses to pin a `shared/bench` with this flasher, and it pins one only
once its rules are shown kept (O13; the gen-inhibit reviewer's condition,
2026-11-11).

## What it is

- **O1. A second built-in flasher, `ota`,** beside `esptool`, with the same
  result: `ok`, `phase`, `detail`, `recoverable`. It loads one ESP-IDF app
  image into a WiCAN running gen-inhibit's firmware, over the WiCAN's own
  hotspot, through that firmware's upload endpoint. It takes no lock: the
  runner that calls it holds the bench (SPEC.md).
- **O2. What it is given.** From the image: the app `.bin`, its identity,
  the app ELF's sha256 (`Image.expect`), and, for a pinned image, its size,
  sha256 and md5. From the suite: its retry policy (O11). From the
  machine's settings: the device's address (default `192.168.80.1`, a WiCAN
  firmware default), the Wi-Fi interface, the name of a saved Wi-Fi profile
  for the hotspot, and the hotspot's SSID. The SSID embeds the WiCAN's MAC:
  it is in local settings only, and masked in every log, record and
  message.

## Before anything is sent

- **O3. The image is checked.** For every image, as today: the file exists,
  byte 0 is `0xE9`, the app descriptor's magic is at offset `0x20`, and it
  carries a version and an `app_elf_sha256`. **Change:** that
  `app_elf_sha256` must equal `Image.expect`, and a pinned image's size,
  sha256 and md5 must equal its pin. gen-inhibit's images are built per run
  and carry no pin, so for them only the identity is added. Otherwise it
  refuses, and nothing is sent.
- **O4. Joining the hotspot, without the passphrase.** The flasher never
  reads, stores or passes a Wi-Fi passphrase. It brings up the saved profile
  the user made (Windows: `netsh wlan connect name=<profile>
  interface=<iface>`, each `netsh` call under a 45 s timeout; Linux:
  `nmcli connection up id <profile>`). Joined means the interface is
  connected **and** on the WiCAN's SSID, read back from the system (`netsh
  wlan show interfaces`; `nmcli` on the interface), polled every 1 s for up
  to 40 s.
- **O4a. Linux and Windows the same way** (user, 2026-11-11: the tool is
  built to run on Linux as easily, with the same Wi-Fi joining).
  **Change.** The Wi-Fi handling is one module with the same calls on both
  systems: join the profile, read whether the interface is on the WiCAN's
  SSID, read the network it is on, rejoin. It has a Windows backend
  (`netsh`) and a Linux backend (NetworkManager's `nmcli`), chosen by the
  host's system, with the same timeouts and results. Nothing else in the
  flasher branches on the system, and every other step (the checks, the
  upload, the reads, the verdict) is the same code on both, except stopping
  the optional capture (O12). Each backend has its own tests, from recorded
  command output, and can-fails, and the Linux backend is shown on
  madhouse-debian joining and rejoining the real hotspot before port plan
  step 3's acceptance relies on it.
- **O5. The second-route preflight, as today.** Before the join, a TCP
  connection to a public resolver (1.1.1.1:53 or 8.8.8.8:53) must succeed
  from a local address outside the hotspot's subnet; after the join, again.
  Whether the path is the same both times is recorded, not required. So
  joining the hotspot never takes the PC's own network away, and the device
  is never reached through some other route. On Linux the saved profile is
  made with `ipv4.never-default yes` (the profile's setup, in
  `tools-reference.md`); the check catches a moved route either way.
- **O6. The device's state.** The device must answer `GET
  http://<host>/gen_inhibit` with its `elf_sha256` and `uptime_ms`, read
  leniently (trailing bytes tolerated and noted), polled every 2 s for up to
  60 s. It must be in mode 0 (OFF): gen-inhibit's firmware refuses an OTA in
  any other mode (its spec 9.3, a 403). The flasher never reads
  `/check_status` or `/load_config`, which carry credentials.
- **O7. An armed WiCAN is disarmed when the bench is held** (user,
  2026-11-11). **Change:** today's flasher refuses an armed device by design
  and leaves it to a person. Now, under the caller's hold of the bench lock,
  finding the device in a mode other than 0, the flasher disarms it exactly
  as gen-inhibit's `wican_reboot.py` does at `40ecfa5e`: `POST
  /gen_inhibit_set?mode=0` with an empty body, then up to 4 reads of the
  page, 3 s apart, 8 s timeout each, read leniently. It goes on only if a
  read shows the page clean as `wican_reboot`'s `clean()` defines it: mode
  0, and not latched, not disabled, not self-off. Its record notes that the
  device came up armed and was disarmed. Otherwise it stops with
  `recoverable` false. Called without the bench held, it refuses an armed
  device and disarms nothing, as today. gen-inhibit's preflight test "still
  refuses an armed device" becomes two: refused without a hold, disarmed
  with one.

## The upload and its verdict

- **O8. The upload, as today:** `curl -s -S --max-time 180 -w
  "\n%{http_code} %{size_upload} %{time_total}" -F ota_file=@<bin>
  http://<host>/upload/ota.bin`, under a 240 s timeout. The field name and
  the URI's filename are both required. Its status, bytes sent, time, curl's
  exit and error and the reply body (up to 2000 bytes) are recorded, and
  curl's `000` is no status. curl ships with Windows 10 and Debian.
- **O9. One verdict per upload: `bench_flash`'s `classify()` and `watch()`
  at `40ecfa5e`, unchanged.** In short, from the reference:
  - The constants: settle 90 000 ms of the new image's uptime, gate 60 s,
    never-ran 10 s, a1 confirmation 30 s, same boot within 1 s, and the
    crash reasons PANIC, INT_WDT, TASK_WDT, WDT and BROWNOUT.
  - The watch reads the page every 2 s, leniently (an unread answer is
    recorded as not answered), for up to 240 s after a 303 or no status, and
    30 s after any other status. It stops early once the verdict is a1, c,
    d, d' or e. Each reading records the wall time of its request and of its
    answer. Boots are grouped by their instants (the read's time minus
    `uptime_ms`) within 1 s plus both reads' spread, from both times.
  - The order of the checks: (b), a 4xx/5xx status; `other`, any other
    status that is not 303; then, by the boots seen: (a1) no status and no
    restart, only with a reading at least 30 s past the upload's end, else
    `undecided`; (c) the new image, one restart, settled; (d') back on the
    old image after a crash reason; (d) back on the old image, booted 60 s
    or more after the end, reason SW; (e) back on the old image, its one
    restart within 10 s of the end; `other`, running neither image;
    `undecided`, for any of its four causes in the reference. With no status
    and a restart, the verdict is (a2) joined to what follows, as `a2+<x>`.
  - With the same build in both slots, a return to the old image needs two
    or more restarts, and a (c) carries the note that (e) cannot be told
    from (c).
  - The line `VERDICT: (<v>) <names> -- <detail>` is printed and kept, with
    the boot arithmetic in the detail.
- **O10. What counts as a load, as gen-inhibit's suite counts it today:**
  the verdict is exactly `(c) TOOK`, and one strict read of the page after
  it (HTTP 200, and the whole body parses as JSON) succeeds. `a2+c` is not
  a load. A (c) whose strict read fails is not a load either: the image
  runs, but its status page is unreadable.
- **O11. After a failed load, the suite's retry policy, named on the
  image:**
  - `none`: a failed load is the flasher's failure. `recoverable` is true
    when the verdict left the device on its old image (a1, b, d, d', e) and
    false for `other` and `undecided` (user, 2026-11-11). The
    charge-interposer's runs use this: they have one WiCAN image, so there
    is never another image to restore with.
  - `dual-slot`: gen-inhibit's `ota()` at `40ecfa5e`, unchanged, with its
    phases and flags. Attempt 1 a load: `ok`, `recoverable` true. Else the
    device's image is identified; if the page can't be read, or no suite
    image matches it, no restore, `recoverable` false. Else that image is
    uploaded over itself, so both slots hold it; if that fails,
    `recoverable` false. Else the image is tried once more, and its result
    is the flasher's, `recoverable` true whatever its verdict. A failure of
    that second attempt stops the run: two failures in a row point at the
    build, not the Wi-Fi. Every attempt's VERDICT line is kept.
  - Either way, a flash that is not `ok` stops the bench, and the runner's
    end pass identifies, as for any failed flash (SPEC.md section 6).

## Where it leaves the PC, and the records

- **O12. As today.**
  - The PC stays on the hotspot, so the suite's identify and device tests
    can follow, with the network the interface was on before the join
    recorded.
  - During the watch, a failed page read rejoins (20 s) only if the
    interface is then off the WiCAN's SSID. A failed rejoin is a missed
    reading, bounded by the watch's own window (user, 2026-11-11).
  - Each upload's record goes in the run's log folder, SSID masked: the
    image, its identity, version and size, the state before, the upload's
    start, end and figures, the readings, the boots, the settle uptime, the
    verdict with its names and detail, and the capture's account.
  - The optional Wi-Fi capture (`dumpcap`/`tshark`, `bench_flash_wire`)
    moves too and stays message-only (user, 2026-11-11). It never reaches a
    verdict, every call into it is guarded, it stops at a 540 s ceiling,
    and its summary is printed only when the upload did not end in a 303.
    On Windows it stops by CTRL_BREAK, as today; on Linux by SIGINT. A
    missing tool or capture right (Linux: the `wireshark` group) is noted,
    not refused.

## Tests, and what stays where

- **O13. The rules shown kept.** Before gen-inhibit pins a `shared/bench`
  with this flasher, its tests here carry the outcome mutants of
  `test_bench_flash_outcome_mutants.py`, the preflight's and the wire's
  mutants, and `test_suite_gen_inhibit_mutants.py`'s dual-slot mutants, or
  can-fails covering the same rules. Plus one new can-fail: a second failed
  attempt stops the run, which no mutant covers today.
- **What moves, beside the flasher:** the helpers it shares with
  gen-inhibit's guard scripts (`bench_ota_guard.py`,
  `bench_flash_guard.py`): the HTTP read with a connection reset as no
  status (10 s timeout), the external-route check, the page read lenient and
  strict, the device identity, `MODES`, `set_mode`, `wifi_mode`,
  `ap_mode_detail` and `runs_dir`. `set_mode` sends the form its caller
  names: the flasher's disarm sends `mode=0` alone, as `wican_reboot` does,
  and `bench_ota_guard`'s `mode=0&offset=500` stays its own.
- **What stays gen-inhibit's:** the guard scripts themselves, which test
  gen-inhibit's firmware (its spec 12.4 row 17, 5.1 item 3), importing the
  helpers from the pinned `shared/bench` (user, 2026-11-11); and each
  suite's identify, such as gen-inhibit's `wican_sha()` (3 tries 3 s apart,
  a join after the first failure, 64 hex) and the interposer's
  `bench_wican` page read. Identify is the suite's, not the flasher's.
- **The hotspot's Wi-Fi adapter on madhouse-debian** is port plan step 3's
  (a USB Wi-Fi adapter for the WiCAN's hotspot). This
  flasher needs one within the WiCAN's range on whichever host runs the
  bench.
