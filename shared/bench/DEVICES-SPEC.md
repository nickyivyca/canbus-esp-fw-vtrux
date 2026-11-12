# Bench devices: specification

Approved by the user 2026-11-11, in the gen-inhibit reviewer's session,
which drafted it; read by the charge-interposer reviewer before merge
(shared code rules, AGENTS.md "Shared code"). It covers bench devices other
than the lock (`SPEC.md`) and the flasher (`FLASH-SPEC.md`): how serial
devices are found, and the bench's programmable 12 V supply. Both are for
port plan step 3, when the bench moves to madhouse-debian
(`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`).
Named `RIG-SPEC.md` until 2026-11-11 (renamed by the user's ruling).

## 1. Serial devices are found by device ID

- **S1.** Every serial device the bench tooling opens (the X2's esptool and
  slcan ports, the supply, any USB-serial adapter) is named in a suite's
  local settings by its USB vendor ID, product ID and serial number, with
  the interface number on a composite device. The tooling resolves the
  port when it opens it, through `/dev/serial/by-id/`. No tty name is
  written in settings or code. The lookup is Linux only and refuses on
  Windows (user, 2026-11-11: new bench hardware tooling is Linux-only);
  existing tooling that already runs on Windows, such as the esptool
  flasher, keeps its COM-port setting there.
- **S2.** No match, or more than one, refuses, naming every device that was
  found. The tooling never guesses.
- **S3.** The run record keeps the port resolved and the identifiers
  matched. Serial numbers are bench identifiers: they live in local
  settings and run records, never in git.

## 2. The bench supply

A Korad KA3005P, controlled over USB, replaces the fixed supply feeding the
boards' 12 V (user, 2026-11-11). Every rule below is enforced by code that
sessions and the bench scripts they edit cannot change or bypass.

- **K1. Pinned.** The supply driver, the protection service (K2) and its
  client library are `shared/bench` code. The service runs from a pinned
  `shared/main` tag, installed on the bench host by the user, read-only.
  A client whose protocol version is not the service's is refused.
- **K2. One owner.** Only the protection service opens the supply. It runs
  as a system service under its own user; the supply's device belongs to
  that user alone (a udev rule matching S1's identifiers), and the service
  holds the port open exclusively. Sessions run as other users and cannot
  open it. Every other caller (bench runs, a session's hand work, any later
  wrapper) is a client of the service, over a local socket, through the
  client library. The service finds the supply per S1 and confirms it by
  its `*IDN?` answer; anything else is refused.
- **K3. Limits, in two tiers.** The absolute limits are in the service's
  configuration, owned by root and set by the user: 14.5 V and 200 mA
  (user, 2026-11-11: the boards' draw pulses from about 23 to 50 mA, and a
  100 mA limit was too low before). Each suite also states its own maximum
  voltage and current in committed code, at or below the absolute ones. A
  request above either tier is rejected before anything is sent to the
  supply, and the rejection is logged.
- **K4. Every write is read back.** After each setpoint, current limit or
  output change the service reads the setting back. A readback that
  differs from the request by more than the supply's resolution (10 mV,
  1 mA), a garbled or missing answer, or a timeout makes the service turn
  the output off, read that back, and latch a fault (K6).
- **K5. The output is watched.** While the output is on, the service polls
  the measured voltage and current at the rate step 3's
  characterisation sets. A measured voltage above the commanded voltage
  plus a tolerance, or above the absolute limit, is taken as a fault in the
  supply or on its line: output off, read back, and a latched fault (K6).
  For example, 14 V commanded and 20 V measured.
- **K6. A latched fault** refuses every command but reads until the user
  clears it by restarting the service (user, 2026-11-11). No session can
  clear it. Each fault is written to the service's log and to the record
  of the run or hand take that held the bench.
- **K7. Only the bench's holder writes** (user, 2026-11-11). Each client
  request carries its role and process ID. The service accepts a write only
  when the bench lock (`SPEC.md`) shows that role and process holding the
  bench. Reads are open to every client.
- **K8. Sequencing** (user, 2026-11-11, with the charge-interposer
  reviewer's review).
  - A run never assumes the output is off. At its start it turns the output
    off, read back, then sets its setpoint and current limit, each read
    back, then turns it on, before it identifies any board. The measured
    voltage must settle within tolerance.
  - A case that needs a power cycle (a power-up or cold-boot test) or a
    setpoint other than the one in force gets the same: off, its setpoint
    and current limit, on, each read back and settled. Every other case
    keeps the output on at the setpoint in force, read back at the case's
    entry. Which cases need a cycle or their own setpoint is each suite's,
    in committed code.
  - Within a case a test may step the setpoint with the output on (for
    example a brownout), each step read back and within the limits.
  - At a run's end, and at a hand release, the output is turned off and
    read back. Off is the supply's idle state: the 12 V feed is off while
    nobody holds the bench. A board may stay powered another way (an X2 with
    its USB cable in), so a run never assumes a board was reset because the
    supply went off.
- **K8a. The service turns the output off itself** (user, 2026-11-11, on
  the gen-inhibit tester's question). It watches the bench lock, and when
  the lock no longer shows the holder that turned the output on (a
  release, a `clear` of a dead holder, a `lost`, or another role's take),
  it turns the output off and reads that back, so a crashed holder or a
  skipped step cannot leave the 12 V feed on. A run's pause (`free_paused`)
  is the exception: the output stays as it is, as a paused run leaves its
  boards as they are (`SPEC.md`). Another role's take during the pause
  turns it off like any other take (user, 2026-11-11, on the
  charge-interposer reviewer's review). At the resume the runner reads the
  output back; if it is not as the paused run left it (on, at its
  setpoint), the runner cycles it as at a run's start (K8) and identifies
  the boards again before the next case.
- **K8b. The supply's own protection.** The service can set the KA3005P's
  built-in over-voltage and over-current protection and read each back.
  Whether it does is set in the root-owned configuration after step 3's
  characterisation shows how this unit's protection behaves, for example
  whether over-current cuts the output instead of limiting the current
  (user, 2026-11-11). The software limits (K3), readback (K4) and watch
  (K5) apply either way.
- **K9. Recorded.** Every command, readback and measurement goes into the
  service's log and into the record of the run or hand take, with the time
  it was read.
- **K10. Bring-up acceptance,** part of step 3's characterisation,
  each item recorded:
  - a request above the absolute limit (20 V against 14.5 V) is rejected
    and never reaches the supply;
  - a request above a suite's own limit is rejected;
  - a second process cannot open the supply's port;
  - a forced readback mismatch, and a garbled answer, each turn the output
    off and latch a fault that only a service restart clears;
  - a client that does not hold the bench cannot write;
  - the output goes off, read back, when the holder that turned it on
    releases, is cleared or another role takes (during a pause too), and
    stays as it is at a pause otherwise; a resume that finds it changed
    cycles it and identifies the boards again (K8a);
  - how the supply's own over-voltage and over-current protection behave,
    so the user can set K8b;
  - the polling rate and settle tolerance are measured and set;
  - the 200 mA limit is shown to cover both boards together at their
    peaks (the WiCAN's Wi-Fi transmitting, as in an OTA upload over its
    hotspot, and an X2 booting), before a current-limit trip can be read as
    a board fault (charge-interposer reviewer's review, user 2026-11-11).

## Ruled

- **No MCP server for the supply** (user, 2026-11-11). The protection
  comes from the service owning the device (K2); an MCP server would add a
  process without adding protection. A thin one over the client library
  can come later if interactive power work needs it.
