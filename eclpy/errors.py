"""Error types shared across the eclpy package."""

from __future__ import annotations


class EclError(RuntimeError):
    """Raised when the ECL WebAssembly runtime cannot evaluate Lisp code."""

    def __init__(self, message: str, *, condition_type: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.condition_type = condition_type
