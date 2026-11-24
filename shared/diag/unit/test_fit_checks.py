"""shared/diag's unit tests of the generated fit checks (SPEC.md D6), the
implementor's. Self-checks, not verification.

    <python> -B -m unittest discover -s shared/diag/unit -p "test_*.py"

Each case is a small program that includes a generated C header and uses
one fit-check macro. It is compiled by gcc as C11 and by g++ as C++11, with
-Wall -Wextra -Werror and -fsyntax-only, with the source on stdin. A pass
case must compile. A fail case must stop on a static assertion that names
its signal. One run case per language is built and run: each macro's value,
and the capped form's cap. Its binary goes to a folder of its own under the
worktree's scratch/diag-unit/, removed when the case ends. The table is
built by hand, so cantools is not needed. The cases are skipped if gcc or
g++ is missing.
"""
import os
import shutil
import stat
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INCLUDE = os.path.join(HERE, "..", "include")
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import diag_table_gen as G  # noqa: E402


def sig(name, start, length, signed=False):
    return {"name": name, "start": start, "length": length, "big_endian": False,
            "is_signed": signed, "mux": G.MUX_NONE}


MSGS = [
    {"name": "F", "id": 0x10, "extended": False, "dlc": 8, "signals": [
        sig("u8", 0, 8), sig("u16", 8, 16), sig("u4", 24, 4), sig("s8", 32, 8, signed=True),
        sig("u1", 40, 1)]},
    {"name": "G", "id": 0x11, "extended": False, "dlc": 8, "signals": [sig("v63", 0, 63)]},
    {"name": "H", "id": 0x12, "extended": False, "dlc": 8, "signals": [sig("v64", 0, 64)]},
]
HEADER = G.c_header("t_dbc", "fit.dbc", 1, "", MSGS)

# Every case runs in both languages. BOOL is the language's bool type.
PREAMBLE = """
#ifdef __cplusplus
#define BOOL bool
#else
#define BOOL _Bool
#endif
int main(void) {
    int64_t r[8] = {0};
    uint8_t a8 = 1; uint16_t a16 = 2; uint32_t a32 = 3; uint64_t a64 = 4;
    int8_t i8 = 5; int ii = 6; BOOL b = 1; const BOOL cb = 1; const uint8_t c8 = 7;
    (void)a8; (void)a16; (void)a32; (void)a64; (void)i8; (void)ii; (void)b; (void)cb; (void)c8;
"""
POSTAMBLE = """
    return (int)r[0];
}
"""

PASS = [
    "t_dbc_FIT_PUT(r, F, u8, a8);",
    "t_dbc_FIT_PUT(r, F, u8, c8);",
    "t_dbc_FIT_PUT(r, F, u16, a8);",
    "t_dbc_FIT_PUT(r, F, u16, a16);",
    "t_dbc_FIT_PUT(r, G, v63, a32);",
    "t_dbc_FIT_PUT(r, H, v64, a32);",
    "t_dbc_FIT_BIT(r, F, u1, ii > 3);",
    "t_dbc_FIT_BIT(r, F, u8, b);",
    "t_dbc_FIT_MASK(r, F, u4, a32, 0x0Fu);",
    "t_dbc_FIT_MASK(r, F, u4, i8, 0x0F);",
    "t_dbc_FIT_MASK(r, G, v63, a64, INT64_MAX);",
    "t_dbc_FIT_CONST(r, F, u4, 0);",
    "t_dbc_FIT_CONST(r, F, u4, 15u);",
    "t_dbc_FIT_CONST(r, F, u1, 1);",
    "t_dbc_FIT_CONST(r, G, v63, INT64_MAX);",
    "t_dbc_FIT_CONST(r, H, v64, INT64_MAX);",
    "t_dbc_FIT_CAP(r, F, u8, a64);",                   # the capped form takes any width
    "t_dbc_FIT_CAP(r, F, u4, a32);",
    "t_dbc_FIT_CAP(r, H, v64, a64);",
]

# (code, the signal the assertion must name)
FAIL = [
    ("t_dbc_FIT_PUT(r, F, u8, a16);", "F.u8"),          # wider than the signal
    ("t_dbc_FIT_PUT(r, F, u16, a32);", "F.u16"),
    ("t_dbc_FIT_PUT(r, F, u8, i8);", "F.u8"),           # a signed type
    ("t_dbc_FIT_PUT(r, F, u16, ii);", "F.u16"),
    ("t_dbc_FIT_PUT(r, F, u8, b);", "F.u8"),            # bool
    ("t_dbc_FIT_PUT(r, F, u8, cb);", "F.u8"),
    ("t_dbc_FIT_PUT(r, H, v64, a64);", "H.v64"),        # 64 bits can pass INT64_MAX
    ("t_dbc_FIT_PUT(r, F, s8, a8);", "F.s8"),           # a signed signal, each macro
    ("t_dbc_FIT_BIT(r, F, s8, 1);", "F.s8"),
    ("t_dbc_FIT_MASK(r, F, s8, a8, 0x0F);", "F.s8"),
    ("t_dbc_FIT_CONST(r, F, s8, 0);", "F.s8"),
    ("t_dbc_FIT_MASK(r, F, u4, a8, 0x1F);", "F.u4"),    # a mask that does not fit
    ("t_dbc_FIT_MASK(r, F, u4, a8, -1);", "F.u4"),
    ("t_dbc_FIT_MASK(r, G, v63, a64, UINT64_MAX);", "G.v63"),
    ("t_dbc_FIT_CONST(r, F, u4, 16);", "F.u4"),         # a constant that does not fit
    ("t_dbc_FIT_CONST(r, F, u4, -1);", "F.u4"),
    ("t_dbc_FIT_CONST(r, F, u1, 2);", "F.u1"),
    ("t_dbc_FIT_CONST(r, H, v64, (uint64_t)INT64_MAX + 1u);", "H.v64"),
    ("t_dbc_FIT_CAP(r, F, u8, ii);", "F.u8"),           # the capped form: a signed value
    ("t_dbc_FIT_CAP(r, F, u8, i8);", "F.u8"),
    ("t_dbc_FIT_CAP(r, F, s8, a8);", "F.s8"),           # ... and a signed signal
]

# Run once in each language: each macro's value lands in its signal's slot,
# and the capped form caps at the signal's largest value and reads e once.
RUN = """
    uint64_t big = UINT64_MAX; unsigned n = 0; int bad = 0;
#define WANT(sig, v) do { if (r[sig] != (int64_t)(v)) { \\
        printf("%s = %lld, want %lld\\n", #sig, (long long)r[sig], (long long)(v)); bad = 1; } } while (0)
    t_dbc_FIT_CAP(r, F, u8, a64 * 1000u); WANT(t_dbc_F__u8, 255);
    t_dbc_FIT_CAP(r, F, u16, a32); WANT(t_dbc_F__u16, 3);
    t_dbc_FIT_CAP(r, F, u4, (uint32_t)15u); WANT(t_dbc_F__u4, 15);
    t_dbc_FIT_CAP(r, F, u4, (uint32_t)16u); WANT(t_dbc_F__u4, 15);
    t_dbc_FIT_CAP(r, F, u1, big); WANT(t_dbc_F__u1, 1);
    t_dbc_FIT_CAP(r, F, u8, n++); WANT(t_dbc_F__u8, 0);
    if (n != 1) { printf("the capped form read e %u times\\n", n); bad = 1; }
    t_dbc_FIT_CAP(r, G, v63, big); WANT(t_dbc_G__v63, INT64_MAX);
    t_dbc_FIT_CAP(r, H, v64, big); WANT(t_dbc_H__v64, INT64_MAX);
    t_dbc_FIT_PUT(r, F, u16, a16); WANT(t_dbc_F__u16, 2);
    t_dbc_FIT_BIT(r, F, u1, ii == 6); WANT(t_dbc_F__u1, 1);
    t_dbc_FIT_BIT(r, F, u1, ii == 7); WANT(t_dbc_F__u1, 0);
    t_dbc_FIT_MASK(r, F, u4, (uint32_t)0xABu, 0x0Fu); WANT(t_dbc_F__u4, 0xB);
    t_dbc_FIT_CONST(r, F, u8, 200); WANT(t_dbc_F__u8, 200);
    r[0] = bad;
"""

COMPILERS = (("gcc", ["-std=c11", "-x", "c"]), ("g++", ["-std=c++11", "-x", "c++"]))
# The run cases' binaries: the worktree's scratch/, as the Makefile's.
SCRATCH = os.path.normpath(os.path.join(HERE, "..", "..", "..", "scratch", "diag-unit"))


def remove_tree(path):
    """Removes path, clearing the read-only flag and retrying once, and says
    so if it still cannot."""
    def again(func, p, _exc):
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(p)
    try:
        shutil.rmtree(path, onexc=again) if sys.version_info >= (3, 12) \
            else shutil.rmtree(path, onerror=again)
    except OSError as e:
        print("could not remove %s: %s" % (path, e), file=sys.stderr)


def compile_case(cc, lang, code):
    src = HEADER + PREAMBLE + "    " + code + "\n" + POSTAMBLE
    p = subprocess.run([cc] + lang + ["-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                                      "-I", INCLUDE, "-"],
                       input=src, capture_output=True, text=True, encoding="ascii",
                       errors="replace")
    return p.returncode, p.stderr


class FitChecks(unittest.TestCase):
    def test_header_has_the_checks(self):
        for name in ("t_dbc_FIT_PUT(", "t_dbc_FIT_BIT(", "t_dbc_FIT_MASK(", "t_dbc_FIT_CONST("):
            self.assertIn("#define " + name, HEADER)
        self.assertNotIn("@P@", HEADER)
        self.assertEqual(HEADER, G.c_header("t_dbc", "fit.dbc", 1, "", MSGS))
        other = G.c_header("u_dbc", "fit.dbc", 1, "", MSGS)
        self.assertIn("#define u_dbc_FIT_PUT(", other)
        self.assertNotIn("t_dbc_", other)

    def check_compiler(self, cc, lang):
        if shutil.which(cc) is None:
            self.skipTest("%s is not installed" % cc)
        for code in PASS:
            rc, err = compile_case(cc, lang, code)
            self.assertEqual(rc, 0, "%s refused %s:\n%s" % (cc, code, err))
        for code, name in FAIL:
            rc, err = compile_case(cc, lang, code)
            self.assertNotEqual(rc, 0, "%s let through %s" % (cc, code))
            self.assertIn("static assertion failed", err, "%s %s:\n%s" % (cc, code, err))
            self.assertIn(name, err, "%s %s:\n%s" % (cc, code, err))

    def test_c(self):
        self.check_compiler(*COMPILERS[0])

    def test_cpp(self):
        self.check_compiler(*COMPILERS[1])

    def check_run(self, cc, lang):
        if shutil.which(cc) is None:
            self.skipTest("%s is not installed" % cc)
        out = os.path.join(SCRATCH, "fit-%d-%s" % (os.getpid(), cc.replace("+", "p")))
        os.makedirs(out)
        try:
            exe = os.path.join(out, "run")
            src = HEADER + "#include <stdio.h>\n" + PREAMBLE + RUN + POSTAMBLE
            p = subprocess.run([cc] + lang + ["-O1", "-Wall", "-Wextra", "-Werror", "-I", INCLUDE,
                                              "-o", exe, "-"],
                               input=src, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            p = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual((p.returncode, p.stdout), (0, ""))
        finally:
            remove_tree(out)
            try:
                os.rmdir(SCRATCH)   # only if no other run is using it
            except OSError:
                pass

    def test_run_c(self):
        self.check_run(*COMPILERS[0])

    def test_run_cpp(self):
        self.check_run(*COMPILERS[1])

    def test_two_tables_in_one_unit(self):
        both = HEADER + G.c_header("u_dbc", "fit.dbc", 1, "", MSGS)
        for cc, lang in COMPILERS:
            if shutil.which(cc) is None:
                continue
            src = both + PREAMBLE + ("    t_dbc_FIT_PUT(r, F, u8, a8);\n"
                                     "    u_dbc_FIT_PUT(r, F, u8, a8);\n") + POSTAMBLE
            p = subprocess.run([cc] + lang + ["-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                                              "-I", INCLUDE, "-"],
                               input=src, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
