/*
 * gi_diag_boot.h -- BOOT DIAGNOSTIC, DIAG BUILD ONLY. NOT FOR THE VEHICLE.
 * See gi_diag_boot.c. Present only on the `gen-inhibit/diag-reset` branch.
 */
#ifndef GI_DIAG_BOOT_H
#define GI_DIAG_BOOT_H

/* Prints esp_reset_reason() by name at once, then an uptime line once a second
 * for the first 30 s. Call as early in app_main() as the logger allows. */
void gi_diag_boot_init(void);

#endif /* GI_DIAG_BOOT_H */
