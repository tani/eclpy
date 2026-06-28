from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from eclpy import EclError, EclSession, session


def fake_engine() -> object:
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


class FakeCaller:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get(self, name: str) -> object | None:
        return self.values.get(name)


class FakeTable:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self, caller, index: int) -> object:
        return self.value


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

    def test_export_and_zero_helpers(self) -> None:
        value = FakeFunc()
        self.assertIs(session._export({"f": value}, "f", FakeFunc), value)
        with self.assertRaisesRegex(EclError, "does not export"):
            session._export({}, "missing", FakeFunc)
        self.assertEqual(session._zero("i32"), 0)
        self.assertEqual(session._zero("f32"), 0.0)
        self.assertEqual(session._zero("f64"), 0.0)

    def test_read_c_string(self) -> None:
        self.assertEqual(session._read_c_string(FakeMemory(), object(), 0), "")
        self.assertEqual(session._read_c_string(FakeMemory(b"\xffab\0tail"), object(), 0), "")
        self.assertEqual(session._read_c_string(FakeMemory(b"xxhi\0tail"), object(), 2), "hi")

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

    def test_env_func_imports_and_definitions(self) -> None:
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
            imports = session._env_func_imports(module)
            self.assertEqual([name for name, _type in imports], ["invoke_ii", "regular"])
            session._define_emscripten_imports(linker, module)
        self.assertEqual([item[1] for item in linker.defined], ["invoke_ii", "regular"])

    def test_invoke_import_paths(self) -> None:
        with patch.multiple(session.wasmtime, Table=FakeTable, Func=FakeFunc):
            callback = session._invoke_import("invoke_ii", FakeFuncType(["i32"]))
            with self.assertRaisesRegex(EclError, "indirect function table"):
                callback(FakeCaller(), 0)
            with self.assertRaisesRegex(EclError, "missing table entry"):
                callback(FakeCaller({"__indirect_function_table": FakeTable(object())}), 0)

            caller = FakeCaller(
                {"__indirect_function_table": FakeTable(FakeFunc(lambda caller, x: x + 1))}
            )
            self.assertEqual(callback(caller, 0, 4), 5)

            no_result = session._invoke_import("invoke_v", FakeFuncType([]))
            caller = FakeCaller(
                {"__indirect_function_table": FakeTable(FakeFunc(lambda caller: 99))}
            )
            self.assertIsNone(no_result(caller, 0))

            restore_stack = FakeFunc()
            set_threw = FakeFunc()
            longjmp_caller = FakeCaller(
                {
                    "__indirect_function_table": FakeTable(
                        FakeFunc(lambda caller: (_ for _ in ()).throw(session._LongjmpError()))
                    ),
                    "emscripten_stack_get_current": FakeFunc(lambda caller: 123),
                    "_emscripten_stack_restore": restore_stack,
                    "setThrew": set_threw,
                }
            )
            self.assertEqual(callback(longjmp_caller, 0), 0)
            self.assertEqual(restore_stack.calls[-1], (longjmp_caller, 123))
            self.assertEqual(set_threw.calls[-1], (longjmp_caller, 1, 0))

    def test_env_import_and_syscall_helpers(self) -> None:
        self.assertIsNone(
            session._env_import("emscripten_notify_memory_growth", has_result=False)(FakeCaller())
        )
        with self.assertRaises(session._LongjmpError):
            session._env_import("_emscripten_throw_longjmp", has_result=False)(FakeCaller())
        self.assertEqual(
            session._env_import("_emscripten_system", has_result=True)(FakeCaller()), -1
        )
        self.assertEqual(
            session._env_import("unknown", has_result=True)(FakeCaller()),
            -session.WASI_ENOSYS,
        )
        self.assertIsNone(session._env_import("unknown", has_result=False)(FakeCaller()))

        with patch.object(session.wasmtime, "Memory", FakeMemory):
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
            self.assertIs(session._caller_func(FakeCaller({"fn": FakeFunc()}), "fn"), None)
            with patch.object(session.wasmtime, "Func", FakeFunc):
                func = FakeFunc()
                self.assertIs(session._caller_func(FakeCaller({"fn": func}), "fn"), func)
            with self.assertRaisesRegex(EclError, "does not export memory"):
                session._memory(FakeCaller())


if __name__ == "__main__":
    unittest.main()
