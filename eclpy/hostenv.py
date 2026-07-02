"""Wasmtime ``env`` imports implemented by the Python host."""

from __future__ import annotations

import builtins
import select as select_module
import socket as socket_module
import stat as stat_module
import struct
from pathlib import Path
from typing import Any

import wasmtime

from .encode import to_data_expr
from .wasmmem import (
    WASI_EINVAL,
    WASI_EIO,
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
    sockets: dict[int, socket_module.socket] = {}
    next_handle = [1]
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
                sockets=sockets,
                next_handle=next_handle,
            )
            linker.define_func("env", name, item.type, callback, access_caller=True)


def env_import(
    name: str,
    *,
    has_result: bool,
    py_globals: dict[str, Any],
    sockets: dict[int, socket_module.socket] | None = None,
    next_handle: list[int] | None = None,
) -> Any:
    """Return the Python callback for one ``env`` import name."""
    if sockets is None:
        sockets = {}
    if next_handle is None:
        next_handle = [1]

    def callback(caller: wasmtime.Caller, *args: int) -> Any:
        match name:
            case "eclpy_read_file":
                return read_host_file(caller, *args)
            case "eclpy_stat":
                return stat_host_file(caller, *args)
            case "eclpy_home_directory":
                return host_home_directory(caller, *args)
            case "eclpy_socket_resolve":
                return socket_resolve(caller, *args)
            case "eclpy_socket_connect":
                return socket_connect(caller, sockets, next_handle, *args)
            case "eclpy_socket_send":
                return socket_send(caller, sockets, *args)
            case "eclpy_socket_recv":
                return socket_recv(caller, sockets, *args)
            case "eclpy_socket_close":
                return socket_close(sockets, *args)
            case "eclpy_socket_listen":
                return socket_listen(caller, sockets, next_handle, *args)
            case "eclpy_socket_accept":
                return socket_accept(sockets, next_handle, *args)
            case "eclpy_socket_poll":
                return socket_poll(sockets, *args)
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
    """Evaluate Python expression source and return its Lisp-encoded value."""
    return run_python(caller, py_globals, "eval", *args)


def exec_python(caller: wasmtime.Caller, py_globals: dict[str, Any], *args: int) -> int:
    """Execute Python statement source and return Lisp nil."""
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
    """Run Python code from WASM memory and write a Lisp source result buffer."""
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
            result = str(to_data_expr(builtins.eval(code, py_globals)))
        else:
            exec(code, py_globals)
            result = str(to_data_expr(None))
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


def host_home_directory(
    caller: wasmtime.Caller,
    out_data_ptr: int,
    out_len_ptr: int,
) -> int:
    """Write the host home directory, with a trailing slash, as UTF-8 bytes."""
    wasm_memory = caller_memory(caller)
    malloc = caller.get("malloc")
    if not isinstance(malloc, wasmtime.Func):
        return WASI_ENOSYS

    data = (str(Path.home()) + "/").encode("utf-8")
    return write_host_buffer(caller, wasm_memory, malloc, data, out_data_ptr, out_len_ptr)


def socket_resolve(
    caller: wasmtime.Caller,
    host_ptr: int,
    host_len: int,
    out_data_ptr: int,
    out_len_ptr: int,
) -> int:
    """Resolve a hostname to a dotted-quad IPv4 address string."""
    if host_len < 0:
        return WASI_EINVAL

    wasm_memory = caller_memory(caller)
    malloc = caller.get("malloc")
    if not isinstance(malloc, wasmtime.Func):
        return WASI_ENOSYS

    try:
        host_bytes = wasm_memory.read(caller, host_ptr, host_ptr + host_len)
        host = bytes(host_bytes).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return WASI_EINVAL

    try:
        address = socket_module.gethostbyname(host)
    except OSError:
        return WASI_EIO

    return write_host_buffer(
        caller, wasm_memory, malloc, address.encode("utf-8"), out_data_ptr, out_len_ptr
    )


def socket_connect(
    caller: wasmtime.Caller,
    sockets: dict[int, socket_module.socket],
    next_handle: list[int],
    host_ptr: int,
    host_len: int,
    port: int,
) -> int:
    """Open a blocking TCP connection and return a positive handle."""
    if host_len < 0 or port < 0:
        return -WASI_EINVAL

    wasm_memory = caller_memory(caller)
    try:
        host_bytes = wasm_memory.read(caller, host_ptr, host_ptr + host_len)
        host = bytes(host_bytes).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return -WASI_EINVAL

    try:
        sock = socket_module.create_connection((host, port))
    except OSError:
        return -WASI_EIO

    sock.setblocking(True)
    handle = next_handle[0]
    next_handle[0] += 1
    sockets[handle] = sock
    return handle


def socket_send(
    caller: wasmtime.Caller,
    sockets: dict[int, socket_module.socket],
    handle: int,
    data_ptr: int,
    data_len: int,
) -> int:
    """Send bytes from WASM memory on an open socket."""
    if data_len < 0 or handle not in sockets:
        return -WASI_EINVAL

    wasm_memory = caller_memory(caller)
    data = bytes(wasm_memory.read(caller, data_ptr, data_ptr + data_len))
    try:
        sockets[handle].sendall(data)
    except OSError:
        return -WASI_EIO
    return data_len


def socket_recv(
    caller: wasmtime.Caller,
    sockets: dict[int, socket_module.socket],
    handle: int,
    max_len: int,
    out_data_ptr: int,
    out_len_ptr: int,
) -> int:
    """Receive up to ``max_len`` bytes from an open socket; empty read means EOF."""
    if max_len <= 0 or handle not in sockets:
        return WASI_EINVAL

    wasm_memory = caller_memory(caller)
    malloc = caller.get("malloc")
    if not isinstance(malloc, wasmtime.Func):
        return WASI_ENOSYS

    try:
        data = sockets[handle].recv(max_len)
    except OSError:
        return WASI_EIO

    return write_host_buffer(caller, wasm_memory, malloc, data, out_data_ptr, out_len_ptr)


def socket_close(sockets: dict[int, socket_module.socket], handle: int) -> int:
    """Close and forget a socket handle; unknown handles are a no-op success."""
    sock = sockets.pop(handle, None)
    if sock is not None:
        sock.close()
    return 0


def socket_listen(
    caller: wasmtime.Caller,
    sockets: dict[int, socket_module.socket],
    next_handle: list[int],
    host_ptr: int,
    host_len: int,
    port: int,
    backlog: int,
    out_port_ptr: int,
) -> int:
    """Create a listening TCP socket bound to host:port; 0 lets the OS pick a port."""
    if host_len < 0 or port < 0 or backlog < 1:
        return -WASI_EINVAL

    wasm_memory = caller_memory(caller)
    try:
        host_bytes = wasm_memory.read(caller, host_ptr, host_ptr + host_len)
        host = bytes(host_bytes).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return -WASI_EINVAL

    sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    try:
        sock.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(backlog)
    except OSError:
        sock.close()
        return -WASI_EIO

    bound_port = sock.getsockname()[1]
    write_i32(wasm_memory, caller, out_port_ptr, bound_port)

    handle = next_handle[0]
    next_handle[0] += 1
    sockets[handle] = sock
    return handle


def socket_accept(
    sockets: dict[int, socket_module.socket],
    next_handle: list[int],
    handle: int,
) -> int:
    """Block until a peer connects to a listening socket; return the new connection's handle."""
    if handle not in sockets:
        return -WASI_EINVAL

    try:
        connection, _ = sockets[handle].accept()
    except OSError:
        return -WASI_EIO

    connection.setblocking(True)
    new_handle = next_handle[0]
    next_handle[0] += 1
    sockets[new_handle] = connection
    return new_handle


def socket_poll(sockets: dict[int, socket_module.socket], handle: int) -> int:
    """Non-blocking readability check; used to implement ANSI CL:LISTEN, which
    must never block. Returns 1 if a read would return data or EOF immediately,
    0 if it would block, or -WASI_EINVAL for an unknown handle."""
    if handle not in sockets:
        return -WASI_EINVAL

    ready, _, _ = select_module.select([sockets[handle]], [], [], 0)
    return 1 if ready else 0


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
