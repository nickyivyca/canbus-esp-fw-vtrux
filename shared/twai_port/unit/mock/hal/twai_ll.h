/*
 * Host stand-in for ESP-IDF 5.4.1's and 5.4.2's esp32c3 hal/twai_ll.h
 * (byte-identical in the two; the esp32s3 one has the same calls): the status
 * register is a value the case sets, and the abort command is recorded. No
 * controller behaviour: writing the command changes nothing the next read
 * returns. For the restart stop (T12): the reset-mode bit, the error counters
 * and the enter-reset command, recorded with the status it was written at;
 * the scripted 2 us wait (stub.c's esp_rom_delay_us) is what changes the
 * status. For the kept-driver switch (T14, T15): the mode written, the REC
 * written and the exit from reset mode, each recorded.
 */
#ifndef MOCK_TWAI_LL_H
#define MOCK_TWAI_LL_H
#include <stdbool.h>
#include <stdint.h>
typedef struct {
    uint32_t status;
    int abort_cmds;
    bool reset_mode;
    uint32_t tec, rec;
    int enter_reset_cmds;
    uint32_t status_at_enter;   /* the status when reset mode was entered */
    int delays_at_enter;        /* the waits made before it */
    int exit_reset_cmds;
    int mode;                   /* the last twai_ll_set_mode() */
    int mode_writes;
    bool mode_set_in_reset;     /* the last mode write was in reset mode */
    int rec_writes;
    bool rec_set_in_reset;      /* the last REC write was in reset mode */
} twai_dev_t;
extern twai_dev_t stub_twai_dev;
extern int stub_delays;         /* esp_rom_delay_us() calls so far */
#define TWAI_LL_GET_HW(id) ((id) == 0 ? &stub_twai_dev : (twai_dev_t *)0)
#define TWAI_LL_STATUS_TBS (0x1 << 2)
#define TWAI_LL_STATUS_RS  (0x1 << 4)
#define TWAI_LL_STATUS_TS  (0x1 << 5)
static inline uint32_t twai_ll_get_status(twai_dev_t *hw) { return hw->status; }
static inline void twai_ll_set_cmd_abort_tx(twai_dev_t *hw) { hw->abort_cmds++; }
static inline bool twai_ll_is_in_reset_mode(twai_dev_t *hw) { return hw->reset_mode; }
static inline uint32_t twai_ll_get_tec(twai_dev_t *hw) { return hw->tec; }
static inline uint32_t twai_ll_get_rec(twai_dev_t *hw) { return hw->rec; }
static inline void twai_ll_enter_reset_mode(twai_dev_t *hw)
{
    hw->enter_reset_cmds++;
    hw->status_at_enter = hw->status;
    hw->delays_at_enter = stub_delays;
    hw->reset_mode = true;
}
static inline void twai_ll_exit_reset_mode(twai_dev_t *hw)
{
    hw->exit_reset_cmds++;
    hw->reset_mode = false;
}
static inline void twai_ll_set_mode(twai_dev_t *hw, int mode)
{
    hw->mode = mode;
    hw->mode_writes++;
    hw->mode_set_in_reset = hw->reset_mode;
}
static inline void twai_ll_set_rec(twai_dev_t *hw, uint32_t rec)
{
    hw->rec = rec;
    hw->rec_writes++;
    hw->rec_set_in_reset = hw->reset_mode;
}
#endif
