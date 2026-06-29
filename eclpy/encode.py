"""Encode Python values as explicit Lisp S-expression syntax."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from .objects import Cons, List, Reference, Symbol
from .session import EclError
from .sexp import SExp


def to_syntax_expr(value: Any) -> SExp:
    """Convert a Python value into syntax-level Lisp expression input."""
    match value:
        case SExp() as sexp:
            return sexp
        case tuple() as items:
            if not items:
                return SExp.atom("nil")
            head = items[0]
            if isinstance(head, str):
                message = (
                    "Lisp form operators must be eclpy.Symbol instances; "
                    f"use Symbol({head!r}) instead of {head!r}"
                )
                raise TypeError(message)
            return SExp.list(*(to_syntax_expr(item) for item in items))
        case Symbol() as symbol:
            return SExp.symbol(symbol.name, symbol.package)
        case _:
            return to_data_expr(value)


def to_syntax_api_expr(value: Any) -> SExp:
    """Convert a Python Syntax API value into a Lisp expression."""
    match value:
        case SExp() as sexp:
            return sexp
        case Symbol() as symbol:
            return SExp.symbol(symbol.name, symbol.package)
        case str() as name:
            return SExp.symbol(name.upper())
        case (tuple() | list() | List()) as sequence:
            items = tuple(sequence)
            if not items:
                return SExp.atom("nil")
            if isinstance(items[0], (str, Symbol, SExp)):
                return SExp.list(*(to_syntax_api_expr(item) for item in items))
            return SExp.quote(to_syntax_api_literal_list(items))
        case _:
            try:
                return to_data_expr(value)
            except TypeError as exc:
                message = f"cannot convert {type(value).__name__} to Lisp syntax expression"
                raise TypeError(message) from exc


def to_syntax_api_literal(value: Any) -> SExp:
    """Convert a Python Syntax API value into a literal Lisp expression."""
    match value:
        case SExp() as sexp:
            return sexp
        case Symbol() as symbol:
            return SExp.symbol(symbol.name, symbol.package)
        case str() as name:
            return SExp.symbol(name.upper())
        case (tuple() | list() | List()) as sequence:
            return to_syntax_api_literal_list(tuple(sequence))
        case _:
            try:
                return to_data_expr(value)
            except TypeError as exc:
                message = f"cannot convert {type(value).__name__} to Lisp syntax literal"
                raise TypeError(message) from exc


def to_syntax_api_literal_list(items: tuple[Any, ...]) -> SExp:
    """Convert Python sequence contents into a proper Lisp literal list."""
    if not items:
        return SExp.atom("nil")
    return SExp.list(*(to_syntax_api_literal(item) for item in items))


def to_data_expr(value: Any) -> SExp:
    """Convert a Python value into a Lisp expression that reconstructs data."""
    match value:
        case SExp() as sexp:
            return sexp
        case None | False:
            return SExp.atom("nil")
        case True:
            return SExp.atom("t")
        case int() as integer:
            return SExp.integer(integer)
        case Fraction() as ratio:
            return SExp.ratio(ratio)
        case float() as number:
            return SExp.float(number)
        case str() as string:
            return SExp.string(string)
        case Symbol() as symbol:
            return SExp.quote(SExp.symbol(symbol.name, symbol.package))
        case _ if _is_callable_symbol(value):
            return SExp.function_quote(SExp.symbol(value.name, value.package))
        case _ if _is_package(value):
            return SExp.list(SExp.symbol("FIND-PACKAGE"), SExp.string(value.name))
        case Reference() as reference:
            if reference.released:
                message = "cannot pass a released Lisp reference"
                raise EclError(message)
            return SExp.list(SExp.atom("ecl-python:value"), SExp.integer(reference.object_id))
        case List() as items:
            if not items:
                return SExp.atom("nil")
            return SExp.list(SExp.symbol("LIST"), *(to_data_expr(item) for item in items))
        case tuple() as items:
            if not items:
                return SExp.atom("nil")
            return SExp.list(SExp.symbol("LIST"), *(to_data_expr(item) for item in items))
        case list() as items:
            return SExp.list(SExp.symbol("VECTOR"), *(to_data_expr(item) for item in items))
        case Cons() as cons:
            return SExp.list(
                SExp.symbol("CONS"),
                to_data_expr(cons.car),
                to_data_expr(cons.cdr),
            )
        case _:
            message = f"cannot convert {type(value).__name__} to Lisp"
            raise TypeError(message)


def keyword_parts(kwargs: dict[str, Any], *, values_as_expr: bool) -> list[SExp]:
    """Encode Python keyword arguments as alternating Lisp keyword/value forms."""
    parts: list[SExp] = []
    for key, value in kwargs.items():
        parts.append(SExp.keyword(key))
        parts.append(to_syntax_expr(value) if values_as_expr else to_data_expr(value))
    return parts


def _is_callable_symbol(value: Any) -> bool:
    return (
        value.__class__.__name__ == "_CallableSymbol"
        and hasattr(value, "name")
        and hasattr(value, "package")
    )


def _is_package(value: Any) -> bool:
    return (
        value.__class__.__name__ == "Package"
        and hasattr(value, "lisp")
        and hasattr(value, "name")
    )
