/*
 * test_main.c -- the jammer app (main/jammer_main.c, main/console_usj.c)
 * with the real pulse engine and MCP2515 reset, on the host fakes. TEST
 * EQUIPMENT, NOT FOR VEHICLE.
 *
 * Checked (x2_test_images SPEC T8, jammer/README.md):
 *  - start-up order: CAN1's TXD driven recessive first, then the MCP2515
 *    reset, the console, the engine and line tasks, the banner last;
 *  - every line the app prints is one whole "JAMMER <word> ..." line, the
 *    form the gen-inhibit harness parses (x2_jammer.py parse_line);
 *  - the boot banner: the image name with NOT_FOR_VEHICLE and "TEST
 *    EQUIPMENT, NEVER ON THE TRUCK" (SPEC T8: the image name and the boot
 *    banner, user 2026-11-12), the build (version and ELF hash, SPEC T10),
 *    CANSTAT with config_mode, the stops and the limits;
 *  - the line handling: ID, unknown words, line ends, an over-long line.
 */
#include <string.h>

#include "check.h"
#include "console_usj.h"
#include "fake_idf.h"
#include "jam_internal.h"
#include "jammer_main.h"

void app_main(void);

#define PREFIX "JAMMER "

/* Every line written to the port: prefix first, "\r\n" last, no bare line
 * end inside. Returns the number of lines. */
static int check_lines(const char *what)
{
    int lines = 0;
    const char *p = fake_out;
    while (*p) {
        const char *e = strstr(p, "\r\n");
        CHECK(e != NULL, "%s: output ends without a line end: '%s'", what, p);
        if (!e) break;
        CHECK(!strncmp(p, PREFIX, strlen(PREFIX)),
              "%s: a line without the prefix: '%.*s'", what, (int)(e - p), p);
        for (const char *q = p; q < e; q++)
            CHECK(*q != '\r' && *q != '\n', "%s: a bare line end", what);
        lines++;
        p = e + 2;
    }
    return lines;
}

static int has(const char *s)
{
    return strstr(fake_out, s) != NULL;
}

static int count_of(const char *s)
{
    int n = 0;
    for (const char *p = fake_out; (p = strstr(p, s)) != NULL; p++) n++;
    return n;
}

static void clear_out(void)
{
    fake_out_n = 0;
    fake_out[0] = 0;
}

static void feed(const char *s)
{
    jammer_feed((const uint8_t *)s, strlen(s));
}

static void run_queued(void)
{
    jam_cmd_t c;
    if (jam_engine_take(&c, 0)) jam_engine_run(&c);
}

static void test_start(void)
{
    fake_reset();
    app_main();
    int hi = fake_log_find("gpio_set_level 7 1", 0);
    int out = fake_log_find("gpio_set_direction 7 out", 0);
    int spi = fake_log_find("spi_bus_initialize", 0);
    int rst = fake_log_find("spi c0", 0);
    int usb = fake_log_find("usb_install", 0);
    int jam = fake_log_find("task jam", 0);
    int line = fake_log_find("task jam_line", 0);
    int dly = fake_log_find("delay 300", 0);
    CHECK(hi == 0, "the first call is not pin 7 high: '%s'", fake_log[0]);
    CHECK(out == 1, "the second call is not pin 7 output: '%s'", fake_log[1]);
    CHECK(spi > out && rst > spi, "MCP2515 reset not after the pin (%d %d)",
          spi, rst);
    CHECK(usb > rst, "console not after the MCP2515 reset (%d)", usb);
    CHECK(jam > usb && line > jam, "tasks not after the console (%d %d)", jam,
          line);
    CHECK(dly > line, "the banner delay not last (%d)", dly);
    CHECK(fake_log_find("rmt_new", 0) < 0, "an RMT channel at start");
    CHECK(fake_log_find("gpio_set_level 7 0", 0) < 0, "pin 7 driven low");

    int n = check_lines("banner");
    CHECK(n == 5, "%d banner lines, not 5", n);
    static const char first[] = PREFIX "vtrux_jammer_x2_NOT_FOR_VEHICLE -- "
                                "TEST EQUIPMENT, NEVER ON THE TRUCK\r\n";
    CHECK(!strncmp(fake_out, first, sizeof first - 1),
          "the banner's first line is not the name line: %.80s", fake_out);
    CHECK(has(PREFIX "build version=2026.11.12.2-1-gabcdef0 elf="
              "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
              " idf=v5.4.2\r\n"), "banner build line");
    CHECK(has(PREFIX "tx=GPIO7(CAN1/TWAI) twai=never_started stops=a+b "
              "reset_reason=1 uptime_us="), "banner tx line");
    CHECK(has(PREFIX "mcp2515 canstat=0x80 canctrl=0x87 config_mode=yes\r\n"),
          "banner mcp2515 line");
    CHECK(has(PREFIX "limits w_us=12..50 p_us>=4w..20000 ms<=30000 "
              "n<=1000 interval_ms=1..10000\r\n"), "banner limits line");
    /* The gen-inhibit harness takes the uptime from a banner line before
     * the mcp2515 line (x2_jammer.py sub_id). */
    CHECK(strstr(fake_out, "uptime_us=") < strstr(fake_out, "mcp2515 "),
          "uptime_us not before the mcp2515 line");
    /* The banner comes after the 300 ms wait, so a host that opens the port
     * at reset sees it. */
    CHECK(fake_now_us >= 1000000 + 300000, "banner before the wait");
}

static void test_mcp_states(void)
{
    fake_reset();
    fake_mcp_canstat_after_reset = 0x00;
    app_main();
    CHECK(has(PREFIX "mcp2515 canstat=0x00 canctrl=0x87 config_mode=NO\r\n"),
          "a chip out of configuration mode not reported NO");
    fake_reset();
    fake_spi_bus_init_result = ESP_FAIL;
    app_main();
    CHECK(has(PREFIX "mcp2515 reset_failed err=0xffffffff config_mode=NO\r\n"),
          "a failed reset not reported");
    CHECK(fake_log_find("task jam", 0) >= 0, "a failed reset stopped the app");
    check_lines("mcp states");
}

static void test_lines(void)
{
    fake_reset();
    app_main();
    clear_out();
    jammer_handle_line("ID");
    CHECK(check_lines("ID") == 5
          && has(PREFIX "vtrux_jammer_x2_NOT_FOR_VEHICLE -- "), "ID banner");
    clear_out();
    jammer_handle_line("  ID extra");
    CHECK(check_lines("ID extra") == 5, "ID with words after");
    clear_out();
    jammer_handle_line("IDX");
    CHECK(has(PREFIX "ERR unknown_command\r\n") && check_lines("IDX") == 1,
          "IDX not unknown");
    clear_out();
    jammer_handle_line("FOO 1 2 3");
    CHECK(has(PREFIX "ERR unknown_command\r\n"), "FOO not unknown");
    clear_out();
    jammer_handle_line("TRAIN 20 5000");
    CHECK(has(PREFIX "ERR need_exactly_3_args got=2\r\n"), "refusal line");

    /* Bytes: '\r' dropped, '\n' ends the line, several lines in one read. */
    clear_out();
    feed("STAT");
    CHECK(fake_out_n == 0, "a part line was handled");
    feed("US\r\nSTATUS\n");
    CHECK(check_lines("feed") == 2, "two STATUS lines, got '%s'", fake_out);
    CHECK(count_of(PREFIX "STATUS idle uptime_us=") == 2 && !has("ERR"),
          "both lines are STATUS (a '\\r' kept would make the first "
          "unknown): '%s'", fake_out);

    /* An over-long line is cut to 63 characters and handled once; the next
     * line starts clean. */
    clear_out();
    char longl[200];
    memset(longl, 'A', 150);
    strcpy(longl + 150, "\nSTATUS\n");
    feed(longl);
    CHECK(check_lines("long") == 2, "long line: %s", fake_out);
    CHECK(has(PREFIX "ERR unknown_command\r\n" PREFIX "STATUS idle"),
          "long line then STATUS: %s", fake_out);

    /* A command's replies carry the prefix too. */
    clear_out();
    feed("TRAIN 20 5000 60000\n");
    run_queued();
    CHECK(has(PREFIX "CAPPED ms 60000 -> 30000\r\n"), "CAPPED line");
    CHECK(has(PREFIX "ACK TRAIN w_us=20 p_us=5000 ms=30000 "), "ACK line");
    CHECK(has(PREFIX "DONE TRAIN end=deadline_layer_b stop_by=none "),
          "DONE line");
    check_lines("train");
    CHECK(fake_gpio_level[7] == 1 && fake_gpio_output[7] == 1,
          "pin 7 not recessive after the train");
}

static void test_console(void)
{
    fake_reset();
    app_main();
    clear_out();
    jcon_line("a\nb\rc");
    CHECK(!strcmp(fake_out, PREFIX "a b c\r\n"), "line ends inside: '%s'",
          fake_out);
    clear_out();
    char big[400];
    memset(big, 'z', sizeof big - 1);
    big[sizeof big - 1] = 0;
    jcon_line(big);
    CHECK(check_lines("big") == 1, "a long line split");
    CHECK(fake_out_n == CONSOLE_LINE_MAX, "a long line is %zu bytes, not cut "
          "to %d", fake_out_n, CONSOLE_LINE_MAX);
}

int main(void)
{
    test_start();
    test_mcp_states();
    test_lines();
    test_console();
    return check_end("test_main");
}
