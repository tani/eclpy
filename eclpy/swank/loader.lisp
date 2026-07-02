;;;; Minimal replacement for the real swank-loader.lisp.
;;;;
;;;; The real swank-loader.lisp compiles each source file to a FASL before
;;;; loading it. Native compilation is impossible in the eclpy WASM sandbox
;;;; (no C compiler, no dlopen), so this shim loads the bundled SWANK source
;;;; files directly, uncompiled, exactly like ecl-python::provide-asdf does
;;;; for ASDF. It supplies only the symbols the vendored swank/*.lisp files
;;;; actually reference from swank-loader: DEFINE-PACKAGE (a warning-muffled
;;;; DEFPACKAGE) and *STARTED-FROM-EMACS*.

(defpackage #:swank-loader
  (:use #:cl)
  (:export #:*started-from-emacs* #:define-package))

(in-package #:swank-loader)

(defvar *started-from-emacs* nil)

(defmacro define-package (package &rest options)
  "Like CL:DEFPACKAGE, but does not signal on redefinition variance."
  `(handler-bind ((warning #'muffle-warning))
     (cl:defpackage ,package ,@options)))
