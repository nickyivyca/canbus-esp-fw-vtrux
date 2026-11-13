/*
 * test_jam_parse.c -- the jammer's command parser and limits
 * (components/jam_pulse/jam_parse.c), on the host. TEST EQUIPMENT, NOT FOR
 * VEHICLE. The expected words are the PlatformIO jammer's
 * (gen-inhibit/tools/x2_jammer/README.md, "Commands"): its host side reads
 * them.
 */
#include <string.h>

#include "check.h"
#include "jam_internal.h"
#include "jam_pulse.h"

static char note[JAM_REPLY_MAX];
static jam_cmd_t cmd;

static jp_result_t P(const char *line, bool busy)
{
    memset(&cmd, 0xA5, sizeof cmd);
    return jam_parse(line, busy, &cmd, note, sizeof note);
}

static void refused(const char *line, bool busy, const char *want)
{
    jp_result_t r = P(line, busy);
    CHECK(r == JP_REFUSED, "'%s' gave %d, not refused", line, r);
    CHECK(!strcmp(note, want), "'%s' gave '%s', not '%s'", line, note, want);
}

static void accepted(const char *line, const char *want_note)
{
    jp_result_t r = P(line, false);
    CHECK(r == JP_CMD, "'%s' gave %d (%s), not a command", line, r, note);
    CHECK(!strcmp(note, want_note), "'%s' note '%s', not '%s'", line, note,
          want_note);
}

int main(void)
{
    /* Nothing, someone else's word, or the engine's own words. */
    CHECK(P("", false) == JP_EMPTY, "empty");
    CHECK(P("   \t ", false) == JP_EMPTY, "blank");
    CHECK(P("ID", false) == JP_NOT_MINE, "ID is the app's");
    CHECK(P("FOO 1 2 3", false) == JP_NOT_MINE, "unknown word");
    CHECK(P("train 20 5000 100", false) == JP_NOT_MINE, "lower case");
    CHECK(P("TRAINX 20 5000 100", false) == JP_NOT_MINE, "longer word");
    CHECK(P("TRAI 20 5000 100", false) == JP_NOT_MINE, "shorter word");
    CHECK(P("STOP", false) == JP_STOP, "STOP");
    CHECK(P("STOP", true) == JP_STOP, "STOP when busy");
    CHECK(P("  STOP now", false) == JP_STOP, "STOP with words after");
    CHECK(P("STATUS", false) == JP_STATUS, "STATUS");

    /* Every jam command carries exactly three numbers: no duration, no
     * command. */
    refused("TRAIN", false, "ERR need_exactly_3_args got=0");
    refused("TRAIN 20 5000", false, "ERR need_exactly_3_args got=2");
    refused("PULSES 20 10", false, "ERR need_exactly_3_args got=2");
    refused("TRAIN 20 5000 100 7", false, "ERR need_exactly_3_args got=4");
    refused("TRAIN 20 5000 100 x", false, "ERR need_exactly_3_args got=4");
    refused("TRAIN 20 x 100", false, "ERR need_exactly_3_args got=1");
    refused("TRAIN -5 5000 100", false, "ERR need_exactly_3_args got=0");
    refused("TRAIN 20 5000 1e3", false, "ERR need_exactly_3_args got=2");
    /* The argument count is judged before busy, busy before the ranges. */
    refused("TRAIN 20 5000", true, "ERR need_exactly_3_args got=2");
    refused("TRAIN 20 5000 100", true, "ERR busy");
    refused("TRAIN 5 5000 100", true, "ERR busy");
    refused("PULSES 20 10 1000", true, "ERR busy");

    /* W: 12..50 us. */
    refused("TRAIN 11 5000 100", false, "ERR w_us_out_of_range 11");
    refused("TRAIN 51 5000 100", false, "ERR w_us_out_of_range 51");
    refused("TRAIN 0 5000 100", false, "ERR w_us_out_of_range 0");
    refused("PULSES 51 10 100", false, "ERR w_us_out_of_range 51");
    refused("TRAIN 4294967295 5000 100", false,
            "ERR w_us_out_of_range 4294967295");
    refused("TRAIN 99999999999 5000 100", false,
            "ERR w_us_out_of_range 4294967295");
    accepted("TRAIN 12 48 100", "");
    CHECK(cmd.kind == JAM_K_TRAIN && cmd.w_us == 12 && cmd.p_us == 48
          && cmd.ms == 100, "TRAIN 12 48 100 read back");
    accepted("TRAIN 50 200 100", "");

    /* P: at least 4 W, at most 20000 us. */
    refused("TRAIN 20 79 100", false, "ERR p_us_out_of_range 79");
    accepted("TRAIN 20 80 100", "");
    refused("TRAIN 20 20001 100", false, "ERR p_us_out_of_range 20001");
    accepted("TRAIN 20 20000 100", "");
    refused("TRAIN 50 199 100", false, "ERR p_us_out_of_range 199");

    /* The duration: not zero, capped at 30 s. */
    refused("TRAIN 20 5000 0", false, "ERR ms_zero");
    accepted("TRAIN 20 5000 1", "");
    accepted("TRAIN 20 5000 30000", "");
    CHECK(cmd.ms == 30000, "30000 kept");
    accepted("TRAIN 20 5000 30001", "CAPPED ms 30001 -> 30000");
    CHECK(cmd.ms == 30000, "30001 capped to %lu", (unsigned long)cmd.ms);
    accepted("TRAIN 20 5000 99999999999", "CAPPED ms 4294967295 -> 30000");
    CHECK(cmd.ms == 30000, "huge capped");
    CHECK(jam_cmd_ms(&cmd) == 30000, "TRAIN deadline is its ms");

    /* PULSES: n 1..1000, interval 1..10000 ms, n x interval + 100 <= 30 s. */
    refused("PULSES 20 0 100", false, "ERR pulses_out_of_range n=0 interval_ms=100");
    refused("PULSES 20 1001 1", false, "ERR pulses_out_of_range n=1001 interval_ms=1");
    refused("PULSES 20 1 0", false, "ERR pulses_out_of_range n=1 interval_ms=0");
    refused("PULSES 20 1 10001", false, "ERR pulses_out_of_range n=1 interval_ms=10001");
    refused("PULSES 20 300 100", false, "ERR pulses_out_of_range n=300 interval_ms=100");
    refused("PULSES 20 1000 10000", false, "ERR pulses_out_of_range n=1000 interval_ms=10000");
    refused("PULSES 20 4294967295 4294967295", false,
            "ERR pulses_out_of_range n=4294967295 interval_ms=4294967295");
    accepted("PULSES 20 299 100", "");
    CHECK(jam_cmd_ms(&cmd) == 30000, "299 x 100 + 100 = 30000, got %lu",
          (unsigned long)jam_cmd_ms(&cmd));
    accepted("PULSES 20 1000 29", "");
    accepted("PULSES 20 2 10000", "");
    accepted("PULSES 20 10 1000", "");
    CHECK(cmd.kind == JAM_K_PULSES && cmd.n == 10 && cmd.interval_ms == 1000
          && cmd.w_us == 20, "PULSES read back");
    CHECK(jam_cmd_ms(&cmd) == 10100, "PULSES deadline n x interval + 100");

    /* Spaces and tabs separate. */
    accepted("  \tTRAIN\t20  5000 100 ", "");

    /* A refusal never leaves a command for the caller to queue. */
    CHECK(P("TRAIN 20 5000 0", false) != JP_CMD, "refusal is not a command");

    return check_end("test_jam_parse");
}
