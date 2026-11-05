# Host test app: builds for the IDF linux target and runs as a native executable.
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components")
# Only build what the tests need; keeps the linux build fast and free of hardware components.
set(COMPONENTS main)
project(host_test)
