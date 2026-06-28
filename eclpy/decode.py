"""Decode Lisp values serialized by the ECL helper package."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from .objects import Cons, List, Symbol
from .session import EclError

_OK_VALUE_INDEX = 1
_ERROR_MIN_FIELDS = 3
_ERROR_CONDITION_INDEX = 1
_ERROR_MESSAGE_INDEX = 2


def decode_result(node: Any, lisp: Any) -> Any:
    """Decode a top-level success or error wrapper from ECL."""
    match node_tag(node):
        case ":OK":
            expect_len(node, 2)
            return decode_value(node[_OK_VALUE_INDEX], lisp)
        case ":ERROR":
            if len(node) < _ERROR_MIN_FIELDS:
                message = f"malformed ECL error result: {node!r}"
                raise EclError(message)
            condition_type = str(node[_ERROR_CONDITION_INDEX])
            message = str(node[_ERROR_MESSAGE_INDEX])
            raise EclError(message, condition_type=condition_type)
        case _:
            message = f"expected ECL result wrapper, got {node!r}"
            raise EclError(message)


def decode_value(node: Any, lisp: Any) -> Any:
    """Decode one serialized Lisp value into its Python representation."""
    if not isinstance(node, list):
        message = f"expected serialized ECL value, got {node!r}"
        raise EclError(message)
    match tag := node_tag(node):
        case ":NIL":
            expect_len(node, 1)
            return List()
        case ":TRUE":
            expect_len(node, 1)
            return True
        case ":INT":
            expect_len(node, 2)
            return int(node[1])
        case ":RATIO":
            expect_len(node, 3)
            return Fraction(int(node[1]), int(node[2]))
        case ":FLOAT":
            expect_len(node, 2)
            return float(str(node[1]).replace("d", "e").replace("D", "E"))
        case ":STRING":
            expect_len(node, 2)
            return str(node[1])
        case ":SYMBOL":
            expect_len(node, 3)
            return Symbol(str(node[1]), optional_string(node[2]))
        case ":LIST":
            return List(*(decode_value(item, lisp) for item in node[1:]))
        case ":DOTTED-LIST":
            expect_len(node, 3)
            items = [decode_value(item, lisp) for item in node[1]]
            tail = decode_value(node[2], lisp)
            for item in reversed(items):
                tail = Cons(item, tail)
            return tail
        case ":VECTOR":
            return [decode_value(item, lisp) for item in node[1:]]
        case ":PACKAGE":
            expect_len(node, 2)
            return lisp.find_package(str(node[1]))
        case ":REF":
            expect_len(node, 3)
            return lisp._make_reference(int(node[1]), str(node[2]))
        case _:
            message = f"unknown ECL serialization tag {tag}"
            raise EclError(message)


def node_tag(node: Any) -> str:
    """Return the uppercase tag atom for a serialized ECL node."""
    if not isinstance(node, list) or not node:
        message = f"expected tagged ECL value, got {node!r}"
        raise EclError(message)
    return symbol_atom(node[0]).upper()


def symbol_atom(value: Any) -> str:
    """Return a serialized symbol atom as a string."""
    if not isinstance(value, str):
        message = f"expected ECL symbol atom, got {value!r}"
        raise EclError(message)
    return value


def optional_string(value: Any) -> str | None:
    """Decode NIL-like values as ``None`` and other values as strings."""
    if value in (None, "NIL"):
        return None
    return str(value)


def expect_len(node: list[Any], length: int) -> None:
    """Raise when a serialized node does not have the expected length."""
    if len(node) != length:
        message = f"malformed ECL tagged value: {node!r}"
        raise EclError(message)
