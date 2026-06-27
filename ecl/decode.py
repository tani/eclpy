from __future__ import annotations

from fractions import Fraction
from typing import Any

from .objects import Cons, List, Symbol
from .session import EclError


def decode_result(node: Any, lisp: Any) -> Any:
    tag = node_tag(node)
    if tag == ":OK":
        expect_len(node, 2)
        return decode_value(node[1], lisp)
    if tag == ":ERROR":
        if len(node) < 3:
            raise EclError(f"malformed ECL error result: {node!r}")
        condition_type = str(node[1]) if len(node) > 1 else None
        message = str(node[2]) if len(node) > 2 else "Lisp evaluation failed"
        raise EclError(message, condition_type=condition_type)
    raise EclError(f"expected ECL result wrapper, got {node!r}")


def decode_value(node: Any, lisp: Any) -> Any:
    if not isinstance(node, list):
        raise EclError(f"expected serialized ECL value, got {node!r}")
    tag = node_tag(node)
    if tag == ":NIL":
        expect_len(node, 1)
        return List()
    if tag == ":TRUE":
        expect_len(node, 1)
        return True
    if tag == ":INT":
        expect_len(node, 2)
        return int(node[1])
    if tag == ":RATIO":
        expect_len(node, 3)
        return Fraction(int(node[1]), int(node[2]))
    if tag == ":FLOAT":
        expect_len(node, 2)
        return float(str(node[1]).replace("d", "e").replace("D", "E"))
    if tag == ":STRING":
        expect_len(node, 2)
        return str(node[1])
    if tag == ":SYMBOL":
        expect_len(node, 3)
        return Symbol(str(node[1]), optional_string(node[2]))
    if tag == ":LIST":
        return List(*(decode_value(item, lisp) for item in node[1:]))
    if tag == ":DOTTED-LIST":
        expect_len(node, 3)
        items = [decode_value(item, lisp) for item in node[1]]
        tail = decode_value(node[2], lisp)
        for item in reversed(items):
            tail = Cons(item, tail)
        return tail
    if tag == ":VECTOR":
        return [decode_value(item, lisp) for item in node[1:]]
    if tag == ":PACKAGE":
        expect_len(node, 2)
        return lisp.find_package(str(node[1]))
    if tag == ":REF":
        expect_len(node, 3)
        return lisp._make_reference(int(node[1]), str(node[2]))
    raise EclError(f"unknown ECL serialization tag {tag}")


def node_tag(node: Any) -> str:
    if not isinstance(node, list) or not node:
        raise EclError(f"expected tagged ECL value, got {node!r}")
    return symbol_atom(node[0]).upper()


def symbol_atom(value: Any) -> str:
    if not isinstance(value, str):
        raise EclError(f"expected ECL symbol atom, got {value!r}")
    return value


def optional_string(value: Any) -> str | None:
    if value in (None, "NIL"):
        return None
    return str(value)


def expect_len(node: list[Any], length: int) -> None:
    if len(node) != length:
        raise EclError(f"malformed ECL tagged value: {node!r}")
