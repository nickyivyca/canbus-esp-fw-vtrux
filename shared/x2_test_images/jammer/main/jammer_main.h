/*
 * jammer_main.h -- jammer_main.c's line handling, declared for its host
 * tests (../test/test_main.c). TEST EQUIPMENT, NOT FOR VEHICLE.
 */
#ifndef JAMMER_MAIN_H
#define JAMMER_MAIN_H

#include <stddef.h>
#include <stdint.h>

#define JAMMER_LINE_MAX 64      /* longer lines are cut, as before the port */

/* Bytes from the host: lines end at '\n', '\r' is dropped. */
void jammer_feed(const uint8_t *buf, size_t n);

/* One command line: ID prints the banner, everything else goes to
 * jam_line(), and a word neither knows gets "ERR unknown_command". */
void jammer_handle_line(const char *line);

void jammer_banner(void);

#endif
