/*
 * Replay one host scenario through the SHIM (gen_inhibit.c, the scheduler and can.c
 * as shipped, on fake_twai) and print every frame the fake driver put on the wire,
 * with its completion time from the start of the replay. Gen-inhibit tester,
 * 2026-10-16, for the reviewer's question on liveflag-mmode: do the diag pages stop
 * after the M-mode latch in emulation too, and for how long?
 *
 * It does NOT edit the implementor's shim_test.c: it includes it, renames its main,
 * and reuses its setup(), hexnib() and the fake driver. Built by shim_wire_probe.sh on
 * madhouse-debian, where host C runs count for VERIFICATION.md (NICKY-XPS's WSL gcc is
 * for self-checks only), in a copy of the shim folder.
 *
 * WHY ITS OWN REPLAY LOOP. shim_test.c's replay_scn() drains the receive queue with
 * `while (ft_rx_pending() > 0) ft_run(0);`, which never ends once the worker stops
 * taking frames: on disable-clears-live-flag the first build spun at 100 % CPU for
 * 7 min and was killed. This loop parses the same `mode` / `f` / `end` lines but
 * gives up on a drain after DRAIN_STALL turns without progress, and reports each
 * stalled drain (`S` lines) instead of hanging.
 *
 *   ./shim_wire_probe <scenario name>       (scenarios/ then ../gen_inhibit_host/scenarios/)
 *
 * Output lines:
 *   M <t_us> <mode>                                     a mode line applied
 *   S <t_us> <pending>                                  a drain that stopped making progress
 *   REPLAY frames <n> end_us <us> wire <n> saturated <0|1> stalls <n>
 *   W <t_us> <id hex> <dlc> <data hex> <foreign 0|1> <failed 0|1>
 */
#define main shim_suite_main
#include "shim_test.c"
#undef main

#include <unistd.h>

#define DRAIN_STALL 64

static FILE *open_scn(const char *name)
{
    char path[512];
    snprintf(path, sizeof(path), "%s%s.scn", SCN_DIR, name);
    FILE *fh = fopen(path, "r");
    if (fh == NULL)
    {
        snprintf(path, sizeof(path), "%s%s.scn", SCN_DIR2, name);
        fh = fopen(path, "r");
    }
    return fh;
}

int main(int argc, char **argv)
{
    const char *name = (argc > 1) ? argv[1] : "disable-clears-live-flag";
    FILE *fh = open_scn(name);
    if (fh == NULL)
    {
        fprintf(stderr, "no scenario %s\n", name);
        return 2;
    }
    setup();
    const int64_t base = ft_now();
    long frames = 0, stalls = 0, last_stall_at = -1;
    long end_us = 0;
    static char line[512];
    while (fgets(line, (int)sizeof(line), fh) != NULL)
    {
        long at = 0, mode = 0, off = 0, us = 0, dlc = 0;
        char idbuf[16], hexbuf[33];
        if (sscanf(line, "mode %ld %ld %ld", &at, &mode, &off) == 3)
        {
            if (base + at > ft_now()) { ft_run(base + at - ft_now()); }
            gen_inhibit_set_mode((gen_inhibit_mode_t)mode, (uint32_t)off);
            printf("M %ld %ld\n", at, mode);
            continue;
        }
        if (sscanf(line, "end %ld", &us) == 1)
        {
            end_us = us;
            if (base + us > ft_now()) { ft_run(base + us - ft_now()); }
            continue;
        }
        if (sscanf(line, "f %ld %15s %ld %32s", &us, idbuf, &dlc, hexbuf) != 4) { continue; }
        const uint32_t id = (uint32_t)strtoul(idbuf, NULL, 16);
        uint8_t data[8] = { 0 };
        const size_t hl = strlen(hexbuf);
        for (size_t i = 0; i + 1 < hl && i / 2 < 8; i += 2)
        {
            data[i / 2] = (uint8_t)((hexnib(hexbuf[i]) << 4) | hexnib(hexbuf[i + 1]));
        }
        if (base + us > ft_now()) { ft_run(base + us - ft_now()); }
        ft_deliver(id, data, (uint8_t)(dlc > 8 ? 8 : dlc));
        frames++;
        int idle = 0;
        while (ft_rx_pending() > 0 && idle < DRAIN_STALL)
        {
            const int p = ft_rx_pending();
            ft_run(0);
            idle = (ft_rx_pending() < p) ? 0 : idle + 1;
        }
        if (ft_rx_pending() > 0)
        {
            stalls++;
            if (last_stall_at < 0 || us - last_stall_at > 100000)
            {
                printf("S %ld %d\n", us, ft_rx_pending());
            }
            last_stall_at = us;
        }
    }
    fclose(fh);
    printf("REPLAY frames %ld end_us %ld wire %d saturated %d stalls %ld\n", frames, end_us,
           ft_wire_count(), ft_logs_saturated() ? 1 : 0, stalls);
    for (int i = 0; i < ft_wire_count(); i++)
    {
        const ft_frame_t *f = ft_wire(i);
        printf("W %lld %03X %u ", (long long)(f->t_done - base), (unsigned)f->id,
               (unsigned)f->dlc);
        for (int k = 0; k < f->dlc && k < 8; k++) { printf("%02x", f->data[k]); }
        printf(" %d %d\n", f->foreign ? 1 : 0, f->failed ? 1 : 0);
    }
    fflush(stdout);
    _exit(0);       /* the worker thread is still blocked in the fake driver */
}
