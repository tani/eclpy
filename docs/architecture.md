# Architecture

The runtime is layered so that each boundary has one responsibility.

```mermaid
flowchart TB
    subgraph PY[Python process]
        direction TB
        subgraph HL["High-level API"]
            direction LR
            lisp_py["lisp.py<br/>Lisp facade, Reference lifecycle"]
            proxy_py["proxy.py<br/>package / callable-symbol proxies"]
            syntax_py["syntax.py<br/>L.expr / L.quote / L.array"]
        end
        subgraph VS["Value and syntax"]
            direction LR
            sexp_py["sexp.py<br/>SExp tree -&gt; safe Lisp source"]
            encode_py["encode.py<br/>Python values -&gt; Lisp source (call args)"]
            protocol_py["protocol.py<br/>object-shaped JSON protocol"]
            objects_py["objects.py<br/>Symbol / List / Cons / Reference"]
        end
        subgraph LL["Low-level host"]
            direction LR
            session_py["session.py<br/>Wasmtime lifecycle, eval, eval_json"]
            hostenv_py["hostenv.py<br/>env imports: host files, stat, Python eval/exec"]
            wasmmem_py["wasmmem.py<br/>linear-memory helpers, WASI errno"]
        end
        subgraph WB["WASM boundary"]
            cbridge["native/eclpy_eval.c<br/>ECL boot, C ABI, string shuttling"]
        end
        HL --> VS --> LL --> WB
    end

    subgraph ECL["ECL inside WebAssembly"]
        runtime_lisp["eclpy/runtime.lisp<br/>ecl-python helper package<br/>evaluate / serialize / json-encode<br/>py-eval / py-exec / ASDF file bridge shims"]
    end

    subgraph DSL["Explicit-load DSL (never loaded automatically)"]
        python_lisp["eclpy/python.lisp<br/>PY / PY.RUNTIME / PY.INTERNAL<br/>Pythonic object protocol on py-eval/py-exec"]
    end

    WB -->|WASM host imports| ECL
    ECL -.->|load explicitly| DSL
```

## Value Protocol

The two directions use different mechanisms, each owned by a different
module.

**Python -> Lisp** (`Lisp.eval`/proxy call arguments, and the Python value
a `py-eval`/`py-exec` call produces): `encode.py`'s `to_data_expr` renders
the value as literal Lisp source text -- numbers, strings, symbols, and
safely escaped string/symbol literals -- which ECL's own reader parses and
evaluates directly. No JSON is involved; the C layer never sees these
values, only the finished Lisp source string.

**Lisp -> Python** (every `Lisp.eval` result, including the value returned
by a `py-eval`/`py-exec` form): `protocol.py` owns decoding; the Lisp side
owns `serialize` in `runtime.lisp`. The C layer does not parse JSON and
does not interpret value fields; it only moves strings across the WASM
boundary and calls Lisp helper functions.

```mermaid
flowchart LR
    subgraph "Python -> Lisp"
        direction LR
        pv["Python value<br/>(call arg or py-eval/py-exec result)"] --> enc["encode.to_data_expr"]
        enc --> src["Lisp source text"]
        src --> spliced["spliced into the evaluated form<br/>/ (%py-eval source)"]
        spliced --> rd["read-from-string + eval"]
        rd --> lv["Lisp value"]
    end
```

```mermaid
flowchart LR
    subgraph "Lisp -> Python"
        direction LR
        lv2["Lisp value"] --> ser["ecl-python:serialize"]
        ser --> je["ecl-python:json-encode"]
        je --> shuttle["C string shuttle"]
        shuttle --> dec["json.loads / protocol.decode_value"]
        dec --> pv2["Python value"]
    end
```

The wire shape for the Lisp -> Python direction is a JSON object with
named fields. Every top-level Lisp result is a protocol envelope:

```json
{"protocol": "eclpy", "version": 1, "status": "ok", "value": {}}
{"protocol": "eclpy", "version": 1, "status": "error",
 "condition_type": "SIMPLE-ERROR", "message": "boom"}
```

Value nodes use a `type` field plus named payload fields:

```json
{"type": "nil"}
{"type": "true"}
{"type": "int", "value": "42"}
{"type": "ratio", "numerator": "3", "denominator": "2"}
{"type": "float", "value": "1.5d0"}
{"type": "string", "value": "hello"}
{"type": "symbol", "name": "CAR", "package": "COMMON-LISP"}
{"type": "list", "items": [{"type": "int", "value": "1"}]}
{"type": "dotted-list", "items": [{"type": "int", "value": "1"}],
 "tail": {"type": "int", "value": "2"}}
{"type": "vector", "items": []}
{"type": "package", "name": "COMMON-LISP"}
{"type": "ref", "id": 7, "kind": "FUNCTION"}
```

Package lookup uses the same protocol/version envelope and returns exactly
one of `missing`, `callable`, `value`, or `symbol`:

```json
{"protocol": "eclpy", "version": 1, "kind": "missing"}
{"protocol": "eclpy", "version": 1, "kind": "callable",
 "callable_type": "function", "name": "+", "package": "COMMON-LISP"}
{"protocol": "eclpy", "version": 1, "kind": "value", "value": {}}
{"protocol": "eclpy", "version": 1, "kind": "symbol",
 "name": "FOO", "package": null}
```
