"""Build Lisp S-expression syntax trees before string rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fractions import Fraction

_SAFE_SYMBOL_RE = re.compile(r"^[A-Za-z0-9!$%&*+\-./:<=>?@^_~]+$")
_INTEGER_TOKEN_RE = re.compile(r"^[+-]?\d+$")
_RATIO_TOKEN_RE = re.compile(r"^[+-]?\d+/\d+$")
_FLOAT_TOKEN_RE = re.compile(
    r"^[+-]?((\d+\.\d*|\.\d+)([eEsSfFdDlL][+-]?\d+)?|\d+[eEsSfFdDlL][+-]?\d+)$"
)


class SExp:
    """A Lisp S-expression syntax tree."""

    @staticmethod
    def atom(token: str) -> SExp:
        """Create an unescaped atom from an already-valid Lisp token."""
        return _SAtom(token)

    @staticmethod
    def symbol(name: str, package: str | None = None) -> SExp:
        """Create a Lisp symbol reference."""
        token = _symbol_token(name)
        if package is None:
            return _SAtom(token)
        if package.upper() == "KEYWORD":
            return _SAtom(":" + token.lstrip(":"))
        return _SAtom(f"{_symbol_token(package)}::{token}")

    @staticmethod
    def keyword(name: str) -> SExp:
        """Create a Lisp keyword symbol."""
        return _SAtom(":" + _symbol_token(name.lstrip(":").replace("_", "-").upper()))

    @staticmethod
    def integer(value: int) -> SExp:
        """Create an integer literal."""
        return _SAtom(str(value))

    @staticmethod
    def ratio(value: Fraction) -> SExp:
        """Create a rational number literal."""
        return _SAtom(f"{value.numerator}/{value.denominator}")

    @staticmethod
    def float(value: float) -> SExp:
        """Create a floating-point literal."""
        return _SAtom(repr(value))

    @staticmethod
    def string(value: str) -> SExp:
        """Create an escaped Lisp string literal."""
        return _SString(value)

    @staticmethod
    def raw(source: str) -> SExp:
        """Embed raw Lisp source as an S-expression node."""
        return _SRaw(source)

    @staticmethod
    def list(*items: SExp) -> SExp:
        """Create a proper Lisp list expression."""
        return _SList(tuple(items))

    @staticmethod
    def quote(value: SExp) -> SExp:
        """Create a quoted Lisp expression."""
        return _SQuote(value)

    @staticmethod
    def function_quote(value: SExp) -> SExp:
        """Create a function-quoted Lisp expression."""
        return _SFunctionQuote(value)

    def __str__(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class _SAtom(SExp):
    token: str

    def __str__(self) -> str:
        return self.token


@dataclass(frozen=True)
class _SString(SExp):
    value: str

    def __str__(self) -> str:
        return _string_literal(self.value)


@dataclass(frozen=True)
class _SRaw(SExp):
    source: str

    def __str__(self) -> str:
        return self.source


@dataclass(frozen=True)
class _SList(SExp):
    items: tuple[SExp, ...]

    def __str__(self) -> str:
        if not self.items:
            return "nil"
        return "(" + " ".join(str(item) for item in self.items) + ")"


@dataclass(frozen=True)
class _SQuote(SExp):
    value: SExp

    def __str__(self) -> str:
        return "'" + str(self.value)


@dataclass(frozen=True)
class _SFunctionQuote(SExp):
    value: SExp

    def __str__(self) -> str:
        return "#'" + str(self.value)


def _symbol_token(name: str) -> str:
    if _SAFE_SYMBOL_RE.match(name) and not _looks_like_number(name):
        return name
    return "|" + name.replace("\\", "\\\\").replace("|", "\\|") + "|"


def _looks_like_number(token: str) -> bool:
    return (
        bool(_INTEGER_TOKEN_RE.match(token))
        or bool(_RATIO_TOKEN_RE.match(token))
        or bool(_FLOAT_TOKEN_RE.match(token))
    )


def _string_literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
