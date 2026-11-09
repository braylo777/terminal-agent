"""LLM providers. Tests and the built-in suite use the scripted mock.

OpenAICompatibleProvider talks to any POST {base}/chat/completions endpoint.
OllamaProvider is that client pointed at a local Ollama server, which speaks
the same shape at http://127.0.0.1:11434/v1 and does not need an API key.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from terminal_agent.pricing import estimate_tokens


class ProviderError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


@dataclass
class ScriptedProvider:
    """Replay a list of actions or raw strings. This is not a model.

    Dict items are JSON-encoded. String items are returned as the raw reply,
    which is how tests cover parse failures.
    """

    items: list
    model: str = "mock"
    calls: list = field(default_factory=list)
    index: int = 0

    def complete(self, messages: list[dict], max_tokens: int | None = None) -> Completion:
        self.calls.append(messages)
        if self.index >= len(self.items):
            text = json.dumps({"action": "finish", "summary": "script exhausted"})
        else:
            item = self.items[self.index]
            self.index += 1
            text = item if isinstance(item, str) else json.dumps(item)
        return Completion(
            text=text,
            prompt_tokens=estimate_tokens(messages),
            completion_tokens=max(1, estimate_tokens([{"content": text}])),
            model=self.model,
        )


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    def complete(self, messages: list[dict], max_tokens: int | None = None) -> Completion:
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        # Ask the server for the same cap the preflight priced. Servers can ignore it.
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        body = json.dumps(payload).encode()
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"request to {url} failed: {exc.reason}") from exc
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape from {url}") from exc
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            usage = {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if not isinstance(prompt_tokens, int):
            prompt_tokens = estimate_tokens(messages)
        if not isinstance(completion_tokens, int):
            completion_tokens = max(1, estimate_tokens([{"content": text or ""}]))
        return Completion(
            text=text or "",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.model,
        )


class OllamaProvider(OpenAICompatibleProvider):
    """Local Ollama via its OpenAI-compatible endpoint. No API key."""

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://127.0.0.1:11434/v1",
        timeout_s: float = 120.0,
    ):
        super().__init__(base_url=base_url, model=model, api_key="", timeout_s=timeout_s)
