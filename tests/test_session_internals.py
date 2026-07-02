from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from eclpy import EclError, EclSession, hostenv, session, wasmmem


def fake_engine(config: object = None) -> object:
    return object()


class FakeFunc:
    def __init__(self, impl=None) -> None:
        self.impl = impl or (lambda *args: None)
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.impl(*args)


class FakeMemory:
    def __init__(self, data: bytes = b"") -> None:
        self.data = data
        self.writes: list[tuple[object, bytes, int]] = []

    def write(self, context, data: bytes, ptr: int) -> None:
        self.writes.append((context, bytes(data), ptr))

    def read(self, context, start: int, stop: int) -> bytes:
        return self.data[start:stop]

    def data_len(self, context) -> int:
        return len(self.data)


class MutableFakeMemory(FakeMemory):
    def __init__(self, data: bytes = b"", size: int = 128) -> None:
        super().__init__(data.ljust(size, b"\0"))
        self.data = bytearray(self.data)

    def write(self, context, data: bytes, ptr: int) -> None:
        super().write(context, data, ptr)
        self.data[ptr : ptr + len(data)] = data

    def read(self, context, start: int, stop: int) -> bytes:
        return bytes(self.data[start:stop])


class FakeCaller:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get(self, name: str) -> object | None:
        return self.values.get(name)


class FakeFuncType:
    def __init__(self, results=()) -> None:
        self.results = list(results)


class FakeImport:
    def __init__(self, module: str, name: str, type_: object) -> None:
        self.module = module
        self.name = name
        self.type = type_


class ClosingLock:
    def __init__(self, ecl: EclSession) -> None:
        self.ecl = ecl

    def __enter__(self) -> None:
        self.ecl._closed = True

    def __exit__(self, exc_type, exc, tb) -> None:
        pass


class SessionInternalsTests(unittest.TestCase):
    def test_resolve_wasm_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.wasm"
            self.assertEqual(session._resolve_wasm_path(path), path.resolve())
            with patch.dict(os.environ, {"ECL_WASM": str(path)}):
                self.assertEqual(session._resolve_wasm_path(None), path.resolve())
            with patch.dict(os.environ, {}, clear=True):
                self.assertIn(
                    session._resolve_wasm_path(None), {session.PACKAGE_WASM, session.BUILD_WASM}
                )

    def test_export_helper(self) -> None:
        value = FakeFunc()
        self.assertIs(session._export({"f": value}, "f", FakeFunc), value)
        with self.assertRaisesRegex(EclError, "does not export"):
            session._export({}, "missing", FakeFunc)

    def test_read_c_string(self) -> None:
        self.assertEqual(wasmmem.read_c_string(FakeMemory(), object(), 0), "")
        self.assertEqual(wasmmem.read_c_string(FakeMemory(b"\xffab\0tail"), object(), 0), "")
        self.assertEqual(wasmmem.read_c_string(FakeMemory(b"xxhi\0tail"), object(), 2), "hi")

    def test_read_host_file_import(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.multiple(
                session.wasmtime,
                Func=FakeFunc,
                Memory=MutableFakeMemory,
            ),
        ):
            self.assertEqual(
                hostenv.read_host_file(FakeCaller(), 0, -1, 32, 36),
                wasmmem.WASI_EINVAL,
            )

            self.assertEqual(
                hostenv.read_host_file(FakeCaller({"memory": MutableFakeMemory()}), 0, 0, 32, 36),
                wasmmem.WASI_ENOSYS,
            )

            path = Path(directory) / "source.lisp"
            path.write_bytes(b"(+ 1 2)")

            memory = MutableFakeMemory(str(path).encode(), size=512)
            caller = FakeCaller({"memory": memory, "malloc": FakeFunc(lambda caller, size: 128)})
            status = hostenv.read_host_file(caller, 0, len(str(path)), 32, 36)

            self.assertEqual(status, 0)
            self.assertEqual(memory.data[32:36], (128).to_bytes(4, "little", signed=True))
            self.assertEqual(memory.data[36:40], (7).to_bytes(4, "little", signed=True))
            self.assertEqual(memory.data[128:135], b"(+ 1 2)")

            missing = MutableFakeMemory(b"/missing.lisp", size=128)
            missing_caller = FakeCaller(
                {"memory": missing, "malloc": FakeFunc(lambda caller, size: 64)}
            )
            self.assertEqual(
                hostenv.read_host_file(missing_caller, 0, len("/missing.lisp"), 32, 36),
                wasmmem.WASI_ENOENT,
            )

            failed_allocation = MutableFakeMemory(str(path).encode(), size=128)
            failed_allocation_caller = FakeCaller(
                {"memory": failed_allocation, "malloc": FakeFunc(lambda caller, size: 0)}
            )
            self.assertEqual(
                hostenv.read_host_file(failed_allocation_caller, 0, len(str(path)), 32, 36),
                wasmmem.WASI_ENOSYS,
            )

    def test_host_home_directory_import(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            self.assertEqual(
                hostenv.host_home_directory(
                    FakeCaller({"memory": MutableFakeMemory()}), 32, 36
                ),
                wasmmem.WASI_ENOSYS,
            )

            memory = MutableFakeMemory(size=256)
            caller = FakeCaller({"memory": memory, "malloc": FakeFunc(lambda caller, size: 100)})
            status = hostenv.host_home_directory(caller, 32, 36)

            self.assertEqual(status, 0)
            self.assertEqual(memory.data[32:36], (100).to_bytes(4, "little", signed=True))
            out_len = int.from_bytes(memory.data[36:40], "little", signed=True)
            expected = (str(Path.home()) + "/").encode("utf-8")
            self.assertEqual(out_len, len(expected))
            self.assertEqual(memory.data[100 : 100 + out_len], expected)

    def test_ecl_session_eval_error_paths_without_wasm(self) -> None:
        ecl = EclSession.__new__(EclSession)
        ecl._closed = True
        with self.assertRaisesRegex(EclError, "closed"):
            ecl.eval("(+ 1 2)")
        with self.assertRaisesRegex(EclError, "closed"):
            ecl.eval_json("(+ 1 2)")

        ecl._closed = False
        self.assertEqual(ecl.eval(""), "")
        self.assertEqual(ecl.eval_json(""), "null")

        ecl._eval = object()
        ecl._lock = ClosingLock(ecl)
        with self.assertRaisesRegex(EclError, "closed"):
            ecl.eval("x")

        ecl._closed = False
        ecl._lock = threading.RLock()
        ecl._alloc = object()
        ecl._call_i32 = lambda func, *args: 0
        with self.assertRaisesRegex(EclError, "allocate input"):
            ecl.eval("x")

        ecl._closed = True
        ecl.close()

        ecl._store = object()
        with self.assertRaisesRegex(EclError, "non-integer"):
            EclSession._call_i32(ecl, lambda store: "not int")

    def test_init_reports_instantiation_failure(self) -> None:
        class FakeStore:
            def set_wasi(self, wasi) -> None:
                pass

        class FakeWasiConfig:
            def inherit_stderr(self) -> None:
                pass

        fake_module = type(
            "FakeModule", (), {"imports": [FakeImport("env", "missing", object())]}
        )()

        class FakeModuleClass:
            @staticmethod
            def from_file(engine, wasm_path):
                return fake_module

        class FakeLinker:
            def __init__(self, engine) -> None:
                pass

            def define_wasi(self) -> None:
                pass

            def instantiate(self, store, module):
                raise session.wasmtime.WasmtimeError("bad module")

        with (
            tempfile.NamedTemporaryFile() as wasm,
            patch.multiple(
                session.wasmtime,
                Engine=fake_engine,
                Store=lambda engine: FakeStore(),
                WasiConfig=FakeWasiConfig,
                Module=FakeModuleClass,
                Linker=FakeLinker,
            ),
            self.assertRaisesRegex(EclError, "Required imports: env.missing"),
        ):
            EclSession(wasm.name)

    def test_init_reports_ecl_init_failure(self) -> None:
        class FakeStore:
            def set_wasi(self, wasi) -> None:
                pass

        class FakeWasiConfig:
            def inherit_stderr(self) -> None:
                pass

        class FakeModuleClass:
            @staticmethod
            def from_file(engine, wasm_path):
                return type("FakeModule", (), {"imports": []})()

        class FakeInstance:
            def exports(self, store):
                return {
                    "memory": FakeMemory(),
                    "eclpy_init": FakeFunc(lambda store: 1),
                    "eclpy_eval": FakeFunc(lambda *args: 0),
                    "eclpy_eval_json": FakeFunc(lambda *args: 0),
                    "eclpy_alloc": FakeFunc(lambda *args: 1),
                    "eclpy_free": FakeFunc(lambda *args: None),
                    "eclpy_last_error": FakeFunc(lambda store: 0),
                }

        class FakeLinker:
            def __init__(self, engine) -> None:
                pass

            def define_wasi(self) -> None:
                pass

            def instantiate(self, store, module):
                return FakeInstance()

        with (
            tempfile.NamedTemporaryFile() as wasm,
            patch.multiple(
                session.wasmtime,
                Engine=fake_engine,
                Store=lambda engine: FakeStore(),
                WasiConfig=FakeWasiConfig,
                Module=FakeModuleClass,
                Linker=FakeLinker,
                Memory=FakeMemory,
                Func=FakeFunc,
            ),
            self.assertRaisesRegex(EclError, "failed to initialize ECL"),
        ):
            EclSession(wasm.name)

    def test_define_emscripten_imports(self) -> None:
        class FakeLinker:
            def __init__(self) -> None:
                self.defined: list[tuple[str, str, object, object, bool]] = []

            def define_func(self, *args, **kwargs) -> None:
                self.defined.append((*args, kwargs["access_caller"]))

        module = type(
            "FakeModule",
            (),
            {
                "imports": [
                    FakeImport("env", "invoke_ii", FakeFuncType(["i32"])),
                    FakeImport("env", "regular", FakeFuncType([])),
                    FakeImport("env", "regular", FakeFuncType([])),
                    FakeImport("wasi_snapshot_preview1", "fd_write", FakeFuncType([])),
                    FakeImport("env", "memory", object()),
                ]
            },
        )()
        linker = FakeLinker()
        with patch.object(session.wasmtime, "FuncType", FakeFuncType):
            hostenv.define_emscripten_imports(linker, module, {"__builtins__": __builtins__})
        self.assertEqual([item[1] for item in linker.defined], ["invoke_ii", "regular"])

    def test_env_import_and_syscall_helpers(self) -> None:
        globals_: dict[str, object] = {"__builtins__": __builtins__}
        self.assertIsNone(
            hostenv.env_import(
                "emscripten_notify_memory_growth", has_result=False, py_globals=globals_
            )(FakeCaller())
        )
        self.assertEqual(
            hostenv.env_import("_emscripten_system", has_result=True, py_globals=globals_)(
                FakeCaller()
            ),
            -1,
        )
        self.assertEqual(
            hostenv.env_import("unknown", has_result=True, py_globals=globals_)(FakeCaller()),
            -wasmmem.WASI_ENOSYS,
        )
        self.assertIsNone(
            hostenv.env_import("unknown", has_result=False, py_globals=globals_)(FakeCaller())
        )

        with patch.object(session.wasmtime, "Memory", FakeMemory):
            self.assertEqual(
                hostenv.stat_host_file(FakeCaller(), 0, -1, 32, 40),
                wasmmem.WASI_EINVAL,
            )

            memory = FakeMemory(b"x.\0bad\0")
            caller = FakeCaller({"memory": memory})
            self.assertEqual(hostenv.getcwd(caller, 10, 0), -wasmmem.WASI_EINVAL)
            self.assertEqual(hostenv.getcwd(caller, 10, 1), -wasmmem.WASI_ERANGE)
            self.assertEqual(hostenv.getcwd(caller, 10, 2), 2)
            self.assertEqual(memory.writes[-1], (caller, b"/\0", 10))
            self.assertEqual(hostenv.chdir(caller, 1), 0)
            self.assertEqual(
                hostenv.env_import("__syscall_chdir", has_result=True, py_globals=globals_)(
                    caller, 1
                ),
                0,
            )
            self.assertEqual(
                hostenv.chdir(FakeCaller({"memory": FakeMemory(b"xbad\0")}), 1),
                -wasmmem.WASI_ENOENT,
            )
            with self.assertRaisesRegex(EclError, "does not export memory"):
                wasmmem.memory(FakeCaller())

    def test_eval_python_import(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            py_globals: dict[str, object] = {"__builtins__": __builtins__}

            self.assertEqual(
                hostenv.eval_python(FakeCaller(), py_globals, 0, -1, 64, 68, 72),
                wasmmem.WASI_EINVAL,
            )

            self.assertEqual(
                hostenv.eval_python(
                    FakeCaller({"memory": MutableFakeMemory()}), py_globals, 0, 0, 64, 68, 72
                ),
                wasmmem.WASI_ENOSYS,
            )

            def run_eval(source: str, memory: MutableFakeMemory, malloc_ptr: int = 256):
                memory.data[: len(source.encode())] = source.encode()
                caller = FakeCaller(
                    {"memory": memory, "malloc": FakeFunc(lambda caller, size: malloc_ptr)}
                )
                status = hostenv.eval_python(
                    caller, py_globals, 0, len(source.encode()), 64, 68, 72
                )
                out_ptr = int.from_bytes(memory.data[64:68], "little", signed=True)
                out_len = int.from_bytes(memory.data[68:72], "little", signed=True)
                is_error = int.from_bytes(memory.data[72:76], "little", signed=True)
                return status, memory.data[out_ptr : out_ptr + out_len], is_error

            def run_exec(source: str, memory: MutableFakeMemory, malloc_ptr: int = 256):
                memory.data[: len(source.encode())] = source.encode()
                caller = FakeCaller(
                    {"memory": memory, "malloc": FakeFunc(lambda caller, size: malloc_ptr)}
                )
                status = hostenv.exec_python(
                    caller, py_globals, 0, len(source.encode()), 64, 68, 72
                )
                out_ptr = int.from_bytes(memory.data[64:68], "little", signed=True)
                out_len = int.from_bytes(memory.data[68:72], "little", signed=True)
                is_error = int.from_bytes(memory.data[72:76], "little", signed=True)
                return status, memory.data[out_ptr : out_ptr + out_len], is_error

            def decoded_value(data: bytes) -> object:
                return json.loads(data.decode("utf-8"))

            status, result, is_error = run_eval("1 + 2", MutableFakeMemory(size=512))
            self.assertEqual(
                (status, decoded_value(result), is_error), (0, {"type": "int", "value": "3"}, 0)
            )

            status, result, is_error = run_eval("x = 41", MutableFakeMemory(size=512))
            self.assertEqual((status, is_error), (0, 1))
            self.assertIn(b"SyntaxError", result)

            status, result, is_error = run_exec("x = 41", MutableFakeMemory(size=512))
            self.assertEqual((status, decoded_value(result), is_error), (0, {"type": "nil"}, 0))
            status, result, is_error = run_eval("x + 1", MutableFakeMemory(size=512))
            self.assertEqual(
                (status, decoded_value(result), is_error), (0, {"type": "int", "value": "42"}, 0)
            )
            status, result, is_error = run_eval("[1, 'x']", MutableFakeMemory(size=512))
            self.assertEqual(
                (status, decoded_value(result), is_error),
                (
                    0,
                    {
                        "type": "list",
                        "items": [
                            {"type": "int", "value": "1"},
                            {"type": "string", "value": "x"},
                        ],
                    },
                    0,
                ),
            )

            status, result, is_error = run_eval("1 / 0", MutableFakeMemory(size=512))
            self.assertEqual((status, is_error), (0, 1))
            self.assertIn(b"ZeroDivisionError", result)

            failed_alloc = MutableFakeMemory(size=512)
            failed_alloc.data[:5] = b"1 + 2"
            caller = FakeCaller(
                {"memory": failed_alloc, "malloc": FakeFunc(lambda caller, size: 0)}
            )
            self.assertEqual(
                hostenv.eval_python(caller, py_globals, 0, 5, 64, 68, 72),
                wasmmem.WASI_ENOSYS,
            )

    def test_eval_python_default_globals(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            memory = MutableFakeMemory(b"2 ** 5", size=512)
            caller = FakeCaller({"memory": memory, "malloc": FakeFunc(lambda caller, size: 256)})
            callback = hostenv.env_import(
                "eclpy_eval_python",
                has_result=True,
                py_globals={"__builtins__": __builtins__},
            )
            status = callback(caller, 0, 6, 64, 68, 72)
            out_ptr = int.from_bytes(memory.data[64:68], "little", signed=True)
            out_len = int.from_bytes(memory.data[68:72], "little", signed=True)
            self.assertEqual(
                (status, json.loads(memory.data[out_ptr : out_ptr + out_len].decode("utf-8"))),
                (0, {"type": "int", "value": "32"}),
            )

            exec_memory = MutableFakeMemory(b"z = 99", size=512)
            exec_caller = FakeCaller(
                {"memory": exec_memory, "malloc": FakeFunc(lambda caller, size: 256)}
            )
            exec_callback = hostenv.env_import(
                "eclpy_exec_python",
                has_result=True,
                py_globals={"__builtins__": __builtins__},
            )
            self.assertEqual(exec_callback(exec_caller, 0, 6, 64, 68, 72), 0)
            out_ptr = int.from_bytes(exec_memory.data[64:68], "little", signed=True)
            out_len = int.from_bytes(exec_memory.data[68:72], "little", signed=True)
            self.assertEqual(
                json.loads(exec_memory.data[out_ptr : out_ptr + out_len].decode("utf-8")),
                {"type": "nil"},
            )

    def test_socket_resolve_import(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            self.assertEqual(
                hostenv.socket_resolve(FakeCaller(), 0, -1, 32, 36),
                wasmmem.WASI_EINVAL,
            )

            no_malloc_memory = MutableFakeMemory(b"127.0.0.1", size=64)
            self.assertEqual(
                hostenv.socket_resolve(
                    FakeCaller({"memory": no_malloc_memory}), 0, 9, 32, 36
                ),
                wasmmem.WASI_ENOSYS,
            )

            bad_utf8 = MutableFakeMemory(b"\xff\xfe", size=64)
            caller = FakeCaller(
                {"memory": bad_utf8, "malloc": FakeFunc(lambda caller, size: 40)}
            )
            self.assertEqual(hostenv.socket_resolve(caller, 0, 2, 32, 36), wasmmem.WASI_EINVAL)

            memory = MutableFakeMemory(b"127.0.0.1", size=256)
            success_caller = FakeCaller(
                {"memory": memory, "malloc": FakeFunc(lambda caller, size: 100)}
            )
            status = hostenv.socket_resolve(success_caller, 0, len("127.0.0.1"), 32, 36)
            self.assertEqual(status, 0)
            out_ptr = int.from_bytes(memory.data[32:36], "little", signed=True)
            out_len = int.from_bytes(memory.data[36:40], "little", signed=True)
            self.assertEqual(bytes(memory.data[out_ptr : out_ptr + out_len]), b"127.0.0.1")

            invalid_host = "definitely-invalid-host-name.invalid"
            invalid_memory = MutableFakeMemory(invalid_host.encode(), size=256)
            invalid_caller = FakeCaller(
                {"memory": invalid_memory, "malloc": FakeFunc(lambda caller, size: 100)}
            )
            self.assertEqual(
                hostenv.socket_resolve(invalid_caller, 0, len(invalid_host), 32, 36),
                wasmmem.WASI_EIO,
            )

    def test_socket_connect_import(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            sockets: dict[int, socket.socket] = {}
            next_handle = [1]

            self.assertEqual(
                hostenv.socket_connect(FakeCaller(), sockets, next_handle, 0, -1, 80),
                -wasmmem.WASI_EINVAL,
            )

            memory = MutableFakeMemory(b"127.0.0.1", size=64)
            self.assertEqual(
                hostenv.socket_connect(
                    FakeCaller({"memory": memory}), sockets, next_handle, 0, 9, -1
                ),
                -wasmmem.WASI_EINVAL,
            )

            bad_utf8 = MutableFakeMemory(b"\xff\xfe", size=64)
            self.assertEqual(
                hostenv.socket_connect(
                    FakeCaller({"memory": bad_utf8}), sockets, next_handle, 0, 2, 80
                ),
                -wasmmem.WASI_EINVAL,
            )

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            accept_thread = threading.Thread(
                target=lambda: listener.accept()[0].close(), daemon=True
            )
            accept_thread.start()
            try:
                host_memory = MutableFakeMemory(b"127.0.0.1", size=64)
                handle = hostenv.socket_connect(
                    FakeCaller({"memory": host_memory}), sockets, next_handle, 0, 9, port
                )
                self.assertGreater(handle, 0)
                self.assertIn(handle, sockets)
                self.assertEqual(next_handle[0], handle + 1)
            finally:
                accept_thread.join(timeout=5)
                listener.close()
                stray = sockets.pop(handle, None)
                if stray is not None:
                    stray.close()

            refused_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            refused_listener.bind(("127.0.0.1", 0))
            refused_port = refused_listener.getsockname()[1]
            refused_listener.close()
            refused_memory = MutableFakeMemory(b"127.0.0.1", size=64)
            self.assertEqual(
                hostenv.socket_connect(
                    FakeCaller({"memory": refused_memory}),
                    sockets,
                    next_handle,
                    0,
                    9,
                    refused_port,
                ),
                -wasmmem.WASI_EIO,
            )

    def test_socket_send_recv_close_round_trip(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            accepted: dict[str, socket.socket] = {}
            received: dict[str, bytes] = {}

            def accept_and_echo() -> None:
                connection, _ = server.accept()
                accepted["connection"] = connection
                received["data"] = connection.recv(4096)
                connection.sendall(b"pong")

            server_thread = threading.Thread(target=accept_and_echo, daemon=True)
            server_thread.start()

            sockets: dict[int, socket.socket] = {}
            next_handle = [1]
            memory = MutableFakeMemory(b"127.0.0.1ping", size=64)
            handle = hostenv.socket_connect(
                FakeCaller({"memory": memory}), sockets, next_handle, 0, 9, port
            )
            self.assertGreater(handle, 0)

            send_caller = FakeCaller({"memory": memory})
            sent = hostenv.socket_send(send_caller, sockets, handle, 9, 4)
            self.assertEqual(sent, 4)

            self.assertEqual(
                hostenv.socket_send(send_caller, sockets, 99999, 9, 4),
                -wasmmem.WASI_EINVAL,
            )
            self.assertEqual(
                hostenv.socket_send(send_caller, sockets, handle, 9, -1),
                -wasmmem.WASI_EINVAL,
            )

            server_thread.join(timeout=5)
            self.assertEqual(received.get("data"), b"ping")

            recv_memory = MutableFakeMemory(size=256)
            recv_caller = FakeCaller(
                {"memory": recv_memory, "malloc": FakeFunc(lambda caller, size: 100)}
            )
            status = hostenv.socket_recv(recv_caller, sockets, handle, 1024, 32, 36)
            self.assertEqual(status, 0)
            out_ptr = int.from_bytes(recv_memory.data[32:36], "little", signed=True)
            out_len = int.from_bytes(recv_memory.data[36:40], "little", signed=True)
            self.assertEqual(bytes(recv_memory.data[out_ptr : out_ptr + out_len]), b"pong")

            self.assertEqual(
                hostenv.socket_recv(recv_caller, sockets, 99999, 1024, 32, 36),
                wasmmem.WASI_EINVAL,
            )
            self.assertEqual(
                hostenv.socket_recv(recv_caller, sockets, handle, 0, 32, 36),
                wasmmem.WASI_EINVAL,
            )
            self.assertEqual(
                hostenv.socket_recv(
                    FakeCaller({"memory": recv_memory}), sockets, handle, 1024, 32, 36
                ),
                wasmmem.WASI_ENOSYS,
            )

            accepted["connection"].close()
            eof_status = hostenv.socket_recv(recv_caller, sockets, handle, 1024, 32, 36)
            self.assertEqual(eof_status, 0)
            eof_len = int.from_bytes(recv_memory.data[36:40], "little", signed=True)
            self.assertEqual(eof_len, 0)

            self.assertEqual(hostenv.socket_close(sockets, handle), 0)
            self.assertNotIn(handle, sockets)
            self.assertEqual(hostenv.socket_close(sockets, handle), 0)
            self.assertEqual(hostenv.socket_close(sockets, 99999), 0)

            server.close()

    def test_socket_send_reports_os_error(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            local_socket, remote_socket = socket.socketpair()
            sockets = {1: local_socket}
            remote_socket.close()
            local_socket.close()
            memory = MutableFakeMemory(b"data", size=64)
            caller = FakeCaller({"memory": memory})
            self.assertEqual(
                hostenv.socket_send(caller, sockets, 1, 0, 4),
                -wasmmem.WASI_EIO,
            )

    def test_socket_recv_reports_os_error(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            local_socket, remote_socket = socket.socketpair()
            sockets = {1: local_socket}
            remote_socket.close()
            local_socket.close()
            memory = MutableFakeMemory(size=64)
            caller = FakeCaller(
                {"memory": memory, "malloc": FakeFunc(lambda caller, size: 40)}
            )
            self.assertEqual(
                hostenv.socket_recv(caller, sockets, 1, 1024, 32, 36),
                wasmmem.WASI_EIO,
            )

    def test_socket_listen_import(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            sockets: dict[int, socket.socket] = {}
            next_handle = [1]

            self.assertEqual(
                hostenv.socket_listen(FakeCaller(), sockets, next_handle, 0, -1, 0, 1, 40),
                -wasmmem.WASI_EINVAL,
            )
            self.assertEqual(
                hostenv.socket_listen(FakeCaller(), sockets, next_handle, 0, 9, -1, 1, 40),
                -wasmmem.WASI_EINVAL,
            )
            self.assertEqual(
                hostenv.socket_listen(FakeCaller(), sockets, next_handle, 0, 9, 0, 0, 40),
                -wasmmem.WASI_EINVAL,
            )

            bad_utf8 = MutableFakeMemory(b"\xff\xfe", size=64)
            self.assertEqual(
                hostenv.socket_listen(
                    FakeCaller({"memory": bad_utf8}), sockets, next_handle, 0, 2, 0, 1, 40
                ),
                -wasmmem.WASI_EINVAL,
            )

            unroutable_memory = MutableFakeMemory(b"256.256.256.256", size=64)
            self.assertEqual(
                hostenv.socket_listen(
                    FakeCaller({"memory": unroutable_memory}),
                    sockets,
                    next_handle,
                    0,
                    15,
                    0,
                    1,
                    40,
                ),
                -wasmmem.WASI_EIO,
            )

            listen_memory = MutableFakeMemory(b"127.0.0.1", size=64)
            handle = hostenv.socket_listen(
                FakeCaller({"memory": listen_memory}), sockets, next_handle, 0, 9, 0, 1, 32
            )
            self.assertGreater(handle, 0)
            self.assertIn(handle, sockets)
            bound_port = int.from_bytes(listen_memory.data[32:36], "little", signed=True)
            self.assertGreater(bound_port, 0)

            sockets[handle].close()
            del sockets[handle]

    def test_socket_accept_and_poll_import(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]

            sockets: dict[int, socket.socket] = {1: server}
            next_handle = [2]

            self.assertEqual(hostenv.socket_poll(sockets, 1), 0)
            self.assertEqual(hostenv.socket_poll(sockets, 99999), -wasmmem.WASI_EINVAL)
            self.assertEqual(
                hostenv.socket_accept(sockets, next_handle, 99999), -wasmmem.WASI_EINVAL
            )

            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                deadline = threading.Event()

                def wait_ready() -> None:
                    for _ in range(50):
                        if hostenv.socket_poll(sockets, 1) == 1:
                            deadline.set()
                            return
                        threading.Event().wait(0.05)

                waiter = threading.Thread(target=wait_ready, daemon=True)
                waiter.start()
                waiter.join(timeout=5)
                self.assertTrue(deadline.is_set())

                new_handle = hostenv.socket_accept(sockets, next_handle, 1)
                self.assertGreater(new_handle, 0)
                self.assertIn(new_handle, sockets)
                sockets[new_handle].close()
                del sockets[new_handle]
            finally:
                client.close()
                server.close()

    def test_socket_accept_reports_os_error(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            local_socket, remote_socket = socket.socketpair()
            sockets = {1: local_socket}
            next_handle = [2]
            remote_socket.close()

            self.assertEqual(
                hostenv.socket_accept(sockets, next_handle, 1), -wasmmem.WASI_EIO
            )
            local_socket.close()

    def test_socket_env_import_dispatch(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            server_thread = threading.Thread(
                target=lambda: server.accept()[0].close(), daemon=True
            )
            server_thread.start()

            globals_: dict[str, object] = {"__builtins__": __builtins__}
            sockets: dict[int, socket.socket] = {}
            next_handle = [1]
            resolve_callback = hostenv.env_import(
                "eclpy_socket_resolve",
                has_result=True,
                py_globals=globals_,
                sockets=sockets,
                next_handle=next_handle,
            )
            connect_callback = hostenv.env_import(
                "eclpy_socket_connect",
                has_result=True,
                py_globals=globals_,
                sockets=sockets,
                next_handle=next_handle,
            )
            close_callback = hostenv.env_import(
                "eclpy_socket_close",
                has_result=True,
                py_globals=globals_,
                sockets=sockets,
                next_handle=next_handle,
            )

            resolve_memory = MutableFakeMemory(b"127.0.0.1", size=256)
            resolve_caller = FakeCaller(
                {"memory": resolve_memory, "malloc": FakeFunc(lambda caller, size: 100)}
            )
            self.assertEqual(resolve_callback(resolve_caller, 0, 9, 32, 36), 0)

            connect_memory = MutableFakeMemory(b"127.0.0.1", size=64)
            handle = connect_callback(FakeCaller({"memory": connect_memory}), 0, 9, port)
            self.assertGreater(handle, 0)

            self.assertEqual(close_callback(FakeCaller(), 1), 0)
            server_thread.join(timeout=5)
            server.close()


if __name__ == "__main__":
    unittest.main()
