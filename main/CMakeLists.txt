# See the build system documentation in IDF programming guide
# for more information about component CMakeLists.txt files.
set(srcs "main.c" "comm_server.c" "config_server.c" "slcan.c" "can.c" "wifi_network.c" "wc_uart.c" "elm327.c" "mqtt.c" "sleep_mode.c" "autopid.c" "expression_parser.c" "wc_mdns.c" "wc_timer.c" "dev_status.c" "ota_health.c" "gen_inhibit.c" "gen_inhibit_core.c" "gi_sched.c" "gi_diag_boot.c")

# DIAG BUILD ONLY -- `gi_diag_boot.c` is listed unconditionally because this
# branch (`gen-inhibit/diag-reset`) exists only to produce a diagnostic image
# and must never be merged. See the file's own header.
# hal and soc: the scheduler reaches the TWAI status register and the abort
# command through the private HAL (spec 5.2 items 4 and 9, cleared by the user
# 2026-09-27). E3 confines that call to one site.
set(requires   hal soc esp_timer esp_wifi nvs_flash fatfs vfs driver esp-tls esp_adc esp_eth log app_update esp_http_server spiffs freertos mqtt json debug_logs)

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

idf_component_register(
    SRCS "hw_config.c" "wc_timer.c" "autopid.c" "" "${srcs}"        # list the source files of this component
    INCLUDE_DIRS        # optional, add here public include directories
    PRIV_INCLUDE_DIRS   # optional, add here private include directories
    REQUIRES   "${requires}"         # optional, list the public requirements (component names)
    PRIV_REQUIRES       # optional, list the private requirements
    EMBED_FILES "homepage.html"
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

# ROLLBACK REHEARSAL BUILD -- an image that deliberately fails its health gate.
#
#   idf.py -B build-otafault -DOTA_HEALTH_FAULT_INJECT=1 build
#
# `ota_health.c` has carried the OTA_HEALTH_FAULT_INJECT hook since a22da84 and
# its own docstring tells you to pass `-DOTA_HEALTH_FAULT_INJECT=<bits>`, but
# until now NOTHING WIRED IT TO THE BUILD. A bare `idf.py -D` sets a CMake cache
# variable, not a preprocessor macro -- the same trap GEN_INHIBIT_AUTOARM
# documents directly above -- so following that docstring produced an ordinary
# image with no fault in it, silently. That is the worst outcome this flag has
# available: a rehearsal that reports "it did not roll back" about an image that
# was never faulty, which reads as a broken safety net rather than a broken test.
#
# The value is the BIT MASK of subsystems whose report is suppressed
# (OTA_HEALTH_WIFI_AP = 1, OTA_HEALTH_HTTPD = 2, from ota_health.h).
#
# SUPPRESS EXACTLY ONE. The gate then logs `have 0x02, need 0x03 (missing 0x01)`,
# which separates a gate that waited for a specific missing bit from a gate that
# received nothing at all. Suppress both and `have 0x00` is equally consistent
# with an event group that never worked, so the rehearsal loses the one reading
# that tells a working gate from a gate that always trips.
#
# IT SUPPRESSES THE REPORT, NOT THE SUBSYSTEM. `ota_health_report()` is
# intercepted at its entry; the SoftAP has already started (wifi_network.c:149
# reports from the WIFI_EVENT_AP_START handler) and the HTTP server is already
# listening (config_server.c:2445). So a fault-injected image stays reachable
# over HTTP for its whole 60 s gate window and after it, and can be replaced
# over the air. Serial is a fallback here, not the only way back.
#
# Never flash a build configured this way to the truck.
if(OTA_HEALTH_FAULT_INJECT)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        OTA_HEALTH_FAULT_INJECT=${OTA_HEALTH_FAULT_INJECT})
    message(STATUS "ota_health: FAULT INJECTION ENABLED, mask "
                   "${OTA_HEALTH_FAULT_INJECT} -- this image is built to fail "
                   "its health gate and roll back")
endif()
