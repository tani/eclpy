=====
eclpy
=====

Common Lisp for Python, powered by Embeddable Common Lisp (ECL) running as a
packaged WebAssembly runtime.

``eclpy`` exposes a cl4py-inspired ``Lisp`` API for evaluating Lisp forms,
calling functions, accessing packages, and converting common Lisp values to
Python values. Normal users can start with ``eclpy.syntax``; heavier users can
drop down to explicit ``SExp`` trees or the raw ``EclSession`` bridge.

The bridge is bidirectional: Lisp code can also evaluate Python through
``ecl-python:py-eval`` / ``py-exec``. Values cross the boundary as a single
object-shaped JSON protocol.

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

For everyday use, import ``eclpy.syntax`` as a small expression builder:

.. code-block:: python

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

``L.expr`` takes one Python value. Use ``L.expr(("+", 1, 1))``, not
``L.expr("+", 1, 1)``.

Command-Line Interface
----------------------

``eclpy`` ships a command-line REPL that starts in the ``eclpy-user`` package,
which already uses ``ecl-python`` and ``cl``, so ``py-eval`` and ``py-exec``
are available without a package prefix:

.. code-block:: sh

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

The prompt always shows the current package name. Multi-line forms are
supported; input continues until all parentheses are closed. Readline
history is saved to ``~/.eclpy_history`` when the session ends.

To evaluate a single expression and exit:

.. code-block:: sh

   eclpy -e "(+ 1 2)"

To run a Lisp source file:

.. code-block:: sh

   eclpy path/to/script.lisp

To start a SWANK/SLIME server instead of the REPL (see *SWANK/SLIME* below):

.. code-block:: sh

   eclpy --swank            # port 4005
   eclpy --swank 4006       # explicit port

Use Pythonic Proxies
--------------------

``find_package`` returns a package proxy. Attributes are converted to Common
Lisp symbol names in a cl4py-style way:

.. code-block:: python

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

Proxy function arguments use the same conversion rules as ``L.expr``. A Python
string means a Lisp symbol, so use ``L.string("...")`` when you need a Lisp
string value.

Strings, Symbols, Lists
-----------------------

Strings and symbols stay distinct on both sides of the bridge. ``List`` models
proper Lisp lists, and ``Cons`` models dotted cons cells:

.. code-block:: python

   import eclpy
   import eclpy.syntax as L

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
       EclJSONEncoder,
       EclSession,
       Lisp,
       List,
       Package,
       Reference,
       SExp,
       Symbol,
   )

``Package`` is the package proxy returned by ``eclpy.proxy.find_package``.
``Reference`` is a scoped handle for Lisp objects that cannot be copied directly
into Python.

The modules are split by layer:

.. code-block:: text

   eclpy/lisp.py        # high-level Lisp facade and reference lifecycle
   eclpy/proxy.py       # Pythonic package proxy
   eclpy/syntax.py      # fluent SExp/literal builders
   eclpy/sexp.py        # safe Lisp source rendering
   eclpy/encode.py      # Python values -> Lisp source expressions
   eclpy/protocol.py    # object-shaped JSON value protocol
   eclpy/session.py     # low-level Wasmtime/ECL session
   eclpy/hostenv.py     # WASM env imports: files, stat, Python eval/exec
   eclpy/wasmmem.py     # WASM linear-memory helpers
   eclpy/runtime.lisp   # Lisp-side helper package source
   eclpy/swank/         # bundled upstream SWANK server source

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
   import eclpy.syntax as L
   from eclpy.proxy import find_package

   with eclpy.Lisp() as lisp:
       cl = find_package(lisp, "CL")
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

Evaluate Python from Lisp
-------------------------

Every high-level ``Lisp`` session loads the ``ecl-python`` helper package. Lisp
code can evaluate Python expressions with ``ecl-python:py-eval`` and execute
Python statements with ``ecl-python:py-exec``:

.. code-block:: python

   import eclpy

   with eclpy.Lisp() as lisp:
       assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-eval "1 + 2")')) == 3
       assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-exec "x = 5")')) == eclpy.List()
       assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-eval "x * 2")')) == 10
       assert lisp.eval(eclpy.SExp.raw('(ecl-python:py-eval "[1, \\"x\\"]")')) == eclpy.List(1, "x")

``py-eval`` accepts Python expressions and returns their value. ``py-exec``
accepts Python statements and returns ``NIL``. The Python globals are scoped to
the owning ``EclSession`` and persist for the session lifetime.

This is a full-power host Python evaluation hook, not a sandbox. Only evaluate
trusted code.

ASDF
----

``(require 'asdf)`` works out of the box. The wheel bundles ECL's ASDF source
(``eclpy/asdf.lisp``), and every ``Lisp`` session registers a ``cl:require``
provider that loads it through the WASM file bridge. The first ``require`` loads
ASDF; later ``require`` calls in the same Lisp session are no-ops:

.. code-block:: python

   import eclpy
   import eclpy.syntax as L
   from eclpy.proxy import find_package

   with eclpy.Lisp() as lisp:
       cl = find_package(lisp, "CL")
       cl.require(L.quote("asdf"))

       asdf = find_package(lisp, "ASDF")
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
   import eclpy.syntax as L
   from eclpy.proxy import find_package

   project = "/path/to/demo/"

   with eclpy.Lisp() as lisp:
       cl = find_package(lisp, "CL")
       cl.require(L.quote("asdf"))
       asdf = find_package(lisp, "ASDF")

       cl.push(L.path(project), asdf.symbol("*central-registry*"))
       asdf.load_system(L.string("demo"))

       demo = find_package(lisp, "DEMO")
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

SWANK/SLIME
-----------

``eclpy`` bundles the unmodified upstream SWANK server source (from the SLIME
project) so Emacs can connect to a running ``Lisp`` session as a normal SLIME
REPL. Start the server with ``Lisp.start_swank``, which blocks the calling
thread for as long as it serves requests, so run it from a background thread:

.. code-block:: python

   import threading

   import eclpy

   lisp = eclpy.Lisp()
   thread = threading.Thread(target=lisp.start_swank, kwargs={"port": 4005})
   thread.daemon = True
   thread.start()

Then, in Emacs: ``M-x slime-connect RET 127.0.0.1 RET 4005 RET``.

From the command line, ``eclpy --swank`` (or ``eclpy --swank PORT``) starts
the server directly instead of the REPL, printing the port and blocking
until interrupted with Ctrl-C.

``start_swank`` bypasses the JSON evaluation protocol used by ``Lisp.eval``
and calls the session directly, so unhandled conditions raised while
evaluating a SWANK request reach ECL's native condition system instead of
being caught and reported back as an ``EclError``.

Limitations in the WASM Sandbox
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **No native compilation.** The sandbox has no C compiler and cannot
  ``dlopen`` shared objects, so ``compile-string-for-emacs`` and
  ``compile-file-for-emacs`` evaluate source directly instead of compiling to
  a FASL. Compiler errors and warnings are still reported to Emacs as
  compiler notes.
* **Interactive debugger (SLDB) works, with one patched primitive.** Walking
  ECL's raw interpreter history/frame stacks for a backtrace
  (``SI::IHS-TOP`` / ``SI::FRS-TOP``) works under this build, except reading
  interpreter-history-stack frame 0's environment, which is a sentinel with
  no real frame and hard-traps the WASM instance (not a catchable Lisp
  condition) rather than erroring cleanly as on native platforms.
  ``start_swank`` patches ``call-with-debugging-environment`` to skip that
  one index; the rest of SLDB (backtraces, restarts, frame locals,
  ``eval-in-frame``) runs unmodified. Unhandled errors during a SWANK
  request open a normal interactive debugger buffer in Emacs.
* **Single-threaded.** This ECL WASM build has no real threads
  (``:threads`` is absent from ``*features*``), so the server always runs
  with ``:communication-style nil``: one blocking, synchronous request loop
  per connection, exactly like a native single-threaded Lisp bound to Emacs.

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

Embed Lisp Values in JSON
-------------------------

``EclJSONEncoder`` is a ``json.JSONEncoder`` for ordinary JSON documents that
happen to carry eclpy Lisp values -- ``Symbol``, ``Cons``, ``Reference``, or
``fractions.Fraction`` -- mixed in with plain Python data, at any nesting
depth. Pass it as ``cls=`` to the standard library's ``json.dumps``/``json.dump``:

.. code-block:: python

   import json
   from fractions import Fraction

   from eclpy import EclJSONEncoder, Symbol

   doc = {"name": "example", "count": 3, "symbol": Symbol("CAR", "COMMON-LISP"),
          "ratio": Fraction(1, 3)}
   print(json.dumps(doc, cls=EclJSONEncoder))
   # {"name": "example", "count": 3,
   #  "symbol": {"type": "symbol", "name": "CAR", "package": "COMMON-LISP"},
   #  "ratio": {"type": "ratio", "numerator": "1", "denominator": "3"}}

Plain JSON-native values (``str``, ``int``, ``float``, ``bool``, ``None``,
``list``, ``dict``) encode exactly as ``json.JSONEncoder`` normally would --
only the embedded Lisp values render through ``protocol.to_protocol``. This is
unrelated to the WASM wire protocol: it does not wrap the whole document in a
protocol envelope, and it is never used internally by ``Lisp.eval`` or
``EclSession``.

Security Model
--------------

``eclpy`` is designed for trusted local code. It is not a sandbox for
untrusted Lisp or Python:

* ``SExp.raw`` intentionally embeds raw Lisp source.
* ``ecl-python:py-eval`` and ``py-exec`` run in the host Python interpreter with
  normal Python privileges.
* The ASDF/file bridge lets Lisp inspect and load ordinary host paths needed by
  the current process.

Do not expose these APIs directly to untrusted input without an application-level
sandbox or allow-list.

For Developers
==============

Architecture
------------

The runtime is layered so that each boundary has one responsibility:

.. code-block:: text

   Python process
   ├─ High-level API
   │  ├─ lisp.py      Lisp facade, context manager, Reference lifecycle
   │  ├─ proxy.py     package and callable-symbol proxies
   │  └─ syntax.py    L.expr / L.quote / L.array helpers
   ├─ Value and syntax
   │  ├─ sexp.py      SExp tree -> safe Lisp source text
   │  ├─ encode.py    Python values -> Lisp source expressions (call args)
   │  ├─ protocol.py  object-shaped JSON protocol encode/decode
   │  └─ objects.py   Symbol / List / Cons / Reference
   ├─ Low-level host
   │  ├─ session.py   Wasmtime lifecycle, eval, eval_json
   │  ├─ hostenv.py   env imports: host files, stat, Python eval/exec
   │  └─ wasmmem.py   linear-memory helpers and WASI errno
   └─ WASM boundary
      └─ native/eclpy_eval.c  ECL boot, C ABI, string shuttling

   ECL inside WebAssembly
   └─ eclpy/runtime.lisp      ecl-python helper package
      ├─ evaluate / serialize / deserialize
      ├─ json-encode / json-decode
      ├─ py-eval / py-exec
      └─ ASDF file bridge shims

Value Protocol
--------------

Both directions use the same object-shaped JSON protocol. The Python side owns
``protocol.py``; the Lisp side owns ``serialize`` / ``deserialize`` in
``runtime.lisp``. The C layer does not parse JSON and does not interpret value
fields; it only moves strings across the WASM boundary and calls Lisp helper
functions.

.. code-block:: text

   Python value
     -> protocol.to_protocol / json.dumps
     -> JSON text
     -> C string shuttle
     -> ecl-python:json-decode
     -> ecl-python:deserialize
     -> Lisp value

   Lisp value
     -> ecl-python:serialize
     -> ecl-python:json-encode
     -> C string shuttle
     -> json.loads / protocol.decode_value
     -> Python value

The wire shape is a JSON object with named fields. Every top-level Lisp result
is a protocol envelope:

.. code-block:: text

   {"protocol": "eclpy", "version": 1, "status": "ok", "value": {...}}
   {"protocol": "eclpy", "version": 1, "status": "error",
    "condition_type": "SIMPLE-ERROR", "message": "boom"}

Value nodes use a ``type`` field plus named payload fields:

.. code-block:: text

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
   {"type": "vector", "items": [...]}
   {"type": "package", "name": "COMMON-LISP"}
   {"type": "ref", "id": 7, "kind": "FUNCTION"}

Package lookup uses the same protocol/version envelope and returns exactly one
of ``missing``, ``callable``, ``value``, or ``symbol``:

.. code-block:: text

   {"protocol": "eclpy", "version": 1, "kind": "missing"}
   {"protocol": "eclpy", "version": 1, "kind": "callable",
    "callable_type": "function", "name": "+", "package": "COMMON-LISP"}
   {"protocol": "eclpy", "version": 1, "kind": "value", "value": {...}}
   {"protocol": "eclpy", "version": 1, "kind": "symbol",
    "name": "FOO", "package": null}

Test
----

.. code-block:: sh

   uv run ruff check .
   uv run basedpyright
   uv run python -m unittest discover -s tests
   uv run coverage run -m unittest discover -s tests
   uv run coverage report -m

The tests cover raw low-level evaluation, strict ``SExp`` evaluation, Syntax API
shorthand evaluation, package lookup, macros and special forms, bidirectional
Python/Lisp evaluation, object-shaped JSON protocol conversion, cons/list conversion,
higher-order Lisp functions, reference lifecycle, ``(require 'asdf)`` module
loading, missing runtime errors, Lisp-side exceptions, internal runtime
error paths, and the SWANK-RPC wire protocol served by ``Lisp.start_swank``.
Coverage is configured to fail below 100% for the Python package.

Build the WASM Runtime
----------------------

The wheel includes ``eclpy/ecl_eval.wasm``, but that file must be generated before
building a distribution. The ECL source is vendored in ``vendor/ecl-26.5.5`` and
the SWANK server source is vendored in ``vendor/slime``; no source tarball or
network fetch is required to build the wheel itself. Local ECL build patches
are kept under ``patch/`` and applied only to copied source trees under
``build/``.

.. code-block:: sh

   uv run python scripts/build_ecl_wasm.py

The script:

* builds a native host ECL used for cross-compilation;
* builds the vendored ECL source for ``wasm32-unknown-emscripten``, lowering
  setjmp/longjmp to native WebAssembly exception handling;
* links ``native/eclpy_eval.c`` into ``build/eclpy/ecl_eval.wasm``;
* copies the runtime to ``eclpy/ecl_eval.wasm`` for wheel packaging;
* copies the vendored ASDF source to ``eclpy/asdf.lisp``;
* copies the vendored SWANK source files from ``vendor/slime`` to ``eclpy/swank/*.lisp``;
* runs a Python smoke test through ``EclSession()``.

Build a Wheel
-------------

After generating ``eclpy/ecl_eval.wasm``:

.. code-block:: sh

   uv build --wheel --out-dir dist --clear

The wheel should contain:

.. code-block:: text

   eclpy/__init__.py
   eclpy/lisp.py
   eclpy/syntax.py
   eclpy/proxy.py
   eclpy/protocol.py
   eclpy/encode.py
   eclpy/session.py
   eclpy/hostenv.py
   eclpy/wasmmem.py
   eclpy/objects.py
   eclpy/runtime_lisp.py
   eclpy/runtime.lisp
   eclpy/sexp.py
   eclpy/ecl_eval.wasm
   eclpy/asdf.lisp
   eclpy/swank/loader.lisp
   eclpy/swank/packages.lisp
   eclpy/swank/backend.lisp
   eclpy/swank/ecl.lisp
   eclpy/swank/gray.lisp
   eclpy/swank/match.lisp
   eclpy/swank/rpc.lisp
   eclpy/swank/swank-core.lisp
   eclpy/swank/swank-repl.lisp

You can smoke-test the built wheel outside the source tree:

.. code-block:: sh

   uv run --no-project --isolated \
     --with dist/eclpy-0.1.0-py3-none-any.whl \
     python -c 'import eclpy; import eclpy.syntax as L; print(eclpy.Lisp().eval(L.expr(("+", 10, 32))))'

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
