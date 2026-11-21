# Installing the bench supply's protection service

**Owner:** tester (gen-inhibit), 2026-11-21. Written for the gen-inhibit
reviewer to put to the user. The user does every step, as root, on the
bench host (madhouse-debian). Sessions cannot do any of them, and
DEVICES-SPEC K1-K2 says they must not be able to.

What it installs: `vtrux_bench/supply/service.py` (DEVICES-SPEC K1-K9),
from a pinned `shared/main` tag, running as a system service under its own
user. That user alone owns the KA3005P's port. Every other caller reaches
the supply through the service's socket with `vtrux_bench/supply/client.py`.

`<tag>` below is the `shared/main` tag the reviewer names at merge.
`<vid>`, `<pid>` and `<serial>` are the supply's USB IDs. They are bench
identifiers: they go in the udev rule and the configuration on the bench
host, and never into git (DEVICES-SPEC S3).

## 1. The pinned code (K1)

```
git -C <anchor clone> fetch origin
git -C <anchor clone> worktree add --detach /opt/vtrux-supply/shared-<tag> <tag>
chown -R root:root /opt/vtrux-supply
chmod -R a-w /opt/vtrux-supply/shared-<tag>/shared
```

The service runs with `-B` and `PYTHONDONTWRITEBYTECODE=1`, so nothing is
written into the checkout. To move to a newer tag, make a new checkout
beside this one, point the unit's `PYTHONPATH` at it, and restart the
service. A client of another protocol version is refused (K1).

## 2. The service's user and the sessions' group (K2)

```
groupadd --system vtrux-bench
useradd --system --no-create-home --shell /usr/sbin/nologin \
        --gid vtrux-bench vtrux-supply
usermod -aG vtrux-bench <the sessions' user>
```

- `vtrux-supply` runs the service and owns the supply's port.
- `vtrux-bench` is the service's group. The socket is `0660` in that group,
  so the sessions' user can connect. The port itself stays `0600`
  `vtrux-supply`, so being in the group does not open the supply.

The sessions' user must not be put in `vtrux-supply`. The sessions need no
sudo rule for this service. The only sudo rule today is `canctl`, and it
must stay the only one.

## 3. The udev rule (K2, S1)

Read the supply's IDs with the supply plugged in:

```
PYTHONPATH=/opt/vtrux-supply/shared-<tag>/shared/bench \
  python3.13 -B -m vtrux_bench.serial_dev
```

Then write `/etc/udev/rules.d/90-vtrux-supply.rules`, with the IDs filled in:

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="<vid>", ATTRS{idProduct}=="<pid>", ATTRS{serial}=="<serial>", OWNER="vtrux-supply", GROUP="vtrux-supply", MODE="0600", ENV{ID_MM_DEVICE_IGNORE}="1"
```

Then apply it and check the result:

```
udevadm control --reload
udevadm trigger --subsystem-match=tty
ls -lL /dev/serial/by-id/      # the supply's link: crw------- vtrux-supply
```

`ID_MM_DEVICE_IGNORE` keeps ModemManager from probing the port.

## 4. The configuration (K3, K5, K8, K8b)

Write `/etc/vtrux-supply/config.json`, owned by `root:root`, mode `0644`.
The service refuses any other owner, and a file writable by group or
others:

```json
{
  "device": {"vid": "<vid>", "pid": "<pid>", "serial": "<serial>", "interface": 0},
  "idn_prefix": "KORAD KA3005P",
  "absolute": {"volts": 14.5, "amps": 0.2},
  "socket": "/run/vtrux-supply/supply.sock",
  "socket_mode": "0660",
  "log": "/var/log/vtrux-supply/service.jsonl",
  "journals": "/var/log/vtrux-supply/journals",
  "bench_log": "<REVERSE_IT_DIR>/firmware/bench-log.jsonl",
  "poll_s": 0.5,
  "lock_poll_s": 1.0,
  "over_tolerance_v": 0.5,
  "settle_tolerance_v": 0.3,
  "settle_timeout_s": 3.0,
  "protection_bits": {"ovp": null, "ocp": null}
}
```

- `absolute` holds the user's limits (K3; user, 2026-11-11).
- `interface` is the one `serial_dev` lists for the supply. If the supply has
  only one interface, the key may be left out.
- **Provisional values, for bring-up only.** K10 measures `poll_s`,
  `over_tolerance_v`, `settle_tolerance_v` and `settle_timeout_s`, and the
  user then sets them:
  - `poll_s` is K5's polling rate.
  - `over_tolerance_v` is K5's margin above the commanded voltage.
  - `settle_tolerance_v` and `settle_timeout_s` are K8's settle check.
  - `lock_poll_s` is how often the bench log is read for K7 and K8a.
- `protection_bits` names the STATUS? bit that reports the supply's own
  OVP and OCP, once K10 shows which ones do. While both are `null`, the
  service refuses to set either protection (K8b; the gen-inhibit reviewer's
  reading, 2026-11-21).
- The suite tier of K3 is not in this file. Its source awaits the user's
  ruling.

## 5. Reading the bench log (K7, K8a)

The service reads the bench log, and never writes it. On madhouse-debian
every folder on the way to `$REVERSE_IT_DIR/firmware/bench-log.jsonl` is
readable by others, except the home folder, which is `0700`. Give the
service's user passage through that folder only, with no listing:

```
setfacl -m u:vtrux-supply:x <the sessions' user's home folder>
sudo -u vtrux-supply head -c 0 <REVERSE_IT_DIR>/firmware/bench-log.jsonl && echo readable
```

## 6. The unit

Write `/etc/systemd/system/vtrux-supply.service`:

```
[Unit]
Description=Vtrux bench supply protection service (DEVICES-SPEC K2)

[Service]
User=vtrux-supply
Group=vtrux-bench
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONPATH=/opt/vtrux-supply/shared-<tag>/shared/bench
ExecStart=/usr/bin/python3.13 -B -m vtrux_bench.supply.service --config /etc/vtrux-supply/config.json
RuntimeDirectory=vtrux-supply
RuntimeDirectoryMode=0750
LogsDirectory=vtrux-supply
LogsDirectoryMode=0755
Restart=no
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

Then start it and check it:

```
systemctl daemon-reload
systemctl enable --now vtrux-supply
systemctl status vtrux-supply
```

- **`Restart=no` is required.** A restart clears a latched fault (K6), so a
  service that restarted itself would clear faults without the user.
- The service refuses to start if:
  - the configuration is not root's;
  - no device, or more than one, matches the IDs (S2);
  - the port cannot be opened exclusively;
  - the device's `*IDN?` answer does not start with `idn_prefix`.

  The reason is in `journalctl -u vtrux-supply`.
- At its start, the service turns off an output it finds on, and reads that
  back. It does the same when it stops.

## 7. Clearing a fault (K6)

```
systemctl restart vtrux-supply
```

Only the user does this. The fault is in `/var/log/vtrux-supply/service.jsonl`,
and in the journal of the holder that held the bench, under
`/var/log/vtrux-supply/journals/`.

## 8. Checks after installing

Run these from a session's shell:
- The sessions' user cannot open the port: `head -c 0 /dev/serial/by-id/<the
  supply's link>` gives Permission denied.
- The service answers:

  ```
  PYTHONPATH=<a shared/bench at the tag> python3.13 -B -c "from vtrux_bench.supply.client import Supply; import os; print(Supply('tester (gen-inhibit)', os.getpid()).hello())"
  ```

- K10's bring-up items then run on the real supply, under the bench lock,
  each recorded (DEVICES-SPEC K10).

## Things K10 must look at on this unit

Each of these is about this unit, and none has been checked on it yet:
- Whether STATUS? reports OVP and OCP, and in which bits (K8b).
- Whether ISET1? answers with exactly three decimals. The driver refuses
  any other answer as garbled, and that trips (K4).
- Whether the protocol's gaps (`ka3005p.GAP_S`, `READ_GAP_S`, `TIMEOUT_S`)
  suit this unit.
