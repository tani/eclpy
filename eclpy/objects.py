"""Python objects that model Common Lisp values.

These classes are the concrete results returned by protocol decoding when a
Lisp value has no exact built-in Python equivalent: symbols preserve package
identity, proper lists preserve Lisp list semantics, conses preserve dotted
tails, and references preserve ownership of opaque Lisp objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from .errors import EclError

if TYPE_CHECKING:
    from collections.abc import Iterator


class Symbol:
    """A Common Lisp symbol.

    :param name: Symbol name exactly as reported by Lisp.
    :param package: Package name, or ``None`` for an uninterned symbol.
    """

    def __init__(self, name: str, package: str | None = None) -> None:
        """Create a symbol value, rejecting the empty name."""
        if not name:
            message = "symbol name cannot be empty"
            raise ValueError(message)
        self.name = name
        self.package = package

    def __repr__(self) -> str:
        """Return a constructor-style representation."""
        if self.package is None:
            return f"Symbol({self.name!r})"
        return f"Symbol({self.name!r}, {self.package!r})"

    def __eq__(self, other: object) -> bool:
        """Compare symbol identity by name and package."""
        return isinstance(other, Symbol) and (self.name, self.package) == (
            other.name,
            other.package,
        )

    def __hash__(self) -> int:
        """Hash symbol identity by name and package."""
        return hash((self.name, self.package))


class List(tuple[Any, ...]):
    """A Lisp proper list represented as an immutable tuple subclass.

    ``List()`` is how eclpy represents Lisp ``NIL`` when it is decoded as data.
    It remains distinct from Python ``False`` even though both encode to Lisp
    ``nil`` on the Python-to-Lisp path.
    """

    __slots__ = ()

    def __new__(cls, *items: Any) -> Self:
        """Create a proper list from positional items."""
        return super().__new__(cls, items)

    @property
    def car(self) -> Any:
        """Return the first item in the list.

        :raises IndexError: If the list is empty.
        """
        if not self:
            message = "empty Lisp list has no car"
            raise IndexError(message)
        return self[0]

    @property
    def cdr(self) -> List:
        """Return a proper list containing every item after :attr:`car`.

        :raises IndexError: If the list is empty.
        """
        if not self:
            message = "empty Lisp list has no cdr"
            raise IndexError(message)
        return List(*self[1:])

    def __repr__(self) -> str:
        """Render empty lists as ``()`` and non-empty lists as ``List(...)``."""
        if not self:
            return "()"
        return f"List({', '.join(repr(item) for item in self)})"


@dataclass
class Cons:
    """A mutable Lisp cons cell.

    A chain ending in :class:`List` or ``None``/``False`` behaves as a proper
    list during iteration. Any other tail is a dotted list and is intentionally
    not iterable as a flat Python sequence.
    """

    car: Any
    cdr: Any = field(default_factory=List)

    def __iter__(self) -> Iterator[Any]:
        """Iterate over a proper cons chain.

        :raises TypeError: For circular or dotted lists.
        """
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
        """Render proper, dotted, and circular cons chains readably."""
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
    """A Python-owned handle to an opaque Lisp object.

    Functions, packages, streams, and other non-serializable Lisp values stay in
    a helper hash table inside ECL. ``Reference`` stores the object id and
    releases that table entry when closed.
    """

    lisp: Any
    object_id: int
    type_name: str
    released: bool = False

    def release(self) -> None:
        """Release the referenced Lisp object from the ECL helper table."""
        if not self.released:
            self.lisp._release_reference(self)

    def __enter__(self) -> Self:
        """Enter a context that owns the live Lisp reference."""
        if self.released:
            message = "cannot enter a released Lisp reference"
            raise EclError(message)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Release the reference when leaving a context manager block."""
        self.release()

    def __repr__(self) -> str:
        """Return a representation that includes released state when relevant."""
        state = ", released=True" if self.released else ""
        return f"Reference({self.object_id}, {self.type_name!r}{state})"
