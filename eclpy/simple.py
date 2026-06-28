"""Fluent helpers for aggressively converting Python literals into Lisp syntax."""

from __future__ import annotations

import os
from typing import Any

from .encode import to_simple_expr, to_simple_literal
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
    """Convert one Python Simple API value into an S-expression."""
    return to_simple_expr(value)


def string(value: str) -> SExp:
    """Create an escaped Lisp string literal."""
    return SExp.string(value)


def symbol(name: str, package: str | None = None) -> SExp:
    """Create a Lisp symbol, uppercasing the name and optional package."""
    return SExp.symbol(name.upper(), package.upper() if package is not None else None)


def keyword(name: str) -> SExp:
    """Create a Lisp keyword symbol."""
    return SExp.keyword(name)


def array(items: Any) -> SExp:
    """Create a Lisp vector or multidimensional array literal."""
    if not _is_array_sequence(items):
        message = "array expects one list or tuple"
        raise TypeError(message)
    contents = tuple(items)
    shape = _array_shape(contents)
    if len(shape) <= 1:
        return SExp.raw("#(" + " ".join(str(to_simple_literal(item)) for item in contents) + ")")
    return SExp.raw(f"#{len(shape)}A{_array_contents(contents)}")


def path(value: str | os.PathLike[str]) -> SExp:
    """Create a Lisp pathname literal."""
    return SExp.raw(f"#p{SExp.string(os.fspath(value))}")


def quote(value: Any) -> SExp:
    """Create a quoted expression from a Simple API literal."""
    return SExp.quote(to_simple_literal(value))


def function(value: Any) -> SExp:
    """Create a function quote from a Simple API expression."""
    return SExp.function_quote(to_simple_expr(value))


def raw(source: str) -> SExp:
    """Embed raw Lisp source as an S-expression node."""
    return SExp.raw(source)


def _is_array_sequence(value: Any) -> bool:
    return isinstance(value, (tuple, list, List))


def _array_shape(value: Any) -> tuple[int, ...]:
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
    if _is_array_sequence(value):
        return "(" + " ".join(_array_contents(item) for item in tuple(value)) + ")"
    return str(to_simple_literal(value))
