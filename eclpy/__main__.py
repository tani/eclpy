"""Command-line interface for eclpy: Common Lisp with Python interop."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .errors import EclError
from .lisp import Lisp

_REPL_INIT = """
(defpackage #:eclpy-user (:use #:ecl-python #:cl))
(in-package #:eclpy-user)
"""

_HISTORY_FILE = Path.home() / ".eclpy_history"


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
        i += 1
    return depth == 0


def _setup_readline() -> Any:
    try:
        import readline
    except ImportError:
        return None
    try:
        readline.read_history_file(_HISTORY_FILE)
    except OSError:
        pass
    readline.set_history_length(1000)
    return readline


def _save_readline_history(rl: Any) -> None:
    if rl is None:
        return
    try:
        rl.write_history_file(_HISTORY_FILE)
    except OSError:
        pass


def _highlight(text: str) -> str:
    if not sys.stdout.isatty():
        return text
    try:
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import CommonLispLexer
        return highlight(text, CommonLispLexer(), TerminalFormatter()).rstrip("\n")
    except ImportError:
        return text


def _eval_and_print(lisp: Lisp, source: str) -> bool:
    try:
        result = lisp.session.eval(source)
        if result:
            print(_highlight(result))
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
    rl = _setup_readline()
    pkg = _current_package(lisp, "?")

    while True:
        try:
            line = input(f"{pkg}> ")
        except EOFError:
            print()
            break

        if not line.strip():
            continue

        buf = line
        while not _balanced(buf):
            try:
                buf += "\n" + input("  ")
            except EOFError:
                print()
                _save_readline_history(rl)
                return

        if _eval_and_print(lisp, buf):
            pkg = _current_package(lisp, pkg)

    _save_readline_history(rl)


def _run(lisp: Lisp, source: str) -> int:
    return 0 if _eval_and_print(lisp, source) else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="eclpy",
        description="Common Lisp REPL/runner with Python interop (ECL via WebAssembly)",
    )
    parser.add_argument("-e", "--eval", metavar="EXPR", help="evaluate EXPR and exit")
    parser.add_argument("file", nargs="?", metavar="FILE", help="Lisp source file to run")
    args = parser.parse_args()

    with Lisp() as lisp:
        if args.eval:
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
