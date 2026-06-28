from __future__ import annotations

import unittest
from fractions import Fraction

import eclpy.simple as L
from eclpy import List, SExp, Symbol


class SimpleApiTests(unittest.TestCase):
    def test_helpers_render_explicit_sexp(self) -> None:
        self.assertEqual(str(L.string('a"b')), '"a\\"b"')
        self.assertEqual(str(L.symbol("car", "cl")), "CL::CAR")
        self.assertEqual(str(L.keyword("test_key")), ":TEST-KEY")
        self.assertEqual(str(L.quote(("a", "b"))), "'(A B)")
        self.assertEqual(str(L.quote(SExp.symbol("FOO"))), "'FOO")
        self.assertEqual(str(L.quote(Symbol("BAR", "CL"))), "'CL::BAR")
        self.assertEqual(str(L.quote(None)), "'nil")
        self.assertEqual(str(L.quote(False)), "'nil")
        self.assertEqual(str(L.quote(True)), "'t")
        self.assertEqual(str(L.quote(Fraction(4, 3))), "'4/3")
        self.assertEqual(str(L.quote(1.25)), "'1.25")
        self.assertEqual(str(L.quote(())), "'nil")
        self.assertEqual(str(L.function("+")), "#'+")
        self.assertEqual(str(L.raw("(+ 1 2)")), "(+ 1 2)")

    def test_expr_converts_supported_values(self) -> None:
        passthrough = SExp.integer(7)
        self.assertIs(L.expr(passthrough), passthrough)
        self.assertEqual(str(L.expr(Symbol("FOO", "CL"))), "CL::FOO")
        self.assertEqual(str(L.expr("foo")), "FOO")
        self.assertEqual(str(L.expr(None)), "nil")
        self.assertEqual(str(L.expr(False)), "nil")
        self.assertEqual(str(L.expr(True)), "t")
        self.assertEqual(str(L.expr(12)), "12")
        self.assertEqual(str(L.expr(Fraction(3, 2))), "3/2")
        self.assertEqual(str(L.expr(1.25)), "1.25")
        self.assertEqual(str(L.expr(())), "nil")
        self.assertEqual(str(L.expr([])), "nil")
        self.assertEqual(str(L.expr(("+", 1, 2))), "(+ 1 2)")
        self.assertEqual(str(L.expr([Symbol("+"), 1, 2])), "(+ 1 2)")
        self.assertEqual(str(L.expr((SExp.symbol("+"), 1, 2))), "(+ 1 2)")
        self.assertEqual(str(L.expr((1, 2, 3))), "'(1 2 3)")
        self.assertEqual(str(L.expr(List(1, 2))), "'(1 2)")

    def test_expr_rejects_unknown_values(self) -> None:
        with self.assertRaisesRegex(TypeError, "simple expression"):
            L.expr(object())
        with self.assertRaisesRegex(TypeError, "simple literal"):
            L.quote(object())


if __name__ == "__main__":
    unittest.main()
