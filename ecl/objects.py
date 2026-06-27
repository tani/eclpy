from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Self

from .session import EclError


class Symbol:
    """A Common Lisp symbol."""

    def __init__(self, name: str, package: str | None = None) -> None:
        if not name:
            raise ValueError("symbol name cannot be empty")
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

    def __new__(cls, *items: Any) -> Self:
        return super().__new__(cls, items)

    @property
    def car(self) -> Any:
        if not self:
            raise IndexError("empty Lisp list has no car")
        return self[0]

    @property
    def cdr(self) -> List:
        if not self:
            raise IndexError("empty Lisp list has no cdr")
        return List(*self[1:])

    def __repr__(self) -> str:
        if not self:
            return "()"
        return f"List({', '.join(repr(item) for item in self)})"


@dataclass
class Cons:
    """A mutable Lisp cons cell."""

    car: Any
    cdr: Any = List()

    def __iter__(self) -> Iterator[Any]:
        seen: set[int] = set()
        tail: Any = self
        while isinstance(tail, Cons):
            ident = id(tail)
            if ident in seen:
                raise TypeError("cannot iterate over a circular Lisp list")
            seen.add(ident)
            yield tail.car
            tail = tail.cdr
        if tail in (None, False) or tail == List():
            return
        if isinstance(tail, List):
            yield from tail
            return
        raise TypeError("cannot iterate over a dotted Lisp list")

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
class LispReference:
    """A handle to a Lisp object that cannot be copied into Python."""

    lisp: Any
    object_id: int
    type_name: str
    released: bool = False

    def release(self) -> None:
        if not self.released:
            self.lisp._release_reference(self)

    def __enter__(self) -> LispReference:
        if self.released:
            raise EclError("cannot enter a released Lisp reference")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    def __repr__(self) -> str:
        state = ", released=True" if self.released else ""
        return f"LispReference({self.object_id}, {self.type_name!r}{state})"
