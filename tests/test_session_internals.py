from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from eclpy import EclError, EclSession, session


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
        self.assertEqual(session._read_c_string(FakeMemory(), object(), 0), "")
        self.assertEqual(session._read_c_string(FakeMemory(b"\xffab\0tail"), object(), 0), "")
        self.assertEqual(session._read_c_string(FakeMemory(b"xxhi\0tail"), object(), 2), "hi")

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
                session._read_host_file(FakeCaller(), 0, -1, 32, 36),
                session.WASI_EINVAL,
            )

            self.assertEqual(
                session._read_host_file(FakeCaller({"memory": MutableFakeMemory()}), 0, 0, 32, 36),
                session.WASI_ENOSYS,
            )

            path = Path(directory) / "source.lisp"
            path.write_bytes(b"(+ 1 2)")

            memory = MutableFakeMemory(str(path).encode(), size=512)
            caller = FakeCaller({"memory": memory, "malloc": FakeFunc(lambda caller, size: 128)})
            status = session._read_host_file(caller, 0, len(str(path)), 32, 36)

            self.assertEqual(status, 0)
            self.assertEqual(memory.data[32:36], (128).to_bytes(4, "little", signed=True))
            self.assertEqual(memory.data[36:40], (7).to_bytes(4, "little", signed=True))
            self.assertEqual(memory.data[128:135], b"(+ 1 2)")

            missing = MutableFakeMemory(b"/missing.lisp", size=128)
            missing_caller = FakeCaller(
                {"memory": missing, "malloc": FakeFunc(lambda caller, size: 64)}
            )
            self.assertEqual(
                session._read_host_file(missing_caller, 0, len("/missing.lisp"), 32, 36),
                session.WASI_ENOENT,
            )

            failed_allocation = MutableFakeMemory(str(path).encode(), size=128)
            failed_allocation_caller = FakeCaller(
                {"memory": failed_allocation, "malloc": FakeFunc(lambda caller, size: 0)}
            )
            self.assertEqual(
                session._read_host_file(failed_allocation_caller, 0, len(str(path)), 32, 36),
                session.WASI_ENOSYS,
            )

    def test_ecl_session_eval_error_paths_without_wasm(self) -> None:
        ecl = EclSession.__new__(EclSession)
        ecl._closed = True
        with self.assertRaisesRegex(EclError, "closed"):
            ecl.eval("(+ 1 2)")

        ecl._closed = False
        self.assertEqual(ecl.eval(""), "")

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
            session._define_emscripten_imports(linker, module, {"__builtins__": __builtins__})
        self.assertEqual([item[1] for item in linker.defined], ["invoke_ii", "regular"])

    def test_env_import_and_syscall_helpers(self) -> None:
        self.assertIsNone(
            session._env_import("emscripten_notify_memory_growth", has_result=False)(FakeCaller())
        )
        self.assertEqual(
            session._env_import("_emscripten_system", has_result=True)(FakeCaller()), -1
        )
        self.assertEqual(
            session._env_import("unknown", has_result=True)(FakeCaller()),
            -session.WASI_ENOSYS,
        )
        self.assertIsNone(session._env_import("unknown", has_result=False)(FakeCaller()))

        with patch.object(session.wasmtime, "Memory", FakeMemory):
            self.assertEqual(
                session._stat_host_file(FakeCaller(), 0, -1, 32, 40),
                session.WASI_EINVAL,
            )

            memory = FakeMemory(b"x.\0bad\0")
            caller = FakeCaller({"memory": memory})
            self.assertEqual(session._getcwd(caller, 10, 0), -session.WASI_EINVAL)
            self.assertEqual(session._getcwd(caller, 10, 1), -session.WASI_ERANGE)
            self.assertEqual(session._getcwd(caller, 10, 2), 2)
            self.assertEqual(memory.writes[-1], (caller, b"/\0", 10))
            self.assertEqual(session._chdir(caller, 1), 0)
            self.assertEqual(
                session._env_import("__syscall_chdir", has_result=True)(caller, 1),
                0,
            )
            self.assertEqual(
                session._chdir(FakeCaller({"memory": FakeMemory(b"xbad\0")}), 1),
                -session.WASI_ENOENT,
            )
            with self.assertRaisesRegex(EclError, "does not export memory"):
                session._memory(FakeCaller())

    def test_eval_python_import(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            py_globals: dict[str, object] = {"__builtins__": __builtins__}

            self.assertEqual(
                session._eval_python(FakeCaller(), py_globals, 0, -1, 64, 68, 72),
                session.WASI_EINVAL,
            )

            self.assertEqual(
                session._eval_python(
                    FakeCaller({"memory": MutableFakeMemory()}), py_globals, 0, 0, 64, 68, 72
                ),
                session.WASI_ENOSYS,
            )

            def run_eval(source: str, memory: MutableFakeMemory, malloc_ptr: int = 256):
                memory.data[: len(source.encode())] = source.encode()
                caller = FakeCaller(
                    {"memory": memory, "malloc": FakeFunc(lambda caller, size: malloc_ptr)}
                )
                status = session._eval_python(
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
                status = session._exec_python(
                    caller, py_globals, 0, len(source.encode()), 64, 68, 72
                )
                out_ptr = int.from_bytes(memory.data[64:68], "little", signed=True)
                out_len = int.from_bytes(memory.data[68:72], "little", signed=True)
                is_error = int.from_bytes(memory.data[72:76], "little", signed=True)
                return status, memory.data[out_ptr : out_ptr + out_len], is_error

            status, result, is_error = run_eval("1 + 2", MutableFakeMemory(size=512))
            self.assertEqual((status, result, is_error), (0, b"3", 0))

            status, result, is_error = run_eval("x = 41", MutableFakeMemory(size=512))
            self.assertEqual((status, is_error), (0, 1))
            self.assertIn(b"SyntaxError", result)

            status, result, is_error = run_exec("x = 41", MutableFakeMemory(size=512))
            self.assertEqual((status, result, is_error), (0, b"", 0))
            status, result, is_error = run_eval("x + 1", MutableFakeMemory(size=512))
            self.assertEqual((status, result, is_error), (0, b"42", 0))

            status, result, is_error = run_eval("1 / 0", MutableFakeMemory(size=512))
            self.assertEqual((status, is_error), (0, 1))
            self.assertIn(b"ZeroDivisionError", result)

            failed_alloc = MutableFakeMemory(size=512)
            failed_alloc.data[:5] = b"1 + 2"
            caller = FakeCaller(
                {"memory": failed_alloc, "malloc": FakeFunc(lambda caller, size: 0)}
            )
            self.assertEqual(
                session._eval_python(caller, py_globals, 0, 5, 64, 68, 72),
                session.WASI_ENOSYS,
            )

    def test_eval_python_default_globals(self) -> None:
        with patch.multiple(session.wasmtime, Func=FakeFunc, Memory=MutableFakeMemory):
            memory = MutableFakeMemory(b"2 ** 5", size=512)
            caller = FakeCaller({"memory": memory, "malloc": FakeFunc(lambda caller, size: 256)})
            callback = session._env_import("eclpy_eval_python", has_result=True)
            status = callback(caller, 0, 6, 64, 68, 72)
            out_ptr = int.from_bytes(memory.data[64:68], "little", signed=True)
            out_len = int.from_bytes(memory.data[68:72], "little", signed=True)
            self.assertEqual((status, memory.data[out_ptr : out_ptr + out_len]), (0, b"32"))

            exec_memory = MutableFakeMemory(b"z = 99", size=512)
            exec_caller = FakeCaller(
                {"memory": exec_memory, "malloc": FakeFunc(lambda caller, size: 256)}
            )
            exec_callback = session._env_import("eclpy_exec_python", has_result=True)
            self.assertEqual(exec_callback(exec_caller, 0, 6, 64, 68, 72), 0)
            out_ptr = int.from_bytes(exec_memory.data[64:68], "little", signed=True)
            out_len = int.from_bytes(exec_memory.data[68:72], "little", signed=True)
            self.assertEqual(exec_memory.data[out_ptr : out_ptr + out_len], b"")


if __name__ == "__main__":
    unittest.main()
