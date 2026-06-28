=====
eclpy
=====

Common Lisp for Python, powered by Embeddable Common Lisp (ECL) running as a
packaged WebAssembly runtime.

``eclpy`` exposes a cl4py-inspired ``Lisp`` API for evaluating Lisp forms,
calling functions, accessing packages, and converting common Lisp values to
Python values. The lower-level ``EclSession`` API is still available as the raw
WASM boundary.

.. contents::
   :local:
   :depth: 2

Highlights
==========

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Area
     - Behavior
   * - Runtime
     - ECL is built as a WebAssembly module and loaded from the wheel.
   * - Evaluation
     - ``Lisp.eval`` accepts explicit ``eclpy.SExp`` trees only.
   * - Simple API
     - ``eclpy.simple`` provides aggressive Python shorthand for constructing
       those ``SExp`` trees.
   * - Interop
     - Lisp strings, symbols, lists, cons cells, functions, packages, and
       conditions have explicit Python-side representations.
   * - Distribution
     - The ECL source is vendored, while the generated ``*.wasm`` runtime is
       built locally and excluded from Git history.

Install
=======

After the package is published to PyPI:

.. code-block:: sh

   pip install eclpy

The wheel is expected to include the ECL WebAssembly runtime, so normal users do
not need a local ECL installation or an Emscripten toolchain.

Use From Python
===============

Strict ``SExp`` Evaluation
--------------------------

The high-level API receives a syntax tree first. ``SExp.__str__`` renders that
tree to Lisp source only at the WASM boundary.

.. code-block:: python

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

``Lisp.eval`` only accepts explicit ``eclpy.SExp`` values. It does not accept
Python values, tuples, ``Symbol`` objects, or source strings directly.

Use ``eclpy.SExp.raw(...)`` when you intentionally need raw Lisp source:

.. code-block:: python

   import eclpy

   with eclpy.Lisp() as lisp:
       assert lisp.eval(eclpy.SExp.raw("(+ 1 2)")) == 3
       assert lisp.eval(eclpy.SExp.raw("(+ 1 2) (+ 3 4)")) == 7

Simple API Shorthand
--------------------

For shorthand syntax, use the separate Simple API layer:

.. code-block:: python

   from fractions import Fraction

   import eclpy
   import eclpy.simple as L

   with eclpy.Lisp() as lisp:
       assert lisp.eval(L.expr(1)) == 1
       assert lisp.eval(L.expr(("+", 1, 1))) == 2
       assert lisp.eval(L.expr(("/", ("*", 3, 5), 2))) == Fraction(15, 2)
       assert lisp.eval(
           L.expr(("loop", "for", "i", "below", 5, "collect", "i"))
       ) == eclpy.List(0, 1, 2, 3, 4)

.. note::

   ``L.expr`` takes one Python value. Use ``L.expr(("+", 1, 1))``, not
   ``L.expr("+", 1, 1)``.

Strings and Symbols
-------------------

Strings and symbols stay distinct on both sides of the bridge:

.. code-block:: python

   import eclpy
   import eclpy.simple as L

   with eclpy.Lisp() as lisp:
       form = L.expr(("STRING=", L.string("foo"), L.string("foo")))
       assert lisp.eval(form) is True
       assert lisp.eval(eclpy.SExp.symbol("*PRINT-BASE*", "COMMON-LISP")) == 10
       assert lisp.eval(eclpy.SExp.raw("'CL:CAR")) == eclpy.Symbol("CAR", "COMMON-LISP")

Functions and Packages
----------------------

You can look up functions and packages:

.. code-block:: python

   import eclpy

   with eclpy.Lisp() as lisp:
       add = lisp.function("+")
       assert add(1, 2, 3, 4) == 10

       cl = lisp.find_package("CL")
       assert cl.oddp(5) is True
       assert cl.cons(5, None) == eclpy.List(5)
       assert cl.remove(5, [1, -5, 2, 7, 5, 9], key=cl.abs) == [1, 2, 7, 9]
       assert cl.mapcar(cl.constantly(4), (1, 2, 3)) == eclpy.List(4, 4, 4)

Lisp references can be scoped with ``with``:

.. code-block:: python

   import eclpy

   with eclpy.Lisp() as lisp:
       cl = lisp.find_package("CL")
       with cl.constantly(4) as fn:
           assert cl.mapcar(fn, (1, 2, 3)) == eclpy.List(4, 4, 4)

Package attributes follow cl4py-style name conversion:

.. code-block:: python

   with eclpy.Lisp() as lisp:
       cl = lisp.find_package("CL")
       assert cl.add(2, 3, 4, 5) == 14        # +
       assert cl.stringgt("baz", "bar") == 2  # STRING>
       assert cl.print_base == 10             # *PRINT-BASE*

Conses and Lists
----------------

``eclpy.Cons`` and ``eclpy.List`` model Lisp cons cells and proper lists:

.. code-block:: python

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

Runtime Selection
-----------------

By default, ``Lisp()`` and ``EclSession()`` load the packaged
``eclpy/ecl_eval.wasm``. To use a different runtime, pass ``wasm_path=`` or set
``ECL_WASM``.

Low-Level Session API
---------------------

For the low-level source-string bridge, use ``EclSession``:

.. code-block:: python

   from eclpy import EclSession

   with EclSession() as session:
       assert session.eval("(+ 1 2)") == "3"
       session.eval("(defparameter *x* 41)")
       assert session.eval("(1+ *x*)") == "42"

Errors
------

High-level Lisp conditions cross into Python as ``EclError`` with condition
details:

.. code-block:: python

   import eclpy

   with eclpy.Lisp() as lisp:
       try:
           lisp.eval(eclpy.SExp.raw('(error "boom")'))
       except eclpy.EclError as exc:
           print(exc.condition_type)
           print(exc.message)

Test
====

.. code-block:: sh

   uv run basedpyright
   uv run python -m unittest
   uv run coverage run -m unittest
   uv run coverage report -m

The tests cover raw low-level evaluation, strict ``SExp`` evaluation, Simple API
shorthand evaluation, package/function lookup, macros and special forms,
cons/list conversion, higher-order Lisp functions, reference lifecycle, result
parsing, missing runtime errors, Lisp-side exceptions, and internal runtime
error paths. Coverage is configured to fail below 100% for the Python package.

Source Builds and Wheels
========================

Build the WASM Runtime
----------------------

The wheel includes ``eclpy/ecl_eval.wasm``, but that file must be generated before
building a distribution. The ECL source is vendored in ``vendor/ecl-26.5.5``; no
source tarball is required. Local ECL build patches are kept under ``patch/`` and
applied only to copied source trees under ``build/``.

.. code-block:: sh

   uv run python scripts/build_ecl_wasm.py

The script:

* builds a native host ECL used for cross-compilation;
* builds the vendored ECL source for ``wasm32-unknown-emscripten``;
* links ``native/eclpy_eval.c`` into ``build/eclpy/ecl_eval.wasm``;
* copies the runtime to ``eclpy/ecl_eval.wasm`` for wheel packaging;
* runs a Python smoke test through ``EclSession()``.

Build a Wheel
-------------

After generating ``eclpy/ecl_eval.wasm``:

.. code-block:: sh

   uv build --wheel --out-dir dist --clear

The wheel should contain:

.. code-block:: text

   eclpy/__init__.py
   eclpy/api.py
   eclpy/decode.py
   eclpy/encode.py
   eclpy/session.py
   eclpy/objects.py
   eclpy/reader.py
   eclpy/runtime_lisp.py
   eclpy/sexp.py
   eclpy/ecl_eval.wasm

You can smoke-test the built wheel outside the source tree:

.. code-block:: sh

   uv run --no-project --isolated \
     --with dist/eclpy-0.1.0-py3-none-any.whl \
     python -c 'import eclpy; import eclpy.simple as L; print(eclpy.Lisp().eval(L.expr(("+", 10, 32))))'

Runtime Notes
=============

The current Emscripten build emits a core wasm module that imports
``wasi_snapshot_preview1`` plus Emscripten ``env`` functions. The Python loader
provides the minimal Emscripten compatibility shims needed by this runtime.
The build disables ECL's runtime stack-size probing on Emscripten, avoiding the
unsupported ``prlimit64`` startup syscall path.

WASI 0.3 requires a component-model toolchain/runtime path. The local
Emscripten 6.0.1 and ``wasmtime`` Python package used here do not expose that
path, so this package hosts the Preview 1 module directly.

License
=======

This project uses the same license terms as ECL: GNU LGPL 2.1 or later. See
``LICENSE`` for ECL's copyright notice and ``COPYING`` for the LGPL 2.1 text.
