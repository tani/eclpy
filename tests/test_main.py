from __future__ import annotations

import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from eclpy import EclError
from eclpy.__main__ import (
    _balanced,
    _current_package,
    _eval_and_print,
    _repl,
    _run,
    _save_readline_history,
    _setup_readline,
    main,
)


class BalancedTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertTrue(_balanced(""))

    def test_atom(self) -> None:
        self.assertTrue(_balanced("42"))

    def test_string(self) -> None:
        self.assertTrue(_balanced('"hello (world"'))

    def test_balanced_form(self) -> None:
        self.assertTrue(_balanced("(+ 1 2)"))

    def test_unbalanced_open(self) -> None:
        self.assertFalse(_balanced("(defun foo (x)"))

    def test_extra_close(self) -> None:
        # surplus ')' must not be treated as balanced
        self.assertFalse(_balanced(")))"))

    def test_unbalanced_closed_by_string(self) -> None:
        self.assertFalse(_balanced('(foo "bar)'))

    def test_comment_paren_ignored(self) -> None:
        self.assertFalse(_balanced("(foo ; comment )\n"))

    def test_comment_closes_line(self) -> None:
        self.assertTrue(_balanced("(foo ; comment\n  )"))

    def test_escape_in_string(self) -> None:
        self.assertTrue(_balanced('(foo "a\\"b")'))

    def test_comment_at_end_of_input_no_newline(self) -> None:
        # ';' with no trailing newline — find("\n") returns -1, hits the break
        self.assertFalse(_balanced("(foo ; unterminated comment"))


class FakeSession:
    def __init__(self, responses: list[str]) -> None:
        self._responses = iter(responses)

    def eval(self, code: str) -> str:
        if "(package-name *package*)" in code:
            return '"ECLPY-USER"'
        if "defpackage" in code:
            return ""
        return next(self._responses)


def _fake_lisp(responses: list[str]) -> SimpleNamespace:
    return SimpleNamespace(session=FakeSession(responses))


class EvalAndPrintTests(unittest.TestCase):
    def test_prints_result(self) -> None:
        lisp = _fake_lisp(["42"])
        with patch("sys.stdout", new_callable=StringIO) as out:
            ok = _eval_and_print(lisp, "(+ 1 41)")  # type: ignore[arg-type]
        self.assertTrue(ok)
        self.assertEqual(out.getvalue(), "42\n")

    def test_empty_result_no_output(self) -> None:
        lisp = _fake_lisp([""])
        with patch("sys.stdout", new_callable=StringIO) as out:
            ok = _eval_and_print(lisp, "(values)")  # type: ignore[arg-type]
        self.assertTrue(ok)
        self.assertEqual(out.getvalue(), "")

    def test_ecl_error_returns_false(self) -> None:
        lisp = SimpleNamespace(session=MagicMock())
        lisp.session.eval.side_effect = EclError("oops")
        with patch("sys.stderr", new_callable=StringIO) as err:
            ok = _eval_and_print(lisp, "(error)")  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertIn("oops", err.getvalue())


class RunTests(unittest.TestCase):
    def test_success_returns_0(self) -> None:
        self.assertEqual(_run(_fake_lisp(["42"]), "(+ 1 41)"), 0)  # type: ignore[arg-type]

    def test_error_returns_1(self) -> None:
        lisp = SimpleNamespace(session=MagicMock())
        lisp.session.eval.side_effect = EclError("boom")
        with patch("sys.stderr", new_callable=StringIO):
            self.assertEqual(_run(lisp, "(error)"), 1)  # type: ignore[arg-type]


class CurrentPackageTests(unittest.TestCase):
    def test_returns_package_name(self) -> None:
        lisp = _fake_lisp([])
        self.assertEqual(_current_package(lisp, "?"), "ECLPY-USER")  # type: ignore[arg-type]

    def test_fallback_on_error(self) -> None:
        lisp = SimpleNamespace(session=MagicMock())
        lisp.session.eval.side_effect = EclError("err")
        self.assertEqual(_current_package(lisp, "FALLBACK"), "FALLBACK")  # type: ignore[arg-type]


class ReadlineTests(unittest.TestCase):
    def test_setup_returns_module_and_loads_history(self) -> None:
        mock_rl = MagicMock()
        with patch.dict("sys.modules", {"readline": mock_rl}):
            result = _setup_readline()
        self.assertIs(result, mock_rl)
        mock_rl.read_history_file.assert_called_once()
        mock_rl.set_history_length.assert_called_once_with(1000)

    def test_setup_missing_history_file(self) -> None:
        mock_rl = MagicMock()
        mock_rl.read_history_file.side_effect = OSError("no file")
        with patch.dict("sys.modules", {"readline": mock_rl}):
            result = _setup_readline()
        self.assertIs(result, mock_rl)

    def test_setup_no_module_returns_none(self) -> None:
        with patch.dict("sys.modules", {"readline": None}):
            result = _setup_readline()
        self.assertIsNone(result)

    def test_save_calls_write(self) -> None:
        mock_rl = MagicMock()
        _save_readline_history(mock_rl)
        mock_rl.write_history_file.assert_called_once()

    def test_save_oserror_is_silent(self) -> None:
        mock_rl = MagicMock()
        mock_rl.write_history_file.side_effect = OSError("no write")
        _save_readline_history(mock_rl)  # must not raise

    def test_save_none_is_noop(self) -> None:
        _save_readline_history(None)  # must not raise


class ReplTests(unittest.TestCase):
    def setUp(self) -> None:
        self._p_setup = patch("eclpy.__main__._setup_readline", return_value=None)
        self._p_save = patch("eclpy.__main__._save_readline_history")
        self._p_setup.start()
        self._p_save.start()

    def tearDown(self) -> None:
        self._p_setup.stop()
        self._p_save.stop()

    def _inputs(self, *items: object):
        it = iter(items)

        def fake_input(prompt: str = "") -> str:
            val = next(it)
            if isinstance(val, BaseException):
                raise val
            return val  # type: ignore[return-value]

        return fake_input

    def test_basic_eval(self) -> None:
        lisp = _fake_lisp(["3"])
        with patch("builtins.input", side_effect=self._inputs("(+ 1 2)", EOFError())), \
             patch("sys.stdout", new_callable=StringIO) as out:
            _repl(lisp)  # type: ignore[arg-type]
        self.assertIn("3", out.getvalue())

    def test_empty_line_skipped(self) -> None:
        lisp = _fake_lisp([])
        with patch("builtins.input", side_effect=self._inputs("", EOFError())), \
             patch("sys.stdout", new_callable=StringIO):
            _repl(lisp)  # type: ignore[arg-type]

    def test_multiline_form(self) -> None:
        lisp = _fake_lisp(["T"])
        with patch("builtins.input", side_effect=self._inputs("(defun foo (x)", "  x)", EOFError())), \
             patch("sys.stdout", new_callable=StringIO) as out:
            _repl(lisp)  # type: ignore[arg-type]
        self.assertIn("T", out.getvalue())

    def test_eof_during_continuation(self) -> None:
        lisp = _fake_lisp([])
        with patch("builtins.input", side_effect=self._inputs("(foo", EOFError())), \
             patch("sys.stdout", new_callable=StringIO):
            _repl(lisp)  # type: ignore[arg-type]

    def test_ecl_error_continues_repl(self) -> None:
        lisp = SimpleNamespace(session=MagicMock())

        def side_effect(code: str) -> str:
            if "(package-name *package*)" in code or "defpackage" in code:
                return ""
            raise EclError("bad form")

        lisp.session.eval.side_effect = side_effect
        with patch("builtins.input", side_effect=self._inputs("(bad)", EOFError())), \
             patch("sys.stderr", new_callable=StringIO) as err:
            _repl(lisp)  # type: ignore[arg-type]
        self.assertIn("bad form", err.getvalue())

    def test_pkg_error_falls_back_to_question_mark(self) -> None:
        lisp = SimpleNamespace(session=MagicMock())
        lisp.session.eval.side_effect = EclError("pkg error")
        with patch("builtins.input", side_effect=self._inputs(EOFError())) as mock_input, \
             patch("sys.stdout", new_callable=StringIO):
            _repl(lisp)  # type: ignore[arg-type]
        mock_input.assert_called_once_with("?> ")

    def test_empty_result_no_extra_output(self) -> None:
        lisp = _fake_lisp([""])
        with patch("builtins.input", side_effect=self._inputs("(values)", EOFError())), \
             patch("sys.stdout", new_callable=StringIO) as out:
            _repl(lisp)  # type: ignore[arg-type]
        self.assertEqual(out.getvalue(), "\n")

    def test_prompt_updates_after_in_package(self) -> None:
        session = MagicMock()
        call_count = 0

        def side_effect(code: str) -> str:
            nonlocal call_count
            if "defpackage" in code:
                return ""
            if "(package-name *package*)" in code:
                return '"CL-USER"' if call_count > 0 else '"ECLPY-USER"'
            call_count += 1
            return ""

        lisp = SimpleNamespace(session=session)
        session.eval.side_effect = side_effect
        prompts: list[str] = []

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            if len(prompts) == 1:
                return "(in-package :cl-user)"
            raise EOFError

        with patch("builtins.input", side_effect=fake_input), \
             patch("sys.stdout", new_callable=StringIO):
            _repl(lisp)  # type: ignore[arg-type]

        self.assertEqual(prompts[0], "ECLPY-USER> ")
        self.assertEqual(prompts[1], "CL-USER> ")


class MainTests(unittest.TestCase):
    def _patched_lisp(self, responses: list[str]):
        mock_instance = MagicMock()
        mock_instance.__enter__ = lambda s: s
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.session = FakeSession(responses)
        return mock_instance

    def test_eval_flag(self) -> None:
        lisp = self._patched_lisp(["5"])
        with patch("eclpy.__main__.Lisp", return_value=lisp), \
             patch("sys.argv", ["eclpy", "-e", "(+ 2 3)"]), \
             patch("sys.stdout", new_callable=StringIO) as out:
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(out.getvalue(), "5\n")

    def test_eval_error(self) -> None:
        lisp = MagicMock()
        lisp.__enter__ = lambda s: s
        lisp.__exit__ = MagicMock(return_value=False)
        lisp.session = MagicMock()
        lisp.session.eval.side_effect = EclError("boom")
        with patch("eclpy.__main__.Lisp", return_value=lisp), \
             patch("sys.argv", ["eclpy", "-e", "(boom)"]), \
             patch("sys.stderr", new_callable=StringIO):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertEqual(ctx.exception.code, 1)

    def test_file(self) -> None:
        lisp = self._patched_lisp(["NIL"])
        with tempfile.NamedTemporaryFile(suffix=".lisp", mode="w", delete=False) as f:
            f.write("(print nil)")
            fname = f.name
        try:
            with patch("eclpy.__main__.Lisp", return_value=lisp), \
                 patch("sys.argv", ["eclpy", fname]), \
                 patch("sys.stdout", new_callable=StringIO) as out:
                with self.assertRaises(SystemExit) as ctx:
                    main()
        finally:
            Path(fname).unlink()
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(out.getvalue(), "NIL\n")

    def test_file_not_found(self) -> None:
        lisp = self._patched_lisp([])
        with patch("eclpy.__main__.Lisp", return_value=lisp), \
             patch("sys.argv", ["eclpy", "/no/such/file.lisp"]), \
             patch("sys.stderr", new_callable=StringIO):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertEqual(ctx.exception.code, 1)

    def test_repl(self) -> None:
        lisp = self._patched_lisp(["7"])
        with patch("eclpy.__main__.Lisp", return_value=lisp), \
             patch("sys.argv", ["eclpy"]), \
             patch("builtins.input", side_effect=lambda p="": (_ for _ in ()).throw(EOFError())), \
             patch("sys.stdout", new_callable=StringIO):
            main()

