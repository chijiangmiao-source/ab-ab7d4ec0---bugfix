"""HTTP API built only on the Python standard library.

Endpoints
---------
* ``GET  /healthz`` -- liveness probe used by Compose.
* ``POST /api/audit`` -- validate an instance, solve the minimum-cost subnet
  joining every calibration endpoint, and return the canonical edge set plus
  the adjacency list it induces.

Failures always respond with a complete, stable error document
(``{"code", "message", "pointer"?}``); a failed audit never returns a partial
subnet and never reuses state from a previous request -- every request builds
a fresh problem and solver run.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .errors import TopologyError, ValidationError
from .solver import build_adjacency, solve
from .validation import parse_problem

MAX_BODY_BYTES = 2_000_000


def _solve_payload(payload: Any) -> dict[str, Any]:
    problem = parse_problem(payload)
    cost, selected, edge_ids = solve(problem)
    return {
        "cost": cost,
        "edge_set": list(edge_ids),
        "edges": [
            {
                "id": e.id,
                "source": problem.nodes[e.source],
                "target": problem.nodes[e.target],
                "cost": e.cost,
            }
            for e in sorted(selected, key=lambda e: e.id)
        ],
        "adjacency": build_adjacency(problem.nodes, selected),
    }


class AuditHandler(BaseHTTPRequestHandler):
    server_version = "CalibrationAudit/1.0"

    # --- helpers -------------------------------------------------------

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(
        self, status: int, code: str, message: str, pointer: str = ""
    ) -> None:
        body: dict[str, Any] = {"code": code, "message": message}
        if pointer:
            body["pointer"] = pointer
        self._send_json(status, body)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep stderr concise; verification relies on exit codes, not logs.
        # log_request passes (requestline, status, size) as format args.
        status = args[1] if len(args) > 1 else "-"
        print(f'[audit] {self.address_string()} "{self.requestline}" {status}')

    # --- routes --------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path.split("?", 1)[0] == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if self.path.split("?", 1)[0] == "/api/audit":
            self._send_error(
                405,
                "METHOD_NOT_ALLOWED",
                "POST /api/audit with a JSON body",
            )
            return
        self._send_error(404, "NOT_FOUND", f"unknown path: {self.path}")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/audit":
            self._send_error(404, "NOT_FOUND", f"unknown path: {self.path}")
            return

        length_hdr = self.headers.get("Content-Length")
        try:
            length = int(length_hdr) if length_hdr is not None else -1
        except ValueError:
            self._send_error(
                400, "INVALID_CONTENT_LENGTH", "Content-Length must be an integer"
            )
            return
        if length < 0:
            self._send_error(411, "LENGTH_REQUIRED", "Content-Length is required")
            return
        if length > MAX_BODY_BYTES:
            self._send_error(
                413,
                "PAYLOAD_TOO_LARGE",
                f"request body exceeds {MAX_BODY_BYTES} bytes",
            )
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            self._send_error(400, "MALFORMED_JSON", "request body must be UTF-8 JSON")
            return
        except json.JSONDecodeError as exc:
            self._send_error(
                400,
                "MALFORMED_JSON",
                f"request body is not valid JSON: {exc.msg}",
            )
            return

        try:
            result = _solve_payload(payload)
        except ValidationError as exc:
            self._send_json(400, exc.to_dict())
            return
        except TopologyError as exc:
            self._send_json(422, exc.to_dict())
            return
        except Exception:
            # Log internally but never leak internals or a partial subnet.
            import traceback

            traceback.print_exc()
            self._send_json(
                500,
                {
                    "code": "INTERNAL_ERROR",
                    "message": "solver failed while computing the subnet",
                },
            )
            return

        self._send_json(200, result)


def build_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), AuditHandler)


def main() -> None:
    host = os.environ.get("AUDIT_HOST", "0.0.0.0")
    port = int(os.environ.get("AUDIT_PORT", "8080"))
    httpd = build_server(host, port)
    print(f"[audit] listening on {host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
