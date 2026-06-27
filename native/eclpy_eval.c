#include <ecl/ecl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static int eclpy_booted = 0;
static char *eclpy_error = NULL;

static void eclpy_set_error(const char *message) {
    free(eclpy_error);
    if (message == NULL) {
        eclpy_error = NULL;
        return;
    }
    size_t length = strlen(message);
    eclpy_error = (char *)malloc(length + 1);
    if (eclpy_error != NULL) {
        memcpy(eclpy_error, message, length + 1);
    }
}

static char *eclpy_strdup(const char *value) {
    size_t length = strlen(value);
    char *copy = (char *)malloc(length + 1);
    if (copy != NULL) {
        memcpy(copy, value, length + 1);
    }
    return copy;
}

void *eclpy_alloc(int32_t size) {
    if (size <= 0) {
        return NULL;
    }
    return malloc((size_t)size);
}

void eclpy_free(void *ptr) {
    free(ptr);
}

const char *eclpy_last_error(void) {
    return eclpy_error == NULL ? "" : eclpy_error;
}

int32_t eclpy_init(void) {
    if (eclpy_booted) {
        return 0;
    }

    eclpy_set_error(NULL);
    ecl_set_option(ECL_OPT_SIGNAL_HANDLING_THREAD, 0);
    ecl_set_option(ECL_OPT_TRAP_SIGSEGV, 0);
    ecl_set_option(ECL_OPT_TRAP_SIGFPE, 0);
    ecl_set_option(ECL_OPT_TRAP_SIGINT, 0);
    ecl_set_option(ECL_OPT_TRAP_SIGILL, 0);
    ecl_set_option(ECL_OPT_TRAP_SIGBUS, 0);
    ecl_set_option(ECL_OPT_TRAP_SIGPIPE, 0);
    ecl_set_option(ECL_OPT_C_STACK_SIZE, 1048576);

    char *argv[] = {"eclpy", NULL};
    cl_boot(1, argv);
    eclpy_booted = 1;
    return 0;
}

char *eclpy_eval(const char *source, int32_t source_len) {
    if (source == NULL || source_len < 0) {
        eclpy_set_error("invalid Lisp source buffer");
        return NULL;
    }
    if (eclpy_init() != 0) {
        return NULL;
    }

    eclpy_set_error(NULL);
    char *source_copy = (char *)malloc((size_t)source_len + 1);
    if (source_copy == NULL) {
        eclpy_set_error("failed to allocate Lisp source buffer");
        return NULL;
    }
    memcpy(source_copy, source, (size_t)source_len);
    source_copy[source_len] = '\0';

    char *output = NULL;
    cl_env_ptr env = ecl_process_env();
    ECL_CATCH_ALL_BEGIN(env) {
        cl_object lisp_source = ecl_make_simple_base_string(source_copy, source_len);
        cl_object stream = ecl_make_string_input_stream(lisp_source, 0, source_len);
        cl_object eof = ecl_make_symbol("ECLPY-EOF", "KEYWORD");
        cl_object result = ECL_NIL;
        cl_object form;
        int saw_form = 0;

        while (1) {
            form = cl_read(4, stream, ECL_NIL, eof, ECL_NIL);
            if (form == eof) {
                break;
            }
            result = cl_eval(form);
            saw_form = 1;
        }

        if (!saw_form) {
            output = eclpy_strdup("");
        } else {
            cl_object printed = cl_prin1_to_string(result);
            cl_object base = si_coerce_to_base_string(printed);
            output = eclpy_strdup(ecl_base_string_pointer_safe(base));
            if (output == NULL) {
                eclpy_set_error("failed to allocate Lisp result buffer");
            }
        }
    } ECL_CATCH_ALL_IF_CAUGHT {
        eclpy_set_error("ECL evaluation escaped the protected region");
        output = NULL;
    } ECL_CATCH_ALL_END;

    free(source_copy);
    return output;
}

void eclpy_shutdown(void) {
    if (eclpy_booted) {
        cl_shutdown();
        eclpy_booted = 0;
    }
    eclpy_set_error(NULL);
}
