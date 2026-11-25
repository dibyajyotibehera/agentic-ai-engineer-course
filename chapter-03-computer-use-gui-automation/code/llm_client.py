"""A small production-style client for a model, two ways to connect it.

This is the client every chapter's live example uses when it needs a
real model. It is deliberately boring: it reads its configuration
from the environment, calls the model, retries the failures that
are worth retrying, and returns the provider's response in the
OpenAI chat-completions shape so the caller can see the token
counts and the cost.

Connect a model one of two ways, with ``LLM_BACKEND``:

Key-based (``LLM_BACKEND=env``, the default) — any OpenAI-compatible
API. All three of these are required; there is no default provider,
no default base URL and no default model:

    LLM_API_KEY     The key. No default; a missing key is a loud
                    error at call time, not a silent fallback.
    LLM_BASE_URL    Base URL of the chat API, for example
                    https://api.example.com/v1.
    LLM_MODEL       The model for agent decisions and summaries.
    LLM_JUDGE_MODEL The model for guardrail judgements. Optional;
                    defaults to ``LLM_MODEL``.
    LLM_TIMEOUT     Request timeout in seconds. Defaults to 120.

AWS Bedrock (``LLM_BACKEND=bedrock``) — the model is called through
the Amazon Bedrock Converse API instead of over HTTPS, so no API
key or base URL is involved. Credentials come from the standard
AWS credential chain (environment variables, ``~/.aws/credentials``,
or an IAM role); never pass a key to this client. Install the AWS
SDK first — the key-based backend needs no install, this one does:

    pip install boto3

    LLM_BEDROCK_MODEL_ID  The model to call, as an inference
                    profile ID. Required. Newer Claude models on
                    Bedrock REQUIRE an inference profile ID, not
                    the bare model ID — the bare ID fails with
                    HTTP 400. For example, the Claude Sonnet 4.5
                    inference profile is
                    us.anthropic.claude-sonnet-4-5-20250929-v1:0
                    (bare ID anthropic.claude-sonnet-4-5-20250929-v1:0);
                    the prefix is the region (us / eu / global).
    LLM_JUDGE_MODEL The model for guardrail judgements, as a
                    Bedrock model or inference-profile ID.
                    Optional; defaults to ``LLM_BEDROCK_MODEL_ID``.
    AWS_REGION      The AWS region, or ``AWS_DEFAULT_REGION``.

The Bedrock transport translates the same OpenAI-shaped payload
the key-based transport sends — messages, tools, tool_choice,
temperature, max_tokens — into a Converse API call, and translates
the Converse response back into the OpenAI shape the callers
already expect, so ``chat()``, ``complete()`` and ``structured()``
behave identically on either backend. One deliberate exception:
``response_format`` is IGNORED on Bedrock, because the Converse
API has no equivalent — the instruction to reply in JSON lives in
the prompt, so a JSON reply is still requested, just not enforced
by the API. ``LLM_TIMEOUT`` likewise applies to the key-based
backend only; Bedrock calls use the boto3 defaults.

The client never invents a reply. If the provider is unreachable,
the configuration is missing, or the response is not valid JSON,
the call raises instead of returning a plausible-looking fake. A
security demo that silently pretends is worse than one that fails
loudly.

Chapter 3 extension: ``complete_with_image`` sends one image
alongside a text prompt, for the Set-of-Mark grounding calls in
Exercise 2 and the delegated dismiss step in Exercise 5. It is
the only addition to the Chapter 7 client in this copy; everything
else is unchanged, so this file stays a drop-in copy plus one
method. The image is sent as a base64 data URI inside the normal
chat-completions message, so no new transport or dependency is
needed — the client is still standard library only on the
key-based backend, and on the Bedrock backend the client's image
handling converts the data URI into a Converse image block.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_TIMEOUT = 120

# Retried statuses: rate limits and server-side failures. A 400 or
# a 401 is a bug in the request or the credential, and retrying it
# only spends money twice.
_RETRYABLE = {429, 500, 502, 503, 504}

# Bedrock Converse error codes worth retrying: throttling and
# server-side failures. Anything else is a bug in the request or
# the configuration, and retrying it only spends money twice.
_RETRYABLE_BEDROCK_CODES = {
    "ThrottlingException",
    "Throttling",
    "ServiceUnavailableException",
    "InternalServerError",
    "ModelTimeoutException",
}


class LLMError(RuntimeError):
    """A model call that did not produce a usable reply."""


class MissingKeyError(LLMError):
    """Required model configuration is missing."""


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


# ---------------------------------------------------------------------------
# Bedrock transport: the same OpenAI-shaped payload, via Converse
# ---------------------------------------------------------------------------

_IMAGE_FORMATS = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _text_of_content(content: Any) -> str:
    """Return the text of a message content string or block list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _content_blocks(content: Any) -> list[dict]:
    """Translate an OpenAI message content value to Converse blocks."""
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if not isinstance(content, list):
        return []
    blocks: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            blocks.append({"text": block.get("text", "")})
        elif block_type == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
            blocks.append(_image_block(url))
        # Any other block type has no Converse equivalent; dropping
        # it is safer than inventing a translation for it.
    return blocks


def _image_block(url: str) -> dict:
    """Translate an image_url block to a Converse image block.

    Only data URIs can be sent: Converse takes raw image bytes, so
    a remote URL has nothing to send, and fetching it here would
    silently turn a model call into a network fetch.
    """
    if not url.startswith("data:"):
        raise LLMError(
            "Bedrock image input must be a data URI "
            "(data:image/png;base64,...); a remote image URL cannot be "
            f"sent to the Converse API: {url[:120]}"
        )
    try:
        header, encoded = url.split(",", 1)
        mime = header.split(";", 1)[0][len("data:"):]
        image_format = _IMAGE_FORMATS[mime]
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, KeyError, binascii.Error) as error:
        raise LLMError(
            "Bedrock image input must be a data URI with a supported "
            f"image type (png, jpeg, gif, webp): {url[:120]}"
        ) from error
    return {"image": {"format": image_format, "source": {"bytes": raw}}}


def _translate_to_converse(payload: dict) -> dict:
    """Translate an OpenAI-shaped chat payload to a Converse call."""
    system: list[dict] = []
    messages: list[dict] = []
    for message in payload.get("messages") or []:
        role = message.get("role")
        if role == "system":
            text = _text_of_content(message.get("content"))
            if text:
                system.append({"text": text})
            continue
        if role == "tool":
            messages.append({
                "role": "user",
                "content": [{
                    "toolResult": {
                        "toolUseId": message.get("tool_call_id", ""),
                        "content": {"text": _text_of_content(message.get("content"))},
                    }
                }],
            })
            continue
        if role not in ("user", "assistant"):
            raise LLMError(f"Bedrock Converse has no role {role!r}.")
        blocks: list[dict] = []
        if role == "assistant":
            blocks.extend(_content_blocks(message.get("content")))
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError as error:
                    raise LLMError(
                        "Bedrock tool call arguments were not valid JSON: "
                        f"{function.get('arguments', '')[:200]}"
                    ) from error
                if not isinstance(arguments, dict):
                    raise LLMError(
                        "Bedrock tool call arguments must be a JSON object: "
                        f"{function.get('arguments', '')[:200]}"
                    )
                blocks.append({
                    "toolUse": {
                        "toolUseId": call.get("id", ""),
                        "name": function.get("name", ""),
                        "input": arguments,
                    }
                })
        else:
            blocks.extend(_content_blocks(message.get("content")))
        if not blocks:
            blocks.append({"text": ""})
        messages.append({"role": role, "content": blocks})

    converse: dict[str, Any] = {
        "modelId": payload.get("model"),
        "messages": messages,
    }
    if system:
        converse["system"] = system
    inference_config: dict[str, Any] = {}
    if payload.get("max_tokens") is not None:
        inference_config["maxTokens"] = payload["max_tokens"]
    if payload.get("temperature") is not None:
        inference_config["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        inference_config["topP"] = payload["top_p"]
    if inference_config:
        converse["inferenceConfig"] = inference_config
    if payload.get("tools") is not None:
        tools = []
        for tool in payload["tools"]:
            function = tool.get("function") or {}
            parameters = function.get("parameters") or {"type": "object", "properties": {}}
            tools.append({
                "toolSpec": {
                    "name": function.get("name", ""),
                    "description": function.get("description", ""),
                    "inputSchema": {"json": parameters},
                }
            })
        tool_config: dict[str, Any] = {"tools": tools}
        tool_choice = payload.get("tool_choice")
        if tool_choice is not None:
            if isinstance(tool_choice, str):
                if tool_choice == "auto":
                    tool_config["toolChoice"] = {"auto": {}}
                elif tool_choice == "none":
                    tool_config["toolChoice"] = {"none": {}}
                elif tool_choice == "required":
                    tool_config["toolChoice"] = {"any": {}}
                else:
                    raise LLMError(
                        f"Bedrock tool_choice {tool_choice!r} has no "
                        "Converse equivalent; use auto, none or required."
                    )
            elif isinstance(tool_choice, dict):
                function = tool_choice.get("function") or {}
                tool_config["toolChoice"] = {"tool": {"name": function.get("name", "")}}
        converse["toolConfig"] = tool_config
    # response_format is deliberately not translated: the Converse
    # API has no equivalent, and the JSON instruction lives in the
    # prompt. See the module docstring.
    return converse


_FINISH_REASONS = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}


def _translate_from_converse(response: dict) -> dict:
    """Translate a Converse response back to the OpenAI shape."""
    output = response.get("output") or {}
    message = output.get("message") or {}
    content_blocks = message.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in content_blocks:
        if "text" in block:
            text_parts.append(block["text"])
        elif "toolUse" in block:
            tool_use = block["toolUse"]
            tool_calls.append({
                "id": tool_use.get("toolUseId", ""),
                "type": "function",
                "function": {
                    "name": tool_use.get("name", ""),
                    "arguments": json.dumps(tool_use.get("input") or {}),
                },
            })
    reply: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if tool_calls:
        reply["tool_calls"] = tool_calls
    stop_reason = response.get("stopReason", "end_turn")
    usage = response.get("usage") or {}
    return {
        "choices": [{
            "message": reply,
            "finish_reason": _FINISH_REASONS.get(stop_reason, "stop"),
        }],
        "usage": {
            "prompt_tokens": int(usage.get("inputTokens") or 0),
            "completion_tokens": int(usage.get("outputTokens") or 0),
        },
    }


def _bedrock_transport(model_id: str) -> Callable[[dict], dict]:
    """Call a model through the Amazon Bedrock Converse API.

    boto3 is imported here, and only here, so the key-based backend
    never needs it. Credentials come from the standard AWS chain;
    this function takes no key argument of any kind.
    """
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError as error:
        raise LLMError(
            "LLM_BACKEND=bedrock needs boto3, which is not installed. "
            "Run: pip install boto3 "
            "(the key-based backend, LLM_BACKEND=env, needs no install)."
        ) from error
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
    except Exception as error:  # noqa: BLE001 — boto3 raises several types here
        raise LLMError(
            "Could not create the Bedrock client. Set AWS_REGION "
            f"(or AWS_DEFAULT_REGION) and check the AWS credentials: {error}"
        ) from error

    def transport(payload: dict) -> dict:
        converse = _translate_to_converse(payload)
        # The payload's model is the one to call: the main model, or
        # the judge model when the caller asked for it. Fall back to
        # this transport's model only if the payload names none.
        if not converse.get("modelId"):
            converse["modelId"] = model_id
        try:
            response = client.converse(**converse)
        except ClientError as error:
            code = (error.response.get("Error") or {}).get("Code", "")
            detail = (error.response.get("Error") or {}).get("Message", "")[:500]
            raise LLMError(
                f"Bedrock Converse call failed with {code}: {detail}"
            ) from None
        except Exception as error:  # noqa: BLE001 — boto3 raises several types here
            raise LLMError(f"Bedrock Converse call did not complete: {error}") from None
        return _translate_from_converse(response)

    return transport


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
        if backend not in ("env", "bedrock"):
            raise LLMError(
                f"Unknown LLM_BACKEND={backend!r}. Use LLM_BACKEND=env "
                "(key-based) or LLM_BACKEND=bedrock."
            )
        self.backend = backend
        self.timeout = int(os.environ.get("LLM_TIMEOUT", timeout))
        if transport is not None:
            self._transport = transport
            self.model = model or os.environ.get("LLM_MODEL") or os.environ.get("LLM_BEDROCK_MODEL_ID", "")
            self.judge_model = judge_model or os.environ.get("LLM_JUDGE_MODEL") or self.model
        elif backend == "bedrock":
            model_id = model or os.environ.get("LLM_BEDROCK_MODEL_ID")
            if not model_id:
                raise MissingKeyError(
                    "No Bedrock model is configured. Set "
                    "LLM_BEDROCK_MODEL_ID to an inference profile ID, "
                    "for example us.anthropic.claude-sonnet-4-5-20250929-v1:0."
                )
            self.model = model_id
            self.judge_model = (
                judge_model or os.environ.get("LLM_JUDGE_MODEL") or model_id
            )
            self._transport = _bedrock_transport(model_id)
        else:
            api_key = api_key or os.environ.get("LLM_API_KEY")
            base_url = base_url or os.environ.get("LLM_BASE_URL")
            self.model = model or os.environ.get("LLM_MODEL")
            missing = [
                name
                for name, value in (
                    ("LLM_API_KEY", api_key),
                    ("LLM_BASE_URL", base_url),
                    ("LLM_MODEL", self.model),
                )
                if not value
            ]
            if missing:
                raise MissingKeyError(
                    "No model is configured for the key-based backend. "
                    f"Set {', '.join(missing)}"
                    + (
                        " (all three of LLM_API_KEY, LLM_BASE_URL and "
                        "LLM_MODEL are required; there is no default "
                        "provider, base URL or model)."
                        if len(missing) > 1
                        else "."
                    )
                )
            self.judge_model = judge_model or os.environ.get("LLM_JUDGE_MODEL") or self.model
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
        backend = os.environ.get("LLM_BACKEND", "env")
        if backend == "bedrock":
            if not os.environ.get("LLM_BEDROCK_MODEL_ID"):
                return False
            try:
                import boto3
            except ImportError:
                return False
            try:
                return boto3.Session().get_credentials() is not None
            except Exception:  # noqa: BLE001 — any credential failure means no backend
                return False
        if backend == "env":
            return bool(
                os.environ.get("LLM_API_KEY")
                and os.environ.get("LLM_BASE_URL")
                and os.environ.get("LLM_MODEL")
            )
        return False

    def _record_usage(self, response: dict) -> None:
        usage = response.get("usage") or {}
        self.total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.total_completion_tokens += int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            self.total_cost_usd += float(cost)

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        message = str(error)
        if any(f"HTTP {code}" in message for code in _RETRYABLE):
            return True
        return any(code in message for code in _RETRYABLE_BEDROCK_CODES)

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
                if not self._is_retryable(error) or attempt == 2:
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

    def complete_with_image(
        self,
        image_path: str | Path,
        prompt: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> str:
        """Return the model's reply text for one image plus a prompt.

        Chapter 3 addition (the only one in this copy). The image is
        read from disk and sent as a base64 data URI in a normal
        chat-completions message, so the existing transports carry it
        unchanged — on the Bedrock backend the client's image handling
        converts the data URI into a Converse image block.
        ``max_tokens`` is deliberately generous for a
        one-word answer: a reasoning model can spend its budget on
        hidden reasoning and return empty content if the cap is
        tight, so the cap protects against runaway replies, not
        against short ones. Raises like ``complete`` when the
        provider returns no usable text.
        """
        path = Path(image_path)
        data = path.read_bytes()
        mime, _ = mimetypes.guess_type(str(path))
        if mime is None or not mime.startswith("image/"):
            mime = "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{encoded}"
                        },
                    },
                ],
            }
        ]
        return self.complete(
            messages, model=model, temperature=temperature,
            max_tokens=max_tokens,
        )

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
        act on. On the Bedrock backend the JSON format request is
        ignored by the transport (the Converse API has no
        equivalent); the instruction to reply in JSON stays in the
        messages, so the fallback path below is what produces the
        parseable reply there.
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
