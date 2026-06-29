from __future__ import annotations

import unittest
from fractions import Fraction

from eclpy import SExp


class ReaderAndSExpTests(unittest.TestCase):
    def test_sexp_rendering_edges(self) -> None:
        with self.assertRaises(NotImplementedError):
            str(SExp())

        self.assertEqual(str(SExp.symbol("A B|C\\D")), "|A B\\|C\\\\D|")
        self.assertEqual(str(SExp.symbol("123")), "|123|")
        self.assertEqual(str(SExp.symbol("+1")), "|+1|")
        self.assertEqual(str(SExp.symbol("1/2")), "|1/2|")
        self.assertEqual(str(SExp.symbol("1.0")), "|1.0|")
        self.assertEqual(str(SExp.symbol("1d0")), "|1d0|")
        self.assertEqual(str(SExp.symbol("+")), "+")
        self.assertEqual(str(SExp.symbol("1+")), "1+")
        self.assertEqual(str(SExp.symbol("foo", "KEYWORD")), ":foo")
        self.assertEqual(str(SExp.keyword(":test_key")), ":TEST-KEY")
        self.assertEqual(str(SExp.ratio(Fraction(7, 3))), "7/3")
        self.assertEqual(str(SExp.float(1.25)), "1.25")
        self.assertEqual(str(SExp.string('a"b\\c')), '"a\\"b\\\\c"')


if __name__ == "__main__":
    unittest.main()
