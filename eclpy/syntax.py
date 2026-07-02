"""Fluent helpers for converting Python literals into Lisp syntax.

This module is the ergonomic layer above :mod:`eclpy.sexp`. It deliberately
interprets strings as Lisp symbols and Python sequences as Lisp forms when that
looks natural; use :class:`eclpy.SExp` directly when every token must be spelled
explicitly.
"""

from __future__ import annotations

import os
from typing import Any

from .encode import to_syntax_api_expr, to_syntax_api_literal
from .objects import List
from .sexp import SExp

__all__ = [
    "array",
    "expr",
    "function",
    "keyword",
    "path",
    "quote",
    "raw",
    "string",
    "symbol",
]


def expr(value: Any) -> SExp:
    """Convert one Python Syntax API value into an S-expression.

    Example: ``expr((\"+\", 1, 2))`` renders as ``(+ 1 2)`` while
    ``expr((1, 2))`` renders as a quoted literal list.
    """
    return to_syntax_api_expr(value)


def string(value: str) -> SExp:
    """Create an escaped Lisp string literal.

    Unlike :func:`expr`, this keeps a Python string as string data instead of
    converting it to a Lisp symbol.
    """
    return SExp.string(value)


def symbol(name: str, package: str | None = None) -> SExp:
    """Create a Lisp symbol, uppercasing the name and optional package."""
    return SExp.symbol(name.upper(), package.upper() if package is not None else None)


def keyword(name: str) -> SExp:
    """Create a Lisp keyword symbol from a Python-friendly name."""
    return SExp.keyword(name)


def array(items: Any) -> SExp:
    """Create a Lisp vector or multidimensional array literal.

    Nested Python sequences must be rectangular. One-dimensional input renders
    as ``#(...)``; deeper input renders as ``#nA(...)``.
    """
    if not _is_array_sequence(items):
        message = "array expects one list or tuple"
        raise TypeError(message)
    contents = tuple(items)
    shape = _array_shape(contents)
    if len(shape) <= 1:
        values = " ".join(str(to_syntax_api_literal(item)) for item in contents)
        return SExp.raw(f"#({values})")
    return SExp.raw(f"#{len(shape)}A{_array_contents(contents)}")


def path(value: str | os.PathLike[str]) -> SExp:
    """Create a Lisp pathname reader literal from a host path."""
    return SExp.raw(f"#p{SExp.string(os.fspath(value))}")


def quote(value: Any) -> SExp:
    """Create a quoted expression from a Syntax API literal."""
    return SExp.quote(to_syntax_api_literal(value))


def function(value: Any) -> SExp:
    """Create a function quote from a Syntax API expression."""
    return SExp.function_quote(to_syntax_api_expr(value))


def raw(source: str) -> SExp:
    """Embed raw trusted Lisp source as an S-expression node."""
    return SExp.raw(source)


def _is_array_sequence(value: Any) -> bool:
    """Return whether ``value`` can be traversed as an array literal."""
    return isinstance(value, (tuple, list, List))


def _array_shape(value: Any) -> tuple[int, ...]:
    """Return the rectangular shape for a nested Lisp array literal."""
    if not _is_array_sequence(value):
        return ()

    items = tuple(value)
    if not items:
        return (0,)

    child_shapes = [_array_shape(item) for item in items]
    if not any(child_shapes):
        return (len(items),)
    if not all(child_shapes) or any(shape != child_shapes[0] for shape in child_shapes):
        message = "multidimensional Lisp array literals must be rectangular"
        raise ValueError(message)
    return (len(items), *child_shapes[0])


def _array_contents(value: Any) -> str:
    """Render nested array contents after shape validation."""
    if _is_array_sequence(value):
        return "(" + " ".join(_array_contents(item) for item in tuple(value)) + ")"
    return str(to_syntax_api_literal(value))
