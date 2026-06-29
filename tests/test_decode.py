from __future__ import annotations

import unittest
from fractions import Fraction
from math import inf

from eclpy import Cons, EclError, List, Package, Reference, Symbol
from eclpy.protocol import (
    decode_result,
    decode_value,
    dump_value,
    expect_len,
    node_tag,
    optional_string,
    symbol_atom,
    to_protocol,
)


class FakeLisp:
    def __init__(self) -> None:
        self.references: list[tuple[int, str]] = []

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
        self.assertEqual(decode_value([":SYMBOL", "FOO", None], lisp), Symbol("FOO"))
        self.assertEqual(decode_value([":LIST", [":INT", 1], [":INT", 2]], lisp), List(1, 2))
        self.assertEqual(
            decode_value([":DOTTED-LIST", [[":INT", 1], [":INT", 2]], [":INT", 3]], lisp),
            Cons(1, Cons(2, 3)),
        )
        self.assertEqual(decode_value([":VECTOR", [":INT", 1], [":INT", 2]], lisp), [1, 2])
        self.assertEqual(decode_value([":PACKAGE", "CL"], lisp), Package(lisp, "CL"))
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
        with self.assertRaisesRegex(EclError, "malformed ECL tagged value"):
            expect_len([":INT"], 2)

    def test_encode_python_values_to_protocol(self) -> None:
        self.assertEqual(to_protocol(None), [":NIL"])
        self.assertEqual(to_protocol(False), [":NIL"])
        self.assertEqual(to_protocol(True), [":TRUE"])
        self.assertEqual(to_protocol(12), [":INT", 12])
        self.assertEqual(to_protocol(Fraction(5, 3)), [":RATIO", 5, 3])
        self.assertEqual(to_protocol(1.25), [":FLOAT", "1.25"])
        self.assertEqual(to_protocol("abc"), [":STRING", "abc"])
        self.assertEqual(to_protocol(Symbol("FOO", "CL")), [":SYMBOL", "FOO", "CL"])
        self.assertEqual(to_protocol(List(1, "x")), [":LIST", [":INT", 1], [":STRING", "x"]])
        self.assertEqual(
            to_protocol(Cons(1, 2)),
            [":DOTTED-LIST", [[":INT", 1]], [":INT", 2]],
        )
        self.assertEqual(
            to_protocol({"a": 1}),
            [":LIST", [":DOTTED-LIST", [[":STRING", "a"]], [":INT", 1]]],
        )
        self.assertEqual(dump_value([1, "x"]), '[":LIST", [":INT", 1], [":STRING", "x"]]')
        self.assertEqual(to_protocol(Reference(None, 7, "FUNCTION")), [":REF", 7, "FUNCTION"])

        with self.assertRaisesRegex(EclError, "released Lisp reference"):
            to_protocol(Reference(None, 7, "FUNCTION", released=True))
        with self.assertRaisesRegex(TypeError, "non-finite float"):
            to_protocol(inf)
        with self.assertRaisesRegex(TypeError, "cannot convert object"):
            to_protocol(object())


if __name__ == "__main__":
    unittest.main()
