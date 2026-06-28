"""Python objects that model Common Lisp values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from .session import EclError

if TYPE_CHECKING:
    from collections.abc import Iterator


class Symbol:
    """A Common Lisp symbol."""

    def __init__(self, name: str, package: str | None = None) -> None:
        if not name:
            message = "symbol name cannot be empty"
            raise ValueError(message)
        self.name = name
        self.package = package

    def __repr__(self) -> str:
        if self.package is None:
            return f"Symbol({self.name!r})"
        return f"Symbol({self.name!r}, {self.package!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Symbol) and (self.name, self.package) == (
            other.name,
            other.package,
        )

    def __hash__(self) -> int:
        return hash((self.name, self.package))


class List(tuple[Any, ...]):
    """A Lisp proper list."""

    __slots__ = ()

    def __new__(cls, *items: Any) -> Self:
        """Create a proper list from positional items."""
        return super().__new__(cls, items)

    @property
    def car(self) -> Any:
        """Return the first item in the list."""
        if not self:
            message = "empty Lisp list has no car"
            raise IndexError(message)
        return self[0]

    @property
    def cdr(self) -> List:
        """Return a list containing every item after ``car``."""
        if not self:
            message = "empty Lisp list has no cdr"
            raise IndexError(message)
        return List(*self[1:])

    def __repr__(self) -> str:
        if not self:
            return "()"
        return f"List({', '.join(repr(item) for item in self)})"


@dataclass
class Cons:
    """A mutable Lisp cons cell."""

    car: Any
    cdr: Any = field(default_factory=List)

    def __iter__(self) -> Iterator[Any]:
        seen: set[int] = set()
        tail: Any = self
        while isinstance(tail, Cons):
            ident = id(tail)
            if ident in seen:
                message = "cannot iterate over a circular Lisp list"
                raise TypeError(message)
            seen.add(ident)
            yield tail.car
            tail = tail.cdr
        if tail in (None, False) or tail == List():
            return
        if isinstance(tail, List):
            yield from tail
            return
        message = "cannot iterate over a dotted Lisp list"
        raise TypeError(message)

    def __repr__(self) -> str:
        items: list[str] = []
        seen: set[int] = set()
        tail: Any = self
        while isinstance(tail, Cons):
            ident = id(tail)
            if ident in seen:
                items.append("...")
                return f"DottedList({', '.join(items)})"
            seen.add(ident)
            items.append(repr(tail.car))
            tail = tail.cdr
        if tail in (None, False) or tail == List():
            return f"List({', '.join(items)})"
        if isinstance(tail, List):
            items.extend(repr(item) for item in tail)
            return f"List({', '.join(items)})"
        if len(items) == 1:
            return f"Cons({items[0]}, {tail!r})"
        items.append(repr(tail))
        return f"DottedList({', '.join(items)})"


@dataclass
class Reference:
    """A handle to a Lisp object that cannot be copied into Python."""

    lisp: Any
    object_id: int
    type_name: str
    released: bool = False

    def release(self) -> None:
        """Release the referenced Lisp object from the ECL helper table."""
        if not self.released:
            self.lisp._release_reference(self)

    def __enter__(self) -> Self:
        if self.released:
            message = "cannot enter a released Lisp reference"
            raise EclError(message)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    def __repr__(self) -> str:
        state = ", released=True" if self.released else ""
        return f"Reference({self.object_id}, {self.type_name!r}{state})"
