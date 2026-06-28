from __future__ import annotations

from fractions import Fraction
from typing import Any

from .objects import List, Symbol
from .sexp import SExp

__all__ = [
    "expr",
    "function",
    "keyword",
    "quote",
    "raw",
    "string",
    "symbol",
]


def expr(value: Any) -> SExp:
    """Convert one Python Simple API value into an S-expression."""

    return _expr(value)


def string(value: str) -> SExp:
    return SExp.string(value)


def symbol(name: str, package: str | None = None) -> SExp:
    return SExp.symbol(name.upper(), package.upper() if package is not None else None)


def keyword(name: str) -> SExp:
    return SExp.keyword(name)


def quote(value: Any) -> SExp:
    return SExp.quote(_literal_expr(value))


def function(value: Any) -> SExp:
    return SExp.function_quote(_expr(value))


def raw(source: str) -> SExp:
    return SExp.raw(source)


def _expr(value: Any) -> SExp:
    if isinstance(value, SExp):
        return value
    if isinstance(value, Symbol):
        return SExp.symbol(value.name, value.package)
    if isinstance(value, str):
        return symbol(value)
    if value is None or value is False:
        return SExp.atom("nil")
    if value is True:
        return SExp.atom("t")
    if isinstance(value, int) and not isinstance(value, bool):
        return SExp.integer(value)
    if isinstance(value, Fraction):
        return SExp.ratio(value)
    if isinstance(value, float):
        return SExp.float(value)
    if isinstance(value, (tuple, list, List)):
        items = tuple(value)
        if not items:
            return SExp.atom("nil")
        if _looks_like_form_head(items[0]):
            return SExp.list(*(_expr(item) for item in items))
        return SExp.quote(_literal_list(items))
    raise TypeError(f"cannot convert {type(value).__name__} to Lisp simple expression")


def _literal_expr(value: Any) -> SExp:
    if isinstance(value, SExp):
        return value
    if isinstance(value, Symbol):
        return SExp.symbol(value.name, value.package)
    if isinstance(value, str):
        return symbol(value)
    if value is None or value is False:
        return SExp.atom("nil")
    if value is True:
        return SExp.atom("t")
    if isinstance(value, int) and not isinstance(value, bool):
        return SExp.integer(value)
    if isinstance(value, Fraction):
        return SExp.ratio(value)
    if isinstance(value, float):
        return SExp.float(value)
    if isinstance(value, (tuple, list, List)):
        return _literal_list(tuple(value))
    raise TypeError(f"cannot convert {type(value).__name__} to Lisp simple literal")


def _literal_list(items: tuple[Any, ...]) -> SExp:
    if not items:
        return SExp.atom("nil")
    return SExp.list(*(_literal_expr(item) for item in items))


def _looks_like_form_head(value: Any) -> bool:
    return isinstance(value, (str, Symbol, SExp))
