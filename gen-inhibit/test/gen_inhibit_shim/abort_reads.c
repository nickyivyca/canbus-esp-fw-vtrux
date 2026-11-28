/*
 * WHEN THE SCHEDULER READ THE BUFFER THROUGH ABORT-IF-AWAITING (spec 5.2
 * item 5, reviewer ruling 2026-11-27: a release or trip withdraws the answer
 * on the latch's own pass). Every twai_port_abort_if_awaiting() call the
 * firmware makes is logged with its virtual time and the state it returned.
 * gi_sched makes the call in begin_abort() and abort_step(), and through
 * reread_past_bound() past the 1 ms bound, so the first logged read after a
 * latch is the withdrawal's first read. ft_ll_aborts() counts only the reads
 * that issued the command; this log has the ones that did not too.
 *
 * --wrap, as in deliver_at_count.c. The port's own calls (the restart stop's,
 * inside twai_port_ll_esp32c3.c) are not wrapped, and the shim makes none.
 *
 * A FILE OF ITS OWN, linked only by this folder's Makefile, for the reason
 * deliver_at_count.c is: the tester's spec shim compiles shim_harness.c
 * without these --wrap flags, and the __real_ reference would not link there.
 */
#include "shim_harness.h"

#include "twai_port.h"

#define AR_MAX 8192

static int             g_ar_n;
static int64_t         g_ar_at[AR_MAX];
static twai_port_buf_t g_ar_state[AR_MAX];

twai_port_buf_t __real_twai_port_abort_if_awaiting(void);
twai_port_buf_t __wrap_twai_port_abort_if_awaiting(void);

twai_port_buf_t __wrap_twai_port_abort_if_awaiting(void)
{
    const int64_t at = ft_now();
    const twai_port_buf_t b = __real_twai_port_abort_if_awaiting();
    if (g_ar_n < AR_MAX)
    {
        g_ar_at[g_ar_n] = at;
        g_ar_state[g_ar_n] = b;
    }
    g_ar_n++;
    return b;
}

void ft_abort_reads_reset(void)
{
    g_ar_n = 0;
}

int ft_abort_reads(void)
{
    return g_ar_n;
}

int64_t ft_abort_read_at(int i)
{
    return (i >= 0 && i < g_ar_n && i < AR_MAX) ? g_ar_at[i] : -1;
}

int ft_abort_read_state(int i)
{
    return (i >= 0 && i < g_ar_n && i < AR_MAX) ? (int)g_ar_state[i] : -1;
}
