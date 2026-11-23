"""A small production-style client for an OpenAI-compatible chat API.

This is the client every chapter's live example uses when it needs a
real model. It is deliberately boring: it reads its configuration
from the environment, posts JSON over HTTPS, retries the failures
that are worth retrying, and returns the provider's response
unchanged so the caller can see the token counts and the cost.

Configuration (environment variables):

    LLM_BASE_URL    Base URL of the chat API, for example
                    https://api.opencheap.si/v1. Defaults to OpenCheap.
    LLM_API_KEY     The key. No default; a missing key is a loud
                    error at call time, not a silent fallback.
    LLM_MODEL       The model for agent decisions and summaries.
                    Defaults to kimi-k3.1-pro.
    LLM_JUDGE_MODEL The model for guardrail judgements. Defaults to
                    the cheapest route of the same model,
                    kimi-k3.1-pro:floor, because a judgement is a
                    short classification, not a reasoning task.
    LLM_TIMEOUT     Request timeout in seconds. Defaults to 120.

The client never invents a reply. If the provider is unreachable,
the key is missing, or the response is not valid JSON, the call
raises instead of returning a plausible-looking fake. A security
demo that silently pretends is worse than one that fails loudly.

Two transports are supported. The default transport posts to
``LLM_BASE_URL`` with the key from ``LLM_API_KEY``. The
``opencheap-connector`` transport instead calls the registered
OpenCheap connector helper, which authenticates through the Secure
Vault without the key ever entering this process. Select it with
``LLM_BACKEND=opencheap-connector``; it only exists on the author's
own machine, where the connector is registered, and it is how this
chapter's demo is run there. Students use the default transport
with their own key.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_BASE_URL = "https://api.opencheap.si/v1"
DEFAULT_MODEL = "kimi-k3.1-pro"
DEFAULT_JUDGE_MODEL = "kimi-k3.1-pro:floor"
DEFAULT_TIMEOUT = 120

# Retried statuses: rate limits and server-side failures. A 400 or
# a 401 is a bug in the request or the credential, and retrying it
# only spends money twice.
_RETRYABLE = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """A model call that did not produce a usable reply."""


class MissingKeyError(LLMError):
    """No API key is configured for the default transport."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LLMError("API endpoint redirected; verify the endpoint before continuing")


def _env_transport(base_url: str, api_key: str, timeout: int) -> Callable[[dict], dict]:
    """Post a payload to the chat completions endpoint with a key."""

    def transport(payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
            },
            method="POST",
        )
        opener = urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise LLMError(f"API returned HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise LLMError(f"API request did not complete: {error}") from None

    return transport


def _opencheap_connector_transport() -> Callable[[dict], dict]:
    """Post a payload through the registered OpenCheap connector.

    The connector helper authenticates with the credential held in
    the Secure Vault, so the key never enters this process. This
    transport exists for the author's own machine only.
    """
    connector_dir = Path(
        os.environ.get(
            "OPENCHEAP_CONNECTOR_DIR",
            str(Path.home() / "workspace" / "custom-connectors" / "custom.opencheap" / "bin"),
        )
    )
    if not (connector_dir / "client.py").exists():
        raise LLMError(
            "LLM_BACKEND=opencheap-connector is set but no OpenCheap "
            f"connector helper exists at {connector_dir / 'client.py'}."
        )
    import sys

    sys.path.insert(0, str(connector_dir))
    import client as opencheap_client  # noqa: E402

    return lambda payload: opencheap_client.call("/chat/completions", payload)


class LLMClient:
    """One client for chat completions and structured JSON replies."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        judge_model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        transport: Optional[Callable[[dict], dict]] = None,
        backend: Optional[str] = None,
    ) -> None:
        backend = backend or os.environ.get("LLM_BACKEND", "env")
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.judge_model = judge_model or os.environ.get("LLM_JUDGE_MODEL", DEFAULT_JUDGE_MODEL)
        self.timeout = int(os.environ.get("LLM_TIMEOUT", timeout))
        if transport is not None:
            self._transport = transport
        elif backend == "opencheap-connector":
            self._transport = _opencheap_connector_transport()
        else:
            api_key = api_key or os.environ.get("LLM_API_KEY")
            if not api_key:
                raise MissingKeyError(
                    "No API key is configured. Set LLM_API_KEY, or run "
                    "with LLM_BACKEND=opencheap-connector on a machine "
                    "where the OpenCheap connector is registered."
                )
            base_url = base_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
            self._transport = _env_transport(base_url, api_key, self.timeout)
        # Token and cost totals for the demo to report. The provider
        # returns usage on every response; cost is included when the
        # router reports it.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_usd = 0.0

    @staticmethod
    def has_backend() -> bool:
        """True when a model call can be made from this environment."""
        if os.environ.get("LLM_API_KEY"):
            return True
        if os.environ.get("LLM_BACKEND") == "opencheap-connector":
            connector_dir = Path(
                os.environ.get(
                    "OPENCHEAP_CONNECTOR_DIR",
                    str(Path.home() / "workspace" / "custom-connectors" / "custom.opencheap" / "bin"),
                )
            )
            return (connector_dir / "client.py").exists()
        return False

    def _record_usage(self, response: dict) -> None:
        usage = response.get("usage") or {}
        self.total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.total_completion_tokens += int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            self.total_cost_usd += float(cost)

    def chat(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None,
        response_format: Optional[dict] = None,
    ) -> dict:
        """Send a chat completion and return the provider's response.

        Retries rate limits and server errors with exponential
        back-off. A 400 or 401 is raised immediately, because
        retrying a bad request or a bad key only spends money twice.
        """
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format

        delay = 1.0
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = self._transport(payload)
            except LLMError as error:
                message = str(error)
                retryable = any(f"HTTP {code}" in message for code in _RETRYABLE)
                if not retryable or attempt == 2:
                    raise
                last_error = error
                time.sleep(delay)
                delay *= 2
                continue
            self._record_usage(response)
            return response
        raise LLMError(f"Model call failed after retries: {last_error}")

    def complete(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Return the model's reply text for a plain completion."""
        response = self.chat(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        choices = response.get("choices") or []
        if not choices:
            raise LLMError(f"Model returned no choices: {response}")
        content = choices[0].get("message", {}).get("content")
        if content is None:
            raise LLMError(f"Model returned no content: {response}")
        return content

    def structured(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        required_fields: tuple[str, ...] = (),
    ) -> dict:
        """Return a validated JSON object from the model.

        Asks for a JSON object reply first. If the provider or the
        route does not honour that, retries once without it and
        extracts the first JSON object from the reply text. Raises
        instead of returning a half-parsed object: a guardrail
        judgement with a missing field is a judgement you cannot
        act on.
        """
        response = self.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        text = self._content_of(response)
        try:
            return self._validate_json(text, required_fields)
        except LLMError:
            pass
        # Second attempt without the format request, so a route
        # that ignores response_format still yields a parseable
        # reply. The instruction to reply in JSON stays in the
        # messages, so this is a fallback, not a different prompt.
        response = self.chat(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        text = self._content_of(response)
        return self._validate_json(_extract_json_object(text), required_fields)

    @staticmethod
    def _content_of(response: dict) -> str:
        choices = response.get("choices") or []
        if not choices:
            raise LLMError(f"Model returned no choices: {response}")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise LLMError(f"Model returned no text content: {response}")
        return content

    @staticmethod
    def _validate_json(text: str, required_fields: tuple[str, ...]) -> dict:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise LLMError(f"Model reply was not valid JSON: {error}: {text[:300]}") from None
        if not isinstance(parsed, dict):
            raise LLMError(f"Model reply was not a JSON object: {text[:300]}")
        missing = [field for field in required_fields if field not in parsed]
        if missing:
            raise LLMError(f"Model reply is missing fields {missing}: {text[:300]}")
        return parsed


def _extract_json_object(text: str) -> str:
    """Return the first {...} block in the text, or the whole text."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start : end + 1]
