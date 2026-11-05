# Pure C logic without IDF/hardware dependencies, so it also builds for the linux target (host tests).
idf_component_register(SRCS "logic.c"
                       INCLUDE_DIRS "include")
