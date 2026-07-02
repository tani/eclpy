"""Low-level Wasmtime host for the ECL WebAssembly runtime.

``EclSession`` is intentionally string-oriented: callers pass Lisp source text
and receive either printed text or the JSON text produced by ``runtime.lisp``.
All Python object conversion, reference ownership, and public API restrictions
live above this layer.
"""

from __future__ import annotations

import builtins
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import wasmtime

from .errors import EclError
from .hostenv import define_emscripten_imports
from .wasmmem import read_c_string

if TYPE_CHECKING:
    from types import TracebackType
    from typing import Self

PACKAGE_WASM = Path(__file__).with_name("ecl_eval.wasm")
BUILD_WASM = Path(__file__).resolve().parents[1] / "build" / "eclpy" / "ecl_eval.wasm"

class EclSession:
    """A persistent ECL process hosted inside one WebAssembly instance.

    The session owns Wasmtime engine/store state, WASI setup, Emscripten-style
    host imports, and the C ABI exports used to allocate buffers and evaluate
    Lisp forms.
    """

    def __init__(self, wasm_path: str | os.PathLike[str] | None = None) -> None:
        """Load and initialize the ECL WebAssembly module.

        :param wasm_path: Optional module path. When omitted, ``ECL_WASM`` wins
            over the packaged artifact, and the build-tree artifact is the last
            fallback.
        :raises FileNotFoundError: If no runtime artifact can be found.
        :raises eclpy.EclError: If module instantiation or ECL initialization
            fails.
        """
        self.wasm_path = _resolve_wasm_path(wasm_path)
        if not self.wasm_path.is_file():
            message = (
                f"ECL WASM artifact not found at {self.wasm_path}. "
                "Run `uv run python scripts/build_ecl_wasm.py` first, "
                "pass wasm_path=..., or set ECL_WASM."
            )
            raise FileNotFoundError(message)

        self._lock = threading.RLock()
        self._closed = False
        self._engine = wasmtime.Engine(_engine_config())
        self._store = wasmtime.Store(self._engine)

        wasi = wasmtime.WasiConfig()
        wasi.inherit_stderr()
        self._store.set_wasi(wasi)

        module = wasmtime.Module.from_file(self._engine, self.wasm_path)
        linker = wasmtime.Linker(self._engine)
        linker.define_wasi()
        self._py_globals: dict[str, Any] = {"__builtins__": builtins}
        define_emscripten_imports(linker, module, self._py_globals)

        try:
            self._instance = linker.instantiate(self._store, module)
        except wasmtime.WasmtimeError as exc:
            imports = ", ".join(f"{item.module}.{item.name}" for item in module.imports)
            details = f" Required imports: {imports}." if imports else ""
            message = f"failed to instantiate ECL WASM module.{details}"
            raise EclError(message) from exc

        exports = self._instance.exports(self._store)
        self._memory = _export(exports, "memory", wasmtime.Memory)
        self._init = _export(exports, "eclpy_init", wasmtime.Func)
        self._eval = _export(exports, "eclpy_eval", wasmtime.Func)
        self._eval_json = _export(exports, "eclpy_eval_json", wasmtime.Func)
        self._alloc = _export(exports, "eclpy_alloc", wasmtime.Func)
        self._free = _export(exports, "eclpy_free", wasmtime.Func)
        self._last_error = _export(exports, "eclpy_last_error", wasmtime.Func)
        self._shutdown = exports.get("eclpy_shutdown")

        initialize = exports.get("_initialize")
        if isinstance(initialize, wasmtime.Func):
            initialize(self._store)

        if self._call_i32(self._init) != 0:
            message = self._read_last_error() or "failed to initialize ECL"
            raise EclError(message)

    def eval(self, code: str) -> str:
        """Evaluate Lisp source and return the printed last primary value.

        This bypasses the JSON value protocol and is used by the CLI, tests, and
        SWANK startup paths that need native ECL behavior.
        """
        if self._closed:
            message = "ECL session is closed"
            raise EclError(message)
        if not code:
            return ""
        return self._eval_with(self._eval, code)

    def eval_json(self, code: str) -> str:
        """Evaluate Lisp source and return the last primary value as JSON text.

        High-level callers pass this text to :mod:`eclpy.protocol` for strict
        envelope validation and Python object construction.
        """
        if self._closed:
            message = "ECL session is closed"
            raise EclError(message)
        if not code:
            return "null"
        return self._eval_with(self._eval_json, code)

    def _eval_with(self, func: wasmtime.Func, code: str) -> str:
        """Call one C evaluation export with UTF-8 source text.

        Input and output buffers are allocated inside WASM linear memory and
        always freed in this method, even when ECL reports an error.
        """
        data = code.encode("utf-8")

        with self._lock:
            if self._closed:
                message = "ECL session is closed"
                raise EclError(message)

            in_ptr = self._call_i32(self._alloc, len(data))
            if in_ptr == 0:
                message = "failed to allocate input buffer in WASM memory"
                raise EclError(message)

            out_ptr = 0
            try:
                self._memory.write(self._store, data, in_ptr)
                out_ptr = self._call_i32(func, in_ptr, len(data))
                if out_ptr == 0:
                    message = self._read_last_error() or "ECL evaluation failed"
                    raise EclError(message)
                return read_c_string(self._memory, self._store, out_ptr)
            finally:
                self._free(self._store, in_ptr)
                if out_ptr:
                    self._free(self._store, out_ptr)

    def close(self) -> None:
        """Shut down the ECL runtime if the session is still open.

        The method is idempotent. Calls after shutdown raise
        :class:`eclpy.EclError` from :meth:`eval` and :meth:`eval_json`.
        """
        if self._closed:
            return
        with self._lock:
            if isinstance(self._shutdown, wasmtime.Func):
                self._shutdown(self._store)
            self._closed = True

    def __enter__(self) -> Self:
        """Enter the session context and return ``self``."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Leave the session context by calling :meth:`close`."""
        self.close()

    def _call_i32(self, func: wasmtime.Func, *args: int) -> int:
        """Call a Wasm export that must return an integer pointer or status."""
        result = func(self._store, *args)
        if not isinstance(result, int):
            message = "ECL WASM function returned a non-integer pointer"
            raise EclError(message)
        return result

    def _read_last_error(self) -> str:
        """Read the C bridge's last error string from WASM memory."""
        return read_c_string(self._memory, self._store, self._call_i32(self._last_error))


def _engine_config() -> wasmtime.Config:
    # ECL lowers setjmp/longjmp to native WebAssembly exception handling (see the
    # build script's WASM_EH_FLAGS). The standard exceptions proposal builds on the
    # function-references and GC proposals, so all three must be enabled here.
    config = wasmtime.Config()
    config.wasm_exceptions = True
    config.wasm_function_references = True
    config.wasm_gc = True
    return config


def _resolve_wasm_path(wasm_path: str | os.PathLike[str] | None) -> Path:
    if wasm_path is not None:
        return Path(wasm_path).expanduser().resolve()
    if env_path := os.environ.get("ECL_WASM"):
        return Path(env_path).expanduser().resolve()
    return PACKAGE_WASM if PACKAGE_WASM.is_file() else BUILD_WASM


def _export(exports: Any, name: str, expected_type: type[Any]) -> Any:
    value = exports.get(name)
    if not isinstance(value, expected_type):
        message = f"ECL WASM module does not export `{name}`"
        raise EclError(message)
    return value
