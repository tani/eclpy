# Lisp source reference

Sphinx does not introspect Common Lisp in this project, so the Lisp runtime is
included as syntax-highlighted source. This keeps the Python API reference and
the Lisp-side protocol implementation in the same documentation build.

## `eclpy/runtime.lisp`

`runtime.lisp` is loaded automatically by `Lisp`. It defines the `ECL-PYTHON`
helper package, the JSON value protocol encoder, reference storage, host-file
bridges for ASDF/SWANK, Python `eval`/`exec` entry points, and the SWANK startup
shim.

```{literalinclude} ../eclpy/runtime.lisp
:language: common-lisp
:linenos:
```

## `eclpy/python.lisp`

`python.lisp` is loaded automatically by high-level `Lisp` sessions after
`runtime.lisp`. It defines an eval-backed Python object protocol on top of
`ecl-python:py-eval` and `ecl-python:py-exec`.

```{literalinclude} ../eclpy/python.lisp
:language: common-lisp
:linenos:
```

## `eclpy/swank/loader.lisp`

`loader.lisp` is the project-authored SWANK loader shim. The remaining files in
`eclpy/swank/` are copied from vendored SLIME by the build script.

```{literalinclude} ../eclpy/swank/loader.lisp
:language: common-lisp
:linenos:
```
