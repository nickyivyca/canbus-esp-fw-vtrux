"""shared/diag's unit tests of the Python twin (python/diag_pack.py) and the
table generator (tools/diag_table_gen.py). Self-checks, not verification.

    <python> -B -m unittest discover -s shared/diag/unit -p "test_*.py"

The twin's vectors are test_diag_pack.c's, cantools 41.3.1's encode of the
same layout. The generator's tests need cantools and are skipped without it.
Nothing is written to disk.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import diag_pack as P  # noqa: E402
import diag_table_gen as G  # noqa: E402

try:
    import cantools
except ImportError:
    cantools = None


def sig(name, start, length, be=False, signed=False, mux=P.MUX_NONE):
    return {"name": name, "start": start, "length": length, "big_endian": be,
            "is_signed": signed, "mux": mux}


T1 = {"name": "T1", "id": 1, "extended": False, "dlc": 8, "signals": (
    sig("a", 0, 8), sig("b", 8, 16), sig("c", 24, 12, signed=True),
    sig("d", 47, 12, be=True), sig("e", 51, 9, be=True, signed=True))}
MUX = {"name": "M", "id": 2, "extended": False, "dlc": 3, "signals": (
    sig("sel", 0, 8, mux=P.MUX_SELECTOR), sig("x", 8, 8, mux=1), sig("y", 8, 16, mux=2))}


class Twin(unittest.TestCase):
    def test_layouts(self):
        for raw, want in (([0x5A, 0x1234, -2, 0xABC, -3], "5a3412fe0fabcfe8"),
                          ([0xFF, 0xFFFF, 2047, 4095, 255], "ffffffff07fff7f8"),
                          ([0, 0, -2048, 0, -256], "0000000008000800")):
            n, data, mis = P.pack_message(T1, raw)
            self.assertEqual((n, data.hex(), mis), (0, want, 0))

    def test_misfits(self):
        n, data, mis = P.pack_message(T1, [256, 0x1234, -2049, 0xABC, 256])
        self.assertEqual((n, data.hex(), mis), (3, "0034120000abc000", 0b10101))
        self.assertEqual(P.pack_message(T1, [-1, 0, 0, 0, 0])[0::2], (1, 1))
        d = bytearray(b"\xaa" * 8)
        self.assertEqual(P.pack_signal(T1["signals"][0], 300, d, 8), P.MISFIT)
        self.assertEqual(d[0], 0xAA)
        self.assertEqual(P.pack_signal(T1["signals"][0], 0x0F, d, 8), P.OK)
        self.assertEqual((d[0], d[1]), (0x0F, 0xAA))

    def test_ranges(self):
        u64, s64, s1 = sig("u", 0, 64), sig("s", 0, 64, signed=True), sig("s1", 0, 1, signed=True)
        self.assertEqual((P.signal_min(u64), P.signal_max(u64)), (0, P.INT64_MAX))
        self.assertEqual((P.signal_min(s64), P.signal_max(s64)), (P.INT64_MIN, P.INT64_MAX))
        self.assertEqual((P.signal_min(s1), P.signal_max(s1)), (-1, 0))
        d = bytearray(8)
        self.assertEqual(P.pack_signal(s64, P.INT64_MIN, d, 8), P.OK)
        self.assertEqual(d.hex(), "0000000000000080")
        self.assertEqual(P.pack_signal(u64, P.INT64_MAX, d, 8), P.OK)
        self.assertEqual(d.hex(), "ffffffffffffff7f")
        self.assertEqual(P.pack_signal(u64, P.INT64_MAX + 1, d, 8), P.MISFIT)
        with self.assertRaises(TypeError):
            P.pack_signal(u64, 1.0, d, 8)

    def test_mux(self):
        self.assertEqual(P.pack_message(MUX, [1, 7, 999])[1].hex(), "010700")
        self.assertEqual(P.pack_message(MUX, [2, 7, 999])[1].hex(), "02e703")
        self.assertEqual(P.pack_message(MUX, [5, 7, 999])[1].hex(), "050000")
        self.assertEqual(P.pack_message(MUX, [1, 300, 0])[0::2], (1, 2))
        self.assertEqual(P.pack_message(MUX, [256, 1, 1]), (-1, b"\0\0\0", 0))

    def test_bad_tables(self):
        def msg(dlc, *sigs):
            return {"name": "B", "id": 3, "extended": False, "dlc": dlc, "signals": sigs}
        for m in (msg(8, sig("p", 60, 8)), msg(1, sig("q", 3, 8, be=True)), msg(8, sig("z", 0, 0)),
                  msg(8, sig("o", 0, 8, mux=1)), msg(9),
                  msg(1, sig("s", 0, 4, mux=P.MUX_SELECTOR), sig("t", 4, 4, mux=P.MUX_SELECTOR))):
            self.assertEqual(P.pack_message(m, [0] * len(m["signals"]))[0], -1, m)

    def test_raw_values(self):
        self.assertEqual(P.raw_values(T1, b=5, e=-1), [0, 5, 0, 0, -1])
        with self.assertRaises(KeyError):
            P.raw_values(T1, nope=1)


DBC = '''VERSION ""
NS_ :
BS_:
BU_: X
BO_ 2 Second: 3 X
 SG_ sel M : 0|8@1+ (1,0) [0|0] "" X
 SG_ y m2 : 8|16@1+ (1,0) [0|0] "" X
 SG_ x m1 : 8|8@1+ (1,0) [0|0] "" X
BO_ 1 T1: 8 X
 SG_ e : 51|9@0- (1,0) [0|0] "" X
 SG_ a : 0|8@1+ (0.5,3) [0|0] "" X
 SG_ b : 8|16@1+ (1,0) [0|0] "" X
 SG_ c : 24|12@1- (1,0) [0|0] "" X
 SG_ d : 47|12@0+ (1,0) [0|0] "" X
BO_ 2566903777 Ext: 8 X
 SG_ v : 0|16@1- (1,0) [0|0] "" X
BA_DEF_ "DiagSchemaVersion" INT 0 255;
BA_DEF_ "DiagSchemaHash" STRING ;
BA_ "DiagSchemaVersion" 7;
BA_ "DiagSchemaHash" "0123456789abcdef";
'''


@unittest.skipIf(cantools is None, "cantools is not installed")
class Generator(unittest.TestCase):
    def gen(self, text):
        return G.table(cantools.database.load_string(text, "dbc"))

    def test_table(self):
        v, h, msgs = self.gen(DBC)
        self.assertEqual((v, h), (7, "0123456789abcdef"))
        self.assertEqual([m["name"] for m in msgs], ["T1", "Second", "Ext"])
        self.assertEqual(msgs[0]["signals"], list(T1["signals"]))
        self.assertEqual(msgs[1]["signals"], list(MUX["signals"]))
        self.assertEqual((msgs[2]["id"], msgs[2]["extended"]), (0x18FFD7E1, True))

    def test_python_module_is_the_table(self):
        v, h, msgs = self.gen(DBC)
        ns = {}
        exec(G.python_module("x.dbc", v, h, msgs), ns)
        self.assertEqual((ns["SCHEMA_VERSION"], ns["SCHEMA_HASH"]), (7, h))
        self.assertEqual([dict(m, signals=list(m["signals"])) for m in ns["MESSAGES"]], msgs)
        n, data, _ = P.pack_message(P.message(ns["MESSAGES"], "T1"), [0x5A, 0x1234, -2, 0xABC, -3])
        self.assertEqual(data.hex(), "5a3412fe0fabcfe8")

    def test_c_text(self):
        v, h, msgs = self.gen(DBC)
        hdr = G.c_header("t_dbc", "x.dbc", v, h, msgs)
        src = G.c_source("t_dbc", "t.h", "x.dbc", v, h, msgs)
        for want in ("#define t_dbc_SCHEMA_VERSION 7", "t_dbc_T1 = 0,", "t_dbc_T1__e = 4,",
                     "t_dbc_T1__N = 5", "#define t_dbc_Ext__ID 0x18FFD7E1u", "t_dbc_MESSAGE_COUNT = 3",
                     "#define t_dbc_T1__e__BITS 9\n", "#define t_dbc_T1__e__SIGNED 1\n",
                     "#define t_dbc_T1__a__SIGNED 0\n"):
            self.assertIn(want, hdr)
        self.assertIn('{ "e", 51, 9, true, true, DIAG_MUX_NONE },', src)
        self.assertIn('{ "sel", 0, 8, false, false, DIAG_MUX_SELECTOR },', src)
        self.assertIn('{ "Ext", 0x18FFD7E1u, true, 8, 1, t_dbc_Ext__signals },', src)
        self.assertEqual(hdr, G.c_header("t_dbc", "x.dbc", *self.gen(DBC)))

    def refused(self, text, why):
        with self.assertRaises(G.Refused) as cm:
            self.gen(text)
        self.assertIn(why, str(cm.exception))

    def test_refusals(self):
        head = DBC.split("BO_ 2 ")[0]
        attrs = 'BA_DEF_ "DiagSchemaVersion" INT 0 255;\nBA_ "DiagSchemaVersion" 1;\n'
        self.refused(head + 'BO_ 9 F: 8 X\n SG_ f : 0|32@1- (1,0) [0|0] "" X\n'
                     'SIG_VALTYPE_ 9 f : 1;\n' + attrs, "float")
        self.refused(head + 'BO_ 9 N: 8 X\n SG_ a : 0|8@1+ (1,0) [0|0] "" X\n', "DiagSchemaVersion")
        self.refused(head + 'BO_ 9 N: 8 X\n SG_ a : 0|8@1+ (1,0) [0|0] "" X\n'
                     'BA_DEF_ "DiagSchemaVersion" STRING ;\nBA_ "DiagSchemaVersion" "9";\n',
                     "DiagSchemaVersion")


if __name__ == "__main__":
    unittest.main()
