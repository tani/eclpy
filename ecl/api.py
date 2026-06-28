from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Self

from .decode import decode_result, decode_value, node_tag, optional_string, symbol_atom
from .encode import keyword_parts, to_data_expr, to_syntax_expr
from .objects import LispReference, Symbol
from .reader import parse_one
from .runtime_lisp import HELPER_SOURCE
from .session import EclError, EclSession
from .sexp import SExp


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
class LispFunction:
    lisp: Lisp
    name: str
    package: str | None = None
    kind: str = "function"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.kind in {"macro", "special"}:
            parts = [self._operator()]
            parts.extend(to_syntax_expr(arg) for arg in args)
            parts.extend(keyword_parts(kwargs, values_as_expr=True))
            return self.lisp._eval_sexp(SExp.list(*parts))

        parts = [self._operator()]
        parts.extend(to_data_expr(arg) for arg in args)
        parts.extend(keyword_parts(kwargs, values_as_expr=False))
        return self.lisp._eval_sexp(SExp.list(*parts))

    def _operator(self) -> SExp:
        return SExp.symbol(self.name, self.package)

    def __repr__(self) -> str:
        package = f"{self.package}::" if self.package else ""
        return f"LispFunction({package}{self.name})"


@dataclass(frozen=True)
class Package:
    lisp: Lisp
    name: str

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._lookup(name)

    def function(self, name: str) -> LispFunction:
        return LispFunction(self.lisp, name.upper(), self.name)

    def symbol(self, name: str) -> Symbol:
        return Symbol(name.upper(), self.name)

    def _lookup(self, attribute: str) -> Any:
        for symbol_name in _attribute_candidates(attribute):
            form = SExp.list(
                SExp.atom("ecl-python:lookup-symbol"),
                SExp.string(self.name),
                SExp.string(symbol_name),
            )
            result = self.lisp._eval_helper(form)
            tag = node_tag(result)
            if tag == ":MISSING":
                continue
            if tag == ":CALLABLE":
                kind = symbol_atom(result[1]).lower().lstrip(":")
                return LispFunction(self.lisp, str(result[2]), optional_string(result[3]), kind)
            if tag == ":VALUE":
                return decode_value(result[1], self.lisp)
            if tag == ":SYMBOL":
                return Symbol(str(result[1]), optional_string(result[2]))
        raise AttributeError(attribute)

    def __repr__(self) -> str:
        return f"Package({self.name!r})"


class Lisp:
    """A cl4py-like interface to an ECL WebAssembly session."""

    def __init__(
        self,
        wasm_path: str | os.PathLike[str] | None = None,
        *,
        session: EclSession | None = None,
    ) -> None:
        self.session = session or EclSession(wasm_path)
        self._owns_session = session is None
        self._closed = False
        self._references: dict[int, LispReference] = {}
        self.session.eval(HELPER_SOURCE)

    def eval(self, form: Any) -> Any:
        """Evaluate an explicit S-expression."""

        if not isinstance(form, SExp):
            raise TypeError("Lisp.eval only accepts SExp; use ecl.SExp.* or ecl.simple.expr(...)")
        return self._eval_sexp(form)

    def function(self, name: str, package: str | None = None) -> LispFunction:
        return LispFunction(self, name.upper(), package)

    def find_package(self, name: str) -> Package:
        return Package(self, name.upper())

    def close(self) -> None:
        if self._closed:
            return
        self._release_all_references()
        if self._owns_session:
            self.session.close()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _eval_sexp(self, sexp: SExp) -> Any:
        if self._closed:
            raise EclError("Lisp session is closed")
        result = self._eval_helper(
            SExp.list(
                SExp.atom("ecl-python:evaluate"),
                SExp.list(SExp.symbol("LAMBDA"), SExp.list(), sexp),
            )
        )
        return decode_result(result, self)

    def _eval_helper(self, sexp: SExp) -> Any:
        return parse_one(self.session.eval(str(sexp)))

    def _decode(self, node: Any) -> Any:
        return decode_value(node, self)

    def _make_reference(self, object_id: int, type_name: str) -> LispReference:
        reference = LispReference(self, object_id, type_name)
        self._references[object_id] = reference
        return reference

    def _release_reference(self, reference: LispReference) -> None:
        if reference.released:
            return
        self._references.pop(reference.object_id, None)
        if not self._closed:
            try:
                self.session.eval(
                    str(
                        SExp.list(
                            SExp.atom("ecl-python:release-object"),
                            SExp.integer(reference.object_id),
                        )
                    )
                )
            except EclError:
                pass
        reference.released = True

    def _release_all_references(self) -> None:
        if self._references and not self._closed:
            try:
                self.session.eval(str(SExp.list(SExp.atom("ecl-python:release-all-objects"))))
            except EclError:
                pass
        for reference in self._references.values():
            reference.released = True
        self._references.clear()


def _attribute_candidates(attribute: str) -> list[str]:
    if attribute in _OPERATOR_NAMES:
        return [_OPERATOR_NAMES[attribute]]

    candidates: list[str] = []
    base = _attribute_to_symbol_name(attribute)
    candidates.append(base)
    if not (base.startswith("*") and base.endswith("*")):
        candidates.append(f"*{base}*")
    return list(dict.fromkeys(candidates))


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
