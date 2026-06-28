from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import eclpy.simple as L
from eclpy import Cons, EclError, EclSession, Lisp, List, Reference, SExp, Symbol
from eclpy.api import ASDF_SOURCE
from eclpy.reader import parse_one

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_WASM = ROOT / "eclpy" / "ecl_eval.wasm"
BUILD_WASM = ROOT / "build" / "eclpy" / "ecl_eval.wasm"
UNSUPPORTED_PRLIMIT64_WARNING = "unsupported syscall: __syscall_prlimit64"


def require_wasm() -> Path:
    wasm_path = Path(os.environ["ECL_WASM"]) if "ECL_WASM" in os.environ else None
    wasm_path = wasm_path or (PACKAGE_WASM if PACKAGE_WASM.is_file() else BUILD_WASM)
    if not wasm_path.is_file():
        raise unittest.SkipTest("ECL WASM artifact is not built")
    return wasm_path


def require_asdf_source() -> Path:
    if not ASDF_SOURCE.is_file():
        raise unittest.SkipTest("ASDF source is not bundled")
    return ASDF_SOURCE


class EclSessionTests(unittest.TestCase):
    def test_missing_wasm_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.wasm"
            with self.assertRaisesRegex(FileNotFoundError, "build_ecl_wasm.py"):
                EclSession(missing)

    def test_eval_arithmetic(self) -> None:
        with EclSession(require_wasm()) as ecl:
            self.assertEqual(ecl.eval("(+ 1 2)"), "3")

    def test_eval_multiple_forms_returns_last_value(self) -> None:
        with EclSession(require_wasm()) as ecl:
            self.assertEqual(ecl.eval("(+ 1 2)\n(+ 3 4)"), "7")

    def test_eval_keeps_session_state(self) -> None:
        with EclSession(require_wasm()) as ecl:
            self.assertEqual(ecl.eval("(defparameter *ecl-test-value* 41)"), "*ECL-TEST-VALUE*")
            self.assertEqual(ecl.eval("(1+ *ecl-test-value*)"), "42")

    def test_eval_error_raises_ecl_error(self) -> None:
        with EclSession(require_wasm()) as ecl, self.assertRaises(EclError):
            ecl.eval("(definitely-not-a-bound-function)")

    def test_lisp_error_condition_raises_ecl_error(self) -> None:
        with EclSession(require_wasm()) as ecl:
            with self.assertRaisesRegex(EclError, "ECL evaluation escaped"):
                ecl.eval('(error "boom from Lisp")')

    def test_lisp_warning_does_not_raise_ecl_error(self) -> None:
        with Lisp(require_wasm()) as lisp:
            self.assertEqual(lisp.eval(SExp.raw('(progn (warn "careful") 7)')), 7)

    def test_startup_does_not_warn_about_prlimit64(self) -> None:
        env = os.environ.copy()
        env["ECL_WASM"] = str(require_wasm())
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import eclpy; lisp = eclpy.Lisp(); "
                    "print(lisp.eval(eclpy.SExp.list(eclpy.SExp.symbol('+'), "
                    "eclpy.SExp.integer(1), eclpy.SExp.integer(2))))"
                ),
            ],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertEqual(completed.stdout.strip(), "3")
        self.assertNotIn(UNSUPPORTED_PRLIMIT64_WARNING, completed.stderr)


class LispApiTests(unittest.TestCase):
    def test_lisp_eval_accepts_explicit_sexp_only(self) -> None:
        with Lisp(require_wasm()) as lisp:
            self.assertEqual(lisp.eval(SExp.integer(42)), 42)
            self.assertEqual(
                lisp.eval(
                    SExp.list(
                        SExp.symbol("+"),
                        SExp.integer(2),
                        SExp.integer(3),
                    )
                ),
                5,
            )
            self.assertEqual(
                lisp.eval(
                    SExp.list(
                        SExp.symbol("/"),
                        SExp.list(SExp.symbol("*"), SExp.integer(3), SExp.integer(5)),
                        SExp.integer(2),
                    )
                ),
                Fraction(15, 2),
            )

    def test_lisp_eval_rejects_shorthand_inputs(self) -> None:
        with Lisp(require_wasm()) as lisp:
            with self.assertRaisesRegex(TypeError, "only accepts SExp"):
                lisp.eval(42)
            with self.assertRaisesRegex(TypeError, "only accepts SExp"):
                lisp.eval("(+ 1 2)")
            with self.assertRaisesRegex(TypeError, "only accepts SExp"):
                lisp.eval((Symbol("+"), 1, 2))
            with self.assertRaisesRegex(TypeError, "only accepts SExp"):
                lisp.eval(Symbol("FOO"))
            self.assertFalse(hasattr(lisp, "eval_source"))

    def test_lisp_eval_accepts_raw_sexp(self) -> None:
        with Lisp(require_wasm()) as lisp:
            self.assertEqual(lisp.eval(SExp.raw("(+ 1 2)")), 3)
            self.assertEqual(lisp.eval(SExp.raw("(+ 1 2) (+ 3 4)")), 7)

    def test_lisp_load_reads_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "loaded.lisp"
            source.write_text(
                "\n".join(
                    [
                        "(defparameter *loaded-from-file* 88)",
                        "(defparameter *load-pathname-during-load*",
                        "  (namestring *load-pathname*))",
                    ]
                ),
                encoding="utf-8",
            )

            with Lisp(require_wasm()) as lisp:
                self.assertIs(
                    lisp.eval(SExp.raw(f"(load #p{SExp.string(str(source))})")),
                    True,
                )
                self.assertEqual(lisp.eval(SExp.raw("*loaded-from-file*")), 88)
                self.assertEqual(
                    lisp.eval(SExp.raw("*load-pathname-during-load*")),
                    str(source),
                )

    def test_lisp_load_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory, Lisp(require_wasm()) as lisp:
            missing = Path(directory) / "missing.lisp"

            self.assertEqual(
                lisp.eval(SExp.raw(f"(load #p{SExp.string(str(missing))} :if-does-not-exist nil)")),
                List(),
            )
            with self.assertRaisesRegex(EclError, "Cannot open"):
                lisp.eval(SExp.raw(f"(load #p{SExp.string(str(missing))})"))

    def test_lisp_load_propagates_lisp_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "broken.lisp"
            source.write_text('(error "boom while loading")\n', encoding="utf-8")

            with (
                Lisp(require_wasm()) as lisp,
                self.assertRaisesRegex(EclError, "boom while loading"),
            ):
                lisp.eval(SExp.raw(f"(load #p{SExp.string(str(source))})"))

    def test_require_asdf(self) -> None:
        require_asdf_source()
        with Lisp(require_wasm()) as lisp:
            lisp.eval(SExp.raw("(require 'asdf)"))
            self.assertIs(lisp.eval(SExp.raw("(and (find-package :asdf) t)")), True)
            self.assertIsInstance(lisp.eval(SExp.raw("(asdf:asdf-version)")), str)
            # ASDF is registered, so requiring it again loads nothing new.
            self.assertEqual(lisp.eval(SExp.raw("(require 'asdf)")), List())

    def test_require_unknown_module_raises(self) -> None:
        with Lisp(require_wasm()) as lisp, self.assertRaisesRegex(EclError, "REQUIRE"):
            lisp.eval(SExp.raw("(require 'no-such-module-xyz)"))

    def test_asdf_loads_a_source_project(self) -> None:
        require_asdf_source()
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "demo.asd").write_text(
                '(defsystem "demo" :serial t :components'
                ' ((:file "pkg") (:file "math")))\n',
                encoding="utf-8",
            )
            (project / "pkg.lisp").write_text(
                "(defpackage :demo (:use :cl) (:export :add))\n", encoding="utf-8"
            )
            (project / "math.lisp").write_text(
                "(in-package :demo)\n(defun add (a b) (+ a b))\n", encoding="utf-8"
            )

            with Lisp(require_wasm()) as lisp:
                lisp.eval(SExp.raw("(require 'asdf)"))
                # probe-file/file-write-date now see the real host file.
                self.assertIs(
                    lisp.eval(SExp.raw(f"(and (probe-file #p{SExp.string(str(project))}/) t)")),
                    True,
                )
                lisp.eval(
                    SExp.raw(f"(push #p{SExp.string(str(project) + '/')} asdf:*central-registry*)")
                )
                lisp.eval(SExp.raw('(asdf:load-system "demo")'))
                self.assertEqual(lisp.eval(SExp.raw("(demo:add 20 22)")), 42)

    def test_truename_of_missing_host_file_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory, Lisp(require_wasm()) as lisp:
            missing = Path(directory) / "missing.lisp"
            self.assertEqual(
                lisp.eval(SExp.raw(f"(probe-file #p{SExp.string(str(missing))})")),
                List(),
            )
            with self.assertRaises(EclError):
                lisp.eval(SExp.raw(f"(truename #p{SExp.string(str(missing))})"))

    def test_simple_api_builds_shorthand_sexp(self) -> None:
        with Lisp(require_wasm()) as lisp:
            self.assertEqual(lisp.eval(L.expr(1)), 1)
            self.assertEqual(lisp.eval(L.expr(("+", 1, 1))), 2)
            with self.assertRaises(TypeError):
                L.expr("+", 1, 1)  # type: ignore[call-arg]

            self.assertEqual(
                lisp.eval(L.expr(("/", ("*", 3, 5), 2))),
                Fraction(15, 2),
            )
            self.assertIs(
                lisp.eval(L.expr(("STRING=", L.string("foo"), L.string("foo")))),
                True,
            )
            self.assertEqual(
                lisp.eval(L.expr(("loop", "for", "i", "below", 5, "collect", "i"))),
                List(0, 1, 2, 3, 4),
            )
            self.assertEqual(
                lisp.eval(L.expr(["mapcar", L.find_function(lisp, "+"), [1, 2], [3, 4]])),
                List(4, 6),
            )
            self.assertEqual(lisp.eval(L.expr([L.find_function(lisp, "+"), 1, 2])), 3)
            self.assertFalse(hasattr(L, "fn"))

    def test_sexp_stringification(self) -> None:
        form = SExp.list(
            SExp.symbol("+"),
            SExp.integer(1),
            SExp.string('two "words" \\ ok'),
            SExp.keyword("test_key"),
            SExp.symbol("CAR", "COMMON-LISP"),
            SExp.quote(SExp.symbol("FOO")),
            SExp.function_quote(SExp.symbol("BAR")),
            SExp.raw("(raw form)"),
        )

        self.assertEqual(
            str(form),
            '(+ 1 "two \\"words\\" \\\\ ok" :TEST-KEY COMMON-LISP::CAR \'FOO #\'BAR (raw form))',
        )
        self.assertEqual(str(SExp.list()), "nil")

    def test_lark_reader_parses_tagged_results(self) -> None:
        self.assertEqual(parse_one("(:OK (:INT 42))"), [":OK", [":INT", 42]])
        self.assertEqual(parse_one('(:STRING "a\\"b\\\\c")'), [":STRING", 'a"b\\c'])
        self.assertEqual(parse_one('(:REF 7 "FUNCTION")'), [":REF", 7, "FUNCTION"])
        self.assertEqual(
            parse_one("(:DOTTED-LIST ((:INT 1) (:INT 2)) (:INT 3))"),
            [
                ":DOTTED-LIST",
                [[":INT", 1], [":INT", 2]],
                [":INT", 3],
            ],
        )

        with self.assertRaises(EclError):
            parse_one("(:INT 1")
        with self.assertRaises(EclError):
            parse_one("(:INT 1) (:INT 2)")

    def test_lisp_values_keep_strings_and_symbols_distinct(self) -> None:
        with Lisp(require_wasm()) as lisp:
            self.assertEqual(
                lisp.eval(L.expr(("STRING=", L.string("foo"), L.string("bar")))),
                List(),
            )
            self.assertIs(
                lisp.eval(L.expr(("STRING=", L.string("foo"), L.string("foo")))),
                True,
            )

            string_value = lisp.eval(SExp.raw('"CAR"'))
            symbol_value = lisp.eval(SExp.raw("'CL:CAR"))
            self.assertIsInstance(string_value, str)
            self.assertIsInstance(symbol_value, Symbol)
            self.assertNotEqual(string_value, symbol_value)
            self.assertEqual(L.find_function(lisp, "SYMBOL-NAME")(Symbol("FOO")), "FOO")

    def test_lisp_symbol_lookup_and_functions(self) -> None:
        with Lisp(require_wasm()) as lisp:
            self.assertEqual(lisp.eval(SExp.symbol("*PRINT-BASE*", "COMMON-LISP")), 10)

            add = L.find_function(lisp, "+")
            div = L.find_function(lisp, "/")
            self.assertEqual(add(1, 2, 3, 4), 10)
            self.assertEqual(div(2, 4), Fraction(1, 2))
            self.assertEqual(L.find_function(lisp, "CAR", L.find_package(lisp, "CL"))((1, 2)), 1)
            self.assertFalse(hasattr(lisp, "function"))
            self.assertFalse(hasattr(lisp, "find_package"))

    def test_lisp_package_attribute_api(self) -> None:
        with Lisp(require_wasm()) as lisp:
            cl = L.find_package(lisp, "CL")

            self.assertEqual(lisp.eval(L.expr(["package-name", cl])), "COMMON-LISP")
            self.assertIs(cl.oddp(5), True)
            self.assertEqual(cl.cons(5, None), List(5))
            self.assertEqual(cl.remove(5, [1, -5, 2, 7, 5, 9], key=cl.abs), [1, 2, 7, 9])
            self.assertEqual(cl.add(2, 3, 4, 5), 14)
            self.assertIs(cl.gt(3, 2), True)
            self.assertEqual(cl.stringgt("baz", "bar"), 2)
            self.assertEqual(cl.print_base, 10)
            self.assertGreater(cl.MOST_POSITIVE_DOUBLE_FLOAT, 1e300)
            self.assertEqual(cl.mapcar(cl.constantly(4), (1, 2, 3)), List(4, 4, 4))

    def test_lisp_macro_and_special_form_wrappers(self) -> None:
        with Lisp(require_wasm()) as lisp:
            cl = L.find_package(lisp, "CL")

            self.assertEqual(
                cl.loop(Symbol("REPEAT"), 5, Symbol("COLLECT"), 42),
                List(42, 42, 42, 42, 42),
            )
            self.assertEqual(cl.progn(5, 6, 7, (Symbol("+"), 4, 4)), 8)
            self.assertEqual(
                lisp.eval(
                    SExp.list(
                        SExp.symbol("WITH-OUTPUT-TO-STRING"),
                        SExp.list(SExp.symbol("STREAM")),
                        SExp.list(SExp.symbol("PRINC"), SExp.integer(12), SExp.symbol("STREAM")),
                        SExp.list(SExp.symbol("PRINC"), SExp.integer(34), SExp.symbol("STREAM")),
                    )
                ),
                "1234",
            )

    def test_lisp_cons_cells(self) -> None:
        with Lisp(require_wasm()) as lisp:
            cl = L.find_package(lisp, "CL")

            self.assertEqual(
                lisp.eval(SExp.list(SExp.symbol("CONS"), SExp.integer(1), SExp.integer(2))),
                Cons(1, 2),
            )

            lst = lisp.eval(
                SExp.list(
                    SExp.symbol("CONS"),
                    SExp.integer(1),
                    SExp.list(SExp.symbol("CONS"), SExp.integer(2), SExp.list()),
                )
            )
            self.assertEqual(lst, List(1, 2))
            self.assertEqual(lst.car, 1)
            self.assertEqual(lst.cdr, List(2))
            self.assertEqual(list(lst), [1, 2])
            self.assertEqual(sum(lst), 3)

            self.assertEqual(
                lisp.eval(
                    SExp.list(
                        SExp.symbol("CONS"),
                        SExp.integer(1),
                        SExp.list(SExp.symbol("CONS"), SExp.integer(2), SExp.integer(3)),
                    )
                ),
                Cons(1, Cons(2, 3)),
            )
            twos = Cons(2, Cons(2, Cons(2, Cons(2))))
            self.assertEqual(
                cl.mapcar(L.find_function(lisp, "+"), (1, 2, 3, 4), twos),
                List(3, 4, 5, 6),
            )

    def test_lisp_high_level_error_has_condition_details(self) -> None:
        with Lisp(require_wasm()) as lisp, self.assertRaises(EclError) as raised:
            lisp.eval(SExp.raw('(error "boom from Lisp")'))

        self.assertIsNotNone(raised.exception.condition_type)
        self.assertIn("SIMPLE-ERROR", raised.exception.condition_type or "")
        self.assertIn("boom from Lisp", raised.exception.message)

    def test_lisp_reference_context_manager(self) -> None:
        with Lisp(require_wasm()) as lisp:
            cl = L.find_package(lisp, "CL")
            reference = cl.constantly(4)

            self.assertIsInstance(reference, Reference)
            with reference as fn:
                self.assertEqual(cl.mapcar(fn, (1, 2, 3)), List(4, 4, 4))

            self.assertTrue(reference.released)
            reference.release()
            with self.assertRaisesRegex(EclError, "released Lisp reference"):
                cl.mapcar(reference, (1, 2, 3))

    def test_lisp_close_releases_outstanding_references(self) -> None:
        lisp = Lisp(require_wasm())
        reference = L.find_package(lisp, "CL").constantly(4)

        self.assertIsInstance(reference, Reference)
        self.assertFalse(reference.released)

        lisp.close()

        self.assertTrue(reference.released)


if __name__ == "__main__":
    unittest.main()
