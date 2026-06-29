#pragma once

#include <ecl/ecl.h>
#include <stdint.h>

char *eclpy_value_to_json_cstring(cl_object value);
cl_object eclpy_json_to_value(const char *s, int32_t n);
