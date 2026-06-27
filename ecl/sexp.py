from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import re


_SAFE_SYMBOL_RE = re.compile(r"^[A-Za-z0-9!$%&*+\-./:<=>?@^_~]+$")


class SExp:
    """A Lisp S-expression syntax tree."""

    @staticmethod
    def atom(token: str) -> SExp:
        return _SAtom(token)

    @staticmethod
    def symbol(name: str, package: str | None = None) -> SExp:
        token = _symbol_token(name)
        if package is None:
            return _SAtom(token)
        if package.upper() == "KEYWORD":
            return _SAtom(":" + token.lstrip(":"))
        return _SAtom(f"{_symbol_token(package)}::{token}")

    @staticmethod
    def keyword(name: str) -> SExp:
        return _SAtom(":" + _symbol_token(name.lstrip(":").replace("_", "-").upper()))

    @staticmethod
    def integer(value: int) -> SExp:
        return _SAtom(str(value))

    @staticmethod
    def ratio(value: Fraction) -> SExp:
        return _SAtom(f"{value.numerator}/{value.denominator}")

    @staticmethod
    def float(value: float) -> SExp:
        return _SAtom(repr(value))

    @staticmethod
    def string(value: str) -> SExp:
        return _SString(value)

    @staticmethod
    def raw(source: str) -> SExp:
        return _SRaw(source)

    @staticmethod
    def list(*items: SExp) -> SExp:
        return _SList(tuple(items))

    @staticmethod
    def quote(value: SExp) -> SExp:
        return _SQuote(value)

    @staticmethod
    def function_quote(value: SExp) -> SExp:
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
    if _SAFE_SYMBOL_RE.match(name):
        return name
    return "|" + name.replace("\\", "\\\\").replace("|", "\\|") + "|"


def _string_literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
