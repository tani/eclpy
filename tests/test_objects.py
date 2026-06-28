from __future__ import annotations

import unittest

from eclpy import Cons, EclError, LispReference, List, Symbol


class FakeLisp:
    def __init__(self) -> None:
        self.released: list[LispReference] = []

    def _release_reference(self, reference: LispReference) -> None:
        self.released.append(reference)
        reference.released = True


class ObjectTests(unittest.TestCase):
    def test_symbol_identity_and_repr(self) -> None:
        with self.assertRaisesRegex(ValueError, "symbol name"):
            Symbol("")
        self.assertEqual(repr(Symbol("FOO")), "Symbol('FOO')")
        self.assertEqual(repr(Symbol("CAR", "CL")), "Symbol('CAR', 'CL')")
        self.assertEqual(Symbol("FOO"), Symbol("FOO"))
        self.assertNotEqual(Symbol("FOO"), Symbol("BAR"))
        self.assertEqual(len({Symbol("FOO"), Symbol("FOO")}), 1)

    def test_list_accessors_and_repr(self) -> None:
        empty = List()
        self.assertEqual(repr(empty), "()")
        with self.assertRaisesRegex(IndexError, "no car"):
            _ = empty.car
        with self.assertRaisesRegex(IndexError, "no cdr"):
            _ = empty.cdr

        values = List(1, 2, 3)
        self.assertEqual(values.car, 1)
        self.assertEqual(values.cdr, List(2, 3))
        self.assertEqual(repr(values), "List(1, 2, 3)")

    def test_cons_iteration_and_repr(self) -> None:
        proper = Cons(1, Cons(2, List(3)))
        self.assertEqual(list(proper), [1, 2, 3])
        self.assertEqual(repr(proper), "List(1, 2, 3)")

        nil_tail = Cons(1, Cons(2))
        self.assertEqual(list(nil_tail), [1, 2])
        self.assertEqual(repr(nil_tail), "List(1, 2)")

        singleton_dotted = Cons(1, 2)
        self.assertEqual(repr(singleton_dotted), "Cons(1, 2)")
        with self.assertRaisesRegex(TypeError, "dotted"):
            list(singleton_dotted)

        dotted = Cons(1, Cons(2, 3))
        self.assertEqual(repr(dotted), "DottedList(1, 2, 3)")

        circular = Cons(2)
        circular.cdr = circular
        self.assertEqual(repr(circular), "DottedList(2, ...)")
        with self.assertRaisesRegex(TypeError, "circular"):
            list(circular)

    def test_lisp_reference_lifecycle_and_repr(self) -> None:
        lisp = FakeLisp()
        reference = LispReference(lisp, 10, "FUNCTION")
        self.assertEqual(repr(reference), "LispReference(10, 'FUNCTION')")

        reference.release()
        self.assertTrue(reference.released)
        self.assertEqual(lisp.released, [reference])
        self.assertEqual(repr(reference), "LispReference(10, 'FUNCTION', released=True)")

        with self.assertRaisesRegex(EclError, "released Lisp reference"), reference:
            pass


if __name__ == "__main__":
    unittest.main()
