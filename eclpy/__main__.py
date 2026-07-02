"""Command-line interface for eclpy: Common Lisp with Python interop."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Protocol

from .errors import EclError
from .lisp import Lisp

_REPL_INIT = """
(defpackage #:eclpy-user (:use #:ecl-python #:cl))
(in-package #:eclpy-user)
"""

_HISTORY_FILE = Path.home() / ".eclpy_history"
_CONTINUATION_PROMPT = "  "


class _PromptSession(Protocol):
    def prompt(self, message: str = "") -> str: ...


def _balanced(text: str) -> bool:
    depth = 0
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif ch == "\\" and in_string:
            escape = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == ";":
                i = text.find("\n", i)
                if i == -1:
                    break
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    return False
        i += 1
    return depth == 0 and not in_string and not escape


def _enter_handler(event: Any) -> None:
    buf = event.app.current_buffer
    if _balanced(buf.text):
        buf.validate_and_handle()
    else:
        buf.insert_text("\n")


def _make_prompt_session(history_file: Path) -> _PromptSession | None:
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.lexers import PygmentsLexer
        from prompt_toolkit.styles import style_from_pygments_cls
        from pygments.lexers import CommonLispLexer
        from pygments.styles import get_style_by_name
    except ImportError:
        return None

    kb = KeyBindings()
    kb.add("enter")(_enter_handler)

    return PromptSession(
        lexer=PygmentsLexer(CommonLispLexer),
        style=style_from_pygments_cls(get_style_by_name("native")),
        history=FileHistory(str(history_file)),
        key_bindings=kb,
        multiline=True,
    )


def _read_stdin_form(prompt: str) -> str:
    source = input(prompt)
    while not _balanced(source):
        source += "\n" + input(_CONTINUATION_PROMPT)
    return source


def _eval_and_print(lisp: Lisp, source: str) -> bool:
    try:
        result = lisp.session.eval(source)
        if result:
            print(result)
        return True
    except EclError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return False


def _current_package(lisp: Lisp, fallback: str) -> str:
    try:
        return lisp.session.eval("(package-name *package*)").strip('"')
    except EclError:
        return fallback


def _repl(lisp: Lisp) -> None:
    try:
        lisp.session.eval(_REPL_INIT)
    except EclError:
        pass
    ps = _make_prompt_session(_HISTORY_FILE)
    pkg = _current_package(lisp, "?")

    while True:
        prompt = f"{pkg}> "
        try:
            source = ps.prompt(prompt) if ps is not None else _read_stdin_form(prompt)
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue

        if not source.strip():
            continue

        if _eval_and_print(lisp, source):
            pkg = _current_package(lisp, pkg)


def _run(lisp: Lisp, source: str) -> int:
    return 0 if _eval_and_print(lisp, source) else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="eclpy",
        description="Common Lisp REPL/runner with Python interop (ECL via WebAssembly)",
    )
    parser.add_argument("-e", "--eval", metavar="EXPR", help="evaluate EXPR and exit")
    parser.add_argument("file", nargs="?", metavar="FILE", help="Lisp source file to run")
    parser.add_argument(
        "--swank",
        nargs="?",
        type=int,
        const=4005,
        default=None,
        metavar="PORT",
        help=(
            "start a SWANK/SLIME server on PORT (default 4005) instead of "
            "evaluating -e/FILE or starting the REPL; blocks until interrupted"
        ),
    )
    args = parser.parse_args()

    with Lisp() as lisp:
        if args.swank is not None:
            print(f"eclpy: starting SWANK on port {args.swank} (Ctrl-C to stop)", file=sys.stderr)
            try:
                lisp.start_swank(args.swank)
            except KeyboardInterrupt:
                sys.exit(0)
            return

        if args.eval is not None:
            sys.exit(_run(lisp, args.eval))
        elif args.file:
            path = Path(args.file)
            try:
                source = path.read_text(encoding="utf-8")
            except OSError as exc:
                print(f"eclpy: {exc}", file=sys.stderr)
                sys.exit(1)
            sys.exit(_run(lisp, source))
        else:
            _repl(lisp)


if __name__ == "__main__":  # pragma: no cover
    main()
