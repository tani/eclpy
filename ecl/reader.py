from __future__ import annotations

import re
from typing import Any

from lark import Lark, Transformer, UnexpectedInput, v_args

from .session import EclError


_INTEGER_RE = re.compile(r"^[+-]?\d+$")

_GRAMMAR = r"""
start: sexp
?sexp: list
     | STRING -> string
     | ATOM -> atom

list: "(" sexp* ")"

ATOM: /[^()\s"]+/
STRING: /"([^"\\]|\\.)*"/s

%import common.WS
%ignore WS
"""

_PARSER = Lark(_GRAMMAR, parser="lalr")


def parse_one(source: str) -> Any:
    try:
        return _TreeToPython().transform(_PARSER.parse(source))
    except UnexpectedInput as exc:
        raise EclError(f"invalid ECL result syntax: {source!r}") from exc


@v_args(inline=True)
class _TreeToPython(Transformer[Any, Any]):
    def start(self, value: Any) -> Any:
        return value

    def list(self, *items: Any) -> list[Any]:
        return list(items)

    def atom(self, token: Any) -> str | int:
        value = str(token)
        if _INTEGER_RE.match(value):
            return int(value)
        return value

    def string(self, token: Any) -> str:
        return _unescape_lisp_string(str(token))


def _unescape_lisp_string(source: str) -> str:
    value = source[1:-1]
    chars: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        index += 1
        if char == "\\":
            if index >= len(value):
                raise EclError(f"invalid ECL string escape: {source!r}")
            chars.append(value[index])
            index += 1
        else:
            chars.append(char)
    return "".join(chars)
