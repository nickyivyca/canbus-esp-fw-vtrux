# See the build system documentation in IDF programming guide
# for more information about component CMakeLists.txt files.
set(srcs "main.c" "comm_server.c" "config_server.c" "slcan.c" "can.c" "wifi_network.c" "wc_uart.c" "elm327.c" "mqtt.c" "sleep_mode.c" "autopid.c" "expression_parser.c" "wc_mdns.c" "wc_timer.c" "dev_status.c" "ota_health.c" "gen_inhibit.c" "gen_inhibit_core.c" "gi_sched.c" "gi_restart_quiesce.c")
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

# STOCK FEATURES, OFF IN THIS PRODUCT (spec 3.2 and 12.3 item 3, user
# 2026-10-16). Four protocols of the stock firmware changed bus state from
# outside gen_inhibit: RealDash, ELM327 (its boot enable, ATSP and the receive
# flush), AutoPID (through ELM327's ATSP) and MQTT (its boot enable and the
# can_enable() before a transmit). Each has a switch, ON here because this is
# the shared code, and the top-level CMakeLists.txt sets all four OFF for
# gen-inhibit. OFF takes the feature's source out of the component, as
# gi_txabort_probe.c is kept out above, and its call sites in main.c, mqtt.c
# and config_server.c are under #if on the same switch, so none of its code is
# compiled. realdash.c is not in the stock build at all; RealDash's code here
# is its boot branch in main.c.
#
#   idf.py -B build-features -DWICAN_FEATURE_MQTT=ON build
#
# AutoPID calls elm327_process_cmd() and mqtt_publish() directly, so it needs
# both; wican_features.h stops a build that asks for it without them.
if(NOT DEFINED WICAN_FEATURE_MQTT)
    set(WICAN_FEATURE_MQTT ON)
endif()
if(NOT DEFINED WICAN_FEATURE_ELM327)
    set(WICAN_FEATURE_ELM327 ON)
endif()
if(NOT DEFINED WICAN_FEATURE_AUTOPID)
    set(WICAN_FEATURE_AUTOPID ON)
endif()
if(NOT DEFINED WICAN_FEATURE_REALDASH)
    set(WICAN_FEATURE_REALDASH ON)
endif()
if(NOT WICAN_FEATURE_MQTT)
    list(REMOVE_ITEM srcs "mqtt.c")
endif()
if(NOT WICAN_FEATURE_ELM327)
    list(REMOVE_ITEM srcs "elm327.c")
endif()
if(NOT WICAN_FEATURE_AUTOPID)
    list(REMOVE_ITEM srcs "autopid.c")
endif()

# autopid.c is listed once, in srcs: the stock line also named it here, which
# would have compiled it with its switch off.
idf_component_register(
    SRCS "hw_config.c" "wc_timer.c" "" "${srcs}"        # list the source files of this component
    INCLUDE_DIRS        # optional, add here public include directories
    PRIV_INCLUDE_DIRS   # optional, add here private include directories
    REQUIRES   "${requires}"         # optional, list the public requirements (component names)
    PRIV_REQUIRES       # optional, list the private requirements
    EMBED_FILES "homepage.html"
)

# The four feature switches reach the C code as 1 or 0, never undefined:
# wican_features.h refuses to compile without them, so an #if cannot read a
# missing definition as off.
if(WICAN_FEATURE_MQTT)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WICAN_FEATURE_MQTT=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WICAN_FEATURE_MQTT=0)
endif()
if(WICAN_FEATURE_ELM327)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WICAN_FEATURE_ELM327=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WICAN_FEATURE_ELM327=0)
endif()
if(WICAN_FEATURE_AUTOPID)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WICAN_FEATURE_AUTOPID=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WICAN_FEATURE_AUTOPID=0)
endif()
if(WICAN_FEATURE_REALDASH)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WICAN_FEATURE_REALDASH=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WICAN_FEATURE_REALDASH=0)
endif()
message(STATUS "stock features: MQTT=${WICAN_FEATURE_MQTT} "
               "ELM327=${WICAN_FEATURE_ELM327} AUTOPID=${WICAN_FEATURE_AUTOPID} "
               "REALDASH=${WICAN_FEATURE_REALDASH}")

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
