from __future__ import annotations

import os
from pathlib import Path

import pytest

from ecl import EclError, EclSession


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_WASM = ROOT / "ecl" / "ecl_eval.wasm"
BUILD_WASM = ROOT / "build" / "ecl" / "ecl_eval.wasm"


def require_wasm() -> Path:
    wasm_path = Path(os.environ["ECL_WASM"]) if "ECL_WASM" in os.environ else None
    wasm_path = wasm_path or (PACKAGE_WASM if PACKAGE_WASM.is_file() else BUILD_WASM)
    if not wasm_path.is_file():
        pytest.skip("ECL WASM artifact is not built")
    return wasm_path


def test_missing_wasm_has_actionable_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.wasm"
    with pytest.raises(FileNotFoundError, match="build_ecl_wasm.py"):
        EclSession(missing)


def test_eval_arithmetic() -> None:
    with EclSession(require_wasm()) as ecl:
        assert ecl.eval("(+ 1 2)") == "3"


def test_eval_multiple_forms_returns_last_value() -> None:
    with EclSession(require_wasm()) as ecl:
        assert ecl.eval("(+ 1 2)\n(+ 3 4)") == "7"


def test_eval_keeps_session_state() -> None:
    with EclSession(require_wasm()) as ecl:
        assert ecl.eval("(defparameter *ecl-test-value* 41)") == "*ECL-TEST-VALUE*"
        assert ecl.eval("(1+ *ecl-test-value*)") == "42"


def test_eval_error_raises_ecl_error() -> None:
    with EclSession(require_wasm()) as ecl:
        with pytest.raises(EclError):
            ecl.eval("(definitely-not-a-bound-function)")


def test_lisp_error_condition_raises_ecl_error() -> None:
    with EclSession(require_wasm()) as ecl:
        with pytest.raises(EclError, match="ECL evaluation escaped"):
            ecl.eval('(error "boom from Lisp")')
