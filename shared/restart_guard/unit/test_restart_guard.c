/*
 * restart_guard's unit tests (SPEC.md G6: the implementor's): the guard of
 * restart_guard.c (G2 to G4) against a scripted board (the ports, their kind
 * and their standby, B5), a scripted can_port restart stop (C7) and a real
 * esp_restart_noos() that jumps back to the case. Each case checks the calls
 * in order and the record left in "RTC memory". Self-checks for the
 * implementor, not verification.
 *
 * The component's file is included, not linked, so each case can reach the
 * record and put it back to what a power-on leaves.
 */
#include "../restart_guard.c"

#include <setjmp.h>
#include <stdio.h>
#include <string.h>

static int s_fail, s_checks;
static const char *s_case;

#define CHECK(cond) do { s_checks++; if (!(cond)) { s_fail++; \
    printf("FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, s_case, #cond); } } while (0)
#define CHECK_CALLS(want) do { s_checks++; if (strcmp(calls(), (want)) != 0) { s_fail++; \
    printf("FAIL %s:%d [%s] calls\n  got:  %s\n  want: %s\n", __FILE__, __LINE__, \
           s_case, calls(), (want)); } } while (0)

/* ----------------------------------------------------------- the log -- */

static char s_log[512];

static void note(const char *what, int port)
{
    char b[32];
    if (port < 0)
    {
        snprintf(b, sizeof(b), "%s%s", s_log[0] ? " " : "", what);
    }
    else
    {
        snprintf(b, sizeof(b), "%s%s%d", s_log[0] ? " " : "", what, port);
    }
    strncat(s_log, b, sizeof(s_log) - strlen(s_log) - 1);
}

static const char *calls(void)
{
    return s_log;
}

/* ----------------------------------------------- the platform stand-ins -- */

void rv_utils_intr_global_disable(void)
{
    note("intr_off", -1);
}

static jmp_buf s_restarted;

void __real_esp_restart_noos(void)
{
    note("restart", -1);
    longjmp(s_restarted, 1);
}

/* ------------------------------------------------- the scripted board -- */

#define MAX_PORTS 4
static int s_ports;
static board_can_kind_t s_kind[MAX_PORTS];
static esp_err_t s_stdby_ret[MAX_PORTS];
static esp_err_t s_stop_ret[MAX_PORTS];
static can_port_restart_report_t s_report[MAX_PORTS];

int board_can_ports(void)
{
    return s_ports;
}

board_can_kind_t board_can_kind(int port)
{
    return (port >= 0 && port < MAX_PORTS) ? s_kind[port] : BOARD_CAN_NONE;
}

esp_err_t board_can_standby(int port, bool on)
{
    note(on ? "stdby" : "normal", port);
    return (port >= 0 && port < MAX_PORTS) ? s_stdby_ret[port] : ESP_ERR_INVALID_ARG;
}

esp_err_t board_twai_pins(int port, board_twai_pins_t *out)
{
    (void)port;
    (void)out;
    return ESP_ERR_NOT_SUPPORTED;
}

/* ------------------------------------------ the scripted restart stop -- */

esp_err_t can_port_restart_stop(int port, can_port_restart_report_t *out)
{
    note("stop", port);
    if (port < 0 || port >= MAX_PORTS)
    {
        return ESP_ERR_INVALID_ARG;
    }
    *out = s_report[port];
    return s_stop_ret[port];
}

/* A TWAI stop that ran, every field set to something distinct. */
static can_port_restart_report_t twai_report(void)
{
    can_port_restart_report_t r;
    r.clock_on = true;
    r.held_in_reset = false;
    r.was_in_reset = true;
    r.status_at_entry = 0x0C4;
    r.tec = 17;
    r.rec = 11;
    r.status_at_reset = 0x00C;
    r.wait_us = 42;
    r.timed_out = true;
    r.aborts = 3;
    r.entered_reset = true;
    return r;
}

/* What a power-on leaves in RTC memory: garbage. */
static void power_on(void)
{
    memset(&s_rec, 0x5A, sizeof(s_rec));
}

/* One port, wican_v3's: TWAI, standby supported. */
static void reset(void)
{
    s_log[0] = '\0';
    s_ports = 1;
    for (int i = 0; i < MAX_PORTS; i++)
    {
        s_kind[i] = BOARD_CAN_NONE;
        s_stdby_ret[i] = ESP_OK;
        s_stop_ret[i] = ESP_OK;
        memset(&s_report[i], 0, sizeof(s_report[i]));
    }
    s_kind[0] = BOARD_CAN_TWAI;
    s_report[0] = twai_report();
    power_on();
}

/* Run the guard as esp_restart() would reach it, and come back. */
static void restart(void)
{
    s_log[0] = '\0';
    if (setjmp(s_restarted) == 0)
    {
        __wrap_esp_restart_noos();
    }
}

/* gen-inhibit's qz_sum() from gi_restart_quiesce.c at the move, written out
 * again here so the record's checksum is held to it (G4: unchanged). */
static uint32_t gi_sum(const restart_guard_record_t *r)
{
    const uint32_t *w = (const uint32_t *)r;
    uint32_t x = 0x9E3779B9u;
    for (size_t i = 0; i < 14; i++)
    {
        x = (x ^ w[i]) * 0x01000193u;
    }
    return x;
}

/* ------------------------------------------------------------- cases -- */

/* G4: gi_restart_record_t's layout, word by word, and its magic. */
static void case_record_layout(void)
{
    CHECK(sizeof(restart_guard_record_t) == 60);
    CHECK(RESTART_GUARD_RECORD_MAGIC == 0x51E5CE0Du);
    CHECK(offsetof(restart_guard_record_t, magic) == 0);
    CHECK(offsetof(restart_guard_record_t, entries) == 4);
    CHECK(offsetof(restart_guard_record_t, twai_clk) == 8);
    CHECK(offsetof(restart_guard_record_t, twai_rst) == 12);
    CHECK(offsetof(restart_guard_record_t, in_reset) == 16);
    CHECK(offsetof(restart_guard_record_t, st_entry) == 20);
    CHECK(offsetof(restart_guard_record_t, tec) == 24);
    CHECK(offsetof(restart_guard_record_t, rec) == 28);
    CHECK(offsetof(restart_guard_record_t, did_twai) == 32);
    CHECK(offsetof(restart_guard_record_t, st_reset) == 36);
    CHECK(offsetof(restart_guard_record_t, wait_us) == 40);
    CHECK(offsetof(restart_guard_record_t, timed_out) == 44);
    CHECK(offsetof(restart_guard_record_t, did_stdby) == 48);
    CHECK(offsetof(restart_guard_record_t, aborts) == 52);
    CHECK(offsetof(restart_guard_record_t, check) == 56);
}

/* G2, G3 on wican_v3's one TWAI port: interrupts off first, the stop, then
 * standby, then the real restart; the record holds the stop's report. */
static void case_one_twai_port(void)
{
    restart();
    CHECK_CALLS("intr_off stop0 stdby0 restart");
    CHECK(restart_guard_record_valid());
    CHECK(s_rec.magic == RESTART_GUARD_RECORD_MAGIC);
    CHECK(s_rec.check == gi_sum(&s_rec));
    CHECK(s_rec.entries == 1);
    CHECK(s_rec.twai_clk == 1 && s_rec.twai_rst == 0 && s_rec.in_reset == 1);
    CHECK(s_rec.st_entry == 0x0C4 && s_rec.tec == 17 && s_rec.rec == 11);
    CHECK(s_rec.did_twai == 1 && s_rec.st_reset == 0x00C);
    CHECK(s_rec.wait_us == 42 && s_rec.timed_out == 1 && s_rec.aborts == 3);
    CHECK(s_rec.did_stdby == 1);

    /* Clocked but held in reset: the stop touched nothing, and did_twai says
     * so, whatever the clock bit. */
    can_port_restart_report_t held = { 0 };
    held.clock_on = true;
    held.held_in_reset = true;
    s_report[0] = held;
    restart();
    CHECK(s_rec.twai_clk == 1 && s_rec.twai_rst == 1);
    CHECK(s_rec.did_twai == 0 && s_rec.in_reset == 0 && s_rec.timed_out == 0);
    CHECK(s_rec.entries == 2);
}

/* G4: the count keeps going across restarts; a power-on's garbage, or a
 * record whose checksum is off, starts it again at 1. */
static void case_entries_count(void)
{
    restart();
    restart();
    restart();
    CHECK(s_rec.entries == 3 && restart_guard_record_valid());
    s_rec.check ^= 1u;
    CHECK(!restart_guard_record_valid());
    restart();
    CHECK(s_rec.entries == 1 && restart_guard_record_valid());
    power_on();
    s_rec.magic = RESTART_GUARD_RECORD_MAGIC;   /* the magic alone is not enough */
    restart();
    CHECK(s_rec.entries == 1);
}

/* G4, the test build's start-up: init makes the record valid at 0, so the
 * first restart after a power-on reads 1; get copies it. */
static void case_record_init_and_get(void)
{
    CHECK(!restart_guard_record_valid());
    restart_guard_record_init();
    CHECK(restart_guard_record_valid());
    restart_guard_record_t r;
    restart_guard_record_get(&r);
    CHECK(r.magic == RESTART_GUARD_RECORD_MAGIC && r.entries == 0);
    CHECK(r.did_stdby == 0 && r.twai_clk == 0 && r.check == gi_sum(&r));
    restart();
    restart_guard_record_get(&r);
    CHECK(r.entries == 1 && r.did_twai == 1);
}

/* G3: a stop that answers "not supported" (a TWAI port on a chip with no
 * measured file) leaves the TWAI fields at 0, and the steps go on. */
static void case_stop_not_supported(void)
{
    s_stop_ret[0] = ESP_ERR_NOT_SUPPORTED;
    restart();
    CHECK_CALLS("intr_off stop0 stdby0 restart");
    CHECK(s_rec.twai_clk == 0 && s_rec.in_reset == 0 && s_rec.st_entry == 0);
    CHECK(s_rec.tec == 0 && s_rec.did_twai == 0 && s_rec.wait_us == 0);
    CHECK(s_rec.timed_out == 0 && s_rec.aborts == 0);
    CHECK(s_rec.did_stdby == 1 && s_rec.entries == 1);
    CHECK(restart_guard_record_valid());
}

/* G3, every port the board has, in port order, each stop then its standby
 * (the X2's shape: TWAI then SPI, neither with a standby line). The TWAI
 * fields come from the TWAI port only; standby is one bit per port. */
static void case_two_ports(void)
{
    s_ports = 2;
    s_kind[1] = BOARD_CAN_SPI;
    s_stop_ret[1] = ESP_ERR_NOT_SUPPORTED;
    s_stdby_ret[0] = s_stdby_ret[1] = ESP_ERR_NOT_SUPPORTED;
    restart();
    CHECK_CALLS("intr_off stop0 stdby0 stop1 stdby1 restart");
    CHECK(s_rec.did_stdby == 0);
    CHECK(s_rec.did_twai == 1 && s_rec.st_entry == 0x0C4);

    /* Standby on port 1 only: bit 1. */
    s_stdby_ret[1] = ESP_OK;
    restart();
    CHECK(s_rec.did_stdby == 2);
    s_stdby_ret[0] = ESP_OK;
    restart();
    CHECK(s_rec.did_stdby == 3);
}

/* A stop that ran on a port that is not TWAI (an SPI driver's, from stage
 * (b)) does not fill the TWAI fields; the TWAI port's at another index
 * does. */
static void case_twai_fields_from_twai_port(void)
{
    s_ports = 2;
    s_kind[0] = BOARD_CAN_SPI;
    s_kind[1] = BOARD_CAN_TWAI;
    s_report[1] = twai_report();
    s_report[0] = twai_report();
    s_report[0].status_at_entry = 0x111;
    s_report[0].aborts = 9;
    restart();
    CHECK_CALLS("intr_off stop0 stdby0 stop1 stdby1 restart");
    CHECK(s_rec.did_twai == 1 && s_rec.st_entry == 0x0C4 && s_rec.aborts == 3);

    /* The TWAI port's stop not supported: the SPI port's report stays out. */
    s_stop_ret[1] = ESP_ERR_NOT_SUPPORTED;
    restart();
    CHECK(s_rec.did_twai == 0 && s_rec.twai_clk == 0 && s_rec.st_entry == 0);
    CHECK(s_rec.aborts == 0);
}

/* A board with no CAN ports: nothing called but the restart; the record is
 * still written. */
static void case_no_ports(void)
{
    s_ports = 0;
    restart();
    CHECK_CALLS("intr_off restart");
    CHECK(restart_guard_record_valid() && s_rec.entries == 1);
    CHECK(s_rec.did_stdby == 0 && s_rec.did_twai == 0);
}

int main(void)
{
    static const struct { const char *name; void (*fn)(void); } cases[] = {
        { "record_layout", case_record_layout },
        { "one_twai_port", case_one_twai_port },
        { "entries_count", case_entries_count },
        { "record_init_and_get", case_record_init_and_get },
        { "stop_not_supported", case_stop_not_supported },
        { "two_ports", case_two_ports },
        { "twai_fields_from_twai_port", case_twai_fields_from_twai_port },
        { "no_ports", case_no_ports },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        s_case = cases[i].name;
        reset();
        cases[i].fn();
    }
    printf("%s: %d checks, %d failed, %u cases\n", s_fail ? "FAIL" : "PASS",
           s_checks, s_fail, (unsigned)(sizeof(cases) / sizeof(cases[0])));
    return s_fail ? 1 : 0;
}
