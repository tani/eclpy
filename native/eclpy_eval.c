#include <ecl/ecl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static int eclpy_booted = 0;
static char *eclpy_error = NULL;

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_read_file")))
#endif
extern int32_t eclpy_read_file(const char *path, int32_t path_len, char **out_data,
                               int32_t *out_len);

/* Probe a host path: writes 0 (absent), 1 (file), or 2 (directory) to out_kind,
 * and the modification time in Unix seconds to out_mtime. */
#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_stat")))
#endif
extern int32_t eclpy_stat(const char *path, int32_t path_len, int32_t *out_kind,
                          double *out_mtime);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_home_directory")))
#endif
extern int32_t eclpy_home_directory(char **out_data, int32_t *out_len);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_socket_resolve")))
#endif
extern int32_t eclpy_socket_resolve(const char *host, int32_t host_len, char **out_data,
                                    int32_t *out_len);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_socket_connect")))
#endif
extern int32_t eclpy_socket_connect(const char *host, int32_t host_len, int32_t port);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_socket_send")))
#endif
extern int32_t eclpy_socket_send(int32_t handle, const char *data, int32_t data_len);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_socket_recv")))
#endif
extern int32_t eclpy_socket_recv(int32_t handle, int32_t max_len, char **out_data,
                                 int32_t *out_len);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_socket_close")))
#endif
extern int32_t eclpy_socket_close(int32_t handle);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_socket_listen")))
#endif
extern int32_t eclpy_socket_listen(const char *host, int32_t host_len, int32_t port,
                                   int32_t backlog, int32_t *out_port);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_socket_accept")))
#endif
extern int32_t eclpy_socket_accept(int32_t handle);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_socket_poll")))
#endif
extern int32_t eclpy_socket_poll(int32_t handle);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_eval_python")))
#endif
extern int32_t eclpy_eval_python(const char *source, int32_t source_len, char **out_data,
                                 int32_t *out_len, int32_t *out_is_error);

#ifdef __wasm__
__attribute__((import_module("env"), import_name("eclpy_exec_python")))
#endif
extern int32_t eclpy_exec_python(const char *source, int32_t source_len, char **out_data,
                                 int32_t *out_len, int32_t *out_is_error);

/* Seconds between the Common Lisp universal-time epoch (1900) and the Unix epoch. */
#define ECLPY_UNIX_TO_UNIVERSAL 2208988800LL

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

static cl_object eclpy_eval_forms(const char *source, int32_t source_len, cl_object print,
                                  int *saw_form) {
    cl_object lisp_source = ecl_make_simple_base_string(source, source_len);
    cl_object stream = ecl_make_string_input_stream(lisp_source, 0, source_len);
    cl_object eof = ecl_make_symbol("ECLPY-EOF", "KEYWORD");
    cl_object result = ECL_NIL;
    cl_object form;
    *saw_form = 0;

    while (1) {
        form = cl_read(4, stream, ECL_NIL, eof, ECL_NIL);
        if (form == eof) {
            break;
        }
        result = cl_eval(form);
        *saw_form = 1;
        if (print != ECL_NIL) {
            cl_prin1(1, result);
            cl_terpri(0);
        }
    }

    return result;
}

static cl_object eclpy_native_load(cl_object source, cl_object verbose, cl_object print,
                                   cl_object if_does_not_exist, cl_object external_format) {
    (void)external_format;

    cl_object namestring = cl_namestring(source);
    cl_object base = si_coerce_to_base_string(namestring);
    const char *path = ecl_base_string_pointer_safe(base);
    int32_t path_len = (int32_t)strlen(path);
    char *data = NULL;
    int32_t data_len = 0;

    int32_t status = eclpy_read_file(path, path_len, &data, &data_len);
    if (status != 0 || data == NULL || data_len < 0) {
        if (if_does_not_exist == ECL_NIL) {
            return ECL_NIL;
        }
        FEerror("Cannot open ~S.", 1, source);
    }

    if (verbose != ECL_NIL) {
        cl_format(3, ECL_T, ecl_make_simple_base_string("~&;;; Loading ~S~%", -1), source);
    }

    int saw_form = 0;
    eclpy_eval_forms(data, data_len, print, &saw_form);
    free(data);
    return ECL_T;
}

/* Returns (KIND . WRITE-DATE) for an existing host path, or NIL when absent.
 * KIND is :FILE or :DIRECTORY; WRITE-DATE is a Common Lisp universal time. */
static cl_object eclpy_host_stat(cl_object source) {
    cl_object namestring = cl_namestring(source);
    cl_object base = si_coerce_to_base_string(namestring);
    const char *path = ecl_base_string_pointer_safe(base);
    int32_t kind = 0;
    double mtime = 0.0;

    if (eclpy_stat(path, (int32_t)strlen(path), &kind, &mtime) != 0 || kind == 0) {
        return ECL_NIL;
    }
    cl_object date = ecl_make_long_long((long long)mtime + ECLPY_UNIX_TO_UNIVERSAL);
    return CONS(ecl_make_keyword(kind == 2 ? "DIRECTORY" : "FILE"), date);
}

static cl_object eclpy_host_read_bytes(cl_object pathname) {
    cl_object namestring = cl_namestring(pathname);
    cl_object base = si_coerce_to_base_string(namestring);
    const char *path = ecl_base_string_pointer_safe(base);
    char *data = NULL;
    int32_t data_len = 0;

    int32_t status = eclpy_read_file(path, (int32_t)strlen(path), &data, &data_len);
    if (status != 0 || data == NULL || data_len < 0) {
        FEerror("Cannot open ~S.", 1, pathname);
    }
    cl_object result = ecl_make_simple_base_string(data, data_len);
    free(data);
    return result;
}

static cl_object eclpy_host_home_directory(void) {
    char *data = NULL;
    int32_t data_len = 0;

    int32_t status = eclpy_home_directory(&data, &data_len);
    if (status != 0 || data == NULL || data_len < 0) {
        FEerror("Cannot determine host home directory (status ~A).", 1,
                ecl_make_integer(status));
    }
    cl_object result = ecl_make_simple_base_string(data, data_len);
    free(data);
    return result;
}

static cl_object eclpy_socket_resolve_wrapper(cl_object host) {
    cl_object base = si_coerce_to_base_string(host);
    const char *text = ecl_base_string_pointer_safe(base);
    int32_t text_len = (int32_t)base->base_string.fillp;
    char *data = NULL;
    int32_t data_len = 0;

    int32_t status = eclpy_socket_resolve(text, text_len, &data, &data_len);
    if (status != 0 || data == NULL || data_len < 0) {
        FEerror("Cannot resolve host ~S (status ~A).", 2, host, ecl_make_integer(status));
    }
    cl_object result = ecl_make_simple_base_string(data, data_len);
    free(data);
    return result;
}

static cl_object eclpy_socket_connect_wrapper(cl_object host, cl_object port) {
    cl_object base = si_coerce_to_base_string(host);
    const char *text = ecl_base_string_pointer_safe(base);
    int32_t text_len = (int32_t)base->base_string.fillp;
    int32_t port_number = (int32_t)ecl_fixnum(port);

    int32_t handle = eclpy_socket_connect(text, text_len, port_number);
    if (handle < 0) {
        FEerror("Cannot connect to ~S:~A (status ~A).", 3, host, port,
                ecl_make_integer(-handle));
    }
    return ecl_make_fixnum(handle);
}

static cl_object eclpy_socket_send_wrapper(cl_object handle, cl_object byte_string) {
    int32_t handle_number = (int32_t)ecl_fixnum(handle);
    cl_object data_base = si_coerce_to_base_string(byte_string);
    const char *data = ecl_base_string_pointer_safe(data_base);
    int32_t data_len = (int32_t)data_base->base_string.fillp;

    int32_t sent = eclpy_socket_send(handle_number, data, data_len);
    if (sent < 0) {
        FEerror("Cannot send on socket ~A (status ~A).", 2, handle, ecl_make_integer(-sent));
    }
    return ecl_make_fixnum(sent);
}

static cl_object eclpy_socket_recv_wrapper(cl_object handle, cl_object max_len) {
    int32_t handle_number = (int32_t)ecl_fixnum(handle);
    int32_t max_len_number = (int32_t)ecl_fixnum(max_len);
    char *data = NULL;
    int32_t data_len = 0;

    int32_t status = eclpy_socket_recv(handle_number, max_len_number, &data, &data_len);
    if (status != 0 || data == NULL || data_len < 0) {
        FEerror("Cannot receive on socket ~A (status ~A).", 2, handle, ecl_make_integer(status));
    }
    cl_object result = ecl_make_simple_base_string(data, data_len);
    free(data);
    return result;
}

static cl_object eclpy_socket_close_wrapper(cl_object handle) {
    int32_t handle_number = (int32_t)ecl_fixnum(handle);
    eclpy_socket_close(handle_number);
    return ECL_T;
}

static cl_object eclpy_socket_listen_wrapper(cl_object host, cl_object port, cl_object backlog) {
    cl_object base = si_coerce_to_base_string(host);
    const char *text = ecl_base_string_pointer_safe(base);
    int32_t text_len = (int32_t)base->base_string.fillp;
    int32_t port_number = (int32_t)ecl_fixnum(port);
    int32_t backlog_number = (int32_t)ecl_fixnum(backlog);
    int32_t bound_port = 0;

    int32_t handle = eclpy_socket_listen(text, text_len, port_number, backlog_number,
                                         &bound_port);
    if (handle < 0) {
        FEerror("Cannot listen on ~S:~A (status ~A).", 3, host, port,
                ecl_make_integer(-handle));
    }
    return CONS(ecl_make_fixnum(handle), ecl_make_fixnum(bound_port));
}

static cl_object eclpy_socket_accept_wrapper(cl_object handle) {
    int32_t handle_number = (int32_t)ecl_fixnum(handle);

    int32_t connection = eclpy_socket_accept(handle_number);
    if (connection < 0) {
        FEerror("Cannot accept on socket ~A (status ~A).", 2, handle,
                ecl_make_integer(-connection));
    }
    return ecl_make_fixnum(connection);
}

static cl_object eclpy_socket_poll_wrapper(cl_object handle) {
    int32_t handle_number = (int32_t)ecl_fixnum(handle);

    int32_t ready = eclpy_socket_poll(handle_number);
    if (ready < 0) {
        FEerror("Cannot poll socket ~A (status ~A).", 2, handle, ecl_make_integer(-ready));
    }
    return ready != 0 ? ECL_T : ECL_NIL;
}

typedef int32_t (*eclpy_python_bridge)(const char *, int32_t, char **, int32_t *, int32_t *);

static cl_object eclpy_call_python(cl_object source, eclpy_python_bridge callback,
                                   const char *operation) {
    cl_object base = si_coerce_to_base_string(source);
    const char *code = ecl_base_string_pointer_safe(base);
    int32_t code_len = (int32_t)strlen(code);
    char *data = NULL;
    int32_t data_len = 0;
    int32_t is_error = 0;

    int32_t status = callback(code, code_len, &data, &data_len, &is_error);
    if (status != 0 || data == NULL || data_len < 0) {
        FEerror("Python ~A bridge failed with status ~A.", 2,
                ecl_make_simple_base_string(operation, -1),
                ecl_make_integer(status));
    }

    if (is_error != 0) {
        cl_object message = ecl_make_simple_base_string(data, data_len);
        free(data);
        FEerror("Python ~A failed: ~A", 2, ecl_make_simple_base_string(operation, -1), message);
    }
    /* The helper package owns Lisp-source reading and evaluation. */
    cl_object source_text = ecl_make_simple_base_string(data, data_len);
    free(data);
    return source_text;
}

static cl_object eclpy_py_eval(cl_object source) {
    return eclpy_call_python(source, eclpy_eval_python, "eval");
}

static cl_object eclpy_py_exec(cl_object source) {
    return eclpy_call_python(source, eclpy_exec_python, "exec");
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
    /* Documentation lookups otherwise consult the on-disk help database
     * (SYS:help.doc), which cannot be opened in the standalone WASM sandbox and
     * aborts loading documented code such as ASDF. Keep only the in-memory
     * dictionaries so documentation never touches the filesystem. */
    cl_eval(ecl_read_from_cstring(
        "(setf ext:*documentation-pool* "
        "(remove-if-not #'hash-table-p ext:*documentation-pool*))"));
    cl_eval(ecl_read_from_cstring(
        "(defpackage #:ecl-python (:use #:cl) (:shadow #:load) "
        "(:export #:native-load #:host-stat #:%host-read-bytes #:%host-home-directory "
        "#:%py-eval #:%py-exec #:%socket-resolve #:%socket-connect #:%socket-send "
        "#:%socket-recv #:%socket-close #:%socket-listen #:%socket-accept "
        "#:%socket-poll))"));
    ecl_def_c_function(ecl_read_from_cstring("ecl-python:native-load"),
                       (cl_objectfn_fixed)eclpy_native_load, 5);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python:host-stat"),
                       (cl_objectfn_fixed)eclpy_host_stat, 1);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%host-read-bytes"),
                       (cl_objectfn_fixed)eclpy_host_read_bytes, 1);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%host-home-directory"),
                       (cl_objectfn_fixed)eclpy_host_home_directory, 0);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%py-eval"),
                       (cl_objectfn_fixed)eclpy_py_eval, 1);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%py-exec"),
                       (cl_objectfn_fixed)eclpy_py_exec, 1);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%socket-resolve"),
                       (cl_objectfn_fixed)eclpy_socket_resolve_wrapper, 1);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%socket-connect"),
                       (cl_objectfn_fixed)eclpy_socket_connect_wrapper, 2);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%socket-send"),
                       (cl_objectfn_fixed)eclpy_socket_send_wrapper, 2);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%socket-recv"),
                       (cl_objectfn_fixed)eclpy_socket_recv_wrapper, 2);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%socket-close"),
                       (cl_objectfn_fixed)eclpy_socket_close_wrapper, 1);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%socket-listen"),
                       (cl_objectfn_fixed)eclpy_socket_listen_wrapper, 3);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%socket-accept"),
                       (cl_objectfn_fixed)eclpy_socket_accept_wrapper, 1);
    ecl_def_c_function(ecl_read_from_cstring("ecl-python::%socket-poll"),
                       (cl_objectfn_fixed)eclpy_socket_poll_wrapper, 1);
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
    char *output = NULL;
    cl_env_ptr env = ecl_process_env();
    ECL_CATCH_ALL_BEGIN(env) {
        int saw_form = 0;
        cl_object result = eclpy_eval_forms(source, source_len, ECL_NIL, &saw_form);

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

    return output;
}

char *eclpy_eval_json(const char *source, int32_t source_len) {
    if (source == NULL || source_len < 0) {
        eclpy_set_error("invalid Lisp source buffer");
        return NULL;
    }
    if (eclpy_init() != 0) {
        return NULL;
    }

    eclpy_set_error(NULL);
    char *output = NULL;
    cl_env_ptr env = ecl_process_env();
    ECL_CATCH_ALL_BEGIN(env) {
        int saw_form = 0;
        cl_object result = eclpy_eval_forms(source, source_len, ECL_NIL, &saw_form);
        cl_object value = saw_form ? result : ECL_NIL;
        cl_object json = cl_funcall(2, ecl_read_from_cstring("ecl-python:json-encode"), value);
        cl_object base = si_coerce_to_base_string(json);
        output = eclpy_strdup(ecl_base_string_pointer_safe(base));
        if (output == NULL) {
            eclpy_set_error("failed to allocate JSON result buffer");
        }
    } ECL_CATCH_ALL_IF_CAUGHT {
        eclpy_set_error("ECL evaluation escaped the protected region");
        output = NULL;
    } ECL_CATCH_ALL_END;

    return output;
}

void eclpy_shutdown(void) {
    if (eclpy_booted) {
        cl_shutdown();
        eclpy_booted = 0;
    }
    eclpy_set_error(NULL);
}
