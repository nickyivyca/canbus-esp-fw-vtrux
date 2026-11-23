# vtrux/diag: the diag frames' shared parts (SPEC.md D1 to D4): the build-id
# read and the frame packer. A project takes it from git at a pinned tag of
# shared/main and compiles its own frame table, generated from its DBC by
# tools/diag_table_gen.py, into its own component.
idf_component_register(SRCS "diag_pack.c" "diag_build_id.c"
                       INCLUDE_DIRS "include"
                       PRIV_REQUIRES esp_app_format)
