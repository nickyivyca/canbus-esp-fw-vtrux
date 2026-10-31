/*
 * gi_wican -- gen-inhibit's hooks into the shared stock code (spec 3.3). See
 * gi_wican.c.
 */
#ifndef GI_WICAN_H
#define GI_WICAN_H

/* Sets port 0 up and registers it with vtrux/can_port, then registers the
 * bus-owner callbacks and the URI hook with vtrux/wican. Call in app_main
 * before wican_init() (shared/wican W4). */
void gi_wican_register(void);

#endif
