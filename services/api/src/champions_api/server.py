from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from champions_copilot.events import EventValidationError

from .service import AppService
from .showdown import ShowdownUnavailable


class CopilotHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], repo_root: Path, service: AppService) -> None:
        super().__init__(address, CopilotHandler)
        self.repo_root = repo_root
        self.web_root = repo_root / "apps" / "web"
        self.service = service

    def server_close(self) -> None:
        self.service.close()
        super().server_close()


class CopilotHandler(BaseHTTPRequestHandler):
    server: CopilotHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                self._json(HTTPStatus.OK, self.server.service.health())
            elif path == "/api/team":
                self._json(HTTPStatus.OK, self.server.service.team())
            elif path == "/api/matches":
                self._json(HTTPStatus.OK, self.server.service.list_matches())
            elif match := re.fullmatch(r"/api/matches/([a-f0-9]+)", path):
                self._json(HTTPStatus.OK, self.server.service.get_match(match.group(1)))
            elif match := re.fullmatch(r"/api/matches/([a-f0-9]+)/export", path):
                self._json(HTTPStatus.OK, self.server.service.export_match(match.group(1)))
            else:
                self._static(path)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            self._error(exc)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/matches":
                self._json(HTTPStatus.CREATED, self.server.service.create_match(payload))
            elif path == "/api/calculate/damage":
                self._json(HTTPStatus.OK, self.server.service.damage(payload))
            elif path == "/api/calculate/showdown":
                self._json(HTTPStatus.OK, self.server.service.showdown_damage(payload))
            elif path == "/api/calculate/showdown/batch":
                self._json(HTTPStatus.OK, self.server.service.showdown_batch(payload))
            elif path == "/api/calculate/speed":
                self._json(HTTPStatus.OK, self.server.service.speed(payload))
            elif match := re.fullmatch(r"/api/matches/([a-f0-9]+)/events", path):
                self._json(
                    HTTPStatus.CREATED,
                    self.server.service.record_event(match.group(1), payload),
                )
            elif match := re.fullmatch(r"/api/matches/([a-f0-9]+)/corrections", path):
                self._json(
                    HTTPStatus.CREATED,
                    self.server.service.correct_event(match.group(1), payload),
                )
            elif match := re.fullmatch(r"/api/matches/([a-f0-9]+)/recommend", path):
                self._json(HTTPStatus.OK, self.server.service.recommend(match.group(1)))
            elif match := re.fullmatch(r"/api/matches/([a-f0-9]+)/interpret", path):
                self._json(HTTPStatus.OK, self.server.service.interpret(match.group(1), payload))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            self._error(exc)

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 1_000_000:
            raise ValueError("request body is too large")
        raw = self.rfile.read(content_length) if content_length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        requested = (self.server.web_root / relative).resolve()
        if self.server.web_root.resolve() not in requested.parents and requested != self.server.web_root.resolve():
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not requested.is_file():
            requested = self.server.web_root / "index.html"
        body = requested.read_bytes()
        content_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, (ValueError, EventValidationError, json.JSONDecodeError)):
            status = HTTPStatus.BAD_REQUEST
        elif isinstance(exc, KeyError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, ShowdownUnavailable):
            status = HTTPStatus.SERVICE_UNAVAILABLE
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._json(status, {"error": type(exc).__name__, "message": str(exc)})


def create_server(repo_root: Path, host: str, port: int) -> CopilotHTTPServer:
    return CopilotHTTPServer((host, port), repo_root=repo_root, service=AppService())
