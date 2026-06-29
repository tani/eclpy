(defpackage #:ecl-python
  (:use #:cl)
  (:shadow #:load)
  (:export #:evaluate #:lookup-symbol #:release-object #:release-all-objects
           #:json-encode #:native-load #:py-eval #:py-exec #:serialize #:value
           #:*asdf-source*))

(in-package #:ecl-python)

(defvar *objects* (make-hash-table :test #'eql))
(defvar *next-id* 0)

(defvar *asdf-source* nil
  "Host pathname of the bundled ASDF source, set from Python, or NIL.")

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
  (deserialize (json-decode (%py-eval (string source)))))

(defun py-exec (source)
  (deserialize (json-decode (%py-exec (string source)))))

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

(let ((previous-lock (si::package-lock "CL" nil)))
  (unwind-protect
       (progn
         (setf (symbol-function 'cl:load) #'load)
         (setf (symbol-function 'cl:probe-file) #'host-probe-file)
         (setf (symbol-function 'cl:truename) #'host-truename)
         (setf (symbol-function 'cl:file-write-date) #'host-file-write-date))
    (si::package-lock "CL" previous-lock)))

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

(defconstant +protocol-name+ "eclpy")
(defconstant +protocol-version+ 1)
(defconstant +json-null+ :json-null)
(defconstant +json-false+ :json-false)

(defun json-field (key value)
  (cons key value))

(defun json-object (&rest fields)
  (cons :object fields))

(defun json-object-p (value)
  (and (consp value) (eq (car value) :object)))

(defun json-array (items)
  (cons :array items))

(defun json-array-p (value)
  (and (consp value) (eq (car value) :array)))

(defun protocol-field (key value)
  (json-field key value))

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
         (return (json-object
                  (protocol-field "type" "ref")
                  (protocol-field "id" (store-object value))
                  (protocol-field "kind" "CONS"))))
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
      (protocol-field "package"
                      (let ((package (symbol-package value)))
                        (if package (package-name package) +json-null+)))))
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
    ((functionp value)
     (json-object
      (protocol-field "type" "ref")
      (protocol-field "id" (store-object value))
      (protocol-field "kind" "FUNCTION")))
    (t
     (json-object
      (protocol-field "type" "ref")
      (protocol-field "id" (store-object value))
      (protocol-field "kind" (prin1-to-string (type-of value)))))))

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

(defun json-skip-ws (source index)
  (loop while (and (< index (length source))
                   (find (aref source index) " \t\n\r"))
        do (incf index))
  index)

(defun json-hex-value (char)
  (digit-char-p char 16))

(defun json-decode-string (source index)
  (incf index)
  (let ((out (make-string-output-stream)))
    (loop
      (when (>= index (length source))
        (error "Unterminated JSON string"))
      (let ((char (aref source index)))
        (incf index)
        (cond
          ((char= char #\") (return (values (get-output-stream-string out) index)))
          ((char= char #\\)
           (when (>= index (length source))
             (error "Invalid JSON string escape"))
           (let ((escape (aref source index)))
             (incf index)
             (case escape
               (#\" (write-char #\" out))
               (#\\ (write-char #\\ out))
               (#\/ (write-char #\/ out))
               (#\b (write-char (code-char 8) out))
               (#\f (write-char (code-char 12) out))
               (#\n (write-char #\Newline out))
               (#\r (write-char #\Return out))
               (#\t (write-char #\Tab out))
               (#\u
                (when (> (+ index 4) (length source))
                  (error "Invalid JSON unicode escape"))
                (let ((code 0))
                  (dotimes (n 4)
                    (let ((digit (json-hex-value (aref source index))))
                      (unless digit (error "Invalid JSON unicode escape"))
                      (setf code (+ (* code 16) digit))
                      (incf index)))
                  (write-char (code-char code) out)))
               (otherwise (error "Invalid JSON string escape: ~A" escape)))))
          (t (write-char char out)))))))

(defun json-decode-number (source index)
  (let ((start index))
    (loop while (and (< index (length source))
                     (find (aref source index) "+-0123456789.eE"))
          do (incf index))
    (let ((*read-eval* nil))
      (values (read-from-string source nil nil :start start :end index) index))))

(defun json-prefix-p (source index text)
  (and (<= (+ index (length text)) (length source))
       (string= source text :start1 index :end1 (+ index (length text)))))

(defun json-decode-array (source index)
  (incf index)
  (setf index (json-skip-ws source index))
  (when (and (< index (length source)) (char= (aref source index) #\]))
    (return-from json-decode-array (values (json-array '()) (1+ index))))
  (let ((items '()))
    (loop
      (multiple-value-bind (item next-index) (json-decode-value source index)
        (push item items)
        (setf index (json-skip-ws source next-index)))
      (when (>= index (length source))
        (error "Unterminated JSON array"))
      (let ((char (aref source index)))
        (cond
          ((char= char #\,)
           (setf index (json-skip-ws source (1+ index))))
          ((char= char #\])
           (return (values (json-array (nreverse items)) (1+ index))))
          (t (error "Invalid JSON array separator: ~A" char)))))))

(defun json-decode-object (source index)
  (incf index)
  (setf index (json-skip-ws source index))
  (when (and (< index (length source)) (char= (aref source index) #\}))
    (return-from json-decode-object (values (json-object) (1+ index))))
  (let ((fields '()))
    (loop
      (unless (and (< index (length source)) (char= (aref source index) #\"))
        (error "Expected JSON object key"))
      (multiple-value-bind (key next-index) (json-decode-string source index)
        (setf index (json-skip-ws source next-index))
        (unless (and (< index (length source)) (char= (aref source index) #\:))
          (error "Expected JSON object colon"))
        (multiple-value-bind (value value-index)
            (json-decode-value source (json-skip-ws source (1+ index)))
          (push (json-field key value) fields)
          (setf index (json-skip-ws source value-index))))
      (when (>= index (length source))
        (error "Unterminated JSON object"))
      (let ((char (aref source index)))
        (cond
          ((char= char #\,)
           (setf index (json-skip-ws source (1+ index))))
          ((char= char #\})
           (return (values (apply #'json-object (nreverse fields)) (1+ index))))
          (t (error "Invalid JSON object separator: ~A" char)))))))

(defun json-decode-value (source index)
  (setf index (json-skip-ws source index))
  (when (>= index (length source))
    (error "Unexpected end of JSON"))
  (let ((char (aref source index)))
    (cond
      ((char= char #\[) (json-decode-array source index))
      ((char= char #\{) (json-decode-object source index))
      ((char= char #\") (json-decode-string source index))
      ((json-prefix-p source index "true") (values t (+ index 4)))
      ((json-prefix-p source index "false") (values +json-false+ (+ index 5)))
      ((json-prefix-p source index "null") (values +json-null+ (+ index 4)))
      (t (json-decode-number source index)))))

(defun json-decode (source)
  (let ((text (string source)))
    (multiple-value-bind (value index) (json-decode-value text 0)
    (unless (= (json-skip-ws text index) (length text))
      (error "Trailing data in JSON"))
    value)))

(defun json-object-field (object key)
  (unless (json-object-p object)
    (error "Expected JSON object, got ~S" object))
  (assoc key (cdr object) :test #'string=))

(defun json-object-require (object key)
  (let ((field (json-object-field object key)))
    (unless field
      (error "Missing JSON object field ~S in ~S" key object))
    (cdr field)))

(defun json-object-exact-keys (object keys)
  (unless (json-object-p object)
    (error "Expected JSON object, got ~S" object))
  (dolist (field (cdr object))
    (unless (member (car field) keys :test #'string=)
      (error "Unexpected JSON object field ~S in ~S" (car field) object)))
  (dolist (key keys)
    (unless (json-object-field object key)
      (error "Missing JSON object field ~S in ~S" key object))))

(defun json-require-string (object key)
  (let ((value (json-object-require object key)))
    (unless (stringp value)
      (error "Expected string field ~S, got ~S" key value))
    value))

(defun json-require-nullable-string (object key)
  (let ((value (json-object-require object key)))
    (cond
      ((eq value +json-null+) nil)
      ((stringp value) value)
      (t (error "Expected string or null field ~S, got ~S" key value)))))

(defun json-require-integer (object key)
  (let ((value (json-object-require object key)))
    (unless (integerp value)
      (error "Expected integer field ~S, got ~S" key value))
    value))

(defun json-require-decimal-string (object key)
  (let ((value (json-require-string object key)))
    (unless (and (> (length value) 0)
                 (or (every #'digit-char-p value)
                     (and (char= (aref value 0) #\-)
                          (> (length value) 1)
                          (every #'digit-char-p (subseq value 1)))))
      (error "Expected decimal string field ~S, got ~S" key value))
    value))

(defun json-require-array (object key)
  (let ((value (json-object-require object key)))
    (unless (json-array-p value)
      (error "Expected array field ~S, got ~S" key value))
    (cdr value)))

(defun parse-decimal-string (text)
  (parse-integer text :junk-allowed nil))

(defun deserialize-list (items)
  (mapcar #'deserialize items))

(defun deserialize (node)
  (unless (json-object-p node)
    (error "Invalid eclpy protocol value: ~S" node))
  (let ((type (json-require-string node "type")))
    (cond
      ((string= type "nil")
       (json-object-exact-keys node '("type"))
       nil)
      ((string= type "true")
       (json-object-exact-keys node '("type"))
       t)
      ((string= type "int")
       (json-object-exact-keys node '("type" "value"))
       (parse-decimal-string (json-require-decimal-string node "value")))
      ((string= type "ratio")
       (json-object-exact-keys node '("type" "numerator" "denominator"))
       (/ (parse-decimal-string (json-require-decimal-string node "numerator"))
          (parse-decimal-string (json-require-decimal-string node "denominator"))))
      ((string= type "float")
       (json-object-exact-keys node '("type" "value"))
       (let ((*read-eval* nil))
         (read-from-string (json-require-string node "value"))))
      ((string= type "string")
       (json-object-exact-keys node '("type" "value"))
       (json-require-string node "value"))
      ((string= type "symbol")
       (json-object-exact-keys node '("type" "name" "package"))
       (let ((name (json-require-string node "name"))
             (package (json-require-nullable-string node "package")))
         (if package
             (intern name package)
             (make-symbol name))))
      ((string= type "list")
       (json-object-exact-keys node '("type" "items"))
       (deserialize-list (json-require-array node "items")))
      ((string= type "dotted-list")
       (json-object-exact-keys node '("type" "items" "tail"))
       (let ((tail (deserialize (json-object-require node "tail"))))
         (dolist (item (reverse (json-require-array node "items")) tail)
           (setf tail (cons (deserialize item) tail)))))
      ((string= type "vector")
       (json-object-exact-keys node '("type" "items"))
       (coerce (deserialize-list (json-require-array node "items")) 'vector))
      ((string= type "package")
       (json-object-exact-keys node '("type" "name"))
       (find-package (json-require-string node "name")))
      ((string= type "ref")
       (json-object-exact-keys node '("type" "id" "kind"))
       (value (json-require-integer node "id")))
      (t (error "Unknown eclpy protocol type: ~S" type)))))

(defun lookup-envelope (&rest fields)
  (apply #'protocol-envelope fields))

(defun lookup-symbol (package-name symbol-name)
  (let ((package (find-package package-name)))
    (unless package
      (return-from lookup-symbol
        (lookup-envelope (protocol-field "kind" "missing"))))
    (multiple-value-bind (symbol status) (find-symbol symbol-name package)
      (declare (ignore status))
      (unless symbol
        (return-from lookup-symbol
          (lookup-envelope (protocol-field "kind" "missing"))))
      (let ((symbol-package (symbol-package symbol)))
        (cond
          ((special-operator-p symbol)
           (lookup-envelope
            (protocol-field "kind" "callable")
            (protocol-field "callable_type" "special")
            (protocol-field "name" (symbol-name symbol))
            (protocol-field "package"
                            (if symbol-package (package-name symbol-package) +json-null+))))
          ((macro-function symbol)
           (lookup-envelope
            (protocol-field "kind" "callable")
            (protocol-field "callable_type" "macro")
            (protocol-field "name" (symbol-name symbol))
            (protocol-field "package"
                            (if symbol-package (package-name symbol-package) +json-null+))))
          ((fboundp symbol)
           (lookup-envelope
            (protocol-field "kind" "callable")
            (protocol-field "callable_type" "function")
            (protocol-field "name" (symbol-name symbol))
            (protocol-field "package"
                            (if symbol-package (package-name symbol-package) +json-null+))))
          ((boundp symbol)
           (lookup-envelope
            (protocol-field "kind" "value")
            (protocol-field "value" (serialize (symbol-value symbol)))))
          (t
           (lookup-envelope
            (protocol-field "kind" "symbol")
            (protocol-field "name" (symbol-name symbol))
            (protocol-field "package"
                            (if symbol-package (package-name symbol-package) +json-null+)))))))))

(in-package #:cl-user)
