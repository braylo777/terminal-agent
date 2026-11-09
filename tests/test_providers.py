import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from terminal_agent.agent import DenyAll, run_agent
from terminal_agent.policy import CommandPolicy
from terminal_agent.pricing import cost_usd, estimate_tokens
from terminal_agent.providers import (
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ScriptedProvider,
)
from terminal_agent.sandbox import Sandbox


class _Server(ThreadingHTTPServer):
    def __init__(self, status: int, body: dict):
        self.requests = []
        self._status = status
        self._body = body
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        self.server.requests.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": json.loads(raw.decode()),
            }
        )
        payload = json.dumps(self.server._body).encode()
        self.send_response(self.server._status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


@pytest.fixture
def http_server():
    servers = []

    def start(status: int, body: dict) -> _Server:
        server = _Server(status, body)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()


def test_scripted_provider_replays_in_order_then_finishes():
    provider = ScriptedProvider(
        [
            {"action": "plan", "plan": "first"},
            "not json",
        ]
    )
    first = provider.complete([{"role": "user", "content": "task"}])
    second = provider.complete([{"role": "user", "content": "task"}])
    third = provider.complete([{"role": "user", "content": "task"}])
    assert json.loads(first.text)["plan"] == "first"
    assert second.text == "not json"
    assert json.loads(third.text)["summary"] == "script exhausted"
    assert first.prompt_tokens == estimate_tokens([{"role": "user", "content": "task"}])


def test_openai_request_shape_and_usage(http_server):
    server = http_server(
        200,
        {
            "choices": [
                {"message": {"role": "assistant", "content": '{"action":"finish","summary":"ok"}'}}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
    )
    port = server.server_address[1]
    provider = OpenAICompatibleProvider(
        base_url=f"http://127.0.0.1:{port}/v1",
        model="unit-test-model",
        api_key="sk-unit",
    )
    completion = provider.complete([{"role": "user", "content": "hi"}])
    assert completion.text.startswith("{")
    assert completion.prompt_tokens == 3
    assert completion.completion_tokens == 4
    recorded = server.requests[0]
    assert recorded["path"] == "/v1/chat/completions"
    assert recorded["auth"] == "Bearer sk-unit"
    assert recorded["body"]["model"] == "unit-test-model"
    assert recorded["body"]["messages"][0]["content"] == "hi"
    assert recorded["body"]["temperature"] == 0


def test_openai_request_includes_max_tokens(http_server):
    server = http_server(
        200,
        {
            "choices": [{"message": {"content": '{"action":"finish","summary":"ok"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )
    port = server.server_address[1]
    provider = OpenAICompatibleProvider(
        base_url=f"http://127.0.0.1:{port}/v1",
        model="unit-test-model",
        api_key="sk-unit",
    )
    provider.complete([{"role": "user", "content": "hi"}], max_tokens=512)
    assert server.requests[0]["body"]["max_tokens"] == 512


def test_ollama_sends_no_authorization_header(http_server):
    server = http_server(
        200,
        {
            "choices": [{"message": {"content": '{"action":"finish","summary":"local"}'}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        },
    )
    port = server.server_address[1]
    provider = OllamaProvider(model="llama-unit", base_url=f"http://127.0.0.1:{port}/v1")
    completion = provider.complete([{"role": "user", "content": "hi"}])
    assert completion.model == "llama-unit"
    assert server.requests[0]["auth"] is None
    assert provider.api_key == ""


def test_openai_http_error(http_server):
    server = http_server(401, {"error": {"message": "bad key"}})
    port = server.server_address[1]
    provider = OpenAICompatibleProvider(
        base_url=f"http://127.0.0.1:{port}/v1",
        model="unit-test-model",
        api_key="sk-unit",
    )
    with pytest.raises(ProviderError) as caught:
        provider.complete([{"role": "user", "content": "hi"}])
    assert "401" in str(caught.value)


def test_agent_uses_api_usage_for_cost(http_server, tmp_path):
    server = http_server(
        200,
        {
            "choices": [
                {"message": {"content": '{"action":"finish","summary":"ok"}'}}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
    )
    port = server.server_address[1]
    provider = OpenAICompatibleProvider(
        base_url=f"http://127.0.0.1:{port}/v1",
        model="mock",
        api_key="sk-unit",
    )
    result = run_agent(
        "task",
        sandbox=Sandbox(tmp_path),
        provider=provider,
        policy=CommandPolicy(tmp_path),
        confirmer=DenyAll(),
    )
    assert result.reason == "finish"
    assert result.spent_usd == round(cost_usd("mock", 3, 4), 8)
    assert result.price_basis == "illustrative"


def test_estimate_tokens_is_a_char_ratio_not_a_tokenizer():
    assert estimate_tokens([{"content": "abcd"}]) == 1
    assert estimate_tokens([{"content": "a" * 8}]) == 2
