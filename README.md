# ecl

Common Lisp for Python, powered by Embeddable Common Lisp (ECL) running as a
packaged WebAssembly runtime.

The package exposes a cl4py-inspired `Lisp` API for evaluating Lisp forms,
calling functions, accessing packages, and converting common Lisp values to
Python values. The lower-level `EclSession` API is still available as the raw
WASM boundary.

## Build the WASM runtime

The wheel includes `ecl/ecl_eval.wasm`, but that file must be generated before
building a distribution. The ECL source is vendored in `vendor/ecl-26.5.5`; no
source tarball is required.

```sh
uv run python scripts/build_ecl_wasm.py
```

The script:

- builds a native host ECL used for cross-compilation;
- builds the vendored ECL source for `wasm32-unknown-emscripten`;
- links `native/eclpy_eval.c` into `build/ecl/ecl_eval.wasm`;
- copies the runtime to `ecl/ecl_eval.wasm` for wheel packaging;
- runs a Python smoke test through `EclSession()`.

## Use from Python

```python
from fractions import Fraction

import ecl

with ecl.Lisp() as lisp:
    assert lisp.eval(42) == 42
    assert lisp.eval(("+", 2, 3)) == 5
    assert lisp.eval(("/", ("*", 3, 5), 2)) == Fraction(15, 2)
    assert lisp.eval(("loop", "for", "i", "below", 5, "collect", "i")) == ecl.List(
        0, 1, 2, 3, 4
    )
```

Python tuples are treated as Lisp forms. Strings inside tuples are converted to
symbols, matching cl4py's short notation. `Lisp.eval` does not accept raw Python
source strings; use `ecl.SExp.raw(...)` when you intentionally need raw Lisp
source:

```python
with ecl.Lisp() as lisp:
    assert lisp.eval(ecl.SExp.raw("(+ 1 2)")) == 3
    assert lisp.eval(ecl.SExp.raw("(+ 1 2) (+ 3 4)")) == 7
```

Use `ecl.List` when you need strings to remain Lisp strings:

```python
with ecl.Lisp() as lisp:
    form = ecl.List(ecl.Symbol("STRING="), "foo", "foo")
    assert lisp.eval(form) is True
    assert lisp.eval(ecl.Symbol("*PRINT-BASE*", "COMMON-LISP")) == 10
```

You can look up functions and packages:

```python
with ecl.Lisp() as lisp:
    add = lisp.function("+")
    assert add(1, 2, 3, 4) == 10

    cl = lisp.find_package("CL")
    assert cl.oddp(5) is True
    assert cl.cons(5, None) == ecl.List(5)
    assert cl.remove(5, [1, -5, 2, 7, 5, 9], key=cl.abs) == [1, 2, 7, 9]
    assert cl.mapcar(cl.constantly(4), (1, 2, 3)) == ecl.List(4, 4, 4)
```

Lisp references can be scoped with `with`:

```python
with ecl.Lisp() as lisp:
    cl = lisp.find_package("CL")
    with cl.constantly(4) as fn:
        assert cl.mapcar(fn, (1, 2, 3)) == ecl.List(4, 4, 4)
```

Package attributes follow cl4py-style name conversion:

```python
with ecl.Lisp() as lisp:
    cl = lisp.find_package("CL")
    assert cl.add(2, 3, 4, 5) == 14       # +
    assert cl.stringgt("baz", "bar") == 2 # STRING>
    assert cl.print_base == 10            # *PRINT-BASE*
```

`ecl.Cons` and `ecl.List` model Lisp cons cells and proper lists:

```python
with ecl.Lisp() as lisp:
    assert lisp.eval(("CONS", 1, 2)) == ecl.Cons(1, 2)

    values = lisp.eval(("CONS", 1, ("CONS", 2, ())))
    assert values == ecl.List(1, 2)
    assert values.car == 1
    assert list(values) == [1, 2]
```

Internally, Python values are converted to an `ecl.SExp` syntax tree first. The
WASM bridge only receives Lisp source after `str(sexp)` renders that tree at the
runtime boundary.

By default `Lisp()` and `EclSession()` load the packaged `ecl/ecl_eval.wasm`. To
use a different runtime, pass `wasm_path=` or set `ECL_WASM`.

For the low-level source-string bridge, use `EclSession`:

```python
from ecl import EclSession

with EclSession() as session:
    assert session.eval("(+ 1 2)") == "3"
    session.eval("(defparameter *x* 41)")
    assert session.eval("(1+ *x*)") == "42"
```

High-level Lisp conditions cross into Python as `EclError` with condition
details:

```python
import ecl

with ecl.Lisp() as lisp:
    try:
        lisp.eval(ecl.SExp.raw('(error "boom")'))
    except ecl.EclError as exc:
        print(exc.condition_type)
        print(exc.message)
```

## Build a wheel

After generating `ecl/ecl_eval.wasm`:

```sh
uv build --wheel --out-dir dist --clear
```

The wheel should contain:

```text
ecl/__init__.py
ecl/api.py
ecl/decode.py
ecl/encode.py
ecl/session.py
ecl/objects.py
ecl/reader.py
ecl/runtime_lisp.py
ecl/sexp.py
ecl/ecl_eval.wasm
```

You can smoke-test the built wheel outside the source tree:

```sh
uv run --no-project --isolated \
  --with dist/ecl-0.1.0-py3-none-any.whl \
  python -c 'import ecl; print(ecl.Lisp().eval(("+", 10, 32)))'
```

## Test

```sh
uv run python -m unittest
```

The tests cover raw low-level evaluation, Python-form evaluation, explicit
`SExp.raw` evaluation, package/function lookup, macros and special forms,
cons/list conversion, higher-order Lisp functions, reference lifecycle, result
parsing, missing runtime errors, and Lisp-side exceptions.

## Runtime notes

The current Emscripten build emits a core wasm module that imports
`wasi_snapshot_preview1` plus Emscripten `env` functions. The Python loader
provides the minimal Emscripten compatibility shims needed by this runtime.

WASI 0.3 requires a component-model toolchain/runtime path. The local
Emscripten 6.0.1 and `wasmtime` Python package used here do not expose that path,
so this package hosts the Preview 1 module directly.

Emscripten prints `warning: unsupported syscall: __syscall_prlimit64` during ECL
startup. It has not blocked initialization or evaluation in the current tests.

## License

This project uses the same license terms as ECL: GNU LGPL 2.1 or later. See
`LICENSE` for ECL's copyright notice and `COPYING` for the LGPL 2.1 text.
