#!/usr/bin/env python3
"""Record the baseline's images (part C of baseline_run_list.md): one JSON row per
image, from the image file itself, never from its name.

    py -3.14 tools/gen_inhibit/bench/baseline_images_record.py \
        --out runs/baseline_c65c45e/images_c65c45e.json label=<dir containing the .bin> [...]

Per image: the app descriptor (version, project_name, time, date, idf_ver and
app_elf_sha256, read by image_elf_sha.py), the file's size, md5 and sha256, the
SHA-256 of the .elf beside it and whether the descriptor carries it, the sha256
of the build's sdkconfig, and the prod record if one is beside the image. A
directory with no image or with two is a FAIL row, not a guess. Paths are
recorded as the directory's basename only.
"""
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import image_elf_sha as M      # noqa: E402


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def record(label, d):
    bins = sorted(glob.glob(os.path.join(d, "wican-fw_obd_*.bin")))
    if len(bins) != 1:
        return {"label": label, "dir": os.path.basename(d), "ok": False,
                "why": "%d images in the directory" % len(bins)}
    r = M.read_file(bins[0])
    r["path"] = os.path.basename(bins[0])
    elf = os.path.splitext(bins[0])[0] + ".elf"
    r["elf_file_sha256"] = sha256(elf) if os.path.exists(elf) else None
    r["descriptor_equals_elf_sha256"] = r["elf_file_sha256"] == r["elf_sha256"]
    cfg = os.path.join(d, "config", "sdkconfig.json")
    r["sdkconfig_json_sha256"] = sha256(cfg) if os.path.exists(cfg) else None
    prod = bins[0] + ".prod.json"
    r["prod_record"] = json.load(open(prod, encoding="utf-8")) if os.path.exists(prod) else None
    r.update(label=label, dir=os.path.basename(d), ok=r["descriptor_equals_elf_sha256"])
    return r


def main(argv):
    out = argv[argv.index("--out") + 1]
    pairs = [a.split("=", 1) for a in argv if "=" in a and not a.startswith("--")]
    rows = [record(k, v) for k, v in pairs]
    with open(out, "w", encoding="ascii") as fh:
        json.dump(rows, fh, indent=1)
    for r in rows:
        print("%-9s %-5s %-36s md5 %s  elf_sha256 %s  %s %s" % (
            r["label"], "ok" if r["ok"] else "FAIL", r.get("project_name", r.get("why")),
            (r.get("file_md5") or "-")[:8], (r.get("elf_sha256") or "-")[:8],
            r.get("date", ""), r.get("time", "")))
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
