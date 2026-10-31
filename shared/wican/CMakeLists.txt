# vtrux/wican: the stock WiCAN code as one component (SPEC.md W1). A project
# takes it at a pinned tag through the Component Manager, with vtrux/can_port,
# whose stock calls on port 0 are the CAN functions this component's files
# call (W3), and supplies app_main (W4).
set(srcs "wican.c" "comm_server.c" "config_server.c" "slcan.c" "wifi_network.c" "wc_uart.c" "elm327.c" "mqtt.c" "sleep_mode.c" "autopid.c" "expression_parser.c" "wc_mdns.c" "wc_timer.c" "dev_status.c" "ota_health.c" "hw_config.c")
# Not built, as in stock: ftp.c, gvret.c, realdash.c, led.c and
# homepage_full.html (W1).
set(requires   hal soc esp_timer esp_wifi nvs_flash fatfs vfs driver esp-tls esp_adc esp_eth log app_update esp_http_server spiffs freertos mqtt json debug_logs can_port)

# THE SWITCHES (W6, W7) are CMake options of this component, on by default, as
# upstream. A project sets one off in its top-level CMakeLists.txt, before
# project(), as a cache variable, e.g.
#
#   set(WICAN_SLCAN_COMMANDS OFF CACHE BOOL "...")
#
# so `idf.py -D<switch>=ON` still overrides it.
#
# WICAN_SLCAN_COMMANDS (W6): off, no call to the SLCAN command parser is
# compiled, on any channel: wican.c's send task refuses each inbound buffer
# that would have reached it, counts it through the bus owner's
# command_refused() and logs at most once in 5 s. Receive-only monitoring is
# not affected.
#
# The four feature switches (W7). Four protocols of the stock firmware change
# bus state on their own: RealDash, ELM327 (its boot enable, ATSP and the
# receive flush), AutoPID (through ELM327's ATSP) and MQTT (its boot enable and
# the can_enable() before a transmit). Off takes the feature's source out of
# the component and its call sites in wican.c, mqtt.c and config_server.c,
# which are under #if on the same switch, out of the build. realdash.c is not
# in the stock build at all; RealDash's code here is its boot branch in
# wican.c. AutoPID calls elm327_process_cmd() and mqtt_publish() directly, so
# it needs both; wican_features.h stops a build that asks for it without them.
if(NOT DEFINED WICAN_SLCAN_COMMANDS)
    set(WICAN_SLCAN_COMMANDS ON)
endif()
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

# THE CAN FUNCTIONS ARE vtrux/can_port's (W3, port step 1b): can.h is its
# header, and its stock calls run on port 0's backend, which the project
# registers. Until 1b the project's main component defined them, and this
# component had PRIV_REQUIRES main to link them.
idf_component_register(
    SRCS "${srcs}"
    INCLUDE_DIRS "."
    REQUIRES "${requires}"
    EMBED_FILES "homepage.html"
)

# THE BOARD IS THE PROJECT'S (W9; shared/boards B2, B6). This component names
# no board: it builds against the project's one board dependency, the one
# component of the build that has include/board_pins.h, for board.h,
# board_hw_name() and the pins. None, or more than one, stops the configure.
idf_build_get_property(wican_build_components BUILD_COMPONENTS)
set(wican_boards "")
foreach(c IN LISTS wican_build_components)
    idf_component_get_property(c_dir ${c} COMPONENT_DIR)
    if(EXISTS "${c_dir}/include/board_pins.h")
        list(APPEND wican_boards ${c})
    endif()
endforeach()
list(LENGTH wican_boards wican_board_count)
if(NOT wican_board_count EQUAL 1)
    message(FATAL_ERROR
        "vtrux/wican: the build has ${wican_board_count} board components "
        "(components with include/board_pins.h: '${wican_boards}'); it needs "
        "exactly one, the project's shared/boards dependency (SPEC.md W9).")
endif()
idf_component_get_property(wican_board_lib ${wican_boards} COMPONENT_LIB)
target_link_libraries(${COMPONENT_LIB} PUBLIC ${wican_board_lib})

# The switches reach the C code as 1 or 0, never undefined: wican_features.h
# refuses to compile without them, so an #if cannot read a missing definition
# as off.
foreach(sw WICAN_SLCAN_COMMANDS WICAN_FEATURE_MQTT WICAN_FEATURE_ELM327
           WICAN_FEATURE_AUTOPID WICAN_FEATURE_REALDASH)
    if(${sw})
        target_compile_definitions(${COMPONENT_LIB} PRIVATE ${sw}=1)
    else()
        target_compile_definitions(${COMPONENT_LIB} PRIVATE ${sw}=0)
    endif()
endforeach()
message(STATUS "vtrux/wican: SLCAN_COMMANDS=${WICAN_SLCAN_COMMANDS} "
               "MQTT=${WICAN_FEATURE_MQTT} ELM327=${WICAN_FEATURE_ELM327} "
               "AUTOPID=${WICAN_FEATURE_AUTOPID} "
               "REALDASH=${WICAN_FEATURE_REALDASH}, board ${wican_boards}")

# ROLLBACK REHEARSAL BUILD (W7), test-only, off by default -- an image that
# deliberately fails its health gate:
#
#   idf.py -B build-otafault -DOTA_HEALTH_FAULT_INJECT=1 build
#
# A bare `idf.py -D` sets a CMake cache variable, not a preprocessor macro, so
# without this forwarding ota_health.c's hook stays at 0 and the build is an
# ordinary image with no fault in it, silently: a rehearsal that reports "it
# did not roll back" about an image that was never faulty.
#
# The value is the BIT MASK of subsystems whose report is suppressed
# (OTA_HEALTH_WIFI_AP = 1, OTA_HEALTH_HTTPD = 2, from ota_health.h).
#
# SUPPRESS EXACTLY ONE. The gate then logs `have 0x02, need 0x03 (missing 0x01)`,
# which separates a gate that waited for a specific missing bit from a gate that
# received nothing at all. Suppress both and `have 0x00` is equally consistent
# with an event group that never worked.
#
# IT SUPPRESSES THE REPORT, NOT THE SUBSYSTEM. ota_health_report() is
# intercepted at its entry; the SoftAP and the HTTP server are already up, so a
# fault-injected image stays reachable over HTTP for its whole gate window and
# after it, and can be replaced over the air.
#
# The image name is the project's: a project marks a fault build in it from
# the same cache variable in its top-level CMakeLists.txt. Never flash a build
# configured this way to a vehicle.
if(OTA_HEALTH_FAULT_INJECT)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        OTA_HEALTH_FAULT_INJECT=${OTA_HEALTH_FAULT_INJECT})
    message(STATUS "vtrux/wican: ota_health FAULT INJECTION ENABLED, mask "
                   "${OTA_HEALTH_FAULT_INJECT} -- this image is built to fail "
                   "its health gate and roll back")
endif()
