from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WasmArtifactBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel":
            return

        wasm_path = Path(self.root) / "eclpy" / "ecl_eval.wasm"
        if not wasm_path.is_file():
            message = (
                f"missing required wheel artifact: {wasm_path}. "
                "Run `uv run python scripts/build_ecl_wasm.py` before building a wheel."
            )
            raise FileNotFoundError(message)
