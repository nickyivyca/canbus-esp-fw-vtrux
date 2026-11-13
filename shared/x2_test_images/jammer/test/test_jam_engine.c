/*
 * test_jam_engine.c -- the jammer's pulse engine (components/jam_pulse) on
 * the host fakes (fake/fake_idf.h), judged from the emulated wire: what the
 * RMT put on CAN1's TXD, not what the engine says it did. TEST EQUIPMENT,
 * NOT FOR VEHICLE.
 *
 * Built three times, as the image is (Makefile): no define (the jammer,
 * both stops), JAM_TEST_STOP_A_ONLY and JAM_TEST_STOP_B_ONLY. The checks
 * that differ say which build they are for.
 *
 * The safety properties checked (x2_test_images SPEC T8, jammer/README.md):
 *  - the pin is driven recessive at start and after every command, and the
 *    RMT output is inverted with a recessive end level (fake: a fault in
 *    either counts as a violation);
 *  - pulses only, W 12..50 us, never a held line;
 *  - a refusal puts nothing on the wire;
 *  - every command ends by its deadline, capped at 30 s, through each stop
 *    alone: (a) the deadline timer, shown on the a_only build and with a
 *    late task; (b) the chunk sizing, shown with a broken timer and on the
 *    b_only build; and with the task hung, the wire stops within one chunk.
 */
#include <stdlib.h>
#include <string.h>

#include "check.h"
#include "fake_idf.h"
#include "jam_internal.h"
#include "jam_pulse.h"

#if defined(JAM_TEST_STOP_A_ONLY)
#define BUILD "a_only"
#define HAVE_A 1
#define HAVE_B_AT_DEADLINE 0
#elif defined(JAM_TEST_STOP_B_ONLY)
#define BUILD "b_only"
#define HAVE_A 0
#define HAVE_B_AT_DEADLINE 1
#else
#define BUILD "a+b"
#define HAVE_A 1
#define HAVE_B_AT_DEADLINE 1
#endif

#define REPLIES_MAX 2048
static char replies[REPLIES_MAX][JAM_REPLY_MAX + 8];
static int nreplies;
static int long_replies;

static void on_reply(const char *line, void *ctx)
{
    (void)ctx;
    size_t n = strlen(line);
    if (n >= JAM_REPLY_MAX || strchr(line, '\n') || strchr(line, '\r'))
        long_replies++;
    if (nreplies < REPLIES_MAX) {
        strncpy(replies[nreplies], line, sizeof replies[0] - 1);
        nreplies++;
    }
}

static int find(const char *prefix)
{
    size_t n = strlen(prefix);
    for (int i = 0; i < nreplies; i++)
        if (!strncmp(replies[i], prefix, n)) return i;
    return -1;
}

static int count(const char *prefix)
{
    size_t n = strlen(prefix);
    int c = 0;
    for (int i = 0; i < nreplies; i++)
        if (!strncmp(replies[i], prefix, n)) c++;
    return c;
}

/* key=value from a reply line, as text ("" if absent). */
static const char *field(int i, const char *key)
{
    static char v[64];
    v[0] = 0;
    if (i < 0) return v;
    char pat[32];
    snprintf(pat, sizeof pat, " %s=", key);
    const char *p = strstr(replies[i], pat);
    if (!p) return v;
    p += strlen(pat);
    size_t k = 0;
    while (p[k] && p[k] != ' ' && k < sizeof v - 1) {
        v[k] = p[k];
        k++;
    }
    v[k] = 0;
    return v;
}

static long long lfield(int i, const char *key)
{
    return atoll(field(i, key));
}

static void setup(void)
{
    fake_reset();
    nreplies = 0;
    long_replies = 0;
    jam_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.tx_gpio = 7;
    cfg.reply = on_reply;
    cfg.core = 1;
    cfg.priority = 10;
    CHECK(jam_init(&cfg) == ESP_OK, "jam_init");
}

/* The jam task's turn: run the queued command, if any, to its end. */
static int run_queued(void)
{
    jam_cmd_t c;
    if (!jam_engine_take(&c, 0)) return 0;
    jam_engine_run(&c);
    return 1;
}

static void stop_line(void *arg)
{
    (void)arg;
    jam_line("STOP");
}

/* The end state every command must leave: pin recessive and an output,
 * channel deleted, no RMT fault, idle. */
static void check_end_state(const char *what)
{
    CHECK(fake_gpio_level[7] == 1 && fake_gpio_output[7] == 1,
          "%s: pin 7 not driven recessive at the end (level %d out %d)",
          what, fake_gpio_level[7], fake_gpio_output[7]);
    CHECK(fake_rmt_channels == 0, "%s: %d RMT channels left", what,
          fake_rmt_channels);
    CHECK(fake_rmt_violations == 0, "%s: %d RMT/level faults", what,
          fake_rmt_violations);
    CHECK(!jam_busy(), "%s: still busy", what);
    CHECK(long_replies == 0, "%s: a reply too long or with a line end", what);
}

static void check_pulse_shape(const char *what, uint32_t w)
{
    CHECK(fake_wire_pulses == 0
          || (fake_wire_min_w_us == w && fake_wire_max_w_us == w),
          "%s: pulse widths %u..%u, not %u", what, fake_wire_min_w_us,
          fake_wire_max_w_us, w);
    CHECK(fake_wire_max_w_us <= JAM_W_MAX_US, "%s: a pulse over 50 us", what);
}

static void test_init(void)
{
    setup();
    CHECK(fake_log_find("gpio_set_level 7 1", 0) == 0,
          "init: the first call is not pin 7 high (%s)", fake_log[0]);
    CHECK(fake_log_find("gpio_set_direction 7 out", 0) == 1,
          "init: the second call is not pin 7 output");
    CHECK(fake_timer_creates == HAVE_A, "init [%s]: %d timers created", BUILD,
          fake_timer_creates);
    CHECK(fake_tasks_n == 1 && !strcmp(fake_task_name[0], "jam")
          && fake_task_prio[0] == 10 && fake_task_core[0] == 1,
          "init: the jam task");
    CHECK(fake_log_find("rmt_new", 0) < 0, "init: an RMT channel at start");
    CHECK(!strcmp(jam_stops_text(),
                  HAVE_A && HAVE_B_AT_DEADLINE ? "a+b"
                  : HAVE_A ? "a_only(b at deadline+2000ms)" : "b_only"),
          "stops text [%s] is %s", BUILD, jam_stops_text());
    char lim[JAM_REPLY_MAX];
    jam_limits_text(lim, sizeof lim);
    CHECK(!strcmp(lim, "limits w_us=12..50 p_us>=4w..20000 ms<=30000 "
                       "n<=1000 interval_ms=1..10000"), "limits: %s", lim);
    jam_cmd_t c;
    CHECK(!jam_engine_take(&c, 0), "init: a command queued");
}

static void test_refusals(void)
{
    static const char *bad[] = {
        "TRAIN", "TRAIN 20 5000", "TRAIN 20 5000 100 7", "TRAIN 11 5000 100",
        "TRAIN 51 5000 100", "TRAIN 20 79 100", "TRAIN 20 20001 100",
        "TRAIN 20 5000 0", "PULSES 20 0 100", "PULSES 20 300 100",
        "PULSES 20 1 10001", "PULSES 20 10",
    };
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        setup();
        CHECK(jam_line(bad[i]) == JAM_LINE_DONE, "'%s' not handled", bad[i]);
        CHECK(nreplies == 1 && !strncmp(replies[0], "ERR ", 4),
              "'%s': replies %d, first '%s'", bad[i], nreplies,
              nreplies ? replies[0] : "");
        CHECK(!run_queued(), "'%s' was queued", bad[i]);
        CHECK(fake_log_find("rmt_new", 0) < 0 && fake_wire_pulses == 0,
              "'%s' touched the RMT", bad[i]);
        CHECK(!jam_busy(), "'%s' left it busy", bad[i]);
    }
    setup();
    CHECK(jam_line("ID") == JAM_LINE_NOT_MINE, "ID is not the engine's");
    CHECK(jam_line("FOO") == JAM_LINE_NOT_MINE, "FOO is not the engine's");
    CHECK(jam_line("") == JAM_LINE_DONE && nreplies == 0, "empty line");
    CHECK(jam_line("STATUS") == JAM_LINE_DONE && find("STATUS idle ") == 0,
          "STATUS idle");
    CHECK(jam_line("STOP") == JAM_LINE_DONE && find("ACK STOP busy=0") >= 0,
          "STOP when idle");
    CHECK(!run_queued(), "STOP queued a command");
}

/* TRAIN 20 5000 2000 to its end; returns its deadline (t0 + 2 s). */
static int64_t train_2s(void)
{
    CHECK(jam_line("TRAIN 20 5000 2000") == JAM_LINE_DONE, "TRAIN accepted");
    CHECK(jam_busy(), "busy once accepted");
    CHECK(run_queued(), "TRAIN queued");
    int a = find("ACK TRAIN ");
    CHECK(a >= 0, "no ACK");
    CHECK(!strcmp(field(a, "ms"), "2000") && !strcmp(field(a, "p_us"), "5000"),
          "ACK fields: %s", replies[a]);
    return lfield(a, "t0_us") + 2000000;
}

static void test_train_both_or_one(void)
{
    setup();
    int64_t d = train_2s();
    int i = find("DONE TRAIN ");
    CHECK(i >= 0, "no DONE");
    check_end_state("train");
    check_pulse_shape("train", 20);
#if HAVE_B_AT_DEADLINE
    /* Layer (b) sizes every chunk to end by the deadline; the timer, where
     * there is one, never fires. */
    CHECK(!strcmp(field(i, "end"), "deadline_layer_b")
          && !strcmp(field(i, "stop_by"), "none"),
          "[%s] ended %s by %s", BUILD, field(i, "end"), field(i, "stop_by"));
    CHECK(fake_tx_end_max_us <= d, "[%s] a chunk ends %lld us past the "
          "deadline", BUILD, (long long)(fake_tx_end_max_us - d));
    CHECK(fake_wire_pulses >= 395 && fake_wire_pulses <= 400,
          "[%s] %ld pulses in 2 s at 5 ms", BUILD, fake_wire_pulses);
    CHECK(fake_wire_last_us > d - 3 * 5000 && fake_wire_last_us < d,
          "[%s] last pulse %lld us from the deadline", BUILD,
          (long long)(fake_wire_last_us - d));
    CHECK(fake_timer_fired == 0, "[%s] the timer fired", BUILD);
#else
    /* a_only: layer (b) runs to deadline + 2 s, so only the timer can end
     * the train at the deadline. */
    CHECK(!strcmp(field(i, "end"), "stopped")
          && !strcmp(field(i, "stop_by"), "deadline_timer"),
          "[a_only] ended %s by %s", field(i, "end"), field(i, "stop_by"));
    CHECK(fake_timer_fired == 1, "[a_only] timer fired %d", fake_timer_fired);
    CHECK(fake_wire_last_us < d && fake_wire_last_us >= d - 5000,
          "[a_only] last pulse %lld us from the deadline",
          (long long)(fake_wire_last_us - d));
    CHECK(fake_tx_end_max_us > d, "[a_only] layer (b) still sized chunks to "
          "the deadline, so the timer was not shown alone");
#endif
    CHECK(fake_timer_starts == HAVE_A, "[%s] timer started %d times", BUILD,
          fake_timer_starts);
    CHECK(lfield(i, "pulses") >= fake_wire_pulses,
          "DONE pulses= counts queued pulses, at least the wire's");
}

/* Stop (b) alone: the timer never fires. */
static void test_layer_b_alone(void)
{
    setup();
    fake_timer_broken = true;
    int64_t d = train_2s();
    int i = find("DONE TRAIN ");
    check_end_state("broken timer");
#if HAVE_B_AT_DEADLINE
    CHECK(!strcmp(field(i, "end"), "deadline_layer_b"),
          "[%s] broken timer: ended %s", BUILD, field(i, "end"));
    CHECK(fake_tx_end_max_us <= d && fake_wire_last_us < d,
          "[%s] broken timer: the wire ran %lld us past the deadline", BUILD,
          (long long)(fake_wire_last_us - d));
#else
    /* a_only with no timer: only the backstop at deadline + 2 s. A judge
     * that wants the last pulse within 0.5 s of the deadline FAILs this,
     * which is what shows (a) when the train does end at the deadline. */
    int64_t b = d + JAM_BACKSTOP_US;
    CHECK(!strcmp(field(i, "end"), "backstop_layer_b")
          && !strcmp(field(i, "stop_by"), "none"),
          "[a_only] broken timer: ended %s by %s", field(i, "end"),
          field(i, "stop_by"));
    CHECK(fake_tx_end_max_us <= b, "[a_only] past the backstop");
    CHECK(fake_wire_last_us > d + 500000,
          "[a_only] broken timer: the wire stopped %lld us after the "
          "deadline, not distinguishable from (a)",
          (long long)(fake_wire_last_us - d));
#endif
}

/* The task hangs after its first chunk (3 s late): the wire stops within
 * that chunk on every build, and the timer, where there is one, records
 * the stop. */
static void test_task_hung(void)
{
    setup();
    fake_task_late_us = 3000000;
    int64_t d = train_2s();
    int i = find("DONE TRAIN ");
    int a = find("ACK TRAIN ");
    int64_t t0 = lfield(a, "t0_us");
    check_end_state("hung task");
    CHECK(fake_wire_last_us < t0 + JAM_CHUNK_US,
          "[%s] hung task: the wire ran %lld us, past one chunk", BUILD,
          (long long)(fake_wire_last_us - t0));
    CHECK(fake_wire_last_us < d, "[%s] hung task: past the deadline", BUILD);
#if HAVE_A
    CHECK(!strcmp(field(i, "stop_by"), "deadline_timer"),
          "[%s] hung task: stop_by %s", BUILD, field(i, "stop_by"));
#else
    CHECK(!strcmp(field(i, "end"), "deadline_layer_b")
          && !strcmp(field(i, "stop_by"), "none"),
          "[b_only] hung task: ended %s by %s", field(i, "end"),
          field(i, "stop_by"));
#endif
}

/* Stop (a) on its own hardware path: the task wakes 300 ms late every time,
 * and the deadline (2.1 s) falls inside a running batch, so only the
 * timer's own disable of the RMT can end the wire at the deadline. */
static void test_timer_disables_alone(void)
{
    setup();
    fake_task_late_us = 300000;
    CHECK(jam_line("TRAIN 20 5000 2100") == JAM_LINE_DONE, "accepted");
    CHECK(run_queued(), "runs");
    int a = find("ACK TRAIN ");
    int64_t d = lfield(a, "t0_us") + 2100000;
    int i = find("DONE TRAIN ");
    CHECK(fake_wire_last_us < d, "[%s] late task: the wire ran %lld us past "
          "the deadline", BUILD, (long long)(fake_wire_last_us - d));
#if HAVE_A
    CHECK(!strcmp(field(i, "stop_by"), "deadline_timer"),
          "[%s] late task: stop_by %s", BUILD, field(i, "stop_by"));
#else
    CHECK(!strcmp(field(i, "end"), "deadline_layer_b"),
          "[b_only] late task: ended %s", field(i, "end"));
#endif
#if !HAVE_B_AT_DEADLINE
    CHECK(fake_tx_end_max_us > d, "[a_only] the batch did not span the "
          "deadline, so the timer's disable was not what stopped it");
#endif
    check_end_state("timer alone");
}

static void test_stop_mid_train(void)
{
    setup();
    int64_t at = fake_now_us + 500000;
    fake_at(at, stop_line, NULL);
    train_2s();
    int i = find("DONE TRAIN ");
    CHECK(find("ACK STOP busy=1") >= 0, "STOP not acknowledged busy");
    CHECK(!strcmp(field(i, "end"), "stopped")
          && !strcmp(field(i, "stop_by"), "stop_cmd"),
          "STOP: ended %s by %s", field(i, "end"), field(i, "stop_by"));
    CHECK(fake_wire_last_us < at && fake_wire_last_us >= at - 5000,
          "STOP: last pulse %lld us from the STOP",
          (long long)(fake_wire_last_us - at));
    check_end_state("stop");
}

/* A STOP between acceptance and start is kept. */
static void test_stop_before_start(void)
{
    setup();
    CHECK(jam_line("TRAIN 20 5000 2000") == JAM_LINE_DONE, "accepted");
    CHECK(jam_line("STOP") == JAM_LINE_DONE, "STOP");
    CHECK(find("ACK STOP busy=1") >= 0, "STOP busy=1");
    CHECK(run_queued(), "queued");
    int i = find("DONE TRAIN ");
    CHECK(!strcmp(field(i, "end"), "stopped")
          && !strcmp(field(i, "stop_by"), "stop_cmd"),
          "early STOP: ended %s by %s", field(i, "end"), field(i, "stop_by"));
    CHECK(fake_wire_pulses == 0, "early STOP: %ld pulses", fake_wire_pulses);
    check_end_state("early stop");
}

static void test_busy_status(void)
{
    setup();
    CHECK(jam_line("TRAIN 20 5000 300") == JAM_LINE_DONE, "accepted");
    jam_line("TRAIN 20 5000 100");
    CHECK(find("ERR busy") >= 0, "second command not refused busy");
    jam_line("PULSES 20 1 1");
    CHECK(count("ERR busy") == 2, "PULSES not refused busy");
    jam_line("STATUS");
    CHECK(find("STATUS jamming ") >= 0, "STATUS while busy");
    CHECK(run_queued(), "first runs");
    CHECK(!run_queued(), "the refused ones were queued");
    nreplies = 0;
    jam_line("STATUS");
    CHECK(find("STATUS idle ") == 0, "STATUS after");
    check_end_state("busy");
}

static void test_capped(void)
{
    setup();
    jam_line("TRAIN 20 5000 60000");
    CHECK(find("CAPPED ms 60000 -> 30000") == 0, "CAPPED line first");
    CHECK(run_queued(), "capped runs");
    int a = find("ACK TRAIN ");
    CHECK(!strcmp(field(a, "ms"), "30000"), "ACK ms %s", field(a, "ms"));
    int64_t d = lfield(a, "t0_us") + 30000000;
    CHECK(fake_wire_last_us < d, "capped: past 30 s");
    CHECK(fake_tx_end_max_us <= d + (HAVE_B_AT_DEADLINE ? 0 : JAM_BACKSTOP_US),
          "capped: a chunk past the deadline");
    check_end_state("capped");
}

static void test_pulses(void)
{
    setup();
    CHECK(jam_line("PULSES 20 10 1000") == JAM_LINE_DONE, "PULSES");
    CHECK(run_queued(), "PULSES runs");
    int a = find("ACK PULSES ");
    CHECK(a >= 0 && !strcmp(field(a, "deadline_ms"), "10100"),
          "ACK PULSES: %s", a >= 0 ? replies[a] : "none");
    int64_t t0 = lfield(a, "t0_us");
    int i = find("DONE PULSES ");
    CHECK(!strcmp(field(i, "end"), "complete")
          && !strcmp(field(i, "stop_by"), "none")
          && !strcmp(field(i, "pulses"), "10"),
          "PULSES: %s", i >= 0 ? replies[i] : "none");
    CHECK(fake_wire_pulses == 10, "PULSES: %ld on the wire", fake_wire_pulses);
    CHECK(count("PULSE k=") == 10, "PULSE lines %d", count("PULSE k="));
    CHECK(fake_wire_first_us >= t0 + 1000 && fake_wire_first_us < t0 + 2000,
          "first pulse at +%lld us", (long long)(fake_wire_first_us - t0));
    CHECK(fake_wire_last_us >= t0 + 9001000 && fake_wire_last_us < t0 + 9003000,
          "last pulse at +%lld us", (long long)(fake_wire_last_us - t0));
    CHECK(fake_timer_fired == 0, "PULSES: timer fired");
    check_pulse_shape("pulses", 20);
    check_end_state("pulses");
}

static void test_pulses_stop(void)
{
    setup();
    fake_at(fake_now_us + 3500000, stop_line, NULL);
    jam_line("PULSES 20 10 1000");
    CHECK(run_queued(), "runs");
    int i = find("DONE PULSES ");
    CHECK(!strcmp(field(i, "end"), "stopped")
          && !strcmp(field(i, "stop_by"), "stop_cmd"),
          "PULSES STOP: ended %s by %s", field(i, "end"), field(i, "stop_by"));
    CHECK(fake_wire_pulses == 4, "PULSES STOP: %ld pulses", fake_wire_pulses);
    check_end_state("pulses stop");
}

/* A late task (1.5 s each wake) must not push a pulse past the deadline. */
static void test_pulses_late(void)
{
    setup();
    fake_task_late_us = 1500000;
    jam_line("PULSES 20 10 1000");
    CHECK(run_queued(), "runs");
    int a = find("ACK PULSES ");
    int64_t d = lfield(a, "t0_us") + 10100000;
    CHECK(fake_wire_last_us + 20 <= d, "[%s] late PULSES: a pulse ends %lld us "
          "past the deadline", BUILD, (long long)(fake_wire_last_us + 20 - d));
    CHECK(fake_wire_pulses < 10, "[%s] late PULSES: all 10 sent", BUILD);
    check_end_state("late pulses");
}

int main(void)
{
    printf("build: %s\n", BUILD);
    test_init();
    test_refusals();
    test_train_both_or_one();
    test_layer_b_alone();
    test_task_hung();
    test_timer_disables_alone();
    test_stop_mid_train();
    test_stop_before_start();
    test_busy_status();
    test_capped();
    test_pulses();
    test_pulses_stop();
    test_pulses_late();
    return check_end("test_jam_engine [" BUILD "]");
}
