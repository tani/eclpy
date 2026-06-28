from __future__ import annotations

from fractions import Fraction
from typing import Any

from .objects import Cons, LispReference, List, Symbol
from .session import EclError
from .sexp import SExp


def to_syntax_expr(value: Any) -> SExp:
    if isinstance(value, SExp):
        return value
    if isinstance(value, tuple):
        if not value:
            return SExp.atom("nil")
        if isinstance(value[0], str):
            raise TypeError(
                "Lisp form operators must be eclpy.Symbol instances; "
                f"use Symbol({value[0]!r}) instead of {value[0]!r}"
            )
        return SExp.list(*(to_syntax_expr(item) for item in value))
    if isinstance(value, Symbol):
        return SExp.symbol(value.name, value.package)
    return to_data_expr(value)


def to_data_expr(value: Any) -> SExp:
    if isinstance(value, SExp):
        return value
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
    if isinstance(value, str):
        return SExp.string(value)
    if isinstance(value, Symbol):
        return SExp.quote(SExp.symbol(value.name, value.package))
    if _is_lisp_function(value):
        return SExp.function_quote(SExp.symbol(value.name, value.package))
    if isinstance(value, LispReference):
        if value.released:
            raise EclError("cannot pass a released Lisp reference")
        return SExp.list(SExp.atom("ecl-python:value"), SExp.integer(value.object_id))
    if isinstance(value, List):
        if not value:
            return SExp.atom("nil")
        return SExp.list(SExp.symbol("LIST"), *(to_data_expr(item) for item in value))
    if isinstance(value, tuple):
        if not value:
            return SExp.atom("nil")
        return SExp.list(SExp.symbol("LIST"), *(to_data_expr(item) for item in value))
    if isinstance(value, list):
        return SExp.list(SExp.symbol("VECTOR"), *(to_data_expr(item) for item in value))
    if isinstance(value, Cons):
        return SExp.list(
            SExp.symbol("CONS"),
            to_data_expr(value.car),
            to_data_expr(value.cdr),
        )
    raise TypeError(f"cannot convert {type(value).__name__} to Lisp")


def keyword_parts(kwargs: dict[str, Any], *, values_as_expr: bool) -> list[SExp]:
    parts: list[SExp] = []
    for key, value in kwargs.items():
        parts.append(SExp.keyword(key))
        parts.append(to_syntax_expr(value) if values_as_expr else to_data_expr(value))
    return parts


def _is_lisp_function(value: Any) -> bool:
    return (
        value.__class__.__name__ == "LispFunction"
        and hasattr(value, "lisp")
        and hasattr(value, "name")
        and hasattr(value, "package")
    )
