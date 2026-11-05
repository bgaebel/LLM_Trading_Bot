# Host test project: builds pure-logic modules from app/ with the host compiler.
# Independent of the firmware build (no toolchain file, no HAL).
cmake_minimum_required(VERSION 3.22)
project(host_tests C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)
FetchContent_Declare(
  googletest
  URL https://github.com/google/googletest/archive/refs/tags/v1.17.0.zip
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
# Match MSVC runtime of the test executable (no-op for GCC/Clang).
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

set(APP_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../app)

add_executable(test_ringbuf test_ringbuf.cpp ${APP_DIR}/ringbuf.c)
target_include_directories(test_ringbuf PRIVATE ${APP_DIR})
target_link_libraries(test_ringbuf PRIVATE GTest::gtest_main)

if(MSVC)
  target_compile_options(test_ringbuf PRIVATE /W4)
else()
  target_compile_options(test_ringbuf PRIVATE -Wall -Wextra -Werror)
endif()

enable_testing()
include(GoogleTest)
gtest_discover_tests(test_ringbuf)
