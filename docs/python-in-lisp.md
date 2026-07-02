# Python in Lisp

`eclpy` loads the `PY` Common Lisp DSL automatically whenever a high-level
`eclpy.Lisp` session starts. You can call Python modules, objects, methods,
operators, and context managers from Lisp code without first loading
`eclpy/python.lisp` yourself.

The DSL is implemented in ordinary Lisp source at `eclpy/python.lisp` and is
included in the Sphinx source reference. It is loaded after `runtime.lisp`, using
the same in-memory source-loading pattern as the core helper package.

## Minimal example

```python
import eclpy

with eclpy.Lisp() as lisp:
    assert lisp.eval(eclpy.SExp.raw("(py:as-int (py:add 1 2))")) == 3
```

`PY` forms return `PY.RUNTIME:PY-OBJECT` handles for live Python objects. Convert
handles back to serializable Common Lisp values with `PY:AS-*` helpers:

```python
import eclpy

with eclpy.Lisp() as lisp:
    assert lisp.eval(eclpy.SExp.raw('(py:as-string (py:str (py:list 1 2 3)))')) == "[1, 2, 3]"
    assert lisp.eval(eclpy.SExp.raw('(py:as-bool (py:truth (py:list 1)))')) is True
    assert lisp.eval(eclpy.SExp.raw('(py:as-bool (py:none-p (py:none)))')) is True
```

## Import modules

Use `PY:WITH-PY` for short-lived module bindings. A string binding imports the
module and derives the Lisp variable name from the final dotted component.

```lisp
(py:with-py ("json")
  (py:as-string
   (py:call-attr json "dumps" (py:dict "x" 1))))
```

Use an explicit two-item binding when the Lisp variable name should differ from
the module name:

```lisp
(py:with-py ((np "numpy"))
  (py:as-string (py:repr (py:call-attr np "array" (py:list 1 2 3)))))
```

## Build Python values

The DSL distinguishes Common Lisp values from Python object handles:

| PY form | Python value |
| --- | --- |
| `(py:none)` | `None` |
| `(py:true)` / `(py:false)` | `True` / `False` |
| `(py:list 1 2)` | `[1, 2]` |
| `(py:tuple 1 2)` | `(1, 2)` |
| `(py:dict "x" 1)` | `{"x": 1}` |
| `(py:set 1 2)` | `{1, 2}` |
| `(py:slice (py:none) 10 2)` | `slice(None, 10, 2)` |

Plain Common Lisp `NIL` is not implicitly converted to Python `None`; use
`PY:NONE` when you need `None`.

## Attributes, calls, and subscripts

```lisp
(py:with-py ("math")
  (py:as-float (py:call-attr math "sqrt" 9)))
```

```lisp
(py:as-int
 (py:subscript (py:list 10 20 30) 1))
```

`PY:CALL` accepts positional values, keyword arguments, starred positional
arguments, and starred keyword arguments:

```lisp
(py:call callable
         1
         (py:keyword :base 10)
         (py:starred (py:list 2 3))
         (py:kw-starred (py:dict "debug" (py:false))))
```

## Operators and truth

Arithmetic and comparison forms map to Python operators and return Python object
handles unless converted:

```lisp
(py:as-int (py:mul (py:add 1 2) 10))
(py:as-bool (py:lt 3 5))
(py:truth-cl (py:list 1)) ; Common Lisp boolean
```

`PY:AND` and `PY:OR` preserve Python truth-value behavior: they return the
winning Python object, not just a Common Lisp boolean.

## Context managers

`PY:WITH-CONTEXT` calls `__enter__` and guarantees `__exit__` in an
`UNWIND-PROTECT` cleanup.

```lisp
(py:with-py ((pathlib "pathlib"))
  (py:with-context ((tmp (py:call-attr (py:attr pathlib "Path") "cwd")))
    (py:as-string (py:str tmp))))
```

## Error mapping

Python exceptions raised through `py-eval` or `py-exec` become `PY:PYTHON-ERROR`
subclasses:

- `PY:PYTHON-IMPORT-ERROR`
- `PY:PYTHON-ATTRIBUTE-ERROR`
- `PY:PYTHON-TYPE-ERROR`
- `PY:PYTHON-VALUE-ERROR`
- `PY:PYTHON-RUNTIME-ERROR`

`PY:TRY` lets Lisp code handle those mapped conditions:

```lisp
(py:try
  (py:attr (py:none) "missing")
  ("AttributeError" (condition)
    (py:condition-message condition)))
```

## Current limitation

`PY:CALLBACK` is intentionally unsupported in the current eval-backed bridge.
The bridge can ask host Python to evaluate source, but it cannot pass a live
Common Lisp callback object into Python. Calling `PY:CALLBACK` raises
`PY:PYTHON-RUNTIME-ERROR` immediately.
