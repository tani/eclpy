"""Common Lisp PY DSL source loaded into every high-level ECL session.

The actual Lisp lives in ``python.lisp`` next to this module so it can be edited
and reviewed as Lisp source rather than an embedded Python string.
"""

from __future__ import annotations

from pathlib import Path

PYTHON_SOURCE = Path(__file__).with_name("python.lisp")

PY_SOURCE = PYTHON_SOURCE.read_text(encoding="utf-8")
