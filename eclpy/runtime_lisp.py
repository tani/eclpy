from __future__ import annotations


HELPER_SOURCE = r"""
(defpackage #:ecl-python
  (:use #:cl)
  (:export #:evaluate #:lookup-symbol #:release-object #:release-all-objects
           #:serialize #:value))

(in-package #:ecl-python)

(defvar *objects* (make-hash-table :test #'eql))
(defvar *next-id* 0)

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

(defun condition-type-name (condition)
  (prin1-to-string (type-of condition)))

(defun condition-message (condition)
  (format nil "~A" condition))

(defun evaluate (thunk)
  (handler-case
      (list :ok (serialize (funcall thunk)))
    (condition (condition)
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
"""
