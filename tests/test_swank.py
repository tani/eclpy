"""Regression tests for the SWANK/SLIME server support in :mod:`eclpy.lisp`.

These tests speak the real SWANK-RPC wire protocol (the same one Emacs/SLIME
uses) over a raw TCP socket against the actual bundled ECL WASM runtime --
no mocks. See ``tests/test_session.py``'s
``test_lisp_tcp_socket_client_round_trip`` for the sibling
raw-socket-against-real-ECL testing style this module follows.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time
import unittest
from pathlib import Path

from eclpy import Lisp
from tests.test_session import require_wasm

SOCKET_TIMEOUT = 10.0
PORT_DISCOVERY_TIMEOUT = 10.0


def _listening_ports() -> set[int]:
    """Return TCP ports this process currently has bound in LISTEN state."""
    completed = subprocess.run(
        ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(os.getpid())],
        capture_output=True,
        text=True,
        check=False,
    )
    return {int(port) for port in re.findall(r":(\d+)\s*\(LISTEN\)", completed.stdout)}


def _send_rex(sock: socket.socket, form: str, request_id: int) -> None:
    message = f'(:emacs-rex {form} "COMMON-LISP-USER" t {request_id})'
    payload = message.encode("utf-8")
    header = f"{len(payload):06x}".encode("ascii")
    sock.sendall(header + payload)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            message = "SWANK connection closed while reading a message"
            raise ConnectionError(message)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_message(sock: socket.socket) -> str:
    length = int(_recv_exact(sock, 6), 16)
    return _recv_exact(sock, length).decode("utf-8")


def _rex_until_return(sock: socket.socket, form: str, request_id: int) -> str:
    """Send an ``:emacs-rex`` request and collect messages up to ``:return``.

    Out-of-band messages (``:write-string``, ``:new-features``, ...) and the
    terminating ``:return`` reply are concatenated so tests can assert on
    both the echoed output and the final status in one string.
    """
    _send_rex(sock, form, request_id)
    parts: list[str] = []
    while True:
        message = _read_message(sock)
        parts.append(message)
        if message.startswith("(:return"):
            return "\n".join(parts)


class SwankServerTests(unittest.TestCase):
    """Exercise ``Lisp.start_swank`` against a real SWANK client connection."""

    lisp: Lisp
    server_thread: threading.Thread
    client_socket: socket.socket

    def setUp(self) -> None:
        if shutil.which("lsof") is None:
            self.skipTest("lsof is not available to discover the bound SWANK port")
        wasm_path: Path = require_wasm()
        self._request_id = 0
        self.lisp = Lisp(wasm_path)

        before = _listening_ports()
        self.server_thread = threading.Thread(
            target=self.lisp.start_swank,
            kwargs={"port": 0, "dont_close": True},
            daemon=True,
        )
        self.server_thread.start()

        port = self._discover_bound_port(before)
        self.client_socket = socket.create_connection(("127.0.0.1", port), timeout=SOCKET_TIMEOUT)
        self.client_socket.settimeout(SOCKET_TIMEOUT)

    def tearDown(self) -> None:
        # `Lisp.close()`/`EclSession.close()` would block forever here: the
        # background thread is parked inside a single blocking
        # `session.eval()` call for as long as it serves SWANK requests (it
        # never returns while `dont_close` is true), holding the session's
        # internal lock the whole time. Only release the client-side socket;
        # the daemon thread and its WASM instance are reclaimed when the
        # test process exits.
        self.client_socket.close()

    def _discover_bound_port(self, before: set[int]) -> int:
        deadline = time.monotonic() + PORT_DISCOVERY_TIMEOUT
        time.sleep(1.0)
        while True:
            new_ports = _listening_ports() - before
            if new_ports:
                return next(iter(new_ports))
            if time.monotonic() >= deadline:
                message = "timed out waiting for start_swank to bind a port"
                raise AssertionError(message)
            time.sleep(0.1)

    def _rex(self, form: str) -> str:
        self._request_id += 1
        return _rex_until_return(self.client_socket, form, self._request_id)

    def _create_repl(self) -> None:
        reply = self._rex("(swank-repl:create-repl nil)")
        self.assertIn("(:return (:ok", reply)

    def test_listener_eval_round_trip(self) -> None:
        self._create_repl()

        reply = self._rex('(swank-repl:listener-eval "(+ 40 2)")')

        self.assertIn("42", reply)
        self.assertIn("(:return (:ok", reply)

    def test_compile_string_defines_callable_function(self) -> None:
        self._create_repl()

        compile_form = (
            '(swank:compile-string-for-emacs '
            '"(defun test-added-fn (x) (* x 2))" '
            '"buffer" (quote ((:position 0))) nil nil)'
        )
        reply = self._rex(compile_form)

        self.assertIn(":compilation-result", reply)
        self.assertIn("(:return (:ok", reply)
        # `(:compilation-result NOTES SUCCESSFUL-P DURATION LOADP FASL)`:
        # a clean compile reports no notes and a true success flag.
        self.assertIn("(:compilation-result nil t", reply)

        call_reply = self._rex('(swank-repl:listener-eval "(test-added-fn 21)")')
        self.assertIn("42", call_reply)
        self.assertIn("(:return (:ok", call_reply)

    def test_compile_string_reports_error_without_breaking_connection(self) -> None:
        self._create_repl()

        compile_form = (
            '(swank:compile-string-for-emacs '
            '"(defun broken-fn (x) (* x 2)" '
            '"buffer" (quote ((:position 0))) nil nil)'
        )
        reply = self._rex(compile_form)

        # The compiler-condition machinery reports reader/eval errors as
        # *notes* inside a still-`:ok` reply, never as a protocol `:abort`.
        self.assertIn("(:return (:ok", reply)
        self.assertNotIn(":abort", reply)
        self.assertIn(":severity :error", reply)

        # The connection must still be usable after a compile error.
        further_reply = self._rex('(swank-repl:listener-eval "(+ 1 1)")')
        self.assertIn("2", further_reply)
        self.assertIn("(:return (:ok", further_reply)

    def test_runtime_error_aborts_without_crashing_session(self) -> None:
        self._create_repl()

        reply = self._rex('(swank-repl:listener-eval "(error \\"boom\\")")')

        self.assertIn(":abort", reply)
        self.assertTrue(self.server_thread.is_alive())

        further_reply = self._rex('(swank-repl:listener-eval "(+ 5 5)")')
        self.assertIn("10", further_reply)
        self.assertIn("(:return (:ok", further_reply)
        self.assertTrue(self.server_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
