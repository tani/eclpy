from __future__ import annotations

import unittest
from fractions import Fraction

from eclpy import Cons, EclError, List, Reference, SExp, Symbol
from eclpy.encode import to_data_expr


class Package:
    def __init__(self, name: str) -> None:
        self.lisp = object()
        self.name = name


class EncodeTests(unittest.TestCase):
    def test_to_data_expr_converts_python_values(self) -> None:
        passthrough = SExp.string("x")
        self.assertIs(to_data_expr(passthrough), passthrough)
        self.assertEqual(str(to_data_expr(None)), "nil")
        self.assertEqual(str(to_data_expr(False)), "nil")
        self.assertEqual(str(to_data_expr(True)), "t")
        self.assertEqual(str(to_data_expr(12)), "12")
        self.assertEqual(str(to_data_expr(Fraction(5, 3))), "5/3")
        self.assertEqual(str(to_data_expr(1.5)), "1.5")
        self.assertEqual(str(to_data_expr("foo")), '"foo"')
        self.assertEqual(str(to_data_expr(Symbol("FOO"))), "'FOO")
        self.assertEqual(str(to_data_expr(Package("CL"))), '(FIND-PACKAGE "CL")')
        self.assertEqual(
            str(to_data_expr(Reference(None, 7, "OBJECT"))), "(ecl-python:value 7)"
        )
        self.assertEqual(str(to_data_expr(List())), "nil")
        self.assertEqual(str(to_data_expr(List(1, "x"))), '(LIST 1 "x")')
        self.assertEqual(str(to_data_expr(())), "nil")
        self.assertEqual(str(to_data_expr((1, 2))), "(LIST 1 2)")
        self.assertEqual(str(to_data_expr([1, 2])), "(VECTOR 1 2)")
        self.assertEqual(str(to_data_expr(Cons(1, 2))), "(CONS 1 2)")

    def test_to_data_expr_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(EclError, "released Lisp reference"):
            to_data_expr(Reference(None, 7, "OBJECT", released=True))
        with self.assertRaisesRegex(TypeError, "cannot convert object"):
            to_data_expr(object())


if __name__ == "__main__":
    unittest.main()
