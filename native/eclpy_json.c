#include "eclpy_json.h"

#include <stdlib.h>
#include <string.h>

/* ============================ JSON bridge ============================
 * Values cross the Lisp/Python boundary as JSON. The C layer owns the
 * Lisp side of that conversion: it serializes Lisp values to JSON text and
 * parses JSON text (produced by Python's json module) back into Lisp values.
 */

typedef struct {
    char *data;
    size_t len;
    size_t cap;
    int ok;
} eclpy_json_out;

static void eclpy_out_reserve(eclpy_json_out *out, size_t extra) {
    if (!out->ok) {
        return;
    }
    if (out->len + extra <= out->cap) {
        return;
    }
    size_t cap = out->cap ? out->cap : 64;
    while (cap < out->len + extra) {
        cap *= 2;
    }
    char *data = (char *)realloc(out->data, cap);
    if (data == NULL) {
        out->ok = 0;
        return;
    }
    out->data = data;
    out->cap = cap;
}

static void eclpy_out_char(eclpy_json_out *out, char c) {
    eclpy_out_reserve(out, 1);
    if (out->ok) {
        out->data[out->len++] = c;
    }
}

static void eclpy_out_bytes(eclpy_json_out *out, const char *s, size_t n) {
    eclpy_out_reserve(out, n);
    if (out->ok) {
        memcpy(out->data + out->len, s, n);
        out->len += n;
    }
}

static void eclpy_out_cstr(eclpy_json_out *out, const char *s) {
    eclpy_out_bytes(out, s, strlen(s));
}

static void eclpy_out_utf8(eclpy_json_out *out, unsigned long cp) {
    if (cp < 0x80) {
        eclpy_out_char(out, (char)cp);
    } else if (cp < 0x800) {
        eclpy_out_char(out, (char)(0xC0 | (cp >> 6)));
        eclpy_out_char(out, (char)(0x80 | (cp & 0x3F)));
    } else {
        eclpy_out_char(out, (char)(0xE0 | (cp >> 12)));
        eclpy_out_char(out, (char)(0x80 | ((cp >> 6) & 0x3F)));
        eclpy_out_char(out, (char)(0x80 | (cp & 0x3F)));
    }
}

static void eclpy_out_json_string(eclpy_json_out *out, const char *s, size_t n) {
    static const char hex[] = "0123456789abcdef";
    eclpy_out_char(out, '"');
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
            case '"': eclpy_out_cstr(out, "\\\""); break;
            case '\\': eclpy_out_cstr(out, "\\\\"); break;
            case '\n': eclpy_out_cstr(out, "\\n"); break;
            case '\r': eclpy_out_cstr(out, "\\r"); break;
            case '\t': eclpy_out_cstr(out, "\\t"); break;
            case '\b': eclpy_out_cstr(out, "\\b"); break;
            case '\f': eclpy_out_cstr(out, "\\f"); break;
            default:
                if (c < 0x20) {
                    char esc[6] = {'\\', 'u', '0', '0', hex[(c >> 4) & 0xF], hex[c & 0xF]};
                    eclpy_out_bytes(out, esc, sizeof(esc));
                } else {
                    eclpy_out_char(out, (char)c);
                }
        }
    }
    eclpy_out_char(out, '"');
}

static void eclpy_string_bytes(cl_object value, const char **out_ptr, size_t *out_len) {
    cl_object base = si_coerce_to_base_string(value);
    *out_ptr = ecl_base_string_pointer_safe(base);
    *out_len = (size_t)ecl_length(base);
}

/* Serialize the simplified tagged structure produced by ecl-python:serialize.
 * Only NIL, T, keywords, integers, strings, and proper lists can appear. */
static void eclpy_value_to_json(eclpy_json_out *out, cl_object value) {
    if (value == ECL_NIL) {
        eclpy_out_cstr(out, "null");
    } else if (value == ECL_T) {
        eclpy_out_cstr(out, "true");
    } else if (ecl_keywordp(value)) {
        const char *name;
        size_t name_len;
        eclpy_string_bytes(ecl_symbol_name(value), &name, &name_len);
        eclpy_out_char(out, '"');
        eclpy_out_char(out, ':');
        eclpy_out_bytes(out, name, name_len);
        eclpy_out_char(out, '"');
    } else if (ECL_FIXNUMP(value) || ECL_BIGNUMP(value)) {
        const char *digits;
        size_t digits_len;
        eclpy_string_bytes(cl_prin1_to_string(value), &digits, &digits_len);
        eclpy_out_bytes(out, digits, digits_len);
    } else if (ECL_STRINGP(value)) {
        const char *text;
        size_t text_len;
        eclpy_string_bytes(value, &text, &text_len);
        eclpy_out_json_string(out, text, text_len);
    } else if (ECL_CONSP(value)) {
        eclpy_out_char(out, '[');
        cl_object tail = value;
        int first = 1;
        while (ECL_CONSP(tail)) {
            if (!first) {
                eclpy_out_char(out, ',');
            }
            first = 0;
            eclpy_value_to_json(out, ECL_CONS_CAR(tail));
            tail = ECL_CONS_CDR(tail);
        }
        eclpy_out_char(out, ']');
    } else {
        eclpy_out_cstr(out, "null");
    }
}

char *eclpy_value_to_json_cstring(cl_object value) {
    eclpy_json_out out = {NULL, 0, 0, 1};
    eclpy_value_to_json(&out, value);
    char *result = NULL;
    if (out.ok) {
        result = (char *)malloc(out.len + 1);
        if (result != NULL) {
            memcpy(result, out.data, out.len);
            result[out.len] = '\0';
        }
    }
    free(out.data);
    return result;
}

typedef struct {
    const char *s;
    size_t i;
    size_t n;
    int ok;
} eclpy_json_in;

static int eclpy_hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void eclpy_in_skip_ws(eclpy_json_in *in) {
    while (in->i < in->n) {
        char c = in->s[in->i];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
            in->i++;
        } else {
            break;
        }
    }
}

static int eclpy_in_match(eclpy_json_in *in, const char *literal) {
    size_t k = strlen(literal);
    if (in->i + k > in->n || memcmp(in->s + in->i, literal, k) != 0) {
        return 0;
    }
    in->i += k;
    return 1;
}

static cl_object eclpy_json_parse_value(eclpy_json_in *in);

static cl_object eclpy_json_parse_string(eclpy_json_in *in) {
    eclpy_json_out out = {NULL, 0, 0, 1};
    in->i++; /* opening quote */
    while (in->i < in->n) {
        unsigned char c = (unsigned char)in->s[in->i++];
        if (c == '"') {
            cl_object str = ecl_make_simple_base_string(out.data ? out.data : "", (cl_fixnum)out.len);
            free(out.data);
            if (!out.ok) {
                break;
            }
            return str;
        }
        if (c == '\\') {
            if (in->i >= in->n) {
                break;
            }
            char e = in->s[in->i++];
            if (e == 'u') {
                unsigned long cp = 0;
                if (in->i + 4 > in->n) {
                    break;
                }
                int bad = 0;
                for (int k = 0; k < 4; k++) {
                    int d = eclpy_hex_value(in->s[in->i++]);
                    if (d < 0) {
                        bad = 1;
                        break;
                    }
                    cp = (cp << 4) | (unsigned long)d;
                }
                if (bad) {
                    break;
                }
                eclpy_out_utf8(&out, cp);
                continue;
            }
            switch (e) {
                case '"': eclpy_out_char(&out, '"'); break;
                case '\\': eclpy_out_char(&out, '\\'); break;
                case '/': eclpy_out_char(&out, '/'); break;
                case 'b': eclpy_out_char(&out, '\b'); break;
                case 'f': eclpy_out_char(&out, '\f'); break;
                case 'n': eclpy_out_char(&out, '\n'); break;
                case 'r': eclpy_out_char(&out, '\r'); break;
                case 't': eclpy_out_char(&out, '\t'); break;
                default: out.ok = 0; break;
            }
            if (!out.ok) {
                break;
            }
        } else {
            eclpy_out_char(&out, (char)c);
        }
    }
    free(out.data);
    in->ok = 0;
    return ECL_NIL;
}

static cl_object eclpy_json_parse_number(eclpy_json_in *in) {
    size_t start = in->i;
    int is_float = 0;
    if (in->i < in->n && (in->s[in->i] == '-' || in->s[in->i] == '+')) {
        in->i++;
    }
    while (in->i < in->n) {
        char c = in->s[in->i];
        if (c >= '0' && c <= '9') {
            in->i++;
        } else if (c == '.' || c == 'e' || c == 'E') {
            is_float = 1;
            in->i++;
        } else if (c == '+' || c == '-') {
            in->i++;
        } else {
            break;
        }
    }
    size_t len = in->i - start;
    if (len == 0) {
        in->ok = 0;
        return ECL_NIL;
    }
    cl_object token = ecl_make_simple_base_string(in->s + start, (cl_fixnum)len);
    if (is_float) {
        char *buf = (char *)malloc(len + 1);
        if (buf == NULL) {
            in->ok = 0;
            return ECL_NIL;
        }
        memcpy(buf, in->s + start, len);
        buf[len] = '\0';
        double value = strtod(buf, NULL);
        free(buf);
        return ecl_make_double_float(value);
    }
    return si_string_to_object(1, token);
}

static cl_object eclpy_json_parse_array(eclpy_json_in *in) {
    in->i++; /* [ */
    eclpy_in_skip_ws(in);
    if (in->i < in->n && in->s[in->i] == ']') {
        in->i++;
        return ECL_NIL;
    }
    cl_object acc = ECL_NIL;
    while (in->ok) {
        cl_object value = eclpy_json_parse_value(in);
        if (!in->ok) {
            return ECL_NIL;
        }
        acc = CONS(value, acc);
        eclpy_in_skip_ws(in);
        if (in->i >= in->n) {
            in->ok = 0;
            return ECL_NIL;
        }
        char c = in->s[in->i++];
        if (c == ',') {
            continue;
        }
        if (c == ']') {
            return cl_nreverse(acc);
        }
        in->ok = 0;
        return ECL_NIL;
    }
    return ECL_NIL;
}

static cl_object eclpy_json_parse_object(eclpy_json_in *in) {
    in->i++; /* { */
    eclpy_in_skip_ws(in);
    if (in->i < in->n && in->s[in->i] == '}') {
        in->i++;
        return ECL_NIL;
    }
    cl_object acc = ECL_NIL;
    while (in->ok) {
        eclpy_in_skip_ws(in);
        if (in->i >= in->n || in->s[in->i] != '"') {
            in->ok = 0;
            return ECL_NIL;
        }
        cl_object key = eclpy_json_parse_string(in);
        if (!in->ok) {
            return ECL_NIL;
        }
        eclpy_in_skip_ws(in);
        if (in->i >= in->n || in->s[in->i] != ':') {
            in->ok = 0;
            return ECL_NIL;
        }
        in->i++;
        cl_object value = eclpy_json_parse_value(in);
        if (!in->ok) {
            return ECL_NIL;
        }
        acc = CONS(CONS(key, value), acc);
        eclpy_in_skip_ws(in);
        if (in->i >= in->n) {
            in->ok = 0;
            return ECL_NIL;
        }
        char c = in->s[in->i++];
        if (c == ',') {
            continue;
        }
        if (c == '}') {
            return cl_nreverse(acc);
        }
        in->ok = 0;
        return ECL_NIL;
    }
    return ECL_NIL;
}

static cl_object eclpy_json_parse_value(eclpy_json_in *in) {
    eclpy_in_skip_ws(in);
    if (in->i >= in->n) {
        in->ok = 0;
        return ECL_NIL;
    }
    char c = in->s[in->i];
    switch (c) {
        case '{': return eclpy_json_parse_object(in);
        case '[': return eclpy_json_parse_array(in);
        case '"': return eclpy_json_parse_string(in);
        case 't': return eclpy_in_match(in, "true") ? ECL_T : (in->ok = 0, ECL_NIL);
        case 'f': return eclpy_in_match(in, "false") ? ECL_NIL : (in->ok = 0, ECL_NIL);
        case 'n': return eclpy_in_match(in, "null") ? ECL_NIL : (in->ok = 0, ECL_NIL);
        default: return eclpy_json_parse_number(in);
    }
}

cl_object eclpy_json_to_value(const char *s, int32_t n) {
    eclpy_json_in in = {s, 0, (size_t)n, 1};
    cl_object value = eclpy_json_parse_value(&in);
    if (!in.ok) {
        FEerror("invalid JSON value from Python.", 0);
    }
    return value;
}

