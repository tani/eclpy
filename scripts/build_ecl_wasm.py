#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
ECL_DIR = "ecl-26.5.5"
VENDORED_ECL = ROOT / "vendor" / ECL_DIR
WRAPPER = ROOT / "native" / "eclpy_eval.c"

HOST_SRC = BUILD / "ecl-host-src"
WASM_SRC = BUILD / "ecl-wasm-src"
HOST_PREFIX = BUILD / "ecl-host"
WASM_PREFIX = BUILD / "ecl-wasm"
OUT_WASM = BUILD / "eclpy" / "ecl_eval.wasm"
PACKAGE_WASM = ROOT / "eclpy" / "ecl_eval.wasm"
ECL_WASM_PATCH_DIR = ROOT / "patch" / "ecl-wasm"

ECL_LIBS = [
    ("libecl-help.a", False),
    ("libecl-cdb.a", False),
    ("libecl.a", True),
    ("libeclgc.a", True),
    ("libeclgmp.a", True),
]
EXPORTED_FUNCTIONS = (
    "['_eclpy_alloc','_eclpy_free','_eclpy_init','_eclpy_eval',"
    "'_eclpy_last_error','_eclpy_shutdown','_malloc','_free']"
)
UNSUPPORTED_PRLIMIT64_WARNING = "unsupported syscall: __syscall_prlimit64"


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    where = f" cwd={cwd}" if cwd else ""
    print(f"+ {' '.join(args)}{where}", flush=True)
    subprocess.run(args, cwd=cwd, env=env, check=True)


def source_tree(target: Path, *, fresh: bool) -> Path:
    source = target / ECL_DIR
    if source.exists() and not fresh:
        return source
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    shutil.copytree(VENDORED_ECL, source)
    return source


def host_triplet() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return f"{'aarch64' if machine in {'arm64', 'aarch64'} else machine}-apple-darwin"
    if system == "linux":
        return f"{'x86_64' if machine in {'amd64', 'x86_64'} else machine}-pc-linux-gnu"
    return f"{machine}-unknown-{system}"


def build_host(*, force: bool) -> Path:
    ecl = HOST_PREFIX / "bin" / "ecl"
    if ecl.exists() and not force:
        print(f"host ECL already exists: {ecl}")
        return ecl

    source = source_tree(HOST_SRC, fresh=force)
    shutil.rmtree(HOST_PREFIX, ignore_errors=True)
    run("./configure", f"--prefix={HOST_PREFIX}", "--disable-shared", cwd=source)
    run("make", f"-j{os.cpu_count() or 2}", cwd=source)
    run("make", "install", cwd=source)
    return ecl


def build_wasm(host_ecl: Path, *, force: bool) -> Path:
    if find_library("libecl.a", required=False) and not force:
        print(f"wasm ECL already exists: {WASM_PREFIX}")
        return WASM_PREFIX

    source = source_tree(WASM_SRC, fresh=True)
    apply_patch_dir(source, ECL_WASM_PATCH_DIR)
    shutil.rmtree(WASM_PREFIX, ignore_errors=True)

    env = os.environ.copy()
    env["ECL_TO_RUN"] = str(host_ecl)
    env["CC_FOR_BUILD"] = env.get("CC_FOR_BUILD") or shutil.which("cc") or "cc"
    env["CFLAGS"] = env.get("CFLAGS") or "-O0"
    env["CXXFLAGS"] = env.get("CXXFLAGS") or "-O0"
    env["LDFLAGS"] = without_spill_pointers(env.get("LDFLAGS", ""))

    run(
        "emconfigure",
        "./configure",
        "--host=wasm32-unknown-emscripten",
        f"--build={host_triplet()}",
        f"--with-cross-config={source / 'src/util/wasm32-unknown-emscripten.cross_config'}",
        f"--prefix={WASM_PREFIX}",
        "--disable-shared",
        "--with-tcp=no",
        "--with-cmp=no",
        cwd=source,
        env=env,
    )
    run("emmake", "make", f"-j{os.cpu_count() or 2}", cwd=source, env=env)
    run("emmake", "make", "install", cwd=source, env=env)
    return WASM_PREFIX


def apply_patch_dir(source: Path, patch_dir: Path) -> None:
    if not patch_dir.is_dir():
        message = f"missing ECL patch directory: {patch_dir}"
        raise SystemExit(message)
    for patch in sorted(patch_dir.glob("*.patch")):
        run("patch", "-p1", "-i", str(patch), cwd=source)


def without_spill_pointers(flags: str) -> str:
    return " ".join(
        flag for flag in flags.split() if flag != "-sBINARYEN_EXTRA_PASSES=--spill-pointers"
    )


def link_wrapper() -> Path:
    OUT_WASM.parent.mkdir(parents=True, exist_ok=True)
    libs = [
        str(path)
        for name, required in ECL_LIBS
        if (path := find_library(name, required=required)) is not None
    ]
    run(
        "emcc",
        str(WRAPPER),
        f"-I{include_root()}",
        *libs,
        "-O0",
        "--no-entry",
        "-sSTANDALONE_WASM=1",
        "-sALLOW_MEMORY_GROWTH=1",
        "-sSTACK_SIZE=1048576",
        "-lm",
        f"-sEXPORTED_FUNCTIONS={EXPORTED_FUNCTIONS}",
        "-sEXPORTED_RUNTIME_METHODS=[]",
        "-o",
        str(OUT_WASM),
    )
    return OUT_WASM


def package_wasm() -> Path:
    PACKAGE_WASM.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUT_WASM, PACKAGE_WASM)
    print(f"packaged {PACKAGE_WASM}")
    return PACKAGE_WASM


def include_root() -> Path:
    for path in (WASM_PREFIX / "include", WASM_PREFIX):
        if (path / "ecl" / "ecl.h").is_file():
            return path
    message = f"could not find ECL headers under {WASM_PREFIX}"
    raise SystemExit(message)


def find_library(name: str, *, required: bool) -> Path | None:
    for path in (WASM_PREFIX / name, WASM_PREFIX / "lib" / name):
        if path.is_file():
            return path
    if required:
        message = f"could not find {name} under {WASM_PREFIX}"
        raise SystemExit(message)
    return None


def smoke_test() -> None:
    code = (
        "from eclpy import EclSession\n"
        "with EclSession() as ecl:\n"
        "    assert ecl.eval('(+ 1 2)') == '3'\n"
        "    ecl.eval('(defparameter *x* 41)')\n"
        "    assert ecl.eval('(1+ *x*)') == '42'\n"
    )
    print(f"+ {sys.executable} -c {code!r}", flush=True)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        check=True,
    )
    if UNSUPPORTED_PRLIMIT64_WARNING in completed.stderr:
        raise SystemExit(completed.stderr.strip())
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ECL as a WASM module for eclpy.")
    parser.add_argument("--force", action="store_true", help="rebuild all ECL artifacts")
    parser.add_argument("--force-wasm", action="store_true", help="rebuild wasm ECL only")
    parser.add_argument("--skip-smoke", action="store_true", help="skip Python smoke test")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not VENDORED_ECL.is_dir():
        message = f"missing vendored ECL source: {VENDORED_ECL}"
        raise SystemExit(message)
    if not WRAPPER.is_file():
        message = f"missing required file: {WRAPPER}"
        raise SystemExit(message)

    build_wasm(build_host(force=args.force), force=args.force or args.force_wasm)
    print(f"built {link_wrapper()}")
    package_wasm()
    if not args.skip_smoke:
        smoke_test()


if __name__ == "__main__":
    main()
