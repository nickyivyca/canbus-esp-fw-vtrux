# The OTA flasher: specification

**Status:** approved by the user on 2026-11-11, in the charge-interposer
reviewer's session, and drafted that day by the charge-interposer reviewer;
the gen-inhibit reviewer reads it before it merges into `shared/main` (port
plan goal 20). It follows the
user's ruling of 2026-11-11 (option C): the interposer's runs load the WiCAN
through the shared tooling, so gen-inhibit's OTA upload moves into
`shared/bench`'s flasher (port plan, shared bench toolkit item 2, "flash"),
written to run on Windows and on Linux for the rig step (port plan step 3).
Inputs: the charge-interposer tester's reading of gen-inhibit's
`bench_flash.py`, `bench_flash_wire.py`, `bench_ota_guard.py`,
`bench_flash_guard.py` and `suite_gen_inhibit.py` at gen-inhibit/main
`40ecfa5e`. The bench lock's spec is `SPEC.md` and the acceptance runner's
`ACCEPT-SPEC.md`, beside this file.

Requirements are numbered O1 to O12. The numbers are stable.

**What moves, and what must not change.** The OTA upload's rules are
gen-inhibit's, proven on its bench (reviewer, 2026-10-08 and 2026-10-16):
this spec moves them unchanged and adds only what Linux needs and what a
second project needs. gen-inhibit keeps its own copies until it chooses to
pin a `shared/bench` with this flasher, and it pins one only once the rules
below are shown kept, by its tests and mutants moved here or by can-fails
covering the same rules (the gen-inhibit reviewer's condition, 2026-11-11).

## What it is

- **O1. A second built-in flasher, `ota`,** beside `esptool`, with the same
  result: `ok`, `phase`, `detail`, `recoverable`. It loads one ESP-IDF app
  image into a WiCAN running gen-inhibit's firmware, over the WiCAN's own
  hotspot, through that firmware's upload endpoint. It takes no lock: the
  runner that calls it holds the bench (SPEC.md).
- **O2. What it is given.** From the image: the app `.bin`, its size,
  sha256 and md5 (the suite's pin), and its identity, the app ELF's sha256
  (`Image.expect`). From the machine's settings: the device's address
  (default `192.168.80.1`, a WiCAN firmware default), the Wi-Fi interface,
  the name of a saved Wi-Fi profile for the hotspot, and the hotspot's SSID.
  The SSID embeds the WiCAN's MAC: it is in local settings only, and masked
  in every log, record and message.

## Before anything is sent

- **O3. The image is checked against the pin.** The file's size, sha256 and
  md5 must equal the pin's, and the app descriptor's `app_elf_sha256` must
  equal `Image.expect`. Otherwise it refuses, and nothing is uploaded.
- **O4. Joining the hotspot, without the passphrase.** The flasher never
  reads, stores or passes a Wi-Fi passphrase. It brings up the saved
  profile the user made (Windows: `netsh wlan connect name=<profile>
  interface=<iface>`; Linux: `nmcli connection up id <profile>`). Joined
  means the interface is connected **and** on the WiCAN's SSID, read back
  from the system (`netsh wlan show interfaces`; `nmcli` on the interface).
  The join waits up to 40 s.
- **O4a. Linux and Windows the same way** (user, 2026-11-11: the tool is
  built to run on Linux as easily, with the same Wi-Fi joining). The Wi-Fi
  handling is one module with the same calls on both systems: join the
  profile, read whether the interface is on the WiCAN's SSID, read the
  network it is on, rejoin. It has a Windows backend (`netsh`) and a Linux
  backend (NetworkManager's `nmcli`), chosen by the host's system, with the
  same timeouts and results. Nothing else in the flasher branches on the
  system, and every other step (the checks, the upload, the reads, the
  verdict) is the same code on both. Each backend has its own tests, from
  recorded command output, and can-fails, and the Linux backend is shown on
  madhouse-debian joining and rejoining the real hotspot before the rig
  step's acceptance relies on it.
- **O5. The second-route preflight.** Before and after the join, a TCP
  connection to a public resolver (1.1.1.1:53 or 8.8.8.8:53) must succeed
  from a local address outside the hotspot's subnet, on the same path both
  times, recorded. So joining the hotspot never takes the PC's own network
  away, and the device is never reached through some other route. On Linux
  the saved profile is made with `ipv4.never-default yes` (the profile's
  setup, in `tools-reference.md`); the check catches a moved route either
  way.
- **O6. The device's state.** The device must answer `GET
  http://<host>/gen_inhibit` with its `elf_sha256` and `uptime_ms`, read
  leniently (trailing bytes tolerated and noted), and be in mode 0 (OFF):
  gen-inhibit's firmware refuses an OTA in any other mode (its spec 9.3, a
  403). **An armed WiCAN is disarmed when the bench is held** (user,
  2026-11-11): the flasher runs under the caller's hold of the bench lock
  (SPEC.md), and finding the device in a mode other than 0 it posts `POST
  /gen_inhibit_set?mode=0` once, then reads the page again. It goes on only
  if the page then reads mode 0, as gen-inhibit's `wican_reboot.py` does
  (never trusting the POST alone), and its record notes that the device
  came up armed and was disarmed. If the read-back is not mode 0, or the
  page can't be read, it stops with `recoverable` false. Called without the
  bench held, it refuses an armed device and disarms nothing. The flasher
  never reads `/check_status` or `/load_config`, which carry credentials.

## The upload and its verdict

- **O7. The upload** is `curl -F ota_file=@<bin> http://<host>/upload/ota.bin`
  with `--max-time 180`, its HTTP status, bytes sent and time recorded
  (curl's `000` is no status). curl ships with Windows 10 and Debian, so the
  proven path is kept.
- **O8. One verdict per upload, gen-inhibit's, unchanged.** After the
  upload the flasher reads the page every 2 s (strictly: it must parse) for
  up to 240 s after a 303 or no status, 30 s otherwise, and classifies:
  (a1) no status, no restart; (a2) no status, restarted, read on with the
  verdict that follows; (b) refused, a 4xx/5xx; (c) TOOK: the new image,
  one restart, up at least 90 s; (d) rolled back by the gate, the old image
  booted 60 s or more after the upload with reason SW; (d') rolled back
  after a crash; (e) the new image never ran, its one restart within 10 s;
  `other`, running neither image; `undecided`, the window closed first.
  **Only (c), including (a2)+(c), is a load.** The verdict is printed as
  `VERDICT: (<v>) <names> -- <detail>` and kept.
- **O9. What a verdict leaves, and `recoverable`.** (a1) and (b) leave the
  device up on its old image with no restart; (d), (d') and (e) leave it on
  its old image after one or two restarts: `recoverable` true, since the
  old image can be identified. `other` and `undecided` leave it unknown:
  `recoverable` false (user, 2026-11-11). Either way the runner stops the bench
  and its end pass identifies, as for any failed flash (SPEC.md section 6).
- **O10. A retry is the suite's policy, named on the image:**
  - `none`: the first verdict that is not (c) stops the bench. The
    charge-interposer's runs use this: they have one WiCAN image, so there
    is never another image to restore with.
  - `dual-slot`, gen-inhibit's, unchanged: the device's current image, if it
    is one of the suite's, is uploaded over itself, then the image is tried
    once more. An unreadable page, or no suite image matching it, is no
    restore and `recoverable` false; a failed restore is `recoverable`
    false, with no further WiCAN flash in the run; a second failure stops
    the run; every attempt's VERDICT line is kept.
- **O11. Where it leaves the PC.** On the hotspot, so the suite's identify
  and device tests can follow, with the interface's network before the
  join recorded. After any WiCAN restart the interface may have moved to
  another known network, so "joined" is always re-checked as O4 says, never
  taken from "connected". During the watch, a failed page read rejoins
  (20 s), and a failed rejoin is a missed reading, bounded by the watch's
  own window, as today (user, 2026-11-11).

## Records and the parts that never decide

- **O12. The record and the capture.** Each upload's record (the state
  before, the readings, the boots, the verdict and the upload's figures),
  SSID masked, goes in the run's log folder. The optional Wi-Fi capture
  (`dumpcap`/`tshark`, gen-inhibit's `bench_flash_wire`) moves too and stays
  message-only: it never reaches a verdict, every call into it is guarded,
  and a missing tool or capture right (Linux: the `wireshark` group) is
  noted, not refused (user, 2026-11-11).

## Not in this flasher

- **gen-inhibit's guard scripts** (`bench_ota_guard.py`,
  `bench_flash_guard.py`) test gen-inhibit's firmware (its spec 12.4 row
  17, 5.1 item 3); they stay gen-inhibit's. Only the helpers they share with
  the flasher move here (the HTTP read with a connection reset as no
  status, the external-route check, the page read lenient and strict, the
  device identity), and the guards import them from the pinned
  `shared/bench` (user, 2026-11-11). gen-inhibit's `test_bench_guards_page` then
  covers those helpers through the guards, and the helpers get tests of
  their own here.
- **The hotspot's Wi-Fi adapter on madhouse-debian** is the rig step's (port
  plan step 3: a USB Wi-Fi adapter for the WiCAN's hotspot). This flasher
  needs one within the WiCAN's range on whichever host runs the bench.
</content>
</invoke>
