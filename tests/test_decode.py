from __future__ import annotations

import unittest
from fractions import Fraction
from typing import Any

from eclpy import Cons, EclError, List, Package, Symbol
from eclpy.protocol import (
    decode_lookup,
    decode_result,
    decode_value,
    lookup_kind,
    lookup_optional_string,
    lookup_string,
)


def ok(value: dict[str, Any]) -> dict[str, Any]:
    return {"protocol": "eclpy", "version": 1, "status": "ok", "value": value}


def error(condition_type: Any, message: Any) -> dict[str, Any]:
    return {
        "protocol": "eclpy",
        "version": 1,
        "status": "error",
        "condition_type": condition_type,
        "message": message,
    }


def lookup(kind: str, **fields: Any) -> dict[str, Any]:
    return {"protocol": "eclpy", "version": 1, "kind": kind, **fields}


class FakeLisp:
    def __init__(self) -> None:
        self.references: list[tuple[int, str]] = []

    def _make_reference(self, object_id: int, type_name: str) -> tuple[int, str]:
        reference = (object_id, type_name)
        self.references.append(reference)
        return reference


class DecodeTests(unittest.TestCase):
    def test_decode_result(self) -> None:
        self.assertEqual(decode_result(ok({"type": "int", "value": "4"}), FakeLisp()), 4)
        with self.assertRaisesRegex(EclError, "boom") as raised:
            decode_result(error("SIMPLE-ERROR", "boom"), FakeLisp())
        self.assertEqual(raised.exception.condition_type, "SIMPLE-ERROR")

    def test_decode_result_rejects_malformed_envelopes(self) -> None:
        with self.assertRaisesRegex(EclError, "expected protocol envelope object"):
            decode_result([":ERROR", "SIMPLE-ERROR", "boom"], FakeLisp())
        with self.assertRaisesRegex(EclError, "expected string keys"):
            decode_result({1: "eclpy", "version": 1, "status": "ok"}, FakeLisp())
        with self.assertRaisesRegex(EclError, "expected eclpy protocol envelope"):
            decode_result({"protocol": "other", "version": 1, "status": "ok"}, FakeLisp())
        with self.assertRaisesRegex(EclError, "unsupported eclpy protocol version"):
            decode_result({"protocol": "eclpy", "version": 2, "status": "ok"}, FakeLisp())
        with self.assertRaisesRegex(EclError, "unknown ECL result status"):
            decode_result({"protocol": "eclpy", "version": 1, "status": "nope"}, FakeLisp())
        with self.assertRaisesRegex(EclError, "malformed ECL result"):
            decode_result(
                {"protocol": "eclpy", "version": 1, "status": "error", "message": "boom"},
                FakeLisp(),
            )
        with self.assertRaisesRegex(EclError, "expected string field 'message'"):
            decode_result(error("SIMPLE-ERROR", 1), FakeLisp())

    def test_decode_values(self) -> None:
        lisp = FakeLisp()
        self.assertEqual(decode_value({"type": "nil"}, lisp), List())
        self.assertIs(decode_value({"type": "true"}, lisp), True)
        self.assertEqual(decode_value({"type": "int", "value": "4"}, lisp), 4)
        self.assertEqual(
            decode_value({"type": "ratio", "numerator": "6", "denominator": "8"}, lisp),
            Fraction(3, 4),
        )
        self.assertEqual(decode_value({"type": "float", "value": "1.25d0"}, lisp), 1.25)
        self.assertEqual(decode_value({"type": "string", "value": "abc"}, lisp), "abc")
        self.assertEqual(
            decode_value({"type": "symbol", "name": "CAR", "package": "COMMON-LISP"}, lisp),
            Symbol("CAR", "COMMON-LISP"),
        )
        self.assertEqual(
            decode_value({"type": "symbol", "name": "FOO", "package": None}, lisp),
            Symbol("FOO"),
        )
        self.assertEqual(
            decode_value(
                {
                    "type": "list",
                    "items": [{"type": "int", "value": "1"}, {"type": "int", "value": "2"}],
                },
                lisp,
            ),
            List(1, 2),
        )
        self.assertEqual(
            decode_value(
                {
                    "type": "dotted-list",
                    "items": [{"type": "int", "value": "1"}, {"type": "int", "value": "2"}],
                    "tail": {"type": "int", "value": "3"},
                },
                lisp,
            ),
            Cons(1, Cons(2, 3)),
        )
        self.assertEqual(
            decode_value(
                {
                    "type": "vector",
                    "items": [{"type": "int", "value": "1"}, {"type": "int", "value": "2"}],
                },
                lisp,
            ),
            [1, 2],
        )
        self.assertEqual(decode_value({"type": "package", "name": "CL"}, lisp), Package(lisp, "CL"))
        self.assertEqual(
            decode_value({"type": "ref", "id": 7, "kind": "FUNCTION"}, lisp), (7, "FUNCTION")
        )

    def test_decode_rejects_malformed_values(self) -> None:
        lisp = FakeLisp()
        with self.assertRaisesRegex(EclError, "expected value object"):
            decode_value(":INT", lisp)
        with self.assertRaisesRegex(EclError, "unknown ECL value type"):
            decode_value({"type": "nope"}, lisp)
        with self.assertRaisesRegex(EclError, "missing required field 'type'"):
            decode_value({}, lisp)
        with self.assertRaisesRegex(EclError, "malformed ECL value"):
            decode_value({"type": "nil", "value": None}, lisp)
        with self.assertRaisesRegex(EclError, "expected string field 'value'"):
            decode_value({"type": "int", "value": 4}, lisp)
        with self.assertRaisesRegex(EclError, "expected decimal string field 'value'"):
            decode_value({"type": "int", "value": "4.0"}, lisp)
        with self.assertRaisesRegex(EclError, "expected list field 'items'"):
            decode_value({"type": "list", "items": None}, lisp)
        with self.assertRaisesRegex(EclError, "expected integer field 'id'"):
            decode_value({"type": "ref", "id": True, "kind": "FUNCTION"}, lisp)

    def test_decode_lookup(self) -> None:
        self.assertEqual(lookup_kind(decode_lookup(lookup("missing"))), "missing")

        callable_lookup = decode_lookup(
            lookup("callable", callable_type="function", name="FOO", package="CL")
        )
        self.assertEqual(lookup_kind(callable_lookup), "callable")
        self.assertEqual(lookup_string(callable_lookup, "name"), "FOO")
        self.assertEqual(lookup_optional_string(callable_lookup, "package"), "CL")

        value_lookup = decode_lookup(lookup("value", value={"type": "int", "value": "9"}))
        self.assertEqual(value_lookup["value"], {"type": "int", "value": "9"})

        symbol_lookup = decode_lookup(lookup("symbol", name="BAR", package=None))
        self.assertEqual(lookup_kind(symbol_lookup), "symbol")
        self.assertIsNone(lookup_optional_string(symbol_lookup, "package"))

    def test_decode_lookup_rejects_malformed_envelopes(self) -> None:
        with self.assertRaisesRegex(EclError, "expected protocol envelope object"):
            decode_lookup([":MISSING"])
        with self.assertRaisesRegex(EclError, "unknown ECL lookup kind"):
            decode_lookup(lookup("nope"))
        with self.assertRaisesRegex(EclError, "unknown ECL lookup kind"):
            lookup_kind({"kind": "nope"})
        with self.assertRaisesRegex(EclError, "malformed ECL lookup"):
            decode_lookup(lookup("missing", value=None))
        with self.assertRaisesRegex(EclError, "expected string field 'name'"):
            decode_lookup(lookup("symbol", name=1, package=None))
        with self.assertRaisesRegex(EclError, "expected string or null field 'package'"):
            decode_lookup(lookup("callable", callable_type="function", name="FOO", package=1))


if __name__ == "__main__":
    unittest.main()
