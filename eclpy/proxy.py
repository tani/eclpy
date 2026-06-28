"""Pythonic package proxies for calling Lisp symbols."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .decode import decode_value, node_tag, optional_string, symbol_atom
from .encode import to_simple_expr
from .objects import Symbol
from .sexp import SExp

if TYPE_CHECKING:
    from .api import Lisp

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
    lisp: Lisp
    name: str
    package: str | None = None
    kind: str = "function"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        parts = [SExp.symbol(self.name, self.package)]
        parts.extend(to_simple_expr(arg) for arg in args)
        for key, value in kwargs.items():
            parts.append(SExp.keyword(key))
            parts.append(to_simple_expr(value))
        return self.lisp._eval_sexp(SExp.list(*parts))

    def __repr__(self) -> str:
        package = f"{self.package}::" if self.package else ""
        return f"{package}{self.name}"


@dataclass(frozen=True)
class Package:
    """A Python view over a Common Lisp package."""

    lisp: Lisp
    name: str

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._lookup(name)

    def symbol(self, name: str) -> Symbol:
        """Return a symbol interned in this package."""
        return Symbol(name.upper(), self.name)

    def _lookup(self, attribute: str) -> Any:
        for symbol_name in _attribute_candidates(attribute):
            form = SExp.list(
                SExp.atom("ecl-python:lookup-symbol"),
                SExp.string(self.name),
                SExp.string(symbol_name),
            )
            result = self.lisp._eval_helper(form)
            match node_tag(result):
                case ":MISSING":
                    continue
                case ":CALLABLE":
                    kind = symbol_atom(result[1]).lower().lstrip(":")
                    return _CallableSymbol(
                        self.lisp,
                        str(result[2]),
                        optional_string(result[3]),
                        kind,
                    )
                case ":VALUE":
                    return decode_value(result[1], self.lisp)
                case ":SYMBOL":
                    return Symbol(str(result[1]), optional_string(result[2]))
        raise AttributeError(attribute)

    def __repr__(self) -> str:
        return f"Package({self.name!r})"


def find_package(lisp: Lisp, name: str) -> Package:
    """Return a Python view over a Common Lisp package."""
    return Package(lisp, name.upper())


def _attribute_candidates(attribute: str) -> list[str]:
    if attribute in _OPERATOR_NAMES:
        return [_OPERATOR_NAMES[attribute]]

    base = _attribute_to_symbol_name(attribute)
    if base.startswith("*") and base.endswith("*"):
        return [base]
    return [base, f"*{base}*"]


def _attribute_to_symbol_name(attribute: str) -> str:
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
