"""Strip device credentials out of console captures.

WHY. The WiCAN prints its whole config.json to the console at boot, twice -- once
as the raw file and once key by key through config_server_load_cfg. That dump
contains the AP password, the station Wi-Fi password, the MQTT password and the
battery-alert MQTT password, in clear. A console capture is therefore a
credential file, and this folder syncs through SeaDrive to every machine that
touches the project.

This was found on 2026-09-28 in four console logs written the same day, by the
reviewing session rather than by the session that wrote them, which is the point:
nothing about capturing a boot log suggests you are about to write passwords to a
synced folder, so the redaction has to be automatic rather than remembered.

Both shapes the firmware emits are handled:

    "sta_pass":"...."                      -- the raw config.json line
    device_config.sta_pass: ....           -- the per-key load_cfg lines

Every key whose name ends in `pass` is redacted, so a key added to the firmware
later is covered without editing this list. Used as a filter by
serial_boot_log.py, and as a CLI to clean captures written before it existed:

    py -3.11 redact_secrets.py runs/some_console.log [...]

Rewrites in place and reports what changed. Redacting is not reversible, which is
the intent -- the captures are kept for their boot sequence and timing, and the
credentials were never the evidence.
"""

import io
import os
import re
import sys

# "any_key_ending_in_pass":"value"
_JSON_PASS = re.compile(r'("(?:\w*?pass)"\s*:\s*")([^"]*)(")')
# device_config.any_key_ending_in_pass: value   (to end of line)
_LOG_PASS = re.compile(r'(device_config\.\w*?pass:\s*)(\S[^\r\n]*)')

PLACEHOLDER = "[REDACTED]"


def redact(text):
    """Return `text` with every password value replaced. Safe on partial lines."""
    text = _JSON_PASS.sub(lambda m: m.group(1) + PLACEHOLDER + m.group(3), text)
    text = _LOG_PASS.sub(lambda m: m.group(1) + PLACEHOLDER, text)
    return text


def redact_file(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        before = fh.read()
    after = redact(before)
    if after == before:
        return 0
    n = after.count(PLACEHOLDER) - before.count(PLACEHOLDER)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(after)
    return n


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    total = 0
    for p in argv:
        if not os.path.isfile(p):
            print("skip (not a file): %s" % p)
            continue
        n = redact_file(p)
        total += n
        print("%-48s %s" % (os.path.basename(p),
                            ("%d redacted" % n) if n else "clean"))
    print("total %d value(s) redacted" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
