# eclpy

Common Lisp for Python, powered by Embeddable Common Lisp (ECL) running as a
packaged WebAssembly runtime.

`eclpy` exposes a cl4py-inspired `Lisp` API for evaluating Lisp forms,
calling functions, accessing packages, and converting common Lisp values to
Python values. Normal users can start with `eclpy.syntax`; heavier users can
drop down to explicit `SExp` trees or the raw `EclSession` bridge.

The bridge is bidirectional: Lisp code can also evaluate Python through
`ecl-python:py-eval` / `py-exec`, and an explicit-load `PY` DSL
(`eclpy/python.lisp`) gives Lisp code a Pythonic object protocol (import,
attributes, calls, operators, context managers) on top of that bridge.

## Install

After the package is published to PyPI:

```sh
pip install eclpy
```

The wheel is expected to include the ECL WebAssembly runtime, so normal users
do not need a local ECL installation or an Emscripten toolchain.

## Quick Start

```python
import eclpy
import eclpy.syntax as L

with eclpy.Lisp() as lisp:
    assert lisp.eval(L.expr(("+", 1, 1))) == 2
```

See [docs/quickstart.md](docs/quickstart.md) for the syntax API, the
command-line REPL, Pythonic package proxies, and the full public API.

## Documentation

- [docs/quickstart.md](docs/quickstart.md) -- install, quick start, CLI,
  Pythonic proxies, strings/symbols/lists, public API.
- [docs/api-guide.md](docs/api-guide.md) -- strict `SExp` evaluation,
  references, conses, evaluating Python from Lisp, the `PY` DSL, low-level
  session API, errors, security model.
- [docs/asdf.md](docs/asdf.md) -- loading ASDF and local source systems.
- [docs/swank.md](docs/swank.md) -- SWANK/SLIME server and its WASM sandbox
  limitations.
- [docs/architecture.md](docs/architecture.md) -- module layering and the
  Python<->Lisp value protocol.
- [docs/development.md](docs/development.md) -- running tests, building the
  WASM runtime, building a wheel.

## License

Copyright (c) 2026 Masaya Taniguchi <masaya.taniguchi@tani.cc>.

This project uses the same license terms as ECL: GNU LGPL 2.1 or later. See
`LICENSE` for eclpy and ECL copyright notices and `COPYING` for the LGPL 2.1
text.
