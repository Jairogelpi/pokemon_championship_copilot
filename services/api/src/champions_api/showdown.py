from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4


class ShowdownUnavailable(RuntimeError):
    """Raised when the official damage calculator cannot answer safely."""


class ShowdownCalculationError(ValueError):
    """Raised when @smogon/calc rejects a calculation request."""


class ShowdownCalculator:
    def __init__(
        self,
        repo_root: Path | None = None,
        node_binary: str | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[4]
        self.worker_path = self.repo_root / "services" / "showdown-calc" / "worker.mjs"
        self.node_binary = node_binary or os.environ.get("SHOWDOWN_NODE") or shutil.which("node")
        self.timeout = timeout
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=20)
        self._lock = threading.RLock()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - best-effort interpreter cleanup
            pass

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is None:
                return
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def health(self) -> dict[str, Any]:
        try:
            result = self._request("health", {})
            return {**result, "available": True}
        except (ShowdownUnavailable, ShowdownCalculationError) as exc:
            return {
                "status": "unavailable",
                "available": False,
                "engine": "@smogon/calc",
                "message": str(exc),
            }

    def calculate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("calculate", payload)

    def batch(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not requests:
            return []
        result = self._request("batch", {"requests": requests})
        values = result.get("results")
        if not isinstance(values, list):
            raise ShowdownUnavailable("calculator returned a malformed batch response")
        return values

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self.node_binary:
            raise ShowdownUnavailable("Node.js was not found; install Node 20 or newer")
        if not self.worker_path.is_file():
            raise ShowdownUnavailable(f"calculator worker is missing: {self.worker_path}")
        package_path = self.repo_root / "node_modules" / "@smogon" / "calc"
        if not package_path.is_dir():
            raise ShowdownUnavailable("@smogon/calc is not installed; run npm ci")

        self._responses = queue.Queue()
        self._stderr.clear()
        try:
            self._process = subprocess.Popen(
                [self.node_binary, str(self.worker_path)],
                cwd=self.repo_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise ShowdownUnavailable(f"could not start calculator worker: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                self._stderr.append(f"invalid worker output: {line.rstrip()}")
                continue
            if isinstance(value, dict):
                self._responses.put(value)
        self._responses.put(None)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.rstrip())

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._start()
            process = self._process
            if process is None or process.stdin is None:
                raise ShowdownUnavailable("calculator process did not expose stdin")
            request_id = uuid4().hex
            try:
                process.stdin.write(
                    json.dumps(
                        {"id": request_id, "method": method, "params": params},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                details = "; ".join(self._stderr) or str(exc)
                self.close()
                raise ShowdownUnavailable(f"calculator worker stopped: {details}") from exc

            try:
                response = self._responses.get(timeout=self.timeout)
            except queue.Empty as exc:
                self.close()
                raise ShowdownUnavailable(
                    f"calculator did not answer within {self.timeout:g} seconds"
                ) from exc
            if response is None:
                details = "; ".join(self._stderr) or "worker exited without a response"
                self.close()
                raise ShowdownUnavailable(details)
            if response.get("id") != request_id:
                self.close()
                raise ShowdownUnavailable("calculator response ID did not match the request")
            if not response.get("ok"):
                error = response.get("error") or {}
                raise ShowdownCalculationError(str(error.get("message", "calculation failed")))
            result = response.get("result")
            if not isinstance(result, dict):
                raise ShowdownUnavailable("calculator returned a malformed response")
            return result
