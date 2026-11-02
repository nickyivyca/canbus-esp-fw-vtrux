# gen-inhibit's own firmware: app_main, the inhibitor and its hooks into the
# stock code (gi_wican.c, spec 3.3), port 0's setup among them. The stock
# WiCAN code is the component vtrux/wican; its CAN calls are vtrux/can_port's
# on port 0, backed by vtrux/twai_port (spec 3.3, port step 1b; can.c
# retired). Every software restart takes port 0 off the bus first through
# vtrux/restart_guard (spec 3, port step 1c), which sets its own link-time
# wrap. Those, vtrux/debug_logs and the board all come from main's
# idf_component.yml; a managed dependency of main is one of its requirements
# without being listed below.
set(srcs "main.c" "gen_inhibit.c" "gen_inhibit_core.c" "gi_sched.c" "gi_wican.c")
# hal and soc: the restart step's register reads are twai_port's now (T12),
# and the abort command with them (T8), which E3 confines to its one site;
# they stay listed so this component's graph is as it was.
set(requires   hal soc esp_timer esp_wifi nvs_flash fatfs vfs driver esp-tls esp_adc esp_eth log app_update esp_http_server spiffs freertos mqtt json)

# THE TWO TEST APPS ARE THE SHARED COMPONENTS' (port step 1c), built in by
# their own switches, which those components read themselves:
#
#   idf.py -B build-txabort -DTWAI_PORT_TXABORT_PROBE=1 build       (twai_port T13)
#   idf.py -B build-restart-test -DRESTART_GUARD_TEST=1 build       (restart_guard G5)
#
# The TX-abort probe is spec 5.2 item 9's measurement, for spec 12.4's C3-abort
# row; the restart test is for spec 12.4's "no error frames at a restart" row.
# This component only registers them (gi_wican.c) and calls the restart test's
# pre-install snapshot (gen_inhibit.c), so it takes the same switches as
# defines below. Never flash a build configured either way to the truck: the
# shared build tooling's build-switch check (shared/tools SPEC R20, rows in
# vtrux_checks.json) refuses a release image carrying their symbols.

idf_component_register(
    SRCS "${srcs}"
    REQUIRES "${requires}"
)

# MEASUREMENT-ONLY INSTRUMENT, OFF UNLESS ASKED FOR EXPLICITLY.
#
#   idf.py -B build-rxq -DGI_INSTRUMENT_RXQ=1 build
#
# Enables the RX-backlog instrument in gen_inhibit.c, which measures the one
# quantity neither device timing instrument can see: how many frames were waiting
# in the driver's queue when the worker dequeued one. Build it into a SEPARATE
# directory -- ESP-IDF names the image from the commit, so an instrument build
# and a shipping build of the same commit produce the same filename, and this
# project has already been caught once by a mislabelled image.
#
# Never flash a build configured this way to the truck.
if(GI_INSTRUMENT_RXQ)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE GI_INSTRUMENT_RXQ=1)
    message(STATUS "gen_inhibit: RX backlog instrument ENABLED (measurement build)")
endif()

if(RESTART_GUARD_TEST)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE RESTART_GUARD_TEST=1)
    message(STATUS "gen_inhibit: RESTART TEST build -- /gi_quiesce and its "
                   "crash trigger are registered")
endif()

if(TWAI_PORT_TXABORT_PROBE)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE TWAI_PORT_TXABORT_PROBE=1)
    message(STATUS "gen_inhibit: TX ABORT probe registered (measurement build) "
                   "-- it writes the TWAI abort command register directly")
endif()

# ABORT-WINDOW MEASUREMENT BUILD.
#
#   idf.py -B build-abortwin -DGI_INSTRUMENT_ABORTWIN=1 build
#
# Counts how often gs_command_received() is entered with `held && aborting`,
# and how often the controller had ALSO freed the frame by then -- the window
# the pre-fix note_left_controller() defect needed. Read-only: it makes no
# scheduling decision. Unlike the TXABORT probe it adds no source file and
# touches no private header, so the guard lives inside gi_sched.c; what it adds
# is one twai_get_status_info() read inside an already-rare state.
#
# Never flash a build configured this way to the truck. E3 refuses it.
if(GI_INSTRUMENT_ABORTWIN)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE GI_INSTRUMENT_ABORTWIN=1)
    message(STATUS "gen_inhibit: ABORT WINDOW counters ENABLED (measurement build)")
endif()

# AUTO-ARM BUILD, for spec 12.4's auto-arm row.
#
#   idf.py -B build-autoarm -DGEN_INHIBIT_AUTOARM=GEN_INHIBIT_INHIBIT build
#
# A plain `idf.py -D` sets a CMake cache variable, not a preprocessor macro,
# which is why gen_inhibit.c's own comment says the override has to come through
# target_compile_definitions. The value is an enum name from gen_inhibit.h and is
# compared at run time, not by the preprocessor, so the name works directly.
#
# This is the DEPLOYED configuration (relay power -> boot -> inhibiting, no human
# in the loop), not a measurement hack -- but it is not the current default, so it
# gets its own build directory for the same reason build-rxq does: two
# configurations of one commit produce the same image filename.
if(GEN_INHIBIT_AUTOARM)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        GEN_INHIBIT_AUTOARM_MODE=${GEN_INHIBIT_AUTOARM})
    message(STATUS "gen_inhibit: AUTO-ARM build, mode ${GEN_INHIBIT_AUTOARM}")
endif()
