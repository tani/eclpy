"""Small helpers for reading and writing WebAssembly linear memory.

The C bridge communicates with Python through integer pointers into Wasm memory.
These helpers keep pointer decoding, little-endian integer writes, and missing
memory errors in one place.
"""

from __future__ import annotations

from typing import Any

import wasmtime

from .errors import EclError

WASI_EINVAL = 28
WASI_EIO = 29
WASI_ENOENT = 44
WASI_ENOSYS = 52
WASI_ERANGE = 68


def memory(caller: wasmtime.Caller) -> wasmtime.Memory:
    """Return the exported memory from a Wasmtime caller.

    Host callbacks receive a :class:`wasmtime.Caller`; this function extracts
    the module's ``memory`` export and raises :class:`eclpy.EclError` if the
    runtime artifact does not provide it.
    """
    value = caller.get("memory")
    if not isinstance(value, wasmtime.Memory):
        message = "ECL WASM module does not export memory"
        raise EclError(message)
    return value


def write_i32(memory: wasmtime.Memory, context: Any, ptr: int, value: int) -> None:
    """Write one little-endian signed i32 into WebAssembly memory."""
    memory.write(context, value.to_bytes(4, "little", signed=True), ptr)


def read_c_string(memory: wasmtime.Memory, context: Any, ptr: int) -> str:
    """Read a NUL-terminated UTF-8 string from WebAssembly memory.

    A null pointer is treated as an empty string, matching the C bridge's error
    string and output pointer conventions.
    """
    if ptr == 0:
        return ""
    data = memory.read(context, ptr, memory.data_len(context))
    nul_index = data.find(0)
    if nul_index >= 0:
        data = data[:nul_index]
    return bytes(data).decode("utf-8", errors="replace")
