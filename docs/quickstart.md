# Quick Start

For everyday use, import `eclpy.syntax` as a small expression builder.

## Install

After the package is published to PyPI:

```sh
pip install eclpy
```

The wheel is expected to include the ECL WebAssembly runtime, so normal
users do not need a local ECL installation or an Emscripten toolchain.

## Quick Start

```python
from fractions import Fraction

import eclpy
import eclpy.syntax as L

with eclpy.Lisp() as lisp:
    assert lisp.eval(L.expr(1)) == 1
    assert lisp.eval(L.expr(("+", 1, 1))) == 2
    assert lisp.eval(L.expr(("/", ("*", 3, 5), 2))) == Fraction(15, 2)
    assert lisp.eval(
        L.expr(("loop", "for", "i", "below", 5, "collect", "i"))
    ) == eclpy.List(0, 1, 2, 3, 4)
```

`L.expr` takes one Python value. Use `L.expr(("+", 1, 1))`, not
`L.expr("+", 1, 1)`.

## Command-Line Interface

`eclpy` ships a command-line REPL that starts in the `eclpy-user` package,
which already uses `ecl-python` and `cl`, so `py-eval` and `py-exec` are
available without a package prefix:

```console
$ eclpy
ECLPY-USER> (py-eval "1 + 2")
3
ECLPY-USER> (py-exec "import math")
NIL
ECLPY-USER> (py-eval "math.pi")
3.1415927
ECLPY-USER> (in-package :cl-user)
#<"COMMON-LISP-USER" package>
COMMON-LISP-USER>
```

The prompt always shows the current package name. Multi-line forms are
supported; input continues until all parentheses are closed. Readline
history is saved to `~/.eclpy_history` when the session ends.

To evaluate a single expression and exit:

```sh
eclpy -e "(+ 1 2)"
```

To run a Lisp source file:

```sh
eclpy path/to/script.lisp
```

To start a SWANK/SLIME server instead of the REPL (see
[SWANK/SLIME](swank.md)):

```sh
eclpy --swank            # port 4005
eclpy --swank 4006       # explicit port
```

## Use Pythonic Proxies

`find_package` returns a package proxy. Attributes are converted to Common
Lisp symbol names in a cl4py-style way:

```python
import eclpy
import eclpy.syntax as L
from eclpy.proxy import find_package

with eclpy.Lisp() as lisp:
    cl = find_package(lisp, "CL")
    assert lisp.eval(L.expr(["package-name", cl])) == "COMMON-LISP"
    assert cl.oddp(5) is True
    assert cl.add(2, 3, 4, 5) == 14        # +
    assert cl.stringgt(L.string("baz"), L.string("bar")) == 2  # STRING>
    assert cl.print_base == 10             # *PRINT-BASE*
```

Proxy function arguments use the same conversion rules as `L.expr`. A
Python string means a Lisp symbol, so use `L.string("...")` when you need a
Lisp string value.

## Strings, Symbols, Lists

Strings and symbols stay distinct on both sides of the bridge. `List`
models proper Lisp lists, and `Cons` models dotted cons cells:

```python
import eclpy
import eclpy.syntax as L

with eclpy.Lisp() as lisp:
    assert lisp.eval(L.expr(("STRING=", L.string("foo"), L.string("foo")))) is True
    assert lisp.eval(eclpy.SExp.raw("'CL:CAR")) == eclpy.Symbol("CAR", "COMMON-LISP")
    assert lisp.eval(L.expr(("list", 1, 2, 3))) == eclpy.List(1, 2, 3)
    assert lisp.eval(L.expr(("length", L.array([1, 2, 3])))) == 3
    assert lisp.eval(L.expr(("array-dimensions", L.array([[1, 2], [3, 4]])))) == eclpy.List(2, 2)
```

## Public API

`eclpy` exports these public Python objects:

```python
from eclpy import (
    Cons,
    EclError,
    EclSession,
    Lisp,
    List,
    Package,
    Reference,
    SExp,
    Symbol,
)
```

`Package` is the package proxy returned by `eclpy.proxy.find_package`.
`Reference` is a scoped handle for Lisp objects that cannot be copied
directly into Python.

The modules are split by layer:

| Module                    | Responsibility                                        |
| -------------------------- | ------------------------------------------------------ |
| `eclpy/lisp.py`            | high-level `Lisp` facade and reference lifecycle       |
| `eclpy/proxy.py`           | Pythonic package proxy                                 |
| `eclpy/syntax.py`          | fluent `SExp`/literal builders                         |
| `eclpy/sexp.py`            | safe Lisp source rendering                              |
| `eclpy/encode.py`          | Python values -> Lisp source expressions                |
| `eclpy/protocol.py`        | object-shaped JSON value protocol                       |
| `eclpy/session.py`         | low-level Wasmtime/ECL session                          |
| `eclpy/hostenv.py`         | WASM env imports: files, stat, Python eval/exec         |
| `eclpy/wasmmem.py`         | WASM linear-memory helpers                              |
| `eclpy/runtime.lisp`       | Lisp-side helper package source                         |
| `eclpy/python.lisp`        | automatically loaded Common Lisp `PY` DSL for Python object interop |
| `eclpy/swank/`             | bundled upstream SWANK server source                    |

See [the API guide](api-guide.md) for `SExp`, references, conses, the `PY`
DSL, and error handling; [Python in Lisp](python-in-lisp.md) for Python interop
from Lisp; [Architecture](architecture.md) for how values cross the WASM boundary.
