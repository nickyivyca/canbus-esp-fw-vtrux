# vtrux/restart_guard: every software restart takes the CAN ports off the bus
# first (SPEC.md G1 to G4). A project takes it with vtrux/can_port, its CAN
# drivers and its board at the same shared/main tag, and lists it itself. It
# needs no switch and no call: depending on it is enough (G2).
set(restart_guard_srcs "restart_guard.c")
set(restart_guard_includes "include")
# The test app's components (esp_http_server, esp_timer, app_update and
# esp_app_format) are listed in every build, not only under the switch below. ESP-IDF reads each
# component's requirements in an early pass, a separate `cmake -P` that is
# given no -D cache variables (IDF v5.4.1 tools/cmake/component.cmake,
# __component_get_requirements), so a switch there changes nothing. It is
# only a library edge: the guard calls none of them, and the switch still
# decides whether the test's source is compiled.
set(restart_guard_priv_requires can_port esp_hw_support
                                esp_http_server esp_timer app_update esp_app_format)

# G5, THE RESTART TEST: a reader for the record, a crash trigger and two
# snapshots of the controller and the standby pin. Never in a release image
# (shared/tools R20).
#
#   idf.py -B build-restart-test -DRESTART_GUARD_TEST=1 build
#
# The guard itself is in every build (restart_guard.c, above); this adds only
# what lets a bench see it. The source is added to the component only here,
# rather than compiled always and guarded inside with #if: a source that is
# not in the build cannot be linked by accident. A release image carrying the
# test's symbols is refused by the build-switch check (rows in
# vtrux_checks.json). The project registers it from its URI hook (W8) and
# calls its pre-install snapshot just before its first driver install.
if(RESTART_GUARD_TEST)
    list(APPEND restart_guard_srcs "test_app/restart_guard_test.c")
    list(APPEND restart_guard_includes "test_app/include")
endif()

idf_component_register(SRCS ${restart_guard_srcs}
                       INCLUDE_DIRS ${restart_guard_includes}
                       PRIV_REQUIRES ${restart_guard_priv_requires})

if(RESTART_GUARD_TEST)
    message(STATUS "vtrux/restart_guard: RESTART TEST build -- /gi_quiesce "
                   "and its crash trigger are compiled in")
endif()

# THE BOARD IS THE PROJECT'S (shared/boards B2, B5): the guard asks it for the
# ports, their kind and their standby, and the test app reads the standby pin
# from board_pins.h (B6). As vtrux/twai_port does, it builds against the one
# component of the build that has include/board_pins.h. None, or more than
# one, stops the configure.
idf_build_get_property(restart_guard_build_components BUILD_COMPONENTS)
set(restart_guard_boards "")
foreach(c IN LISTS restart_guard_build_components)
    idf_component_get_property(c_dir ${c} COMPONENT_DIR)
    if(EXISTS "${c_dir}/include/board_pins.h")
        list(APPEND restart_guard_boards ${c})
    endif()
endforeach()
list(LENGTH restart_guard_boards restart_guard_board_count)
if(NOT restart_guard_board_count EQUAL 1)
    message(FATAL_ERROR
        "vtrux/restart_guard: the build has ${restart_guard_board_count} board "
        "components (components with include/board_pins.h: "
        "'${restart_guard_boards}'); it needs exactly one, the project's "
        "shared/boards dependency.")
endif()
idf_component_get_property(restart_guard_board_lib ${restart_guard_boards} COMPONENT_LIB)
target_link_libraries(${COMPONENT_LIB} PRIVATE ${restart_guard_board_lib})

# G2, THE WRAP, SET BY THE COMPONENT ITSELF (user, 2026-11-01). Every caller of
# esp_restart_noos() -- esp_restart(), panic_restart() and call_start_cpu0's
# early-boot failures -- is in another object file of esp_system, so --wrap
# redirects all of them to __wrap_esp_restart_noos in restart_guard.c, which
# reaches the real function as __real_esp_restart_noos. INTERFACE, so it is
# on the link of every image that has the component, with nothing for the
# project to remember.
target_link_libraries(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=esp_restart_noos")
message(STATUS "vtrux/restart_guard: esp_restart_noos wrapped, board "
               "${restart_guard_boards}")
