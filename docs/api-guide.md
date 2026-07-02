# API Guide for Heavy Users

## Strict `SExp` Evaluation

`Lisp.eval` accepts explicit `eclpy.SExp` values only. It does not accept
Python values, tuples, `Symbol` objects, or source strings directly. The
syntax tree is rendered to Lisp source only at the WASM boundary.

```python
from fractions import Fraction

import eclpy

with eclpy.Lisp() as lisp:
    assert lisp.eval(eclpy.SExp.integer(42)) == 42
    assert lisp.eval(
        eclpy.SExp.list(
            eclpy.SExp.symbol("+"),
            eclpy.SExp.integer(2),
            eclpy.SExp.integer(3),
        )
    ) == 5
    assert lisp.eval(
        eclpy.SExp.list(
            eclpy.SExp.symbol("/"),
            eclpy.SExp.list(
                eclpy.SExp.symbol("*"),
                eclpy.SExp.integer(3),
                eclpy.SExp.integer(5),
            ),
            eclpy.SExp.integer(2),
        )
    ) == Fraction(15, 2)
```

Use `eclpy.SExp.raw(...)` when you intentionally need raw Lisp source:

```python
import eclpy

with eclpy.Lisp() as lisp:
    assert lisp.eval(eclpy.SExp.raw("(+ 1 2)")) == 3
    assert lisp.eval(eclpy.SExp.raw("(+ 1 2) (+ 3 4)")) == 7
```

## References

Some Lisp values are returned as `Reference` handles. Scope them with
`with` or release them by closing the owning `Lisp` session:

```python
import eclpy
import eclpy.syntax as L
from eclpy.proxy import find_package

with eclpy.Lisp() as lisp:
    cl = find_package(lisp, "CL")
    with cl.constantly(4) as fn:
        assert cl.mapcar(fn, (1, 2, 3)) == eclpy.List(4, 4, 4)
```

## Conses and Proper Lists

`eclpy.Cons` and `eclpy.List` model Lisp cons cells and proper lists:

```python
import eclpy

with eclpy.Lisp() as lisp:
    assert lisp.eval(
        eclpy.SExp.list(
            eclpy.SExp.symbol("CONS"),
            eclpy.SExp.integer(1),
            eclpy.SExp.integer(2),
        )
    ) == eclpy.Cons(1, 2)

    values = lisp.eval(
        eclpy.SExp.list(
            eclpy.SExp.symbol("CONS"),
            eclpy.SExp.integer(1),
            eclpy.SExp.list(
                eclpy.SExp.symbol("CONS"),
                eclpy.SExp.integer(2),
                eclpy.SExp.list(),
            ),
        )
    )
    assert values == eclpy.List(1, 2)
    assert values.car == 1
    assert list(values) == [1, 2]
```

## Evaluate Python from Lisp

Every high-level `Lisp` session loads the `ecl-python` helper package.
Lisp code can evaluate Python expressions with `ecl-python:py-eval` and
execute Python statements with `ecl-python:py-exec`:

```python
import eclpy

with eclpy.Lisp() as lisp:
    assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-eval "1 + 2")')) == 3
    assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-exec "x = 5")')) == eclpy.List()
    assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-eval "x * 2")')) == 10
    assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-eval "[1, \\"x\\"]")')) == [1, "x"]
    assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-eval "(1, \\"x\\")")')) == eclpy.List(1, "x")
    assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-eval "{\\"a\\": 1}")')) == eclpy.List(
        eclpy.Cons("a", 1)
    )
```

`py-eval` accepts Python expressions and returns their value. `py-exec`
accepts Python statements and returns `NIL`. The Python globals are scoped
to the owning `EclSession` and persist for the session lifetime. A Python
`list` becomes a Lisp vector (round-tripping back as a Python `list`); a
`tuple` becomes a proper Lisp list; a `dict` becomes an alist of
`(key . value)` pairs, matching how `eclpy.encode.to_data_expr` already
converts these types for ordinary call arguments -- the same converter
renders a `py-eval`/`py-exec` result as Lisp source text for ECL's reader
to read and evaluate.

This is a full-power host Python evaluation hook, not a sandbox. Only
evaluate trusted code.

## The `PY` DSL

`eclpy/python.lisp` defines an explicit-load `PY` package with a Pythonic
object protocol (import, attributes, calls, subscripts, operators, context
managers, exception mapping) built on top of `py-eval`/`py-exec`. It is not
loaded automatically, so ordinary sessions pay nothing for it:

```python
import eclpy

with eclpy.Lisp() as lisp:
    lisp.eval(eclpy.SExp.raw('(load #p"/path/to/eclpy/python.lisp")'))
    assert lisp.eval(eclpy.SExp.raw("(py:as-int (py:add 1 2))")) == 3
```

True Python-to-Lisp callbacks (`PY:CALLBACK`) are not implemented by this
eval-backed runtime; calling them raises `PY:PYTHON-RUNTIME-ERROR`.

## Runtime Selection

By default, `Lisp()` and `EclSession()` load the packaged
`eclpy/ecl_eval.wasm`. To use a different runtime, pass `wasm_path=` or set
`ECL_WASM`.

## Low-Level Session API

For the low-level source-string bridge, use `EclSession`:

```python
from eclpy import EclSession

with EclSession() as session:
    assert session.eval("(+ 1 2)") == "3"
    session.eval("(defparameter *x* 41)")
    assert session.eval("(1+ *x*)") == "42"
```

## Errors

High-level Lisp conditions cross into Python as `EclError` with condition
details:

```python
import eclpy

with eclpy.Lisp() as lisp:
    try:
        lisp.eval(eclpy.SExp.raw('(error "boom")'))
    except eclpy.EclError as exc:
        print(exc.condition_type)
        print(exc.message)
```

## Security Model

`eclpy` is designed for trusted local code. It is not a sandbox for
untrusted Lisp or Python:

- `SExp.raw` intentionally embeds raw Lisp source.
- `ecl-python:py-eval` and `py-exec` run in the host Python interpreter
  with normal Python privileges.
- The ASDF/file bridge lets Lisp inspect and load ordinary host paths
  needed by the current process.

Do not expose these APIs directly to untrusted input without an
application-level sandbox or allow-list.

See also: [ASDF](asdf.md), [SWANK/SLIME](swank.md),
[Architecture](architecture.md).
