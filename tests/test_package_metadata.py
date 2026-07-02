"""Pure-Python checks that eclpy/python.lisp is packaged, no WASM required."""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackageMetadataTests(unittest.TestCase):
    def test_python_lisp_source_is_tracked(self) -> None:
        self.assertTrue((ROOT / "eclpy" / "python.lisp").is_file())

    def test_python_lisp_is_a_wheel_artifact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)
        artifacts = data["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"]
        self.assertIn("eclpy/python.lisp", artifacts)

    def test_python_lisp_is_force_included_in_sdist(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)
        force_include = data["tool"]["hatch"]["build"]["targets"]["sdist"]["force-include"]
        self.assertEqual(force_include.get("eclpy/python.lisp"), "eclpy/python.lisp")


if __name__ == "__main__":
    unittest.main()
