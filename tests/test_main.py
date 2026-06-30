from __future__ import annotations

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
    _enter_handler,
    _eval_and_print,
    _make_prompt_session,
    _repl,
    _run,
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
        self.assertFalse(_balanced(")))"))

    def test_close_before_open(self) -> None:
        self.assertFalse(_balanced(")("))

    def test_unbalanced_closed_by_string(self) -> None:
        self.assertFalse(_balanced('(foo "bar)'))

    def test_unclosed_string(self) -> None:
        self.assertFalse(_balanced('"unterminated'))

    def test_trailing_string_escape(self) -> None:
        self.assertFalse(_balanced('"unterminated\\'))

    def test_comment_paren_ignored(self) -> None:
        self.assertFalse(_balanced("(foo ; comment )\n"))

    def test_comment_closes_line(self) -> None:
        self.assertTrue(_balanced("(foo ; comment\n  )"))

    def test_escape_in_string(self) -> None:
        self.assertTrue(_balanced('(foo "a\\"b")'))

    def test_comment_at_end_of_input_no_newline(self) -> None:
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


class MakePromptSessionTests(unittest.TestCase):
    def test_returns_session_when_deps_available(self) -> None:
        result = _make_prompt_session(Path("/tmp/test_history"))
        self.assertIsNotNone(result)

    def test_returns_none_when_import_fails(self) -> None:
        with patch.dict("sys.modules", {"prompt_toolkit": None}):
            result = _make_prompt_session(Path("/tmp/test_history"))
        self.assertIsNone(result)


class EnterHandlerTests(unittest.TestCase):
    def _make_event(self, text: str) -> MagicMock:
        mock_buf = MagicMock()
        mock_buf.text = text
        mock_event = MagicMock()
        mock_event.app.current_buffer = mock_buf
        return mock_event

    def test_submits_when_balanced(self) -> None:
        event = self._make_event("(+ 1 2)")
        _enter_handler(event)
        event.app.current_buffer.validate_and_handle.assert_called_once()
        event.app.current_buffer.insert_text.assert_not_called()

    def test_inserts_newline_when_unbalanced(self) -> None:
        event = self._make_event("(defun foo (x)")
        _enter_handler(event)
        event.app.current_buffer.insert_text.assert_called_once_with("\n")
        event.app.current_buffer.validate_and_handle.assert_not_called()


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


class ReplTests(unittest.TestCase):
    def setUp(self) -> None:
        self._p_ps = patch("eclpy.__main__._make_prompt_session", return_value=None)
        self._p_ps.start()

    def tearDown(self) -> None:
        self._p_ps.stop()

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
        inputs = self._inputs("(defun foo (x)", "  x)", EOFError())
        with patch("builtins.input", side_effect=inputs), \
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

    def test_keyboard_interrupt_continues(self) -> None:
        lisp = _fake_lisp([])
        call_count = 0

        def fake_input(prompt: str = "") -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise KeyboardInterrupt
            raise EOFError

        with patch("builtins.input", side_effect=fake_input), \
             patch("sys.stdout", new_callable=StringIO):
            _repl(lisp)  # type: ignore[arg-type]
        self.assertEqual(call_count, 2)

    def test_prompt_session_used_when_available(self) -> None:
        lisp = _fake_lisp(["5"])
        mock_ps = MagicMock()
        call_count = 0

        def fake_prompt(prompt: str = "") -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "(+ 2 3)"
            raise EOFError

        mock_ps.prompt.side_effect = fake_prompt

        with patch("eclpy.__main__._make_prompt_session", return_value=mock_ps), \
             patch("sys.stdout", new_callable=StringIO) as out:
            _repl(lisp)  # type: ignore[arg-type]
        self.assertIn("5", out.getvalue())
        mock_ps.prompt.assert_called()

    def test_prompt_session_keyboard_interrupt(self) -> None:
        lisp = _fake_lisp([])
        mock_ps = MagicMock()
        call_count = 0

        def fake_prompt(prompt: str = "") -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise KeyboardInterrupt
            raise EOFError

        mock_ps.prompt.side_effect = fake_prompt

        with patch("eclpy.__main__._make_prompt_session", return_value=mock_ps), \
             patch("sys.stdout", new_callable=StringIO):
            _repl(lisp)  # type: ignore[arg-type]
        self.assertEqual(call_count, 2)


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

    def test_eval_flag_accepts_empty_expression(self) -> None:
        lisp = self._patched_lisp([""])
        with patch("eclpy.__main__.Lisp", return_value=lisp), \
             patch("sys.argv", ["eclpy", "-e", ""]), \
             patch("sys.stdout", new_callable=StringIO):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertEqual(ctx.exception.code, 0)

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
             patch("eclpy.__main__._make_prompt_session", return_value=None), \
             patch("builtins.input", side_effect=lambda p="": (_ for _ in ()).throw(EOFError())), \
             patch("sys.stdout", new_callable=StringIO):
            main()
