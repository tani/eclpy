"""Pythonic package proxies for calling Lisp symbols.

Package proxies make Common Lisp symbols feel like Python attributes while
preserving Lisp lookup semantics. Attribute names are translated to candidate
symbol names, Lisp reports whether the symbol is callable/value/symbol, and the
proxy returns the corresponding Python wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .encode import to_syntax_api_expr
from .objects import Symbol
from .protocol import (
    decode_lookup,
    decode_value,
    lookup_kind,
    lookup_optional_string,
    lookup_string,
)
from .sexp import SExp

if TYPE_CHECKING:
    from .lisp import Lisp

_OPERATOR_NAMES = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "div": "/",
    "inc": "1+",
    "dec": "1-",
    "gt": ">",
    "lt": "<",
    "ge": ">=",
    "le": "<=",
    "ne": "/=",
    "sim": "=",
}


@dataclass(frozen=True)
class _CallableSymbol:
    """Callable proxy for a Lisp function-like symbol."""
    lisp: Lisp
    name: str
    package: str | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Evaluate the resolved Lisp symbol with Syntax API argument conversion."""
        parts = [SExp.symbol(self.name, self.package)]
        parts.extend(to_syntax_api_expr(arg) for arg in args)
        for key, value in kwargs.items():
            parts.append(SExp.keyword(key))
            parts.append(to_syntax_api_expr(value))
        return self.lisp._eval_sexp(SExp.list(*parts))

    def __repr__(self) -> str:
        """Render the symbol with package qualification when known."""
        package = f"{self.package}::" if self.package else ""
        return f"{package}{self.name}"


@dataclass(frozen=True)
class Package:
    """A Python view over a Common Lisp package.

    Attribute access performs a live lookup in the underlying Lisp image.
    Function-like symbols become callables; bound variables decode to Python
    values; ordinary symbols become :class:`eclpy.objects.Symbol`.
    """

    lisp: Lisp
    name: str

    def __getattr__(self, name: str) -> Any:
        """Resolve a Python attribute name to a Lisp package member."""
        if name.startswith("_"):
            raise AttributeError(name)
        return self._lookup(name)

    def symbol(self, name: str) -> Symbol:
        """Return a symbol interned in this package without checking bindings."""
        return Symbol(name.upper(), self.name)

    def _lookup(self, attribute: str) -> Any:
        """Try every translated symbol spelling for one Python attribute."""
        for symbol_name in _attribute_candidates(attribute):
            form = SExp.list(
                SExp.atom("ecl-python:lookup-symbol"),
                SExp.string(self.name),
                SExp.string(symbol_name),
            )
            result = decode_lookup(self.lisp._eval_helper(form))
            match lookup_kind(result):
                case "missing":
                    continue
                case "callable":
                    return _CallableSymbol(
                        self.lisp,
                        lookup_string(result, "name"),
                        lookup_optional_string(result, "package"),
                    )
                case "value":
                    return decode_value(result["value"], self.lisp)
                case "symbol":
                    return Symbol(
                        lookup_string(result, "name"),
                        lookup_optional_string(result, "package"),
                    )
        raise AttributeError(attribute)

    def __repr__(self) -> str:
        """Return a readable package proxy representation."""
        return f"Package({self.name!r})"


def find_package(lisp: Lisp, name: str) -> Package:
    """Return a Python view over a Common Lisp package.

    ``name`` is uppercased because Common Lisp package names are conventionally
    uppercase and ECL's default reader preserves that convention.
    """
    return Package(lisp, name.upper())


def _attribute_candidates(attribute: str) -> list[str]:
    """Return candidate Lisp symbol names for a Python attribute."""
    if attribute in _OPERATOR_NAMES:
        return [_OPERATOR_NAMES[attribute]]

    base = _attribute_to_symbol_name(attribute)
    if base.startswith("*") and base.endswith("*"):
        return [base]
    return [base, f"*{base}*"]


def _attribute_to_symbol_name(attribute: str) -> str:
    """Translate Python-friendly attribute spelling to a Lisp symbol name."""
    lowered = attribute.lower()
    suffixes = (
        ("tilde", "~"),
        ("ge", ">="),
        ("le", "<="),
        ("ne", "/="),
        ("gt", ">"),
        ("lt", "<"),
        ("sim", "="),
    )
    for suffix, replacement in suffixes:
        if lowered.endswith(suffix) and len(attribute) > len(suffix):
            prefix = attribute[: -len(suffix)]
            return prefix.replace("_", "-").upper() + replacement
    return attribute.replace("_", "-").upper()
