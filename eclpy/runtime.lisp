(defpackage #:ecl-python
  (:use #:cl)
  (:shadow #:load)
  (:export #:evaluate #:lookup-symbol #:release-object #:release-all-objects
           #:json-encode #:native-load #:py-eval #:py-exec #:serialize #:value
           #:*asdf-source* #:*swank-source-directory* #:start-swank))

(in-package #:ecl-python)

(defvar *objects* (make-hash-table :test #'eql))
(defvar *next-id* 0)

(defvar *asdf-source* nil
  "Host pathname of the bundled ASDF source, set from Python, or NIL.")

(defvar *swank-source-directory* nil
  "Host pathname of the bundled SWANK source directory, set from Python, or NIL.")

(defun store-object (value)
  (let ((id (incf *next-id*)))
    (setf (gethash id *objects*) value)
    id))

(defun value (id)
  (multiple-value-bind (value present) (gethash id *objects*)
    (unless present
      (error "No Python-visible ECL object with id ~A" id))
    value))

(defun release-object (id)
  (remhash id *objects*)
  (list :released id))

(defun release-all-objects ()
  (clrhash *objects*)
  (setf *next-id* 0)
  '(:released-all))

(defun py-eval (source)
  (eval (read-from-string (%py-eval (string source)))))

(defun py-exec (source)
  (eval (read-from-string (%py-exec (string source)))))

(defun condition-type-name (condition)
  (prin1-to-string (type-of condition)))

(defun condition-message (condition)
  (format nil "~A" condition))

(defun load (source &key
                    (verbose *load-verbose*)
                    (print *load-print*)
                    (if-does-not-exist :error)
                    (external-format :default)
                    &allow-other-keys)
  (let* ((pathname (merge-pathnames source))
         (*package* *package*)
         (*readtable* *readtable*)
         (*load-pathname* pathname)
         (*load-truename* pathname))
    (native-load pathname verbose print if-does-not-exist external-format)))

;; The WASM sandbox has no real filesystem; ECL's open/stat resolve to an empty
;; in-memory FS. Route file existence, resolution, and timestamps through the
;; host bridge (host-stat) so that probe-file, truename, and file-write-date see
;; real host files. ASDF's find-system, in particular, only loads a system
;; definition when file-write-date returns a usable stamp.
(defun host-probe-file (pathspec &rest ignored)
  (declare (ignore ignored))
  (let ((pathname (merge-pathnames pathspec)))
    (and (host-stat pathname) pathname)))

(defun host-truename (pathspec &rest ignored)
  (declare (ignore ignored))
  (let ((pathname (merge-pathnames pathspec)))
    (if (host-stat pathname)
        pathname
        (error 'file-error :pathname pathname))))

(defun host-file-write-date (pathspec &rest ignored)
  (declare (ignore ignored))
  (let ((info (host-stat (merge-pathnames pathspec))))
    (if info
        (cdr info)
        (error 'file-error :pathname pathspec))))

;; SWANK needs real (read-only) file access: SLIME-SECRET probes and reads
;; ~/.slime-secret, and SWANK-COMPILE-FILE opens a buffer's source file for
;; recompilation. Neither writes, lists directories, or renames anything, so
;; only :INPUT/:PROBE OPEN and USER-HOMEDIR-PATHNAME need a host-backed
;; implementation; ECL's native versions resolve against an empty in-memory
;; FS in the WASM sandbox and hard-trap instead of signalling a condition.
(defun host-read-bytes (pathname)
  (%host-read-bytes pathname))

(defun host-home-directory ()
  (%host-home-directory))

(defclass host-character-input-stream (gray:fundamental-character-input-stream)
  ((pathname :initarg :pathname :reader host-stream-pathname)
   (buffer :initarg :buffer :reader host-stream-buffer)
   (cursor :initform 0 :accessor host-stream-cursor))
  (:documentation "A host-backed Gray input stream over an in-memory text buffer."))

(defmethod gray:stream-element-type ((stream host-character-input-stream))
  'character)

(defmethod gray:stream-read-char ((stream host-character-input-stream))
  (with-slots (buffer cursor) stream
    (if (< cursor (length buffer))
        (prog1 (char buffer cursor) (incf cursor))
        :eof)))

(defmethod gray:stream-unread-char ((stream host-character-input-stream) character)
  (declare (ignore character))
  (when (> (host-stream-cursor stream) 0)
    (decf (host-stream-cursor stream)))
  nil)

(defun open-with-host-streams (filespec &rest options
                               &key (direction :input)
                                    (if-does-not-exist nil if-does-not-exist-p)
                               &allow-other-keys)
  (declare (ignore options))
  (unless (member direction '(:input :probe))
    (error "Host-backed OPEN only supports :INPUT and :PROBE, not ~S." direction))
  (let ((pathname (merge-pathnames filespec)))
    (unless if-does-not-exist-p
      (setf if-does-not-exist (if (eq direction :probe) nil :error)))
    (cond
      ((host-stat pathname)
       (make-instance 'host-character-input-stream :pathname pathname
                     :buffer (if (eq direction :probe) "" (host-read-bytes pathname))))
      ((null if-does-not-exist) nil)
      (t (error 'file-error :pathname pathname)))))

(defun host-user-homedir-pathname (&optional host)
  (declare (ignore host))
  (parse-namestring (host-home-directory)))

(defun bytes-to-byte-vector (bytes)
  "Convert a base-char byte-string to an (unsigned-byte 8) vector."
  (let* ((len (length bytes))
         (result (make-array len :element-type '(unsigned-byte 8)
                             :adjustable t :fill-pointer len)))
    (dotimes (index len result)
      (setf (aref result index) (char-code (char bytes index))))))

(defun buffer-to-bytes (buffer)
  "Convert a base-char or (unsigned-byte 8) fill-pointer buffer to a byte-string."
  (let* ((len (length buffer))
         (result (make-string len)))
    (dotimes (index len result)
      (let ((element (aref buffer index)))
        (setf (char result index) (if (characterp element) element (code-char element)))))))


(let ((previous-lock (si::package-lock "CL" nil)))
  (unwind-protect
       (progn
         (setf (symbol-function 'cl:load) #'load)
         (setf (symbol-function 'cl:probe-file) #'host-probe-file)
         (setf (symbol-function 'cl:truename) #'host-truename)
         (setf (symbol-function 'cl:file-write-date) #'host-file-write-date)
         (setf (symbol-function 'cl:open) #'open-with-host-streams)
         (setf (symbol-function 'cl:user-homedir-pathname) #'host-user-homedir-pathname))
    (si::package-lock "CL" previous-lock)))

(defvar *eval-source-forms-eof* (list :eof)
  "Unique sentinel object for EVAL-SOURCE-FORMS; never EQ to any value READ
returns, unlike a fresh #:EOF token which is a distinct uninterned symbol
on every read.")

(defun eval-source-forms (text)
  "Read and evaluate each top-level form in TEXT sequentially, so an
IN-PACKAGE form takes effect for the forms that follow it. Evaluating
TEXT as a single form (e.g. inside one enclosing LAMBDA) would read the
whole tree before any IN-PACKAGE side effect could run, leaving later
symbols interned in the wrong package. *PACKAGE* and *READTABLE* are bound
dynamically so an IN-PACKAGE inside TEXT never leaks into the caller."
  (let ((*package* *package*)
        (*readtable* *readtable*))
    (with-input-from-string (stream text)
      (loop
        (let ((form (read stream nil *eval-source-forms-eof*)))
          (when (eq form *eval-source-forms-eof*) (return))
          (eval form))))))

(defun provide-asdf (module)
  "REQUIRE provider that loads the bundled ASDF source for module ASDF."
  (when (and *asdf-source* (string-equal (string module) "ASDF"))
    (load *asdf-source*)
    ;; Compilation cannot write output in the sandbox, so make LOAD-SYSTEM load
    ;; source directly instead of compiling.
    (let ((operation (find-symbol "*LOAD-SYSTEM-OPERATION*" "ASDF"))
          (source-op (find-symbol "LOAD-SOURCE-OP" "ASDF")))
      (when (and operation source-op (boundp operation))
        (set operation source-op)))
    (provide "ASDF")
    t))

(pushnew 'provide-asdf ext:*module-provider-functions*)

;;; ---------------------------------------------------------------------
;;; Real TCP sockets via the Python host bridge.
;;;
;;; WASM cannot open sockets itself; %socket-resolve/%socket-connect/
;;; %socket-send/%socket-recv/%socket-close (registered in eclpy_init)
;;; tunnel blocking IPv4 TCP through the Python host's `socket` module.
;;; SB-BSD-SOCKETS reproduces the client/server subset of the real API
;;; (get-host-by-name -> host-ent-address -> inet-socket -> socket-connect
;;; -> socket-make-stream) that swank/ecl.lisp's TCP server backend drives.
;;; ---------------------------------------------------------------------

(defpackage #:sb-bsd-sockets
  (:use #:cl)
  (:export #:get-host-by-name #:host-ent-address #:host-ent-addresses #:host-ent
           #:socket #:inet-socket #:socket-connect #:socket-make-stream #:socket-close
           #:socket-bind #:socket-listen #:socket-accept #:socket-name
           #:socket-file-descriptor #:sockopt-reuse-address #:interrupted-error))

(in-package #:sb-bsd-sockets)

(define-condition interrupted-error (error) ()
  (:documentation "Signaled when a blocking socket call is interrupted.
The eclpy host bridge's ACCEPT/RECV/CONNECT calls are Python-level
blocking calls that never raise EINTR, so this is never actually
signaled; it exists only because swank/ecl.lisp handler-cases it."))

(defclass socket ()
  ((handle :initarg :handle :initform nil :accessor socket-handle)))

(defun socket-file-descriptor (socket)
  (socket-handle socket))

(defclass host-ent ()
  ((address :initarg :address :reader host-ent-address)))

(defun host-ent-addresses (host-ent)
  (list (host-ent-address host-ent)))

(defclass inet-socket (socket)
  ((protocol :initarg :protocol :initform :tcp :reader socket-protocol)
   (socket-type :initarg :type :initform :stream :reader socket-type)
   ;; SOCKET-BIND only records the intended address; the host bridge
   ;; performs the real create+bind+listen as one atomic call in
   ;; SOCKET-LISTEN, once the backlog is known.
   (bind-host :initform nil :accessor socket-bind-host)
   (bind-port :initform nil :accessor socket-bind-port)
   (reuse-address :initarg :reuse-address :initform nil
                  :accessor sockopt-reuse-address)))

(defmethod initialize-instance :after ((socket inet-socket) &key &allow-other-keys)
  (unless (eq (socket-protocol socket) :tcp)
    (error "SB-BSD-SOCKETS: unsupported protocol ~S (only :TCP is implemented)."
           (socket-protocol socket)))
  (unless (eq (socket-type socket) :stream)
    (error "SB-BSD-SOCKETS: unsupported socket type ~S (only :STREAM is implemented)."
           (socket-type socket))))

(defun dotted-quad-to-vector (text)
  (let ((octets '())
        (start 0))
    (loop
      (let ((dot (position #\. text :start start)))
        (push (parse-integer text :start start :end dot) octets)
        (unless dot (return))
        (setf start (1+ dot))))
    (make-array 4 :element-type '(unsigned-byte 8) :initial-contents (nreverse octets))))

(defun dotted-quad-from-vector (address)
  (format nil "~D.~D.~D.~D" (aref address 0) (aref address 1) (aref address 2) (aref address 3)))

(defun address-to-dotted-quad (address)
  (if (stringp address) address (dotted-quad-from-vector address)))

(defun get-host-by-name (host)
  (make-instance 'host-ent
                 :address (dotted-quad-to-vector
                           (ecl-python:%socket-resolve (string host)))))

(defun socket-connect (socket address port)
  (setf (socket-handle socket)
        (ecl-python:%socket-connect (address-to-dotted-quad address) port))
  socket)

(defun socket-bind (socket address &optional port)
  "Record the address/port SOCKET will listen on; SOCKET-LISTEN performs
the real bind, since the host bridge creates, binds, and listens on the
underlying socket as one atomic call."
  (setf (socket-bind-host socket) (address-to-dotted-quad address))
  (setf (socket-bind-port socket) (or port 0))
  t)

(defun socket-listen (socket backlog)
  (let ((result (ecl-python:%socket-listen (or (socket-bind-host socket) "127.0.0.1")
                                           (or (socket-bind-port socket) 0)
                                           backlog)))
    (setf (socket-handle socket) (car result))
    (setf (socket-bind-port socket) (cdr result)))
  t)

(defun socket-accept (socket)
  (make-instance 'inet-socket :handle (ecl-python:%socket-accept (socket-handle socket))))

(defun socket-name (socket)
  (values (or (socket-bind-host socket) "0.0.0.0") (socket-bind-port socket)))

;; A bidirectional binary Gray stream over one TCP handle. STREAM-READ-SEQUENCE
;; and STREAM-WRITE-SEQUENCE are intentionally not defined: ECL's own default
;; methods for FUNDAMENTAL-BINARY-{INPUT,OUTPUT}-STREAM already loop over
;; STREAM-READ-BYTE/STREAM-WRITE-BYTE, matching the host file streams above.
(defclass host-socket-stream (gray:fundamental-binary-input-stream
                              gray:fundamental-binary-output-stream)
  ((handle :initarg :handle :accessor host-socket-stream-handle)
   (read-buffer :initform (make-array 0 :element-type '(unsigned-byte 8))
                :accessor host-socket-read-buffer)
   (read-pos :initform 0 :accessor host-socket-read-pos)
   (eof-p :initform nil :accessor host-socket-eof-p)
   (write-buffer :initform (make-array 0 :element-type '(unsigned-byte 8)
                                       :adjustable t :fill-pointer 0)
                 :accessor host-socket-write-buffer)
   (closed-p :initform nil :accessor host-socket-closed-p)))

(defmethod gray:stream-element-type ((stream host-socket-stream))
  '(unsigned-byte 8))

(defmethod gray:stream-read-byte ((stream host-socket-stream))
  (with-slots (read-buffer read-pos eof-p handle) stream
    (cond
      ((< read-pos (length read-buffer))
       (prog1 (aref read-buffer read-pos) (incf read-pos)))
      (eof-p :eof)
      (t
       (let ((chunk (ecl-python:%socket-recv handle 65536)))
         (if (zerop (length chunk))
             (progn (setf eof-p t) :eof)
             (progn
               (setf read-buffer (ecl-python::bytes-to-byte-vector chunk))
               (setf read-pos 0)
               (prog1 (aref read-buffer 0) (incf read-pos)))))))))

(defmethod gray:stream-listen ((stream host-socket-stream))
  "Non-blocking readability check. ANSI LISTEN must never block, unlike
STREAM-READ-BYTE, so this cannot simply try a recv."
  (with-slots (read-buffer read-pos eof-p handle) stream
    (or (< read-pos (length read-buffer))
        (and (not eof-p) (ecl-python:%socket-poll handle) t))))

(defmethod gray:stream-write-byte ((stream host-socket-stream) integer)
  (vector-push-extend integer (host-socket-write-buffer stream))
  integer)

(defun flush-socket-stream (stream)
  (let ((buffer (host-socket-write-buffer stream)))
    (when (plusp (length buffer))
      (ecl-python:%socket-send (host-socket-stream-handle stream)
                               (ecl-python::buffer-to-bytes buffer))
      (setf (fill-pointer buffer) 0))))

(defmethod gray:stream-finish-output ((stream host-socket-stream))
  (flush-socket-stream stream)
  nil)

(defmethod gray:stream-force-output ((stream host-socket-stream))
  (flush-socket-stream stream)
  nil)

(defmethod gray:close ((stream host-socket-stream) &key abort)
  (unless (host-socket-closed-p stream)
    (setf (host-socket-closed-p stream) t)
    (unless abort
      (flush-socket-stream stream))
    (ecl-python:%socket-close (host-socket-stream-handle stream)))
  (call-next-method))

(defun socket-make-stream (socket &key element-type input output buffering &allow-other-keys)
  (declare (ignore element-type input output buffering))
  (make-instance 'host-socket-stream :handle (socket-handle socket)))

(defun socket-close (socket)
  (ecl-python:%socket-close (socket-handle socket))
  (setf (socket-handle socket) nil)
  t)

(in-package #:ecl-python)

(defun provide-sockets (module)
  "REQUIRE provider for SOCKETS/SB-BSD-SOCKETS backed by a real TCP socket
bridge through the Python host, used by swank/ecl.lisp's TCP server backend."
  (when (member (string module) '("SOCKETS" "SB-BSD-SOCKETS") :test #'string-equal)
    (provide "SOCKETS")
    (provide "SB-BSD-SOCKETS")
    t))

(pushnew 'provide-sockets ext:*module-provider-functions*)

;;; ---------------------------------------------------------------------
;;; SWANK/SLIME support.
;;;
;;; Bundles the real, unmodified upstream SWANK source (eclpy/swank/*.lisp)
;;; and loads it directly as source -- SWANK's own loader normally compiles
;;; each file to a FASL first, which is impossible in the WASM sandbox (no
;;; C compiler, no dlopen). SB-BSD-SOCKETS above already implements the
;;; exact server-side API (socket-bind/socket-listen/socket-accept/
;;; sockopt-reuse-address/socket-name) that the real swank/ecl.lisp backend
;;; drives via CREATE-SOCKET/ACCEPT-CONNECTION, so that backend file loads
;;; unmodified too. ECL has no real threads in this WASM build (:threads is
;;; absent from *features*), so SWANK runs in its single-threaded, blocking
;;; :communication-style NIL mode: START-SWANK's CREATE-SERVER call blocks
;;; the calling thread for as long as the server serves requests, exactly
;;; like a native single-threaded Lisp bound to Emacs. Run it from a
;;; background thread on the Python side to keep the host process usable.
;;;
;;; The vendored swank/ecl.lisp reads its socket support behind a
;;; #+sockets conditional, so SOCKETS must be REQUIREd and :SOCKETS pushed
;;; onto *FEATURES* before that file loads.
;;; ---------------------------------------------------------------------

(defun swank-source-file (name)
  (merge-pathnames name *swank-source-directory*))

(defun start-swank (&key (port 4005) (dont-close t))
  "Load the bundled SWANK source (once per session) and start a SWANK
server on PORT. Blocks the calling thread for as long as the server
serves requests; run this from a background thread. Returns the bound
port number once the server socket is listening."
  (unless *swank-source-directory*
    (error "ecl-python:*swank-source-directory* is not set."))
  (unless (find-package "SWANK")
    (require 'sockets)
    (pushnew :sockets *features*)
    (load (swank-source-file "loader.lisp"))
    (load (swank-source-file "packages.lisp"))
    (load (swank-source-file "backend.lisp"))
    (load (swank-source-file "ecl.lisp"))
    (load (swank-source-file "gray.lisp"))
    (load (swank-source-file "match.lisp"))
    (load (swank-source-file "rpc.lisp"))
    (load (swank-source-file "swank-core.lisp"))
    (load (swank-source-file "swank-repl.lisp"))
    (funcall (intern "INIT" "SWANK"))
    ;; Native compilation is impossible in the WASM sandbox (no C compiler,
    ;; no dlopen). Replace the compile-file-based backend implementations
    ;; with source EVAL, mirroring how PROVIDE-ASDF above skips COMPILE-OP.
    ;; Errors are still reported as COMPILER-CONDITIONs so Emacs shows them
    ;; the normal way instead of aborting the RPC request.
    (eval-source-forms "
(in-package #:swank/ecl)

(defimplementation swank-compile-string
    (string &key buffer position filename line column policy)
  (declare (ignore filename line column policy))
  (with-compilation-hooks ()
    (let ((*buffer-name* buffer)
          (*buffer-start-position* position)
          (failure-p nil))
      (with-input-from-string (in string)
        (loop
          (let ((form (handler-case (read in nil :eof)
                        (error (e)
                          (signal-compiler-condition
                           :original-condition e
                           :message (princ-to-string e)
                           :severity :error
                           :location (make-error-location e))
                          (setf failure-p t)
                          :eof))))
            (when (eq form :eof) (return))
            (handler-case (eval form)
              (error (e)
                (signal-compiler-condition
                 :original-condition e
                 :message (princ-to-string e)
                 :severity :error
                 :location (make-error-location e))
                (setf failure-p t))))))
      (not failure-p))))

(defimplementation swank-compile-file (input-file output-file load-p
                                       external-format &key policy)
  (declare (ignore output-file external-format policy))
  (with-compilation-hooks ()
    (let ((failure-p nil))
      (when load-p
        (handler-case
            (with-open-file (in input-file :direction :input)
              (loop
                (let ((form (read in nil :eof)))
                  (when (eq form :eof) (return))
                  (eval form))))
          (error (e)
            (signal-compiler-condition
             :original-condition e
             :message (princ-to-string e)
             :severity :error
             :location (make-error-location e))
            (setf failure-p t))))
      (not failure-p))))
"))
    ;; ECL's WASM build lowers its pervasive setjmp/longjmp to native WASM
    ;; exceptions (see scripts/build_ecl_wasm.py). SWANK's interactive
    ;; debugger (SLDB) needs SI::IHS-TOP/SI::FRS-TOP to walk the raw
    ;; interpreter history/frame stacks for backtraces; CALL-WITH-DEBUGGING-
    ;; ENVIRONMENT collects frame 0's environment via (SI::IHS-ENV 0), but
    ;; frame 0 is the sentinel base of the interpreter history stack with no
    ;; real environment, and reading it hard-traps the WASM instance (not a
    ;; catchable Lisp condition) instead of erroring cleanly. Guard index 0
    ;; only; higher indices are real interpreter frames and work unmodified.
    (eval-source-forms "
(in-package #:swank/ecl)

(defimplementation call-with-debugging-environment (debugger-loop-fn)
  (declare (type function debugger-loop-fn))
  (let* ((*ihs-top* (ihs-top))
         (*ihs-current* *ihs-top*)
         (*frs-base* (or (sch-frs-base *frs-top* *ihs-base*) (1+ (frs-top))))
         (*frs-top* (frs-top))
         (*tpl-level* (1+ *tpl-level*))
         (*backtrace* (loop for ihs from 0 below *ihs-top*
                            collect (list (if (plusp ihs) (si::ihs-fun ihs) nil)
                                          (if (plusp ihs) (si::ihs-env ihs) nil)
                                          nil))))
    (declare (special *ihs-current*))
    (loop for f from *frs-base* until *frs-top*
          do (let ((i (- (si::frs-ihs f) *ihs-base* 1)))
               (when (plusp i)
                 (let* ((x (elt *backtrace* i))
                        (name (si::frs-tag f)))
                   (unless (si::fixnump name)
                     (push name (third x)))))))
    (setf *backtrace* (remove-if (function is-ignorable-fun-p) (nreverse *backtrace*)))
    (set-break-env)
    (set-current-ihs)
    (let ((*ihs-base* *ihs-top*))
      (funcall debugger-loop-fn))))
")
  (setf (symbol-value (intern "*COMMUNICATION-STYLE*" "SWANK")) nil)
  ;; The WASM sandbox's *standard-input* is a FILE-STREAM whose LISTEN
  ;; fails with "Function not implemented" (there is no real stdin).
  ;; SIMPLE-SERVE-REQUESTS's REPL-INPUT-STREAM-READ calls LISTEN on both
  ;; the socket and *standard-input* via WAIT-FOR-INPUT, so give it a
  ;; stream whose LISTEN always safely returns false instead.
  (let ((*standard-input* (make-string-input-stream "")))
    (funcall (intern "CREATE-SERVER" "SWANK")
             :port port :style nil :dont-close dont-close)))

(defconstant +protocol-name+ "eclpy")
(defconstant +protocol-version+ 1)
(defconstant +json-null+ :json-null)
(defconstant +json-false+ :json-false)

(defun json-object (&rest fields)
  (cons :object fields))

(defun json-object-p (value)
  (and (consp value) (eq (car value) :object)))

(defun json-array (items)
  (cons :array items))

(defun json-array-p (value)
  (and (consp value) (eq (car value) :array)))

(defun protocol-field (key value)
  (cons key value))

(defun protocol-envelope (&rest fields)
  (apply #'json-object
         (append (list (protocol-field "protocol" +protocol-name+)
                       (protocol-field "version" +protocol-version+))
                 fields)))

(defun evaluate (thunk)
  (handler-case
      (protocol-envelope
       (protocol-field "status" "ok")
       (protocol-field "value" (serialize (funcall thunk))))
    (error (condition)
      (protocol-envelope
       (protocol-field "status" "error")
       (protocol-field "condition_type" (condition-type-name condition))
       (protocol-field "message" (condition-message condition))))))

(defun symbol-package-name-or-null (symbol)
  (let ((package (symbol-package symbol)))
    (if package (package-name package) +json-null+)))

(defun ref-node (value kind)
  (json-object
   (protocol-field "type" "ref")
   (protocol-field "id" (store-object value))
   (protocol-field "kind" kind)))

(defun serialize-cons (value seen)
  (let ((items '())
        (tail value))
    (loop
      (cond
        ((null tail)
         (return (json-object
                  (protocol-field "type" "list")
                  (protocol-field "items" (json-array (nreverse items))))))
        ((not (consp tail))
         (return (json-object
                  (protocol-field "type" "dotted-list")
                  (protocol-field "items" (json-array (nreverse items)))
                  (protocol-field "tail" (serialize tail seen)))))
        ((gethash tail seen)
         (return (ref-node value "CONS")))
        (t
         (setf (gethash tail seen) t)
         (push (serialize (car tail) seen) items)
         (setf tail (cdr tail)))))))

(defun serialize (value &optional (seen (make-hash-table :test #'eq)))
  (cond
    ((null value) (json-object (protocol-field "type" "nil")))
    ((eq value t) (json-object (protocol-field "type" "true")))
    ((integerp value)
     (json-object
      (protocol-field "type" "int")
      (protocol-field "value" (princ-to-string value))))
    ((rationalp value)
     (json-object
      (protocol-field "type" "ratio")
      (protocol-field "numerator" (princ-to-string (numerator value)))
      (protocol-field "denominator" (princ-to-string (denominator value)))))
    ((floatp value)
     (json-object
      (protocol-field "type" "float")
      (protocol-field "value" (format nil "~E" value))))
    ((stringp value)
     (json-object
      (protocol-field "type" "string")
      (protocol-field "value" value)))
    ((characterp value)
     (json-object
      (protocol-field "type" "string")
      (protocol-field "value" (string value))))
    ((symbolp value)
     (json-object
      (protocol-field "type" "symbol")
      (protocol-field "name" (symbol-name value))
      (protocol-field "package" (symbol-package-name-or-null value))))
    ((consp value) (serialize-cons value seen))
    ((vectorp value)
     (json-object
      (protocol-field "type" "vector")
      (protocol-field "items"
                      (json-array
                       (loop for index below (length value)
                             collect (serialize (aref value index) seen))))))
    ((packagep value)
     (json-object
      (protocol-field "type" "package")
      (protocol-field "name" (package-name value))))
    ((functionp value) (ref-node value "FUNCTION"))
    (t (ref-node value (prin1-to-string (type-of value))))))

(defun json-escape-string (value)
  (with-output-to-string (out)
    (write-char #\" out)
    (loop for char across value do
      (case char
        (#\" (write-string "\\\"" out))
        (#\\ (write-string "\\\\" out))
        (#\Newline (write-string "\\n" out))
        (#\Return (write-string "\\r" out))
        (#\Tab (write-string "\\t" out))
        (otherwise
         (let ((code (char-code char)))
           (if (< code 32)
               (format out "\\u~4,'0x" code)
               (write-char char out))))))
    (write-char #\" out)))

(defun json-encode-value (value out)
  (cond
    ((eq value +json-null+) (write-string "null" out))
    ((eq value +json-false+) (write-string "false" out))
    ((eq value t) (write-string "true" out))
    ((integerp value) (princ value out))
    ((floatp value) (princ value out))
    ((stringp value) (write-string (json-escape-string value) out))
    ((json-object-p value)
     (write-char #\{ out)
     (loop for fields on (cdr value)
           for field = (car fields)
           for first = t then nil do
       (unless first (write-char #\, out))
       (write-string (json-escape-string (car field)) out)
       (write-char #\: out)
       (json-encode-value (cdr field) out))
     (write-char #\} out))
    ((json-array-p value)
     (write-char #\[ out)
     (loop for tail on (cdr value)
           for first = t then nil do
       (unless first (write-char #\, out))
       (json-encode-value (car tail) out))
     (write-char #\] out))
    (t (error "Cannot JSON encode value: ~S" value))))

(defun json-encode (value)
  (with-output-to-string (out)
    (json-encode-value value out)))

(defun lookup-symbol (package-name symbol-name)
  (let ((package (find-package package-name)))
    (unless package
      (return-from lookup-symbol
        (protocol-envelope (protocol-field "kind" "missing"))))
    (multiple-value-bind (symbol status) (find-symbol symbol-name package)
      (declare (ignore status))
      (unless symbol
        (return-from lookup-symbol
          (protocol-envelope (protocol-field "kind" "missing"))))
      (let ((callable-type (cond
                              ((special-operator-p symbol) "special")
                              ((macro-function symbol) "macro")
                              ((fboundp symbol) "function"))))
        (cond
          (callable-type
           (protocol-envelope
            (protocol-field "kind" "callable")
            (protocol-field "callable_type" callable-type)
            (protocol-field "name" (symbol-name symbol))
            (protocol-field "package" (symbol-package-name-or-null symbol))))
          ((boundp symbol)
           (protocol-envelope
            (protocol-field "kind" "value")
            (protocol-field "value" (serialize (symbol-value symbol)))))
          (t
           (protocol-envelope
            (protocol-field "kind" "symbol")
            (protocol-field "name" (symbol-name symbol))
            (protocol-field "package" (symbol-package-name-or-null symbol)))))))))

(in-package #:cl-user)
