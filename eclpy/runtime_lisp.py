"""Common Lisp helper source loaded into every high-level ECL session.

The actual Lisp lives in ``runtime.lisp`` next to this module so it can be
edited and reviewed as Lisp source rather than an embedded Python string.
"""

from __future__ import annotations

from pathlib import Path

RUNTIME_SOURCE = Path(__file__).with_name("runtime.lisp")

HELPER_SOURCE = RUNTIME_SOURCE.read_text(encoding="utf-8")
