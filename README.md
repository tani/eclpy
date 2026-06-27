# ecl

Python bindings for running Embeddable Common Lisp (ECL) through a packaged
WebAssembly runtime.

The package exposes a small `EclSession` API. A session boots ECL inside
Wasmtime, evaluates Common Lisp source strings, and preserves Lisp state across
calls.

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
from ecl import EclSession

with EclSession() as ecl:
    print(ecl.eval("(+ 1 2)"))        # "3"
    ecl.eval("(defparameter *x* 41)")
    print(ecl.eval("(1+ *x*)"))       # "42"
```

By default `EclSession()` loads the packaged `ecl/ecl_eval.wasm`. To use a
different runtime, pass `wasm_path=` or set `ECL_WASM`.

Lisp conditions currently cross into Python as `EclError` with a generic error
message:

```python
from ecl import EclError, EclSession

with EclSession() as ecl:
    try:
        ecl.eval('(error "boom")')
    except EclError as exc:
        print(exc)
```

## Build a wheel

After generating `ecl/ecl_eval.wasm`:

```sh
uv build --wheel --out-dir dist --clear
```

The wheel should contain:

```text
ecl/__init__.py
ecl/session.py
ecl/ecl_eval.wasm
```

You can smoke-test the built wheel outside the source tree:

```sh
uv run --no-project --isolated \
  --with dist/ecl-0.1.0-py3-none-any.whl \
  python -c 'from ecl import EclSession; print(EclSession().eval("(+ 10 32)"))'
```

## Test

```sh
uv run pytest
```

The tests cover arithmetic evaluation, multiple forms, persistent session state,
missing runtime errors, and Lisp-side exceptions.

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
