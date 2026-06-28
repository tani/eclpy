Patch Directory
===============

Keep vendored upstream source trees unchanged. Store local build-time changes
here and apply them to copied source trees under ``build/``.

Each task owns one subdirectory. Patch files are applied in lexicographic order,
so prefix them with a stable number:

- ``patch/ecl-wasm/*.patch``: ECL patches needed for the Emscripten WASM runtime.

