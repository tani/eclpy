"""The JSON value protocol shared by Python and Lisp.

The bridge uses object-shaped JSON so every node can be validated by field name.
Top-level ECL results are envelopes::

    {"protocol": "eclpy", "version": 1, "status": "ok", "value": {...}}
    {"protocol": "eclpy", "version": 1, "status": "error",
     "condition_type": "SIMPLE-ERROR", "message": "boom"}

Values use a ``type`` field plus named payload fields. The Lisp half lives in
``runtime.lisp`` (``serialize`` / ``deserialize``); the C layer only shuttles
JSON text across the WASM boundary.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Literal, cast

from .errors import EclError
from .objects import Cons, List, Symbol

PROTOCOL_NAME = "eclpy"
PROTOCOL_VERSION = 1

_JSON_SCALAR = str | int | float | bool | None
_JSON_VALUE = _JSON_SCALAR | list[Any] | dict[str, Any]
_LookupKind = Literal["missing", "callable", "value", "symbol"]


def decode_result(node: Any, lisp: Any) -> Any:
    """Decode a top-level success or error envelope from ECL."""
    envelope = _protocol_envelope(node)
    status = _required_string(envelope, "status")
    match status:
        case "ok":
            _expect_exact_keys(envelope, {"protocol", "version", "status", "value"}, "result")
            return decode_value(envelope["value"], lisp)
        case "error":
            _expect_exact_keys(
                envelope,
                {"protocol", "version", "status", "condition_type", "message"},
                "result",
            )
            raise EclError(
                _required_string(envelope, "message"),
                condition_type=_required_string(envelope, "condition_type"),
            )
        case _:
            message = f"unknown ECL result status {status!r}"
            raise EclError(message)


def decode_lookup(node: Any) -> dict[str, Any]:
    """Validate a package lookup envelope produced by Lisp."""
    envelope = _protocol_envelope(node)
    kind = _required_string(envelope, "kind")
    match kind:
        case "missing":
            _expect_exact_keys(envelope, {"protocol", "version", "kind"}, "lookup")
        case "callable":
            _expect_exact_keys(
                envelope,
                {"protocol", "version", "kind", "callable_type", "name", "package"},
                "lookup",
            )
            _required_string(envelope, "callable_type")
            _required_string(envelope, "name")
            _optional_string(envelope, "package")
        case "value":
            _expect_exact_keys(envelope, {"protocol", "version", "kind", "value"}, "lookup")
        case "symbol":
            _expect_exact_keys(
                envelope,
                {"protocol", "version", "kind", "name", "package"},
                "lookup",
            )
            _required_string(envelope, "name")
            _optional_string(envelope, "package")
        case _:
            message = f"unknown ECL lookup kind {kind!r}"
            raise EclError(message)
    return envelope


def decode_value(node: Any, lisp: Any) -> Any:
    """Decode one serialized Lisp value into its Python representation."""
    value = _object_node(node, "value")
    value_type = _required_string(value, "type")
    match value_type:
        case "nil":
            _expect_exact_keys(value, {"type"}, "value")
            return List()
        case "true":
            _expect_exact_keys(value, {"type"}, "value")
            return True
        case "int":
            _expect_exact_keys(value, {"type", "value"}, "value")
            return int(_required_decimal_string(value, "value"))
        case "ratio":
            _expect_exact_keys(value, {"type", "numerator", "denominator"}, "value")
            return Fraction(
                int(_required_decimal_string(value, "numerator")),
                int(_required_decimal_string(value, "denominator")),
            )
        case "float":
            _expect_exact_keys(value, {"type", "value"}, "value")
            return float(_required_string(value, "value").replace("d", "e").replace("D", "E"))
        case "string":
            _expect_exact_keys(value, {"type", "value"}, "value")
            return _required_string(value, "value")
        case "symbol":
            _expect_exact_keys(value, {"type", "name", "package"}, "value")
            return Symbol(_required_string(value, "name"), _optional_string(value, "package"))
        case "list":
            _expect_exact_keys(value, {"type", "items"}, "value")
            return List(*(decode_value(item, lisp) for item in _required_list(value, "items")))
        case "dotted-list":
            _expect_exact_keys(value, {"type", "items", "tail"}, "value")
            tail = decode_value(value["tail"], lisp)
            for item in reversed(_required_list(value, "items")):
                tail = Cons(decode_value(item, lisp), tail)
            return tail
        case "vector":
            _expect_exact_keys(value, {"type", "items"}, "value")
            return [decode_value(item, lisp) for item in _required_list(value, "items")]
        case "package":
            _expect_exact_keys(value, {"type", "name"}, "value")
            from .proxy import find_package

            return find_package(lisp, _required_string(value, "name"))
        case "ref":
            _expect_exact_keys(value, {"type", "id", "kind"}, "value")
            return lisp._make_reference(_required_int(value, "id"), _required_string(value, "kind"))
        case _:
            message = f"unknown ECL value type {value_type!r}"
            raise EclError(message)


def lookup_kind(node: dict[str, Any]) -> _LookupKind:
    """Return the validated lookup kind."""
    kind = _required_string(node, "kind")
    if kind in {"missing", "callable", "value", "symbol"}:
        return cast(_LookupKind, kind)
    message = f"unknown ECL lookup kind {kind!r}"
    raise EclError(message)


def lookup_string(node: dict[str, Any], key: str) -> str:
    """Return a required string field from a validated lookup envelope."""
    return _required_string(node, key)


def lookup_optional_string(node: dict[str, Any], key: str) -> str | None:
    """Return an optional string field from a validated lookup envelope."""
    return _optional_string(node, key)


def _protocol_envelope(node: Any) -> dict[str, Any]:
    envelope = _object_node(node, "protocol envelope")
    _expect_protocol(envelope)
    return envelope


def _expect_protocol(node: dict[str, Any]) -> None:
    if node.get("protocol") != PROTOCOL_NAME:
        message = f"expected eclpy protocol envelope, got {node!r}"
        raise EclError(message)
    if node.get("version") != PROTOCOL_VERSION:
        message = f"unsupported eclpy protocol version {node.get('version')!r}"
        raise EclError(message)


def _object_node(node: Any, context: str) -> dict[str, Any]:
    if not isinstance(node, dict):
        message = f"expected {context} object, got {node!r}"
        raise EclError(message)
    if not all(isinstance(key, str) for key in node):
        message = f"expected string keys in {context} object, got {node!r}"
        raise EclError(message)
    return node


def _expect_exact_keys(node: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(node)
    if actual != expected:
        message = f"malformed ECL {context}: expected keys {sorted(expected)}, got {sorted(actual)}"
        raise EclError(message)


def _required_string(node: dict[str, Any], key: str) -> str:
    value = _required(node, key)
    if not isinstance(value, str):
        message = f"expected string field {key!r}, got {value!r}"
        raise EclError(message)
    return value


def _optional_string(node: dict[str, Any], key: str) -> str | None:
    value = _required(node, key)
    if value is None:
        return None
    if not isinstance(value, str):
        message = f"expected string or null field {key!r}, got {value!r}"
        raise EclError(message)
    return value


def _required_list(node: dict[str, Any], key: str) -> list[Any]:
    value = _required(node, key)
    if not isinstance(value, list):
        message = f"expected list field {key!r}, got {value!r}"
        raise EclError(message)
    return value


def _required_int(node: dict[str, Any], key: str) -> int:
    value = _required(node, key)
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"expected integer field {key!r}, got {value!r}"
        raise EclError(message)
    return value


def _required_decimal_string(node: dict[str, Any], key: str) -> str:
    value = _required_string(node, key)
    digits = value[1:] if value.startswith("-") else value
    if not digits or not digits.isdecimal():
        message = f"expected decimal string field {key!r}, got {value!r}"
        raise EclError(message)
    return value


def _required(node: dict[str, Any], key: str) -> Any:
    if key not in node:
        message = f"missing required field {key!r}"
        raise EclError(message)
    return node[key]


