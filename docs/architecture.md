# Architecture

The runtime is layered so that each boundary has one responsibility.

The Python layer has three entry paths. They deliberately converge at
`Lisp._eval_sexp`, so all high-level calls share one reference table, one
JSON decoder, and one low-level session boundary.

```mermaid
%%{init: {"themeVariables": {"fontSize": "18px"}}}%%
flowchart TB
    user["User code"]
    choose{"How is Lisp code built?"}
    syntax["syntax.py<br/>Python literals as Lisp forms"]
    proxy["proxy.py<br/>live package lookup and calls"]
    direct["SExp<br/>explicit source tree"]
    encode["encode.py<br/>data arguments become Lisp literals"]
    sexp["sexp.py<br/>one safe renderer"]
    lisp["lisp.py<br/>one facade, one reference table"]
    session["session.py<br/>one Wasmtime boundary"]
    protocol["protocol.py + objects.py<br/>JSON result -> Python values"]

    user --> choose
    choose -->|"convenience"| syntax
    choose -->|"Common Lisp package API"| proxy
    choose -->|"full control"| direct
    proxy -->|"call args"| encode
    syntax --> sexp
    direct --> sexp
    encode --> sexp
    sexp -->|"only high-level input accepted"| lisp
    lisp --> session
    session -->|"result envelope"| protocol
    protocol -->|"Reference handles release through owner"| lisp
    protocol --> user
```

The Wasm side is a separate boundary. The C bridge moves bytes and calls ECL;
`runtime.lisp` owns Lisp-level policy such as serialization, reference storage,
ASDF loading, Python eval/exec, and SWANK startup.

```mermaid
%%{init: {"themeVariables": {"fontSize": "18px"}}}%%
flowchart TB
    session["session.py<br/>alloc source, call export, free buffers"]
    cbridge["native/eclpy_eval.c<br/>boot ECL, read forms, call runtime helpers"]
    runtime{"runtime.lisp policy layer"}
    serializer["serialize/json-encode<br/>Lisp values -> protocol JSON"]
    refs["*objects* table<br/>opaque Reference ids"]
    hostenv["hostenv.py imports<br/>files, sockets, Python eval/exec"]
    lazy["lazy source loads<br/>ASDF and SWANK only when requested"]
    pydsl["python.lisp<br/>explicit PY DSL load"]

    session --> cbridge
    cbridge --> runtime
    runtime --> serializer
    runtime --> refs
    runtime -->|"native-load, OPEN, sockets, py-eval"| hostenv
    hostenv -->|"read/probe/stat"| lazy
    pydsl -.->|"uses py-eval/py-exec"| runtime
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
%%{init: {"themeVariables": {"fontSize": "18px"}}}%%
flowchart LR
    pyin{"Python value<br/>or SExp"}
    source["Lisp source text<br/>not JSON"]
    eval["ECL reader + evaluator"]
    lisp{"Lisp value"}
    json["Protocol JSON<br/>status + typed value"]
    pyout{"Python value"}

    pyin -->|"SExp: render directly"| source
    pyin -->|"data: encode.to_data_expr"| source
    source --> eval
    eval --> lisp
    lisp -->|"runtime.lisp serialize/json-encode"| json
    json -->|"protocol.decode_result"| pyout

    pyeval["py-eval / py-exec result"] -.->|"hostenv encodes as Lisp source"| source
    json -.->|"C bridge only moves UTF-8 bytes"| pyout
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
