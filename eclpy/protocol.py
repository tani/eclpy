"""The JSON value protocol shared by Python and Lisp.

Both directions of the bridge use a single tagged JSON schema. Each value is a
JSON array whose first element is an uppercase tag and whose remaining elements
are the payload::

    [":NIL"]                          Lisp NIL / Python None
    [":TRUE"]                         Lisp T / Python True
    [":INT", n]                       integer
    [":RATIO", num, den]              rational
    [":FLOAT", "1.5d0"]              float (Lisp-readable text)
    [":STRING", s]                    string
    [":SYMBOL", name, package|null]   symbol
    [":LIST", item, ...]              proper list
    [":DOTTED-LIST", [item, ...], t]  improper list with tail ``t``
    [":VECTOR", item, ...]            vector
    [":PACKAGE", name]                package
    [":REF", id, type]                opaque handle to a Lisp object

Top-level results are wrapped as ``[":OK", value]`` or
``[":ERROR", type, message]``.

This module owns the Python half of the protocol: :func:`decode_value` /
:func:`decode_result` consume tagged JSON produced by Lisp, and
:func:`to_protocol` / :func:`dump_value` produce tagged JSON for Lisp. The Lisp
half lives in ``runtime_lisp`` (``serialize`` / ``deserialize``); the C layer
only shuttles the structure to and from JSON text.
"""

from __future__ import annotations

import json
import math
from fractions import Fraction
from typing import Any

from .errors import EclError
from .objects import Cons, List, Reference, Symbol

_OK_VALUE_INDEX = 1
_ERROR_MIN_FIELDS = 3
_ERROR_CONDITION_INDEX = 1
_ERROR_MESSAGE_INDEX = 2


# --- decode: tagged JSON structure -> Python value -------------------------


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
            from .proxy import find_package

            return find_package(lisp, str(node[1]))
        case ":REF":
            expect_len(node, 3)
            return lisp._make_reference(int(node[1]), str(node[2]))
        case _:
            message = f"unknown ECL serialization tag {tag}"
            raise EclError(message)


def node_tag(node: Any) -> str:
    """Return the tag atom for a serialized ECL node."""
    if not isinstance(node, list) or not node:
        message = f"expected tagged ECL value, got {node!r}"
        raise EclError(message)
    return symbol_atom(node[0])


def symbol_atom(value: Any) -> str:
    """Return a serialized symbol atom as a string."""
    if not isinstance(value, str):
        message = f"expected ECL symbol atom, got {value!r}"
        raise EclError(message)
    return value


def optional_string(value: Any) -> str | None:
    """Decode JSON null values as ``None`` and other values as strings."""
    if value is None:
        return None
    return str(value)


def expect_len(node: list[Any], length: int) -> None:
    """Raise when a serialized node does not have the expected length."""
    if len(node) != length:
        message = f"malformed ECL tagged value: {node!r}"
        raise EclError(message)


# --- encode: Python value -> tagged JSON structure -------------------------


def dump_value(value: Any) -> str:
    """Encode a Python value as tagged JSON text for the Lisp side."""
    return json.dumps(to_protocol(value), ensure_ascii=False)


def to_protocol(value: Any) -> Any:
    """Convert a Python value into the tagged protocol structure."""
    match value:
        case None:
            return [":NIL"]
        case bool():
            return [":TRUE"] if value else [":NIL"]
        case int():
            return [":INT", value]
        case Fraction() as ratio:
            return [":RATIO", ratio.numerator, ratio.denominator]
        case float() as number:
            return [":FLOAT", _float_text(number)]
        case str() as text:
            return [":STRING", text]
        case Symbol() as symbol:
            return [":SYMBOL", symbol.name, symbol.package]
        case Cons() as cons:
            return [":DOTTED-LIST", [to_protocol(cons.car)], to_protocol(cons.cdr)]
        case (List() | tuple() | list()) as items:
            return [":LIST", *(to_protocol(item) for item in items)]
        case dict() as mapping:
            pairs = (
                [":DOTTED-LIST", [to_protocol(key)], to_protocol(item)]
                for key, item in mapping.items()
            )
            return [":LIST", *pairs]
        case Reference() as reference:
            if reference.released:
                message = "cannot pass a released Lisp reference"
                raise EclError(message)
            return [":REF", reference.object_id, reference.type_name]
        case _:
            message = f"cannot convert {type(value).__name__} to the eclpy JSON protocol"
            raise TypeError(message)


def _float_text(value: float) -> str:
    if not math.isfinite(value):
        message = "cannot convert a non-finite float to the eclpy JSON protocol"
        raise TypeError(message)
    return repr(value)
