project(
  'inih',
  ['c', 'cpp'],
  version: 'r58',
  license: 'BSD-3-Clause',
  default_options: ['warning_level=0', 'cpp_std=c++17'],
)

# Vendored inih (r58) — sous-projet fourni localement pour le build de howdy.
# Expose INIReader_dep / inih_dep ; voir subprojects/inih/LICENSE.txt

inih_inc = include_directories('.', 'cpp')

inih_sources = files('ini.c')
INIReader_sources = files('cpp/INIReader.cpp')

inih_lib = static_library('inih', inih_sources, include_directories: inih_inc)
INIReader_lib = static_library('INIReader', INIReader_sources, include_directories: inih_inc)

inih_dep = declare_dependency(
  link_with: inih_lib,
  include_directories: inih_inc,
)

INIReader_dep = declare_dependency(
  link_with: [INIReader_lib, inih_lib],
  include_directories: inih_inc,
)