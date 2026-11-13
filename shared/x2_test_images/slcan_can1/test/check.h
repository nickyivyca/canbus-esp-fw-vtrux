/* The tests' one assertion: counts failures, prints each, never stops. */
#ifndef CHECK_H
#define CHECK_H

#include <stdio.h>

static int check_failures;
static int check_count;

#define CHECK(cond, ...) do {                                              \
        check_count++;                                                     \
        if (!(cond)) {                                                     \
            check_failures++;                                              \
            printf("FAIL %s:%d: %s -- ", __FILE__, __LINE__, #cond);       \
            printf(__VA_ARGS__);                                           \
            printf("\n");                                                  \
        }                                                                  \
    } while (0)

static int check_report(const char *name)
{
    if (check_failures) {
        printf("%s: %d of %d checks FAILED\n", name, check_failures, check_count);
        return 1;
    }
    printf("%s: ALL PASS (%d checks)\n", name, check_count);
    return 0;
}

#endif
