"""High-level Python API for evaluating Common Lisp through ECL."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from .errors import EclError
from .objects import Reference
from .protocol import decode_result
from .runtime_lisp import HELPER_SOURCE
from .session import EclSession
from .sexp import SExp

if TYPE_CHECKING:
    from os import PathLike

ASDF_SOURCE = Path(__file__).with_name("asdf.lisp")
SWANK_SOURCE_DIRECTORY = Path(__file__).with_name("swank")


class Lisp:
    """A cl4py-like interface to an ECL WebAssembly session."""

    def __init__(
        self,
        wasm_path: str | PathLike[str] | None = None,
        *,
        session: EclSession | None = None,
    ) -> None:
        self.session = session or EclSession(wasm_path)
        self._owns_session = session is None
        self._closed = False
        self._references: dict[int, Reference] = {}
        self.session.eval(HELPER_SOURCE)
        form = SExp.list(
            SExp.atom("setf"),
            SExp.atom("ecl-python:*asdf-source*"),
            SExp.raw(f"#p{SExp.string(str(ASDF_SOURCE))}"),
        )
        self.session.eval(str(form))
        swank_form = SExp.list(
            SExp.atom("setf"),
            SExp.atom("ecl-python:*swank-source-directory*"),
            SExp.raw(f"#p{SExp.string(str(SWANK_SOURCE_DIRECTORY) + '/')}"),
        )
        self.session.eval(str(swank_form))

    def eval(self, form: Any) -> Any:
        """Evaluate an explicit S-expression."""
        if not isinstance(form, SExp):
            message = "Lisp.eval only accepts SExp; use eclpy.SExp.* or eclpy.syntax.expr(...)"
            raise TypeError(message)
        return self._eval_sexp(form)

    def start_swank(self, port: int = 4005, *, dont_close: bool = True) -> None:
        """Start a SWANK server and block the calling thread serving requests.

        Unlike :meth:`eval`, this bypasses the JSON evaluation protocol and
        calls the session directly. ``eval``'s ``ecl-python:evaluate`` wraps
        every call in a ``handler-case`` that traps *all* conditions so they
        can be reported back to Python as an :class:`EclError` -- but SWANK
        needs unhandled conditions raised inside a request to reach ECL's
        native ``*debugger-hook*`` so its own SLDB debugger can intercept
        them instead. Run this from a background thread; it does not return
        while the server is serving requests (i.e. whenever ``dont_close``
        is true, which is the default).
        """
        form = SExp.list(
            SExp.atom("ecl-python:start-swank"),
            SExp.keyword("port"),
            SExp.integer(port),
            SExp.keyword("dont-close"),
            SExp.atom("t") if dont_close else SExp.atom("nil"),
        )
        self.session.eval(str(form))

    def close(self) -> None:
        """Release Lisp references and close the owned ECL session."""
        if self._closed:
            return
        self._release_all_references()
        if self._owns_session:
            self.session.close()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _eval_sexp(self, sexp: SExp) -> Any:
        if self._closed:
            message = "Lisp session is closed"
            raise EclError(message)
        result = self._eval_helper(
            SExp.list(
                SExp.atom("ecl-python:evaluate"),
                SExp.list(SExp.symbol("LAMBDA"), SExp.list(), sexp),
            )
        )
        return decode_result(result, self)

    def _eval_helper(self, sexp: SExp) -> Any:
        return json.loads(self.session.eval_json(str(sexp)))

    def _make_reference(self, object_id: int, type_name: str) -> Reference:
        reference = Reference(self, object_id, type_name)
        self._references[object_id] = reference
        return reference

    def _release_reference(self, reference: Reference) -> None:
        if reference.released:
            return
        self._references.pop(reference.object_id, None)
        if not self._closed:
            with suppress(EclError):
                self.session.eval(
                    str(
                        SExp.list(
                            SExp.atom("ecl-python:release-object"),
                            SExp.integer(reference.object_id),
                        )
                    )
                )
        reference.released = True

    def _release_all_references(self) -> None:
        if self._references and not self._closed:
            with suppress(EclError):
                self.session.eval(str(SExp.list(SExp.atom("ecl-python:release-all-objects"))))
        for reference in self._references.values():
            reference.released = True
        self._references.clear()
