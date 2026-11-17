#!/usr/bin/env python3
"""One OTA upload of a WiCAN image through the pinned core's flasher, for the harness's own
OTAs inside a check (ota_pcap.sh: the OTA group's steps after its first, whose image the
chain runner did not load).

Gen-inhibit tester, 2026-11-16, the gen-inhibit reviewer's job (1). It replaces
bench_flash.py --flash --record in ota_pcap.sh: the same one preflight and upload
(vtrux_bench.run.ota.flash_once, which is bench_flash's main() with --flash, FLASH-SPEC
O1-O12), joined through the Wi-Fi backend the machine's settings choose (gi_wifi:
GI_OTA_SETTINGS, the settings.ota block), never netsh or nmcli of its own.

  - No retry: one upload, as ota_pcap.sh's bench_flash did (an OTA that is not (c) TOOK
    stops the OTA group; reviewer, 2026-10-16).
  - No hold is handed to the flasher, so an armed device is refused and nothing is
    disarmed (O7: a disarm needs the caller's own check of the hold, which a check's
    subprocess does not have), as bench_flash refused it.
  - The image's identity is its own descriptor's app_elf_sha256 (O3: gen-inhibit's
    images are built per run and carry no pin).
  - The console, the SSID masked by the core, goes to stdout; the record to --record; the
    core's optional capture to the record's name with .pcapng.

    python3.13 -B gi_ota.py --bin <image .bin> --record <stem>.json
Exit 0 a load ((c) TOOK and the strict read parsed), 1 not, 2 refused before anything was
sent.
"""
import argparse
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_wifi as GW     # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--record", required=True)
    a = ap.parse_args(argv)
    try:
        cfg = GW.from_env()
        OTA, W = GW.core()
    except GW.Refused as e:
        print("REFUSED: %s (nothing sent)" % e)
        return 2
    expect = OTA.image_elf_sha256(a.bin) if os.path.isfile(a.bin) else None
    args = types.SimpleNamespace(
        bin=a.bin, host=cfg["host"], ssid=cfg["ssid"], profile=cfg["profile"], iface=cfg["iface"],
        expect=expect or "", pin={}, flash=True, status=False, record=a.record,
        no_capture=not cfg["capture"], capture_iface=cfg["capture_iface"], echo=False)
    W.choose(OTA.wifi_backend(cfg))
    try:
        res = OTA.flash_once(args, None)
    finally:
        W.choose(None)
    sys.stdout.write(OTA.mask(res["console"], cfg["ssid"]))
    print("\n%s backend; exit %d" % (cfg["wifi"], res["rc"]))
    return 0 if res["rc"] == 0 else (1 if res.get("sent") else 2)


if __name__ == "__main__":
    sys.exit(main())
