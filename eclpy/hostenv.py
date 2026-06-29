"""Wasmtime ``env`` imports implemented by the Python host."""

from __future__ import annotations

import builtins
import json
import stat as stat_module
import struct
from pathlib import Path
from typing import Any

import wasmtime

from .wasmmem import (
    WASI_EINVAL,
    WASI_ENOENT,
    WASI_ENOSYS,
    WASI_ERANGE,
    read_c_string,
    write_i32,
)
from .wasmmem import (
    memory as caller_memory,
)


def define_emscripten_imports(
    linker: wasmtime.Linker,
    module: wasmtime.Module,
    py_globals: dict[str, Any],
) -> None:
    """Define all Emscripten-style ``env`` imports required by a module."""
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
            callback = env_import(
                name,
                has_result=bool(list(item.type.results)),
                py_globals=py_globals,
            )
            linker.define_func("env", name, item.type, callback, access_caller=True)


def env_import(
    name: str,
    *,
    has_result: bool,
    py_globals: dict[str, Any],
) -> Any:
    """Return the Python callback for one ``env`` import name."""

    def callback(caller: wasmtime.Caller, *args: int) -> Any:
        match name:
            case "eclpy_read_file":
                return read_host_file(caller, *args)
            case "eclpy_stat":
                return stat_host_file(caller, *args)
            case "eclpy_eval_python":
                return eval_python(caller, py_globals, *args)
            case "eclpy_exec_python":
                return exec_python(caller, py_globals, *args)
            case "emscripten_notify_memory_growth":
                return None
            case "_emscripten_system":
                return -1
            case "__syscall_getcwd":
                return getcwd(caller, *args)
            case "__syscall_chdir":
                return chdir(caller, *args)
            case _:
                return -WASI_ENOSYS if has_result else None

    return callback


def eval_python(caller: wasmtime.Caller, py_globals: dict[str, Any], *args: int) -> int:
    """Evaluate Python expression source and return its JSON-encoded value."""
    return run_python(caller, py_globals, "eval", *args)


def exec_python(caller: wasmtime.Caller, py_globals: dict[str, Any], *args: int) -> int:
    """Execute Python statement source and return JSON null."""
    return run_python(caller, py_globals, "exec", *args)


def run_python(
    caller: wasmtime.Caller,
    py_globals: dict[str, Any],
    mode: str,
    src_ptr: int,
    src_len: int,
    out_data_ptr: int,
    out_len_ptr: int,
    out_is_error_ptr: int,
) -> int:
    """Run Python code from WASM memory and write a JSON result buffer."""
    if src_len < 0:
        return WASI_EINVAL

    wasm_memory = caller_memory(caller)
    malloc = caller.get("malloc")
    if not isinstance(malloc, wasmtime.Func):
        return WASI_ENOSYS

    is_error = 0
    try:
        source = bytes(wasm_memory.read(caller, src_ptr, src_ptr + src_len)).decode("utf-8")
        code = builtins.compile(source, "<ecl-python>", mode)
        if mode == "eval":
            result = json.dumps(builtins.eval(code, py_globals), allow_nan=False)
        else:
            exec(code, py_globals)
            result = "null"
    except Exception as exc:
        is_error = 1
        result = f"{type(exc).__name__}: {exc}"

    return write_host_buffer(
        caller,
        wasm_memory,
        malloc,
        result.encode("utf-8"),
        out_data_ptr,
        out_len_ptr,
        out_is_error_ptr,
        is_error,
    )


def read_host_file(
    caller: wasmtime.Caller,
    path_ptr: int,
    path_len: int,
    out_data_ptr: int,
    out_len_ptr: int,
) -> int:
    """Read a host file into a WASM-allocated buffer."""
    if path_len < 0:
        return WASI_EINVAL

    wasm_memory = caller_memory(caller)
    malloc = caller.get("malloc")
    if not isinstance(malloc, wasmtime.Func):
        return WASI_ENOSYS

    try:
        path_bytes = wasm_memory.read(caller, path_ptr, path_ptr + path_len)
        path = Path(bytes(path_bytes).decode("utf-8"))
        data = path.read_bytes()
    except (OSError, UnicodeDecodeError, ValueError):
        return WASI_ENOENT

    return write_host_buffer(caller, wasm_memory, malloc, data, out_data_ptr, out_len_ptr)


def stat_host_file(
    caller: wasmtime.Caller,
    path_ptr: int,
    path_len: int,
    out_kind_ptr: int,
    out_mtime_ptr: int,
) -> int:
    """Stat a host path for the WASM runtime."""
    if path_len < 0:
        return WASI_EINVAL

    wasm_memory = caller_memory(caller)
    kind = 0
    mtime = 0.0
    try:
        path_bytes = wasm_memory.read(caller, path_ptr, path_ptr + path_len)
        stat = Path(bytes(path_bytes).decode("utf-8")).stat()
    except (OSError, UnicodeDecodeError, ValueError):
        pass
    else:
        mode = stat.st_mode
        kind = 2 if stat_module.S_ISDIR(mode) else 1 if stat_module.S_ISREG(mode) else 0
        mtime = stat.st_mtime
    write_i32(wasm_memory, caller, out_kind_ptr, kind)
    wasm_memory.write(caller, struct.pack("<d", mtime), out_mtime_ptr)
    return 0


def getcwd(caller: wasmtime.Caller, buf: int, size: int) -> int:
    """Minimal getcwd syscall implementation for Emscripten startup probes."""
    cwd = b"/\0"
    if size == 0:
        return -WASI_EINVAL
    if size < len(cwd):
        return -WASI_ERANGE
    caller_memory(caller).write(caller, cwd, buf)
    return len(cwd)


def chdir(caller: wasmtime.Caller, path_ptr: int) -> int:
    """Minimal chdir syscall implementation for Emscripten startup probes."""
    path = read_c_string(caller_memory(caller), caller, path_ptr)
    return 0 if path in {".", "/"} else -WASI_ENOENT


def write_host_buffer(
    caller: wasmtime.Caller,
    wasm_memory: wasmtime.Memory,
    malloc: wasmtime.Func,
    data: bytes,
    out_data_ptr: int,
    out_len_ptr: int,
    out_is_error_ptr: int | None = None,
    is_error: int = 0,
) -> int:
    """Allocate a WASM buffer, copy bytes into it, and write out pointers."""
    allocation_size = max(len(data), 1)
    data_ptr = malloc(caller, allocation_size)
    if not isinstance(data_ptr, int) or data_ptr == 0:
        return WASI_ENOSYS

    if data:
        wasm_memory.write(caller, data, data_ptr)
    write_i32(wasm_memory, caller, out_data_ptr, data_ptr)
    write_i32(wasm_memory, caller, out_len_ptr, len(data))
    if out_is_error_ptr is not None:
        write_i32(wasm_memory, caller, out_is_error_ptr, is_error)
    return 0
