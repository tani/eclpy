"""Error types shared across the eclpy package."""

from __future__ import annotations


class EclError(RuntimeError):
    """Raised when the ECL WebAssembly runtime cannot evaluate Lisp code.

    :param message: Human-readable error report.
    :param condition_type: Optional Lisp condition type name when the error came
        from a high-level protocol envelope.
    """

    def __init__(self, message: str, *, condition_type: str | None = None) -> None:
        """Create an ECL error while preserving the optional condition type."""
        super().__init__(message)
        self.message = message
        self.condition_type = condition_type
