"""Low-level Wasmtime host for the ECL WebAssembly runtime."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import wasmtime

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType
    from typing import Self

PACKAGE_WASM = Path(__file__).with_name("ecl_eval.wasm")
BUILD_WASM = Path(__file__).resolve().parents[1] / "build" / "eclpy" / "ecl_eval.wasm"

WASI_EINVAL = 28
WASI_ENOENT = 44
WASI_ENOSYS = 52
WASI_ERANGE = 68


class EclError(RuntimeError):
    """Raised when the ECL WebAssembly runtime cannot evaluate Lisp code."""

    def __init__(self, message: str, *, condition_type: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.condition_type = condition_type


class _LongjmpError(Exception):
    pass


class EclSession:
    """A persistent ECL process hosted inside a WebAssembly instance."""

    def __init__(self, wasm_path: str | os.PathLike[str] | None = None) -> None:
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
        self._engine = wasmtime.Engine()
        self._store = wasmtime.Store(self._engine)

        wasi = wasmtime.WasiConfig()
        wasi.inherit_stderr()
        self._store.set_wasi(wasi)

        module = wasmtime.Module.from_file(self._engine, self.wasm_path)
        linker = wasmtime.Linker(self._engine)
        linker.define_wasi()
        _define_emscripten_imports(linker, module)

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
        """Evaluate Lisp source and return the printed last primary value."""
        if self._closed:
            message = "ECL session is closed"
            raise EclError(message)

        data = code.encode("utf-8")
        if not data:
            return ""

        with self._lock:
            in_ptr = self._call_i32(self._alloc, len(data))
            if in_ptr == 0:
                message = "failed to allocate input buffer in WASM memory"
                raise EclError(message)

            out_ptr = 0
            try:
                self._memory.write(self._store, data, in_ptr)
                out_ptr = self._call_i32(self._eval, in_ptr, len(data))
                if out_ptr == 0:
                    message = self._read_last_error() or "ECL evaluation failed"
                    raise EclError(message)
                return _read_c_string(self._memory, self._store, out_ptr)
            finally:
                self._free(self._store, in_ptr)
                if out_ptr:
                    self._free(self._store, out_ptr)

    def close(self) -> None:
        """Shut down the ECL runtime if the session is still open."""
        if self._closed:
            return
        with self._lock:
            if isinstance(self._shutdown, wasmtime.Func):
                self._shutdown(self._store)
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _call_i32(self, func: wasmtime.Func, *args: int) -> int:
        result = func(self._store, *args)
        if not isinstance(result, int):
            message = "ECL WASM function returned a non-integer pointer"
            raise EclError(message)
        return result

    def _read_last_error(self) -> str:
        return _read_c_string(self._memory, self._store, self._call_i32(self._last_error))


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


def _define_emscripten_imports(linker: wasmtime.Linker, module: wasmtime.Module) -> None:
    for name, func_type in _env_func_imports(module):
        callback = (
            _invoke_import(name, func_type)
            if name.startswith("invoke_")
            else _env_import(name, has_result=bool(list(func_type.results)))
        )
        linker.define_func("env", name, func_type, callback, access_caller=True)


def _env_func_imports(module: wasmtime.Module) -> list[tuple[str, wasmtime.FuncType]]:
    imports: list[tuple[str, wasmtime.FuncType]] = []
    seen: set[str] = set()
    for item in module.imports:
        if (
            item.module == "env"
            and item.name not in seen
            and isinstance(item.type, wasmtime.FuncType)
        ):
            seen.add(item.name)
            imports.append((item.name, item.type))
    return imports


def _invoke_import(name: str, func_type: wasmtime.FuncType) -> Callable[..., Any]:
    results = list(func_type.results)
    zero = _zero(results[0]) if results else None

    def invoke(caller: wasmtime.Caller, index: int, *args: int) -> Any:
        table = caller.get("__indirect_function_table")
        if not isinstance(table, wasmtime.Table):
            message = "Emscripten indirect function table is not exported"
            raise EclError(message)
        func = table.get(caller, index)
        if not isinstance(func, wasmtime.Func):
            message = f"Emscripten invoke `{name}` missing table entry {index}"
            raise EclError(message)

        get_stack = _caller_func(caller, "emscripten_stack_get_current")
        restore_stack = _caller_func(caller, "_emscripten_stack_restore")
        stack_pointer = get_stack(caller) if get_stack else None
        try:
            result = func(caller, *args)
        except _LongjmpError:
            if stack_pointer is not None and restore_stack:
                restore_stack(caller, stack_pointer)
            if set_threw := _caller_func(caller, "setThrew"):
                set_threw(caller, 1, 0)
            return zero

        return result if results else None

    return invoke


def _env_import(name: str, *, has_result: bool) -> Callable[..., Any]:
    def callback(caller: wasmtime.Caller, *args: int) -> Any:
        match name:
            case "emscripten_notify_memory_growth":
                return None
            case "_emscripten_throw_longjmp":
                raise _LongjmpError
            case "_emscripten_system":
                return -1
            case "__syscall_getcwd":
                return _getcwd(caller, *args)
            case "__syscall_chdir":
                return _chdir(caller, *args)
            case _:
                return -WASI_ENOSYS if has_result else None

    return callback


def _getcwd(caller: wasmtime.Caller, buf: int, size: int) -> int:
    cwd = b"/\0"
    if size == 0:
        return -WASI_EINVAL
    if size < len(cwd):
        return -WASI_ERANGE
    _memory(caller).write(caller, cwd, buf)
    return len(cwd)


def _chdir(caller: wasmtime.Caller, path_ptr: int) -> int:
    return 0 if _read_c_string(_memory(caller), caller, path_ptr) in {".", "/"} else -WASI_ENOENT


def _caller_func(caller: wasmtime.Caller, name: str) -> wasmtime.Func | None:
    value = caller.get(name)
    return value if isinstance(value, wasmtime.Func) else None


def _memory(caller: wasmtime.Caller) -> wasmtime.Memory:
    value = caller.get("memory")
    if not isinstance(value, wasmtime.Memory):
        message = "ECL WASM module does not export memory"
        raise EclError(message)
    return value


def _zero(value_type: wasmtime.ValType) -> float | int:
    return 0.0 if str(value_type) in {"f32", "f64"} else 0


def _read_c_string(memory: wasmtime.Memory, context: Any, ptr: int) -> str:
    if ptr == 0:
        return ""
    data = memory.read(context, ptr, memory.data_len(context))
    nul_index = data.find(0)
    if nul_index >= 0:
        data = data[:nul_index]
    return bytes(data).decode("utf-8", errors="replace")
