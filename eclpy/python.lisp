;;;; python.lisp -- the PY DSL for calling into Python from Common Lisp.
;;;;
;;;; This file is loaded automatically by high-level `Lisp` sessions, immediately
;;;; after runtime.lisp. It is still kept as ordinary Lisp source so it can be
;;;; reviewed and documented directly.
;;;;
;;;; PY.RUNTIME is an eval-backed handle runtime: every Python object is a
;;;; string naming a persistent global (or an immutable literal such as
;;;; "None"), and every operation is implemented by generating a small piece
;;;; of Python source and running it through the existing
;;;; ecl-python:py-eval / py-exec bridge. There is no direct Python
;;;; object-handle C API in this project, so this is the only bridge
;;;; available; PY is written so a future direct-handle backend could replace
;;;; PY.RUNTIME without changing the PY API.

(defpackage #:py
  (:use)
  (:export
   ;; conditions
   #:python-error #:python-import-error #:python-attribute-error
   #:python-type-error #:python-value-error #:python-runtime-error
   #:condition-type #:condition-message #:condition-traceback #:condition-object
   ;; canonical API
   #:with-py #:import #:resolve #:attr #:call #:keyword #:starred #:kw-starred
   #:subscript #:set-subscript #:set-attr #:list #:tuple #:dict #:set #:slice
   #:none #:true #:false
   #:add #:sub #:mult #:mat-mult #:div #:floor-div #:mod #:pow
   #:u-add #:u-sub #:invert #:bit-and #:bit-or #:bit-xor #:l-shift #:r-shift
   #:eq #:not-eq #:lt #:lt-e #:gt #:gt-e #:is #:is-not #:in #:not-in
   #:ne #:le #:ge #:and #:or #:not
   #:to-py #:to-cl #:repr #:str
   #:with-context #:try #:callback #:eval #:exec
   ;; sugar
   #:method #:call-attr #:dotted #:get #:all #:from #:to #:between #:by
   #:plist-dict #:columns #:chain #:chain-call #:chain-attr #:chain-subscript
   #:as-bool #:as-int #:as-float #:as-string #:as-list #:as-vector #:as-dict
   #:truth #:falsehood #:truth-cl #:none-p #:some-p #:with-from))

(defpackage #:py.runtime
  (:use #:cl)
  (:shadow #:eval #:make-list)
  (:export
   #:py-object #:py-object-p #:py-object-expr
   #:import-module #:resolve-name #:get-attr #:set-attr #:call
   #:get-item #:set-item #:make-list #:make-tuple #:make-dict #:make-set
   #:make-slice #:number-op #:compare-op #:truth #:to-py #:to-cl
   #:enter-context #:exit-context #:make-callback #:eval #:exec))

(defpackage #:py.internal
  (:use #:cl))

;;; ---------------------------------------------------------------------
;;; PY conditions -- defined before PY.RUNTIME so the eval-backed runtime
;;; can wrap py-eval/py-exec failures in them.
;;; ---------------------------------------------------------------------

(in-package #:py)

(cl:define-condition python-error (cl:error)
  ((type :initarg :type :reader condition-type :initform "Exception")
   (message :initarg :message :reader condition-message :initform "")
   (traceback :initarg :traceback :reader condition-traceback :initform cl:nil)
   (object :initarg :object :reader condition-object :initform cl:nil))
  (:report (cl:lambda (condition stream)
             (cl:format stream "Python error (~A): ~A"
                        (condition-type condition)
                        (condition-message condition)))))
(cl:define-condition python-import-error (python-error) ())
(cl:define-condition python-attribute-error (python-error) ())
(cl:define-condition python-type-error (python-error) ())
(cl:define-condition python-value-error (python-error) ())
(cl:define-condition python-runtime-error (python-error) ())

;;; ---------------------------------------------------------------------
;;; PY.RUNTIME -- the eval-backed handle runtime.
;;; ---------------------------------------------------------------------

(cl:in-package #:py.runtime)

(defclass py-object ()
  ((expr :initarg :expr :reader py-object-expr))
  (:documentation
   "An opaque handle naming a Python expression: either a persistent global
temporary holding an operation result, or an immutable literal expression
such as \"None\", \"True\", or \"False\"."))

(defun py-object-p (value)
  (typep value 'py-object))

(defvar *fresh-counter* 0)

(defun fresh-name ()
  (format nil "__eclpy_py_~D" (incf *fresh-counter*)))

(defun python-string-literal (string)
  "Render STRING as a double-quoted Python string literal."
  (with-output-to-string (out)
    (write-char #\" out)
    (loop for ch across string do
      (cond
        ((char= ch #\\) (write-string "\\\\" out))
        ((char= ch #\") (write-string "\\\"" out))
        ((char= ch #\Newline) (write-string "\\n" out))
        ((char= ch #\Return) (write-string "\\r" out))
        ((char= ch #\Tab) (write-string "\\t" out))
        ((< (char-code ch) 32) (format out "\\u~4,'0X" (char-code ch)))
        (t (write-char ch out))))
    (write-char #\" out)))

(defun keyword-name (keyword-symbol)
  "Lowercase KEYWORD-SYMBOL's name. Does not translate hyphens to
underscores; callers needing an exact underscored/reserved/mixed-case
Python name must use PY:KEYWORD with an explicit string."
  (string-downcase (symbol-name keyword-symbol)))

(defun parse-python-error-message (message)
  "Best-effort split of an ecl-python condition report MESSAGE of the form
\"Python eval failed: <TypeName>: ...\" or \"Python exec failed: ...\" into
the Python exception type name and the remaining description text."
  (dolist (prefix '("Python eval failed: " "Python exec failed: "))
    (when (and (>= (length message) (length prefix))
               (string= message prefix :end1 (length prefix)))
      (let* ((rest (subseq message (length prefix)))
             (colon (search ": " rest)))
        (return-from parse-python-error-message
          (if colon
              (values (subseq rest 0 colon) rest)
              (values "Exception" rest))))))
  (values "Exception" message))

(defun condition-class-for-type (type-name)
  (cond
    ((member type-name '("ImportError" "ModuleNotFoundError") :test #'string=)
     'py:python-import-error)
    ((string= type-name "AttributeError") 'py:python-attribute-error)
    ((string= type-name "TypeError") 'py:python-type-error)
    ((string= type-name "ValueError") 'py:python-value-error)
    (t 'py:python-runtime-error)))

(defun wrap-error (thunk)
  "Invoke THUNK; map any condition escaping ecl-python:py-eval/py-exec onto
the matching PY condition class."
  (handler-case (funcall thunk)
    (error (condition)
      (multiple-value-bind (type-name description)
          (parse-python-error-message (format nil "~A" condition))
        (error (condition-class-for-type type-name) :type type-name :message description)))))

(defun eval-source (source)
  (wrap-error (lambda () (ecl-python:py-eval source))))

(defun exec-source (source)
  (wrap-error (lambda () (ecl-python:py-exec source))))

(defun assign-expression (expr)
  (let ((name (fresh-name)))
    (exec-source (format nil "~A = ~A" name expr))
    (make-instance 'py-object :expr name)))

(defun format-python-float (value)
  "Render a CL float as a Python float literal."
  (let* ((text (let ((*read-default-float-format* 'double-float))
                 (prin1-to-string (coerce value 'double-float))))
         (marker (position-if (lambda (c) (find c "dDeEfFsSlL")) text)))
    (if marker
        (concatenate 'string (subseq text 0 marker) "e" (subseq text (1+ marker)))
        text)))

(defun list-literal (expr-strings)
  (format nil "[~{~A~^, ~}]" expr-strings))

(defun tuple-literal (expr-strings)
  (if (= (length expr-strings) 1)
      (format nil "(~A,)" (first expr-strings))
      (format nil "(~{~A~^, ~})" expr-strings)))

(defun set-literal (expr-strings)
  (if (null expr-strings)
      "set()"
      (format nil "{~{~A~^, ~}}" expr-strings)))

(defun dict-literal (pair-strings)
  (if (null pair-strings)
      "{}"
      (format nil "{~{~A~^, ~}}" pair-strings)))

(defun vector-expr-list (vector)
  (loop for index below (length vector) collect (py-expr (aref vector index))))

(defun hash-table-literal (table)
  (dict-literal
   (loop for key being the hash-keys of table using (hash-value value)
         collect (format nil "~A: ~A" (py-expr key) (py-expr value)))))

(defun py-expr (value)
  "Render VALUE (a py-object handle or a supported CL literal) as Python
source text."
  (cond
    ((py-object-p value) (py-object-expr value))
    ((null value)
     (error 'py:python-type-error :type "TypeError"
            :message "CL NIL is not implicitly converted to Python None; use PY:NONE"))
    ((stringp value) (python-string-literal value))
    ((integerp value) (princ-to-string value))
    ((rationalp value)
     (format nil "__import__('fractions').Fraction(~D, ~D)" (numerator value) (denominator value)))
    ((floatp value) (format-python-float value))
    ((hash-table-p value) (hash-table-literal value))
    ((vectorp value) (list-literal (vector-expr-list value)))
    ((listp value) (tuple-literal (mapcar #'py-expr value)))
    (t (error 'py:python-type-error :type "TypeError"
              :message (format nil "Unsupported CL value for Python conversion: ~S" value)))))

(defun import-module (name)
  (assign-expression (format nil "__import__('importlib').import_module(~A)"
                              (python-string-literal name))))

(defun resolve-name (dotted-name)
  (let ((dot (position #\. dotted-name :from-end t)))
    (if (null dot)
        (assign-expression (format nil "getattr(__import__('builtins'), ~A)"
                                    (python-string-literal dotted-name)))
        (get-attr (import-module (subseq dotted-name 0 dot)) (subseq dotted-name (1+ dot))))))

(defun get-attr (object name)
  (assign-expression (format nil "getattr(~A, ~A)" (py-expr object) (python-string-literal name))))

(defun set-attr (object name value)
  (exec-source (format nil "setattr(~A, ~A, ~A)"
                        (py-expr object) (python-string-literal name) (py-expr value)))
  (to-py value))

(defun get-item (object key)
  (assign-expression (format nil "~A[~A]" (py-expr object) (py-expr key))))

(defun set-item (object key value)
  (exec-source (format nil "~A[~A] = ~A" (py-expr object) (py-expr key) (py-expr value)))
  (to-py value))

(defun make-list (items)
  (assign-expression (list-literal (mapcar #'py-expr items))))

(defun make-tuple (items)
  (assign-expression (tuple-literal (mapcar #'py-expr items))))

(defun make-set (items)
  (assign-expression (set-literal (mapcar #'py-expr items))))

(defun make-dict (pairs)
  (assign-expression
   (dict-literal (mapcar (lambda (pair) (format nil "~A: ~A" (py-expr (car pair)) (py-expr (cdr pair))))
                          pairs))))

(defun make-slice (start stop &optional step)
  (assign-expression
   (format nil "slice(~A, ~A, ~A)"
           (if start (py-expr start) "None")
           (if stop (py-expr stop) "None")
           (if step (py-expr step) "None"))))

(defun call (callable normalized-args)
  "Call CALLABLE with NORMALIZED-ARGS, a list preserving source order where
each element is (:arg value), (:keyword name value), (:starred value), or
(:kw-starred value)."
  (let ((fresh (fresh-name))
        (callable-expr (py-expr callable)))
    (exec-source
     (with-output-to-string (out)
       (format out "_args = []~%")
       (format out "_kwargs = {}~%")
       (dolist (arg normalized-args)
         (let ((kind (first arg)))
           (cond
             ((eq kind :arg)
              (format out "_args.append(~A)~%" (py-expr (second arg))))
             ((eq kind :starred)
              (format out "_args.extend(list(~A))~%" (py-expr (second arg))))
             ((eq kind :keyword)
              (format out "_kwargs[~A] = ~A~%"
                      (python-string-literal (second arg)) (py-expr (third arg))))
             ((eq kind :kw-starred)
              (format out "_kwargs.update(dict(~A))~%" (py-expr (second arg))))
             (t (error 'py:python-type-error :type "TypeError"
                       :message (format nil "Unknown call argument kind ~S" kind))))))
       (format out "~A = (~A)(*_args, **_kwargs)~%" fresh callable-expr)))
    (make-instance 'py-object :expr fresh)))

(defparameter *unary-operators*
  '((:u-add . "+(~A)") (:u-sub . "-(~A)") (:invert . "~~(~A)")))

(defparameter *binary-operators*
  '((:add . "(~A) + (~A)") (:sub . "(~A) - (~A)") (:mult . "(~A) * (~A)")
    (:mat-mult . "(~A) @ (~A)") (:div . "(~A) / (~A)") (:floor-div . "(~A) // (~A)")
    (:mod . "(~A) % (~A)") (:pow . "(~A) ** (~A)")
    (:bit-and . "(~A) & (~A)") (:bit-or . "(~A) | (~A)") (:bit-xor . "(~A) ^ (~A)")
    (:l-shift . "(~A) << (~A)") (:r-shift . "(~A) >> (~A)")))

(defun number-op (operator &rest operands)
  (let ((unary (assoc operator *unary-operators*)))
    (if unary
        (progn
          (unless (= (length operands) 1)
            (error 'py:python-type-error :type "TypeError"
                   :message (format nil "~S requires exactly one operand" operator)))
          (assign-expression (format nil (cdr unary) (py-expr (first operands)))))
        (let ((binary (assoc operator *binary-operators*)))
          (unless binary
            (error 'py:python-type-error :type "TypeError"
                   :message (format nil "Unknown number operator ~S" operator)))
          (unless (= (length operands) 2)
            (error 'py:python-type-error :type "TypeError"
                   :message (format nil "~S requires exactly two operands" operator)))
          (assign-expression
           (format nil (cdr binary) (py-expr (first operands)) (py-expr (second operands))))))))

(defparameter *comparison-operators*
  '((:eq . "(~A) == (~A)") (:not-eq . "(~A) != (~A)")
    (:lt . "(~A) < (~A)") (:lt-e . "(~A) <= (~A)")
    (:gt . "(~A) > (~A)") (:gt-e . "(~A) >= (~A)")
    (:is . "(~A) is (~A)") (:is-not . "(~A) is not (~A)")
    (:in . "(~A) in (~A)") (:not-in . "(~A) not in (~A)")))

(defun compare-op (operator a b)
  (let ((entry (assoc operator *comparison-operators*)))
    (unless entry
      (error 'py:python-type-error :type "TypeError"
             :message (format nil "Unknown comparison operator ~S" operator)))
    (assign-expression (format nil (cdr entry) (py-expr a) (py-expr b)))))

(defun truth (object)
  (assign-expression (format nil "bool(~A)" (py-expr object))))

(defun to-py (value)
  (if (py-object-p value)
      value
      (assign-expression (py-expr value))))

(defun to-cl (object &key (as :raw))
  (case as
    (:raw object)
    (:bool (eval-source (format nil "bool(~A)" (py-expr object))))
    (:integer (eval-source (format nil "int(~A)" (py-expr object))))
    (:float (eval-source (format nil "float(~A)" (py-expr object))))
    (:string (eval-source (format nil "str(~A)" (py-expr object))))
    (:list (coerce (eval-source (format nil "list(~A)" (py-expr object))) 'list))
    (:vector (coerce (eval-source (format nil "list(~A)" (py-expr object))) 'vector))
    (:dict (eval-source (format nil "dict(~A)" (py-expr object))))
    (:hash-table
     (let ((alist (eval-source (format nil "dict(~A)" (py-expr object))))
           (table (make-hash-table :test #'equal)))
       (dolist (pair alist table)
         (setf (gethash (car pair) table) (cdr pair)))))
    (t (error 'py:python-value-error :type "ValueError"
              :message (format nil "Unknown PY:TO-CL :AS value ~S" as)))))

(defun enter-context (context)
  (assign-expression (format nil "(~A).__enter__()" (py-expr context))))

(defun exit-context (context condition)
  "Attempt Python __exit__ cleanup for CONTEXT and return whether it
requested suppression. CONDITION is ignored by this eval-backed backend:
cleanup always runs, and no CL condition is ever suppressed."
  (declare (ignore condition))
  (eval-source (format nil "bool((~A).__exit__(None, None, None))" (py-expr context))))

(defun make-callback (function)
  (declare (ignore function))
  (error 'py:python-runtime-error :type "CallbackUnsupported"
         :message "Callbacks require a Python-to-Lisp callback backend; eclpy currently exposes only py-eval/py-exec."))

(defun eval (code &key globals locals)
  (when (or globals locals)
    (error 'py:python-runtime-error :type "RuntimeError"
           :message "PY:EVAL globals/locals are not supported by the eval-backed runtime"))
  (assign-expression code))

(defun exec (code &key globals locals)
  (when (or globals locals)
    (error 'py:python-runtime-error :type "RuntimeError"
           :message "PY:EXEC globals/locals are not supported by the eval-backed runtime"))
  (exec-source code)
  (make-instance 'py-object :expr "None"))

;;; ---------------------------------------------------------------------
;;; PY.INTERNAL -- implementation-only marker structs.
;;; ---------------------------------------------------------------------

(in-package #:py.internal)

(defstruct (keyword-arg (:constructor make-keyword-arg (name value)))
  name value)
(defstruct (starred-arg (:constructor make-starred-arg (value)))
  value)
(defstruct (kw-starred-arg (:constructor make-kw-starred-arg (value)))
  value)

;;; ---------------------------------------------------------------------
;;; PY -- canonical API and sugar, implemented on top of PY.RUNTIME.
;;;
;;; PY :USEs nothing, so every Common Lisp operator below must be
;;; CL:-qualified; unqualified symbols (IMPORT, LIST, NOT, AND, ...) are the
;;; DSL's own public names.
;;; ---------------------------------------------------------------------

(in-package #:py)

(cl:defun normalize-call-args (args)
  "Walk ARGS (already-evaluated PY:CALL arguments) into PY.RUNTIME:CALL's
normalized-args shape, preserving source order."
  (cl:let ((result '()))
    (cl:loop
      (cl:when (cl:null args) (cl:return))
      (cl:let ((item (cl:pop args)))
        (cl:cond
          ((py.internal::keyword-arg-p item)
           (cl:push (cl:list :keyword (py.internal::keyword-arg-name item)
                              (py.internal::keyword-arg-value item))
                    result))
          ((py.internal::starred-arg-p item)
           (cl:push (cl:list :starred (py.internal::starred-arg-value item)) result))
          ((py.internal::kw-starred-arg-p item)
           (cl:push (cl:list :kw-starred (py.internal::kw-starred-arg-value item)) result))
          ((cl:keywordp item)
           (cl:when (cl:null args)
             (cl:error 'python-value-error :type "ValueError"
                       :message "Python keyword argument missing value"))
           (cl:push (cl:list :keyword (py.runtime::keyword-name item) (cl:pop args)) result))
          (cl:t (cl:push (cl:list :arg item) result)))))
    (cl:nreverse result)))

(cl:defun import (module-name) (py.runtime:import-module module-name))
(cl:defun resolve (dotted-name) (py.runtime:resolve-name dotted-name))

(cl:defun attr (object cl:&rest names)
  (cl:reduce (cl:lambda (current name) (py.runtime:get-attr current name))
             names :initial-value object))

(cl:defun call (callable cl:&rest args)
  (py.runtime:call callable (normalize-call-args args)))

(cl:defun subscript (object key) (py.runtime:get-item object key))
(cl:defun set-subscript (object key value) (py.runtime:set-item object key value))
(cl:defun set-attr (object field value) (py.runtime:set-attr object field value))
(cl:defun to-py (value) (py.runtime:to-py value))
(cl:defun to-cl (object cl:&key (as :raw)) (py.runtime:to-cl object :as as))
(cl:defun repr (object) (call (resolve "repr") object))
(cl:defun str (object) (call (resolve "str") object))

(cl:defun eval (code cl:&key globals locals)
  (py.runtime:eval code :globals globals :locals locals))
(cl:defun exec (code cl:&key globals locals)
  (py.runtime:exec code :globals globals :locals locals))

(cl:defun keyword (name value)
  (cl:unless (cl:stringp name)
    (cl:error 'python-value-error :type "ValueError" :message "PY:KEYWORD requires a string name"))
  (py.internal::make-keyword-arg name value))
(cl:defun starred (value) (py.internal::make-starred-arg value))
(cl:defun kw-starred (value) (py.internal::make-kw-starred-arg value))

(cl:defun none () (cl:make-instance 'py.runtime:py-object :expr "None"))
(cl:defun true () (cl:make-instance 'py.runtime:py-object :expr "True"))
(cl:defun false () (cl:make-instance 'py.runtime:py-object :expr "False"))

(cl:defun list (cl:&rest items) (py.runtime:make-list items))
(cl:defun tuple (cl:&rest items) (py.runtime:make-tuple items))

(cl:defun plist-to-pairs (plist)
  (cl:when plist
    (cl:cons (cl:cons (cl:first plist) (cl:second plist)) (plist-to-pairs (cl:cddr plist)))))

(cl:defun dict (cl:&rest key-values)
  (cl:when (cl:oddp (cl:length key-values))
    (cl:error 'python-value-error :type "ValueError"
              :message "PY:DICT requires an even number of key/value arguments"))
  (py.runtime:make-dict (plist-to-pairs key-values)))

(cl:defun set (cl:&rest items) (py.runtime:make-set items))
(cl:defun slice (start stop cl:&optional step) (py.runtime:make-slice start stop step))

(cl:defun add (cl:&rest operands) (cl:apply #'py.runtime:number-op :add operands))
(cl:defun sub (cl:&rest operands) (cl:apply #'py.runtime:number-op :sub operands))
(cl:defun mult (cl:&rest operands) (cl:apply #'py.runtime:number-op :mult operands))
(cl:defun mat-mult (cl:&rest operands) (cl:apply #'py.runtime:number-op :mat-mult operands))
(cl:defun div (cl:&rest operands) (cl:apply #'py.runtime:number-op :div operands))
(cl:defun floor-div (cl:&rest operands) (cl:apply #'py.runtime:number-op :floor-div operands))
(cl:defun mod (cl:&rest operands) (cl:apply #'py.runtime:number-op :mod operands))
(cl:defun pow (cl:&rest operands) (cl:apply #'py.runtime:number-op :pow operands))
(cl:defun u-add (cl:&rest operands) (cl:apply #'py.runtime:number-op :u-add operands))
(cl:defun u-sub (cl:&rest operands) (cl:apply #'py.runtime:number-op :u-sub operands))
(cl:defun invert (cl:&rest operands) (cl:apply #'py.runtime:number-op :invert operands))
(cl:defun bit-and (cl:&rest operands) (cl:apply #'py.runtime:number-op :bit-and operands))
(cl:defun bit-or (cl:&rest operands) (cl:apply #'py.runtime:number-op :bit-or operands))
(cl:defun bit-xor (cl:&rest operands) (cl:apply #'py.runtime:number-op :bit-xor operands))
(cl:defun l-shift (cl:&rest operands) (cl:apply #'py.runtime:number-op :l-shift operands))
(cl:defun r-shift (cl:&rest operands) (cl:apply #'py.runtime:number-op :r-shift operands))

(cl:defun eq (a b) (py.runtime:compare-op :eq a b))
(cl:defun not-eq (a b) (py.runtime:compare-op :not-eq a b))
(cl:defun lt (a b) (py.runtime:compare-op :lt a b))
(cl:defun lt-e (a b) (py.runtime:compare-op :lt-e a b))
(cl:defun gt (a b) (py.runtime:compare-op :gt a b))
(cl:defun gt-e (a b) (py.runtime:compare-op :gt-e a b))
(cl:defun is (a b) (py.runtime:compare-op :is a b))
(cl:defun is-not (a b) (py.runtime:compare-op :is-not a b))
(cl:defun in (a b) (py.runtime:compare-op :in a b))
(cl:defun not-in (a b) (py.runtime:compare-op :not-in a b))
(cl:defun ne (a b) (not-eq a b))
(cl:defun le (a b) (lt-e a b))
(cl:defun ge (a b) (gt-e a b))

(cl:defun truth (object) (py.runtime:truth object))
(cl:defun truth-cl (object) (to-cl (truth object) :as :bool))
(cl:defun not (object) (cl:if (truth-cl object) (false) (true)))
(cl:defun falsehood (object) (not (truth object)))

(cl:defmacro and (cl:&rest forms)
  (cl:cond
    ((cl:null forms) (cl:error "PY:AND requires at least one operand"))
    ((cl:null (cl:cdr forms)) (cl:first forms))
    (cl:t
     (cl:let ((var (cl:gensym "PY-AND")))
       `(cl:let ((,var ,(cl:first forms)))
          (cl:if (truth-cl ,var) (and ,@(cl:rest forms)) ,var))))))

(cl:defmacro or (cl:&rest forms)
  (cl:cond
    ((cl:null forms) (cl:error "PY:OR requires at least one operand"))
    ((cl:null (cl:cdr forms)) (cl:first forms))
    (cl:t
     (cl:let ((var (cl:gensym "PY-OR")))
       `(cl:let ((,var ,(cl:first forms)))
          (cl:if (truth-cl ,var) ,var (or ,@(cl:rest forms))))))))

(cl:defun derive-module-symbol (module-string)
  (cl:let* ((dot (cl:position #\. module-string :from-end cl:t))
            (component (cl:if dot (cl:subseq module-string (cl:1+ dot)) module-string)))
    (cl:intern (cl:string-upcase component))))

(cl:defmacro with-py (bindings cl:&body body)
  (cl:let ((normalized
             (cl:mapcar
              (cl:lambda (binding)
                (cl:if (cl:consp binding)
                       (cl:list (cl:first binding) (cl:second binding))
                       (cl:list (derive-module-symbol binding) binding)))
              bindings)))
    `(cl:let ,(cl:mapcar (cl:lambda (entry) `(,(cl:first entry) (import ,(cl:second entry)))) normalized)
       ,@body)))

(cl:defmacro with-from (spec cl:&body body)
  (cl:let ((bindings '()))
    (cl:loop
      (cl:when (cl:null spec) (cl:return))
      (cl:let* ((module (cl:pop spec))
                (items (cl:pop spec)))
        (cl:dolist (item items)
          (cl:if (cl:consp item)
                 (cl:push (cl:list (cl:first item) `(attr (import ,module) ,(cl:second item))) bindings)
                 (cl:push (cl:list item `(attr (import ,module) ,(cl:symbol-name item))) bindings)))))
    `(cl:let ,(cl:nreverse bindings) ,@body)))

(cl:defmacro with-context (bindings cl:&body body)
  (cl:if (cl:null bindings)
         `(cl:progn ,@body)
         (cl:let* ((binding (cl:first bindings))
                   (var (cl:first binding))
                   (context-form (cl:second binding))
                   (context-var (cl:gensym "PY-CONTEXT"))
                   (rest (cl:rest bindings)))
           `(cl:let ((,context-var ,context-form))
              (cl:let ((,var (py.runtime:enter-context ,context-var)))
                (cl:unwind-protect
                     (with-context ,rest ,@body)
                  (py.runtime:exit-context ,context-var cl:nil)))))))

(cl:defun python-error-type-matches-p (condition type-string)
  (cl:let ((actual (condition-type condition)))
    (cl:or (cl:string= actual type-string)
           (cl:let ((dot (cl:position #\. type-string :from-end cl:t)))
             (cl:and dot (cl:string= actual (cl:subseq type-string (cl:1+ dot))))))))

(cl:defmacro try (protected-form cl:&rest clauses)
  (cl:let ((condition-var (cl:gensym "PY-CONDITION")))
    `(cl:handler-case ,protected-form
       (python-error (,condition-var)
         (cl:cond
           ,@(cl:mapcar
              (cl:lambda (clause)
                (cl:let ((matcher (cl:first clause))
                         (bind-var (cl:first (cl:second clause)))
                         (clause-body (cl:cddr clause)))
                  (cl:if (cl:and (cl:symbolp matcher) (cl:string= (cl:symbol-name matcher) "PYTHON-ERROR"))
                         `(cl:t (cl:let ((,bind-var ,condition-var)) ,@clause-body))
                         `((python-error-type-matches-p ,condition-var ,matcher)
                           (cl:let ((,bind-var ,condition-var)) ,@clause-body)))))
              clauses)
           (cl:t (cl:error ,condition-var)))))))

(cl:defmacro callback (args cl:&body body)
  `(py.runtime:make-callback (cl:lambda ,args (to-py (cl:progn ,@body)))))

(cl:defun method (object method-name cl:&rest args)
  (cl:apply #'call (attr object method-name) args))
(cl:defun call-attr (object attr-name cl:&rest args)
  (cl:apply #'call (attr object attr-name) args))

(cl:defun split-on-dot (string)
  (cl:let ((parts '()) (start 0))
    (cl:loop
      (cl:let ((dot (cl:position #\. string :start start)))
        (cl:push (cl:subseq string start dot) parts)
        (cl:when (cl:null dot) (cl:return))
        (cl:setf start (cl:1+ dot))))
    (cl:nreverse parts)))

(cl:defun dotted (object dotted-name)
  (cl:let ((names (split-on-dot dotted-name)))
    (cl:dolist (name names)
      (cl:when (cl:zerop (cl:length name))
        (cl:error 'python-value-error :type "ValueError"
                  :message "PY:DOTTED requires non-empty dotted components")))
    (cl:apply #'attr object names)))

(cl:defun get (object cl:&rest keys)
  (cl:if (cl:= (cl:length keys) 1)
         (subscript object (cl:first keys))
         (subscript object (cl:apply #'tuple keys))))

(cl:defun all () (slice (none) (none)))
(cl:defun from (start) (slice start (none)))
(cl:defun to (stop) (slice (none) stop))
(cl:defun between (start stop cl:&optional step) (slice start stop step))
(cl:defun by (step) (slice (none) (none) step))

(cl:defun plist-dict-pairs (plist)
  (cl:when plist
    (cl:list* (py.runtime::keyword-name (cl:first plist)) (cl:second plist)
              (plist-dict-pairs (cl:cddr plist)))))

(cl:defun plist-dict (cl:&rest plist)
  (cl:when (cl:oddp (cl:length plist))
    (cl:error 'python-value-error :type "ValueError"
              :message "PY:PLIST-DICT requires an even number of forms"))
  (cl:apply #'dict (plist-dict-pairs plist)))

(cl:defmacro columns (cl:&rest specs)
  (cl:dolist (spec specs)
    (cl:unless (cl:and (cl:consp spec) (cl:stringp (cl:first spec)))
      (cl:error "PY:COLUMNS requires each spec to start with a literal string column name")))
  `(dict ,@(cl:mapcan (cl:lambda (spec) (cl:list (cl:first spec) `(list ,@(cl:rest spec)))) specs)))

(cl:defmacro chain (object cl:&rest steps)
  (cl:let ((current object))
    (cl:dolist (step steps)
      (cl:let ((head (cl:symbol-name (cl:first step))) (rest-args (cl:rest step)))
        (cl:setf current
                 (cl:cond
                   ((cl:member head (cl:list "CALL" "CHAIN-CALL") :test #'cl:string=)
                    `(method ,current ,@rest-args))
                   ((cl:member head (cl:list "ATTR" "CHAIN-ATTR") :test #'cl:string=)
                    `(attr ,current ,@rest-args))
                   ((cl:member head (cl:list "GET" "CHAIN-SUBSCRIPT") :test #'cl:string=)
                    `(subscript ,current ,@rest-args))
                   (cl:t (cl:error "PY:CHAIN encountered unknown step head ~S" (cl:first step)))))))
    current))

(cl:defun chain-call (cl:&rest args)
  (cl:declare (cl:ignore args))
  (cl:error 'python-value-error :type "ValueError"
            :message "PY:CHAIN-* forms are only meaningful inside PY:CHAIN"))
(cl:defun chain-attr (cl:&rest args)
  (cl:declare (cl:ignore args))
  (cl:error 'python-value-error :type "ValueError"
            :message "PY:CHAIN-* forms are only meaningful inside PY:CHAIN"))
(cl:defun chain-subscript (cl:&rest args)
  (cl:declare (cl:ignore args))
  (cl:error 'python-value-error :type "ValueError"
            :message "PY:CHAIN-* forms are only meaningful inside PY:CHAIN"))

(cl:defun as-bool (object) (to-cl object :as :bool))
(cl:defun as-int (object) (to-cl object :as :integer))
(cl:defun as-float (object) (to-cl object :as :float))
(cl:defun as-string (object) (to-cl object :as :string))
(cl:defun as-list (object) (to-cl object :as :list))
(cl:defun as-vector (object) (to-cl object :as :vector))
(cl:defun as-dict (object) (to-cl object :as :dict))

(cl:defun none-p (object) (is object (none)))
(cl:defun some-p (object) (is-not object (none)))
