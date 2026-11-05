cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(filter_test LANGUAGES C)

set(APP_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../../app/src)

target_include_directories(app PRIVATE ${APP_SRC})
target_sources(app PRIVATE
  src/main.c
  ${APP_SRC}/filter.c
)
