from __future__ import annotations

from fractions import Fraction
import unittest

from eclpy import EclError, SExp
from eclpy.reader import _unescape_lisp_string


class ReaderAndSExpTests(unittest.TestCase):
    def test_sexp_rendering_edges(self) -> None:
        with self.assertRaises(NotImplementedError):
            str(SExp())

        self.assertEqual(str(SExp.symbol("A B|C\\D")), "|A B\\|C\\\\D|")
        self.assertEqual(str(SExp.symbol("foo", "KEYWORD")), ":foo")
        self.assertEqual(str(SExp.keyword(":test_key")), ":TEST-KEY")
        self.assertEqual(str(SExp.ratio(Fraction(7, 3))), "7/3")
        self.assertEqual(str(SExp.float(1.25)), "1.25")
        self.assertEqual(str(SExp.string('a"b\\c')), '"a\\"b\\\\c"')

    def test_reader_rejects_invalid_string_escape(self) -> None:
        with self.assertRaisesRegex(EclError, "invalid ECL string escape"):
            _unescape_lisp_string('"abc\\"')


if __name__ == "__main__":
    unittest.main()
