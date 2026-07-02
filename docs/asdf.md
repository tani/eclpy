# ASDF

`(require 'asdf)` works out of the box. The wheel bundles ECL's ASDF source
(`eclpy/asdf.lisp`), and every `Lisp` session registers a `cl:require`
provider that loads it through the WASM file bridge. The first `require`
loads ASDF; later `require` calls in the same Lisp session are no-ops:

```python
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
```

ASDF is loaded from source with the ordinary `load` path, which is fast
enough thanks to native WebAssembly exception handling.

## Load a Local ASDF System

To load a local source project, create or point at a directory containing
an `.asd` file, push that directory into `asdf:*central-registry*`, then
call `asdf:load-system`:

```python
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
```

For that example, the host directory could look like this:

```text
/path/to/demo/
  demo.asd
  pkg.lisp
  math.lisp
```

```lisp
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
```

Use a trailing slash when pushing a project directory into
`asdf:*central-registry*`. The examples above build `#p"/path/to/demo/"`,
not `#p"/path/to/demo"`.

## Host Files and Limits

ASDF sees host files through eclpy's WASM host bridge. `load`,
`probe-file`, `truename`, and `file-write-date` can inspect ordinary host
paths, which is enough for ASDF source projects.

This does not turn the WASM runtime into a full native process
environment. Shelling out, compiling foreign code, or relying on
implementation-specific native build steps is out of scope. Prefer
source-only systems whose components can be loaded directly by ECL inside
the packaged WASM runtime.
