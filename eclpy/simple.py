"""Fluent helpers for aggressively converting Python literals into Lisp syntax."""

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
    """Create an escaped Lisp string literal."""
    return SExp.string(value)


def symbol(name: str, package: str | None = None) -> SExp:
    """Create a Lisp symbol, uppercasing the name and optional package."""
    return SExp.symbol(name.upper(), package.upper() if package is not None else None)


def keyword(name: str) -> SExp:
    """Create a Lisp keyword symbol."""
    return SExp.keyword(name)


def quote(value: Any) -> SExp:
    """Create a quoted expression from a Simple API literal."""
    return SExp.quote(_literal_expr(value))


def function(value: Any) -> SExp:
    """Create a function quote from a Simple API expression."""
    return SExp.function_quote(_expr(value))


def raw(source: str) -> SExp:
    """Embed raw Lisp source as an S-expression node."""
    return SExp.raw(source)


def _expr(value: Any) -> SExp:
    match value:
        case SExp() as sexp:
            return sexp
        case Symbol() as value_symbol:
            return SExp.symbol(value_symbol.name, value_symbol.package)
        case str() as name:
            return symbol(name)
        case None | False:
            return SExp.atom("nil")
        case True:
            return SExp.atom("t")
        case int() as integer if not isinstance(value, bool):
            return SExp.integer(integer)
        case Fraction() as ratio:
            return SExp.ratio(ratio)
        case float() as number:
            return SExp.float(number)
        case (tuple() | list() | List()) as sequence:
            items = tuple(sequence)
            if not items:
                return SExp.atom("nil")
            if _looks_like_form_head(items[0]):
                return SExp.list(*(_expr(item) for item in items))
            return SExp.quote(_literal_list(items))
        case _:
            message = f"cannot convert {type(value).__name__} to Lisp simple expression"
            raise TypeError(message)


def _literal_expr(value: Any) -> SExp:
    match value:
        case SExp() as sexp:
            return sexp
        case Symbol() as value_symbol:
            return SExp.symbol(value_symbol.name, value_symbol.package)
        case str() as name:
            return symbol(name)
        case None | False:
            return SExp.atom("nil")
        case True:
            return SExp.atom("t")
        case int() as integer if not isinstance(value, bool):
            return SExp.integer(integer)
        case Fraction() as ratio:
            return SExp.ratio(ratio)
        case float() as number:
            return SExp.float(number)
        case (tuple() | list() | List()) as sequence:
            return _literal_list(tuple(sequence))
        case _:
            message = f"cannot convert {type(value).__name__} to Lisp simple literal"
            raise TypeError(message)


def _literal_list(items: tuple[Any, ...]) -> SExp:
    if not items:
        return SExp.atom("nil")
    return SExp.list(*(_literal_expr(item) for item in items))


def _looks_like_form_head(value: Any) -> bool:
    return isinstance(value, (str, Symbol, SExp))
