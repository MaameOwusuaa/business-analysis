"""Snwolley Agent API Toolkit client.

``SnwolleyClient`` talks to the real toolkit over HTTPS. Its endpoint, agent id,
API key and response field are all configuration (environment variables), so
when Npontu shares the toolkit specification only ``.env`` - and at most the two
small ``_payload`` / ``_extract`` adapters below - need to change.

``MockSnwolleyClient`` honours the same interface and message format but answers
deterministically from the context pack. It keeps the product demonstrable and
testable without credentials, and it is used as a labelled fallback if the live
service fails.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import Settings, get_settings
from ..logging_setup import get_logger
from . import mock_writer

log = get_logger("agent.client")


class AgentError(RuntimeError):
    pass


@dataclass
class AgentResponse:
    text: str
    provider: str  # "snwolley" | "mock"
    latency_ms: int
    note: str | None = None


class AgentClient(Protocol):
    provider: str

    def complete(self, messages: list[dict], task: str) -> AgentResponse: ...


class SnwolleyClient:
    provider = "snwolley"

    def __init__(self, settings: Settings):
        if not settings.snwolley_base_url or not settings.snwolley_api_key:
            raise AgentError("Snwolley live mode needs SNWOLLEY_BASE_URL and SNWOLLEY_API_KEY.")
        self._url = settings.snwolley_base_url.rstrip("/") + settings.snwolley_chat_path
        self._key = settings.snwolley_api_key  # held in memory only; never logged
        self._agent_id = settings.snwolley_agent_id
        self._timeout = settings.snwolley_timeout_s
        self._response_path = settings.snwolley_response_path
        self._session = requests.Session()
        retry = Retry(total=3, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"POST"}), respect_retry_after_header=True)
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.mount("http://", HTTPAdapter(max_retries=retry))

    # --- adapters: the only toolkit-specific code -------------------------------
    def _payload(self, messages: list[dict], task: str) -> dict:
        payload: dict[str, Any] = {"messages": messages, "temperature": 0.2, "max_tokens": 900,
                                   "metadata": {"app": "insightforge", "task": task}}
        if self._agent_id:
            payload["agent_id"] = self._agent_id
        return payload

    def _extract(self, body: Any) -> str:
        node = body
        for part in self._response_path.split("."):
            if isinstance(node, list):
                node = node[int(part)]
            elif isinstance(node, dict):
                node = node.get(part)
            else:
                node = None
            if node is None:
                break
        if not isinstance(node, str) or not node.strip():
            raise AgentError(f"Unexpected Snwolley response shape (looked for '{self._response_path}').")
        return node

    # ---------------------------------------------------------------------------
    def complete(self, messages: list[dict], task: str) -> AgentResponse:
        t0 = time.time()
        try:
            resp = self._session.post(
                self._url,
                json=self._payload(messages, task),
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise AgentError(f"Snwolley request failed: {exc.__class__.__name__}") from None
        latency = int((time.time() - t0) * 1000)
        if resp.status_code in (401, 403):
            raise AgentError("Snwolley rejected the credentials (check SNWOLLEY_API_KEY).")
        if resp.status_code >= 400:
            raise AgentError(f"Snwolley returned HTTP {resp.status_code}.")
        try:
            text = self._extract(resp.json())
        except ValueError:
            raise AgentError("Snwolley returned a non-JSON response.") from None
        log.info("snwolley call ok task=%s latency_ms=%s", task, latency)
        return AgentResponse(text=text.strip(), provider=self.provider, latency_ms=latency)


class MockSnwolleyClient:
    """Deterministic stand-in with the same contract as the live client."""

    provider = "mock"
    _CTX = re.compile(r"<context>\s*(.*)\s*</context>", re.S)
    _Q = re.compile(r"^QUESTION: (.*)$", re.M)

    def complete(self, messages: list[dict], task: str) -> AgentResponse:
        t0 = time.time()
        user = next(m["content"] for m in messages if m["role"] == "user")
        m = self._CTX.search(user)
        if not m:
            raise AgentError("Mock agent: no context block found.")
        context = json.loads(m.group(1))
        q = self._Q.search(user)
        text = mock_writer.write(task, context, q.group(1) if q else None)
        return AgentResponse(text=text, provider=self.provider, latency_ms=int((time.time() - t0) * 1000))


def get_agent_client(settings: Settings | None = None) -> AgentClient:
    settings = settings or get_settings()
    if settings.snwolley_mode == "live":
        return SnwolleyClient(settings)
    return MockSnwolleyClient()
