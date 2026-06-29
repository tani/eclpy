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

(defun evaluate (thunk)
  (handler-case
      (list :ok (serialize (funcall thunk)))
    (error (condition)
      (list :error
            (condition-type-name condition)
            (condition-message condition)))))

(defun serialize-cons (value seen)
  (let ((items '())
        (tail value))
    (loop
      (cond
        ((null tail)
         (return (cons :list (nreverse items))))
        ((not (consp tail))
         (return (list :dotted-list (nreverse items) (serialize tail seen))))
        ((gethash tail seen)
         (return (list :ref (store-object value) "CONS")))
        (t
         (setf (gethash tail seen) t)
         (push (serialize (car tail) seen) items)
         (setf tail (cdr tail)))))))

(defun serialize (value &optional (seen (make-hash-table :test #'eq)))
  (cond
    ((null value) '(:nil))
    ((eq value t) '(:true))
    ((integerp value) (list :int value))
    ((rationalp value) (list :ratio (numerator value) (denominator value)))
    ((floatp value) (list :float (format nil "~E" value)))
    ((stringp value) (list :string value))
    ((characterp value) (list :string (string value)))
    ((symbolp value)
     (list :symbol
           (symbol-name value)
           (let ((package (symbol-package value)))
             (and package (package-name package)))))
    ((consp value) (serialize-cons value seen))
    ((vectorp value)
     (cons :vector
           (loop for index below (length value)
                 collect (serialize (aref value index) seen))))
    ((packagep value) (list :package (package-name value)))
    ((functionp value) (list :ref (store-object value) "FUNCTION"))
    (t (list :ref (store-object value) (prin1-to-string (type-of value))))))

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
    ((null value) (write-string "null" out))
    ((eq value t) (write-string "true" out))
    ((keywordp value)
     (write-string (json-escape-string (format nil ":~A" (symbol-name value))) out))
    ((integerp value) (princ value out))
    ((floatp value) (princ value out))
    ((stringp value) (write-string (json-escape-string value) out))
    ((consp value)
     (write-char #\[ out)
     (loop for tail on value
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
    (return-from json-decode-array (values '() (1+ index))))
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
           (return (values (nreverse items) (1+ index))))
          (t (error "Invalid JSON array separator: ~A" char)))))))

(defun json-decode-value (source index)
  (setf index (json-skip-ws source index))
  (when (>= index (length source))
    (error "Unexpected end of JSON"))
  (let ((char (aref source index)))
    (cond
      ((char= char #\[) (json-decode-array source index))
      ((char= char #\") (json-decode-string source index))
      ((json-prefix-p source index "true") (values t (+ index 4)))
      ((json-prefix-p source index "false") (values nil (+ index 5)))
      ((json-prefix-p source index "null") (values nil (+ index 4)))
      (t (json-decode-number source index)))))

(defun json-decode (source)
  (let ((text (string source)))
    (multiple-value-bind (value index) (json-decode-value text 0)
    (unless (= (json-skip-ws text index) (length text))
      (error "Trailing data in JSON"))
    value)))

(defun deserialize-list (items)
  (mapcar #'deserialize items))

(defun protocol-tag (value)
  (cond
    ((keywordp value) value)
    ((and (stringp value)
          (> (length value) 0)
          (char= (aref value 0) #\:))
     (intern (subseq value 1) "KEYWORD"))
    (t value)))

(defun deserialize (node)
  (unless (consp node)
    (error "Invalid eclpy protocol value: ~S" node))
  (case (protocol-tag (first node))
    (:nil nil)
    (:true t)
    (:int (second node))
    (:ratio (/ (second node) (third node)))
    (:float (read-from-string (second node)))
    (:string (second node))
    (:symbol (if (third node)
                 (intern (second node) (third node))
                 (make-symbol (second node))))
    (:list (deserialize-list (rest node)))
    (:dotted-list
     (let ((tail (deserialize (third node))))
       (dolist (item (reverse (second node)) tail)
         (setf tail (cons (deserialize item) tail)))))
    (:vector (coerce (deserialize-list (rest node)) 'vector))
    (:package (find-package (second node)))
    (:ref (value (second node)))
    (otherwise (error "Unknown eclpy protocol tag: ~S" (first node)))))

(defun lookup-symbol (package-name symbol-name)
  (let ((package (find-package package-name)))
    (unless package
      (return-from lookup-symbol '(:missing)))
    (multiple-value-bind (symbol status) (find-symbol symbol-name package)
      (declare (ignore status))
      (unless symbol
        (return-from lookup-symbol '(:missing)))
      (let ((symbol-package (symbol-package symbol)))
        (cond
          ((special-operator-p symbol)
           (list :callable :special (symbol-name symbol)
                 (and symbol-package (package-name symbol-package))))
          ((macro-function symbol)
           (list :callable :macro (symbol-name symbol)
                 (and symbol-package (package-name symbol-package))))
          ((fboundp symbol)
           (list :callable :function (symbol-name symbol)
                 (and symbol-package (package-name symbol-package))))
          ((boundp symbol)
           (list :value (serialize (symbol-value symbol))))
          (t
           (list :symbol (symbol-name symbol)
                 (and symbol-package (package-name symbol-package)))))))))

(in-package #:cl-user)
