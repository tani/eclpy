=====
eclpy
=====

Common Lisp for Python, powered by Embeddable Common Lisp (ECL) running as a
packaged WebAssembly runtime.

``eclpy`` exposes a cl4py-inspired ``Lisp`` API for evaluating Lisp forms,
calling functions, accessing packages, and converting common Lisp values to
Python values. Normal users can start with ``eclpy.simple``; heavier users can
drop down to explicit ``SExp`` trees or the raw ``EclSession`` bridge.

.. contents::
   :local:
   :depth: 2

For Beginners
=============

Install
-------

After the package is published to PyPI:

.. code-block:: sh

   pip install eclpy

The wheel is expected to include the ECL WebAssembly runtime, so normal users do
not need a local ECL installation or an Emscripten toolchain.

Quick Start
-----------

For everyday use, import ``eclpy.simple`` as a small expression builder:

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

``L.expr`` takes one Python value. Use ``L.expr(("+", 1, 1))``, not
``L.expr("+", 1, 1)``.

Call Lisp Functions
-------------------

``find_function`` returns a callable proxy for a Lisp function:

.. code-block:: python

   import eclpy
   import eclpy.simple as L

   with eclpy.Lisp() as lisp:
       add = L.find_function(lisp, "+")
       assert add(1, 2, 3, 4) == 10
       assert lisp.eval(L.expr([add, 1, 2])) == 3

Use Lisp Packages
-----------------

``find_package`` returns a package proxy. Attributes are converted to Common
Lisp symbol names in a cl4py-style way:

.. code-block:: python

   import eclpy
   import eclpy.simple as L

   with eclpy.Lisp() as lisp:
       cl = L.find_package(lisp, "CL")
       assert lisp.eval(L.expr(["package-name", cl])) == "COMMON-LISP"
       assert cl.oddp(5) is True
       assert cl.add(2, 3, 4, 5) == 14        # +
       assert cl.stringgt(L.string("baz"), L.string("bar")) == 2  # STRING>
       assert cl.print_base == 10             # *PRINT-BASE*

Proxy function arguments use the same conversion rules as ``L.expr``. A Python
string means a Lisp symbol, so use ``L.string("...")`` when you need a Lisp
string value.

Strings, Symbols, Lists
-----------------------

Strings and symbols stay distinct on both sides of the bridge. ``List`` models
proper Lisp lists, and ``Cons`` models dotted cons cells:

.. code-block:: python

   import eclpy
   import eclpy.simple as L

   with eclpy.Lisp() as lisp:
       assert lisp.eval(L.expr(("STRING=", L.string("foo"), L.string("foo")))) is True
       assert lisp.eval(eclpy.SExp.raw("'CL:CAR")) == eclpy.Symbol("CAR", "COMMON-LISP")
       assert lisp.eval(L.expr(("list", 1, 2, 3))) == eclpy.List(1, 2, 3)
       assert lisp.eval(L.expr(("length", L.array([1, 2, 3])))) == 3
       assert lisp.eval(L.expr(("array-dimensions", L.array([[1, 2], [3, 4]])))) == eclpy.List(2, 2)

Public API
----------

``eclpy`` exports these public Python objects:

.. code-block:: python

   from eclpy import (
       Cons,
       EclError,
       EclSession,
       Function,
       Lisp,
       List,
       Package,
       Reference,
       SExp,
       Symbol,
   )

``Function`` and ``Package`` are the callable and package proxies returned by
``eclpy.simple.find_function`` and ``eclpy.simple.find_package``. ``Reference``
is a scoped handle for Lisp objects that cannot be copied directly into Python.

For Heavy Users
===============

Strict ``SExp`` Evaluation
--------------------------

``Lisp.eval`` accepts explicit ``eclpy.SExp`` values only. It does not accept
Python values, tuples, ``Symbol`` objects, or source strings directly. The
syntax tree is rendered to Lisp source only at the WASM boundary.

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

Use ``eclpy.SExp.raw(...)`` when you intentionally need raw Lisp source:

.. code-block:: python

   import eclpy

   with eclpy.Lisp() as lisp:
       assert lisp.eval(eclpy.SExp.raw("(+ 1 2)")) == 3
       assert lisp.eval(eclpy.SExp.raw("(+ 1 2) (+ 3 4)")) == 7

References
----------

Some Lisp values are returned as ``Reference`` handles. Scope them with
``with`` or release them by closing the owning ``Lisp`` session:

.. code-block:: python

   import eclpy
   import eclpy.simple as L

   with eclpy.Lisp() as lisp:
       cl = L.find_package(lisp, "CL")
       with cl.constantly(4) as fn:
           assert cl.mapcar(fn, (1, 2, 3)) == eclpy.List(4, 4, 4)

Conses and Proper Lists
-----------------------

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

ASDF
----

``(require 'asdf)`` works out of the box. The wheel bundles ECL's ASDF source
(``eclpy/asdf.lisp``), and every ``Lisp`` session registers a ``cl:require``
provider that loads it through the WASM file bridge. The first ``require`` loads
ASDF; later ``require`` calls in the same Lisp session are no-ops:

.. code-block:: python

   import eclpy
   import eclpy.simple as L

   with eclpy.Lisp() as lisp:
       cl = L.find_package(lisp, "CL")
       cl.require(L.quote("asdf"))

       asdf = L.find_package(lisp, "ASDF")
       version = asdf.asdf_version()
       print(version)

       assert cl.require(L.quote("asdf")) == eclpy.List()

ASDF is loaded from source with the ordinary ``load`` path, which is fast
enough thanks to native WebAssembly exception handling.

Load a Local ASDF System
~~~~~~~~~~~~~~~~~~~~~~~~

To load a local source project, create or point at a directory containing an
``.asd`` file, push that directory into ``asdf:*central-registry*``, then call
``asdf:load-system``:

.. code-block:: python

   import eclpy
   import eclpy.simple as L

   project = "/path/to/demo/"

   with eclpy.Lisp() as lisp:
       cl = L.find_package(lisp, "CL")
       cl.require(L.quote("asdf"))
       asdf = L.find_package(lisp, "ASDF")

       cl.push(L.path(project), asdf.symbol("*central-registry*"))
       asdf.load_system(L.string("demo"))

       demo = L.find_package(lisp, "DEMO")
       assert demo.add(20, 22) == 42

For that example, the host directory could look like this:

.. code-block:: text

   /path/to/demo/
     demo.asd
     pkg.lisp
     math.lisp

.. code-block:: lisp

   ;; demo.asd
   (defsystem "demo"
     :serial t
     :components ((:file "pkg") (:file "math")))

   ;; pkg.lisp
   (defpackage :demo
     (:use :cl)
     (:export :add))

   ;; math.lisp
   (in-package :demo)
   (defun add (a b) (+ a b))

Use a trailing slash when pushing a project directory into
``asdf:*central-registry*``. The examples above build ``#p"/path/to/demo/"``,
not ``#p"/path/to/demo"``.

Host Files and Limits
~~~~~~~~~~~~~~~~~~~~~

ASDF sees host files through eclpy's WASM host bridge. ``load``,
``probe-file``, ``truename``, and ``file-write-date`` can inspect ordinary host
paths, which is enough for ASDF source projects.

This does not turn the WASM runtime into a full native process environment.
Shelling out, compiling foreign code, or relying on implementation-specific
native build steps is out of scope. Prefer source-only systems whose components
can be loaded directly by ECL inside the packaged WASM runtime.

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

For Developers
==============

Test
----

.. code-block:: sh

   uv run ruff check .
   uv run basedpyright
   uv run python -m unittest discover -s tests
   uv run coverage run -m unittest discover -s tests
   uv run coverage report -m

The tests cover raw low-level evaluation, strict ``SExp`` evaluation, Simple API
shorthand evaluation, package/function lookup, macros and special forms,
cons/list conversion, higher-order Lisp functions, reference lifecycle, result
parsing, ``(require 'asdf)`` module loading, missing runtime errors, Lisp-side
exceptions, and internal runtime error paths. Coverage is configured to fail
below 100% for the Python package.

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
* builds the vendored ECL source for ``wasm32-unknown-emscripten``, lowering
  setjmp/longjmp to native WebAssembly exception handling;
* links ``native/eclpy_eval.c`` into ``build/eclpy/ecl_eval.wasm``;
* copies the runtime to ``eclpy/ecl_eval.wasm`` for wheel packaging;
* copies the vendored ASDF source to ``eclpy/asdf.lisp``;
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
   eclpy/asdf.lisp

You can smoke-test the built wheel outside the source tree:

.. code-block:: sh

   uv run --no-project --isolated \
     --with dist/eclpy-0.1.0-py3-none-any.whl \
     python -c 'import eclpy; import eclpy.simple as L; print(eclpy.Lisp().eval(L.expr(("+", 10, 32))))'

Runtime Notes
-------------

The current Emscripten build emits a core wasm module that imports
``wasi_snapshot_preview1`` plus Emscripten ``env`` functions. The Python loader
provides the minimal Emscripten compatibility shims needed by this runtime.
The build disables ECL's runtime stack-size probing on Emscripten, avoiding the
unsupported ``prlimit64`` startup syscall path.

ECL relies heavily on setjmp/longjmp for its condition system and binding
stack. The build lowers these to native WebAssembly exception handling
(``-sSUPPORT_LONGJMP=wasm -sWASM_LEGACY_EXCEPTIONS=0``) rather than Emscripten's
JavaScript ``invoke_*`` trampolines, which would otherwise cross the wasm/host
boundary on nearly every Lisp call. The loader enables the matching wasmtime
features (exceptions, function references, GC). This is what makes loading large
libraries such as ASDF practical.

WASI 0.3 requires a component-model toolchain/runtime path. The local
Emscripten 6.0.1 and ``wasmtime`` Python package used here do not expose that
path, so this package hosts the Preview 1 module directly.

License
=======

Copyright (c) 2026 Masaya Taniguchi <masaya.taniguchi@tani.cc>.

This project uses the same license terms as ECL: GNU LGPL 2.1 or later. See
``LICENSE`` for eclpy and ECL copyright notices and ``COPYING`` for the LGPL
2.1 text.
