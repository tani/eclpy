Python API reference
====================

This page is generated from the docstrings in ``eclpy``. The docstrings use
Sphinx/reStructuredText roles so the public contracts, lifetime rules, and
boundary semantics live next to the code they describe.

Public package exports
----------------------

.. automodule:: eclpy

The package root re-exports the stable public classes and helpers documented in
the sections below: ``Lisp``, ``EclSession``, ``SExp``, ``Cons``, ``List``,
``Package``, ``Reference``, ``Symbol``, and ``EclError``.

High-level Lisp facade
----------------------

.. automodule:: eclpy.lisp
   :members:
   :undoc-members:

Syntax builders
---------------

.. automodule:: eclpy.syntax
   :members:
   :undoc-members:

S-expression tree
-----------------

.. automodule:: eclpy.sexp
   :members:
   :undoc-members:
   :private-members: _SAtom, _SString, _SRaw, _SList, _SQuote, _SFunctionQuote

Python-to-Lisp encoding
-----------------------

.. automodule:: eclpy.encode
   :members:
   :undoc-members:

Lisp-to-Python protocol decoding
--------------------------------

.. automodule:: eclpy.protocol
   :members:
   :undoc-members:

Lisp value objects
------------------

.. automodule:: eclpy.objects
   :members:
   :undoc-members:

Package proxies
---------------

.. automodule:: eclpy.proxy
   :members:
   :undoc-members:
   :private-members: _CallableSymbol

Low-level session and host boundary
-----------------------------------

.. automodule:: eclpy.session
   :members:
   :undoc-members:

.. automodule:: eclpy.hostenv
   :members:
   :undoc-members:

.. automodule:: eclpy.wasmmem
   :members:
   :undoc-members:

Lisp source loaders
-------------------

.. automodule:: eclpy.runtime_lisp
   :members:
   :undoc-members:

.. automodule:: eclpy.python_lisp
   :members:
   :undoc-members:

.. automodule:: eclpy.errors
   :members:
   :undoc-members:
