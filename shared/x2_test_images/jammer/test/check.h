/*
 * check.h -- the jammer host tests' checks. TEST EQUIPMENT, NOT FOR VEHICLE.
 * Each failed CHECK prints its line and counts; check_end() prints ALL PASS
 * or the count and gives the exit code.
 */
#ifndef CHECK_H
#define CHECK_H

#include <stdio.h>

static int check_failed;
static int check_count;

#define CHECK(cond, ...)                                                    \
    do {                                                                    \
        check_count++;                                                      \
        if (!(cond)) {                                                      \
            check_failed++;                                                 \
            printf("FAIL %s:%d: %s -- ", __FILE__, __LINE__, #cond);        \
            printf(__VA_ARGS__);                                            \
            printf("\n");                                                   \
        }                                                                   \
    } while (0)

static int check_end(const char *name)
{
    if (check_failed) {
        printf("%s: %d of %d checks FAILED\n", name, check_failed, check_count);
        return 1;
    }
    printf("%s: ALL PASS (%d checks)\n", name, check_count);
    return 0;
}

#endif
