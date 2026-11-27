"""Tests of the pins on outside images (vtrux_pins.images): SPEC R36.

    python -B shared/tools/tests/test_vtrux_pins_images.py
    python -B shared/tools/tests/test_vtrux_pins_images.py --can-fail

The images are synthetic (imagekit), so no firmware image is committed.
Each test writes a pin of two images, each with a bootloader, a partition
table and an app, and a source folder that holds their files.

--can-fail runs this file against edited copies of vtrux_pins/images.py
(MUTANTS), with test_vtrux_pins_packages.py's runner, and passes only if the
unedited copy passes and every edited one fails.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
UNDER_TEST = os.environ.get("VTRUX_PINS_UNDER_TEST")
CODE = UNDER_TEST or TOOLS
sys.path.insert(0, HERE)
sys.path.insert(0, CODE)

import imagekit                                  # noqa: E402
import scratch                                   # noqa: E402
from vtrux_build.image import AppImage           # noqa: E402
from vtrux_pins import images                    # noqa: E402
from vtrux_pins.images import Mismatch, Refused  # noqa: E402

PIN_ID = "abc123_man"


def app_bytes(version, code=b"\x11\x22\x33"):
    data, _ = imagekit.make_image(
        segments=[(0x3C000020, code * 50), (0x40380000, bytes(range(256)))],
        desc=imagekit.descriptor(version=version))
    return data


def entry(offset, name, data, app=False):
    e = {"offset": offset, "name": name, "size": len(data),
         "sha256": hashlib.sha256(data).hexdigest(),
         "md5": hashlib.md5(data).hexdigest()}
    if app:
        e["app"] = True
    return e


class Fixture(unittest.TestCase):

    def setUp(self):
        self.base = scratch.fresh(self.id().rsplit(".", 1)[-1])
        self.src = os.path.join(self.base, "source")
        self.dir = os.path.join(self.base, "images")
        os.makedirs(self.src)
        os.makedirs(self.dir)
        boot = b"bootloader" * 30
        part = b"partitions" * 20
        self.blobs = {"bootloader.bin": boot, "partitions.bin": part,
                      "first.bin": app_bytes("first"),
                      "second.bin": app_bytes("second", b"\x44\x55\x66")}
        for name, data in self.blobs.items():
            scratch.write(os.path.join(self.src, name), data)
        self.pin = {
            "_comment": "a test pin",
            "pin": PIN_ID,
            "source": {"project": "gen-inhibit", "commit": "a" * 40,
                       "variant": "_man"},
            "images": {}}
        for iname, app in (("one", "first.bin"), ("two", "second.bin")):
            a = AppImage(self.blobs[app])
            self.pin["images"][iname] = {
                "image_hash": a.image_hash, "code_hash": a.code_hash,
                "built": "recorded, not judged",
                "files": [entry("0x10000", app, self.blobs[app], app=True),
                          entry("0x0", "bootloader.bin", boot),
                          entry("0x8000", "partitions.bin", part)]}
        self.pin["images"]["one"]["identity"] = \
            self.pin["images"]["one"]["image_hash"][:8]
        self.pin_file = os.path.join(self.base, "pin.json")
        self.write_pin()

    def write_pin(self, pin=None):
        with open(self.pin_file, "w", encoding="utf-8") as fh:
            json.dump(self.pin if pin is None else pin, fh)

    def place(self, iname, name, data=None):
        p = os.path.join(self.dir, PIN_ID, iname, name)
        scratch.write(p, self.blobs[name] if data is None else data)
        return p

    def place_all(self):
        for iname, im in self.pin["images"].items():
            for fe in im["files"]:
                self.place(iname, fe["name"])

    def edited(self, fn):
        pin = copy.deepcopy(self.pin)
        fn(pin)
        return pin

    def refused(self, pin, *words):
        self.write_pin(pin)
        with self.assertRaises(Refused) as cm:
            images.read_pin(self.pin_file)
        for w in words:
            self.assertIn(w, str(cm.exception))

    def mismatch(self, *words, fn=None):
        with self.assertRaises(Mismatch) as cm:
            (fn or (lambda: images.load(self.pin_file, self.dir)))()
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)


class ReadPin(Fixture):

    def test_the_pin_is_read_with_the_sha256_of_its_bytes(self):
        pin, sha = images.read_pin(self.pin_file)
        self.assertEqual(pin, self.pin)
        with open(self.pin_file, "rb") as fh:
            self.assertEqual(sha, hashlib.sha256(fh.read()).hexdigest())

    def test_other_keys_are_not_judged(self):
        def more(p):
            p["extra"] = [1]
            p["source"]["built"] = {"any": "thing"}
            p["images"]["two"]["flash"] = ["--flash_mode", "dio"]
        self.write_pin(self.edited(more))
        images.read_pin(self.pin_file)

    def test_a_pin_that_cannot_be_read_is_refused(self):
        os.remove(self.pin_file)
        with self.assertRaises(Refused):
            images.read_pin(self.pin_file)
        with open(self.pin_file, "w") as fh:
            fh.write("{")
        with self.assertRaises(Refused):
            images.read_pin(self.pin_file)
        self.refused([1], "not a JSON object")

    def test_the_id_source_and_images_are_required(self):
        for bad in ("a/b", "..", ".", "", 1, None):
            self.refused(self.edited(lambda p: p.update(pin=bad)), "id")
        self.refused(self.edited(lambda p: p.pop("source")), "no source")
        self.refused(self.edited(lambda p: p["source"].pop("project")),
                     "no project")
        self.refused(self.edited(lambda p: p["source"].update(project="")),
                     "no project")
        for bad in ("a" * 39, "A" * 40, None):
            self.refused(self.edited(lambda p: p["source"].update(commit=bad)),
                         "source.commit")
        self.refused(self.edited(lambda p: p.update(images={})), "no images")
        self.refused(self.edited(lambda p: p.update(images=[])), "no images")

    def test_each_image_needs_a_plain_name_and_both_hashes(self):
        self.refused(self.edited(lambda p: p["images"].update(
            {"a/b": p["images"]["two"]})), "image a/b")
        self.refused(self.edited(lambda p: p["images"].update(two=[])),
                     "not a JSON object")
        for key in ("image_hash", "code_hash"):
            self.refused(self.edited(lambda p: p["images"]["two"].pop(key)),
                         key)
            for bad in ("a" * 63, "A" * 64, 1):
                self.refused(self.edited(
                    lambda p: p["images"]["two"].update({key: bad})), key)

    def test_an_identity_when_given(self):
        one = self.pin["images"]["one"]
        self.refused(self.edited(
            lambda p: p["images"]["one"].update(identity="")), "identity")
        self.refused(self.edited(
            lambda p: p["images"]["one"].update(identity=7)), "identity")
        wrong = "%08x" % ((int(one["image_hash"][:8], 16) + 1) & 0xFFFFFFFF)
        self.refused(self.edited(
            lambda p: p["images"]["one"].update(identity=wrong)),
            "first 8 digits")
        self.write_pin(self.edited(
            lambda p: p["images"]["one"].update(identity="slcan:V0101")))
        images.read_pin(self.pin_file)

    def test_each_file_entry_is_checked(self):
        def f(**kw):
            return self.edited(
                lambda p: p["images"]["two"]["files"][1].update(**kw))
        self.refused(self.edited(
            lambda p: p["images"]["two"].update(files=[])), "no files")
        self.refused(self.edited(
            lambda p: p["images"]["two"].pop("files")), "no files")
        self.refused(f(extra=1), "its keys")
        self.refused(self.edited(
            lambda p: p["images"]["two"]["files"][1].pop("md5")), "its keys")
        for bad in ("0X8000", "8000", "0x8000 ", 0x8000):
            self.refused(f(offset=bad), "offset")
        for bad in ("sub/partitions.bin", "..", "", "a b"):
            self.refused(f(name=bad), "name")
        for bad in (0, -1, True, "3072", 1.5):
            self.refused(f(size=bad), "size")
        self.refused(f(sha256="0" * 63), "sha256")
        self.refused(f(md5="0" * 31), "md5")
        self.refused(f(app=False), "app")
        self.refused(f(app=True), "exactly one file must be the app, not 2")
        self.refused(self.edited(
            lambda p: p["images"]["two"]["files"][0].pop("app")),
            "exactly one file must be the app, not 0")
        # files[1] is the bootloader at 0x0; files[2] the partition table.
        self.refused(self.edited(lambda p: p["images"]["two"]["files"][2]
                                 .update(offset="0x0")), "twice")
        self.refused(self.edited(lambda p: p["images"]["two"]["files"][2]
                                 .update(name="bootloader.bin")), "twice")


class Load(Fixture):

    def test_every_file_matching_passes_in_flash_order(self):
        self.place_all()
        got = images.load(self.pin_file, self.dir)
        self.assertEqual(got["pin"], PIN_ID)
        self.assertEqual(got["pin_sha256"],
                         images.read_pin(self.pin_file)[1])
        two = got["images"]["two"]
        d = os.path.join(self.dir, PIN_ID, "two")
        self.assertEqual(two["files"], [
            ["0x0", os.path.join(d, "bootloader.bin")],
            ["0x8000", os.path.join(d, "partitions.bin")],
            ["0x10000", os.path.join(d, "second.bin")]])
        self.assertEqual(two["app"], ["0x10000", os.path.join(d, "second.bin")])
        self.assertIsNone(two["identity"])
        self.assertEqual(got["images"]["one"]["identity"],
                         self.pin["images"]["one"]["image_hash"][:8])
        self.assertEqual(two["code_hash"],
                         self.pin["images"]["two"]["code_hash"])

    def test_the_folder_must_exist(self):
        self.mismatch("not a folder",
                      fn=lambda: images.load(self.pin_file,
                                             os.path.join(self.base, "no")))
        self.mismatch("not a folder", fn=lambda: images.load(self.pin_file, ""))

    def test_a_file_only_at_the_folders_top_is_missing(self):
        self.place_all()
        os.remove(os.path.join(self.dir, PIN_ID, "two", "partitions.bin"))
        scratch.write(os.path.join(self.dir, "partitions.bin"),
                      self.blobs["partitions.bin"])
        self.mismatch("two:", "partitions.bin is missing")

    def test_other_bytes_are_refused(self):
        self.place_all()
        p = os.path.join(self.dir, PIN_ID, "one", "partitions.bin")
        data = self.blobs["partitions.bin"]
        for bad in (data[:-1] + b"X", data + b"\0", data[:-1]):
            scratch.write(p, bad)
            self.mismatch("one:", "is not the pinned file")
        scratch.write(p, data)
        # Each digest is compared on its own: a pin that differs from the
        # file in one of them only.
        for key, bad in (("size", len(data) + 1), ("sha256", "0" * 64),
                         ("md5", "0" * 32)):
            self.write_pin(self.edited(
                lambda q: q["images"]["one"]["files"][2].update({key: bad})))
            self.mismatch("one:", "is not the pinned file")

    def test_the_apps_image_hash_is_compared(self):
        self.place_all()
        self.write_pin(self.edited(lambda p: p["images"]["two"].update(
            image_hash="0" * 64)))
        self.mismatch("two: the app's image hash")

    def test_the_apps_code_hash_is_compared(self):
        # A pin taken from another build of other code: the files' bytes
        # match the pin, but its code hash is not theirs.
        self.place_all()
        other = AppImage(app_bytes("first", b"\x77\x88\x99")).code_hash
        self.assertNotEqual(other, self.pin["images"]["one"]["code_hash"])
        self.write_pin(self.edited(lambda p: p["images"]["one"].update(
            code_hash=other)))
        self.mismatch("one: the app's code hash")

    def test_an_app_that_does_not_parse_is_refused(self):
        def swap(p):
            for fe in p["images"]["two"]["files"]:
                if fe["name"] == "bootloader.bin":
                    fe["app"] = True
                else:
                    fe.pop("app", None)
        self.place_all()
        self.write_pin(self.edited(swap))
        self.mismatch("two:", "does not parse as an ESP-IDF app image")


class Install(Fixture):

    def install(self, sources):
        out = []
        n, got = images.install(self.pin_file, self.dir, sources, out.append)
        return n, got, out

    def test_every_file_is_copied_then_checked(self):
        n, got, out = self.install([self.src])
        self.assertEqual(n, 6)
        self.assertEqual(sorted(got["images"]), ["one", "two"])
        self.assertIn("copied    two/second.bin", out)
        n, _, out = self.install([self.src])
        self.assertEqual(n, 0)
        self.assertIn("in place  one/first.bin", out)
        left = [f for _, _, fns in os.walk(self.dir) for f in fns
                if f.endswith(".partial")]
        self.assertEqual(left, [])

    def test_the_first_source_holding_the_bytes_is_taken(self):
        a = os.path.join(self.base, "a")
        scratch.write(os.path.join(a, "partitions.bin"), b"other bytes")
        scratch.write(os.path.join(a, "sub", "first.bin"),
                      self.blobs["first.bin"])
        n, _, _ = self.install([a, self.src])
        self.assertEqual(n, 6)

    def test_sub_folders_are_not_searched(self):
        a = os.path.join(self.base, "a")
        for name, data in self.blobs.items():
            scratch.write(os.path.join(a, "sub", name), data)
        self.mismatch("no --source folder holds it",
                      fn=lambda: self.install([a]))
        self.assertEqual(os.listdir(self.dir), [])

    def test_other_bytes_in_place_are_never_overwritten(self):
        p = self.place("two", "second.bin", b"other bytes")
        self.mismatch("in place with other bytes", "not overwritten",
                      fn=lambda: self.install([self.src]))
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), b"other bytes")
        # planned before copying: nothing else was copied
        self.assertEqual(os.listdir(os.path.join(self.dir, PIN_ID)), ["two"])
        self.assertEqual(os.listdir(os.path.join(self.dir, PIN_ID, "two")),
                         ["second.bin"])

    def test_a_file_no_source_holds_copies_nothing(self):
        os.remove(os.path.join(self.src, "second.bin"))
        self.mismatch("two second.bin: no --source folder holds it",
                      fn=lambda: self.install([self.src]))
        self.assertEqual(os.listdir(self.dir), [])

    def test_the_sources_must_be_folders(self):
        with self.assertRaises(Refused):
            self.install([])
        with self.assertRaises(Refused):
            self.install([os.path.join(self.base, "none")])
        with self.assertRaises(Refused):
            images.install(self.pin_file, "", [self.src], lambda s: None)


class Cli(Fixture):

    def run_cli(self, *args):
        r = subprocess.run(
            [sys.executable, "-B", "-m", "vtrux_pins", "image"] + list(args),
            env=dict(os.environ, PYTHONPATH=CODE, PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, cwd=HERE)
        return r.returncode, r.stdout + r.stderr

    def test_the_command(self):
        common = ["--pin", self.pin_file, "--dir", self.dir]
        rc, out = self.run_cli("check", *common)
        self.assertEqual(rc, 1, out)
        self.assertIn("MISMATCH:", out)
        rc, out = self.run_cli("install", *common, "--source", self.src)
        self.assertEqual(rc, 0, out)
        self.assertIn("installed: 6 file(s) copied", out)
        rc, out = self.run_cli("check", *common)
        self.assertEqual(rc, 0, out)
        self.assertIn("pin %s (pin file sha256 %s)"
                      % (PIN_ID, images.read_pin(self.pin_file)[1]), out)
        self.assertIn("OK: every pinned file matches", out)
        self.assertEqual(self.run_cli("check", *common, "--source",
                                      self.src)[0], 2)
        self.assertEqual(self.run_cli("check", "--pin", self.pin_file)[0], 2)
        self.write_pin(self.edited(lambda p: p.pop("images")))
        rc, out = self.run_cli("check", *common)
        self.assertEqual(rc, 2, out)
        self.assertIn("REFUSED:", out)


MUTANTS = [(n, "images.py", o, r) for n, o, r in [
    ("the id may be any string",
     '    if not _plain(pin.get("pin")):\n',
     '    if not isinstance(pin.get("pin"), str):\n'),
    ("a name may hold a folder", r'NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")',
     r'NAME = re.compile(r"[A-Za-z0-9_./-]+\Z")'),
    ("source.commit not checked",
     '    if not _hex(HEX40, src.get("commit")):\n', '    if False:\n'),
    ("source.project not checked",
     '    if not (isinstance(src.get("project"), str) and src["project"]):\n',
     '    if False:\n'),
    ("the code hash optional",
     '        for k in ("image_hash", "code_hash"):\n',
     '        for k in ("image_hash",):\n'),
    ("an 8-hex identity not compared",
     '            if HEX8.match(ident) and not im["image_hash"].startswith('
     'ident):\n', '            if False:\n'),
    ("an unknown file key accepted",
     "not FILE_NEED <= set(fe) <= FILE_KEYS:\n", "not FILE_NEED <= set(fe):\n"),
    ("a bool size accepted",
     ' or isinstance(fe["size"], bool)', ""),
    ("app false accepted",
     '            if "app" in fe and fe["app"] is not True:\n',
     '            if False:\n'),
    ("two apps accepted", "        if apps != 1:\n", "        if apps < 1:\n"),
    ("an offset given twice accepted",
     '            if fe["offset"] in offs or fe["name"] in names:\n',
     '            if fe["name"] in names:\n'),
    ("a name given twice accepted",
     '            if fe["offset"] in offs or fe["name"] in names:\n',
     '            if fe["offset"] in offs:\n'),
    ("files taken from the folder's top",
     '    return os.path.join(pin_dir, pin["pin"], iname)\n',
     '    return pin_dir\n'),
    ("the size, sha256 and md5 not compared",
     '            if (size, s, m) != (fe["size"], fe["sha256"], fe["md5"]):\n',
     '            if False:\n'),
    ("the md5 not compared",
     '            if (size, s, m) != (fe["size"], fe["sha256"], fe["md5"]):\n',
     '            if (size, s) != (fe["size"], fe["sha256"]):\n'),
    ("the image hash not compared",
     '                if a.image_hash != im["image_hash"]:\n',
     '                if False:\n'),
    ("the code hash not compared",
     '                if a.code_hash != im["code_hash"]:\n',
     '                if False:\n'),
    ("files not in flash order",
     '        for fe in sorted(im["files"], key=lambda e: int(e["offset"], '
     '16)):\n', '        for fe in im["files"]:\n'),
    ("install overwrites other bytes",
     "                if not os.path.isfile(dest) or digests(dest) != want:\n",
     "                if False:\n"),
    ("install copies other bytes",
     "                if os.path.isfile(c) and digests(c) == want:\n",
     "                if os.path.isfile(c):\n"),
    ("install copies as it plans",
     "            plan.append((iname, fe[\"name\"], src, dest))\n",
     "            plan.append((iname, fe[\"name\"], src, dest))\n"
     "            os.makedirs(os.path.dirname(dest), exist_ok=True)\n"
     "            shutil.copyfile(src, dest)\n"),
    ("install searches sub-folders",
     "                c = os.path.join(s, fe[\"name\"])\n",
     "                c = next((os.path.join(dp, fe[\"name\"]) for dp, _, fns "
     "in os.walk(s) if fe[\"name\"] in fns), os.path.join(s, fe[\"name\"]))\n"),
]]


if __name__ == "__main__":
    if sys.argv[1:] == ["--can-fail"]:
        import test_vtrux_pins_packages as packages
        sys.exit(0 if packages.can_fail(MUTANTS, os.path.abspath(__file__))
                 else 1)
    unittest.main()
