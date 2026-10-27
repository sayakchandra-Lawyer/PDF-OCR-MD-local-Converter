"""Minimal Ollama client (stdlib only). Talks to the local server, never the internet."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Callable


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 600.0,
        keep_alive: str = "15m",
        num_ctx: int = 16384,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self._models: list[str] | None = None

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise OllamaError(f"Ollama HTTP {e.code} on {path}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise OllamaError(f"Ollama not reachable at {self.base_url}: {e}") from e

    def version(self) -> str | None:
        try:
            return self._request("GET", "/api/version", timeout=3).get("version")
        except OllamaError:
            return None

    def available(self) -> bool:
        return self.version() is not None

    def models(self, refresh: bool = False) -> list[str]:
        if self._models is None or refresh:
            try:
                data = self._request("GET", "/api/tags", timeout=5)
                self._models = [m.get("name", "") for m in data.get("models", [])]
            except OllamaError:
                self._models = []
        return self._models

    def has_model(self, name: str) -> bool:
        if not name:
            return False
        wanted = name if ":" in name else name + ":latest"
        return any(m == wanted or m == name for m in self.models())

    def chat(
        self,
        model: str,
        prompt: str,
        images: list[bytes] | None = None,
        system: str | None = None,
        json_schema: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        msg: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            msg["images"] = [base64.b64encode(b).decode("ascii") for b in images]
        messages.append(msg)
        options: dict[str, Any] = {"temperature": temperature, "num_ctx": self.num_ctx}
        if max_tokens:
            options["num_predict"] = max_tokens
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": options,
        }
        if json_schema is not None:
            body["format"] = json_schema
        data = self._request("POST", "/api/chat", body)
        content = (data.get("message") or {}).get("content", "")
        if not content.strip():
            raise OllamaError(f"Empty response from model {model}")
        return content

    def pull(self, model: str, on_progress: Callable[[str], None] | None = None) -> None:
        req = urllib.request.Request(
            self.base_url + "/api/pull",
            data=json.dumps({"model": model, "stream": True}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        last = ""
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    evt = json.loads(line)
                    if evt.get("error"):
                        raise OllamaError(evt["error"])
                    status = evt.get("status", "")
                    if evt.get("total") and evt.get("completed"):
                        status += f" {evt['completed'] * 100 // evt['total']}%"
                    if on_progress and status != last:
                        on_progress(status)
                        last = status
        except urllib.error.URLError as e:
            raise OllamaError(f"Ollama not reachable at {self.base_url}: {e}") from e
        self._models = None
