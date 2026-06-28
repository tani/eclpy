from __future__ import annotations

import unittest
from fractions import Fraction

from eclpy import Cons, EclError, List, Symbol
from eclpy.decode import (
    decode_result,
    decode_value,
    expect_len,
    node_tag,
    optional_string,
    symbol_atom,
)


class FakeLisp:
    def __init__(self) -> None:
        self.references: list[tuple[int, str]] = []

    def _find_package(self, name: str) -> str:
        return f"package:{name}"

    def _make_reference(self, object_id: int, type_name: str) -> tuple[int, str]:
        reference = (object_id, type_name)
        self.references.append(reference)
        return reference


class DecodeTests(unittest.TestCase):
    def test_decode_result_errors(self) -> None:
        with self.assertRaisesRegex(EclError, "malformed ECL error"):
            decode_result([":ERROR", "TYPE"], FakeLisp())
        with self.assertRaisesRegex(EclError, "boom") as raised:
            decode_result([":ERROR", "SIMPLE-ERROR", "boom"], FakeLisp())
        self.assertEqual(raised.exception.condition_type, "SIMPLE-ERROR")
        with self.assertRaisesRegex(EclError, "expected ECL result wrapper"):
            decode_result([":NOPE"], FakeLisp())

    def test_decode_values(self) -> None:
        lisp = FakeLisp()
        self.assertEqual(decode_value([":NIL"], lisp), List())
        self.assertIs(decode_value([":TRUE"], lisp), True)
        self.assertEqual(decode_value([":INT", 4], lisp), 4)
        self.assertEqual(decode_value([":RATIO", 6, 8], lisp), Fraction(3, 4))
        self.assertEqual(decode_value([":FLOAT", "1.25d0"], lisp), 1.25)
        self.assertEqual(decode_value([":STRING", "abc"], lisp), "abc")
        self.assertEqual(
            decode_value([":SYMBOL", "CAR", "COMMON-LISP"], lisp), Symbol("CAR", "COMMON-LISP")
        )
        self.assertEqual(decode_value([":SYMBOL", "FOO", "NIL"], lisp), Symbol("FOO"))
        self.assertEqual(decode_value([":LIST", [":INT", 1], [":INT", 2]], lisp), List(1, 2))
        self.assertEqual(
            decode_value([":DOTTED-LIST", [[":INT", 1], [":INT", 2]], [":INT", 3]], lisp),
            Cons(1, Cons(2, 3)),
        )
        self.assertEqual(decode_value([":VECTOR", [":INT", 1], [":INT", 2]], lisp), [1, 2])
        self.assertEqual(decode_value([":PACKAGE", "CL"], lisp), "package:CL")
        self.assertEqual(decode_value([":REF", 7, "FUNCTION"], lisp), (7, "FUNCTION"))

    def test_decode_rejects_malformed_values(self) -> None:
        lisp = FakeLisp()
        with self.assertRaisesRegex(EclError, "expected serialized ECL value"):
            decode_value(":INT", lisp)
        with self.assertRaisesRegex(EclError, "unknown ECL serialization tag"):
            decode_value([":NOPE"], lisp)
        with self.assertRaisesRegex(EclError, "expected tagged ECL value"):
            node_tag([])
        with self.assertRaisesRegex(EclError, "expected ECL symbol atom"):
            symbol_atom(1)
        self.assertIsNone(optional_string(None))
        self.assertIsNone(optional_string("NIL"))
        with self.assertRaisesRegex(EclError, "malformed ECL tagged value"):
            expect_len([":INT"], 2)


if __name__ == "__main__":
    unittest.main()
