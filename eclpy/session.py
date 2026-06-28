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
        self._engine = wasmtime.Engine(_engine_config())
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


def _define_emscripten_imports(linker: wasmtime.Linker, module: wasmtime.Module) -> None:
    for name, func_type in _env_func_imports(module):
        callback = _env_import(name, has_result=bool(list(func_type.results)))
        linker.define_func("env", name, func_type, callback, access_caller=True)


def _env_func_imports(module: wasmtime.Module) -> list[tuple[str, wasmtime.FuncType]]:
    imports: list[tuple[str, wasmtime.FuncType]] = []
    seen: set[str] = set()
    for item in module.imports:
        name = item.name
        if (
            name is not None
            and item.module == "env"
            and name not in seen
            and isinstance(item.type, wasmtime.FuncType)
        ):
            seen.add(name)
            imports.append((name, item.type))
    return imports


def _env_import(name: str, *, has_result: bool) -> Callable[..., Any]:
    def callback(caller: wasmtime.Caller, *args: int) -> Any:
        match name:
            case "eclpy_read_file":
                return _read_host_file(caller, *args)
            case "emscripten_notify_memory_growth":
                return None
            case "_emscripten_system":
                return -1
            case "__syscall_getcwd":
                return _getcwd(caller, *args)
            case "__syscall_chdir":
                return _chdir(caller, *args)
            case _:
                return -WASI_ENOSYS if has_result else None

    return callback


def _read_host_file(
    caller: wasmtime.Caller,
    path_ptr: int,
    path_len: int,
    out_data_ptr: int,
    out_len_ptr: int,
) -> int:
    if path_len < 0:
        return WASI_EINVAL

    memory = _memory(caller)
    malloc = _caller_func(caller, "malloc")
    if malloc is None:
        return WASI_ENOSYS

    try:
        path_bytes = memory.read(caller, path_ptr, path_ptr + path_len)
        path = Path(bytes(path_bytes).decode("utf-8"))
        data = path.read_bytes()
    except OSError, UnicodeDecodeError, ValueError:
        return WASI_ENOENT

    allocation_size = max(len(data), 1)
    data_ptr = malloc(caller, allocation_size)
    if not isinstance(data_ptr, int) or data_ptr == 0:
        return WASI_ENOSYS

    if data:
        memory.write(caller, data, data_ptr)
    _write_i32(memory, caller, out_data_ptr, data_ptr)
    _write_i32(memory, caller, out_len_ptr, len(data))
    return 0


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


def _write_i32(memory: wasmtime.Memory, context: Any, ptr: int, value: int) -> None:
    memory.write(context, value.to_bytes(4, "little", signed=True), ptr)


def _read_c_string(memory: wasmtime.Memory, context: Any, ptr: int) -> str:
    if ptr == 0:
        return ""
    data = memory.read(context, ptr, memory.data_len(context))
    nul_index = data.find(0)
    if nul_index >= 0:
        data = data[:nul_index]
    return bytes(data).decode("utf-8", errors="replace")
