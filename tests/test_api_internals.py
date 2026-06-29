from __future__ import annotations

import unittest
from types import SimpleNamespace

from eclpy import EclError, Lisp, Reference, SExp, Symbol
from eclpy.protocol import decode_value
from eclpy.proxy import Package, _attribute_candidates

NIL_RESULT = '{"protocol":"eclpy","version":1,"status":"ok","value":{"type":"nil"}}'


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def eval(self, code: str) -> str:
        self.calls.append(code)
        if code.startswith(("(ecl-python:release-object", "(ecl-python:release-all-objects")):
            raise EclError("release failed")
        return NIL_RESULT

    def eval_json(self, code: str) -> str:
        self.calls.append(code)
        return f" {NIL_RESULT} "


class ApiInternalsTests(unittest.TestCase):
    def test_repr_and_package_helpers(self) -> None:
        fake_lisp = SimpleNamespace()
        package = Package(fake_lisp, "CL")
        self.assertEqual(repr(package), "Package('CL')")
        with self.assertRaises(AttributeError):
            package.__getattr__("_private")
        self.assertNotIn("function", type(package).__dict__)
        self.assertEqual(package.symbol("car"), Symbol("CAR", "CL"))

    def test_package_lookup_symbol_and_missing(self) -> None:
        fake_lisp = SimpleNamespace(
            _eval_helper=lambda form: {
                "protocol": "eclpy",
                "version": 1,
                "kind": "symbol",
                "name": "FOO",
                "package": "CL",
            }
        )
        self.assertEqual(Package(fake_lisp, "CL").foo, Symbol("FOO", "CL"))

        function_lisp = SimpleNamespace(
            _eval_helper=lambda form: {
                "protocol": "eclpy",
                "version": 1,
                "kind": "callable",
                "callable_type": "function",
                "name": "FOO",
                "package": "CL",
            }
        )
        callable_symbol = Package(function_lisp, "CL").foo
        self.assertTrue(callable(callable_symbol))
        self.assertEqual(repr(callable_symbol), "CL::FOO")

        missing_lisp = SimpleNamespace(
            _eval_helper=lambda form: {"protocol": "eclpy", "version": 1, "kind": "missing"}
        )
        with self.assertRaises(AttributeError):
            _ = Package(missing_lisp, "CL").missing

    def test_attribute_candidates_preserve_earmuff_names(self) -> None:
        self.assertEqual(_attribute_candidates("*features*"), ["*FEATURES*"])

    def test_lisp_close_decode_and_closed_eval(self) -> None:
        lisp = Lisp(session=FakeSession())
        self.assertEqual(decode_value({"type": "int", "value": "5"}, lisp), 5)

        lisp.close()
        lisp.close()
        with self.assertRaisesRegex(EclError, "closed"):
            lisp._eval_sexp(SExp.integer(1))

    def test_release_reference_swallow_paths(self) -> None:
        lisp = Lisp(session=FakeSession())

        already_released = Reference(lisp, 1, "OBJECT", released=True)
        lisp._release_reference(already_released)
        self.assertTrue(already_released.released)

        reference = Reference(lisp, 2, "OBJECT")
        lisp._references[2] = reference
        lisp._release_reference(reference)
        self.assertTrue(reference.released)
        self.assertNotIn(2, lisp._references)

        another = Reference(lisp, 3, "OBJECT")
        lisp._references[3] = another
        lisp._release_all_references()
        self.assertTrue(another.released)
        self.assertEqual(lisp._references, {})


if __name__ == "__main__":
    unittest.main()
