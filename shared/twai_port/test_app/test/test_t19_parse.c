/*
 * Host tests of the T19 app's command line (twai_port_t19_parse.c; SPEC.md
 * T19). Self-checks, not verification.
 */
#include "../twai_port_t19_parse.h"

#include <stdio.h>
#include <string.h>

static int g_fail;
#define CHECK(c) do { if (!(c)) { printf("  FAIL %s:%d: %s\n", __FILE__, \
                                         __LINE__, #c); g_fail++; } } while (0)

static void test_split(void)
{
    t19_line_t l;
    char err[80];

    printf("t19: a command alone, and with arguments\n");
    CHECK(t19_parse("info", &l, err, sizeof(err)));
    CHECK(strcmp(l.cmd, "info") == 0 && l.n == 0);
    CHECK(t19_parse("  switch   to=lom  n=3 ", &l, err, sizeof(err)));
    CHECK(strcmp(l.cmd, "switch") == 0 && l.n == 2);
    CHECK(strcmp(l.key[0], "to") == 0 && strcmp(l.val[0], "lom") == 0);
    CHECK(strcmp(l.key[1], "n") == 0 && strcmp(l.val[1], "3") == 0);
    CHECK(t19_parse("txabort label= reps=2", &l, err, sizeof(err)));
    CHECK(l.n == 2 && strcmp(l.val[0], "") == 0);
    CHECK(t19_parse("txabort a=b=c", &l, err, sizeof(err)));
    CHECK(strcmp(l.key[0], "a") == 0 && strcmp(l.val[0], "b=c") == 0);

    printf("t19: refused: empty, too long, not key=value, empty key, too many\n");
    CHECK(!t19_parse("", &l, err, sizeof(err)));
    CHECK(!t19_parse("    ", &l, err, sizeof(err)));
    char big[T19_LINE_MAX + 2];
    memset(big, 'a', sizeof(big) - 1);
    big[sizeof(big) - 1] = '\0';
    CHECK(!t19_parse(big, &l, err, sizeof(err)) && strstr(err, "too long") != NULL);
    big[T19_LINE_MAX] = '\0';
    CHECK(t19_parse(big, &l, err, sizeof(err)));
    CHECK(!t19_parse("switch lom", &l, err, sizeof(err)));
    CHECK(!t19_parse("switch =lom", &l, err, sizeof(err)));
    char many[T19_LINE_MAX + 1] = "x";
    for (int i = 0; i < T19_ARGS_MAX; i++)
    {
        strcat(many, " k=v");
    }
    CHECK(t19_parse(many, &l, err, sizeof(err)) && l.n == T19_ARGS_MAX);
    strcat(many, " k=v");
    CHECK(!t19_parse(many, &l, err, sizeof(err)) && strstr(err, "many") != NULL);
}

static void test_get(void)
{
    t19_line_t l;
    char err[80], v[16];

    printf("t19: found, absent, unusable, the first of two\n");
    CHECK(t19_parse("c a=1 bb=12345678 a=2", &l, err, sizeof(err)));
    CHECK(t19_get(&l, "a", v, sizeof(v)) == 1 && strcmp(v, "1") == 0);
    CHECK(t19_get(&l, "b", v, sizeof(v)) == 0);
    CHECK(t19_get(&l, "bb", v, 8) == -1);       /* 8 characters need 9 */
    CHECK(t19_get(&l, "bb", v, 9) == 1 && strcmp(v, "12345678") == 0);
    CHECK(t19_get(&l, "a", v, 0) == -1);

    printf("t19: a key a command does not take\n");
    static const char *const ok[] = { "a", "bb", NULL };
    static const char *const only_a[] = { "a", NULL };
    CHECK(t19_unknown(&l, ok) == NULL);
    CHECK(t19_unknown(&l, only_a) != NULL && strcmp(t19_unknown(&l, only_a), "bb") == 0);
}

static void test_int(void)
{
    long x = 77;
    printf("t19: whole numbers in range only\n");
    CHECK(t19_int("12", 0, 32, &x) && x == 12);
    CHECK(t19_int("0", 0, 32, &x) && x == 0);
    CHECK(t19_int("32", 0, 32, &x) && x == 32);
    x = 77;
    CHECK(!t19_int("33", 0, 32, &x) && x == 77);
    CHECK(!t19_int("-1", 0, 32, &x));
    CHECK(!t19_int("", 0, 32, &x));
    CHECK(!t19_int("1x", 0, 32, &x));
    CHECK(!t19_int("x", 0, 32, &x));
}

static void test_json(void)
{
    char b[32];
    printf("t19: JSON strings escaped, and refused when they do not fit\n");
    CHECK(t19_json_str(b, sizeof(b), "info") && strcmp(b, "\"info\"") == 0);
    CHECK(t19_json_str(b, sizeof(b), "a\"b\\c") && strcmp(b, "\"a\\\"b\\\\c\"") == 0);
    CHECK(t19_json_str(b, sizeof(b), "x\ty") && strcmp(b, "\"x\\u0009y\"") == 0);
    CHECK(t19_json_str(b, sizeof(b), "") && strcmp(b, "\"\"") == 0);
    CHECK(t19_json_str(b, 7, "abcd") && strcmp(b, "\"abcd\"") == 0);
    CHECK(!t19_json_str(b, 6, "abcd") && b[0] == '\0');
    CHECK(!t19_json_str(b, 2, "") && b[0] == '\0');
}

int main(void)
{
    test_split();
    test_get();
    test_int();
    test_json();
    printf("%s: %d failure(s)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}
