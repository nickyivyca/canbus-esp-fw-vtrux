# gen-inhibit's own firmware: app_main, the inhibitor and its hooks into the
# stock code (gi_wican.c, spec 3.3), port 0's setup among them. The stock
# WiCAN code is the component vtrux/wican; its CAN calls are vtrux/can_port's
# on port 0, backed by vtrux/twai_port (spec 3.3, port step 1b; can.c
# retired). Those, vtrux/debug_logs and the board all come from main's
# idf_component.yml; a managed dependency of main is one of its requirements
# without being listed below.
set(srcs "main.c" "gen_inhibit.c" "gen_inhibit_core.c" "gi_sched.c" "gi_restart_quiesce.c" "gi_wican.c")
# hal and soc: the restart step reads the TWAI controller's registers
# (gi_restart_quiesce.c). The abort command is twai_port's (T8), which E3
# confines to its one site.
set(requires   hal soc esp_timer esp_wifi nvs_flash fatfs vfs driver esp-tls esp_adc esp_eth log app_update esp_http_server spiffs freertos mqtt json)

# TX-ABORT MEASUREMENT BUILD, spec 5.2 item 9 / spec 12.4's C3-abort row.
#
#   idf.py -B build-txabort -DGI_INSTRUMENT_TXABORT=1 build
#
# The source is ADDED TO THE COMPONENT here rather than compiled always and
# guarded inside with #if. A guard inside a compiled file leaves the abort
# command one #define away from a shipping image; a file that is not in the
# build cannot be linked by accident. `hal` and `soc` are added for the same
# reason -- twai_ll.h and the TWAI register struct are private headers, and the
# shipping build's component graph stays exactly as it was without them.
#
# Never flash a build configured this way to the truck. E3 refuses it.
if(GI_INSTRUMENT_TXABORT)
    list(APPEND srcs "gi_txabort_probe.c")
    list(APPEND requires "hal" "soc")
endif()

# RESTART TEST BUILD, for spec 12.4's "no error frames at a restart" row.
#
#   idf.py -B build-restart-test -DGI_TEST_RESTART_QUIESCE=1 build
#
# Adds a reader for the restart record, a crash trigger and two snapshots of the
# controller and CAN_STDBY -- see gi_restart_test.h. The step itself is in every
# build (gi_restart_quiesce.c, below); this adds only what lets a bench see it.
# The source is added to the component only here, as gi_txabort_probe.c is.
#
# Never in a prod build (spec 12.4). The shared build tooling's build-switch
# check (shared/tools SPEC R20, rows in vtrux_checks.json) refuses a release
# image carrying the test symbols.
if(GI_TEST_RESTART_QUIESCE)
    list(APPEND srcs "gi_restart_test.c")
endif()

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

# EVERY SOFTWARE RESTART TAKES THE CONTROLLER OFF THE BUS FIRST (spec 3, user
# 2026-10-13). In every build, with no switch. Every caller of
# esp_restart_noos() -- esp_restart(), panic_restart() and call_start_cpu0's
# early-boot failures -- is in another object file of esp_system, so --wrap
# redirects all of them to __wrap_esp_restart_noos in gi_restart_quiesce.c,
# which reaches the real function as __real_esp_restart_noos.
target_link_libraries(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=esp_restart_noos")

if(GI_TEST_RESTART_QUIESCE)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE GI_TEST_RESTART_QUIESCE=1)
    message(STATUS "gen_inhibit: RESTART TEST build -- /gi_quiesce and its "
                   "crash trigger are registered")
endif()

if(GI_INSTRUMENT_TXABORT)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE GI_INSTRUMENT_TXABORT=1)
    message(STATUS "gen_inhibit: TX ABORT probe ENABLED (measurement build) -- "
                   "writes the TWAI abort command register directly")
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
