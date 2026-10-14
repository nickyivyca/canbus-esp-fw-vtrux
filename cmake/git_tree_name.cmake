# git_tree_name -- every build is named from the tree it compiles.
#
# Gen-inhibit spec 12.3 item 3, "Every build names its image from the tree it
# compiled" (added 2026-10-13, user). Self-contained, so any ESP-IDF project in
# this repo can use it: it knows nothing about the project's naming scheme, only
# about `git describe`.
#
# THE PROBLEM. ESP-IDF fixes the image name, esp_app_desc.project_name and the
# embedded version when CMake configures, and an incremental build re-runs CMake
# only when one of CMake's own inputs changed. ESP-IDF's version lookup makes the
# commit one of those inputs (GetGitRevisionDescription.cmake copies .git/HEAD
# and the branch ref into the build directory with configure_file), but not the
# working tree. So an edit is compiled under the clean name of the commit before
# it: 2026-09-29, five edited files rebuilt as 5364648; 2026-10-13, an
# `idf.py build` over edited .c files produced new code under a3394ca.
#
# WHAT THIS DOES. git_tree_name(<var>) runs `git describe` when CMake
# configures and returns the result, as the project did before. It also writes
# that result to a stamp file in the build directory, makes the stamp one of
# CMake's inputs (CMAKE_CONFIGURE_DEPENDS), and adds a target, the check, that
# runs the same `git describe` again and rewrites the stamp only when the
# answer differs from it.
#
# WHY THAT RUNS BEFORE EVERY BUILD. Ninja brings build.ninja up to date before
# it builds anything else, by building build.ninja's own inputs first, and the
# stamp is one of those inputs with the check as the rule that produces it.
# CMake writes its usual "a missing CMake input file is not an error" phony rule
# for every input except one a custom command produces, so for the stamp ninja
# sees the check's rule instead. Every build starts ninja on build.ninja --
# `idf.py build`, `idf.py flash`, `ninja -C build` alike -- so every build runs
# the check first:
#
#   - same name: the stamp is left alone, the rule has restat, ninja sees
#     nothing new, and the build goes ahead without a reconfigure;
#   - new name: the stamp is now newer than build.ninja, so ninja re-runs CMake,
#     loads the regenerated build.ninja, and only then compiles, under the new
#     name.
#
# THE NAME ENTERS THE BUILD ONCE (spec 12.3 item 3, "The name enters the build
# once", added 2026-10-13, user). git_tree_name() also sets PROJECT_VER to the
# same string, so ESP-IDF embeds it as esp_app_desc.version instead of running a
# second `git describe` of its own, and firmware reads the name from there at
# runtime (esp_app_get_description()->version). Nothing passes it on a compile
# line, so a name change recompiles two files and relinks: ESP-IDF's descriptor
# file (esp_app_desc.c), and project_elf_src_<target>.c, the empty source ESP-IDF
# generates for the ELF target, whose object is named after it -- and the ELF
# is named after the image (project()). Nothing of the project's own is
# recompiled. ESP-IDF cuts PROJECT_VER to the version field's 31 characters
# without a word (components/esp_app_format/CMakeLists.txt), and the firmware
# would then report a name cut short, so a longer name fails configuring here
# instead.
#
# IF THE NAME CANNOT BE WORKED OUT -- no git, not a repository -- the check
# fails, and the build fails before anything is compiled. Configuring fails the
# same way. If the reconfigure itself fails (a name over a project's length
# limit, say), the stamp stays ahead of build.ninja, so every later build retries
# it and fails the same way until it is fixed.
#
# LIMIT: an edit saved while a build is already compiling is not seen by that
# build, which checked the name before it began. The next build sees it.
#
# USE, in the project's top-level CMakeLists.txt, BEFORE project():
#
#   include(${CMAKE_CURRENT_LIST_DIR}/cmake/git_tree_name.cmake)
#   git_tree_name(GIT_SHA)
#
# That is the only call. It sets <var> and PROJECT_VER, and PROJECT_VER must
# not be set anywhere else. The check target is added by a deferred call at the
# end of the top-level directory, i.e. after project(), so there is no second
# call that could be left out. DESCRIBE <args...> replaces the default
# `--tags --always --dirty`.
#
# The same file is the check, run by the target as `cmake -P` (below).

function(_git_tree_name_describe out_var git source)
    execute_process(
        COMMAND "${git}" -C "${source}" describe ${ARGN}
        RESULT_VARIABLE res
        OUTPUT_VARIABLE name
        ERROR_VARIABLE err
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_STRIP_TRAILING_WHITESPACE)
    if(NOT res EQUAL 0 OR name STREQUAL "")
        string(REPLACE ";" " " shown "${ARGN}")
        message(FATAL_ERROR
            "git_tree_name: 'git describe ${shown}' in ${source} failed (${res}): "
            "${err}\n"
            "  The build cannot be named from the tree it compiles, so it is "
            "not built (spec 12.3 item 3).")
    endif()
    set(${out_var} "${name}" PARENT_SCOPE)
endfunction()

# ---------------------------------------------------------------- the check --
if(CMAKE_SCRIPT_MODE_FILE STREQUAL CMAKE_CURRENT_LIST_FILE)
    string(REPLACE "," ";" _gtn_args "${GTN_ARGS}")
    _git_tree_name_describe(_gtn_now "${GTN_GIT}" "${GTN_SOURCE}" ${_gtn_args})
    set(_gtn_was "")
    if(EXISTS "${GTN_STAMP}")
        file(READ "${GTN_STAMP}" _gtn_was)
    endif()
    if(NOT _gtn_now STREQUAL _gtn_was)
        message(STATUS
            "git_tree_name: the tree is now '${_gtn_now}', configured as "
            "'${_gtn_was}'; CMake re-runs before anything is compiled")
        file(WRITE "${GTN_STAMP}" "${_gtn_now}")
    endif()
    return()
endif()

# ------------------------------------------------------------------ the API --
include_guard(GLOBAL)

if(CMAKE_VERSION VERSION_LESS 3.21)
    message(FATAL_ERROR
        "git_tree_name needs CMake 3.21 or later (cmake_language(DEFER), "
        "find_program NO_CACHE); this is ${CMAKE_VERSION}.")
endif()

set(_GIT_TREE_NAME_FILE "${CMAKE_CURRENT_LIST_FILE}")

function(git_tree_name out_var)
    cmake_parse_arguments(PARSE_ARGV 1 GTN "" "" "DESCRIBE")
    if(NOT GTN_DESCRIBE)
        set(GTN_DESCRIBE --tags --always --dirty)
    endif()
    if(NOT CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
        message(FATAL_ERROR
            "git_tree_name() belongs in the top-level CMakeLists.txt, whose "
            "build.ninja it guards; called from ${CMAKE_CURRENT_SOURCE_DIR}.")
    endif()
    get_property(called GLOBAL PROPERTY _GIT_TREE_NAME_CALLED)
    if(called)
        message(FATAL_ERROR "git_tree_name() is called more than once.")
    endif()
    set_property(GLOBAL PROPERTY _GIT_TREE_NAME_CALLED TRUE)
    if(DEFINED PROJECT_VER)
        message(FATAL_ERROR
            "git_tree_name() sets PROJECT_VER, which is already set "
            "('${PROJECT_VER}'). The embedded version must be the build's name "
            "(spec 12.3 item 3), so set it nowhere else.")
    endif()

    find_program(git NAMES git NO_CACHE)
    if(NOT git)
        message(FATAL_ERROR
            "git_tree_name: git is not on PATH, so the build cannot be named "
            "from the tree it compiles (spec 12.3 item 3).")
    endif()

    _git_tree_name_describe(name "${git}" "${CMAKE_SOURCE_DIR}" ${GTN_DESCRIBE})

    # esp_app_desc.version is 32 bytes, 31 characters and the NUL.
    string(LENGTH "${name}" len)
    if(len GREATER 31)
        message(FATAL_ERROR
            "git_tree_name: '${name}' is ${len} characters; "
            "esp_app_desc.version holds 31 plus a NUL, and ESP-IDF would cut it "
            "short without a word, so the image would report a name that is "
            "not its own (spec 12.3 item 3).")
    endif()

    # Rewritten only when it differs, so a reconfigure that finds the name
    # unchanged leaves the stamp as old as it was.
    set(stamp "${CMAKE_BINARY_DIR}/git_tree_name.txt")
    set(was "")
    if(EXISTS "${stamp}")
        file(READ "${stamp}" was)
    endif()
    if(NOT name STREQUAL was)
        file(WRITE "${stamp}" "${name}")
    endif()
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${stamp}")

    # The describe arguments travel to the check joined with commas: a ";" in a
    # VERBATIM COMMAND argument would split it.
    string(REPLACE ";" "," args "${GTN_DESCRIBE}")
    # A deferred call's arguments are read when it runs, after this function
    # has returned, so the values are written into it now.
    cmake_language(EVAL CODE
        "cmake_language(DEFER CALL _git_tree_name_add_check
            [==[${git}]==] [==[${stamp}]==] [==[${args}]==]
            [==[${_GIT_TREE_NAME_FILE}]==])")

    message(STATUS "git_tree_name: ${name}")
    set(${out_var} "${name}" PARENT_SCOPE)
    set(PROJECT_VER "${name}" PARENT_SCOPE)
endfunction()

function(_git_tree_name_add_check git stamp args script)
    add_custom_target(git_tree_name_check
        COMMAND "${CMAKE_COMMAND}"
                "-DGTN_GIT=${git}"
                "-DGTN_SOURCE=${CMAKE_SOURCE_DIR}"
                "-DGTN_STAMP=${stamp}"
                "-DGTN_ARGS=${args}"
                -P "${script}"
        BYPRODUCTS "${stamp}"
        COMMENT "git_tree_name: is the build named from its tree?"
        VERBATIM)
endfunction()
