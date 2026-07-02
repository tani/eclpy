;;;; Minimal replacement for the real swank-loader.lisp.
;;;;
;;;; The real swank-loader.lisp compiles each source file to a FASL before
;;;; loading it. Native compilation is impossible in the eclpy WASM sandbox
;;;; (no C compiler, no dlopen), so this shim loads the bundled SWANK source
;;;; files directly, uncompiled, exactly like ecl-python::provide-asdf does
;;;; for ASDF. It supplies only the symbols the vendored swank/*.lisp files
;;;; actually reference from swank-loader: DEFINE-PACKAGE (a warning-muffled
;;;; DEFPACKAGE), *STARTED-FROM-EMACS*, and *SOURCE-DIRECTORY*.

(defpackage #:swank-loader
  (:use #:cl)
  (:export #:*source-directory* #:*started-from-emacs* #:define-package))

(in-package #:swank-loader)

(defvar *started-from-emacs* nil)

;; swank/ecl.lisp's IS-SWANK-SOURCE-P compares a frame's source file against
;; this to hide SWANK's own implementation frames from SLDB backtraces. Must
;; be a real pathname -- ECL's MAKE-PATHNAME/PATHNAME-MATCH-P hard-trap the
;; WASM instance on an unbound-variable condition raised while a live signal
;; is already being handled (i.e. from inside a real *DEBUGGER-HOOK* call),
;; rather than raising a catchable condition as on other platforms.
(defvar *source-directory* ecl-python:*swank-source-directory*)

(defmacro define-package (package &rest options)
  "Like CL:DEFPACKAGE, but does not signal on redefinition variance."
  `(handler-bind ((warning #'muffle-warning))
     (cl:defpackage ,package ,@options)))
