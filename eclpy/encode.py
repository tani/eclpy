"""Encode Python values as explicit Lisp S-expression syntax.

The encoder has two public modes. Syntax API conversion treats Python strings
as Lisp symbols and tuples/lists as code-shaped forms. Data conversion treats
Python values as literal data and emits Lisp code that reconstructs equivalent
objects inside ECL.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Protocol, runtime_checkable

from .errors import EclError
from .objects import Cons, List, Reference, Symbol
from .sexp import SExp


@runtime_checkable
class _CallableSymbolLike(Protocol):
    name: str
    package: str | None

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class _PackageLike(Protocol):
    lisp: Any
    name: str


def to_syntax_api_expr(value: Any) -> SExp:
    """Convert one Syntax API value into a Lisp expression.

    Strings become symbols, non-empty tuples/lists whose first item looks like a
    callable become forms, and ordinary Python data falls back to
    :func:`to_data_expr`.
    """
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
            return to_data_expr(value)


def to_syntax_api_literal(value: Any) -> SExp:
    """Convert one Syntax API value into literal Lisp syntax.

    This is used under quote-like contexts where sequences should become list
    contents instead of executable forms.
    """
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
            return to_data_expr(value)


def to_syntax_api_literal_list(items: tuple[Any, ...]) -> SExp:
    """Convert Python sequence contents into a proper Lisp literal list."""
    if not items:
        return SExp.atom("nil")
    return SExp.list(*(to_syntax_api_literal(item) for item in items))


def to_data_expr(value: Any) -> SExp:
    """Convert a Python value into Lisp code that reconstructs data.

    ``None`` and ``False`` become ``nil``; ``True`` becomes ``t``; tuples and
    :class:`eclpy.objects.List` become proper lists; Python lists become
    vectors; dictionaries become association lists. Non-finite floats are
    rejected because ECL reader portability is more important than guessing an
    implementation-specific spelling.
    """
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
            return SExp.float(_finite_float(number))
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
        case dict() as mapping:
            return SExp.list(
                SExp.symbol("LIST"),
                *(
                    SExp.list(SExp.symbol("CONS"), to_data_expr(key), to_data_expr(item))
                    for key, item in mapping.items()
                ),
            )
        case _:
            message = f"cannot convert {type(value).__name__} to Lisp"
            raise TypeError(message)


def _is_callable_symbol(value: Any) -> bool:
    """Return whether ``value`` behaves like a package-proxy callable symbol."""
    return isinstance(value, _CallableSymbolLike)


def _is_package(value: Any) -> bool:
    """Return whether ``value`` behaves like a Common Lisp package proxy."""
    return isinstance(value, _PackageLike)


def _finite_float(value: float) -> float:
    """Reject infinities and NaNs before rendering a Lisp float literal."""
    if not math.isfinite(value):
        message = "cannot convert a non-finite float to Lisp"
        raise TypeError(message)
    return value
