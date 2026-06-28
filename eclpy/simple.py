"""Fluent helpers for aggressively converting Python literals into Lisp syntax."""

from __future__ import annotations

from typing import Any

from .api import Lisp, LispFunction, LispPackage
from .encode import to_data_expr
from .objects import List, Symbol
from .sexp import SExp

__all__ = [
    "expr",
    "find_function",
    "find_package",
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


def find_function(
    lisp: Lisp,
    name: str,
    package: str | LispPackage | None = None,
) -> LispFunction:
    """Return a callable proxy for a Lisp function."""
    package_name = package.name if isinstance(package, LispPackage) else package
    return lisp._find_function(name, package_name)


def find_package(lisp: Lisp, name: str) -> LispPackage:
    """Return a Python view over a Common Lisp package."""
    return lisp._find_package(name)


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
    if isinstance(value, LispFunction):
        return _expr(value)
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
        case (tuple() | list() | List()) as sequence:
            items = tuple(sequence)
            if not items:
                return SExp.atom("nil")
            if isinstance(items[0], LispFunction):
                return SExp.list(
                    SExp.symbol("FUNCALL"),
                    to_data_expr(items[0]),
                    *(_expr(item) for item in items[1:]),
                )
            if isinstance(items[0], (str, Symbol, SExp)):
                return SExp.list(*(_expr(item) for item in items))
            return SExp.quote(_literal_list(items))
        case _:
            try:
                return to_data_expr(value)
            except TypeError as exc:
                message = f"cannot convert {type(value).__name__} to Lisp simple expression"
                raise TypeError(message) from exc


def _literal_expr(value: Any) -> SExp:
    match value:
        case SExp() as sexp:
            return sexp
        case Symbol() as value_symbol:
            return SExp.symbol(value_symbol.name, value_symbol.package)
        case str() as name:
            return symbol(name)
        case (tuple() | list() | List()) as sequence:
            return _literal_list(tuple(sequence))
        case _:
            try:
                return to_data_expr(value)
            except TypeError as exc:
                message = f"cannot convert {type(value).__name__} to Lisp simple literal"
                raise TypeError(message) from exc


def _literal_list(items: tuple[Any, ...]) -> SExp:
    if not items:
        return SExp.atom("nil")
    return SExp.list(*(_literal_expr(item) for item in items))
